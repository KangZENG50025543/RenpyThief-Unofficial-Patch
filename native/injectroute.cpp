#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <QtCore/QByteArray>
#include <QtCore/QUrl>
#include <QtNetwork/QNetworkAccessManager>
#include <QtNetwork/QNetworkReply>
#include <QtNetwork/QNetworkRequest>

#include <atomic>
#include <string>

#include "MinHook.h"
#include "injectroute_request.h"

namespace {

using NetworkGetFn = QNetworkReply* (__thiscall*)(
    QNetworkAccessManager*, const QNetworkRequest&);
using NetworkPostFn = QNetworkReply* (__thiscall*)(
    QNetworkAccessManager*, const QNetworkRequest&, const QByteArray&);

std::wstring g_dir;
CRITICAL_SECTION g_logLock;
bool g_logLockReady = false;
NetworkGetFn g_networkGet = nullptr;
NetworkPostFn g_networkPost = nullptr;
std::atomic<long> g_ready{0};

class NetworkReplyAccess : public QNetworkReply {
public:
    using QNetworkReply::setUrl;
};

void Log(const std::string& event)
{
    if (!g_logLockReady) return;

    SYSTEMTIME time{};
    GetLocalTime(&time);
    char prefix[128]{};
    _snprintf_s(prefix, _TRUNCATE,
                "%04u-%02u-%02u %02u:%02u:%02u.%03u pid=%lu tid=%lu ",
                time.wYear, time.wMonth, time.wDay, time.wHour, time.wMinute,
                time.wSecond, time.wMilliseconds, GetCurrentProcessId(),
                GetCurrentThreadId());

    EnterCriticalSection(&g_logLock);
    const std::wstring path = g_dir + L"\\injectroute.log";
    HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                              FILE_SHARE_READ | FILE_SHARE_WRITE |
                                  FILE_SHARE_DELETE,
                              nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        const std::string line = std::string(prefix) + event + "\r\n";
        DWORD written = 0;
        WriteFile(file, line.data(), static_cast<DWORD>(line.size()), &written,
                  nullptr);
        CloseHandle(file);
    }
    LeaveCriticalSection(&g_logLock);
}

std::string HostOf(const QNetworkRequest& request)
{
    const QByteArray encoded = request.url().toEncoded(QUrl::FullyEncoded);
    const char* data = encoded.constData();
    const int size = encoded.size();
    if (!data || size <= 0) return {};
    const std::string url(data, static_cast<size_t>(size));
    const size_t scheme = url.find("://");
    if (scheme == std::string::npos) return {};
    size_t begin = scheme + 3;
    const size_t slash = url.find('/', begin);
    const size_t end = slash == std::string::npos ? url.size() : slash;
    std::string host = url.substr(begin, end - begin);
    const size_t at = host.rfind('@');
    if (at != std::string::npos) host = host.substr(at + 1);
    const size_t colon = host.find(':');
    if (colon != std::string::npos) host.resize(colon);
    return host;
}

std::string BodyOf(const QByteArray& data)
{
    const char* bytes = data.constData();
    const int size = data.size();
    if (!bytes || size <= 0) return {};
    return std::string(bytes, static_cast<size_t>(size));
}

QNetworkReply* __fastcall HookNetworkPost(QNetworkAccessManager* self, void*,
                                          const QNetworkRequest& request,
                                          const QByteArray& data)
{
    const std::string host = HostOf(request);
    const std::string body = BodyOf(data);
    std::string text;
    std::string from;
    std::string to;
    const bool cipher = IsInjectorCipherBody(body);
    const bool extracted = ExtractInjectorPlaintext(body, text, from, to);
    if (!IsLoopbackHttpHost(host) || cipher || !extracted || !g_networkGet) {
        if (cipher) {
            Log("post pass reason=cipher host=" + host +
                " body_bytes=" + std::to_string(body.size()));
        }
        return g_networkPost(self, request, data);
    }

    const std::string local = InjectorBridgeUrl(text, from, to);
    const QByteArray encoded(local.data(), static_cast<int>(local.size()));
    QNetworkRequest replacement(request);
    replacement.setUrl(QUrl::fromEncoded(encoded, QUrl::StrictMode));
    QNetworkReply* reply = g_networkGet(self, replacement);
    if (!reply) {
        Log("reroute failed reason=null_reply chars=" +
            std::to_string(text.size()) + " action=pass");
        return g_networkPost(self, request, data);
    }
    auto* access = static_cast<NetworkReplyAccess*>(reply);
    access->setUrl(request.url());
    Log("reroute ok host=" + host + " chars=" + std::to_string(text.size()) +
        " from=" + from + " to=" + to);
    return reply;
}

DWORD WINAPI Start(void*)
{
    HMODULE network = GetModuleHandleW(L"Qt5Network.dll");
    if (!network) network = LoadLibraryW(L"Qt5Network.dll");
    if (!network || MH_Initialize() != MH_OK) {
        Log("initialization failed reason=module_or_minhook");
        g_ready.store(-1);
        return 1;
    }

    void* postTarget = GetProcAddress(
        network,
        "?post@QNetworkAccessManager@@QAEPAVQNetworkReply@@ABVQNetworkRequest@@ABVQByteArray@@@Z");
    void* getTarget = GetProcAddress(
        network,
        "?get@QNetworkAccessManager@@QAEPAVQNetworkReply@@ABVQNetworkRequest@@@Z");
    g_networkGet = reinterpret_cast<NetworkGetFn>(getTarget);
    const MH_STATUS postCreated = postTarget
        ? MH_CreateHook(postTarget, reinterpret_cast<void*>(&HookNetworkPost),
                        reinterpret_cast<void**>(&g_networkPost))
        : MH_ERROR_NOT_EXECUTABLE;
    if (postCreated != MH_OK || !g_networkGet) {
        Log("hook create failed post_status=" + std::to_string(postCreated) +
            " get=" + (g_networkGet ? "true" : "false"));
        g_ready.store(-1);
        return 1;
    }

    const MH_STATUS enabled = MH_EnableHook(MH_ALL_HOOKS);
    Log(std::string("state=hook_ready hook_enable_status=") +
        std::to_string(enabled) + " post_hook=true");
    g_ready.store(enabled == MH_OK ? 1 : -1);
    return enabled == MH_OK ? 0 : 1;
}

}  // namespace

extern "C" __declspec(dllexport) long __cdecl injectroute_status()
{
    return g_ready.load();
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        wchar_t path[32768]{};
        GetModuleFileNameW(module, path, static_cast<DWORD>(_countof(path)));
        if (wchar_t* slash = wcsrchr(path, L'\\')) {
            *slash = L'\0';
            g_dir = path;
        } else {
            g_dir = L".";
        }
        InitializeCriticalSection(&g_logLock);
        g_logLockReady = true;
        HANDLE thread = CreateThread(nullptr, 0, Start, nullptr, 0, nullptr);
        if (thread) CloseHandle(thread);
    }
    return TRUE;
}
