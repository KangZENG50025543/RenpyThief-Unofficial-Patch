#pragma once

#include <Windows.h>
#include <algorithm>
#include <cstring>
#include <cwctype>
#include <string>

enum class BlockedWaitAction {
    Confirmed,
    ContinueUnconfirmed,
    FailClosed,
};

constexpr BlockedWaitAction ClassifyBlockedWait(DWORD waitResult) noexcept
{
    if (waitResult == WAIT_OBJECT_0) {
        return BlockedWaitAction::Confirmed;
    }
    if (waitResult == WAIT_TIMEOUT) {
        return BlockedWaitAction::ContinueUnconfirmed;
    }
    return BlockedWaitAction::FailClosed;
}

inline std::wstring EnvironmentNameUpper(std::wstring value)
{
    std::transform(value.begin(), value.end(), value.begin(),
                   [](wchar_t c) {
                       return static_cast<wchar_t>(towupper(c));
                   });
    return value;
}

inline bool EnvironmentNameEndsWith(const std::wstring& value,
                                    const wchar_t* suffix)
{
    const size_t length = wcslen(suffix);
    return value.size() >= length &&
           value.compare(value.size() - length, length, suffix) == 0;
}

// The 64-bit patch GUI is a PyQt5/PyInstaller process. If QT_PLUGIN_PATH and
// related variables leak into 32-bit RenpyThief, Qt lists the parent plugins
// then fails to initialize qwindows, and the three-port group never appears.
inline bool IsBlockedChildEnvironmentName(const std::wstring& original)
{
    const std::wstring name = EnvironmentNameUpper(original);
    if (name.compare(0, 3, L"QT_") == 0 ||
        name.compare(0, 11, L"QTWEBENGINE") == 0 ||
        name == L"QTDIR" ||
        name.compare(0, 3, L"QML") == 0 ||
        name.compare(0, 9, L"UPSTREAM_") == 0 ||
        name.compare(0, 7, L"OPENAI_") == 0 ||
        name.compare(0, 9, L"DEEPSEEK_") == 0 ||
        name.compare(0, 12, L"SILICONFLOW_") == 0 ||
        name.compare(0, 6, L"BAIDU_") == 0 ||
        name.compare(0, 7, L"YOUDAO_") == 0 ||
        name.compare(0, 21, L"MICROSOFT_TRANSLATOR_") == 0 ||
        name == L"BRIDGE_LOG_CONTENT") {
        return true;
    }
    return EnvironmentNameEndsWith(name, L"_API_KEY") ||
           EnvironmentNameEndsWith(name, L"_SECRET") ||
           EnvironmentNameEndsWith(name, L"_TOKEN") ||
           EnvironmentNameEndsWith(name, L"_ACCESS_KEY");
}
