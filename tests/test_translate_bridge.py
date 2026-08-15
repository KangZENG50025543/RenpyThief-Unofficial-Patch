"""Offline tests for Hunyuan local line segmentation and provider payloads."""

from __future__ import annotations

import concurrent.futures
import http.client
import importlib.util
import json
import os
import sys
import threading
import time
import unittest
from argparse import Namespace
from collections import Counter
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "router" / "translate_bridge.py"
SPEC = importlib.util.spec_from_file_location("translate_bridge_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load translate_bridge.py")
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


def make_translator(
    profile: str = "hunyuan-mt",
    upstream_concurrency: int = 4,
    cache_entries: int = BRIDGE.DEFAULT_CACHE_ENTRIES,
    cache_bytes: int = BRIDGE.DEFAULT_CACHE_BYTES,
    prompt_mode: str = "template1",
    custom_prompt: str = "",
):
    thinking = "disabled" if profile == "siliconflow-qwen" else "omit"
    return BRIDGE.Translator(
        Namespace(
            mode="openai",
            base_url="https://offline.invalid/v1",
            api_key="",
            model="offline-model",
            timeout=2.0,
            payload_profile=profile,
            thinking=thinking,
            reasoning_effort="none",
            prompt_mode=prompt_mode,
            custom_prompt=custom_prompt,
            upstream_concurrency=upstream_concurrency,
            cache_entries=cache_entries,
            cache_bytes=cache_bytes,
        )
    )


def make_dedicated_translator(mode: str, credentials: dict[str, str]):
    return BRIDGE.Translator(
        Namespace(
            mode=mode,
            base_url="",
            api_key="",
            model="",
            timeout=2.0,
            payload_profile="openai",
            thinking="omit",
            reasoning_effort="none",
            prompt_mode="template1",
            custom_prompt="",
            credentials=credentials,
            upstream_concurrency=8,
            cache_entries=0,
            cache_bytes=0,
        )
    )


def request_text(request) -> str:
    payload = json.loads(request.data.decode("utf-8"))
    return payload["messages"][-1]["content"].rsplit("\n\n", 1)[1]


class FakeResponse:
    def __init__(self, content: str) -> None:
        self._body = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class JsonResponse:
    def __init__(self, value: object) -> None:
        self._body = json.dumps(value, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class FixedUuid:
    hex = "0123456789abcdef0123456789abcdef"

    def __str__(self) -> str:
        return "01234567-89ab-cdef-0123-456789abcdef"


class DedicatedProviderTests(unittest.TestCase):
    def test_parse_args_requires_and_accepts_dedicated_credentials(self) -> None:
        argv = ["translate_bridge.py", "--mode", "youdao"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, {"UPSTREAM_CREDENTIALS_JSON": ""}),
            self.assertRaises(SystemExit),
        ):
            BRIDGE.parse_args()

        serialized = json.dumps(
            {"app_key": "test-app", "app_secret": "test-secret"}
        )
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(
                os.environ, {"UPSTREAM_CREDENTIALS_JSON": serialized}
            ),
        ):
            args = BRIDGE.parse_args()
        self.assertEqual(args.mode, "youdao")
        self.assertEqual(
            args.credentials,
            {"app_key": "test-app", "app_secret": "test-secret"},
        )
        self.assertNotIn("UPSTREAM_CREDENTIALS_JSON", os.environ)

    def test_parse_args_removes_ai_secrets_and_prompt_from_environment(self) -> None:
        argv = [
            "translate_bridge.py",
            "--mode",
            "openai",
            "--model",
            "offline-model",
        ]
        values = {
            "UPSTREAM_API_KEY": "test-api-key",
            "UPSTREAM_PROMPT_MODE": "custom",
            "UPSTREAM_CUSTOM_PROMPT": "翻译：{text}",
        }
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, values),
        ):
            args = BRIDGE.parse_args()
            self.assertEqual(args.api_key, "test-api-key")
            self.assertEqual(args.prompt_mode, "custom")
            self.assertEqual(args.custom_prompt, "翻译：{text}")
            for name in values:
                self.assertNotIn(name, os.environ)

    def test_baidu_reserves_request_starts_at_standard_tier_qps(self) -> None:
        translator = make_dedicated_translator(
            "baidu", {"app_id": "test-id", "app_secret": "test-secret"}
        )
        translator.timeout = 5.0
        with (
            mock.patch.object(BRIDGE.time, "monotonic", return_value=100.0),
            mock.patch.object(BRIDGE.time, "sleep") as sleep,
        ):
            translator._wait_baidu_rate_limit()
            translator._wait_baidu_rate_limit()
            translator._wait_baidu_rate_limit()
        self.assertEqual(sleep.call_count, 2)
        self.assertAlmostEqual(
            sleep.call_args_list[0].args[0],
            BRIDGE.BAIDU_MIN_REQUEST_INTERVAL_SECONDS,
        )
        self.assertAlmostEqual(
            sleep.call_args_list[1].args[0],
            BRIDGE.BAIDU_MIN_REQUEST_INTERVAL_SECONDS * 2,
        )

    def test_dedicated_provider_length_limits_fail_before_network(self) -> None:
        cases = (
            (
                "youdao",
                {"app_key": "test-app", "app_secret": "test-secret"},
                BRIDGE.YOUDAO_MAX_TEXT_CHARS,
            ),
            (
                "baidu",
                {"app_id": "test-id", "app_secret": "test-secret"},
                BRIDGE.BAIDU_MAX_TEXT_CHARS,
            ),
            (
                "microsoft",
                {"subscription_key": "test-key"},
                BRIDGE.MICROSOFT_MAX_TEXT_CHARS,
            ),
        )
        with mock.patch.object(BRIDGE.urllib.request, "urlopen") as urlopen:
            for mode, credentials, limit in cases:
                with self.subTest(mode=mode):
                    translator = make_dedicated_translator(mode, credentials)
                    with self.assertRaisesRegex(BRIDGE.ProviderError, "exceeds"):
                        translator.translate("x" * (limit + 1), "ja", "zh")
        urlopen.assert_not_called()

    def test_language_codes_are_mapped_per_provider(self) -> None:
        self.assertEqual(BRIDGE.provider_language("youdao", "zh"), "zh-CHS")
        self.assertEqual(BRIDGE.provider_language("baidu", "ja"), "jp")
        self.assertEqual(BRIDGE.provider_language("baidu", "ko"), "kor")
        self.assertEqual(
            BRIDGE.provider_language("microsoft", "zh-TW"), "zh-Hant"
        )
        with self.assertRaises(ValueError):
            BRIDGE.provider_language("youdao", "unsupported")

    def test_youdao_v3_signature_and_response(self) -> None:
        translator = make_dedicated_translator(
            "youdao", {"app_key": "test-app", "app_secret": "test-secret"}
        )
        seen: dict[str, str] = {}

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 2.0)
            seen.update(
                {
                    key: values[0]
                    for key, values in BRIDGE.urllib.parse.parse_qs(
                        request.data.decode("utf-8"), keep_blank_values=True
                    ).items()
                }
            )
            return JsonResponse({"errorCode": "0", "translation": ["译文"]})

        source = "1234567890abcdefghijABCDEFGHIJ"
        with (
            mock.patch.object(BRIDGE.uuid, "uuid4", return_value=FixedUuid()),
            mock.patch.object(BRIDGE.time, "time", return_value=1_700_000_000),
            mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen),
        ):
            self.assertEqual(translator.translate(source, "ja", "zh"), "译文")

        shortened = source[:10] + str(len(source)) + source[-10:]
        expected = BRIDGE.hashlib.sha256(
            (
                "test-app"
                + shortened
                + FixedUuid.hex
                + "1700000000"
                + "test-secret"
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(seen["sign"], expected)
        self.assertEqual(seen["from"], "ja")
        self.assertEqual(seen["to"], "zh-CHS")
        self.assertNotIn("test-secret", seen.values())

    def test_baidu_signature_response_and_conservative_concurrency(self) -> None:
        translator = make_dedicated_translator(
            "baidu", {"app_id": "test-id", "app_secret": "test-secret"}
        )
        self.assertEqual(translator.upstream_concurrency, 1)
        seen: dict[str, str] = {}

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 2.0)
            seen.update(
                {
                    key: values[0]
                    for key, values in BRIDGE.urllib.parse.parse_qs(
                        request.data.decode("utf-8"), keep_blank_values=True
                    ).items()
                }
            )
            return JsonResponse(
                {"trans_result": [{"src": "原文", "dst": "译文"}]}
            )

        with (
            mock.patch.object(BRIDGE.uuid, "uuid4", return_value=FixedUuid()),
            mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen),
        ):
            self.assertEqual(translator.translate("原文", "ja", "zh"), "译文")

        expected = BRIDGE.hashlib.md5(
            ("test-id" + "原文" + FixedUuid.hex + "test-secret").encode("utf-8")
        ).hexdigest()
        self.assertEqual(seen["sign"], expected)
        self.assertEqual(seen["from"], "jp")
        self.assertEqual(seen["to"], "zh")
        self.assertNotIn("test-secret", seen.values())

    def test_microsoft_headers_query_body_and_response(self) -> None:
        translator = make_dedicated_translator(
            "microsoft",
            {"subscription_key": "test-key", "region": "eastasia"},
        )
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 2.0)
            captured["url"] = request.full_url
            captured["headers"] = {
                name.lower(): value for name, value in request.header_items()
            }
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return JsonResponse([{"translations": [{"text": "译文", "to": "zh-Hans"}]}])

        with (
            mock.patch.object(BRIDGE.uuid, "uuid4", return_value=FixedUuid()),
            mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen),
        ):
            self.assertEqual(translator.translate("source", "auto", "zh"), "译文")

        query = BRIDGE.urllib.parse.parse_qs(
            BRIDGE.urllib.parse.urlsplit(str(captured["url"])).query
        )
        self.assertEqual(query, {"api-version": ["3.0"], "to": ["zh-Hans"]})
        self.assertEqual(captured["body"], [{"Text": "source"}])
        headers = captured["headers"]
        self.assertEqual(headers["ocp-apim-subscription-key"], "test-key")
        self.assertEqual(headers["ocp-apim-subscription-region"], "eastasia")

    def test_provider_error_does_not_cache_a_failure(self) -> None:
        translator = make_dedicated_translator(
            "youdao", {"app_key": "test-app", "app_secret": "test-secret"}
        )
        with mock.patch.object(
            BRIDGE.urllib.request,
            "urlopen",
            return_value=JsonResponse({"errorCode": "108"}),
        ):
            with self.assertRaisesRegex(BRIDGE.ProviderError, "108"):
                translator.translate("source", "ja", "zh")
        self.assertEqual(translator._translation_cache, {})



class HunyuanSegmentationTests(unittest.TestCase):
    def test_prompt_template_two_is_selectable(self) -> None:
        payload = make_translator(
            "deepseek", prompt_mode="template2"
        ).build_payload_object("原文", "ja", "zh")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("游戏本地化", payload["messages"][0]["content"])
        self.assertTrue(payload["messages"][-1]["content"].endswith("原文"))

    def test_builtin_prompt_uses_language_names_not_protocol_codes(self) -> None:
        payload = make_translator("deepseek").build_payload_object(
            "source", "ja", "zh"
        )
        content = payload["messages"][-1]["content"]
        self.assertIn("Japanese", content)
        self.assertIn("Simplified Chinese", content)

    def test_custom_prompt_replaces_tokens_and_appends_when_needed(self) -> None:
        with_tokens = make_translator(
            "deepseek",
            prompt_mode="custom",
            custom_prompt="从{source}到{target}: {text}",
        ).build_payload_object("原文", "ja", "zh")
        self.assertEqual(
            with_tokens["messages"],
            [{"role": "user", "content": "从ja到zh: 原文"}],
        )

        without_text = make_translator(
            "deepseek",
            prompt_mode="custom",
            custom_prompt="只输出译文",
        ).build_payload_object("原文", "ja", "zh")
        self.assertEqual(
            without_text["messages"][0]["content"], "只输出译文\n\n原文"
        )

    def test_other_provider_payload_fields_are_unchanged(self) -> None:
        deepseek = BRIDGE.Translator(
            Namespace(
                mode="openai",
                base_url="https://offline.invalid/v1",
                api_key="",
                model="offline-model",
                timeout=2.0,
                payload_profile="deepseek",
                thinking="enabled",
                reasoning_effort="high",
                upstream_concurrency=1,
            )
        ).build_payload_object("source", "ja", "zh")
        self.assertEqual(deepseek["thinking"], {"type": "enabled"})
        self.assertEqual(deepseek["reasoning_effort"], "high")
        self.assertNotIn("enable_thinking", deepseek)
        self.assertNotIn("temperature", deepseek)

        qwen = make_translator("siliconflow-qwen").build_payload_object(
            "source", "ja", "zh"
        )
        self.assertIs(qwen["enable_thinking"], False)
        self.assertNotIn("thinking", qwen)
        self.assertNotIn("reasoning_effort", qwen)

    def test_loopback_openai_sends_reasoning_effort_none(self) -> None:
        payload = BRIDGE.Translator(
            Namespace(
                mode="openai",
                base_url="http://127.0.0.1:11434/v1",
                api_key="",
                model="qwen3.5-9b-local",
                timeout=2.0,
                payload_profile="openai",
                thinking="omit",
                reasoning_effort="none",
                prompt_mode="template1",
                custom_prompt="",
                upstream_concurrency=1,
            )
        ).build_payload_object("source", "ja", "zh")
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertNotIn("thinking", payload)

    def test_remote_openai_still_omits_reasoning_effort_none(self) -> None:
        payload = make_translator("openai").build_payload_object(
            "source", "ja", "zh"
        )
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("thinking", payload)

    def test_mixed_line_endings_translate_segments_and_restore_order(self) -> None:
        source = "A\r\nB\nC\rD"
        translations = {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}
        delays = {"A": 0.03, "B": 0.01, "C": 0.02, "D": 0.0}
        calls: list[str] = []
        calls_lock = threading.Lock()

        def fake_urlopen(request, timeout):
            self.assertGreater(timeout, 0)
            segment = request_text(request)
            with calls_lock:
                calls.append(segment)
            time.sleep(delays[segment])
            return FakeResponse(translations[segment])

        with mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen):
            translated = make_translator().translate(source, "ja", "zh")

        self.assertEqual(translated, "甲\r\n乙\n丙\r丁")
        self.assertCountEqual(calls, ("A", "B", "C", "D"))

    def test_empty_segments_make_no_upstream_call(self) -> None:
        translator = make_translator()
        only_line_breaks = "\r\n\n\r"
        with mock.patch.object(BRIDGE.urllib.request, "urlopen") as urlopen:
            self.assertEqual(
                translator.translate(only_line_breaks, "ja", "zh"),
                only_line_breaks,
            )
        urlopen.assert_not_called()

        calls: list[str] = []

        def fake_urlopen(request, _timeout=None, **_kwargs):
            segment = request_text(request)
            calls.append(segment)
            return FakeResponse(segment.lower())

        with mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(
                translator.translate("A\n\n\r\nB", "ja", "zh"),
                "a\n\n\r\nb",
            )
        self.assertCountEqual(calls, ("A", "B"))

    def test_single_line_uses_exactly_one_call_and_no_executor(self) -> None:
        translator = make_translator(upstream_concurrency=8)
        with (
            mock.patch.object(
                BRIDGE.urllib.request,
                "urlopen",
                return_value=FakeResponse("translated"),
            ) as urlopen,
            mock.patch.object(
                BRIDGE.concurrent.futures,
                "ThreadPoolExecutor",
                side_effect=AssertionError("single line must not create an executor"),
            ),
        ):
            self.assertEqual(
                translator.translate("single line", "ja", "zh"), "translated"
            )
        urlopen.assert_called_once()

    def test_single_segment_response_with_line_break_fails(self) -> None:
        translator = make_translator()
        with mock.patch.object(
            BRIDGE.urllib.request,
            "urlopen",
            return_value=FakeResponse("bad\nline"),
        ):
            with self.assertRaises(ValueError):
                translator.translate("source", "ja", "zh")

    def test_concurrency_one_segments_without_outer_slot_deadlock(self) -> None:
        calls: list[str] = []

        def fake_urlopen(request, _timeout=None, **_kwargs):
            segment = request_text(request)
            calls.append(segment)
            return FakeResponse(segment.lower())

        with (
            mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen),
            mock.patch.object(
                BRIDGE.concurrent.futures,
                "ThreadPoolExecutor",
                side_effect=AssertionError(
                    "concurrency one should execute sequentially without an executor"
                ),
            ),
        ):
            translated = make_translator(upstream_concurrency=1).translate(
                "A\nB\r\nC", "ja", "zh"
            )
        self.assertEqual(translated, "a\nb\r\nc")
        self.assertEqual(calls, ["A", "B", "C"])

    def test_non_hunyuan_profile_remains_one_multiline_request(self) -> None:
        source = "A\r\nB\nC"
        seen: list[str] = []

        def fake_urlopen(request, _timeout=None, **_kwargs):
            seen.append(request_text(request))
            return FakeResponse("X\r\nY\nZ")

        with mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen):
            translated = make_translator("siliconflow-qwen").translate(
                source, "ja", "zh"
            )
        self.assertEqual(translated, "X\r\nY\nZ")
        self.assertEqual(seen, [source])

    def test_submission_and_active_calls_are_bounded(self) -> None:
        concurrency = 3
        segment_count = 30
        source = "\n".join(f"S{index}" for index in range(segment_count))
        batch_ready = threading.Event()
        release_batch = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        submit_count = 0
        result: dict[str, object] = {}

        real_submit = BRIDGE.concurrent.futures.ThreadPoolExecutor.submit

        def counted_submit(executor, *args, **kwargs):
            nonlocal submit_count
            with lock:
                submit_count += 1
            return real_submit(executor, *args, **kwargs)

        def fake_urlopen(request, timeout):
            nonlocal active, maximum_active
            self.assertGreater(timeout, 0)
            segment = request_text(request)
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == concurrency:
                    batch_ready.set()
            try:
                if not release_batch.wait(2.0):
                    raise TimeoutError("test did not release the first batch")
                return FakeResponse(segment.lower())
            finally:
                with lock:
                    active -= 1

        def run_translation() -> None:
            try:
                result["value"] = make_translator(
                    upstream_concurrency=concurrency
                ).translate(source, "ja", "zh")
            except BaseException as error:
                result["error"] = error

        with (
            mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen),
            mock.patch.object(
                BRIDGE.concurrent.futures.ThreadPoolExecutor,
                "submit",
                counted_submit,
            ),
        ):
            worker = threading.Thread(target=run_translation)
            worker.start()
            self.assertTrue(batch_ready.wait(2.0))
            with lock:
                # No call has completed yet, so bounded submission must not have
                # admitted more futures than the configured concurrency.
                self.assertEqual(submit_count, concurrency)
                self.assertEqual(maximum_active, concurrency)
            release_batch.set()
            worker.join(5.0)

        self.assertFalse(worker.is_alive())
        if "error" in result:
            raise result["error"]
        self.assertEqual(result["value"], source.lower())
        self.assertEqual(submit_count, segment_count)
        self.assertLessEqual(maximum_active, concurrency)

    def test_each_segment_keeps_transient_retry(self) -> None:
        attempts = 0

        def flaky_urlopen(_request, _timeout=None, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise http.client.RemoteDisconnected("offline transient")
            return FakeResponse("ok")

        with (
            mock.patch.object(BRIDGE.urllib.request, "urlopen", flaky_urlopen),
            mock.patch.object(BRIDGE, "append_log") as append_log,
            mock.patch.object(BRIDGE.time, "sleep"),
        ):
            self.assertEqual(
                make_translator().translate("source", "ja", "zh"), "ok"
            )
        self.assertEqual(attempts, 2)
        append_log.assert_called_once()


class TranslationCacheTests(unittest.TestCase):
    def test_ordinary_request_is_cached_with_digest_only_key(self) -> None:
        translator = make_translator("siliconflow-qwen")
        calls = 0

        def fake_request(_payload):
            nonlocal calls
            calls += 1
            return "translated"

        source = "private source text"
        with mock.patch.object(translator, "_request_upstream", fake_request):
            self.assertEqual(translator.translate(source, "ja", "zh"), "translated")
            self.assertEqual(translator.translate(source, "ja", "zh"), "translated")

        self.assertEqual(calls, 1)
        self.assertEqual(len(translator._translation_cache), 1)
        key = next(iter(translator._translation_cache))
        self.assertIsInstance(key, bytes)
        self.assertEqual(len(key), 32)
        self.assertNotIn(source.encode("utf-8"), key)
        self.assertEqual(translator._translation_inflight, {})

    def test_language_fields_are_part_of_the_cache_key(self) -> None:
        translator = make_translator("siliconflow-qwen")
        calls = 0

        def fake_request(_payload):
            nonlocal calls
            calls += 1
            return f"result-{calls}"

        with mock.patch.object(translator, "_request_upstream", fake_request):
            first = translator.translate("same", "ja", "zh")
            second = translator.translate("same", "en", "zh")
            third = translator.translate("same", "ja", "en")
            self.assertEqual(translator.translate("same", "ja", "zh"), first)

        self.assertEqual((first, second, third), ("result-1", "result-2", "result-3"))
        self.assertEqual(calls, 3)

    def test_concurrent_same_key_uses_one_upstream_call(self) -> None:
        translator = make_translator("siliconflow-qwen", upstream_concurrency=4)
        worker_count = 24
        gate = threading.Barrier(worker_count)
        upstream_started = threading.Event()
        release_upstream = threading.Event()
        calls_lock = threading.Lock()
        calls = 0

        def fake_request(_payload):
            nonlocal calls
            with calls_lock:
                calls += 1
            upstream_started.set()
            if not release_upstream.wait(2.0):
                raise TimeoutError("test did not release upstream")
            return "shared-result"

        def run_one() -> str:
            gate.wait()
            return translator.translate("same", "ja", "zh")

        with (
            mock.patch.object(translator, "_request_upstream", fake_request),
            concurrent.futures.ThreadPoolExecutor(
                max_workers=worker_count
            ) as executor,
        ):
            futures = [executor.submit(run_one) for _ in range(worker_count)]
            self.assertTrue(upstream_started.wait(2.0))
            time.sleep(0.05)
            release_upstream.set()
            results = [future.result(timeout=3.0) for future in futures]

        self.assertEqual(results, ["shared-result"] * worker_count)
        self.assertEqual(calls, 1)
        self.assertEqual(translator._translation_inflight, {})

    def test_failed_singleflight_is_not_cached_and_next_call_retries(self) -> None:
        translator = make_translator("siliconflow-qwen", upstream_concurrency=4)
        worker_count = 12
        gate = threading.Barrier(worker_count)
        upstream_started = threading.Event()
        release_failure = threading.Event()
        calls_lock = threading.Lock()
        calls = 0
        should_fail = True

        def fake_request(_payload):
            nonlocal calls
            with calls_lock:
                calls += 1
            if should_fail:
                upstream_started.set()
                if not release_failure.wait(2.0):
                    raise TimeoutError("test did not release failure")
                raise RuntimeError("offline failure")
            return "recovered"

        def run_one() -> str:
            gate.wait()
            return translator.translate("same-failure", "ja", "zh")

        with mock.patch.object(translator, "_request_upstream", fake_request):
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=worker_count
            ) as executor:
                futures = [executor.submit(run_one) for _ in range(worker_count)]
                self.assertTrue(upstream_started.wait(2.0))
                time.sleep(0.05)
                release_failure.set()
                for future in futures:
                    with self.assertRaisesRegex(RuntimeError, "offline failure"):
                        future.result(timeout=3.0)

            self.assertEqual(calls, 1)
            self.assertEqual(translator._translation_cache, {})
            self.assertEqual(translator._translation_inflight, {})
            should_fail = False
            self.assertEqual(
                translator.translate("same-failure", "ja", "zh"), "recovered"
            )

        self.assertEqual(calls, 2)
        self.assertEqual(len(translator._translation_cache), 1)

    def test_lru_entry_and_byte_limits(self) -> None:
        entry_limited = make_translator(
            "siliconflow-qwen", cache_entries=2, cache_bytes=1024
        )
        entry_calls: Counter[str] = Counter()

        def entry_request(payload):
            text = payload["messages"][-1]["content"].rsplit("\n\n", 1)[1]
            entry_calls[text] += 1
            return text.lower()

        with mock.patch.object(entry_limited, "_request_upstream", entry_request):
            for text in ("A", "B", "A", "C", "B"):
                self.assertEqual(
                    entry_limited.translate(text, "ja", "zh"), text.lower()
                )

        self.assertEqual(entry_calls, Counter({"B": 2, "A": 1, "C": 1}))
        self.assertEqual(len(entry_limited._translation_cache), 2)

        byte_limited = make_translator(
            "siliconflow-qwen", cache_entries=10, cache_bytes=5
        )
        byte_calls: Counter[str] = Counter()

        def byte_request(payload):
            text = payload["messages"][-1]["content"].rsplit("\n\n", 1)[1]
            byte_calls[text] += 1
            return text * 3

        with mock.patch.object(byte_limited, "_request_upstream", byte_request):
            for text in ("A", "B", "A", "XX", "XX"):
                self.assertEqual(
                    byte_limited.translate(text, "ja", "zh"), text * 3
                )

        self.assertEqual(byte_calls, Counter({"A": 2, "XX": 2, "B": 1}))
        self.assertLessEqual(byte_limited._translation_cache_bytes, 5)
        self.assertLessEqual(len(byte_limited._translation_cache), 10)

    def test_hunyuan_repeated_segments_share_one_validated_result(self) -> None:
        translator = make_translator(upstream_concurrency=4)
        calls: Counter[str] = Counter()
        calls_lock = threading.Lock()

        def fake_urlopen(request, _timeout=None, **_kwargs):
            segment = request_text(request)
            with calls_lock:
                calls[segment] += 1
            time.sleep(0.01)
            return FakeResponse(segment.lower())

        source = "A\nA\r\nB\rA"
        with mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(
                translator.translate(source, "ja", "zh"), "a\na\r\nb\ra"
            )

        self.assertEqual(calls, Counter({"A": 1, "B": 1}))
        self.assertEqual(len(translator._translation_cache), 2)

    def test_invalid_hunyuan_segment_is_never_cached(self) -> None:
        translator = make_translator()
        calls = 0

        def fake_urlopen(_request, _timeout=None, **_kwargs):
            nonlocal calls
            calls += 1
            return FakeResponse("bad\nline")

        with mock.patch.object(BRIDGE.urllib.request, "urlopen", fake_urlopen):
            for _ in range(2):
                with self.assertRaises(ValueError):
                    translator.translate("source", "ja", "zh")

        self.assertEqual(calls, 2)
        self.assertEqual(translator._translation_cache, {})
        self.assertEqual(translator._translation_inflight, {})

    def test_cross_order_hunyuan_requests_do_not_deadlock(self) -> None:
        for concurrency in (1, 2, 4):
            with self.subTest(upstream_concurrency=concurrency):
                translator = make_translator(upstream_concurrency=concurrency)
                lock = threading.Lock()
                calls: Counter[str] = Counter()
                active = 0
                maximum_active = 0

                def fake_urlopen(request, timeout):
                    nonlocal active, maximum_active
                    self.assertGreater(timeout, 0)
                    segment = request_text(request)
                    with lock:
                        calls[segment] += 1
                        active += 1
                        maximum_active = max(maximum_active, active)
                    try:
                        time.sleep(0.01)
                        return FakeResponse(segment.upper())
                    finally:
                        with lock:
                            active -= 1

                sources = ["x\ny", "y\nx"] * 8
                with (
                    mock.patch.object(
                        BRIDGE.urllib.request, "urlopen", fake_urlopen
                    ),
                    concurrent.futures.ThreadPoolExecutor(
                        max_workers=len(sources)
                    ) as executor,
                ):
                    futures = [
                        executor.submit(translator.translate, source, "ja", "zh")
                        for source in sources
                    ]
                    results = [future.result(timeout=5.0) for future in futures]

                self.assertEqual(
                    results,
                    ["X\nY" if source == "x\ny" else "Y\nX" for source in sources],
                )
                self.assertEqual(calls, Counter({"x": 1, "y": 1}))
                self.assertLessEqual(maximum_active, concurrency)
                self.assertEqual(translator._translation_inflight, {})

    def test_echo_mode_does_not_cache(self) -> None:
        translator = make_translator("siliconflow-qwen")
        translator.mode = "echo"
        with mock.patch.object(translator, "_request_upstream") as upstream:
            self.assertEqual(translator.translate("echo", "ja", "zh"), "echo")
            self.assertEqual(translator.translate("echo", "ja", "zh"), "echo")
        upstream.assert_not_called()
        self.assertEqual(translator._translation_cache, {})
        self.assertEqual(translator._translation_inflight, {})

    def test_zero_capacity_disables_cache_and_singleflight(self) -> None:
        translator = make_translator(
            "siliconflow-qwen", cache_entries=0, cache_bytes=0
        )
        calls = 0

        def fake_request(_payload):
            nonlocal calls
            calls += 1
            return "uncached"

        with mock.patch.object(translator, "_request_upstream", fake_request):
            self.assertEqual(translator.translate("same", "ja", "zh"), "uncached")
            self.assertEqual(translator.translate("same", "ja", "zh"), "uncached")

        self.assertEqual(calls, 2)
        self.assertEqual(translator._translation_cache, {})
        self.assertEqual(translator._translation_inflight, {})


if __name__ == "__main__":
    unittest.main()
