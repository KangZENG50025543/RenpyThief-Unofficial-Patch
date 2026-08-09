from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .models import AppSettings


APP_DIRECTORY_NAME = "RenpyThiefUnofficialPatch"


def app_data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_DIRECTORY_NAME


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def find_default_translator() -> str:
    app_dir = application_directory()
    roots = (app_dir, *tuple(app_dir.parents)[:4])
    candidates = tuple(root / "RenpyThief.exe" for root in roots)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


def find_router_script() -> Path | None:
    app_dir = application_directory()
    candidate = app_dir / "router" / "start_routed_translator.ps1"
    if candidate.is_file():
        return candidate.resolve()
    return None


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_directory() / "settings.json"

    def load(self) -> AppSettings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            settings = AppSettings.from_dict(data)
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError):
            settings = AppSettings()
        if not settings.translator_path:
            settings.translator_path = find_default_translator()
        return settings

    def save(self, settings: AppSettings) -> None:
        settings.normalize()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="settings-", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
