#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winver.h>

#include <QtCore/QByteArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonParseError>
#include <QtCore/QUrl>
#include <QtCore/QVariant>
#include <QtNetwork/QNetworkAccessManager>
#include <QtNetwork/QNetworkReply>
#include <QtNetwork/QNetworkRequest>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "MinHook.h"
#include "version_endpoint.h"

namespace {

enum class GuardMode {
    Observe,
    Lock,
};

enum class SessionCompat {
    Observe,
    Lock,
};

enum class ConfigCompat {
    Pass,
    Deny,
};

// MSVC x86 ABI: ECX is the manager, EDX is unused by the fastcall hook, and
// the QNetworkRequest reference remains on the stack.
using NetworkGetFn = QNetworkReply* (__thiscall*)(
    QNetworkAccessManager*, const QNetworkRequest&);
using CreateRequestFn = QNetworkReply* (__thiscall*)(
    QNetworkAccessManager*, QNetworkAccessManager::Operation,
    const QNetworkRequest&, QIODevice*);

// MSVC x86 ABI for the static function:
// QJsonDocument QJsonDocument::fromJson(const QByteArray&, QJsonParseError*)
// The result object is the first hidden stack argument.
using FromJsonFn = void* (__cdecl*)(void*, const void*, void*);

std::wstring g_dir;
CRITICAL_SECTION g_logLock;
bool g_logLockReady = false;
GuardMode g_mode = GuardMode::Observe;
SessionCompat g_sessionCompat = SessionCompat::Observe;
ConfigCompat g_configCompat = ConfigCompat::Pass;
NetworkGetFn g_networkGet = nullptr;
CreateRequestFn g_createRequest = nullptr;
FromJsonFn g_fromJson = nullptr;
std::atomic<long> g_ready{0};
std::atomic<unsigned long> g_blockedChecks{0};
std::atomic<unsigned long> g_getMatches{0};
std::atomic<unsigned long> g_createRequestMatches{0};
std::string g_localVersion;
std::string g_localUserName = "local";

class NetworkReplyAccess : public QNetworkReply {
public:
    using QNetworkReply::setAttribute;
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
    const std::wstring path = g_dir + L"\\versionguard.log";
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

std::wstring TrimLower(std::wstring value)
{
    const wchar_t* whitespace = L" \t\r\n";
    const size_t first = value.find_first_not_of(whitespace);
    if (first == std::wstring::npos) return {};
    value.erase(0, first);
    const size_t last = value.find_last_not_of(whitespace);
    value.erase(last + 1);
    std::transform(value.begin(), value.end(), value.begin(),
                   [](wchar_t c) {
                       return static_cast<wchar_t>(towlower(c));
                   });
    return value;
}

bool IsSafeVersion(const std::string& value)
{
    if (value.empty() || value.size() > 64) return false;
    return std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return std::isalnum(c) || c == '.' || c == '-' || c == '_' ||
               c == '+';
    });
}

bool IsSafeUserName(const std::string& value)
{
    if (value.empty() || value.size() > 64) return false;
    return std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return std::isalnum(c) || c == '.' || c == '-' || c == '_';
    });
}

std::string WideToUtf8(const std::wstring& value)
{
    if (value.empty()) return {};
    const int bytes = WideCharToMultiByte(CP_UTF8, 0, value.data(),
                                          static_cast<int>(value.size()),
                                          nullptr, 0, nullptr, nullptr);
    if (bytes <= 0) return {};
    std::string result(static_cast<size_t>(bytes), '\0');
    if (WideCharToMultiByte(CP_UTF8, 0, value.data(),
                            static_cast<int>(value.size()), &result[0],
                            bytes, nullptr, nullptr) != bytes) {
        return {};
    }
    return result;
}

std::string ReadHostFileVersion()
{
    wchar_t executable[32768]{};
    const DWORD length = GetModuleFileNameW(
        nullptr, executable, static_cast<DWORD>(_countof(executable)));
    if (!length || length >= _countof(executable)) return {};

    DWORD ignored = 0;
    const DWORD size = GetFileVersionInfoSizeW(executable, &ignored);
    if (!size || size > 16 * 1024 * 1024) return {};
    std::vector<unsigned char> data(size);
    if (!GetFileVersionInfoW(executable, 0, size, data.data())) return {};

    struct LanguageAndCodePage {
        WORD language;
        WORD codePage;
    };
    LanguageAndCodePage* translations = nullptr;
    UINT translationsBytes = 0;
    if (VerQueryValueW(data.data(), L"\\VarFileInfo\\Translation",
                       reinterpret_cast<void**>(&translations),
                       &translationsBytes) && translations) {
        const size_t count = translationsBytes / sizeof(LanguageAndCodePage);
        for (size_t i = 0; i < count; ++i) {
            wchar_t query[96]{};
            _snwprintf_s(query, _TRUNCATE,
                         L"\\StringFileInfo\\%04x%04x\\FileVersion",
                         translations[i].language, translations[i].codePage);
            wchar_t* text = nullptr;
            UINT chars = 0;
            if (VerQueryValueW(data.data(), query,
                               reinterpret_cast<void**>(&text), &chars) &&
                text && chars > 1) {
                std::wstring version(text, chars - 1);
                const size_t comma = version.find(L',');
                if (comma != std::wstring::npos) {
                    std::replace(version.begin(), version.end(), L',', L'.');
                    version.erase(std::remove_if(version.begin(), version.end(),
                                                 iswspace),
                                  version.end());
                }
                const std::string utf8 = WideToUtf8(version);
                if (IsSafeVersion(utf8)) return utf8;
            }
        }
    }

    VS_FIXEDFILEINFO* fixed = nullptr;
    UINT fixedBytes = 0;
    if (!VerQueryValueW(data.data(), L"\\",
                        reinterpret_cast<void**>(&fixed), &fixedBytes) ||
        !fixed || fixedBytes < sizeof(VS_FIXEDFILEINFO)) {
        return {};
    }
    const unsigned major = HIWORD(fixed->dwFileVersionMS);
    const unsigned minor = LOWORD(fixed->dwFileVersionMS);
    const unsigned patch = HIWORD(fixed->dwFileVersionLS);
    const unsigned revision = LOWORD(fixed->dwFileVersionLS);
    char version[96]{};
    if (revision == 0) {
        _snprintf_s(version, _TRUNCATE, "%u.%u.%u", major, minor, patch);
    } else {
        _snprintf_s(version, _TRUNCATE, "%u.%u.%u.%u", major, minor, patch,
                    revision);
    }
    return IsSafeVersion(version) ? version : std::string();
}

std::string ReadLocalUserName()
{
    wchar_t executable[32768]{};
    const DWORD length = GetModuleFileNameW(
        nullptr, executable, static_cast<DWORD>(_countof(executable)));
    if (!length || length >= _countof(executable)) return {};
    wchar_t* slash = wcsrchr(executable, L'\\');
    if (!slash) return {};
    wcscpy_s(slash + 1,
             _countof(executable) - static_cast<size_t>(slash + 1 - executable),
             L"user");
    HANDLE file = CreateFileW(executable, GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE |
                                  FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE) return {};
    char buffer[256]{};
    DWORD read = 0;
    const BOOL ok = ReadFile(file, buffer, sizeof(buffer) - 1, &read, nullptr);
    CloseHandle(file);
    if (!ok || read == 0) return {};
    const std::string text(buffer, static_cast<size_t>(read));
    const size_t key = text.find("username");
    if (key == std::string::npos) return {};
    size_t pos = text.find('=', key);
    if (pos == std::string::npos) return {};
    ++pos;
    while (pos < text.size() && (text[pos] == ' ' || text[pos] == '\t')) ++pos;
    size_t end = pos;
    while (end < text.size() && text[end] != '\r' && text[end] != '\n') ++end;
    const std::string name = text.substr(pos, end - pos);
    return IsSafeUserName(name) ? name : std::string();
}

void LoadConfiguration()
{
    wchar_t value[128]{};
    const std::wstring path = g_dir + L"\\versionguard.ini";
    GetPrivateProfileStringW(L"versionguard", L"mode", L"observe", value,
                             static_cast<DWORD>(_countof(value)), path.c_str());
    g_mode = TrimLower(value) == L"lock" ? GuardMode::Lock
                                          : GuardMode::Observe;

    GetPrivateProfileStringW(L"versionguard", L"local_version", L"auto",
                             value, static_cast<DWORD>(_countof(value)),
                             path.c_str());
    const std::wstring configured = TrimLower(value);
    g_localVersion = configured == L"auto" ? ReadHostFileVersion()
                                             : WideToUtf8(configured);
    if (!IsSafeVersion(g_localVersion)) g_localVersion.clear();

    GetPrivateProfileStringW(L"versionguard", L"session_compat", L"observe",
                             value, static_cast<DWORD>(_countof(value)),
                             path.c_str());
    g_sessionCompat = TrimLower(value) == L"lock" ? SessionCompat::Lock
                                                  : SessionCompat::Observe;
    GetPrivateProfileStringW(L"versionguard", L"config_compat", L"pass", value,
                             static_cast<DWORD>(_countof(value)), path.c_str());
    g_configCompat = TrimLower(value) == L"deny" ? ConfigCompat::Deny
                                                 : ConfigCompat::Pass;

    const std::string localUser = ReadLocalUserName();
    if (IsSafeUserName(localUser)) g_localUserName = localUser;

    Log(std::string("configuration mode=") +
        (g_mode == GuardMode::Lock ? "lock" : "observe") +
        " session_compat=" +
        (g_sessionCompat == SessionCompat::Lock ? "lock" : "observe") +
        " config_compat=" +
        (g_configCompat == ConfigCompat::Deny ? "deny" : "pass") +
        " local_version=" +
        (g_localVersion.empty() ? "unavailable" : g_localVersion) +
        " username_present=" +
        (localUser.empty() ? "false" : "true"));
}

std::string PercentEncodedVersionResponse()
{
    // All punctuation is percent encoded so QUrl cannot reinterpret JSON
    // delimiters. g_localVersion has already been restricted to URL-safe
    // ASCII characters.
    return "data:application/json,%7B%22status%22%3A200%2C%22msg%22%3A%22OK%22"
           "%2C%22data%22%3A%7B%22versionNum%22%3A%22" +
           g_localVersion +
           "%22%2C%22versionInfo%22%3A%22%22%2C%22downloadUrl%22%3A%22%22"
           "%2C%22createTime%22%3A%22%22%7D%7D";
}

std::string EncodedUrl(const QNetworkRequest& request)
{
    const QByteArray encoded = request.url().toEncoded(QUrl::FullyEncoded);
    return std::string(encoded.constData(), static_cast<size_t>(encoded.size()));
}

void SignalBlockedCheck();

enum class RequestEntry
{
    Get,
    CreateRequest,
};

const char* EntryName(RequestEntry entry)
{
    return entry == RequestEntry::Get ? "get" : "createRequest";
}

const char* OperationName(QNetworkAccessManager::Operation operation)
{
    switch (operation) {
    case QNetworkAccessManager::HeadOperation:
        return "HEAD";
    case QNetworkAccessManager::GetOperation:
        return "GET";
    case QNetworkAccessManager::PostOperation:
        return "POST";
    default:
        return "OTHER";
    }
}

bool IsSupportedVersionOperation(QNetworkAccessManager::Operation operation)
{
    return operation == QNetworkAccessManager::HeadOperation ||
           operation == QNetworkAccessManager::GetOperation ||
           operation == QNetworkAccessManager::PostOperation;
}

QNetworkReply* HandleVersionRequest(
    QNetworkAccessManager* self, const QNetworkRequest& request,
    RequestEntry entry, QNetworkAccessManager::Operation operation,
    QIODevice* outgoingData, const VersionEndpointMatch& shape)
{
    const unsigned long entryCount = entry == RequestEntry::Get
        ? ++g_getMatches
        : ++g_createRequestMatches;
    Log("request matched endpoint=getVersionInfo entry=" +
        std::string(EntryName(entry)) +
        " operation=" + OperationName(operation) +
        " query=" + (shape.hasQuery ? "true" : "false") +
        " port=" + (shape.hasExplicitPort ? "explicit" : "default") +
        " entry_count=" + std::to_string(entryCount) +
        " action=" +
        std::string(g_mode == GuardMode::Lock ? "short_circuit" : "observe"));
    if (g_mode != GuardMode::Lock) {
        return entry == RequestEntry::Get
            ? g_networkGet(self, request)
            : g_createRequest(self, operation, request, outgoingData);
    }
    if (g_localVersion.empty()) {
        Log("short_circuit failed reason=local_version_unavailable "
            "entry=" + std::string(EntryName(entry)) +
            " action=fail_closed");
        return nullptr;
    }

    const std::string dataUrl = PercentEncodedVersionResponse();
    const QByteArray encoded(dataUrl.data(), static_cast<int>(dataUrl.size()));
    QNetworkRequest replacement(request);
    replacement.setUrl(QUrl::fromEncoded(encoded, QUrl::StrictMode));
    QNetworkReply* reply = entry == RequestEntry::Get
        ? g_networkGet(self, replacement)
        // A future version may submit this read-only check as HEAD or POST.
        // The replacement is always a local body-producing GET and never
        // forwards caller-owned upload data.
        : g_createRequest(self, QNetworkAccessManager::GetOperation,
                          replacement, nullptr);
    if (!reply) {
        Log("short_circuit failed reason=null_reply entry=" +
            std::string(EntryName(entry)) + " action=fail_closed");
        return nullptr;
    }

    auto* access = static_cast<NetworkReplyAccess*>(reply);
    access->setUrl(request.url());
    access->setAttribute(QNetworkRequest::HttpStatusCodeAttribute,
                         QVariant(200));
    const unsigned long count = ++g_blockedChecks;
    Log("state=blocked_check endpoint=getVersionInfo entry=" +
        std::string(EntryName(entry)) + " local_version=" + g_localVersion +
        " blocked_count=" + std::to_string(count) +
        " get_matches=" + std::to_string(g_getMatches.load()) +
        " create_matches=" +
        std::to_string(g_createRequestMatches.load()));
    SignalBlockedCheck();
    return reply;
}

std::string PercentEncode(const std::string& value)
{
    static const char hex[] = "0123456789ABCDEF";
    std::string out;
    out.reserve(value.size() * 3);
    for (unsigned char c : value) {
        if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            out.push_back(static_cast<char>(c));
        } else {
            out.push_back('%');
            out.push_back(hex[c >> 4]);
            out.push_back(hex[c & 15]);
        }
    }
    return out;
}

std::string SessionJson(const std::string& endpoint)
{
    if (endpoint == "pingTest" || endpoint == "submitInject" ||
        endpoint == "submitEndGame") {
        return "{\"status\":200,\"msg\":\"OK\",\"data\":\"1\"}";
    }
    return std::string("{\"status\":200,\"msg\":\"OK\",\"data\":{") +
           "\"userId\":1,\"levelId\":1,\"levelName\":\"local\",\"username\":\"" +
           g_localUserName +
           "\",\"userTransCount\":0,\"userTransCharCount\":0,"
           "\"remainCharCount\":999999999,\"extraCharCount\":0,"
           "\"unreadMessageCount\":0,\"levelExpireDate\":4102444800000,"
           "\"gptTrialUser\":false,\"levelExpireDateStr\":\"2099-12-31\","
           "\"freeUserGPTExpireDate\":0}}";
}

QNetworkReply* SucceedLocally(QNetworkAccessManager* self,
                              const QNetworkRequest& request,
                              RequestEntry entry, const std::string& json)
{
    const std::string dataUrl = "data:application/json," + PercentEncode(json);
    const QByteArray encoded(dataUrl.data(), static_cast<int>(dataUrl.size()));
    QNetworkRequest replacement(request);
    replacement.setUrl(QUrl::fromEncoded(encoded, QUrl::StrictMode));
    QNetworkReply* reply = entry == RequestEntry::Get
        ? g_networkGet(self, replacement)
        : g_createRequest(self, QNetworkAccessManager::GetOperation,
                          replacement, nullptr);
    if (!reply) return nullptr;
    auto* access = static_cast<NetworkReplyAccess*>(reply);
    access->setUrl(request.url());
    access->setAttribute(QNetworkRequest::HttpStatusCodeAttribute,
                         QVariant(200));
    return reply;
}

QNetworkReply* FailLocally(QNetworkAccessManager* self,
                           RequestEntry entry,
                           QNetworkAccessManager::Operation operation,
                           const QNetworkRequest& request)
{
    const char drop[] = "compat-drop:denied";
    const QByteArray encoded(drop, static_cast<int>(sizeof(drop) - 1));
    QNetworkRequest replacement(request);
    replacement.setUrl(QUrl::fromEncoded(encoded, QUrl::StrictMode));
    QNetworkReply* reply = entry == RequestEntry::Get
        ? g_networkGet(self, replacement)
        : g_createRequest(self, operation, replacement, nullptr);
    if (reply) {
        auto* access = static_cast<NetworkReplyAccess*>(reply);
        access->setUrl(request.url());
    }
    return reply;
}

const char* KindName(OfficialApiKind kind)
{
    switch (kind) {
    case OfficialApiKind::Version:
        return "version";
    case OfficialApiKind::Session:
        return "session";
    case OfficialApiKind::Config:
        return "config";
    case OfficialApiKind::Translate:
        return "translate";
    case OfficialApiKind::Other:
        return "other";
    default:
        return "none";
    }
}

QNetworkReply* DispatchOfficialRequest(
    QNetworkAccessManager* self, const QNetworkRequest& request,
    RequestEntry entry, QNetworkAccessManager::Operation operation,
    QIODevice* outgoingData)
{
    OfficialEndpointMatch official;
    if (!MatchOfficialEndpoint(EncodedUrl(request), official)) {
        return entry == RequestEntry::Get
            ? g_networkGet(self, request)
            : g_createRequest(self, operation, request, outgoingData);
    }
    const OfficialApiKind kind = ClassifyOfficialEndpoint(official.endpoint);
    if (kind == OfficialApiKind::Version &&
        IsSupportedVersionOperation(operation)) {
        VersionEndpointMatch shape;
        shape.hasQuery = official.hasQuery;
        shape.hasExplicitPort = official.hasExplicitPort;
        return HandleVersionRequest(self, request, entry, operation,
                                    outgoingData, shape);
    }

    const bool sessionLock = g_mode == GuardMode::Lock &&
                             g_sessionCompat == SessionCompat::Lock &&
                             kind == OfficialApiKind::Session &&
                             IsSupportedVersionOperation(operation);
    const bool configDeny = g_mode == GuardMode::Lock &&
                            g_configCompat == ConfigCompat::Deny &&
                            kind == OfficialApiKind::Config &&
                            IsSupportedVersionOperation(operation);
    const char* action = sessionLock ? "short_circuit"
                                     : (configDeny ? "deny" : "pass");
    Log("request matched endpoint=" + official.endpoint +
        " kind=" + std::string(KindName(kind)) +
        " entry=" + std::string(EntryName(entry)) +
        " operation=" + OperationName(operation) +
        " action=" + action);
    if (sessionLock) {
        QNetworkReply* reply =
            SucceedLocally(self, request, entry, SessionJson(official.endpoint));
        if (!reply) {
            Log("short_circuit failed reason=null_reply endpoint=" +
                official.endpoint + " action=fail_closed");
        }
        return reply;
    }
    if (configDeny) {
        return FailLocally(self, entry, operation, request);
    }
    return entry == RequestEntry::Get
        ? g_networkGet(self, request)
        : g_createRequest(self, operation, request, outgoingData);
}

QNetworkReply* __fastcall HookNetworkGet(QNetworkAccessManager* self, void*,
                                         const QNetworkRequest& request)
{
    return DispatchOfficialRequest(self, request, RequestEntry::Get,
                                   QNetworkAccessManager::GetOperation,
                                   nullptr);
}

QNetworkReply* __fastcall HookCreateRequest(
    QNetworkAccessManager* self, void*,
    QNetworkAccessManager::Operation operation,
    const QNetworkRequest& request, QIODevice* outgoingData)
{
    return DispatchOfficialRequest(self, request, RequestEntry::CreateRequest,
                                   operation, outgoingData);
}

bool ReadQByteArray(const void* object, const char*& data, int& size)
{
    data = nullptr;
    size = 0;
    if (!object) return false;
    __try {
        const uintptr_t d = *reinterpret_cast<const uintptr_t*>(object);
        if (d < 0x10000) return false;
        size = *reinterpret_cast<const int*>(d + 4);
        const intptr_t offset = *reinterpret_cast<const int32_t*>(d + 12);
        if (size < 0 || size > 16 * 1024 * 1024 || offset < 0 ||
            offset > 0x100000) {
            return false;
        }
        data = reinterpret_cast<const char*>(d + offset);
        if (size > 0) {
            volatile char first = data[0];
            volatile char last = data[size - 1];
            (void)first;
            (void)last;
        }
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        data = nullptr;
        size = 0;
        return false;
    }
}

std::string ExtractJsonString(const std::string& json, const char* key)
{
    const std::string marker = std::string("\"") + key + "\"";
    size_t position = json.find(marker);
    if (position == std::string::npos) return {};
    position = json.find(':', position + marker.size());
    if (position == std::string::npos) return {};
    do {
        ++position;
    } while (position < json.size() &&
             std::isspace(static_cast<unsigned char>(json[position])));
    if (position >= json.size() || json[position] != '"') return {};
    const size_t begin = ++position;
    bool escaped = false;
    for (; position < json.size(); ++position) {
        const char c = json[position];
        if (!escaped && c == '"') return json.substr(begin, position - begin);
        escaped = !escaped && c == '\\';
        if (c != '\\') escaped = false;
    }
    return {};
}

void* __cdecl HookFromJson(void* resultObject, const void* jsonObject,
                           void* parseError)
{
    const char* data = nullptr;
    int size = 0;
    if (ReadQByteArray(jsonObject, data, size) && data && size > 0) {
        const std::string json(data, static_cast<size_t>(size));
        if (json.find("\"versionNum\"") != std::string::npos &&
            json.find("\"versionInfo\"") != std::string::npos &&
            json.find("\"downloadUrl\"") != std::string::npos) {
            const std::string remote = ExtractJsonString(json, "versionNum");
            Log("response schema matched versionNum=" +
                (remote.empty() ? std::string("unreadable") : remote) +
                " has_createTime=" +
                (json.find("\"createTime\"") != std::string::npos ? "true"
                                                                     : "false") +
                " bytes=" + std::to_string(size));
        }
    }
    return g_fromJson(resultObject, jsonObject, parseError);
}

void SignalLauncherEvent(const wchar_t* eventName)
{
    wchar_t name[128]{};
    _snwprintf_s(name, _TRUNCATE, L"Local\\%ls-%lu", eventName,
                 GetCurrentProcessId());
    HANDLE event = OpenEventW(EVENT_MODIFY_STATE, FALSE, name);
    if (event) {
        SetEvent(event);
        CloseHandle(event);
    }
}

void SignalBlockedCheck()
{
    SignalLauncherEvent(L"RenpyThiefVersionGuardBlockedCheck");
}

void SignalHookResult(bool success)
{
    SignalLauncherEvent(success ? L"RenpyThiefVersionGuardHookReady"
                                : L"RenpyThiefVersionGuardFailed");
}

DWORD WINAPI Start(void*)
{
    LoadConfiguration();
    if (g_mode == GuardMode::Lock && g_localVersion.empty()) {
        Log("initialization failed reason=local_version_unavailable");
        g_ready.store(-1);
        SignalHookResult(false);
        return 1;
    }
    HMODULE core = GetModuleHandleW(L"Qt5Core.dll");
    HMODULE network = GetModuleHandleW(L"Qt5Network.dll");
    if (!core) core = LoadLibraryW(L"Qt5Core.dll");
    if (!network) network = LoadLibraryW(L"Qt5Network.dll");
    if (!core || !network || MH_Initialize() != MH_OK) {
        Log("initialization failed reason=module_or_minhook");
        g_ready.store(-1);
        SignalHookResult(false);
        return 1;
    }

    void* getTarget = GetProcAddress(
        network,
        "?get@QNetworkAccessManager@@QAEPAVQNetworkReply@@ABVQNetworkRequest@@@Z");
    void* createRequestTarget = GetProcAddress(
        network,
        "?createRequest@QNetworkAccessManager@@MAEPAVQNetworkReply@@W4Operation@1@ABVQNetworkRequest@@PAVQIODevice@@@Z");
    void* jsonTarget = GetProcAddress(
        core,
        "?fromJson@QJsonDocument@@SA?AV1@ABVQByteArray@@PAUQJsonParseError@@@Z");
    const MH_STATUS getCreated = getTarget
        ? MH_CreateHook(getTarget, reinterpret_cast<void*>(&HookNetworkGet),
                        reinterpret_cast<void**>(&g_networkGet))
        : MH_ERROR_NOT_EXECUTABLE;
    const MH_STATUS createRequestCreated = createRequestTarget
        ? MH_CreateHook(createRequestTarget,
                        reinterpret_cast<void*>(&HookCreateRequest),
                        reinterpret_cast<void**>(&g_createRequest))
        : MH_ERROR_NOT_EXECUTABLE;
    const MH_STATUS jsonCreated = jsonTarget
        ? MH_CreateHook(jsonTarget, reinterpret_cast<void*>(&HookFromJson),
                        reinterpret_cast<void**>(&g_fromJson))
        : MH_ERROR_NOT_EXECUTABLE;
    if (getCreated != MH_OK || createRequestCreated != MH_OK ||
        jsonCreated != MH_OK) {
        Log("hook create failed get_status=" + std::to_string(getCreated) +
            " create_request_status=" +
            std::to_string(createRequestCreated) +
            " json_status=" + std::to_string(jsonCreated));
        g_ready.store(-1);
        SignalHookResult(false);
        return 1;
    }

    const MH_STATUS enabled = MH_EnableHook(MH_ALL_HOOKS);
    Log("state=hook_ready hook_enable_status=" + std::to_string(enabled) +
        " blocked_count=0 get_hook=true create_request_hook=true "
        "json_observer=true");
    g_ready.store(enabled == MH_OK ? 1 : -1);
    SignalHookResult(enabled == MH_OK);
    return enabled == MH_OK ? 0 : 1;
}

} // namespace

extern "C" __declspec(dllexport) long __cdecl versionguard_status()
{
    return g_ready.load();
}

extern "C" __declspec(dllexport) unsigned long __cdecl
versionguard_blocked_checks()
{
    return g_blockedChecks.load();
}

extern "C" __declspec(dllexport) unsigned long __cdecl
versionguard_get_matches()
{
    return g_getMatches.load();
}

extern "C" __declspec(dllexport) unsigned long __cdecl
versionguard_create_request_matches()
{
    return g_createRequestMatches.load();
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
