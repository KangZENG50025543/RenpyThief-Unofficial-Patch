#pragma once

#include <algorithm>
#include <cctype>
#include <string>

struct VersionEndpointMatch
{
    bool hasQuery = false;
    bool hasExplicitPort = false;
};

struct OfficialEndpointMatch
{
    bool hasQuery = false;
    bool hasExplicitPort = false;
    std::string endpoint;
};

enum class OfficialApiKind
{
    None,
    Version,
    Session,
    Config,
    Translate,
    Other,
};

inline bool IsSafeOfficialEndpointName(const std::string& value)
{
    if (value.empty() || value.size() > 96) return false;
    return std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return std::isalnum(c) || c == '_' || c == '-';
    });
}

inline bool MatchOfficialEndpoint(const std::string& encodedUrl,
                                  OfficialEndpointMatch& match)
{
    match = {};
    const size_t schemeEnd = encodedUrl.find("://");
    if (schemeEnd == std::string::npos) return false;
    std::string scheme = encodedUrl.substr(0, schemeEnd);
    std::transform(scheme.begin(), scheme.end(), scheme.begin(),
                   [](unsigned char c) {
                       return static_cast<char>(std::tolower(c));
                   });
    // Official APIs are HTTPS. Keeping the scheme narrow avoids treating an
    // unrelated custom URL as an official compatibility target.
    if (scheme != "https") return false;

    const size_t authorityBegin = schemeEnd + 3;
    const size_t authorityEnd = encodedUrl.find_first_of("/?#", authorityBegin);
    const size_t authorityLength =
        (authorityEnd == std::string::npos ? encodedUrl.size() : authorityEnd) -
        authorityBegin;
    std::string authority = encodedUrl.substr(authorityBegin, authorityLength);
    if (authority.empty() || authority.find('@') != std::string::npos) {
        return false;
    }

    std::string host = authority;
    const size_t colon = authority.rfind(':');
    if (colon != std::string::npos) {
        // The known host is not an IPv6 literal, so exactly one colon denotes
        // an explicit port. Accept any valid port to survive an API port move.
        if (authority.find(':') != colon) return false;
        const std::string port = authority.substr(colon + 1);
        if (port.empty() ||
            !std::all_of(port.begin(), port.end(), [](unsigned char c) {
                return std::isdigit(c) != 0;
            })) {
            return false;
        }
        unsigned long parsed = 0;
        try {
            parsed = std::stoul(port);
        } catch (...) {
            return false;
        }
        if (parsed == 0 || parsed > 65535) return false;
        host.erase(colon);
        match.hasExplicitPort = true;
    }
    std::transform(host.begin(), host.end(), host.begin(),
                   [](unsigned char c) {
                       return static_cast<char>(std::tolower(c));
                   });
    if (host != "api.renpy.fun") return false;

    if (authorityEnd == std::string::npos || encodedUrl[authorityEnd] != '/') {
        return false;
    }
    const size_t pathEnd = encodedUrl.find_first_of("?#", authorityEnd);
    const std::string path = encodedUrl.substr(
        authorityEnd,
        (pathEnd == std::string::npos ? encodedUrl.size() : pathEnd) -
            authorityEnd);
    constexpr char prefix[] = "/renpythief/";
    if (path.size() <= sizeof(prefix) - 1 ||
        path.compare(0, sizeof(prefix) - 1, prefix) != 0) {
        return false;
    }
    const std::string endpoint = path.substr(sizeof(prefix) - 1);
    if (!IsSafeOfficialEndpointName(endpoint)) return false;
    match.endpoint = endpoint;

    if (pathEnd != std::string::npos) {
        const size_t fragment = encodedUrl.find('#', pathEnd);
        const size_t query = encodedUrl.find('?', pathEnd);
        match.hasQuery = query != std::string::npos &&
                         (fragment == std::string::npos || query < fragment);
    }
    return true;
}

inline bool MatchVersionEndpoint(const std::string& encodedUrl,
                                 VersionEndpointMatch& match)
{
    match = {};
    OfficialEndpointMatch official;
    if (!MatchOfficialEndpoint(encodedUrl, official) ||
        official.endpoint != "getVersionInfo") {
        return false;
    }
    match.hasQuery = official.hasQuery;
    match.hasExplicitPort = official.hasExplicitPort;
    return true;
}

inline OfficialApiKind ClassifyOfficialEndpoint(const std::string& endpoint)
{
    if (endpoint == "getVersionInfo") return OfficialApiKind::Version;
    if (endpoint == "signIn" || endpoint == "checkUserUnReadMessageCount" ||
        endpoint == "pingTest" || endpoint == "submitInject" ||
        endpoint == "submitEndGame") {
        return OfficialApiKind::Session;
    }
    if (endpoint == "sendTranslate" || endpoint == "sendMenuTranslate" ||
        endpoint == "sendOCRTranslate") {
        return OfficialApiKind::Translate;
    }
    static const char* const config[] = {
        "getFilterWord",
        "checkSwitchInjectFilesSHA1",
        "getForceGenericInjection",
        "getV8",
        "getGameConfig",
        "getUnityHook",
        "checkUnityIsInject",
        "getXUnityResizer",
        "getGameDepend",
        "getHCode",
        "getRegex",
        "getIsUE",
        "checkGameIsSupport",
        "getUnityFontBySha1",
        "downloadUnityFontWithAutoSourceSwitch",
        "checkUnityFontList",
        "getWord",
        "getLatestUpdater",
    };
    for (const char* name : config) {
        if (endpoint == name) return OfficialApiKind::Config;
    }
    return endpoint.empty() ? OfficialApiKind::None : OfficialApiKind::Other;
}
