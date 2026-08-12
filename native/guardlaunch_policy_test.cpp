#include <Windows.h>

#include "guardlaunch_policy.h"

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
    return 0;
}
