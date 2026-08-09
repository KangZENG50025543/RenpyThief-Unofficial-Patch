from __future__ import annotations

import json
import re

import keyring
from keyring.errors import KeyringError


SERVICE_NAME = "RenpyThiefUnofficialPatch"
_BUNDLE_PREFIX = "renpy-patch-credentials-v1:"
_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    """Store API keys in the current user's Windows Credential Manager."""

    def get(self, provider_id: str) -> str:
        bundle = self.get_bundle(provider_id)
        return bundle.get("api_key", "")

    def get_bundle(self, provider_id: str) -> dict[str, str]:
        try:
            stored = keyring.get_password(SERVICE_NAME, provider_id) or ""
        except KeyringError as error:
            raise CredentialError("无法读取 Windows 凭据管理器。") from error
        if not stored:
            return {}
        if not stored.startswith(_BUNDLE_PREFIX):
            # Backward-compatible migration path for the original single-key
            # entry. It remains in Credential Manager and is never copied into
            # settings.json.
            return {"api_key": stored}
        try:
            value = json.loads(stored[len(_BUNDLE_PREFIX) :])
        except (json.JSONDecodeError, UnicodeError) as error:
            raise CredentialError("Windows 凭据中的密钥数据已损坏。") from error
        if not isinstance(value, dict) or not all(
            isinstance(key, str)
            and _FIELD_PATTERN.fullmatch(key)
            and isinstance(item, str)
            for key, item in value.items()
        ):
            raise CredentialError("Windows 凭据中的密钥格式无效。")
        return {key: item for key, item in value.items() if item}

    def set(self, provider_id: str, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise CredentialError("API Key 不能为空。")
        self.set_bundle(provider_id, {"api_key": value})

    def set_bundle(self, provider_id: str, values: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, item in values.items():
            if not isinstance(key, str) or not _FIELD_PATTERN.fullmatch(key):
                raise CredentialError("凭据字段名称无效。")
            if not isinstance(item, str):
                raise CredentialError("凭据字段值必须是文本。")
            stripped = item.strip()
            if stripped:
                normalized[key] = stripped
        if not normalized:
            raise CredentialError("凭据不能为空。")
        serialized = _BUNDLE_PREFIX + json.dumps(
            normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        try:
            keyring.set_password(SERVICE_NAME, provider_id, serialized)
        except KeyringError as error:
            raise CredentialError("无法写入 Windows 凭据管理器。") from error

    def delete(self, provider_id: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, provider_id)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as error:
            raise CredentialError("无法从 Windows 凭据管理器删除密钥。") from error
