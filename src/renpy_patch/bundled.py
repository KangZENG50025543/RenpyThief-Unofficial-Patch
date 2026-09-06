"""Pristine bundled RenpyThief 6.7.8 and the writable runtime clone."""

from __future__ import annotations

import shutil
from pathlib import Path

from .models import AppSettings
from .settings import app_data_directory, application_directory

ORIGIN_DIR_NAME = "6.7.8Origin"
RUNTIME_DIR_NAME = "6.7.8Runtime"
TRANSLATOR_EXE_NAME = "RenpyThief.exe"
_STAMP_NAME = ".origin_stamp"

_POLLUTION_NAMES = frozenset(
    {
        "user",
        "hwid",
        "settings.ini",
        "settings.json",
        "api.txt",
        "api_siliconflow.txt",
    }
)
_POLLUTION_SUFFIXES = (".log", ".dmp", ".pdb")


def bundled_origin_directory(app_dir: Path | None = None) -> Path:
    return (app_dir or application_directory()) / ORIGIN_DIR_NAME


def bundled_origin_exe(app_dir: Path | None = None) -> Path:
    return bundled_origin_directory(app_dir) / TRANSLATOR_EXE_NAME


def bundled_runtime_directory(data_dir: Path | None = None) -> Path:
    return (data_dir or app_data_directory()) / RUNTIME_DIR_NAME


def bundled_origin_is_present(app_dir: Path | None = None) -> bool:
    exe = bundled_origin_exe(app_dir)
    return exe.is_file() and exe.name.casefold() == TRANSLATOR_EXE_NAME.casefold()


def _is_pollution(relative: Path) -> bool:
    for part in relative.parts:
        name = part.casefold()
        if name in _POLLUTION_NAMES:
            return True
        if name.endswith(_POLLUTION_SUFFIXES):
            return True
        if "api" in name and name.endswith(".txt"):
            return True
    return False


def _origin_stamp(origin_exe: Path) -> str:
    stats = origin_exe.stat()
    return f"{stats.st_size}:{int(stats.st_mtime)}"


def _copy_origin_tree(origin_dir: Path, runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for source in origin_dir.rglob("*"):
        relative = source.relative_to(origin_dir)
        if _is_pollution(relative):
            continue
        destination = runtime_dir / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if not source.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (
            destination.is_file()
            and destination.stat().st_size == source.stat().st_size
            and int(destination.stat().st_mtime) >= int(source.stat().st_mtime)
        ):
            continue
        shutil.copy2(source, destination)


def prepare_bundled_runtime(
    origin_dir: Path | None = None,
    runtime_dir: Path | None = None,
) -> Path:
    origin = origin_dir or bundled_origin_directory()
    origin_exe = origin / TRANSLATOR_EXE_NAME
    if not origin_exe.is_file():
        raise FileNotFoundError(
            "找不到内置的干净 RenpyThief 6.7.8。"
            "请确认补丁目录下存在 6.7.8Origin\\RenpyThief.exe。"
        )
    runtime = runtime_dir or bundled_runtime_directory()
    runtime_exe = runtime / TRANSLATOR_EXE_NAME
    stamp_path = runtime / _STAMP_NAME
    stamp = _origin_stamp(origin_exe)
    if (
        not runtime_exe.is_file()
        or not stamp_path.is_file()
        or stamp_path.read_text(encoding="utf-8").strip() != stamp
    ):
        _copy_origin_tree(origin, runtime)
        runtime.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(stamp + "\n", encoding="utf-8")
    if not runtime_exe.is_file():
        raise FileNotFoundError("内置 6.7.8 工作副本未能创建 RenpyThief.exe。")
    return runtime_exe.resolve()


def resolve_launch_translator(
    settings: AppSettings,
    *,
    origin_dir: Path | None = None,
    runtime_dir: Path | None = None,
) -> Path:
    # The launcher no longer starts official-quota mode. Always clone the
    # bundled 6.7.8 origin into the writable runtime, even if an older
    # settings.json still says mode=official.
    settings.normalize()
    return prepare_bundled_runtime(origin_dir, runtime_dir)
