from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from renpy_patch.bundled import (  # noqa: E402
    prepare_bundled_runtime,
    resolve_launch_translator,
)
from renpy_patch.models import AppSettings  # noqa: E402


class BundledOriginTests(unittest.TestCase):
    def test_custom_mode_copies_origin_and_skips_pollution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin"
            runtime = root / "runtime"
            origin.mkdir()
            (origin / "RenpyThief.exe").write_bytes(b"clean-exe")
            (origin / "Qt5Core.dll").write_bytes(b"dll")
            (origin / "user").write_bytes(b"polluted")
            (origin / "hwid").write_bytes(b"id")
            (origin / "settings.ini").write_text("bad=1\n", encoding="utf-8")
            (origin / "debug.log").write_text("log\n", encoding="utf-8")
            settings = AppSettings(
                translator_path=r"C:\Games\RenpyThief.exe",
                mode="custom",
            )
            launched = resolve_launch_translator(
                settings, origin_dir=origin, runtime_dir=runtime
            )
            self.assertEqual(launched, (runtime / "RenpyThief.exe").resolve())
            self.assertEqual(launched.read_bytes(), b"clean-exe")
            self.assertTrue((runtime / "Qt5Core.dll").is_file())
            self.assertFalse((runtime / "user").exists())
            self.assertFalse((runtime / "hwid").exists())
            self.assertFalse((runtime / "settings.ini").exists())
            self.assertFalse((runtime / "debug.log").exists())

    def test_runtime_keeps_existing_user_on_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin"
            runtime = root / "runtime"
            origin.mkdir()
            runtime.mkdir()
            (origin / "RenpyThief.exe").write_bytes(b"v1")
            prepare_bundled_runtime(origin, runtime)
            (runtime / "user").write_bytes(b"local-marker")
            (origin / "RenpyThief.exe").write_bytes(b"v2-changed")
            launched = prepare_bundled_runtime(origin, runtime)
            self.assertEqual(launched.read_bytes(), b"v2-changed")
            self.assertEqual((runtime / "user").read_bytes(), b"local-marker")

    def test_legacy_official_mode_uses_bundled_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            origin = Path(directory) / "origin"
            runtime = Path(directory) / "runtime"
            origin.mkdir()
            (origin / "RenpyThief.exe").write_bytes(b"bundled")
            selected = Path(directory) / "RenpyThief.exe"
            selected.write_bytes(b"official")
            settings = AppSettings(translator_path=str(selected), mode="official")
            settings.normalize()
            self.assertEqual(settings.mode, "custom")
            launched = resolve_launch_translator(
                settings, origin_dir=origin, runtime_dir=runtime
            )
            self.assertEqual(launched, (runtime / "RenpyThief.exe").resolve())

    def test_custom_mode_requires_origin_exe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(mode="custom")
            with self.assertRaises(FileNotFoundError):
                resolve_launch_translator(
                    settings,
                    origin_dir=Path(directory) / "missing",
                    runtime_dir=Path(directory) / "runtime",
                )


if __name__ == "__main__":
    unittest.main()
