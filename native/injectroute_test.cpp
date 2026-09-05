#include <cstdio>
#include <string>

#include "injectroute_request.h"

static int g_failures = 0;

void Expect(bool ok, const char* name)
{
    if (!ok) {
        std::fprintf(stderr, "FAIL %s\n", name);
        ++g_failures;
    }
}

int wmain()
{
    Expect(IsLoopbackHttpHost("127.0.0.1"), "loopback ipv4");
    Expect(IsLoopbackHttpHost("LOCALHOST"), "loopback name");
    Expect(!IsLoopbackHttpHost("api.renpy.fun"), "reject official host");

    Expect(IsInjectorCipherBody(
               "{\"msg\":\"abc=\",\"nonce\":1,\"sign\":\"deadbeef\"}"),
           "embed cipher");
    Expect(IsInjectorCipherBody("{\"data\":\"AAAA\",\"encrypted\":true}"),
           "encrypted flag");
    Expect(!IsInjectorCipherBody("{\"text\":\"hello\",\"from\":\"ja\"}"),
           "plain text is not cipher");

    std::string text;
    std::string from;
    std::string to;
    Expect(ExtractInjectorPlaintext(
               "{\"text\":\"hello\",\"from\":\"ja\",\"to\":\"zh\"}", text, from,
               to) &&
               text == "hello" && from == "ja" && to == "zh",
           "json text fields");
    Expect(ExtractInjectorPlaintext("{\"data\":\"plain line\"}", text, from,
                                    to) &&
               text == "plain line" && from == "auto" && to == "zh",
           "plain data field");
    Expect(!ExtractInjectorPlaintext(
               "{\"data\":\"AAAA\",\"encrypted\":true}", text, from, to),
           "cipher data is not plaintext");
    Expect(!ExtractInjectorPlaintext(
               "{\"msg\":\"abc=\",\"sign\":\"deadbeef\"}", text, from, to),
           "embed is not plaintext");

    Expect(ShouldRerouteInjectorPost(
               "127.0.0.1", "{\"sentence\":\"menu\"}"),
           "reroute loopback sentence");
    Expect(!ShouldRerouteInjectorPost(
               "127.0.0.1",
               "{\"msg\":\"abc=\",\"nonce\":1,\"sign\":\"deadbeef\"}"),
           "do not reroute embed");
    Expect(!ShouldRerouteInjectorPost("example.com", "{\"text\":\"hello\"}"),
           "do not reroute off-box");

    const std::string url = InjectorBridgeUrl("a b", "auto", "zh");
    Expect(url.find("http://127.0.0.1:19899/translate?") == 0 &&
               url.find("text=a%20b") != std::string::npos,
           "bridge url");

    if (g_failures) {
        std::fprintf(stderr, "%d injectroute tests failed\n", g_failures);
        return 1;
    }
    std::printf("injectroute tests passed\n");
    return 0;
}
