#pragma once

#include <cstdint>
#include <string>

// Local-only session marker for custom-API + session_compat=lock.
// Written next to RenpyThief.exe only when `user` is missing or empty.
// This is not an official account and must not be used to claim paid quota.
static const char kLocalSessionUserName[] = "local";
static const char kLocalSessionPasswordHex[] =
    "125aa48c93bebb655c14f0bf75113f8abec3f47561a15b4a5ef8fa4801e7217f";

inline bool ShouldWriteLocalSessionRecord(bool fileExists, bool isDirectory,
                                          bool isReparse,
                                          std::uint64_t size)
{
    if (isDirectory || isReparse) return false;
    if (!fileExists) return true;
    return size == 0;
}

inline std::string LocalSessionRecordText()
{
    return std::string("username=") + kLocalSessionUserName +
           "\r\npassword=" + kLocalSessionPasswordHex + "\r\n";
}
