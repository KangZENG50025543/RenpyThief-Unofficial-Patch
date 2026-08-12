from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from renpy_patch.launcher import (  # noqa: E402
    LaunchEventKind,
    PatchLauncher,
    _GuardedLaunch,
    _custom_bridge_environment,
    _launch_guarded_translator,
    build_custom_command,
)
from renpy_patch.models import (  # noqa: E402
    AppSettings,
    DEFAULT_CUSTOM_PROMPT,
    PromptMode,
    ProviderCategory,
    ProviderId,
    QualityMode,
    SETTINGS_SCHEMA_VERSION,
)
from renpy_patch.providers import (  # noqa: E402
    build_connection_test_payload,
    chat_completions_url,
    get_provider,
    make_launch_profile,
    validate_base_url,
)
from renpy_patch.settings import SettingsStore  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_invalid_values_are_normalized(self) -> None:
        settings = AppSettings.from_dict(
            {
                "mode": "unknown",
                "provider": "unknown",
                "quality": "unknown",
                "prompt_mode": "unknown",
                "block_updates": "no",
                "bridge_concurrency": 999,
                "upstream_concurrency": 999,
                "cache_entries": -1,
                "cache_mebibytes": -1,
            }
        )
        self.assertEqual(settings.mode, "official")
        self.assertEqual(settings.provider, ProviderId.DEEPSEEK.value)
        self.assertEqual(settings.quality, QualityMode.FAST.value)
        self.assertEqual(settings.prompt_mode, PromptMode.TEMPLATE1.value)
        self.assertTrue(settings.block_updates)
        self.assertEqual(settings.bridge_concurrency, 128)
        self.assertEqual(settings.upstream_concurrency, 128)
        self.assertEqual(settings.cache_entries, 0)
        self.assertEqual(settings.cache_mebibytes, 0)

    def test_settings_round_trip_contains_no_secret_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            source = AppSettings(translator_path=r"C:\Games\RenpyThief.exe")
            store.save(source)
            raw = path.read_text(encoding="utf-8")
            serialized = json.loads(raw)
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("secret", serialized)
            self.assertEqual(store.load().translator_path, source.translator_path)
            self.assertEqual(serialized["schema_version"], SETTINGS_SCHEMA_VERSION)
            self.assertTrue(serialized["block_updates"])

    def test_schema_one_settings_migrate_with_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "mode": "custom",
                        "provider": ProviderId.DEEPSEEK.value,
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-v4-flash",
                    }
                ),
                encoding="utf-8",
            )
            settings = SettingsStore(path).load()
            self.assertEqual(settings.schema_version, SETTINGS_SCHEMA_VERSION)
            self.assertEqual(settings.prompt_mode, PromptMode.TEMPLATE1.value)
            self.assertEqual(settings.custom_prompt, DEFAULT_CUSTOM_PROMPT)
            self.assertTrue(settings.block_updates)

    def test_prompt_and_update_preferences_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            source = AppSettings(
                prompt_mode=PromptMode.CUSTOM.value,
                custom_prompt="Translate {text} from {source} to {target}.",
                block_updates=False,
            )
            store.save(source)
            loaded = store.load()
            self.assertEqual(loaded.prompt_mode, PromptMode.CUSTOM.value)
            self.assertEqual(loaded.custom_prompt, source.custom_prompt)
            self.assertFalse(loaded.block_updates)


class ProviderTests(unittest.TestCase):
    def test_dedicated_mt_provider_metadata_and_credential_names(self) -> None:
        expected = {
            ProviderId.YOUDAO: ("app_key", "app_secret"),
            ProviderId.BAIDU: ("app_id", "app_secret"),
            ProviderId.MICROSOFT: ("subscription_key", "region"),
        }
        for provider_id, credential_names in expected.items():
            provider = get_provider(provider_id)
            self.assertIs(provider.category, ProviderCategory.DEDICATED_MT)
            self.assertEqual(
                tuple(field.key for field in provider.credential_fields),
                credential_names,
            )
            self.assertTrue(provider.network_ready)

    def test_dedicated_provider_uses_fixed_adapter_profile(self) -> None:
        settings = AppSettings(
            mode="custom",
            provider=ProviderId.YOUDAO.value,
            base_url="https://stale-ai-setting.invalid/v1",
            model="stale-model",
        )
        profile = make_launch_profile(settings)
        self.assertEqual(profile.payload_profile, "youdao")
        self.assertEqual(profile.base_url, "https://openapi.youdao.com/api")

    def test_deepseek_fast_and_high_profiles(self) -> None:
        settings = AppSettings(mode="custom")
        fast = make_launch_profile(settings)
        self.assertEqual(fast.thinking, "disabled")
        self.assertEqual(fast.reasoning_effort, "none")
        settings.quality = QualityMode.HIGH.value
        high = make_launch_profile(settings)
        self.assertEqual(high.thinking, "enabled")
        self.assertEqual(high.reasoning_effort, "high")

    def test_hunyuan_payload_omits_provider_thinking_fields(self) -> None:
        settings = AppSettings(
            mode="custom",
            provider=ProviderId.SILICONFLOW_HUNYUAN.value,
            base_url="https://api.siliconflow.cn/v1",
            model="tencent/Hunyuan-MT-7B",
        )
        payload = build_connection_test_payload(settings)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(len(payload["messages"]), 1)

    def test_endpoint_and_http_policy(self) -> None:
        self.assertEqual(
            chat_completions_url("https://api.deepseek.com"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            chat_completions_url("http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434/v1/chat/completions",
        )
        self.assertEqual(
            chat_completions_url("https://example.com/v1/chat/completions"),
            "https://example.com/v1/chat/completions",
        )
        with self.assertRaises(ValueError):
            validate_base_url("http://example.com/v1")
        with self.assertRaises(ValueError):
            validate_base_url("https://user:pass@example.com/v1")
        with self.assertRaises(ValueError):
            validate_base_url("https://example.com/v1?token=nope")


class BridgeEnvironmentTests(unittest.TestCase):
    def test_ai_prompt_and_key_use_environment_not_settings(self) -> None:
        settings = AppSettings(
            mode="custom",
            prompt_mode=PromptMode.CUSTOM.value,
            custom_prompt="只翻译：{text}",
        )
        environment = _custom_bridge_environment(settings, {"api_key": "test-api-key"})
        self.assertEqual(environment["UPSTREAM_API_KEY"], "test-api-key")
        self.assertEqual(environment["UPSTREAM_PROMPT_MODE"], "custom")
        self.assertEqual(environment["UPSTREAM_CUSTOM_PROMPT"], "只翻译：{text}")
        self.assertNotIn("UPSTREAM_CREDENTIALS_JSON", environment)
        self.assertNotIn("test-api-key", json.dumps(settings.to_dict()))

    def test_dedicated_credentials_are_json_and_stale_ai_values_are_removed(self) -> None:
        settings = AppSettings(mode="custom", provider=ProviderId.YOUDAO.value)
        with mock.patch.dict(
            os.environ,
            {
                "UPSTREAM_API_KEY": "stale-key",
                "DEEPSEEK_API_KEY": "unrelated-provider-key",
                "EXAMPLE_SECRET": "unrelated-secret",
            },
        ):
            environment = _custom_bridge_environment(
                settings,
                {"app_key": "test-app", "app_secret": "test-secret"},
            )
        self.assertNotIn("UPSTREAM_API_KEY", environment)
        self.assertNotIn("DEEPSEEK_API_KEY", environment)
        self.assertNotIn("EXAMPLE_SECRET", environment)
        self.assertEqual(
            json.loads(environment["UPSTREAM_CREDENTIALS_JSON"]),
            {"app_key": "test-app", "app_secret": "test-secret"},
        )
        self.assertNotIn("UPSTREAM_PROMPT_MODE", environment)


class GuardedLauncherTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        translator = root / "RenpyThief.exe"
        translator.write_bytes(b"test")
        router = root / "router" / "start_routed_translator.ps1"
        router.parent.mkdir()
        router.write_text("# test", encoding="utf-8")
        for name in ("guardlaunch.exe", "versionguard.dll", "versionguard.ini"):
            (router.parent / name).write_bytes(b"test")
        return translator, router

    def test_guard_timeout_warning_returns_live_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            translator, router = self._fixture(Path(directory))
            process = mock.Mock(pid=4321)
            process.image_path.return_value = translator
            completed = mock.Mock(
                returncode=0,
                stdout=b"Started guarded RenpyThief PID 4321.\r\n",
                stderr=(
                    b"WARNING: no known version check was observed within 20000 ms; "
                    b"continuing with update protection unconfirmed.\r\n"
                ),
            )
            with (
                mock.patch("renpy_patch.launcher.subprocess.run", return_value=completed),
                mock.patch(
                    "renpy_patch.launcher._WindowsPidProcess", return_value=process
                ) as process_type,
            ):
                result = _launch_guarded_translator(translator, router)

        self.assertIs(result.process, process)
        self.assertIn("20 秒内没有观察到", result.warning or "")
        process_type.assert_called_once_with(4321)

    def test_guard_hard_failure_remains_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            translator, router = self._fixture(Path(directory))
            completed = mock.Mock(
                returncode=8,
                stdout=b"",
                stderr=b"versionguard injection failed: 5\r\n",
            )
            with (
                mock.patch("renpy_patch.launcher.subprocess.run", return_value=completed),
                mock.patch("renpy_patch.launcher._WindowsPidProcess") as process_type,
                self.assertRaisesRegex(RuntimeError, "代码 8.*injection failed"),
            ):
                _launch_guarded_translator(translator, router)
        process_type.assert_not_called()

    def test_guard_unknown_success_diagnostic_remains_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            translator, router = self._fixture(Path(directory))
            completed = mock.Mock(
                returncode=0,
                stdout=b"Started guarded RenpyThief PID 4321.\r\n",
                stderr=b"unexpected output\r\n",
            )
            process = mock.Mock(pid=4321)
            process.image_path.return_value = translator
            with (
                mock.patch("renpy_patch.launcher.subprocess.run", return_value=completed),
                mock.patch(
                    "renpy_patch.launcher._WindowsPidProcess", return_value=process
                ) as process_type,
                self.assertRaisesRegex(RuntimeError, "未知诊断"),
            ):
                _launch_guarded_translator(translator, router)
        process_type.assert_called_once_with(4321)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        process.close.assert_called_once_with()

    def test_guard_unknown_diagnostic_error_survives_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            translator, router = self._fixture(Path(directory))
            completed = mock.Mock(
                returncode=0,
                stdout=b"Started guarded RenpyThief PID 4321.\r\n",
                stderr=b"unexpected output\r\n",
            )
            process = mock.Mock(pid=4321)
            process.image_path.return_value = translator
            process.terminate.side_effect = OSError("cleanup failed")
            with (
                mock.patch("renpy_patch.launcher.subprocess.run", return_value=completed),
                mock.patch(
                    "renpy_patch.launcher._WindowsPidProcess", return_value=process
                ),
                self.assertRaisesRegex(RuntimeError, "未知诊断"),
            ):
                _launch_guarded_translator(translator, router)
        process.close.assert_called_once_with()

    def test_guard_malformed_stdout_cleans_up_exact_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            translator, router = self._fixture(Path(directory))
            completed = mock.Mock(
                returncode=0,
                stdout=(
                    b"Started guarded RenpyThief PID 4321.\r\n"
                    b"unexpected extra output\r\n"
                ),
                stderr=b"",
            )
            process = mock.Mock(pid=4321)
            process.image_path.return_value = translator
            with (
                mock.patch("renpy_patch.launcher.subprocess.run", return_value=completed),
                mock.patch(
                    "renpy_patch.launcher._WindowsPidProcess", return_value=process
                ),
                self.assertRaisesRegex(RuntimeError, "无效结果"),
            ):
                _launch_guarded_translator(translator, router)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        process.close.assert_called_once_with()

    def test_guard_pid_hint_for_other_path_is_not_terminated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translator, router = self._fixture(root)
            completed = mock.Mock(
                returncode=0,
                stdout=(
                    b"Started guarded RenpyThief PID 4321.\r\n"
                    b"unexpected extra output\r\n"
                ),
                stderr=b"",
            )
            process = mock.Mock(pid=4321)
            process.image_path.return_value = root / "Other.exe"
            with (
                mock.patch("renpy_patch.launcher.subprocess.run", return_value=completed),
                mock.patch(
                    "renpy_patch.launcher._WindowsPidProcess", return_value=process
                ),
                self.assertRaisesRegex(RuntimeError, "进程路径不匹配"),
            ):
                _launch_guarded_translator(translator, router)
        process.terminate.assert_not_called()
        process.close.assert_called_once_with()

    def test_official_warning_does_not_stop_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            translator = Path(directory) / "RenpyThief.exe"
            translator.write_bytes(b"test")
            settings = AppSettings(translator_path=str(translator), mode="official")
            events = []
            process = mock.Mock(pid=4321)
            process.poll.return_value = None
            guarded = _GuardedLaunch(process, "更新保护状态尚未确认。")
            launcher = PatchLauncher(events.append)
            with (
                mock.patch("renpy_patch.launcher._has_existing_translator", return_value=False),
                mock.patch(
                    "renpy_patch.launcher._launch_guarded_translator",
                    return_value=guarded,
                ),
                mock.patch("renpy_patch.launcher.threading.Thread") as thread_type,
            ):
                launcher.start(settings)

        self.assertEqual(
            [event.kind for event in events],
            [LaunchEventKind.STARTING, LaunchEventKind.WARNING],
        )
        self.assertTrue(launcher.running)
        self.assertEqual(launcher.translator_pid, 4321)
        thread_type.return_value.start.assert_called_once_with()

    def test_custom_warning_is_followed_by_ready(self) -> None:
        events = []
        launcher = PatchLauncher(events.append)
        process = mock.Mock()
        process.stdout = io.BytesIO(
            b"WARNING: UPDATE_GUARD_WARNING: timeout_ms=20000\r\n"
            b"Translator-wide route active: 127.0.0.1\r\n"
        )
        process.wait.return_value = 0
        with launcher._lock:
            launcher._process = process
        launcher._monitor_custom(process)

        kinds = [event.kind for event in events]
        self.assertIn(LaunchEventKind.WARNING, kinds)
        self.assertIn(LaunchEventKind.READY, kinds)
        self.assertNotIn(LaunchEventKind.ERROR, kinds)
        warning = next(event for event in events if event.kind is LaunchEventKind.WARNING)
        self.assertIn("20 秒内没有观察到", warning.message)

    def test_powershell_guard_contract_forwards_only_timeout_warning(self) -> None:
        script = (
            PROJECT_DIR / "router" / "start_routed_translator.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("if ($launcher.ExitCode -ne 0)", script)
        self.assertIn("Version-update guard failed closed", script)
        self.assertIn("UPDATE_GUARD_WARNING:", script)
        self.assertIn("continuing with update protection unconfirmed", script)
        self.assertIn("Version-update guard returned an unknown diagnostic", script)


@unittest.skipUnless(sys.platform == "win32", "Windows launcher contract")
class LauncherCommandTests(unittest.TestCase):
    def test_command_is_argument_list_and_contains_no_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translator = root / "RenpyThief.exe"
            translator.write_bytes(b"test")
            router = root / "router" / "start_routed_translator.ps1"
            router.parent.mkdir()
            router.write_text("# test", encoding="utf-8")
            settings = AppSettings(
                translator_path=str(translator),
                mode="custom",
                provider=ProviderId.DEEPSEEK.value,
            )
            command = build_custom_command(settings, router)
            self.assertIn("-File", command)
            self.assertIn(str(router), command)
            self.assertIn("-TranslatorPath", command)
            self.assertEqual(command[command.index("-BlockUpdates") + 1], "true")
            self.assertNotIn("UPSTREAM_API_KEY", " ".join(command))
            self.assertNotIn("sk-", " ".join(command))

    def test_packaged_bridge_is_selected_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translator = root / "RenpyThief.exe"
            translator.write_bytes(b"test")
            router = root / "router" / "start_routed_translator.ps1"
            router.parent.mkdir()
            router.write_text("# test", encoding="utf-8")
            bridge = router.parent / "translate_bridge.exe"
            bridge.write_bytes(b"test")
            settings = AppSettings(translator_path=str(translator), mode="custom")
            command = build_custom_command(settings, router)
            index = command.index("-BridgeExecutable")
            self.assertEqual(command[index + 1], str(bridge.resolve()))

    def test_update_guard_can_be_disabled_for_custom_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translator = root / "RenpyThief.exe"
            translator.write_bytes(b"test")
            router = root / "router" / "start_routed_translator.ps1"
            router.parent.mkdir()
            router.write_text("# test", encoding="utf-8")
            settings = AppSettings(
                translator_path=str(translator), mode="custom", block_updates=False
            )
            command = build_custom_command(settings, router)
            self.assertEqual(command[command.index("-BlockUpdates") + 1], "false")

    def test_dedicated_provider_selects_adapter_mode_without_credentials_on_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translator = root / "RenpyThief.exe"
            translator.write_bytes(b"test")
            router = root / "router" / "start_routed_translator.ps1"
            router.parent.mkdir()
            router.write_text("# test", encoding="utf-8")
            settings = AppSettings(
                translator_path=str(translator),
                mode="custom",
                provider=ProviderId.YOUDAO.value,
            )
            command = build_custom_command(settings, router)
            self.assertEqual(command[command.index("-Mode") + 1], "youdao")
            self.assertEqual(
                command[command.index("-PayloadProfile") + 1], "openai"
            )
            joined = " ".join(command)
            self.assertNotIn("test-app", joined)
            self.assertNotIn("test-secret", joined)


if __name__ == "__main__":
    unittest.main()
