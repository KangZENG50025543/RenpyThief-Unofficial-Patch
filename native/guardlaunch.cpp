#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>

#include <algorithm>
#include <cstring>
#include <cstdio>
#include <cwchar>
#include <string>
#include <vector>

#include "guardlaunch_policy.h"

namespace {

constexpr DWORD kDefaultTimeoutMs = 10000;
constexpr DWORD kBlockedCheckTimeoutMs = 20000;

std::wstring BaseName(const std::wstring& path)
{
    const size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? path : path.substr(slash + 1);
}

bool ResolveRegularFile(const wchar_t* input, const wchar_t* expectedName,
                        std::wstring& result)
{
    wchar_t full[32768]{};
    const DWORD length = GetFullPathNameW(
        input, static_cast<DWORD>(_countof(full)), full, nullptr);
    if (!length || length >= _countof(full)) return false;
    const DWORD attributes = GetFileAttributesW(full);
    if (attributes == INVALID_FILE_ATTRIBUTES ||
        (attributes & FILE_ATTRIBUTE_DIRECTORY) ||
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        _wcsicmp(BaseName(full).c_str(), expectedName) != 0) {
        return false;
    }
    result = full;
    return true;
}

std::wstring ParentDirectory(const std::wstring& path)
{
    const size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? std::wstring() : path.substr(0, slash);
}

void AppendAudit(const std::wstring& dllPath, DWORD targetPid,
                 const char* state)
{
    const std::wstring path = ParentDirectory(dllPath) +
                              L"\\versionguard.log";
    HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                              FILE_SHARE_READ | FILE_SHARE_WRITE |
                                  FILE_SHARE_DELETE,
                              nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME time{};
    GetLocalTime(&time);
    char line[512]{};
    _snprintf_s(line, _TRUNCATE,
                "%04u-%02u-%02u %02u:%02u:%02u.%03u launcher_pid=%lu "
                "target_pid=%lu %s\r\n",
                time.wYear, time.wMonth, time.wDay, time.wHour, time.wMinute,
                time.wSecond, time.wMilliseconds, GetCurrentProcessId(),
                targetPid, state);
    DWORD written = 0;
    WriteFile(file, line, static_cast<DWORD>(strlen(line)), &written, nullptr);
    CloseHandle(file);
}

std::wstring QuoteCommandLineArgument(const std::wstring& value)
{
    // Both inputs are full Windows paths. Double quotes are illegal in a path,
    // so the normal one-argument CreateProcess quoting rule is sufficient.
    return L"\"" + value + L"\"";
}

std::wstring Upper(std::wstring value)
{
    std::transform(value.begin(), value.end(), value.begin(),
                   [](wchar_t c) {
                       return static_cast<wchar_t>(towupper(c));
                   });
    return value;
}

bool EndsWith(const std::wstring& value, const wchar_t* suffix)
{
    const size_t length = wcslen(suffix);
    return value.size() >= length &&
           value.compare(value.size() - length, length, suffix) == 0;
}

bool IsSensitiveEnvironmentName(const std::wstring& original)
{
    const std::wstring name = Upper(original);
    if (name.compare(0, 9, L"UPSTREAM_") == 0 ||
        name.compare(0, 7, L"OPENAI_") == 0 ||
        name.compare(0, 9, L"DEEPSEEK_") == 0 ||
        name.compare(0, 12, L"SILICONFLOW_") == 0 ||
        name.compare(0, 6, L"BAIDU_") == 0 ||
        name.compare(0, 7, L"YOUDAO_") == 0 ||
        name.compare(0, 21, L"MICROSOFT_TRANSLATOR_") == 0 ||
        name == L"BRIDGE_LOG_CONTENT") {
        return true;
    }
    return EndsWith(name, L"_API_KEY") || EndsWith(name, L"_SECRET") ||
           EndsWith(name, L"_TOKEN") || EndsWith(name, L"_ACCESS_KEY");
}

bool BuildSanitizedEnvironment(std::vector<wchar_t>& block)
{
    LPWCH source = GetEnvironmentStringsW();
    if (!source) return false;
    for (const wchar_t* current = source; *current;
         current += wcslen(current) + 1) {
        const std::wstring entry(current);
        // Entries beginning with '=' are drive-current-directory records.
        const size_t separator = entry.find(L'=', entry[0] == L'=' ? 1 : 0);
        const std::wstring name = separator == std::wstring::npos
                                      ? entry
                                      : entry.substr(0, separator);
        if (IsSensitiveEnvironmentName(name)) continue;
        block.insert(block.end(), entry.begin(), entry.end());
        block.push_back(L'\0');
    }
    FreeEnvironmentStringsW(source);
    block.push_back(L'\0');
    return true;
}

bool IsLockConfiguration(const std::wstring& dllPath)
{
    const std::wstring ini = ParentDirectory(dllPath) +
                             L"\\versionguard.ini";
    const DWORD attributes = GetFileAttributesW(ini.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES ||
        (attributes & (FILE_ATTRIBUTE_DIRECTORY |
                       FILE_ATTRIBUTE_REPARSE_POINT))) {
        return false;
    }
    wchar_t mode[32]{};
    wchar_t version[128]{};
    GetPrivateProfileStringW(L"versionguard", L"mode", L"", mode,
                             static_cast<DWORD>(_countof(mode)), ini.c_str());
    GetPrivateProfileStringW(L"versionguard", L"local_version", L"", version,
                             static_cast<DWORD>(_countof(version)), ini.c_str());
    return _wcsicmp(mode, L"lock") == 0 && version[0] != L'\0';
}

bool InjectDll(HANDLE process, const std::wstring& dllPath, DWORD& error)
{
    error = ERROR_SUCCESS;
    const SIZE_T bytes = (dllPath.size() + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(process, nullptr, bytes,
                                  MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    SIZE_T written = 0;
    if (!remote || !WriteProcessMemory(process, remote, dllPath.c_str(), bytes,
                                       &written) || written != bytes) {
        error = GetLastError();
        if (remote) VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        return false;
    }

    HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    auto loadLibrary = reinterpret_cast<LPTHREAD_START_ROUTINE>(
        kernel32 ? GetProcAddress(kernel32, "LoadLibraryW") : nullptr);
    if (!loadLibrary) {
        error = GetLastError();
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        return false;
    }

    HANDLE thread = CreateRemoteThread(process, nullptr, 0, loadLibrary, remote,
                                       0, nullptr);
    if (!thread) {
        error = GetLastError();
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        return false;
    }
    const DWORD wait = WaitForSingleObject(thread, kDefaultTimeoutMs);
    DWORD module = 0;
    const bool loaded = wait == WAIT_OBJECT_0 &&
                        GetExitCodeThread(thread, &module) && module != 0;
    if (!loaded) {
        error = wait == WAIT_FAILED ? GetLastError() : ERROR_TIMEOUT;
    }
    CloseHandle(thread);
    if (wait == WAIT_OBJECT_0) {
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
    }
    return loaded;
}

bool HasExactModule(DWORD pid, const std::wstring& expected)
{
    HANDLE snapshot = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snapshot == INVALID_HANDLE_VALUE) return false;
    MODULEENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    bool found = false;
    if (Module32FirstW(snapshot, &entry)) {
        do {
            wchar_t full[32768]{};
            const DWORD length = GetFullPathNameW(
                entry.szExePath, static_cast<DWORD>(_countof(full)), full,
                nullptr);
            if (length && length < _countof(full) &&
                _wcsicmp(full, expected.c_str()) == 0) {
                found = true;
                break;
            }
        } while (Module32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return found;
}

void TerminateSuspended(PROCESS_INFORMATION& process)
{
    TerminateProcess(process.hProcess, ERROR_CANCELLED);
    WaitForSingleObject(process.hProcess, 5000);
}

}  // namespace

int wmain(int argc, wchar_t** argv)
{
    if (argc != 3) {
        std::fwprintf(stderr,
                      L"usage: guardlaunch.exe <RenpyThief.exe> "
                      L"<versionguard.dll>\n");
        return 2;
    }

    std::wstring translator;
    std::wstring guard;
    if (!ResolveRegularFile(argv[1], L"RenpyThief.exe", translator)) {
        std::fwprintf(stderr,
                      L"refusing guarded launch: translator must be a "
                      L"regular, non-reparse RenpyThief.exe file\n");
        return 3;
    }
    if (!ResolveRegularFile(argv[2], L"versionguard.dll", guard) ||
        !IsLockConfiguration(guard)) {
        std::fwprintf(stderr,
                      L"refusing guarded launch: versionguard.dll and a "
                      L"lock-mode versionguard.ini are required\n");
        return 4;
    }

    std::vector<wchar_t> environment;
    if (!BuildSanitizedEnvironment(environment)) {
        std::fwprintf(stderr, L"could not create sanitized environment: %lu\n",
                      GetLastError());
        return 5;
    }

    std::wstring commandLine = QuoteCommandLineArgument(translator);
    std::vector<wchar_t> mutableCommand(commandLine.begin(), commandLine.end());
    mutableCommand.push_back(L'\0');
    const std::wstring workingDirectory = ParentDirectory(translator);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(translator.c_str(), mutableCommand.data(), nullptr,
                        nullptr, FALSE,
                        CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                        environment.data(), workingDirectory.c_str(), &startup,
                        &process)) {
        std::fwprintf(stderr, L"CreateProcessW failed: %lu\n", GetLastError());
        return 6;
    }

    wchar_t readyName[128]{};
    wchar_t blockedName[128]{};
    wchar_t failedName[128]{};
    _snwprintf_s(readyName, _TRUNCATE,
                 L"Local\\RenpyThiefVersionGuardHookReady-%lu",
                 process.dwProcessId);
    _snwprintf_s(blockedName, _TRUNCATE,
                 L"Local\\RenpyThiefVersionGuardBlockedCheck-%lu",
                 process.dwProcessId);
    _snwprintf_s(failedName, _TRUNCATE,
                 L"Local\\RenpyThiefVersionGuardFailed-%lu",
                 process.dwProcessId);
    HANDLE ready = CreateEventW(nullptr, TRUE, FALSE, readyName);
    const DWORD readyCreateError = GetLastError();
    HANDLE blocked = CreateEventW(nullptr, TRUE, FALSE, blockedName);
    const DWORD blockedCreateError = GetLastError();
    HANDLE failed = CreateEventW(nullptr, TRUE, FALSE, failedName);
    const DWORD failedCreateError = GetLastError();
    if (!ready || !blocked || !failed ||
        readyCreateError == ERROR_ALREADY_EXISTS ||
        blockedCreateError == ERROR_ALREADY_EXISTS ||
        failedCreateError == ERROR_ALREADY_EXISTS) {
        std::fwprintf(stderr,
                      L"could not create fresh versionguard readiness "
                      L"events (error=%lu)\n",
                      GetLastError());
        if (ready) CloseHandle(ready);
        if (blocked) CloseHandle(blocked);
        if (failed) CloseHandle(failed);
        TerminateSuspended(process);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return 7;
    }

    DWORD injectError = ERROR_SUCCESS;
    if (!InjectDll(process.hProcess, guard, injectError)) {
        std::fwprintf(stderr, L"versionguard injection failed: %lu\n",
                      injectError);
        CloseHandle(ready);
        CloseHandle(blocked);
        CloseHandle(failed);
        TerminateSuspended(process);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return 8;
    }

    HANDLE waits[] = {ready, failed, process.hProcess};
    const DWORD wait = WaitForMultipleObjects(
        static_cast<DWORD>(_countof(waits)), waits, FALSE, kDefaultTimeoutMs);
    const bool hookReady = wait == WAIT_OBJECT_0 &&
                           HasExactModule(process.dwProcessId, guard);
    if (!hookReady) {
        const wchar_t* reason = wait == WAIT_OBJECT_0 + 1
                                    ? L"hook reported failure"
                                : wait == WAIT_OBJECT_0 + 2
                                    ? L"process exited while suspended"
                                : wait == WAIT_TIMEOUT ? L"readiness timeout"
                                                       : L"readiness wait failed";
        std::fwprintf(stderr, L"versionguard not ready: %ls (wait=%lu)\n",
                      reason, wait);
        CloseHandle(ready);
        CloseHandle(blocked);
        CloseHandle(failed);
        TerminateSuspended(process);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return 9;
    }

    AppendAudit(guard, process.dwProcessId,
                "state=hook_ready blocked_check=pending action=resume");
    if (ResumeThread(process.hThread) == static_cast<DWORD>(-1)) {
        std::fwprintf(stderr, L"ResumeThread failed: %lu\n", GetLastError());
        CloseHandle(ready);
        CloseHandle(blocked);
        CloseHandle(failed);
        TerminateSuspended(process);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return 10;
    }

    HANDLE blockedWaits[] = {blocked, failed, process.hProcess};
    const DWORD blockedWait = WaitForMultipleObjects(
        static_cast<DWORD>(_countof(blockedWaits)), blockedWaits, FALSE,
        kBlockedCheckTimeoutMs);
    const BlockedWaitAction blockedAction = ClassifyBlockedWait(blockedWait);
    if (blockedAction == BlockedWaitAction::ContinueUnconfirmed) {
        AppendAudit(
            guard, process.dwProcessId,
            "state=blocked_check_missing reason=timeout action=continue_unconfirmed");
        std::fwprintf(
            stderr,
            L"WARNING: no known version check was observed within %lu ms; "
            L"continuing with update protection unconfirmed.\n",
            kBlockedCheckTimeoutMs);
        std::fflush(stderr);
    } else if (blockedAction == BlockedWaitAction::FailClosed) {
        const char* state = blockedWait == WAIT_OBJECT_0 + 1
                                ? "state=blocked_check_missing reason=hook_failed action=fail_closed"
                            : blockedWait == WAIT_OBJECT_0 + 2
                                ? "state=blocked_check_missing reason=process_exited action=fail_closed"
                                : "state=blocked_check_missing reason=wait_failed action=fail_closed";
        AppendAudit(guard, process.dwProcessId, state);
        std::fwprintf(stderr,
                      L"versionguard hook was ready but no version check was "
                      L"blocked (wait=%lu)\n",
                      blockedWait);
        CloseHandle(ready);
        CloseHandle(blocked);
        CloseHandle(failed);
        TerminateSuspended(process);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return 11;
    } else {
        AppendAudit(
            guard, process.dwProcessId,
            "state=blocked_check blocked_count_at_least=1 action=release_launcher");
    }

    std::wprintf(L"Started guarded RenpyThief PID %lu.\n",
                 process.dwProcessId);
    std::fflush(stdout);
    CloseHandle(ready);
    CloseHandle(blocked);
    CloseHandle(failed);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
