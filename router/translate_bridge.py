#!/usr/bin/env python3
"""Local endpoint for XUnity AutoTranslator's CustomTranslate protocol.

CustomTranslate calls:
  GET /translate?from=ja&to=zh&text=...
and expects the translated text as the entire response body.

Modes:
  echo    - routing proof; returns the source text unchanged
  openai  - forwards to an OpenAI-compatible /chat/completions endpoint
  youdao  - forwards to Youdao Cloud Text Translation
  baidu   - forwards to Baidu General Text Translation
  microsoft - forwards to Microsoft Translator Text
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import itertools
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "bridge_requests.log"
LOG_LOCK = threading.Lock()
DEFAULT_MAX_CONCURRENCY = 64
DEFAULT_UPSTREAM_CONCURRENCY = 8
DEFAULT_CACHE_ENTRIES = 2_048
DEFAULT_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHE_ENTRIES = 1_000_000
MAX_CACHE_BYTES = 1024 * 1024 * 1024
MAX_UPSTREAM_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 0.15
MAX_LANGUAGE_CHARS = 64
MAX_TEXT_CHARS = 262_144
PAYLOAD_PROFILES = (
    "openai",
    "deepseek",
    "siliconflow-qwen",
    "hunyuan-mt",
)
PROMPT_MODES = ("template1", "template2", "custom")
MAX_CUSTOM_PROMPT_CHARS = 16_384
YOUDAO_MAX_TEXT_CHARS = 5_000
BAIDU_MAX_TEXT_CHARS = 1_000
MICROSOFT_MAX_TEXT_CHARS = 50_000
BAIDU_MIN_REQUEST_INTERVAL_SECONDS = 1.05
LINE_BREAK_SPLIT_PATTERN = re.compile(r"(\r\n|\n|\r)")
YOUDAO_ENDPOINT = "https://openapi.youdao.com/api"
BAIDU_ENDPOINT = "https://fanyi-api.baidu.com/api/trans/vip/translate"
MICROSOFT_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
DEDICATED_MODES = ("youdao", "baidu", "microsoft")

_CANONICAL_ALIASES = {
    "": "auto",
    "auto": "auto",
    "ja": "ja",
    "jp": "ja",
    "japanese": "ja",
    "zh": "zh-hans",
    "zh-cn": "zh-hans",
    "zh-chs": "zh-hans",
    "zh-hans": "zh-hans",
    "zh-tw": "zh-hant",
    "zh-hk": "zh-hant",
    "zh-cht": "zh-hant",
    "zh-hant": "zh-hant",
    "en": "en",
    "ko": "ko",
    "kor": "ko",
}

_LANGUAGE_MAPS = {
    "youdao": {
        "auto": "auto", "ja": "ja", "zh-hans": "zh-CHS",
        "zh-hant": "zh-CHT", "en": "en", "ko": "ko",
    },
    "baidu": {
        "auto": "auto", "ja": "jp", "zh-hans": "zh",
        "zh-hant": "cht", "en": "en", "ko": "kor",
    },
    "microsoft": {
        "auto": "auto", "ja": "ja", "zh-hans": "zh-Hans",
        "zh-hant": "zh-Hant", "en": "en", "ko": "ko",
    },
}


class ProviderError(RuntimeError):
    """A provider rejected a structurally valid local translation request."""


def provider_language(provider: str, value: str) -> str:
    normalized = (value or "auto").strip().lower().replace("_", "-")
    canonical = _CANONICAL_ALIASES.get(normalized, normalized)
    try:
        return _LANGUAGE_MAPS[provider][canonical]
    except KeyError as error:
        raise ValueError(
            f"unsupported language for {provider}: {normalized or 'auto'}"
        ) from error


def prompt_language_name(value: str, *, chinese: bool = False) -> str:
    normalized = (value or "auto").strip().lower().replace("_", "-")
    canonical = _CANONICAL_ALIASES.get(normalized, normalized)
    names = {
        "auto": ("automatically detected language", "自动检测"),
        "ja": ("Japanese", "日语"),
        "zh-hans": ("Simplified Chinese", "简体中文"),
        "zh-hant": ("Traditional Chinese", "繁体中文"),
        "en": ("English", "英语"),
        "ko": ("Korean", "韩语"),
    }
    pair = names.get(canonical)
    if pair is None:
        return value or ("自动检测" if chinese else "automatically detected language")
    return pair[1] if chinese else pair[0]


def append_log(message: str) -> None:
    safe = message.replace("\r", "\\r").replace("\n", "\\n")
    with LOG_LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + safe + "\n")


def retry_log_fields(error: BaseException) -> str | None:
    """Return safe log fields only when an upstream failure is retryable."""

    if isinstance(error, urllib.error.HTTPError):
        status = error.code
        if isinstance(status, int) and (
            status == HTTPStatus.TOO_MANY_REQUESTS or 500 <= status <= 599
        ):
            return f"type=HTTPError upstream_status={status}"
        # In particular, never retry 400, 401, or 403 responses.
        return None
    if isinstance(error, http.client.RemoteDisconnected):
        return "type=RemoteDisconnected"
    if isinstance(error, ConnectionResetError):
        return "type=ConnectionResetError"
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, socket.gaierror):
            # EAI_AGAIN is explicitly temporary. Other resolver failures are
            # treated as permanent so a bad host name is not queried twice.
            if reason.errno != getattr(socket, "EAI_AGAIN", None):
                return None
        if isinstance(reason, (ssl.CertificateError, ssl.SSLCertVerificationError)):
            return None
        return "type=URLError"
    return None


class Translator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.mode = args.mode
        self.base_url = args.base_url.rstrip("/")
        self.api_key = args.api_key
        self.model = args.model
        self.timeout = args.timeout
        self.payload_profile = getattr(args, "payload_profile", "openai")
        self.thinking = getattr(args, "thinking", "omit")
        self.reasoning_effort = getattr(args, "reasoning_effort", "none")
        self.prompt_mode = getattr(args, "prompt_mode", "template1")
        self.custom_prompt = getattr(args, "custom_prompt", "")
        self.credentials = dict(getattr(args, "credentials", {}) or {})
        requested_concurrency = getattr(
            args, "upstream_concurrency", DEFAULT_UPSTREAM_CONCURRENCY
        )
        # Baidu's standard tier is documented at one request per second. Keep
        # the first release conservative even if an old settings file asks for
        # more workers; higher-tier tuning can be exposed separately later.
        self.upstream_concurrency = (
            1 if self.mode == "baidu" else requested_concurrency
        )
        self.upstream_slots = threading.BoundedSemaphore(self.upstream_concurrency)
        self.cache_entries = getattr(args, "cache_entries", DEFAULT_CACHE_ENTRIES)
        self.cache_bytes_limit = getattr(args, "cache_bytes", DEFAULT_CACHE_BYTES)
        self.cache_enabled = self.cache_entries > 0 and self.cache_bytes_limit > 0
        self._cache_lock = threading.Lock()
        self._translation_cache: OrderedDict[bytes, tuple[str, int]] = OrderedDict()
        self._translation_cache_bytes = 0
        self._baidu_rate_lock = threading.Lock()
        self._baidu_next_request_start = 0.0
        self._translation_inflight: dict[
            bytes, concurrent.futures.Future[str]
        ] = {}

    def _translation_cache_key(
        self, text: str, source: str, target: str, single_line: bool
    ) -> bytes:
        """Hash an exact, length-framed translation unit without retaining source."""

        digest = hashlib.sha256()
        fields = (
            "translation-unit-v1",
            self.mode,
            self.base_url,
            self.model,
            self.payload_profile,
            self.thinking,
            self.reasoning_effort,
            self.prompt_mode,
            self.custom_prompt,
            source,
            target,
            "single-line" if single_line else "whole-request",
            text,
        )
        for field in fields:
            encoded = field.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.digest()

    def _credential(self, name: str) -> str:
        value = self.credentials.get(name, "")
        if not isinstance(value, str) or not value.strip():
            raise ProviderError(f"missing credential field: {name}")
        return value.strip()

    def _request_json_once(self, request: urllib.request.Request) -> object:
        if not self.upstream_slots.acquire(timeout=self.timeout):
            raise TimeoutError("upstream concurrency queue timed out")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        finally:
            self.upstream_slots.release()

    def _wait_baidu_rate_limit(self) -> None:
        """Reserve a standard-tier Baidu request start at no more than 1 QPS."""

        with self._baidu_rate_lock:
            now = time.monotonic()
            reserved_start = max(now, self._baidu_next_request_start)
            delay = reserved_start - now
            if delay >= self.timeout:
                raise TimeoutError("baidu rate-limit queue timed out")
            self._baidu_next_request_start = (
                reserved_start + BAIDU_MIN_REQUEST_INTERVAL_SECONDS
            )
        if delay > 0:
            time.sleep(delay)

    def _translate_youdao(self, text: str, source: str, target: str) -> str:
        if len(text) > YOUDAO_MAX_TEXT_CHARS:
            raise ProviderError(
                f"youdao text exceeds {YOUDAO_MAX_TEXT_CHARS} characters"
            )
        app_key = self._credential("app_key")
        app_secret = self._credential("app_secret")
        salt = uuid.uuid4().hex
        current_time = str(int(time.time()))
        sign_input = (
            text
            if len(text) <= 20
            else text[:10] + str(len(text)) + text[-10:]
        )
        sign = hashlib.sha256(
            (app_key + sign_input + salt + current_time + app_secret).encode(
                "utf-8"
            )
        ).hexdigest()
        form = urllib.parse.urlencode(
            {
                "q": text,
                "from": provider_language("youdao", source),
                "to": provider_language("youdao", target),
                "appKey": app_key,
                "salt": salt,
                "sign": sign,
                "signType": "v3",
                "curtime": current_time,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            YOUDAO_ENDPOINT,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        body = self._request_json_once(request)
        if not isinstance(body, dict) or str(body.get("errorCode", "")) != "0":
            code = body.get("errorCode", "unknown") if isinstance(body, dict) else "invalid"
            raise ProviderError(f"youdao provider error: {code}")
        translations = body.get("translation")
        if (
            not isinstance(translations, list)
            or not translations
            or not isinstance(translations[0], str)
        ):
            raise ProviderError("youdao response lacks a translation")
        return translations[0]

    def _translate_baidu(self, text: str, source: str, target: str) -> str:
        if len(text) > BAIDU_MAX_TEXT_CHARS:
            raise ProviderError(
                f"baidu text exceeds {BAIDU_MAX_TEXT_CHARS} characters"
            )
        self._wait_baidu_rate_limit()
        app_id = self._credential("app_id")
        app_secret = self._credential("app_secret")
        salt = uuid.uuid4().hex
        sign = hashlib.md5(
            (app_id + text + salt + app_secret).encode("utf-8")
        ).hexdigest()
        form = urllib.parse.urlencode(
            {
                "q": text,
                "from": provider_language("baidu", source),
                "to": provider_language("baidu", target),
                "appid": app_id,
                "salt": salt,
                "sign": sign,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            BAIDU_ENDPOINT,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        body = self._request_json_once(request)
        if not isinstance(body, dict):
            raise ProviderError("baidu returned an invalid response")
        error_code = body.get("error_code")
        if error_code is not None and str(error_code) != "52000":
            raise ProviderError(f"baidu provider error: {error_code}")
        results = body.get("trans_result")
        if not isinstance(results, list) or not results:
            raise ProviderError("baidu response lacks trans_result")
        translated: list[str] = []
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("dst"), str):
                raise ProviderError("baidu returned an invalid trans_result item")
            translated.append(item["dst"])
        return "\n".join(translated)

    def _translate_microsoft(self, text: str, source: str, target: str) -> str:
        if len(text) > MICROSOFT_MAX_TEXT_CHARS:
            raise ProviderError(
                f"microsoft text exceeds {MICROSOFT_MAX_TEXT_CHARS} characters"
            )
        subscription_key = self._credential("subscription_key")
        source_code = provider_language("microsoft", source)
        target_code = provider_language("microsoft", target)
        query: list[tuple[str, str]] = [("api-version", "3.0"), ("to", target_code)]
        if source_code != "auto":
            query.append(("from", source_code))
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Ocp-Apim-Subscription-Key": subscription_key,
            "X-ClientTraceId": str(uuid.uuid4()),
        }
        region = str(self.credentials.get("region", "")).strip()
        if region:
            headers["Ocp-Apim-Subscription-Region"] = region
        request = urllib.request.Request(
            MICROSOFT_ENDPOINT + "?" + urllib.parse.urlencode(query),
            data=json.dumps([{"Text": text}], ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        body = self._request_json_once(request)
        try:
            translated = body[0]["translations"][0]["text"]
        except (IndexError, KeyError, TypeError) as error:
            raise ProviderError("microsoft response lacks a translation") from error
        if not isinstance(translated, str):
            raise ProviderError("microsoft translation is not text")
        return translated

    def _custom_prompt_text(self, text: str, source: str, target: str) -> str:
        template = self.custom_prompt
        rendered = (
            template.replace("{source}", source or "auto")
            .replace("{target}", target or "zh")
            .replace("{text}", text)
        )
        if "{text}" not in template:
            rendered += "\n\n" + text
        return rendered

    def _prompt_messages(
        self, text: str, source: str, target: str
    ) -> list[dict[str, str]]:
        """Build chat messages without exposing prompt text to logs."""

        if self.prompt_mode == "custom":
            return [
                {
                    "role": "user",
                    "content": self._custom_prompt_text(
                        text, source or "auto", target or "zh"
                    ),
                }
            ]

        if self.prompt_mode == "template2":
            prompt = (
                f"请将下面的游戏文本从{prompt_language_name(source, chinese=True)}"
                f"翻译成{prompt_language_name(target, chinese=True)}。"
                "译文应自然、符合角色语气和游戏场景；保持人名、专有名词、"
                "占位符、富文本标记与换行结构。只输出最终译文，不要解释。\n\n"
                + text
            )
            return [
                {
                    "role": "system",
                    "content": "你是一名专业的游戏本地化译者和中文编辑。",
                },
                {"role": "user", "content": prompt},
            ]

        prompt = (
            f"Translate the following text from {prompt_language_name(source)} "
            f"to {prompt_language_name(target)}. "
            "Return only the translation, with no explanation. Preserve names, "
            "line breaks, placeholders, and markup.\n\n" + text
        )
        return [
            {
                "role": "system",
                "content": "You are a precise game localization translator.",
            },
            {"role": "user", "content": prompt},
        ]

    def _translate_unit_cached(
        self,
        text: str,
        source: str,
        target: str,
        *,
        single_line: bool,
        compute: Callable[[], str],
    ) -> str:
        """Return one translated unit with bounded LRU and per-key single-flight."""

        if not self.cache_enabled:
            return compute()

        key = self._translation_cache_key(text, source, target, single_line)
        leader = False
        with self._cache_lock:
            cached = self._translation_cache.pop(key, None)
            if cached is not None:
                self._translation_cache[key] = cached
                return cached[0]
            future = self._translation_inflight.get(key)
            if future is None:
                future = concurrent.futures.Future()
                self._translation_inflight[key] = future
                leader = True

        if not leader:
            # Followers never hold the cache lock or an upstream semaphore slot.
            # Timing out one follower must not cancel work shared by other callers.
            try:
                return future.result(timeout=self.timeout + 1.0)
            except concurrent.futures.TimeoutError as error:
                raise TimeoutError("shared translation wait timed out") from error

        try:
            translated = compute()
            translated_bytes = len(translated.encode("utf-8"))
            if translated_bytes <= self.cache_bytes_limit:
                with self._cache_lock:
                    self._translation_cache[key] = (translated, translated_bytes)
                    self._translation_cache.move_to_end(key)
                    self._translation_cache_bytes += translated_bytes
                    while (
                        len(self._translation_cache) > self.cache_entries
                        or self._translation_cache_bytes > self.cache_bytes_limit
                    ):
                        _old_key, (_old_value, old_size) = (
                            self._translation_cache.popitem(last=False)
                        )
                        self._translation_cache_bytes -= old_size
        except BaseException as error:
            # Publish the failure before removing the entry so current followers
            # observe the same failure. Failures are never inserted into the LRU.
            future.set_exception(error)
            with self._cache_lock:
                if self._translation_inflight.get(key) is future:
                    del self._translation_inflight[key]
            raise

        future.set_result(translated)
        with self._cache_lock:
            if self._translation_inflight.get(key) is future:
                del self._translation_inflight[key]
        return translated

    def build_payload_object(
        self, text: str, source: str, target: str
    ) -> dict[str, object]:
        """Build a provider-specific chat-completions payload."""

        if self.prompt_mode != "template1":
            messages = self._prompt_messages(text, source, target)
            if self.payload_profile == "hunyuan-mt":
                # Dedicated chat-based MT models are most reliable with one
                # user message. Fold the optional system instruction into it.
                combined = "\n".join(
                    item["content"] for item in messages if item["content"]
                )
                messages = [{"role": "user", "content": combined}]
        elif self.payload_profile == "hunyuan-mt":
            target_name = prompt_language_name(target, chinese=True)
            prompt = (
                f"把下面的文本翻译成{target_name}，不要额外解释。"
                "保留原文中的占位符和标记。\n\n" + text
            )
            messages = [{"role": "user", "content": prompt}]
        else:
            messages = self._prompt_messages(text, source, target)

        payload_object: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if self.payload_profile in {"openai", "deepseek"}:
            # Keep the generic OpenAI profile backward compatible: callers that
            # previously selected --thinking still receive DeepSeek's nested
            # representation. New launchers select their profile explicitly.
            if self.thinking != "omit":
                payload_object["thinking"] = {"type": self.thinking}
            if self.reasoning_effort != "none":
                payload_object["reasoning_effort"] = self.reasoning_effort
            if self.thinking != "enabled":
                payload_object["temperature"] = 0.2
        elif self.payload_profile == "siliconflow-qwen":
            # SiliconFlow's Qwen endpoint uses a top-level boolean rather than
            # DeepSeek's nested thinking object. This speed-oriented profile is
            # deliberately non-thinking.
            payload_object["enable_thinking"] = False
            payload_object["temperature"] = 0.2
        elif self.payload_profile != "hunyuan-mt":
            raise RuntimeError(
                f"unsupported payload profile: {self.payload_profile}"
            )
        # Hunyuan-MT receives only model/messages/stream: it is a dedicated MT
        # model and must not be sent provider-specific thinking parameters.
        return payload_object

    def _request_upstream(self, payload_object: dict[str, object]) -> str:
        """Perform one independently bounded and retryable upstream call."""

        payload = json.dumps(payload_object, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.base_url + "/chat/completions", payload, headers, method="POST"
        )
        deadline = time.monotonic() + self.timeout
        if not self.upstream_slots.acquire(timeout=self.timeout):
            raise TimeoutError("upstream concurrency queue timed out")
        try:
            for attempt in range(1, MAX_UPSTREAM_ATTEMPTS + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("upstream request deadline expired")
                try:
                    with urllib.request.urlopen(request, timeout=remaining) as response:
                        body = json.loads(response.read().decode("utf-8"))
                    break
                except (
                    urllib.error.HTTPError,
                    http.client.RemoteDisconnected,
                    ConnectionResetError,
                    urllib.error.URLError,
                ) as error:
                    retry_fields = retry_log_fields(error)
                    if isinstance(error, urllib.error.HTTPError):
                        # Closing is enough; never read an upstream error body.
                        error.close()
                    if retry_fields is None or attempt >= MAX_UPSTREAM_ATTEMPTS:
                        raise

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    backoff = min(RETRY_BACKOFF_SECONDS, remaining / 2)
                    append_log(
                        f"RETRY attempt={attempt} next_attempt={attempt + 1} "
                        f"{retry_fields} backoff_ms={backoff * 1000:.0f}"
                    )
                    time.sleep(backoff)
        finally:
            self.upstream_slots.release()
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("upstream response content is not a string")
        # Leading/trailing whitespace can be meaningful in game text.
        return content

    def _translate_hunyuan_segment(
        self, text: str, source: str, target: str
    ) -> str:
        if not text or "\r" in text or "\n" in text:
            raise RuntimeError("Hunyuan segment must be nonempty and single-line")

        def compute() -> str:
            translated = self._request_upstream(
                self.build_payload_object(text, source, target)
            )
            if "\r" in translated or "\n" in translated:
                raise ValueError(
                    "Hunyuan returned a line break for a single-line segment"
                )
            return translated

        return self._translate_unit_cached(
            text,
            source,
            target,
            single_line=True,
            compute=compute,
        )

    def _translate_hunyuan(self, text: str, source: str, target: str) -> str:
        # Capturing split retains every CRLF/LF/CR verbatim at odd indices.
        # Empty even-indexed segments represent leading, trailing, or adjacent
        # line endings and intentionally make no upstream call.
        parts = LINE_BREAK_SPLIT_PATTERN.split(text)
        indices = (index for index in range(0, len(parts), 2) if parts[index])
        first = next(indices, None)
        if first is None:
            return text
        second = next(indices, None)
        if second is None:
            parts[first] = self._translate_hunyuan_segment(
                parts[first], source, target
            )
            return "".join(parts)

        all_indices = itertools.chain((first, second), indices)
        if self.upstream_concurrency == 1:
            for index in all_indices:
                parts[index] = self._translate_hunyuan_segment(
                    parts[index], source, target
                )
            return "".join(parts)

        # Submit at most one future per available per-request worker. New work
        # is admitted only after a prior future completes, so a text containing
        # hundreds of thousands of line breaks cannot create that many futures.
        pending: dict[concurrent.futures.Future[str], int] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.upstream_concurrency,
            thread_name_prefix="hunyuan-segment",
        ) as executor:
            for _slot in range(self.upstream_concurrency):
                index = next(all_indices, None)
                if index is None:
                    break
                future = executor.submit(
                    self._translate_hunyuan_segment,
                    parts[index],
                    source,
                    target,
                )
                pending[future] = index

            try:
                while pending:
                    completed, _not_done = concurrent.futures.wait(
                        tuple(pending),
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    finished: list[tuple[int, str]] = []
                    for future in completed:
                        index = pending.pop(future)
                        # Resolve the whole completed batch before submitting
                        # replacements. If one segment failed, no new upstream
                        # work is admitted after that failure became observable.
                        finished.append((index, future.result()))
                    for index, translated in finished:
                        parts[index] = translated
                        next_index = next(all_indices, None)
                        if next_index is not None:
                            next_future = executor.submit(
                                self._translate_hunyuan_segment,
                                parts[next_index],
                                source,
                                target,
                            )
                            pending[next_future] = next_index
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
        return "".join(parts)

    def translate(self, text: str, source: str, target: str) -> str:
        if self.mode == "echo":
            return text
        if self.mode == "openai":
            if self.payload_profile == "hunyuan-mt":
                return self._translate_hunyuan(text, source, target)

            return self._translate_unit_cached(
                text,
                source,
                target,
                single_line=False,
                compute=lambda: self._request_upstream(
                    self.build_payload_object(text, source, target)
                ),
            )

        dedicated_translators: dict[str, Callable[[str, str, str], str]] = {
            "youdao": self._translate_youdao,
            "baidu": self._translate_baidu,
            "microsoft": self._translate_microsoft,
        }
        translate_unit = dedicated_translators.get(self.mode)
        if translate_unit is None:
            raise RuntimeError(f"unsupported mode: {self.mode}")

        return self._translate_unit_cached(
            text,
            source,
            target,
            single_line=False,
            compute=lambda: translate_unit(text, source, target),
        )


def make_handler(translator: Translator, log_content: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RenpyRouteBridge/1.0"
        protocol_version = "HTTP/1.1"

        def send_text(self, status: int, value: str) -> None:
            body = value.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/health":
                self.send_text(HTTPStatus.OK, "ok")
                return
            if parsed.path != "/translate":
                self.send_text(HTTPStatus.NOT_FOUND, "not found")
                return

            try:
                query = urllib.parse.parse_qs(
                    parsed.query, keep_blank_values=True, max_num_fields=16
                )
            except ValueError:
                self.send_text(HTTPStatus.BAD_REQUEST, "invalid query")
                return
            text = query.get("text", [""])[0]
            source = query.get("from", ["auto"])[0]
            target = query.get("to", ["zh"])[0]
            if not text:
                self.send_text(HTTPStatus.BAD_REQUEST, "missing text")
                return
            if len(source) > MAX_LANGUAGE_CHARS or len(target) > MAX_LANGUAGE_CHARS:
                self.send_text(HTTPStatus.BAD_REQUEST, "language field too long")
                return
            if len(text) > MAX_TEXT_CHARS:
                self.send_text(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "text too long")
                return

            started = time.perf_counter()
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            try:
                translated = translator.translate(text, source, target)
                elapsed = (time.perf_counter() - started) * 1000
                event = (
                    f"OK mode={translator.mode} from={source} to={target} "
                    f"chars={len(text)} result_chars={len(translated)} "
                    f"ms={elapsed:.1f} text_sha256={text_hash}"
                )
                if log_content:
                    event += f" text={text} result={translated}"
                append_log(event)
                self.send_text(HTTPStatus.OK, translated)
            except urllib.error.HTTPError as error:
                elapsed = (time.perf_counter() - started) * 1000
                status = error.code if isinstance(error.code, int) else "unknown"
                error.close()
                append_log(
                    f"ERROR mode={translator.mode} chars={len(text)} ms={elapsed:.1f} "
                    f"type=HTTPError upstream_status={status} "
                    f"text_sha256={text_hash}"
                )
                # Never forward or log an upstream error body: providers can
                # include request details in it. The numeric status is enough to
                # distinguish authentication, rate-limit, and server failures.
                self.send_text(HTTPStatus.BAD_GATEWAY, "translation failed")
            except (
                IndexError,
                KeyError,
                TypeError,
                ValueError,
                RuntimeError,
                TimeoutError,
                OSError,
                urllib.error.URLError,
            ) as error:
                elapsed = (time.perf_counter() - started) * 1000
                append_log(
                    f"ERROR mode={translator.mode} chars={len(text)} ms={elapsed:.1f} "
                    f"type={type(error).__name__} text_sha256={text_hash}"
                )
                self.send_text(HTTPStatus.BAD_GATEWAY, "translation failed")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with a hard cap on live request threads."""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = 64

    def __init__(self, server_address, handler, max_concurrency: int):
        self._request_slots = threading.BoundedSemaphore(max_concurrency)
        super().__init__(server_address, handler)

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        super().server_bind()

    def process_request(self, request: socket.socket, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            # Do not spawn an unbounded rejection thread. Closing here may look
            # like a reset to the caller; ipcroute converts it to its own 502.
            self.shutdown_request(request)
            append_log("BUSY action=closed")
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=19899, type=int)
    parser.add_argument(
        "--mode",
        choices=("echo", "openai", *DEDICATED_MODES),
        default=os.getenv("BRIDGE_MODE", "echo"),
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:11434/v1"),
        help="OpenAI-compatible base URL, without /chat/completions",
    )
    parser.add_argument("--model", default=os.getenv("UPSTREAM_MODEL", ""))
    parser.add_argument(
        "--payload-profile",
        choices=PAYLOAD_PROFILES,
        default=os.getenv("UPSTREAM_PAYLOAD_PROFILE", "openai"),
        help="provider-specific request payload and prompt format",
    )
    parser.add_argument(
        "--api-key-file",
        default=os.getenv("UPSTREAM_API_KEY_FILE", ""),
        help="path to a one-line API key file; the path is not logged",
    )
    parser.add_argument(
        "--thinking",
        choices=("omit", "enabled", "disabled"),
        default=os.getenv("UPSTREAM_THINKING", "omit"),
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default=os.getenv("UPSTREAM_REASONING_EFFORT", "none"),
    )
    parser.add_argument(
        "--prompt-mode",
        choices=PROMPT_MODES,
        default=os.getenv("UPSTREAM_PROMPT_MODE", "template1"),
        help="AI prompt preset; dedicated HTTP MT providers ignore it",
    )
    parser.add_argument(
        "--custom-prompt",
        default=os.getenv("UPSTREAM_CUSTOM_PROMPT", ""),
        help="custom AI prompt; inherited through the environment by default",
    )
    parser.add_argument("--timeout", default=120.0, type=float)
    parser.add_argument(
        "--log-path",
        default=os.getenv("BRIDGE_LOG_PATH", str(LOG_PATH)),
        help="metadata-only request log path",
    )
    parser.add_argument(
        "--max-concurrency", default=DEFAULT_MAX_CONCURRENCY, type=int
    )
    parser.add_argument(
        "--upstream-concurrency",
        default=DEFAULT_UPSTREAM_CONCURRENCY,
        type=int,
    )
    parser.add_argument(
        "--cache-entries",
        default=DEFAULT_CACHE_ENTRIES,
        type=int,
        help="maximum in-process translation cache entries; zero disables caching",
    )
    parser.add_argument(
        "--cache-bytes",
        default=DEFAULT_CACHE_BYTES,
        type=int,
        help="maximum UTF-8 result bytes retained in memory; zero disables caching",
    )
    content_log_group = parser.add_mutually_exclusive_group()
    content_log_group.add_argument(
        "--log-content",
        dest="log_content",
        action="store_true",
        help="opt in to logging plaintext source and translated text",
    )
    content_log_group.add_argument(
        "--no-log-content",
        dest="log_content",
        action="store_false",
        help="force hash-only logs even if BRIDGE_LOG_CONTENT is set",
    )
    parser.set_defaults(
        log_content=os.getenv("BRIDGE_LOG_CONTENT", "").lower()
        in {"1", "true", "yes"}
    )
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("--host must be exactly 127.0.0.1")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not 1 <= args.max_concurrency <= 128:
        parser.error("--max-concurrency must be between 1 and 128")
    if not 1 <= args.upstream_concurrency <= args.max_concurrency:
        parser.error(
            "--upstream-concurrency must be between 1 and --max-concurrency"
        )
    if not 0 <= args.cache_entries <= MAX_CACHE_ENTRIES:
        parser.error(
            f"--cache-entries must be between 0 and {MAX_CACHE_ENTRIES}"
        )
    if not 0 <= args.cache_bytes <= MAX_CACHE_BYTES:
        parser.error(f"--cache-bytes must be between 0 and {MAX_CACHE_BYTES}")
    if not 0 < args.timeout <= 600:
        parser.error("--timeout must be greater than 0 and at most 600 seconds")
    if len(args.custom_prompt) > MAX_CUSTOM_PROMPT_CHARS or "\0" in args.custom_prompt:
        parser.error(
            f"--custom-prompt must be at most {MAX_CUSTOM_PROMPT_CHARS} characters "
            "and contain no NUL"
        )
    if args.prompt_mode == "custom" and not args.custom_prompt.strip():
        parser.error("--prompt-mode custom requires a non-empty --custom-prompt")

    credentials_json = os.environ.pop("UPSTREAM_CREDENTIALS_JSON", "")
    if len(credentials_json) > 16_384 or "\0" in credentials_json:
        parser.error("UPSTREAM_CREDENTIALS_JSON is too large or contains NUL")
    args.credentials = {}
    if credentials_json:
        try:
            credentials = json.loads(credentials_json)
        except json.JSONDecodeError as error:
            parser.error(f"UPSTREAM_CREDENTIALS_JSON is invalid JSON: {error.msg}")
        if not isinstance(credentials, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in credentials.items()
        ):
            parser.error("UPSTREAM_CREDENTIALS_JSON must be a string-to-string object")
        args.credentials = credentials

    args.api_key = os.environ.pop("UPSTREAM_API_KEY", "")
    os.environ.pop("UPSTREAM_PROMPT_MODE", None)
    os.environ.pop("UPSTREAM_CUSTOM_PROMPT", None)
    if args.api_key_file:
        try:
            key_path = Path(args.api_key_file).resolve(strict=True)
            if not key_path.is_file() or not 1 <= key_path.stat().st_size <= 4096:
                raise ValueError("invalid API key file size or type")
            key_lines = key_path.read_text(encoding="utf-8-sig").splitlines()
            if len(key_lines) != 1:
                raise ValueError("API key file must contain exactly one line")
            key_value = key_lines[0].strip()
            if (
                not key_value.startswith("sk-")
                or len(key_value) < 16
                or any(character.isspace() for character in key_value)
            ):
                raise ValueError("API key file has an invalid format")
            args.api_key = key_value
        except (OSError, UnicodeError, ValueError) as error:
            parser.error(f"cannot use --api-key-file: {error}")
    if args.mode == "openai" and not args.model:
        parser.error("--model or UPSTREAM_MODEL is required in openai mode")
    if args.mode == "openai":
        upstream = urllib.parse.urlsplit(args.base_url)
        if (
            upstream.scheme not in {"http", "https"}
            or not upstream.netloc
            or upstream.username is not None
            or upstream.password is not None
            or upstream.query
            or upstream.fragment
        ):
            parser.error(
                "--base-url must be an absolute HTTP(S) URL without credentials, "
                "a query, or a fragment"
            )
        if args.payload_profile == "siliconflow-qwen":
            if args.thinking not in {"omit", "disabled"}:
                parser.error(
                    "siliconflow-qwen is a non-thinking profile; use "
                    "--thinking disabled or omit"
                )
            if args.reasoning_effort != "none":
                parser.error(
                    "siliconflow-qwen does not accept --reasoning-effort"
                )
        if args.payload_profile == "hunyuan-mt":
            if args.thinking != "omit" or args.reasoning_effort != "none":
                parser.error(
                    "hunyuan-mt requires --thinking omit and "
                    "--reasoning-effort none"
                )
    required_credentials = {
        "youdao": ("app_key", "app_secret"),
        "baidu": ("app_id", "app_secret"),
        "microsoft": ("subscription_key",),
    }
    for field in required_credentials.get(args.mode, ()):
        value = args.credentials.get(field, "")
        if not isinstance(value, str) or not value.strip():
            parser.error(
                f"UPSTREAM_CREDENTIALS_JSON must contain non-empty {field!r} "
                f"for {args.mode} mode"
            )
    return args


def main() -> None:
    global LOG_PATH
    args = parse_args()
    LOG_PATH = Path(args.log_path).expanduser().resolve()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    translator = Translator(args)
    server = BoundedThreadingHTTPServer(
        (args.host, args.port),
        make_handler(translator, args.log_content),
        args.max_concurrency,
    )
    append_log(
        f"START host={args.host} port={args.port} mode={args.mode} "
        f"base_url={args.base_url} model={args.model or '-'} "
        f"payload_profile={args.payload_profile} "
        f"prompt_mode={args.prompt_mode} "
        f"thinking={args.thinking} reasoning_effort={args.reasoning_effort} "
        f"max_concurrency={args.max_concurrency} "
        f"upstream_concurrency={args.upstream_concurrency} "
        f"cache_entries={args.cache_entries} cache_bytes={args.cache_bytes} "
        f"log_content={int(args.log_content)}"
    )
    print(f"bridge listening on http://{args.host}:{args.port}/translate ({args.mode})")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        append_log("STOP")


if __name__ == "__main__":
    main()
