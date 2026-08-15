#include <Windows.h>

#include "guardlaunch_policy.h"
#include "session_compat_policy.h"

int wmain()
{
    if (ClassifyBlockedWait(WAIT_OBJECT_0) !=
        BlockedWaitAction::Confirmed) {
        return 1;
    }
    if (ClassifyBlockedWait(WAIT_TIMEOUT) !=
        BlockedWaitAction::ContinueUnconfirmed) {
        return 2;
    }
    if (ClassifyBlockedWait(WAIT_OBJECT_0 + 1) !=
            BlockedWaitAction::FailClosed ||
        ClassifyBlockedWait(WAIT_OBJECT_0 + 2) !=
            BlockedWaitAction::FailClosed ||
        ClassifyBlockedWait(WAIT_FAILED) !=
            BlockedWaitAction::FailClosed) {
        return 3;
    }
    if (!IsBlockedChildEnvironmentName(L"QT_PLUGIN_PATH") ||
        !IsBlockedChildEnvironmentName(L"qt_qpa_platform") ||
        !IsBlockedChildEnvironmentName(L"QT_QPA_PLATFORM_PLUGIN_PATH") ||
        !IsBlockedChildEnvironmentName(L"QTDIR") ||
        !IsBlockedChildEnvironmentName(L"QTWEBENGINEPROCESS_PATH") ||
        !IsBlockedChildEnvironmentName(L"QML2_IMPORT_PATH")) {
        return 4;
    }
    if (IsBlockedChildEnvironmentName(L"PATH") ||
        IsBlockedChildEnvironmentName(L"SystemRoot") ||
        IsBlockedChildEnvironmentName(L"LOCALAPPDATA")) {
        return 5;
    }
    if (!IsBlockedChildEnvironmentName(L"UPSTREAM_API_KEY") ||
        !IsBlockedChildEnvironmentName(L"OPENAI_API_KEY") ||
        !IsBlockedChildEnvironmentName(L"BRIDGE_LOG_CONTENT") ||
        !IsBlockedChildEnvironmentName(L"VENDOR_TOKEN")) {
        return 6;
    }
    const std::string record = LocalSessionRecordText();
    if (record.find("username=local") == std::string::npos ||
        record.find(kLocalSessionPasswordHex) == std::string::npos ||
        std::string(kLocalSessionPasswordHex).size() != 64) {
        return 7;
    }
    if (!ShouldWriteLocalSessionRecord(false, false, false, 0) ||
        !ShouldWriteLocalSessionRecord(true, false, false, 0) ||
        ShouldWriteLocalSessionRecord(true, false, false, 2) ||
        ShouldWriteLocalSessionRecord(true, true, false, 0) ||
        ShouldWriteLocalSessionRecord(true, false, true, 0)) {
        return 8;
    }
    return 0;
}
