from __future__ import annotations

import hashlib
import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from renpy_patch import api_client  # noqa: E402
from renpy_patch.models import AppSettings, PromptMode, ProviderId  # noqa: E402
from renpy_patch.providers import build_connection_test_payload  # noqa: E402


class JsonResponse:
    def __init__(self, value: object) -> None:
        self.body = json.dumps(value, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class FixedUuid:
    hex = "0123456789abcdef0123456789abcdef"

    def __str__(self) -> str:
        return "01234567-89ab-cdef-0123-456789abcdef"


class ApiClientTests(unittest.TestCase):
    def test_custom_prompt_is_used_by_ai_connection_test(self) -> None:
        settings = AppSettings(
            mode="custom",
            prompt_mode=PromptMode.CUSTOM1.value,
            custom_prompt_1="从{source}到{target}：{text}",
        )
        payload = build_connection_test_payload(settings, "原文")
        self.assertEqual(
            payload["messages"],
            [{"role": "user", "content": "从ja到zh：原文"}],
        )

    def test_youdao_connection_test_signs_locally(self) -> None:
        settings = AppSettings(provider=ProviderId.YOUDAO.value)
        captured: dict[str, str] = {}

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 3.0)
            captured.update(
                {
                    name: values[0]
                    for name, values in urllib.parse.parse_qs(
                        request.data.decode("utf-8")
                    ).items()
                }
            )
            return JsonResponse({"errorCode": "0", "translation": ["你好"]})

        with (
            mock.patch.object(api_client.uuid, "uuid4", return_value=FixedUuid()),
            mock.patch.object(api_client.time, "time", return_value=1_700_000_000),
            mock.patch.object(api_client.urllib.request, "urlopen", fake_urlopen),
        ):
            result = api_client.test_api_connection(
                settings,
                {"app_key": "test-app", "app_secret": "test-secret"},
                timeout=3.0,
            )

        expected = hashlib.sha256(
            (
                "test-app"
                + "こんにちは"
                + FixedUuid.hex
                + "1700000000"
                + "test-secret"
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(result.translated_text, "你好")
        self.assertEqual(captured["sign"], expected)
        self.assertNotIn("test-secret", captured.values())

    def test_baidu_provider_error_is_sanitized(self) -> None:
        settings = AppSettings(provider=ProviderId.BAIDU.value)
        with mock.patch.object(
            api_client.urllib.request,
            "urlopen",
            return_value=JsonResponse({"error_code": "54003", "error_msg": "secret"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "54003") as caught:
                api_client.test_api_connection(
                    settings,
                    {"app_id": "test-id", "app_secret": "test-secret"},
                )
        self.assertNotIn("secret", str(caught.exception))

    def test_microsoft_connection_test_uses_key_header_and_region(self) -> None:
        settings = AppSettings(provider=ProviderId.MICROSOFT.value)
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            self.assertGreater(timeout, 0)
            captured["headers"] = {
                name.lower(): value for name, value in request.header_items()
            }
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["query"] = urllib.parse.parse_qs(
                urllib.parse.urlsplit(request.full_url).query
            )
            return JsonResponse(
                [{"translations": [{"text": "你好", "to": "zh-Hans"}]}]
            )

        with mock.patch.object(api_client.urllib.request, "urlopen", fake_urlopen):
            result = api_client.test_api_connection(
                settings,
                {"subscription_key": "test-key", "region": "eastasia"},
            )

        self.assertEqual(result.translated_text, "你好")
        headers = captured["headers"]
        self.assertEqual(headers["ocp-apim-subscription-key"], "test-key")
        self.assertEqual(headers["ocp-apim-subscription-region"], "eastasia")
        self.assertEqual(captured["body"], [{"Text": "こんにちは"}])
        self.assertEqual(captured["query"]["to"], ["zh-Hans"])


if __name__ == "__main__":
    unittest.main()
