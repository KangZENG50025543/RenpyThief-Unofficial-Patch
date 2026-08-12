#pragma once

#include <Windows.h>

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
