#include <cstdio>
#include <string>
#include <vector>

#include "version_endpoint.h"

struct Case
{
    const char* url;
    bool expected;
    bool query = false;
    bool port = false;
};

int wmain()
{
    const std::vector<Case> cases{
        {"https://api.renpy.fun:5986/renpythief/getVersionInfo", true,
         false, true},
        {"https://api.renpy.fun/renpythief/getVersionInfo", true},
        {"HTTPS://API.RENPY.FUN:443/renpythief/getVersionInfo", true,
         false, true},
        {"https://api.renpy.fun:7443/renpythief/getVersionInfo?channel=stable",
         true, true, true},
        {"https://api.renpy.fun/renpythief/getVersionInfo#fragment", true},
        {"http://api.renpy.fun/renpythief/getVersionInfo", false},
        {"https://api.renpy.fun/renpythief/getVersionInfo/", false},
        {"https://api.renpy.fun/renpythief/getLatestUpdater", false},
        {"https://api.renpy.fun/renpythief/signIn", false},
        {"https://other.renpy.fun/renpythief/getVersionInfo", false},
        {"https://api.renpy.fun.example/renpythief/getVersionInfo", false},
        {"https://user@api.renpy.fun/renpythief/getVersionInfo", false},
        {"https://api.renpy.fun:notaport/renpythief/getVersionInfo", false},
        {"https://api.renpy.fun:0/renpythief/getVersionInfo", false},
        {"https://api.renpy.fun:65536/renpythief/getVersionInfo", false},
        {"https://api.renpy.fun/renpythief/getVersion%49nfo", false},
    };
    for (const Case& item : cases) {
        VersionEndpointMatch match;
        const bool actual = MatchVersionEndpoint(item.url, match);
        if (actual != item.expected ||
            (actual && (match.hasQuery != item.query ||
                        match.hasExplicitPort != item.port))) {
            std::fprintf(stderr,
                         "FAIL url=%s expected=%d actual=%d query=%d port=%d\n",
                         item.url, item.expected, actual, match.hasQuery,
                         match.hasExplicitPort);
            return 1;
        }
    }

    OfficialEndpointMatch official;
    if (!MatchOfficialEndpoint(
            "https://api.renpy.fun:5986/renpythief/signIn", official) ||
        official.endpoint != "signIn" ||
        ClassifyOfficialEndpoint(official.endpoint) !=
            OfficialApiKind::Session) {
        std::fprintf(stderr, "FAIL official session endpoint matching\n");
        return 1;
    }
    if (!MatchOfficialEndpoint(
            "https://api.renpy.fun/renpythief/getGameConfig", official) ||
        ClassifyOfficialEndpoint(official.endpoint) !=
            OfficialApiKind::Config) {
        std::fprintf(stderr, "FAIL official config endpoint matching\n");
        return 1;
    }
    if (!MatchOfficialEndpoint(
            "https://api.renpy.fun/renpythief/sendTranslate", official) ||
        ClassifyOfficialEndpoint(official.endpoint) !=
            OfficialApiKind::Translate) {
        std::fprintf(stderr, "FAIL official translate endpoint matching\n");
        return 1;
    }
    if (MatchOfficialEndpoint("http://api.renpy.fun/renpythief/signIn",
                              official) ||
        MatchOfficialEndpoint(
            "https://api.renpy.fun/renpythief/getVersionInfo/", official)) {
        std::fprintf(stderr, "FAIL official endpoint matching stayed narrow\n");
        return 1;
    }
    std::printf("PASS: normalized version endpoint matching is narrow.\n");
    return 0;
}
