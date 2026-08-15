#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iphlpapi.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <cwctype>
#include <limits>
#include <string>

#include "MinHook.h"

namespace {

constexpr unsigned short kBridgePort = 19899;
constexpr size_t kMaxRequestBytes = 1024 * 1024;
constexpr size_t kMaxResponseBytes = 16 * 1024 * 1024;
constexpr int kClientTimeoutMs = 10000;
constexpr int kBridgeTimeoutMs = 130000;
constexpr int kErrorSendTimeoutMs = 1000;
// A real game can submit roughly sixty dialogue fragments in one startup burst.
// Keep all of them inside the local bridge while its much smaller upstream
// semaphore controls cloud concurrency.
constexpr long kMaxConcurrentWorkers = 64;
constexpr ULONGLONG kListenerGroupWindowMs = 5000;

enum class RouteMode {
    Observe,
    Hijack,
};

struct WorkerContext {
    SOCKET client = INVALID_SOCKET;
};

using WSAAcceptFn = SOCKET (WSAAPI*)(SOCKET, sockaddr*, LPINT,
                                      LPCONDITIONPROC, DWORD_PTR);
using ListenFn = int (WSAAPI*)(SOCKET, int);

std::wstring g_dir;
CRITICAL_SECTION g_logLock;
bool g_logLockReady = false;
CRITICAL_SECTION g_stateLock;
bool g_stateLockReady = false;
RouteMode g_mode = RouteMode::Observe;
WSAAcceptFn g_wsaAccept = nullptr;
ListenFn g_listen = nullptr;
std::atomic<long> g_ready{0};
std::atomic<unsigned short> g_dynamicBase{0};
ULONGLONG g_listenerSeen[65536]{};
HANDLE g_workerSlots = nullptr;

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
    const std::wstring path = g_dir + L"\\ipcroute.log";
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
                   [](wchar_t c) { return static_cast<wchar_t>(towlower(c)); });
    return value;
}

void LoadConfiguration()
{
    wchar_t value[64]{};
    const std::wstring path = g_dir + L"\\ipcroute.ini";
    GetPrivateProfileStringW(L"ipcroute", L"mode", L"observe", value,
                             static_cast<DWORD>(_countof(value)), path.c_str());
    const std::wstring mode = TrimLower(value);
    // Deliberately fail closed: only this exact value transfers socket ownership.
    g_mode = mode == L"hijack" ? RouteMode::Hijack : RouteMode::Observe;
    Log(std::string("configuration mode=") +
        (g_mode == RouteMode::Hijack ? "hijack" : "observe") +
        " target=dynamic-loopback-base bridge=127.0.0.1:19899");
}

bool GetSocketAddress(SOCKET socket, bool peer, sockaddr_storage& storage,
                      int& length)
{
    length = sizeof(storage);
    const int result = peer
        ? getpeername(socket, reinterpret_cast<sockaddr*>(&storage), &length)
        : getsockname(socket, reinterpret_cast<sockaddr*>(&storage), &length);
    return result == 0;
}

bool IsLoopback(const sockaddr_storage& storage)
{
    if (storage.ss_family == AF_INET) {
        const auto* address = reinterpret_cast<const sockaddr_in*>(&storage);
        return (ntohl(address->sin_addr.s_addr) & 0xff000000UL) ==
               0x7f000000UL;
    }
    if (storage.ss_family == AF_INET6) {
        const auto* address = reinterpret_cast<const sockaddr_in6*>(&storage);
        return IN6_IS_ADDR_LOOPBACK(&address->sin6_addr) != 0;
    }
    return false;
}

unsigned short AddressPort(const sockaddr_storage& storage)
{
    if (storage.ss_family == AF_INET) {
        return ntohs(reinterpret_cast<const sockaddr_in*>(&storage)->sin_port);
    }
    if (storage.ss_family == AF_INET6) {
        return ntohs(reinterpret_cast<const sockaddr_in6*>(&storage)->sin6_port);
    }
    return 0;
}

bool IsTargetListener(SOCKET listener)
{
    const int saved = WSAGetLastError();
    sockaddr_storage local{};
    int length = 0;
    const unsigned short dynamicBase = g_dynamicBase.load();
    const bool target = dynamicBase != 0 &&
                        GetSocketAddress(listener, false, local, length) &&
                        IsLoopback(local) &&
                        AddressPort(local) == dynamicBase;
    WSASetLastError(saved);
    return target;
}

bool IsTargetConnection(SOCKET socket)
{
    const int saved = WSAGetLastError();
    sockaddr_storage local{};
    sockaddr_storage peer{};
    int localLength = 0;
    int peerLength = 0;
    const unsigned short dynamicBase = g_dynamicBase.load();
    const bool target = dynamicBase != 0 &&
        GetSocketAddress(socket, false, local, localLength) &&
        GetSocketAddress(socket, true, peer, peerLength) && IsLoopback(local) &&
        IsLoopback(peer) && AddressPort(local) == dynamicBase;
    WSASetLastError(saved);
    return target;
}

void RecordLoopbackListener(unsigned short port)
{
    if (!g_stateLockReady || port == 0) return;
    const ULONGLONG now = GetTickCount64();
    unsigned short discovered = 0;

    EnterCriticalSection(&g_stateLock);
    g_listenerSeen[port] = now;
    if (g_dynamicBase.load() == 0) {
        const unsigned int first = port >= 2 ? port - 2 : 1;
        const unsigned int last = (std::min)(
            static_cast<unsigned int>(port), 65533U);
        for (unsigned int base = first; base <= last; ++base) {
            const ULONGLONG firstSeen = g_listenerSeen[base];
            const ULONGLONG secondSeen = g_listenerSeen[base + 1];
            const ULONGLONG thirdSeen = g_listenerSeen[base + 2];
            if (!firstSeen || !secondSeen || !thirdSeen) continue;
            const ULONGLONG earliest = (std::min)(firstSeen,
                (std::min)(secondSeen, thirdSeen));
            const ULONGLONG latest = (std::max)(firstSeen,
                (std::max)(secondSeen, thirdSeen));
            if (latest - earliest <= kListenerGroupWindowMs) {
                discovered = static_cast<unsigned short>(base);
                g_dynamicBase.store(discovered);
                break;
            }
        }
    }
    LeaveCriticalSection(&g_stateLock);

    if (discovered) {
        Log("dynamic_base=" + std::to_string(discovered) +
            " listeners=" + std::to_string(discovered) + "," +
            std::to_string(discovered + 1) + "," +
            std::to_string(discovered + 2));
    }
}

int WSAAPI HookListen(SOCKET socket, int backlog)
{
    const int result = g_listen(socket, backlog);
    const int resultError = WSAGetLastError();
    if (result == 0) {
        sockaddr_storage local{};
        int length = 0;
        if (GetSocketAddress(socket, false, local, length) && IsLoopback(local))
            RecordLoopbackListener(AddressPort(local));
    }
    WSASetLastError(resultError);
    return result;
}

bool IsIpv4LoopbackAddress(DWORD networkOrderAddress)
{
    return (ntohl(networkOrderAddress) & 0xff000000UL) == 0x7f000000UL;
}

void ScanExistingLoopbackListeners()
{
    DWORD size = 0;
    DWORD status = GetExtendedTcpTable(nullptr, &size, FALSE, AF_INET,
                                       TCP_TABLE_OWNER_PID_LISTENER, 0);
    if (status != ERROR_INSUFFICIENT_BUFFER || size == 0) {
        Log("existing_loopback_scan=skipped win32=" + std::to_string(status));
        return;
    }

    void* memory = HeapAlloc(GetProcessHeap(), 0, size);
    if (!memory) {
        Log("existing_loopback_scan=alloc_failed");
        return;
    }

    status = GetExtendedTcpTable(memory, &size, FALSE, AF_INET,
                                 TCP_TABLE_OWNER_PID_LISTENER, 0);
    unsigned counted = 0;
    if (status == NO_ERROR) {
        const auto* table =
            static_cast<const MIB_TCPTABLE_OWNER_PID*>(memory);
        const DWORD self = GetCurrentProcessId();
        for (DWORD index = 0; index < table->dwNumEntries; ++index) {
            const MIB_TCPROW_OWNER_PID& row = table->table[index];
            if (row.dwOwningPid != self) continue;
            if (!IsIpv4LoopbackAddress(row.dwLocalAddr)) continue;
            const auto port = static_cast<unsigned short>(
                ntohs(static_cast<unsigned short>(row.dwLocalPort)));
            if (port == 0) continue;
            RecordLoopbackListener(port);
            ++counted;
        }
    }
    HeapFree(GetProcessHeap(), 0, memory);

    Log("existing_loopback_listeners=" + std::to_string(counted) +
        (status == NO_ERROR ? "" : " win32=" + std::to_string(status)));
}

ULONGLONG DeadlineFromNow(int milliseconds)
{
    return GetTickCount64() + static_cast<ULONGLONG>(milliseconds);
}

bool SetNonBlocking(SOCKET socket)
{
    u_long nonBlocking = 1;
    return ioctlsocket(socket, FIONBIO, &nonBlocking) == 0;
}

bool WaitSocket(SOCKET socket, bool writable, ULONGLONG deadline)
{
    for (;;) {
        const ULONGLONG now = GetTickCount64();
        if (now >= deadline) return false;
        const ULONGLONG remaining = deadline - now;

        fd_set ready{};
        fd_set failed{};
        FD_ZERO(&ready);
        FD_ZERO(&failed);
        FD_SET(socket, &ready);
        FD_SET(socket, &failed);
        timeval timeout{};
        timeout.tv_sec = static_cast<long>(remaining / 1000);
        timeout.tv_usec = static_cast<long>((remaining % 1000) * 1000);
        const int selected = select(0, writable ? nullptr : &ready,
                                    writable ? &ready : nullptr, &failed,
                                    &timeout);
        if (selected > 0) {
            if (FD_ISSET(socket, &failed)) return false;
            return FD_ISSET(socket, &ready) != 0;
        }
        if (selected == 0) return false;
        if (WSAGetLastError() != WSAEINTR) return false;
    }
}

bool SendAll(SOCKET socket, const char* data, size_t size, ULONGLONG deadline)
{
    while (size > 0) {
        const int chunk = static_cast<int>((std::min)(
            size, static_cast<size_t>(0x7fffffff)));
        const int sent = send(socket, data, chunk, 0);
        if (sent > 0) {
            data += sent;
            size -= static_cast<size_t>(sent);
            continue;
        }
        if (sent == 0) return false;
        const int error = WSAGetLastError();
        if (error == WSAEWOULDBLOCK) {
            if (!WaitSocket(socket, true, deadline)) return false;
            continue;
        }
        if (error != WSAEINTR) return false;
    }
    return true;
}

int ReceiveAvailable(SOCKET socket, char* buffer, int size,
                     ULONGLONG deadline)
{
    for (;;) {
        const int received = recv(socket, buffer, size, 0);
        if (received >= 0) return received;
        const int error = WSAGetLastError();
        if (error == WSAEWOULDBLOCK) {
            if (!WaitSocket(socket, false, deadline)) return SOCKET_ERROR;
            continue;
        }
        if (error != WSAEINTR) return SOCKET_ERROR;
    }
}

bool ReceiveHeaders(SOCKET socket, std::string& request, ULONGLONG deadline)
{
    char buffer[4096];
    while (request.size() < kMaxRequestBytes) {
        const size_t remaining = kMaxRequestBytes - request.size();
        const int capacity = static_cast<int>((std::min)(remaining,
            sizeof(buffer)));
        const int received = ReceiveAvailable(socket, buffer, capacity, deadline);
        if (received <= 0) return false;
        request.append(buffer, static_cast<size_t>(received));
        if (request.find("\r\n\r\n") != std::string::npos ||
            request.find("\n\n") != std::string::npos) {
            return true;
        }
    }
    return false;
}

bool HasQueryParameter(const std::string& query, const char* wanted,
                       bool requireValue)
{
    const size_t wantedLength = strlen(wanted);
    size_t offset = 0;
    while (offset <= query.size()) {
        const size_t end = query.find('&', offset);
        const size_t itemEnd = end == std::string::npos ? query.size() : end;
        const size_t equals = query.find('=', offset);
        if (equals != std::string::npos && equals < itemEnd &&
            equals - offset == wantedLength &&
            query.compare(offset, wantedLength, wanted) == 0) {
            return !requireValue || equals + 1 < itemEnd;
        }
        if (end == std::string::npos) break;
        offset = end + 1;
    }
    return false;
}

bool ParseTranslationQuery(const std::string& request, std::string& query)
{
    const size_t lineEnd = request.find("\r\n");
    const size_t fallbackEnd = request.find('\n');
    const size_t end = lineEnd != std::string::npos ? lineEnd : fallbackEnd;
    if (end == std::string::npos) return false;

    const std::string line = request.substr(0, end);
    const size_t firstSpace = line.find(' ');
    const size_t secondSpace = firstSpace == std::string::npos
        ? std::string::npos
        : line.find(' ', firstSpace + 1);
    if (firstSpace == std::string::npos || secondSpace == std::string::npos ||
        line.find(' ', secondSpace + 1) != std::string::npos ||
        line.compare(0, firstSpace, "GET") != 0) {
        return false;
    }

    const std::string target =
        line.substr(firstSpace + 1, secondSpace - firstSpace - 1);
    const std::string version = line.substr(secondSpace + 1);
    if (target.size() < 3 || target.compare(0, 2, "/?") != 0 ||
        (version != "HTTP/1.1" && version != "HTTP/1.0")) {
        return false;
    }

    query = target.substr(2);
    return HasQueryParameter(query, "from", false) &&
           HasQueryParameter(query, "to", false) &&
           HasQueryParameter(query, "text", true);
}

bool EqualsAsciiNoCase(const std::string& left, const char* right)
{
    const size_t length = strlen(right);
    if (left.size() != length) return false;
    for (size_t i = 0; i < length; ++i) {
        if (std::tolower(static_cast<unsigned char>(left[i])) !=
            std::tolower(static_cast<unsigned char>(right[i]))) {
            return false;
        }
    }
    return true;
}

std::string TrimAscii(const std::string& value)
{
    size_t first = 0;
    while (first < value.size() &&
           (value[first] == ' ' || value[first] == '\t')) {
        ++first;
    }
    size_t last = value.size();
    while (last > first &&
           (value[last - 1] == ' ' || value[last - 1] == '\t')) {
        --last;
    }
    return value.substr(first, last - first);
}

bool ParseContentLength(const std::string& value, size_t& length)
{
    if (value.empty()) return false;
    size_t parsed = 0;
    for (char c : value) {
        if (c < '0' || c > '9') return false;
        const size_t digit = static_cast<size_t>(c - '0');
        if (parsed > ((std::numeric_limits<size_t>::max)() - digit) / 10)
            return false;
        parsed = parsed * 10 + digit;
    }
    length = parsed;
    return true;
}

bool ParseBridgeResponseHeaders(const std::string& response, size_t headerEnd,
                                size_t& totalBytes)
{
    const size_t statusEnd = response.find("\r\n");
    if (statusEnd == std::string::npos || statusEnd >= headerEnd) return false;
    const std::string status = response.substr(0, statusEnd);
    const bool httpVersion = status.compare(0, 9, "HTTP/1.0 ") == 0 ||
                             status.compare(0, 9, "HTTP/1.1 ") == 0;
    if (!httpVersion || status.size() < 12 ||
        !std::isdigit(static_cast<unsigned char>(status[9])) ||
        !std::isdigit(static_cast<unsigned char>(status[10])) ||
        !std::isdigit(static_cast<unsigned char>(status[11])) ||
        (status.size() > 12 && status[12] != ' ')) {
        return false;
    }
    const int statusCode = (status[9] - '0') * 100 +
                           (status[10] - '0') * 10 + (status[11] - '0');
    if (statusCode < 200 || statusCode >= 300) return false;

    bool foundLength = false;
    size_t contentLength = 0;
    size_t offset = statusEnd + 2;
    while (offset < headerEnd) {
        const size_t lineEnd = response.find("\r\n", offset);
        if (lineEnd == std::string::npos || lineEnd > headerEnd) return false;
        const size_t colon = response.find(':', offset);
        if (colon == std::string::npos || colon >= lineEnd) return false;
        const std::string name = response.substr(offset, colon - offset);
        const std::string value = TrimAscii(
            response.substr(colon + 1, lineEnd - colon - 1));
        if (EqualsAsciiNoCase(name, "Content-Length")) {
            if (foundLength || !ParseContentLength(value, contentLength))
                return false;
            foundLength = true;
        } else if (EqualsAsciiNoCase(name, "Transfer-Encoding")) {
            return false;
        }
        offset = lineEnd + 2;
    }
    if (!foundLength) return false;

    const size_t headerBytes = headerEnd + 4;
    if (contentLength > kMaxResponseBytes - headerBytes) return false;
    totalBytes = headerBytes + contentLength;
    return true;
}

bool ReadCompleteResponse(SOCKET socket, std::string& response,
                          ULONGLONG deadline)
{
    char buffer[8192];
    size_t totalBytes = 0;
    bool headersParsed = false;
    for (;;) {
        if (!headersParsed) {
            const size_t headerEnd = response.find("\r\n\r\n");
            if (headerEnd != std::string::npos) {
                if (!ParseBridgeResponseHeaders(response, headerEnd, totalBytes))
                    return false;
                headersParsed = true;
            }
        }
        if (headersParsed) {
            if (response.size() == totalBytes) return true;
            if (response.size() > totalBytes) return false;
        }

        const size_t limit = headersParsed ? totalBytes : kMaxResponseBytes;
        if (response.size() >= limit) return false;
        const int capacity = static_cast<int>((std::min)(
            limit - response.size(), sizeof(buffer)));
        const int received = ReceiveAvailable(socket, buffer, capacity, deadline);
        if (received <= 0) return false;
        response.append(buffer, static_cast<size_t>(received));
    }
}

bool FetchBridgeResponse(const std::string& query, std::string& response)
{
    SOCKET bridge = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (bridge == INVALID_SOCKET) return false;
    if (!SetNonBlocking(bridge)) {
        closesocket(bridge);
        return false;
    }
    const ULONGLONG deadline = DeadlineFromNow(kBridgeTimeoutMs);

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(kBridgePort);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    const int connected = connect(bridge,
        reinterpret_cast<const sockaddr*>(&address), sizeof(address));
    if (connected == SOCKET_ERROR) {
        const int error = WSAGetLastError();
        if ((error != WSAEWOULDBLOCK && error != WSAEINPROGRESS &&
             error != WSAEALREADY) || !WaitSocket(bridge, true, deadline)) {
            closesocket(bridge);
            return false;
        }
        int socketError = 0;
        int errorLength = sizeof(socketError);
        if (getsockopt(bridge, SOL_SOCKET, SO_ERROR,
                       reinterpret_cast<char*>(&socketError),
                       &errorLength) == SOCKET_ERROR || socketError != 0) {
            closesocket(bridge);
            return false;
        }
    }

    const std::string request =
        "GET /translate?" + query + " HTTP/1.1\r\n"
        "Host: 127.0.0.1:19899\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n\r\n";
    const bool sent = SendAll(bridge, request.data(), request.size(), deadline);
    const bool received =
        sent && ReadCompleteResponse(bridge, response, deadline);
    closesocket(bridge);
    return received;
}

void SendHttpError(SOCKET client, int status, const char* reason,
                   const char* body)
{
    const std::string response =
        "HTTP/1.1 " + std::to_string(status) + " " + reason + "\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Length: " + std::to_string(strlen(body)) + "\r\n"
        "Connection: close\r\n\r\n" + body;
    SendAll(client, response.data(), response.size(),
            DeadlineFromNow(kErrorSendTimeoutMs));
}

DWORD WINAPI RouteWorker(void* opaque)
{
    WorkerContext* context = static_cast<WorkerContext*>(opaque);
    const SOCKET client = context->client;
    HeapFree(GetProcessHeap(), 0, context);

    const ULONGLONG started = GetTickCount64();
    std::string request;
    std::string query;
    std::string response;
    const char* failureStage = "client_nonblocking";
    bool bridgeResponseReady = false;
    bool success = false;

    if (SetNonBlocking(client)) {
        failureStage = "client_request_read";
        if (ReceiveHeaders(client, request,
                           DeadlineFromNow(kClientTimeoutMs))) {
            failureStage = "request_parse";
            if (ParseTranslationQuery(request, query)) {
                failureStage = "bridge_fetch";
                if (FetchBridgeResponse(query, response)) {
                    bridgeResponseReady = true;
                }
            }
        }
    }

    if (bridgeResponseReady) {
        failureStage = "client_response_send";
        success = SendAll(client, response.data(), response.size(),
                          DeadlineFromNow(kClientTimeoutMs));
        if (success) failureStage = "none";
    } else {
        SendHttpError(client, 502, "Bad Gateway",
                      "ipcroute bridge routing failed");
    }
    shutdown(client, SD_BOTH);
    closesocket(client);

    Log(std::string("route result=") + (success ? "ok" : "failed") +
        " request_bytes=" + std::to_string(request.size()) +
        " response_bytes=" + std::to_string(response.size()) +
        " failure_stage=" + failureStage +
        " elapsed_ms=" + std::to_string(GetTickCount64() - started));
    ReleaseSemaphore(g_workerSlots, 1, nullptr);
    return success ? 0 : 1;
}

void RejectOwnedConnection(SOCKET socket, int status, const char* reason,
                           const char* body)
{
    if (SetNonBlocking(socket)) SendHttpError(socket, status, reason, body);
    shutdown(socket, SD_BOTH);
    closesocket(socket);
}

SOCKET WSAAPI HookWSAAccept(SOCKET listener, sockaddr* address,
                            LPINT addressLength, LPCONDITIONPROC condition,
                            DWORD_PTR callbackData)
{
    const bool targetListener = IsTargetListener(listener);
    const SOCKET accepted = g_wsaAccept(listener, address, addressLength,
                                        condition, callbackData);
    const int resultError = WSAGetLastError();

    if (accepted == INVALID_SOCKET || g_mode != RouteMode::Hijack ||
        !targetListener || !IsTargetConnection(accepted)) {
        WSASetLastError(resultError);
        return accepted;
    }

    if (!g_workerSlots ||
        WaitForSingleObject(g_workerSlots, 0) != WAIT_OBJECT_0) {
        RejectOwnedConnection(accepted, 503, "Service Unavailable",
                              "ipcroute concurrency limit reached");
        Log("hijack rejected=overload response=503");
        WSASetLastError(WSAEWOULDBLOCK);
        return INVALID_SOCKET;
    }

    WorkerContext* context = static_cast<WorkerContext*>(
        HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(WorkerContext)));
    HANDLE thread = nullptr;
    if (context) {
        context->client = accepted;
        thread = CreateThread(nullptr, 0, RouteWorker, context, 0, nullptr);
    }
    if (!thread) {
        if (context) HeapFree(GetProcessHeap(), 0, context);
        ReleaseSemaphore(g_workerSlots, 1, nullptr);
        RejectOwnedConnection(accepted, 502, "Bad Gateway",
                              "ipcroute worker creation failed");
        Log("hijack worker_start=failed response=502");
    } else {
        CloseHandle(thread);
        Log("hijack worker_start=ok");
    }

    // The accepted socket now belongs to RouteWorker. Qt must not see it and
    // must finish draining this readiness notification normally.
    WSASetLastError(WSAEWOULDBLOCK);
    return INVALID_SOCKET;
}

DWORD WINAPI Start(void*)
{
    LoadConfiguration();
    g_workerSlots = CreateSemaphoreW(nullptr, kMaxConcurrentWorkers,
                                    kMaxConcurrentWorkers, nullptr);
    if (!g_workerSlots) {
        Log("initialization failed: concurrency semaphore");
        g_ready.store(-1);
        return 1;
    }
    HMODULE ws2 = GetModuleHandleW(L"ws2_32.dll");
    if (!ws2) ws2 = LoadLibraryW(L"ws2_32.dll");
    if (!ws2 || MH_Initialize() != MH_OK) {
        Log("initialization failed");
        g_ready.store(-1);
        return 1;
    }

    void* listenTarget = GetProcAddress(ws2, "listen");
    const MH_STATUS listenCreated = listenTarget
        ? MH_CreateHook(listenTarget, reinterpret_cast<void*>(&HookListen),
                        reinterpret_cast<void**>(&g_listen))
        : MH_ERROR_NOT_EXECUTABLE;
    void* acceptTarget = GetProcAddress(ws2, "WSAAccept");
    const MH_STATUS acceptCreated = acceptTarget
        ? MH_CreateHook(acceptTarget, reinterpret_cast<void*>(&HookWSAAccept),
                        reinterpret_cast<void**>(&g_wsaAccept))
        : MH_ERROR_NOT_EXECUTABLE;
    if (listenCreated != MH_OK || acceptCreated != MH_OK) {
        Log("hook create failed listen_status=" +
            std::to_string(listenCreated) + " accept_status=" +
            std::to_string(acceptCreated));
        g_ready.store(-1);
        return 1;
    }

    const MH_STATUS enabled = MH_EnableHook(MH_ALL_HOOKS);
    Log("hook enabled status=" + std::to_string(enabled) +
        " dynamic_base=waiting");
    if (enabled == MH_OK) {
        // RenpyThief often bind/listen the three translation ports before this
        // DLL is injected. listen() will not fire again, so snapshot current
        // loopback listeners owned by this process.
        ScanExistingLoopbackListeners();
    }
    g_ready.store(enabled == MH_OK ? 1 : -1);
    return enabled == MH_OK ? 0 : 1;
}

} // namespace

extern "C" __declspec(dllexport) long __cdecl ipcroute_status()
{
    return g_ready.load();
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        wchar_t path[MAX_PATH]{};
        GetModuleFileNameW(module, path, static_cast<DWORD>(_countof(path)));
        if (wchar_t* slash = wcsrchr(path, L'\\')) {
            *slash = L'\0';
            g_dir = path;
        } else {
            g_dir = L".";
        }
        InitializeCriticalSection(&g_logLock);
        g_logLockReady = true;
        InitializeCriticalSection(&g_stateLock);
        g_stateLockReady = true;
        HANDLE thread = CreateThread(nullptr, 0, Start, nullptr, 0, nullptr);
        if (thread) CloseHandle(thread);
    }
    return TRUE;
}
