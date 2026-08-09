#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>

#include <cstdio>
#include <cwchar>
#include <string>

enum class RouteModuleCheck
{
    Clean,
    Conflict,
    Error,
};

bool IsRouteConflictName(const wchar_t* name)
{
    if (_wcsicmp(name, L"ipcroute.dll") == 0) return true;
    const size_t length = wcslen(name);
    return length >= 11 && _wcsnicmp(name, L"looptap", 7) == 0 &&
           _wcsicmp(name + length - 4, L".dll") == 0;
}

RouteModuleCheck CheckRouteModules(DWORD pid, std::wstring& conflict,
                                   DWORD& error)
{
    error = ERROR_SUCCESS;
    HANDLE snapshot = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snapshot == INVALID_HANDLE_VALUE) {
        error = GetLastError();
        return RouteModuleCheck::Error;
    }

    MODULEENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (!Module32FirstW(snapshot, &entry)) {
        error = GetLastError();
        CloseHandle(snapshot);
        return RouteModuleCheck::Error;
    }
    do {
        if (IsRouteConflictName(entry.szModule)) {
            conflict = entry.szModule;
            CloseHandle(snapshot);
            return RouteModuleCheck::Conflict;
        }
    } while (Module32NextW(snapshot, &entry));

    error = GetLastError();
    CloseHandle(snapshot);
    if (error != ERROR_NO_MORE_FILES) return RouteModuleCheck::Error;
    error = ERROR_SUCCESS;
    return RouteModuleCheck::Clean;
}

DWORD FindProcess(const wchar_t* name)
{
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return 0;
    DWORD pid = 0;
    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (_wcsicmp(entry.szExeFile, name) == 0) {
                pid = entry.th32ProcessID;
                break;
            }
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return pid;
}

bool GetProcessBaseName(DWORD pid, std::wstring& name)
{
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return false;
    bool found = false;
    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (entry.th32ProcessID == pid) {
                name = entry.szExeFile;
                found = true;
                break;
            }
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return found;
}

int wmain(int argc, wchar_t** argv)
{
    if (argc < 2) {
        std::fwprintf(stderr,
                      L"usage: netinject.exe <pid|process.exe> [dll-path] "
                      L"[--check-only]\n");
        return 2;
    }

    const bool checkOnly = argc >= 4 && _wcsicmp(argv[3], L"--check-only") == 0;
    if (argc >= 4 && !checkOnly) {
        std::fwprintf(stderr, L"unknown option: %ls\n", argv[3]);
        return 2;
    }

    wchar_t* end = nullptr;
    const unsigned long parsedPid = wcstoul(argv[1], &end, 10);
    const bool exactPid = argv[1][0] != L'\0' && end && *end == L'\0' &&
                          parsedPid != 0;
    DWORD pid = exactPid ? static_cast<DWORD>(parsedPid) : FindProcess(argv[1]);
    if (!pid) {
        std::fwprintf(stderr, L"target process not found: %ls\n", argv[1]);
        return 3;
    }

    wchar_t dllPath[32768]{};
    if (argc >= 3) {
        const DWORD length = GetFullPathNameW(
            argv[2], static_cast<DWORD>(_countof(dllPath)), dllPath, nullptr);
        if (!length || length >= _countof(dllPath)) {
            std::fwprintf(stderr, L"invalid DLL path\n");
            return 4;
        }
    } else {
        const DWORD length = GetModuleFileNameW(
            nullptr, dllPath, static_cast<DWORD>(_countof(dllPath)));
        if (!length || length >= _countof(dllPath) - 1) return 4;
        wchar_t* slash = wcsrchr(dllPath, L'\\');
        if (!slash) return 4;
        wcscpy_s(slash + 1, _countof(dllPath) - static_cast<size_t>(slash + 1 - dllPath),
                 L"nettap.dll");
    }

    const DWORD attributes = GetFileAttributesW(dllPath);
    if (attributes == INVALID_FILE_ATTRIBUTES ||
        (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
        std::fwprintf(stderr, L"DLL not found: %ls\n", dllPath);
        return 4;
    }

    const wchar_t* dllName = wcsrchr(dllPath, L'\\');
    dllName = dllName ? dllName + 1 : dllPath;
    const bool routeDll = _wcsicmp(dllName, L"ipcroute.dll") == 0;
    const bool versionGuardDll =
        _wcsicmp(dllName, L"versionguard.dll") == 0;
    const bool flowTapDll = _wcsicmp(dllName, L"flowtap.dll") == 0;
    const bool guardedRenpyDll = routeDll || versionGuardDll || flowTapDll;
    if (checkOnly && !routeDll) {
        std::fwprintf(stderr,
                      L"--check-only is supported only for ipcroute.dll\n");
        return 2;
    }
    if (guardedRenpyDll) {
        if (!exactPid) {
            std::fwprintf(stderr,
                          L"refusing guarded injection: target must be an "
                          L"explicit numeric PID\n");
            return 3;
        }
        std::wstring processName;
        if (!GetProcessBaseName(pid, processName) ||
            _wcsicmp(processName.c_str(), L"RenpyThief.exe") != 0) {
            std::fwprintf(stderr,
                          L"refusing guarded injection: PID %lu is not "
                          L"RenpyThief.exe\n",
                          pid);
            return 3;
        }
    }
    if (routeDll) {
        std::wstring conflict;
        DWORD checkError = ERROR_SUCCESS;
        const RouteModuleCheck checked =
            CheckRouteModules(pid, conflict, checkError);
        if (checked == RouteModuleCheck::Error) {
            std::fwprintf(stderr,
                          L"refusing ipcroute injection: x86 Toolhelp module "
                          L"check failed for PID %lu (error=%lu)\n",
                          pid, checkError);
            return 9;
        }
        if (checked == RouteModuleCheck::Conflict) {
            std::fwprintf(stderr,
                          L"refusing ipcroute injection: PID %lu already "
                          L"contains conflicting module %ls\n",
                          pid, conflict.c_str());
            return 10;
        }
        if (checkOnly) {
            std::wprintf(L"route module check clean for PID %lu\n", pid);
            return 0;
        }
    }

    HANDLE process = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
                                 PROCESS_VM_OPERATION | PROCESS_VM_WRITE,
                                 FALSE, pid);
    if (!process) {
        std::fwprintf(stderr, L"OpenProcess(%lu) failed: %lu\n", pid, GetLastError());
        return 5;
    }

    if (routeDll) {
        std::wstring conflict;
        DWORD checkError = ERROR_SUCCESS;
        const RouteModuleCheck checked =
            CheckRouteModules(pid, conflict, checkError);
        if (checked != RouteModuleCheck::Clean) {
            if (checked == RouteModuleCheck::Conflict) {
                std::fwprintf(stderr,
                              L"refusing ipcroute injection: PID %lu gained "
                              L"conflicting module %ls\n",
                              pid, conflict.c_str());
            } else {
                std::fwprintf(stderr,
                              L"refusing ipcroute injection: final x86 "
                              L"Toolhelp check failed (error=%lu)\n",
                              checkError);
            }
            CloseHandle(process);
            return checked == RouteModuleCheck::Conflict ? 10 : 9;
        }
    }

    const SIZE_T bytes = (wcslen(dllPath) + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(process, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    SIZE_T written = 0;
    if (!remote ||
        !WriteProcessMemory(process, remote, dllPath, bytes, &written) ||
        written != bytes) {
        std::fwprintf(stderr, L"writing DLL path failed: %lu\n", GetLastError());
        if (remote) VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        return 6;
    }

    HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    auto loadLibrary = reinterpret_cast<LPTHREAD_START_ROUTINE>(
        kernel32 ? GetProcAddress(kernel32, "LoadLibraryW") : nullptr);
    if (!loadLibrary) {
        std::fwprintf(stderr, L"LoadLibraryW address unavailable: %lu\n",
                      GetLastError());
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        return 7;
    }
    HANDLE thread = CreateRemoteThread(process, nullptr, 0, loadLibrary, remote, 0, nullptr);
    if (!thread) {
        std::fwprintf(stderr, L"CreateRemoteThread failed: %lu\n", GetLastError());
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        return 7;
    }

    const DWORD wait = WaitForSingleObject(thread, 10000);
    if (wait != WAIT_OBJECT_0) {
        const DWORD waitError = wait == WAIT_FAILED ? GetLastError() : ERROR_TIMEOUT;
        std::fwprintf(stderr,
                      L"remote LoadLibraryW did not finish (wait=%lu, "
                      L"error=%lu); remote path allocation intentionally "
                      L"retained to avoid a use-after-free\n",
                      wait, waitError);
        CloseHandle(thread);
        CloseHandle(process);
        return 8;
    }

    DWORD module = 0;
    const BOOL gotExitCode = GetExitCodeThread(thread, &module);
    CloseHandle(thread);
    const BOOL freed = VirtualFreeEx(process, remote, 0, MEM_RELEASE);
    const DWORD freeError = freed ? ERROR_SUCCESS : GetLastError();
    CloseHandle(process);

    if (!gotExitCode || module == 0) {
        std::fwprintf(stderr,
                      L"LoadLibraryW failed in target (GetExitCodeThread=%d, "
                      L"module=0x%08lX)\n",
                      gotExitCode, module);
        return 8;
    }
    if (!freed) {
        std::fwprintf(stderr,
                      L"warning: injection succeeded but remote path cleanup "
                      L"failed (error=%lu)\n",
                      freeError);
    }

    std::wprintf(L"injected %ls into pid %lu (module=0x%08lX)\n", dllPath, pid, module);
    return 0;
}
