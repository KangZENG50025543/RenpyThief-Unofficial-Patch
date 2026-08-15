from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .models import (
    AppSettings,
    LaunchProfile,
    ProviderCategory,
    ProviderId,
    QualityMode,
)


@dataclass(frozen=True, slots=True)
class CredentialField:
    key: str
    label: str
    secret: bool = False
    optional: bool = False
    placeholder: str = ""


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    provider_id: ProviderId
    label: str
    base_url: str
    model: str
    payload_profile: str
    supports_quality_modes: bool
    description: str
    category: ProviderCategory = ProviderCategory.AI
    credential_fields: tuple[CredentialField, ...] = (
        CredentialField("api_key", "API Key", secret=True),
    )
    network_ready: bool = True


PROVIDERS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        ProviderId.DEEPSEEK,
        "DeepSeek（推荐）",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        "deepseek",
        True,
        "当前实机验证最充分；极速模式关闭思考。",
    ),
    ProviderPreset(
        ProviderId.SILICONFLOW_HUNYUAN,
        "SiliconFlow · Hunyuan-MT",
        "https://api.siliconflow.cn/v1",
        "tencent/Hunyuan-MT-7B",
        "hunyuan-mt",
        False,
        "专用机器翻译模型，作为实验性选项保留。",
    ),
    ProviderPreset(
        ProviderId.OPENAI_COMPATIBLE,
        "OpenAI-compatible（高级）",
        "https://example.com/v1",
        "model-name",
        "openai",
        False,
        "适用于兼容 /chat/completions 的自定义服务。本机可用 http://127.0.0.1:端口/v1，API Key 可留空。",
        credential_fields=(
            CredentialField("api_key", "API Key（本机可留空）", secret=True, optional=True),
        ),
    ),
    ProviderPreset(
        ProviderId.LOCAL_OPENAI,
        "本地模型（OpenAI 兼容）",
        "http://127.0.0.1:11434/v1",
        "local-model",
        "openai",
        False,
        "llama.cpp / Ollama / vLLM 等本机服务。把 Base URL 改成实际端口，模型名改成已加载的模型；本机 HTTP 允许，API Key 可留空。",
        credential_fields=(
            CredentialField("api_key", "API Key（可留空）", secret=True, optional=True),
        ),
    ),
    ProviderPreset(
        ProviderId.YOUDAO,
        "有道智云翻译",
        "https://openapi.youdao.com/api",
        "general",
        "youdao",
        False,
        "有道专用文本翻译；使用应用 ID 与应用密钥在本机签名请求。",
        ProviderCategory.DEDICATED_MT,
        (
            CredentialField("app_key", "应用 ID (app_key)"),
            CredentialField("app_secret", "应用密钥 (app_secret)", secret=True),
        ),
        True,
    ),
    ProviderPreset(
        ProviderId.BAIDU,
        "百度翻译开放平台",
        "https://fanyi-api.baidu.com/api/trans/vip/translate",
        "general",
        "baidu",
        False,
        "百度通用文本翻译；第一版按标准版保守执行单并发、约 1 QPS。",
        ProviderCategory.DEDICATED_MT,
        (
            CredentialField("app_id", "APP ID (app_id)"),
            CredentialField("app_secret", "密钥 (app_secret)", secret=True),
        ),
        True,
    ),
    ProviderPreset(
        ProviderId.MICROSOFT,
        "Microsoft Translator",
        "https://api.cognitive.microsofttranslator.com",
        "general",
        "microsoft",
        False,
        "Microsoft Translator；全局单服务资源的 region 可以留空。",
        ProviderCategory.DEDICATED_MT,
        (
            CredentialField("subscription_key", "订阅密钥 (subscription_key)", secret=True),
            CredentialField(
                "region",
                "区域 (region，可留空)",
                optional=True,
                placeholder="例如 eastasia；全局单服务资源可留空",
            ),
        ),
        True,
    ),
)

_BY_ID = {item.provider_id.value: item for item in PROVIDERS}


def get_provider(provider: str | ProviderId) -> ProviderPreset:
    key = provider.value if isinstance(provider, ProviderId) else provider
    return _BY_ID.get(key, PROVIDERS[0])


def validate_base_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.username or parsed.password:
        raise ValueError("API 地址不能包含用户名或密码。")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API 地址必须是完整的 HTTP(S) 地址。")
    if parsed.query or parsed.fragment:
        raise ValueError("API 地址不能包含 query 或 fragment。")
    if any(character.isspace() for character in candidate):
        raise ValueError("API 地址不能包含空格或换行。")
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("非本机 API 必须使用 HTTPS。")
    suffix = "/chat/completions"
    if candidate.casefold().endswith(suffix):
        candidate = candidate[: -len(suffix)].rstrip("/")
    return candidate


def is_loopback_base_url(value: str) -> bool:
    host = (urlparse(validate_base_url(value)).hostname or "").casefold()
    return host in {"127.0.0.1", "localhost", "::1"}


def chat_completions_url(base_url: str) -> str:
    normalized = validate_base_url(base_url)
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def _prompt_language_name(value: str, *, chinese: bool = False) -> str:
    normalized = (value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "zh": "zh-hans",
        "zh-cn": "zh-hans",
        "zh-chs": "zh-hans",
        "zh-tw": "zh-hant",
        "zh-cht": "zh-hant",
        "jp": "ja",
        "kor": "ko",
    }
    canonical = aliases.get(normalized, normalized)
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


def make_launch_profile(settings: AppSettings) -> LaunchProfile:
    preset = get_provider(settings.provider)
    if not preset.network_ready:
        raise ValueError(f"{preset.label} 的网络接入尚未实现；当前仅预留凭据设置。")
    if preset.category is ProviderCategory.DEDICATED_MT:
        # Dedicated adapters use fixed HTTPS endpoints. Hidden/stale AI fields
        # from an older settings file must not redirect signed requests.
        base_url = preset.base_url
        model = preset.model
    else:
        base_url = validate_base_url(settings.base_url)
        model = settings.model.strip()
        if not model or any(character.isspace() for character in model):
            raise ValueError("模型名称不能为空，也不能包含空格或换行。")

    if preset.provider_id is ProviderId.DEEPSEEK:
        if settings.quality == QualityMode.HIGH.value:
            thinking, effort = "enabled", "high"
        else:
            thinking, effort = "disabled", "none"
    elif preset.provider_id is ProviderId.SILICONFLOW_HUNYUAN:
        thinking, effort = "omit", "none"
    else:
        thinking, effort = "omit", "none"

    return LaunchProfile(
        preset.provider_id,
        base_url,
        model,
        preset.payload_profile,
        thinking,
        effort,
    )


def build_connection_test_payload(
    settings: AppSettings, sample: str = "こんにちは"
) -> dict[str, object]:
    profile = make_launch_profile(settings)
    preset = get_provider(profile.provider_id)
    if preset.category is ProviderCategory.DEDICATED_MT:
        raise ValueError("专用翻译平台不使用 Chat Completions 测试载荷。")

    source = "ja"
    target = "zh"
    if settings.prompt_mode == "custom":
        rendered = (
            settings.custom_prompt.replace("{source}", source)
            .replace("{target}", target)
            .replace("{text}", sample)
        )
        if "{text}" not in settings.custom_prompt:
            rendered += "\n\n" + sample
        messages = [{"role": "user", "content": rendered}]
    elif settings.prompt_mode == "template2":
        messages = [
            {
                "role": "system",
                "content": "你是一名专业的游戏本地化译者和中文编辑。",
            },
            {
                "role": "user",
                "content": "请将下面的游戏文本从"
                f"{_prompt_language_name(source, chinese=True)}翻译成"
                f"{_prompt_language_name(target, chinese=True)}。"
                "译文应自然、符合角色语气和游戏场景；保留人名、专有名词、"
                "占位符、富文本标记与换行结构。只输出最终译文，不要解释。\n\n"
                + sample,
            },
        ]
    elif profile.payload_profile == "hunyuan-mt":
        messages = [
            {
                "role": "user",
                "content": "把下面的文本翻译成"
                f"{_prompt_language_name(target, chinese=True)}，不要额外解释。"
                "保留原文中的占位符和标记。\n\n"
                + sample,
            }
        ]
    else:
        messages = [
            {
                "role": "system",
                "content": "You are a precise game localization translator.",
            },
            {
                "role": "user",
                "content": "Translate the following text from "
                f"{_prompt_language_name(source)} to {_prompt_language_name(target)}. "
                "Return only the translation, with no explanation. Preserve names, "
                "line breaks, placeholders, and markup.\n\n"
                + sample,
            },
        ]

    if profile.payload_profile == "hunyuan-mt" and len(messages) > 1:
        messages = [
            {
                "role": "user",
                "content": "\n".join(
                    str(message["content"])
                    for message in messages
                    if message.get("content")
                ),
            }
        ]

    payload: dict[str, object] = {
        "model": profile.model,
        "messages": messages,
        "stream": False,
    }
    if profile.payload_profile == "deepseek":
        payload["thinking"] = {"type": profile.thinking}
        if profile.reasoning_effort != "none":
            payload["reasoning_effort"] = profile.reasoning_effort
        if profile.thinking != "enabled":
            payload["temperature"] = 0.2
    elif profile.payload_profile == "openai":
        payload["temperature"] = 0.2
        if is_loopback_base_url(profile.base_url):
            payload["reasoning_effort"] = "none"
    return payload
