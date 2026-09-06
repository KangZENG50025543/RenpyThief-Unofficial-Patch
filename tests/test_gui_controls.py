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

from PyQt5.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QWheelEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from renpy_patch.launcher import LaunchEvent, LaunchEventKind  # noqa: E402
from renpy_patch.main_window import MainWindow, NoWheelComboBox  # noqa: E402
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
        prompt_index = self.window.prompt_combo.findData(PromptMode.CUSTOM1.value)
        self.window.prompt_combo.setCurrentIndex(prompt_index)
        self.assertFalse(self.window.prompt_group.isHidden())
        self.assertFalse(self.window.custom_prompt_edit.isHidden())
        self.assertEqual(self.window.prompt_combo.count(), 5)
        prompt_index_3 = self.window.prompt_combo.findData(PromptMode.CUSTOM3.value)
        self.assertGreater(prompt_index_3, 0)
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

        self.window._handle_launch_event(
            LaunchEvent(LaunchEventKind.READY, "RenpyThief 已启动。", 4321)
        )
        self.assertEqual(self.window.status_title.text(), "我的 API · 已就绪")

    def test_local_openai_exposes_loopback_url_and_optional_key(self) -> None:
        provider_index = self.window.provider_combo.findData(
            ProviderId.LOCAL_OPENAI.value
        )
        self.window.provider_combo.setCurrentIndex(provider_index)
        self.assertFalse(self.window.base_url_edit.isHidden())
        self.assertIn("127.0.0.1", self.window.base_url_edit.text())
        self.assertIn("可留空", self.window.credential_labels[0].text())

    def test_siliconflow_is_platform_name_and_base_url_is_in_api_group(self) -> None:
        index = self.window.provider_combo.findData(ProviderId.SILICONFLOW_HUNYUAN.value)
        self.assertGreaterEqual(index, 0)
        self.assertEqual(self.window.provider_combo.itemText(index), "SiliconFlow")
        self.window.provider_combo.setCurrentIndex(index)
        self.assertFalse(self.window.base_url_label.isHidden())
        self.assertFalse(self.window.base_url_edit.isHidden())
        self.assertEqual(
            self.window.base_url_edit.text(), "https://api.siliconflow.cn/v1"
        )
        self.assertIs(self.window.base_url_edit.parent(), self.window.api_group)

    def test_launcher_locks_bundled_origin_and_has_no_official_mode(self) -> None:
        fake = Path(self.temporary_directory.name) / "6.7.8Origin" / "RenpyThief.exe"
        fake.parent.mkdir(parents=True)
        fake.write_bytes(b"bundled")
        with (
            patch(
                "renpy_patch.main_window.bundled_origin_exe", return_value=fake
            ),
            patch(
                "renpy_patch.main_window.bundled_origin_is_present",
                return_value=True,
            ),
        ):
            self.window._update_mode_ui()
            self.assertTrue(self.window.translator_path.isReadOnly())
            self.assertFalse(self.window.browse_button.isEnabled())
            self.assertFalse(self.window.browse_button.isVisible())
            self.assertEqual(self.window.translator_path.text(), str(fake))
            self.assertTrue(self.window.custom_radio.isChecked())
            settings = self.window._collect_settings()
            self.assertEqual(settings.mode, "custom")
            self.assertEqual(settings.translator_path, str(fake))
            self.assertEqual(self.window.start_button.text(), "使用我的 API 启动")
        self.assertFalse(hasattr(self.window, "official_radio"))

    def test_combo_boxes_ignore_mouse_wheel(self) -> None:
        combos = (
            self.window.provider_combo,
            self.window.quality_combo,
            self.window.prompt_combo,
        )
        for combo in combos:
            self.assertIsInstance(combo, NoWheelComboBox)
            combo.setCurrentIndex(0)
            event = QWheelEvent(
                QPointF(8, 8),
                QPointF(combo.mapToGlobal(QPoint(8, 8))),
                QPoint(0, 0),
                QPoint(0, 120),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.ScrollUpdate,
                False,
            )
            QApplication.sendEvent(combo, event)
            self.assertEqual(combo.currentIndex(), 0)

    def test_missing_bundled_origin_is_visible(self) -> None:
        with patch(
            "renpy_patch.main_window.bundled_origin_is_present",
            return_value=False,
        ):
            self.window._update_mode_ui()
        self.assertIn("6.7.8Origin", self.window.translator_path.placeholderText())


if __name__ == "__main__":
    unittest.main()
