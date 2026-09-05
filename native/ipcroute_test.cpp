#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>

#include <cstdio>
#include <string>
#include <vector>

#include "ipcroute_request.h"

namespace {

constexpr unsigned short kTestDynamicBase = 24377;
constexpr int kExpectedWorkerCap = 64;

struct BridgeContext {
    SOCKET listener = INVALID_SOCKET;
    std::string request;
    std::string response;
    DWORD closeDelayMs = 0;
    bool success = false;
};

bool SendAll(SOCKET socket, const char* data, size_t size)
{
    while (size > 0) {
        const int sent = send(socket, data, static_cast<int>(size), 0);
        if (sent <= 0) return false;
        data += sent;
        size -= static_cast<size_t>(sent);
    }
    return true;
}

bool ReceiveHeaders(SOCKET socket, std::string& value)
{
    char buffer[1024];
    while (value.size() < 65536) {
        const int received = recv(socket, buffer, sizeof(buffer), 0);
        if (received <= 0) return false;
        value.append(buffer, static_cast<size_t>(received));
        if (value.find("\r\n\r\n") != std::string::npos) return true;
    }
    return false;
}

bool ReceiveToClose(SOCKET socket, std::string& value)
{
    char buffer[1024];
    while (value.size() < 65536) {
        const int received = recv(socket, buffer, sizeof(buffer), 0);
        if (received == 0) return true;
        if (received == SOCKET_ERROR) return false;
        value.append(buffer, static_cast<size_t>(received));
    }
    return false;
}

DWORD WINAPI BridgeWorker(void* opaque)
{
    auto* context = static_cast<BridgeContext*>(opaque);
    SOCKET client = accept(context->listener, nullptr, nullptr);
    if (client == INVALID_SOCKET) return 1;
    const bool received = ReceiveHeaders(client, context->request);
    const bool sent = received && SendAll(client, context->response.data(),
                                          context->response.size());
    if (context->closeDelayMs) Sleep(context->closeDelayMs);
    shutdown(client, SD_BOTH);
    closesocket(client);
    context->success = received && sent;
    return context->success ? 0 : 1;
}

bool BindListener(const char* host, unsigned short port, SOCKET& listener)
{
    listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == INVALID_SOCKET) return false;
    BOOL exclusive = TRUE;
    if (setsockopt(listener, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
                   reinterpret_cast<const char*>(&exclusive),
                   sizeof(exclusive)) == SOCKET_ERROR) {
        closesocket(listener);
        return false;
    }

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    if (inet_pton(AF_INET, host, &address.sin_addr) != 1 ||
        bind(listener, reinterpret_cast<const sockaddr*>(&address),
             sizeof(address)) == SOCKET_ERROR ||
        listen(listener, SOMAXCONN) == SOCKET_ERROR) {
        closesocket(listener);
        listener = INVALID_SOCKET;
        return false;
    }
    return true;
}

SOCKET ConnectGame(unsigned short port)
{
    SOCKET game = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (game == INVALID_SOCKET) return INVALID_SOCKET;
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    inet_pton(AF_INET, "127.0.0.2", &address.sin_addr);
    if (connect(game, reinterpret_cast<const sockaddr*>(&address),
                sizeof(address)) == SOCKET_ERROR) {
        closesocket(game);
        return INVALID_SOCKET;
    }
    const int timeout = 10000;
    if (setsockopt(game, SOL_SOCKET, SO_RCVTIMEO,
                   reinterpret_cast<const char*>(&timeout), sizeof(timeout)) ==
        SOCKET_ERROR) {
        closesocket(game);
        return INVALID_SOCKET;
    }
    return game;
}

bool AcceptMustBeHidden(SOCKET listener, WSAEVENT event)
{
    const DWORD waited = WSAWaitForMultipleEvents(1, &event, FALSE, 5000, FALSE);
    if (waited != WSA_WAIT_EVENT_0) return false;
    WSANETWORKEVENTS events{};
    if (WSAEnumNetworkEvents(listener, event, &events) == SOCKET_ERROR ||
        !(events.lNetworkEvents & FD_ACCEPT)) {
        return false;
    }

    sockaddr_storage peer{};
    int peerLength = sizeof(peer);
    WSASetLastError(0);
    const SOCKET hidden = WSAAccept(
        listener, reinterpret_cast<sockaddr*>(&peer), &peerLength, nullptr, 0);
    const int error = WSAGetLastError();
    if (hidden != INVALID_SOCKET) closesocket(hidden);
    return hidden == INVALID_SOCKET && error == WSAEWOULDBLOCK;
}

bool AcceptCrtMustBeHidden(SOCKET listener, WSAEVENT event)
{
    const DWORD waited = WSAWaitForMultipleEvents(1, &event, FALSE, 5000, FALSE);
    if (waited != WSA_WAIT_EVENT_0) return false;
    WSANETWORKEVENTS events{};
    if (WSAEnumNetworkEvents(listener, event, &events) == SOCKET_ERROR ||
        !(events.lNetworkEvents & FD_ACCEPT)) {
        return false;
    }

    sockaddr_storage peer{};
    int peerLength = sizeof(peer);
    WSASetLastError(0);
    const SOCKET hidden = accept(
        listener, reinterpret_cast<sockaddr*>(&peer), &peerLength);
    const int error = WSAGetLastError();
    if (hidden != INVALID_SOCKET) closesocket(hidden);
    return hidden == INVALID_SOCKET && error == WSAEWOULDBLOCK;
}

bool AcceptMustPassThrough(SOCKET listener, WSAEVENT event)
{
    const DWORD waited = WSAWaitForMultipleEvents(1, &event, FALSE, 5000, FALSE);
    if (waited != WSA_WAIT_EVENT_0) return false;
    WSANETWORKEVENTS events{};
    if (WSAEnumNetworkEvents(listener, event, &events) == SOCKET_ERROR ||
        !(events.lNetworkEvents & FD_ACCEPT)) {
        return false;
    }
    SOCKET accepted = WSAAccept(listener, nullptr, nullptr, nullptr, 0);
    if (accepted == INVALID_SOCKET) return false;
    closesocket(accepted);
    return true;
}

std::string HttpResponse(int status, const char* reason,
                         const std::string& body)
{
    return "HTTP/1.1 " + std::to_string(status) + " " + reason + "\r\n"
           "Content-Type: text/plain; charset=utf-8\r\n"
           "Content-Length: " + std::to_string(body.size()) + "\r\n"
           "Connection: close\r\n"
           "X-IpcRoute-Test: yes\r\n\r\n" + body;
}

bool ExpectExtractedText(const char* name, const std::string& request,
                         const char* method, const char* path,
                         const char* text)
{
    TranslationRequestShape shape;
    std::string query;
    if (!ExtractBridgeQuery(request, query, shape)) {
        std::fprintf(stderr, "FAIL: %s parse\n", name);
        return false;
    }
    if (shape.method != method || shape.path != path) {
        std::fprintf(stderr, "FAIL: %s shape method=%s path=%s\n", name,
                     shape.method.c_str(), shape.path.c_str());
        return false;
    }
    std::string encoded;
    std::string decoded;
    if (!QueryRawValue(query, "text", encoded) ||
        !DecodePercent(encoded, decoded) || decoded != text) {
        std::fprintf(stderr, "FAIL: %s text=%s\n", name, decoded.c_str());
        return false;
    }
    return true;
}

bool RunShapeTests()
{
    if (!ExpectExtractedText(
            "get-root",
            "GET /?from=auto&to=zh&text=Hello%20world HTTP/1.1\r\n"
            "Host: 127.0.0.2\r\n\r\n",
            "GET", "/", "Hello world")) {
        return false;
    }
    if (!ExpectExtractedText(
            "get-translate",
            "GET /translate?from=en&to=zh&text=Path%20variant HTTP/1.1\r\n\r\n",
            "GET", "/translate", "Path variant")) {
        return false;
    }
    if (!ExpectExtractedText(
            "post-form",
            "POST /translate HTTP/1.1\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: 32\r\n\r\n"
            "from=auto&to=zh&text=Form%20line",
            "POST", "/translate", "Form line")) {
        return false;
    }
    if (!ExpectExtractedText(
            "post-json",
            "POST /api HTTP/1.1\r\n"
            "Content-Type: application/json\r\n\r\n"
            "{\"text\":\"Json line\",\"from\":\"ja\",\"to\":\"zh\"}",
            "POST", "/api", "Json line")) {
        return false;
    }
    TranslationRequestShape shape;
    std::string query;
    if (ExtractBridgeQuery("OPTIONS / HTTP/1.1\r\n\r\n", query, shape)) {
        std::fprintf(stderr, "FAIL: options should not parse as translation\n");
        return false;
    }
    if (ExtractBridgeQuery(
            "POST /path?type=pt HTTP/1.1\r\n"
            "Content-Type: application/json\r\n\r\n"
            "{\"msg\":\"abc=\",\"nonce\":1,\"sign\":\"deadbeef\"}",
            query, shape) ||
        !IsEmbedCipherRequest(shape)) {
        std::fprintf(stderr, "FAIL: embed cipher should not become bridge text\n");
        return false;
    }
    return true;
}

bool RunBridgeCase(SOCKET routeListener, WSAEVENT routeEvent,
                   unsigned short routePort, SOCKET bridgeListener,
                   const char* caseName,
                   const std::string& bridgeResponse,
                   const std::string& expectedGameResponse,
                   DWORD bridgeCloseDelayMs = 0,
                   DWORD maximumGameResponseMs = INFINITE,
                   const char* targetPrefix = "/?")
{
    BridgeContext bridge{};
    bridge.listener = bridgeListener;
    bridge.response = bridgeResponse;
    bridge.closeDelayMs = bridgeCloseDelayMs;
    HANDLE bridgeThread = CreateThread(nullptr, 0, BridgeWorker, &bridge, 0,
                                       nullptr);
    if (!bridgeThread) return false;

    SOCKET game = ConnectGame(routePort);
    if (game == INVALID_SOCKET ||
        !AcceptMustBeHidden(routeListener, routeEvent)) {
        if (game != INVALID_SOCKET) closesocket(game);
        CloseHandle(bridgeThread);
        return false;
    }

    const std::string query = std::string("from=auto&to=zh&text=") + caseName;
    const std::string request =
        std::string("GET ") + targetPrefix + query + " HTTP/1.1\r\n"
        "Host: 127.0.0.2:" + std::to_string(routePort) + "\r\n"
        "Connection: Keep-Alive\r\n\r\n";

    // The route worker has already accepted a nonblocking socket. Delay and
    // split the request so its first recv must handle WSAEWOULDBLOCK.
    Sleep(150);
    const size_t split = 17;
    if (!SendAll(game, request.data(), split)) {
        closesocket(game);
        CloseHandle(bridgeThread);
        return false;
    }
    Sleep(150);
    if (!SendAll(game, request.data() + split, request.size() - split)) {
        closesocket(game);
        CloseHandle(bridgeThread);
        return false;
    }

    const ULONGLONG started = GetTickCount64();
    std::string gameResponse;
    const bool gameReceived = ReceiveToClose(game, gameResponse);
    const DWORD gameElapsed = static_cast<DWORD>(GetTickCount64() - started);
    closesocket(game);

    const DWORD joined = WaitForSingleObject(bridgeThread, 10000);
    CloseHandle(bridgeThread);
    const std::string expectedLine = "GET /translate?" + query +
                                     " HTTP/1.1\r\n";
    const bool forwarded = bridge.request.compare(
        0, expectedLine.size(), expectedLine) == 0;
    const bool timing = maximumGameResponseMs == INFINITE ||
                        gameElapsed < maximumGameResponseMs;
    return joined == WAIT_OBJECT_0 && bridge.success && gameReceived &&
           forwarded && gameResponse == expectedGameResponse && timing;
}

bool RunPostedBridgeCase(SOCKET routeListener, WSAEVENT routeEvent,
                         unsigned short routePort, SOCKET bridgeListener,
                         const char* caseName,
                         const std::string& bridgeResponse,
                         const std::string& expectedGameResponse)
{
    BridgeContext bridge{};
    bridge.listener = bridgeListener;
    bridge.response = bridgeResponse;
    HANDLE bridgeThread = CreateThread(nullptr, 0, BridgeWorker, &bridge, 0,
                                       nullptr);
    if (!bridgeThread) return false;

    SOCKET game = ConnectGame(routePort);
    if (game == INVALID_SOCKET ||
        !AcceptMustBeHidden(routeListener, routeEvent)) {
        if (game != INVALID_SOCKET) closesocket(game);
        CloseHandle(bridgeThread);
        return false;
    }

    const std::string query = std::string("from=auto&to=zh&text=") + caseName;
    const std::string request =
        "POST /translate HTTP/1.1\r\n"
        "Host: 127.0.0.2:" + std::to_string(routePort) + "\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Content-Length: " + std::to_string(query.size()) + "\r\n"
        "Connection: close\r\n\r\n" + query;

    Sleep(150);
    const size_t split = 17;
    if (!SendAll(game, request.data(), split)) {
        closesocket(game);
        CloseHandle(bridgeThread);
        return false;
    }
    Sleep(150);
    if (!SendAll(game, request.data() + split, request.size() - split)) {
        closesocket(game);
        CloseHandle(bridgeThread);
        return false;
    }

    std::string gameResponse;
    const bool gameReceived = ReceiveToClose(game, gameResponse);
    closesocket(game);
    const DWORD joined = WaitForSingleObject(bridgeThread, 10000);
    CloseHandle(bridgeThread);
    const std::string expectedLine = "GET /translate?" + query +
                                     " HTTP/1.1\r\n";
    const bool forwarded = bridge.request.compare(
        0, expectedLine.size(), expectedLine) == 0;
    return joined == WAIT_OBJECT_0 && bridge.success && gameReceived &&
           forwarded && gameResponse == expectedGameResponse;
}

bool RunEmbedIdentityCase(SOCKET routeListener, WSAEVENT routeEvent,
                          unsigned short routePort)
{
    SOCKET game = ConnectGame(routePort);
    if (game == INVALID_SOCKET ||
        !AcceptMustBeHidden(routeListener, routeEvent)) {
        if (game != INVALID_SOCKET) closesocket(game);
        return false;
    }

    const std::string body =
        "{\"msg\":\"abc=\",\"nonce\":1,\"sign\":\"deadbeef\"}";
    const std::string request =
        "POST /path?type=pt HTTP/1.1\r\n"
        "Host: 127.0.0.2:" + std::to_string(routePort) + "\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: " + std::to_string(body.size()) + "\r\n\r\n" + body;

    Sleep(150);
    if (!SendAll(game, request.data(), request.size())) {
        closesocket(game);
        return false;
    }
    std::string gameResponse;
    const bool gameReceived = ReceiveToClose(game, gameResponse);
    closesocket(game);
    return gameReceived &&
           gameResponse.find("HTTP/1.1 200 OK\r\n") == 0 &&
           gameResponse.find(body) != std::string::npos;
}

bool RunConcurrencyCase(SOCKET routeListener, WSAEVENT routeEvent,
                        unsigned short routePort)
{
    std::vector<SOCKET> held;
    held.reserve(kExpectedWorkerCap);
    for (int i = 0; i < kExpectedWorkerCap; ++i) {
        SOCKET game = ConnectGame(routePort);
        if (game == INVALID_SOCKET ||
            !AcceptMustBeHidden(routeListener, routeEvent)) {
            if (game != INVALID_SOCKET) closesocket(game);
            for (SOCKET item : held) closesocket(item);
            return false;
        }
        // Do not send a request: each worker remains inside its bounded read.
        held.push_back(game);
    }

    SOCKET overflow = ConnectGame(routePort);
    bool success = overflow != INVALID_SOCKET &&
                   AcceptMustBeHidden(routeListener, routeEvent);
    std::string response;
    if (success) success = ReceiveToClose(overflow, response);
    if (overflow != INVALID_SOCKET) closesocket(overflow);
    for (SOCKET item : held) closesocket(item);
    Sleep(250);

    return success &&
           response.find("HTTP/1.1 503 Service Unavailable\r\n") == 0 &&
           response.find("ipcroute concurrency limit reached") !=
               std::string::npos;
}

bool WriteTestIni(const std::wstring& path)
{
    static const char contents[] = "[ipcroute]\r\nmode=hijack\r\n";
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    const BOOL result = WriteFile(file, contents, sizeof(contents) - 1,
                                  &written, nullptr);
    CloseHandle(file);
    return result && written == sizeof(contents) - 1;
}

bool ReadTextFile(const std::wstring& path, std::string& value)
{
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE |
                                  FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;

    LARGE_INTEGER size{};
    const bool validSize = GetFileSizeEx(file, &size) && size.QuadPart >= 0 &&
                           size.QuadPart <= 1024 * 1024;
    if (!validSize) {
        CloseHandle(file);
        return false;
    }

    value.resize(static_cast<size_t>(size.QuadPart));
    DWORD read = 0;
    const BOOL result = value.empty() ||
        ReadFile(file, &value[0], static_cast<DWORD>(value.size()), &read,
                 nullptr);
    CloseHandle(file);
    if (!result || read != value.size()) return false;
    return true;
}

bool WaitForDynamicBaseLog(const std::wstring& path, unsigned short base)
{
    const std::string needle = "dynamic_base=" + std::to_string(base) +
                               " listeners=";
    for (int attempt = 0; attempt < 250; ++attempt) {
        std::string log;
        if (ReadTextFile(path, log) && log.find(needle) != std::string::npos) {
            return true;
        }
        Sleep(20);
    }
    return false;
}

bool WaitForStageLog(const std::wstring& path)
{
    for (int attempt = 0; attempt < 100; ++attempt) {
        std::string log;
        if (ReadTextFile(path, log) &&
            log.find("failure_stage=none elapsed_ms=") != std::string::npos &&
            log.find("failure_stage=bridge_fetch elapsed_ms=") !=
                std::string::npos &&
            log.find("failure_stage=client_request_read elapsed_ms=") !=
                std::string::npos &&
            log.find("route inspect ") != std::string::npos &&
            log.find(" text=split") != std::string::npos) {
            return true;
        }
        Sleep(20);
    }
    return false;
}

std::wstring DirectoryOf(const wchar_t* path)
{
    std::wstring value(path);
    const size_t slash = value.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"." : value.substr(0, slash);
}

void Fail(const char* message)
{
    std::fprintf(stderr, "FAIL: %s (win32=%lu wsa=%d)\n", message,
                 GetLastError(), WSAGetLastError());
}

} // namespace

int wmain(int argc, wchar_t** argv)
{
    if (!RunShapeTests()) {
        Fail("flexible translation request shapes");
        return 1;
    }

    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
        Fail("WSAStartup");
        return 1;
    }

    SOCKET bridgeListener = INVALID_SOCKET;
    // The bridge is a different process in production. Create its test listener
    // before loading ipcroute so it is not part of dynamic translator discovery.
    if (!BindListener("127.0.0.1", 19899, bridgeListener)) {
        Fail("bind isolated bridge listener");
        return 1;
    }

    wchar_t executable[MAX_PATH]{};
    GetModuleFileNameW(nullptr, executable, _countof(executable));
    const std::wstring sourceDll = argc > 1
        ? argv[1]
        : DirectoryOf(executable) + L"\\ipcroute.dll";

    wchar_t tempRoot[MAX_PATH]{};
    GetTempPathW(_countof(tempRoot), tempRoot);
    wchar_t runtimeName[64]{};
    _snwprintf_s(runtimeName, _TRUNCATE, L"ipcroute-selftest-%lu",
                 GetCurrentProcessId());
    const std::wstring runtimeDir = std::wstring(tempRoot) + runtimeName;
    const std::wstring runtimeDll = runtimeDir + L"\\ipcroute.dll";
    const std::wstring runtimeIni = runtimeDir + L"\\ipcroute.ini";
    const std::wstring runtimeLog = runtimeDir + L"\\ipcroute.log";
    if ((!CreateDirectoryW(runtimeDir.c_str(), nullptr) &&
         GetLastError() != ERROR_ALREADY_EXISTS) ||
        !CopyFileW(sourceDll.c_str(), runtimeDll.c_str(), FALSE) ||
        !WriteTestIni(runtimeIni)) {
        Fail("prepare isolated DLL/config copy");
        return 1;
    }

    // Production injects after RenpyThief has already listen()'d the three
    // consecutive translator ports. Discover those existing listeners.
    SOCKET routeListeners[3] = {
        INVALID_SOCKET, INVALID_SOCKET, INVALID_SOCKET
    };
    for (unsigned short offset = 0; offset < 3; ++offset) {
        if (!BindListener("127.0.0.2",
                          static_cast<unsigned short>(kTestDynamicBase + offset),
                          routeListeners[offset])) {
            Fail("bind existing consecutive translator listeners");
            return 1;
        }
    }

    HMODULE module = LoadLibraryW(runtimeDll.c_str());
    if (!module) {
        Fail("LoadLibrary ipcroute.dll");
        return 1;
    }
    using StatusFn = long (__cdecl*)();
    StatusFn status = reinterpret_cast<StatusFn>(
        GetProcAddress(module, "ipcroute_status"));
    if (!status) status = reinterpret_cast<StatusFn>(
        GetProcAddress(module, "_ipcroute_status"));
    if (!status) {
        Fail("resolve ipcroute_status");
        return 1;
    }
    for (int i = 0; i < 250 && status() == 0; ++i) Sleep(20);
    if (status() != 1) {
        Fail("wait for hook initialization");
        return 1;
    }
    if (!WaitForDynamicBaseLog(runtimeLog, kTestDynamicBase)) {
        Fail("snapshot existing consecutive translator listeners");
        return 1;
    }

    WSAEVENT routeEvent = WSACreateEvent();
    if (routeEvent == WSA_INVALID_EVENT ||
        WSAEventSelect(routeListeners[0], routeEvent, FD_ACCEPT) == SOCKET_ERROR) {
        Fail("configure Qt-style nonblocking base accept event");
        return 1;
    }

    // A connection on base+1 must also be stolen; only ports outside the
    // discovered triple remain visible to the original application.
    WSAEVENT secondaryEvent = WSACreateEvent();
    if (secondaryEvent == WSA_INVALID_EVENT ||
        WSAEventSelect(routeListeners[1], secondaryEvent, FD_ACCEPT) ==
            SOCKET_ERROR) {
        Fail("configure secondary accept event");
        return 1;
    }
    SOCKET secondaryClient = ConnectGame(kTestDynamicBase + 1);
    if (secondaryClient == INVALID_SOCKET ||
        !AcceptMustBeHidden(routeListeners[1], secondaryEvent)) {
        Fail("group listener +1 was not intercepted");
        return 1;
    }
    closesocket(secondaryClient);
    WSAEventSelect(routeListeners[1], WSA_INVALID_EVENT, 0);
    WSACloseEvent(secondaryEvent);

    WSAEVENT thirdEvent = WSACreateEvent();
    if (thirdEvent == WSA_INVALID_EVENT ||
        WSAEventSelect(routeListeners[2], thirdEvent, FD_ACCEPT) ==
            SOCKET_ERROR) {
        Fail("configure third accept event");
        return 1;
    }
    SOCKET thirdClient = ConnectGame(kTestDynamicBase + 2);
    if (thirdClient == INVALID_SOCKET ||
        !AcceptCrtMustBeHidden(routeListeners[2], thirdEvent)) {
        Fail("group listener +2 accept() was not intercepted");
        return 1;
    }
    closesocket(thirdClient);
    WSAEventSelect(routeListeners[2], WSA_INVALID_EVENT, 0);
    WSACloseEvent(thirdEvent);

    SOCKET outsideListener = INVALID_SOCKET;
    if (!BindListener("127.0.0.2",
                      static_cast<unsigned short>(kTestDynamicBase + 5),
                      outsideListener)) {
        Fail("bind outside-group listener");
        return 1;
    }
    WSAEVENT outsideEvent = WSACreateEvent();
    if (outsideEvent == WSA_INVALID_EVENT ||
        WSAEventSelect(outsideListener, outsideEvent, FD_ACCEPT) ==
            SOCKET_ERROR) {
        Fail("configure outside-group accept event");
        return 1;
    }
    SOCKET outsideClient = ConnectGame(kTestDynamicBase + 5);
    if (outsideClient == INVALID_SOCKET ||
        !AcceptMustPassThrough(outsideListener, outsideEvent)) {
        Fail("outside-group listener was incorrectly intercepted");
        return 1;
    }
    closesocket(outsideClient);
    WSAEventSelect(outsideListener, WSA_INVALID_EVENT, 0);
    WSACloseEvent(outsideEvent);
    closesocket(outsideListener);

    const std::string successBody = "BRIDGE_OK\n";
    const std::string successResponse =
        HttpResponse(200, "OK", successBody);
    if (!RunBridgeCase(routeListeners[0], routeEvent, kTestDynamicBase,
                       bridgeListener, "split",
                       successResponse, successResponse, 2000, 1500)) {
        Fail("delayed split request or Content-Length completion");
        return 1;
    }

    if (!RunBridgeCase(routeListeners[0], routeEvent, kTestDynamicBase,
                       bridgeListener, "pathvariant",
                       successResponse, successResponse, 0, INFINITE,
                       "/translate?")) {
        Fail("GET /translate query path");
        return 1;
    }

    if (!RunPostedBridgeCase(routeListeners[0], routeEvent, kTestDynamicBase,
                             bridgeListener, "formline",
                             successResponse, successResponse)) {
        Fail("POST form translation body");
        return 1;
    }

    if (!RunEmbedIdentityCase(routeListeners[0], routeEvent,
                              kTestDynamicBase)) {
        Fail("embed cipher identity echo");
        return 1;
    }

    const std::string errorResponse = HttpResponse(500, "Error", "bad");
    if (!RunBridgeCase(routeListeners[0], routeEvent, kTestDynamicBase,
                       bridgeListener, "non2xx",
                       errorResponse,
                       "HTTP/1.1 502 Bad Gateway\r\n"
                       "Content-Type: text/plain; charset=utf-8\r\n"
                       "Content-Length: 30\r\n"
                       "Connection: close\r\n\r\n"
                       "ipcroute bridge routing failed")) {
        Fail("non-2xx bridge response rejection");
        return 1;
    }

    const std::string truncated =
        "HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nbad";
    if (!RunBridgeCase(routeListeners[0], routeEvent, kTestDynamicBase,
                       bridgeListener, "truncated",
                       truncated,
                       "HTTP/1.1 502 Bad Gateway\r\n"
                       "Content-Type: text/plain; charset=utf-8\r\n"
                       "Content-Length: 30\r\n"
                       "Connection: close\r\n\r\n"
                       "ipcroute bridge routing failed")) {
        Fail("truncated Content-Length rejection");
        return 1;
    }

    Sleep(100);
    if (!RunConcurrencyCase(routeListeners[0], routeEvent,
                            kTestDynamicBase)) {
        Fail("64-worker concurrency cap");
        return 1;
    }

    WSAEventSelect(routeListeners[0], WSA_INVALID_EVENT, 0);
    WSACloseEvent(routeEvent);
    for (SOCKET listener : routeListeners) closesocket(listener);
    closesocket(bridgeListener);

    if (!WaitForStageLog(runtimeLog)) {
        Fail("route log failure_stage and elapsed_ms fields");
        WSACleanup();
        return 1;
    }
    WSACleanup();

    std::printf("PASS: request shapes, dynamic consecutive-listener group, "
                "outside pass-through, split nonblocking request, HTTP "
                "validation, and worker cap verified.\n");
    std::wprintf(L"SELFTEST_DIR=%ls\n", runtimeDir.c_str());
    return 0;
}
