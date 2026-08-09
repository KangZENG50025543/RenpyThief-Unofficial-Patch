from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class TranslationMode(str, Enum):
    OFFICIAL = "official"
    CUSTOM = "custom"


class QualityMode(str, Enum):
    FAST = "fast"
    HIGH = "high"


class PromptMode(str, Enum):
    TEMPLATE1 = "template1"
    TEMPLATE2 = "template2"
    CUSTOM = "custom"


class ProviderCategory(str, Enum):
    AI = "ai"
    DEDICATED_MT = "dedicated-mt"


class ProviderId(str, Enum):
    DEEPSEEK = "deepseek"
    SILICONFLOW_HUNYUAN = "siliconflow-hunyuan"
    OPENAI_COMPATIBLE = "openai-compatible"
    YOUDAO = "youdao"
    BAIDU = "baidu"
    MICROSOFT = "microsoft"


SETTINGS_SCHEMA_VERSION = 2
DEFAULT_CUSTOM_PROMPT = (
    "You are a precise game localization translator.\n\n"
    "Translate the following text from {source} to {target}. "
    "Return only the translation, with no explanation. Preserve names, "
    "line breaks, placeholders, and markup.\n\n"
    "{text}"
)


@dataclass(slots=True)
class AppSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    translator_path: str = ""
    mode: str = TranslationMode.OFFICIAL.value
    provider: str = ProviderId.DEEPSEEK.value
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    quality: str = QualityMode.FAST.value
    prompt_mode: str = PromptMode.TEMPLATE1.value
    custom_prompt: str = DEFAULT_CUSTOM_PROMPT
    block_updates: bool = True
    remember_api_key: bool = False
    bridge_concurrency: int = 64
    upstream_concurrency: int = 4
    cache_entries: int = 2048
    cache_mebibytes: int = 16

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "AppSettings":
        if not isinstance(value, dict):
            return cls()
        known = {field: value[field] for field in cls.__dataclass_fields__ if field in value}
        settings = cls(**known)
        settings.normalize()
        return settings

    def normalize(self) -> None:
        # Reading an older settings.json is intentionally a one-way migration.
        # Missing fields retain their safe defaults and the next save writes the
        # current schema version.
        self.schema_version = SETTINGS_SCHEMA_VERSION
        if self.mode not in {item.value for item in TranslationMode}:
            self.mode = TranslationMode.OFFICIAL.value
        if self.provider not in {item.value for item in ProviderId}:
            self.provider = ProviderId.DEEPSEEK.value
        if self.quality not in {item.value for item in QualityMode}:
            self.quality = QualityMode.FAST.value
        if self.prompt_mode not in {item.value for item in PromptMode}:
            self.prompt_mode = PromptMode.TEMPLATE1.value
        if not isinstance(self.block_updates, bool):
            self.block_updates = True
        self.bridge_concurrency = min(128, max(1, int(self.bridge_concurrency)))
        self.upstream_concurrency = min(
            self.bridge_concurrency, max(1, int(self.upstream_concurrency))
        )
        self.cache_entries = min(1_000_000, max(0, int(self.cache_entries)))
        self.cache_mebibytes = min(1024, max(0, int(self.cache_mebibytes)))
        self.translator_path = str(self.translator_path or "").strip()
        self.base_url = str(self.base_url or "").strip()
        self.model = str(self.model or "").strip()
        custom_prompt = str(self.custom_prompt or "")
        self.custom_prompt = (
            custom_prompt if custom_prompt.strip() else DEFAULT_CUSTOM_PROMPT
        )


@dataclass(frozen=True, slots=True)
class LaunchProfile:
    provider_id: ProviderId
    base_url: str
    model: str
    payload_profile: str
    thinking: str
    reasoning_effort: str
