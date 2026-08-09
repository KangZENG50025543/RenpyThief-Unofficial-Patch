from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from renpy_patch import credentials  # noqa: E402


class CredentialBundleTests(unittest.TestCase):
    def test_legacy_single_key_is_read_without_migration_loss(self) -> None:
        with mock.patch.object(
            credentials.keyring, "get_password", return_value="sk-legacy"
        ):
            store = credentials.CredentialStore()
            self.assertEqual(store.get("deepseek"), "sk-legacy")
            self.assertEqual(
                store.get_bundle("deepseek"), {"api_key": "sk-legacy"}
            )

    def test_provider_bundle_is_serialized_only_to_keyring(self) -> None:
        captured: dict[str, str] = {}

        def save(_service: str, _provider: str, value: str) -> None:
            captured["value"] = value

        with mock.patch.object(credentials.keyring, "set_password", save):
            credentials.CredentialStore().set_bundle(
                "youdao", {"app_key": "identifier", "app_secret": "secret"}
            )

        stored = captured["value"]
        self.assertTrue(stored.startswith("renpy-patch-credentials-v1:"))
        decoded = json.loads(stored.split(":", 1)[1])
        self.assertEqual(decoded["app_key"], "identifier")
        self.assertEqual(decoded["app_secret"], "secret")

    def test_invalid_field_name_is_rejected(self) -> None:
        with self.assertRaises(credentials.CredentialError):
            credentials.CredentialStore().set_bundle(
                "provider", {"bad-field": "value"}
            )


if __name__ == "__main__":
    unittest.main()
