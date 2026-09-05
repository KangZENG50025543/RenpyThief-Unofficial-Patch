#pragma once

#include <cctype>
#include <cstring>
#include <string>

#include "ipcroute_request.h"

inline bool IsLoopbackHttpHost(const std::string& host)
{
    std::string lower;
    lower.reserve(host.size());
    for (unsigned char c : host) {
        lower.push_back(static_cast<char>(std::tolower(c)));
    }
    return lower == "127.0.0.1" || lower == "localhost" || lower == "::1";
}

inline bool JsonHasTruthyFlag(const std::string& json, const char* key)
{
    const std::string marker = std::string("\"") + key + "\"";
    size_t position = json.find(marker);
    if (position == std::string::npos) return false;
    position = json.find(':', position + marker.size());
    if (position == std::string::npos) return false;
    do {
        ++position;
    } while (position < json.size() &&
             std::isspace(static_cast<unsigned char>(json[position])));
    if (position + 4 <= json.size() &&
        json.compare(position, 4, "true") == 0) {
        return true;
    }
    if (position < json.size() && json[position] == '"') {
        std::string value;
        return ExtractJsonString(json, key, value) &&
               (value == "true" || value == "1" || value == "yes");
    }
    return position < json.size() && json[position] == '1';
}

inline bool IsInjectorCipherBody(const std::string& body)
{
    if (body.empty()) return false;
    if (JsonHasTruthyFlag(body, "encrypted")) return true;
    std::string keys;
    CollectJsonKeys(body, keys);
    return keys.find("|msg|") != std::string::npos &&
           keys.find("|sign|") != std::string::npos;
}

inline bool ExtractInjectorPlaintext(const std::string& body, std::string& text,
                                     std::string& from, std::string& to)
{
    text.clear();
    from.clear();
    to.clear();
    if (body.empty() || IsInjectorCipherBody(body)) return false;

    static const char* const textKeys[] = {
        "text", "srcText", "sourceText", "originText", "content", "q",
        "sentence", "hookText", "data"};
    static const char* const fromKeys[] = {
        "from", "srcLang", "fromLang", "sourceLang", "sl"};
    static const char* const toKeys[] = {
        "to", "destLang", "toLang", "targetLang", "tl"};

    if (!FirstStringField(body, textKeys, 9, text)) return false;
    FirstStringField(body, fromKeys, 5, from);
    FirstStringField(body, toKeys, 5, to);
    if (from.empty()) from = "auto";
    if (to.empty()) to = "zh";
    return !text.empty();
}

inline bool ShouldRerouteInjectorPost(const std::string& host,
                                      const std::string& body)
{
    std::string text;
    std::string from;
    std::string to;
    return IsLoopbackHttpHost(host) &&
           ExtractInjectorPlaintext(body, text, from, to);
}

inline std::string InjectorBridgeUrl(const std::string& text,
                                     const std::string& from,
                                     const std::string& to)
{
    return "http://127.0.0.1:19899/translate?from=" + PercentEncodeQuery(from) +
           "&to=" + PercentEncodeQuery(to) +
           "&text=" + PercentEncodeQuery(text);
}
