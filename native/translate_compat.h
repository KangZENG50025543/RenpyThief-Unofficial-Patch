#pragma once

#include <string>

#include "version_endpoint.h"

enum class TranslateCompat {
    Pass,
    Lock,
};

inline bool ShouldHijackOfficialTranslate(bool modeLock,
                                          TranslateCompat translateCompat,
                                          OfficialApiKind kind)
{
    return modeLock && translateCompat == TranslateCompat::Lock &&
           kind == OfficialApiKind::Translate;
}

inline std::string OfficialTranslateBridgeUrl(
    const std::string& endpoint, const std::string& encodedOfficialUrl)
{
    std::string local = "http://127.0.0.1:19899/official-translate/";
    local += endpoint;
    const size_t query = encodedOfficialUrl.find('?');
    if (query == std::string::npos) {
        return local;
    }
    const size_t fragment = encodedOfficialUrl.find('#', query);
    local.append(
        encodedOfficialUrl, query,
        fragment == std::string::npos ? std::string::npos : fragment - query);
    return local;
}
