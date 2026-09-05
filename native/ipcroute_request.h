#pragma once

#include <cctype>
#include <cstring>
#include <string>

struct TranslationRequestShape {
    bool http = false;
    std::string method;
    std::string path;
    std::string version;
    std::string queryKeys;
    std::string headerNames;
    std::string contentType;
    std::string bodyKind;
    std::string bodyKeys;
    size_t bodyBytes = 0;
};

inline void AppendSafeToken(std::string& list, const std::string& token)
{
    if (token.empty() || token.size() > 64) return;
    for (unsigned char c : token) {
        if (!std::isalnum(c) && c != '_' && c != '-' && c != '.') return;
    }
    if (list.find("|" + token + "|") != std::string::npos) return;
    if (list.empty()) list = "|";
    list += token;
    list += "|";
}

inline std::string PercentEncodeQuery(const std::string& value)
{
    static const char hex[] = "0123456789ABCDEF";
    std::string out;
    out.reserve(value.size() * 3);
    for (unsigned char c : value) {
        if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            out.push_back(static_cast<char>(c));
        } else {
            out.push_back('%');
            out.push_back(hex[c >> 4]);
            out.push_back(hex[c & 15]);
        }
    }
    return out;
}

inline bool DecodePercent(const std::string& value, std::string& out)
{
    out.clear();
    out.reserve(value.size());
    for (size_t i = 0; i < value.size(); ++i) {
        const char c = value[i];
        if (c == '+') {
            out.push_back(' ');
            continue;
        }
        if (c != '%') {
            out.push_back(c);
            continue;
        }
        if (i + 2 >= value.size()) return false;
        const auto hex = [](char item) -> int {
            if (item >= '0' && item <= '9') return item - '0';
            if (item >= 'a' && item <= 'f') return item - 'a' + 10;
            if (item >= 'A' && item <= 'F') return item - 'A' + 10;
            return -1;
        };
        const int high = hex(value[i + 1]);
        const int low = hex(value[i + 2]);
        if (high < 0 || low < 0) return false;
        out.push_back(static_cast<char>((high << 4) | low));
        i += 2;
    }
    return true;
}

inline bool SplitRequestLine(const std::string& request, std::string& method,
                             std::string& target, std::string& version)
{
    size_t end = request.find("\r\n");
    if (end == std::string::npos) end = request.find('\n');
    if (end == std::string::npos) return false;
    std::string line = request.substr(0, end);
    if (!line.empty() && line.back() == '\r') line.pop_back();
    const size_t first = line.find(' ');
    if (first == std::string::npos || first == 0) return false;
    const size_t second = line.find(' ', first + 1);
    if (second == std::string::npos || second == first + 1) return false;
    method = line.substr(0, first);
    target = line.substr(first + 1, second - first - 1);
    version = line.substr(second + 1);
    return !method.empty() && !target.empty() && !version.empty();
}

inline size_t HeaderBlockEnd(const std::string& request, size_t& bodyStart)
{
    const size_t crlf = request.find("\r\n\r\n");
    if (crlf != std::string::npos) {
        bodyStart = crlf + 4;
        return crlf;
    }
    const size_t lf = request.find("\n\n");
    if (lf != std::string::npos) {
        bodyStart = lf + 2;
        return lf;
    }
    bodyStart = std::string::npos;
    return std::string::npos;
}

inline void CollectHeaderNames(const std::string& request, size_t headerEnd,
                               TranslationRequestShape& shape)
{
    if (headerEnd == std::string::npos) return;
    size_t lineStart = request.find('\n');
    if (lineStart == std::string::npos) return;
    ++lineStart;
    while (lineStart < headerEnd) {
        size_t lineEnd = request.find('\n', lineStart);
        if (lineEnd == std::string::npos || lineEnd > headerEnd) {
            lineEnd = headerEnd;
        }
        std::string line = request.substr(lineStart, lineEnd - lineStart);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        const size_t colon = line.find(':');
        if (colon != std::string::npos) {
            std::string name = line.substr(0, colon);
            while (!name.empty() && (name.back() == ' ' || name.back() == '\t')) {
                name.pop_back();
            }
            AppendSafeToken(shape.headerNames, name);
            std::string lower = name;
            for (char& c : lower) {
                c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            }
            if (lower == "content-type") {
                std::string value = line.substr(colon + 1);
                size_t begin = 0;
                while (begin < value.size() &&
                       (value[begin] == ' ' || value[begin] == '\t')) {
                    ++begin;
                }
                const size_t semi = value.find(';', begin);
                shape.contentType = value.substr(
                    begin, (semi == std::string::npos ? value.size() : semi) -
                               begin);
            }
        }
        lineStart = lineEnd + 1;
    }
}

inline bool HeaderValue(const std::string& request, size_t headerEnd,
                        const char* wanted, std::string& value)
{
    value.clear();
    if (headerEnd == std::string::npos) return false;
    const size_t wantedLength = strlen(wanted);
    size_t lineStart = request.find('\n');
    if (lineStart == std::string::npos) return false;
    ++lineStart;
    while (lineStart < headerEnd) {
        size_t lineEnd = request.find('\n', lineStart);
        if (lineEnd == std::string::npos || lineEnd > headerEnd) {
            lineEnd = headerEnd;
        }
        if (lineEnd > lineStart + wantedLength &&
            request[lineStart + wantedLength] == ':') {
            bool same = true;
            for (size_t i = 0; i < wantedLength; ++i) {
                const char left = static_cast<char>(std::tolower(
                    static_cast<unsigned char>(request[lineStart + i])));
                const char right = static_cast<char>(std::tolower(
                    static_cast<unsigned char>(wanted[i])));
                if (left != right) {
                    same = false;
                    break;
                }
            }
            if (!same) {
                lineStart = lineEnd + 1;
                continue;
            }
            std::string raw =
                request.substr(lineStart + wantedLength + 1,
                               lineEnd - (lineStart + wantedLength + 1));
            if (!raw.empty() && raw.back() == '\r') raw.pop_back();
            size_t begin = 0;
            while (begin < raw.size() &&
                   (raw[begin] == ' ' || raw[begin] == '\t')) {
                ++begin;
            }
            value = raw.substr(begin);
            return true;
        }
        lineStart = lineEnd + 1;
    }
    return false;
}

inline bool ParseContentLength(const std::string& request, size_t headerEnd,
                               size_t& length)
{
    std::string raw;
    if (!HeaderValue(request, headerEnd, "Content-Length", raw) &&
        !HeaderValue(request, headerEnd, "content-length", raw)) {
        return false;
    }
    if (raw.empty()) return false;
    length = 0;
    for (char c : raw) {
        if (c < '0' || c > '9') return false;
        const size_t digit = static_cast<size_t>(c - '0');
        if (length > (static_cast<size_t>(-1) - digit) / 10) return false;
        length = length * 10 + digit;
    }
    return true;
}

inline void CollectQueryKeys(const std::string& query, std::string& keys)
{
    size_t offset = 0;
    while (offset <= query.size()) {
        const size_t end = query.find('&', offset);
        const size_t itemEnd = end == std::string::npos ? query.size() : end;
        const size_t equals = query.find('=', offset);
        const size_t nameEnd =
            (equals != std::string::npos && equals < itemEnd) ? equals : itemEnd;
        if (nameEnd > offset) {
            AppendSafeToken(keys, query.substr(offset, nameEnd - offset));
        }
        if (end == std::string::npos) break;
        offset = end + 1;
    }
}

inline bool QueryRawValue(const std::string& query, const char* key,
                          std::string& value)
{
    const size_t keyLength = strlen(key);
    size_t offset = 0;
    while (offset <= query.size()) {
        const size_t end = query.find('&', offset);
        const size_t itemEnd = end == std::string::npos ? query.size() : end;
        const size_t equals = query.find('=', offset);
        if (equals != std::string::npos && equals < itemEnd &&
            equals - offset == keyLength &&
            query.compare(offset, keyLength, key) == 0) {
            value = query.substr(equals + 1, itemEnd - (equals + 1));
            return true;
        }
        if (end == std::string::npos) break;
        offset = end + 1;
    }
    return false;
}

inline void CollectJsonKeys(const std::string& json, std::string& keys)
{
    size_t i = 0;
    while (i < json.size()) {
        if (json[i] != '"') {
            ++i;
            continue;
        }
        const size_t begin = ++i;
        bool escaped = false;
        for (; i < json.size(); ++i) {
            if (!escaped && json[i] == '"') break;
            escaped = !escaped && json[i] == '\\';
            if (json[i] != '\\') escaped = false;
        }
        if (i >= json.size()) break;
        const std::string name = json.substr(begin, i - begin);
        ++i;
        while (i < json.size() &&
               std::isspace(static_cast<unsigned char>(json[i]))) {
            ++i;
        }
        if (i < json.size() && json[i] == ':') AppendSafeToken(keys, name);
        ++i;
    }
}

inline bool ExtractJsonString(const std::string& json, const char* key,
                              std::string& value)
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
    if (position >= json.size() || json[position] != '"') return false;
    ++position;
    bool escaped = false;
    std::string raw;
    for (; position < json.size(); ++position) {
        const char c = json[position];
        if (!escaped && c == '"') {
            value = raw;
            return true;
        }
        if (escaped) {
            raw.push_back(c);
            escaped = false;
            continue;
        }
        if (c == '\\') {
            escaped = true;
            continue;
        }
        raw.push_back(c);
    }
    return false;
}

inline bool FirstStringField(const std::string& json, const char* const* keys,
                             size_t count, std::string& value)
{
    for (size_t i = 0; i < count; ++i) {
        if (ExtractJsonString(json, keys[i], value) && !value.empty()) {
            return true;
        }
    }
    return false;
}

inline bool FirstQueryField(const std::string& query, const char* const* keys,
                            size_t count, std::string& encoded)
{
    for (size_t i = 0; i < count; ++i) {
        if (QueryRawValue(query, keys[i], encoded) && !encoded.empty()) {
            return true;
        }
    }
    return false;
}

inline std::string DescribeRequestShape(const TranslationRequestShape& shape)
{
    if (!shape.http) return "request_shape http=false";
    return "request_shape method=" + shape.method + " path=" + shape.path +
           " version=" + shape.version + " query_keys=" +
           (shape.queryKeys.empty() ? std::string("|") : shape.queryKeys) +
           " headers=" +
           (shape.headerNames.empty() ? std::string("|") : shape.headerNames) +
           " content_type=" +
           (shape.contentType.empty() ? std::string("-") : shape.contentType) +
           " body_kind=" +
           (shape.bodyKind.empty() ? std::string("none") : shape.bodyKind) +
           " body_keys=" +
           (shape.bodyKeys.empty() ? std::string("|") : shape.bodyKeys) +
           " body_bytes=" + std::to_string(shape.bodyBytes);
}

inline bool IsEmbedCipherRequest(const TranslationRequestShape& shape)
{
    return shape.http && shape.method == "POST" && shape.path == "/path" &&
           shape.bodyKind == "json" &&
           shape.bodyKeys.find("|msg|") != std::string::npos &&
           shape.bodyKeys.find("|sign|") != std::string::npos;
}

inline bool ExtractBridgeQuery(const std::string& request, std::string& query,
                               TranslationRequestShape& shape)
{
    query.clear();
    shape = {};
    std::string method;
    std::string target;
    std::string version;
    if (!SplitRequestLine(request, method, target, version)) {
        return false;
    }
    shape.http = true;
    shape.method = method;
    shape.version = version;

    std::string urlQuery;
    const size_t qmark = target.find('?');
    if (qmark == std::string::npos) {
        shape.path = target;
    } else {
        shape.path = target.substr(0, qmark);
        urlQuery = target.substr(qmark + 1);
        CollectQueryKeys(urlQuery, shape.queryKeys);
    }

    size_t bodyStart = std::string::npos;
    const size_t headerEnd = HeaderBlockEnd(request, bodyStart);
    CollectHeaderNames(request, headerEnd, shape);
    std::string body;
    if (bodyStart != std::string::npos && bodyStart < request.size()) {
        body = request.substr(bodyStart);
        shape.bodyBytes = body.size();
    }

    const std::string media = shape.contentType;
    std::string mediaLower = media;
    for (char& c : mediaLower) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    if (body.empty()) {
        shape.bodyKind = "none";
    } else if (mediaLower.find("json") != std::string::npos ||
               (!body.empty() && (body[0] == '{' || body[0] == '['))) {
        shape.bodyKind = "json";
        CollectJsonKeys(body, shape.bodyKeys);
    } else if (mediaLower.find("x-www-form-urlencoded") != std::string::npos ||
               body.find('=') != std::string::npos) {
        shape.bodyKind = "form";
        CollectQueryKeys(body, shape.bodyKeys);
    } else {
        shape.bodyKind = "raw";
    }

    static const char* const textKeys[] = {
        "text", "srcText", "sourceText", "originText", "content", "q"};
    static const char* const fromKeys[] = {
        "from", "srcLang", "fromLang", "sourceLang", "sl"};
    static const char* const toKeys[] = {
        "to", "destLang", "toLang", "targetLang", "tl"};

    std::string textEncoded;
    std::string fromEncoded;
    std::string toEncoded;
    if (!FirstQueryField(urlQuery, textKeys, 6, textEncoded) &&
        shape.bodyKind == "form") {
        FirstQueryField(body, textKeys, 6, textEncoded);
        FirstQueryField(body, fromKeys, 5, fromEncoded);
        FirstQueryField(body, toKeys, 5, toEncoded);
    } else {
        FirstQueryField(urlQuery, fromKeys, 5, fromEncoded);
        FirstQueryField(urlQuery, toKeys, 5, toEncoded);
    }

    if (textEncoded.empty() && shape.bodyKind == "json") {
        std::string text;
        std::string from;
        std::string to;
        if (FirstStringField(body, textKeys, 6, text)) {
            textEncoded = PercentEncodeQuery(text);
        }
        if (FirstStringField(body, fromKeys, 5, from)) {
            fromEncoded = PercentEncodeQuery(from);
        }
        if (FirstStringField(body, toKeys, 5, to)) {
            toEncoded = PercentEncodeQuery(to);
        }
    }

    if (textEncoded.empty()) return false;
    if (fromEncoded.empty()) fromEncoded = "auto";
    if (toEncoded.empty()) toEncoded = "zh";
    query = "from=" + fromEncoded + "&to=" + toEncoded + "&text=" + textEncoded;
    return method == "GET" || method == "POST" || method == "PUT";
}
