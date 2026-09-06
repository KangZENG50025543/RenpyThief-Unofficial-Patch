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

inline std::string AppendOfficialTranslateHubQuery(
    const std::string& localUrl, const std::string& encodedFrom,
    const std::string& encodedTo, const std::string& encodedText)
{
    std::string local = localUrl;
    local += local.find('?') == std::string::npos ? '?' : '&';
    local += "from=";
    local += encodedFrom;
    local += "&to=";
    local += encodedTo;
    local += "&text=";
    local += encodedText;
    return local;
}

inline bool LooksLikeOfficialTranslateReply(const std::string& json)
{
    return json.find("\"isChatGPT\"") != std::string::npos &&
           json.find("\"remainCharCount\"") != std::string::npos;
}
