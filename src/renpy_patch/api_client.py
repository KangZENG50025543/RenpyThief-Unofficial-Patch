from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Mapping

from .models import AppSettings, ProviderId
from .providers import (
    build_connection_test_payload,
    chat_completions_url,
    get_provider,
    is_loopback_base_url,
    make_launch_profile,
)


@dataclass(frozen=True, slots=True)
class ApiTestResult:
    translated_text: str
    elapsed_ms: float


def _credential_values(
    credentials: str | Mapping[str, str],
) -> dict[str, str]:
    # A plain string remains accepted for callers from the first GUI version.
    if isinstance(credentials, str):
        return {"api_key": credentials.strip()}
    return {
        str(name): str(value).strip()
        for name, value in credentials.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def _required(values: Mapping[str, str], *names: str) -> tuple[str, ...]:
    result: list[str] = []
    for name in names:
        value = values.get(name, "").strip()
        if not value:
            raise ValueError(f"缺少凭据字段：{name}。")
        result.append(value)
    return tuple(result)


def _request_json(request: urllib.request.Request, timeout: float) -> object:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise RuntimeError(f"API 返回 HTTP {status}。") from error
    except urllib.error.URLError as error:
        raise RuntimeError("无法连接 API，请检查网络、代理和地址。") from error
    except TimeoutError as error:
        raise RuntimeError("API 连接测试超时。") from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("API 返回的不是有效 JSON。") from error


def _test_ai(
    settings: AppSettings,
    values: Mapping[str, str],
    timeout: float,
) -> str:
    profile = make_launch_profile(settings)
    api_key = values.get("api_key", "").strip()
    if not api_key and not is_loopback_base_url(profile.base_url):
        raise ValueError("非本机 API 必须填写 API Key。")
    payload = json.dumps(
        build_connection_test_payload(settings), ensure_ascii=False
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(
        chat_completions_url(profile.base_url),
        data=payload,
        headers=headers,
        method="POST",
    )
    body = _request_json(request, timeout)
    try:
        translated = body["choices"][0]["message"]["content"].strip()
    except (IndexError, KeyError, TypeError, AttributeError) as error:
        raise RuntimeError("API 响应缺少 choices[0].message.content。") from error
    if not translated:
        raise RuntimeError("API 返回了空译文。")
    return translated


def _test_youdao(values: Mapping[str, str], timeout: float) -> str:
    app_key, app_secret = _required(values, "app_key", "app_secret")
    sample = "こんにちは"
    salt = uuid.uuid4().hex
    current_time = str(int(time.time()))
    sign_input = (
        sample
        if len(sample) <= 20
        else sample[:10] + str(len(sample)) + sample[-10:]
    )
    sign = hashlib.sha256(
        (app_key + sign_input + salt + current_time + app_secret).encode("utf-8")
    ).hexdigest()
    data = urllib.parse.urlencode(
        {
            "q": sample,
            "from": "ja",
            "to": "zh-CHS",
            "appKey": app_key,
            "salt": salt,
            "sign": sign,
            "signType": "v3",
            "curtime": current_time,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://openapi.youdao.com/api",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    body = _request_json(request, timeout)
    if not isinstance(body, dict) or str(body.get("errorCode", "")) != "0":
        code = body.get("errorCode", "unknown") if isinstance(body, dict) else "invalid"
        raise RuntimeError(f"有道翻译返回错误码 {code}。")
    translations = body.get("translation")
    if not isinstance(translations, list) or not translations:
        raise RuntimeError("有道翻译响应缺少 translation。")
    translated = translations[0]
    if not isinstance(translated, str) or not translated.strip():
        raise RuntimeError("有道翻译返回了空译文。")
    return translated.strip()


def _test_baidu(values: Mapping[str, str], timeout: float) -> str:
    app_id, app_secret = _required(values, "app_id", "app_secret")
    sample = "こんにちは"
    salt = uuid.uuid4().hex
    sign = hashlib.md5(
        (app_id + sample + salt + app_secret).encode("utf-8")
    ).hexdigest()
    data = urllib.parse.urlencode(
        {
            "q": sample,
            "from": "jp",
            "to": "zh",
            "appid": app_id,
            "salt": salt,
            "sign": sign,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://fanyi-api.baidu.com/api/trans/vip/translate",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    body = _request_json(request, timeout)
    if not isinstance(body, dict):
        raise RuntimeError("百度翻译返回了无效响应。")
    code = body.get("error_code")
    if code is not None and str(code) != "52000":
        raise RuntimeError(f"百度翻译返回错误码 {code}。")
    results = body.get("trans_result")
    if not isinstance(results, list) or not results:
        raise RuntimeError("百度翻译响应缺少 trans_result。")
    translated = results[0].get("dst") if isinstance(results[0], dict) else None
    if not isinstance(translated, str) or not translated.strip():
        raise RuntimeError("百度翻译返回了空译文。")
    return translated.strip()


def _test_microsoft(values: Mapping[str, str], timeout: float) -> str:
    (subscription_key,) = _required(values, "subscription_key")
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Ocp-Apim-Subscription-Key": subscription_key,
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    region = values.get("region", "").strip()
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region
    endpoint = (
        "https://api.cognitive.microsofttranslator.com/translate?"
        + urllib.parse.urlencode(
            {"api-version": "3.0", "from": "ja", "to": "zh-Hans"}
        )
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps([{"Text": "こんにちは"}], ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    body = _request_json(request, timeout)
    try:
        translated = body[0]["translations"][0]["text"]
    except (IndexError, KeyError, TypeError) as error:
        raise RuntimeError("Microsoft Translator 响应缺少译文。") from error
    if not isinstance(translated, str) or not translated.strip():
        raise RuntimeError("Microsoft Translator 返回了空译文。")
    return translated.strip()


def test_api_connection(
    settings: AppSettings,
    credentials: str | Mapping[str, str],
    timeout: float = 20.0,
) -> ApiTestResult:
    values = _credential_values(credentials)
    provider_id = get_provider(settings.provider).provider_id
    started = time.perf_counter()
    if provider_id is ProviderId.YOUDAO:
        translated = _test_youdao(values, timeout)
    elif provider_id is ProviderId.BAIDU:
        translated = _test_baidu(values, timeout)
    elif provider_id is ProviderId.MICROSOFT:
        translated = _test_microsoft(values, timeout)
    else:
        translated = _test_ai(settings, values, timeout)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return ApiTestResult(translated, elapsed_ms)
