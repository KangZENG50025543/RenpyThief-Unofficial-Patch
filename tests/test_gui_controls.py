from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from renpy_patch.launcher import LaunchEvent, LaunchEventKind  # noqa: E402
from renpy_patch.main_window import MainWindow  # noqa: E402
from renpy_patch.models import PromptMode, ProviderId  # noqa: E402
from renpy_patch.settings import SettingsStore  # noqa: E402


class MemoryCredentialStore:
    def get(self, _provider_id: str) -> str:
        return ""

    def get_bundle(self, _provider_id: str) -> dict[str, str]:
        return {}

    def set(self, _provider_id: str, _value: str) -> None:
        pass

    def set_bundle(self, _provider_id: str, _values: dict[str, str]) -> None:
        pass

    def delete(self, _provider_id: str) -> None:
        pass


class GuiControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        store = SettingsStore(
            Path(self.temporary_directory.name) / "settings.json"
        )
        self.window = MainWindow(store, MemoryCredentialStore())

    def tearDown(self) -> None:
        self.window.executor.shutdown(wait=False, cancel_futures=True)
        self.window.deleteLater()
        self.temporary_directory.cleanup()

    def test_disabling_update_block_can_be_cancelled(self) -> None:
        self.assertTrue(self.window.block_updates_checkbox.isChecked())
        with patch.object(
            QMessageBox, "warning", return_value=QMessageBox.Cancel
        ) as warning:
            self.window.block_updates_checkbox.click()
        self.assertTrue(self.window.block_updates_checkbox.isChecked())
        warning.assert_called_once()

        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Yes):
            self.window.block_updates_checkbox.click()
        self.assertFalse(self.window.block_updates_checkbox.isChecked())

    def test_prompt_controls_are_ai_only(self) -> None:
        self.window.custom_radio.setChecked(True)
        prompt_index = self.window.prompt_combo.findData(PromptMode.CUSTOM.value)
        self.window.prompt_combo.setCurrentIndex(prompt_index)
        self.assertFalse(self.window.prompt_group.isHidden())
        self.assertFalse(self.window.custom_prompt_edit.isHidden())
        prompt_text = self.window.custom_prompt_edit.toPlainText()
        self.assertIn("{source}", prompt_text)
        self.assertIn("{target}", prompt_text)
        self.assertIn("{text}", prompt_text)

        provider_index = self.window.provider_combo.findData(ProviderId.YOUDAO.value)
        self.window.provider_combo.setCurrentIndex(provider_index)
        self.assertTrue(self.window.prompt_group.isHidden())
        self.assertEqual(self.window.credential_labels[0].text(), "应用 ID (app_key)")
        self.assertEqual(
            self.window.credential_labels[1].text(), "应用密钥 (app_secret)"
        )

    def test_update_guard_warning_keeps_running_and_ready_can_follow(self) -> None:
        self.window._handle_launch_event(
            LaunchEvent(LaunchEventKind.STARTING, "正在启动……")
        )
        with (
            patch.object(QMessageBox, "warning") as warning,
            patch.object(QMessageBox, "critical") as critical,
        ):
            self.window._handle_launch_event(
                LaunchEvent(
                    LaunchEventKind.WARNING,
                    "20 秒内没有观察到已知版本检查，继续启动。",
                    4321,
                )
            )
            self.assertFalse(self.window.start_button.isEnabled())
            self.assertTrue(self.window.stop_button.isEnabled())
            self.assertEqual(self.window.status_title.text(), "更新保护未确认")
            warning.assert_called_once()
            critical.assert_not_called()

        self.window.official_radio.setChecked(True)
        self.window._handle_launch_event(
            LaunchEvent(LaunchEventKind.READY, "RenpyThief 已启动。", 4321)
        )
        self.assertEqual(self.window.status_title.text(), "官方额度 · 已就绪")

    def test_local_openai_exposes_loopback_url_and_optional_key(self) -> None:
        self.window.custom_radio.setChecked(True)
        provider_index = self.window.provider_combo.findData(
            ProviderId.LOCAL_OPENAI.value
        )
        self.window.provider_combo.setCurrentIndex(provider_index)
        self.assertTrue(self.window.advanced_group.isChecked())
        self.assertIn("127.0.0.1", self.window.base_url_edit.text())
        self.assertIn("可留空", self.window.credential_labels[0].text())


if __name__ == "__main__":
    unittest.main()
