from __future__ import annotations

import ctypes
import json
import locale
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from .models import AppSettings, TranslationMode
from .providers import is_loopback_base_url, make_launch_profile
from .settings import find_router_script


class LaunchEventKind(str, Enum):
    STARTING = "starting"
    READY = "ready"
    WARNING = "warning"
    STOPPING = "stopping"
    LOG = "log"
    EXITED = "exited"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LaunchEvent:
    kind: LaunchEventKind
    message: str
    translator_pid: int | None = None


EventCallback = Callable[[LaunchEvent], None]

_PID_PATTERN = re.compile(r"Started clean RenpyThief PID (\d+)\.")
_ROUTE_PATTERN = re.compile(r"Translator-wide route active:")
_GUARDED_PID_PATTERN = re.compile(r"^Started guarded RenpyThief PID (\d+)\.$")
_GUARDED_PID_HINT_PATTERN = re.compile(
    r"(?:^|\r?\n)Started guarded RenpyThief PID (\d+)\.(?:\r?\n|$)"
)
_GUARD_WARNING_PATTERN = re.compile(
    r"^WARNING: no known version check was observed within (\d+) ms; "
    r"continuing with update protection unconfirmed\.$"
)
_CUSTOM_GUARD_WARNING_PATTERN = re.compile(
    r"\bUPDATE_GUARD_WARNING:\s*timeout_ms=(\d+)\b"
)


def _block_updates(settings: AppSettings) -> bool:
    """Read the forward-compatible setting while GUI/settings land separately."""
    value = getattr(settings, "block_updates", True)
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off"}
    return bool(value)


_SECRET_ENVIRONMENT_PREFIXES = (
    "UPSTREAM_",
    "OPENAI_",
    "DEEPSEEK_",
    "SILICONFLOW_",
    "BAIDU_",
    "YOUDAO_",
    "MICROSOFT_TRANSLATOR_",
)
_SECRET_ENVIRONMENT_SUFFIXES = ("_API_KEY", "_SECRET", "_TOKEN", "_ACCESS_KEY")


def _is_blocked_child_environment_name(name: str) -> bool:
    upper = name.upper()
    if (
        upper.startswith("QT_")
        or upper.startswith("QTWEBENGINE")
        or upper == "QTDIR"
        or upper.startswith("QML")
        or upper == "BRIDGE_LOG_CONTENT"
        or upper.startswith(_SECRET_ENVIRONMENT_PREFIXES)
        or upper.endswith(_SECRET_ENVIRONMENT_SUFFIXES)
    ):
        return True
    return False


def _packaged_runtime_directories() -> tuple[str, ...]:
    directories: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        directories.append(os.path.abspath(meipass))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        directories.append(exe_dir)
        directories.append(os.path.join(exe_dir, "_internal"))
    unique: list[str] = []
    seen: set[str] = set()
    for directory in directories:
        key = os.path.normcase(directory)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return tuple(unique)


def _scrub_packaged_runtime_from_path(path_value: str) -> str:
    blocked = set(_packaged_runtime_directories())
    if not blocked:
        return path_value
    kept: list[str] = []
    for part in path_value.split(os.pathsep):
        if not part:
            continue
        try:
            resolved = os.path.normcase(os.path.abspath(part))
        except OSError:
            kept.append(part)
            continue
        if resolved not in blocked:
            kept.append(part)
    return os.pathsep.join(kept)


def _sanitized_translator_environment() -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in os.environ.items():
        if _is_blocked_child_environment_name(name):
            continue
        if name.upper() == "PATH":
            value = _scrub_packaged_runtime_from_path(value)
        result[name] = value
    return result


def _powershell_path() -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not candidate.is_file():
        raise FileNotFoundError("找不到 Windows PowerShell。")
    return candidate


def _has_existing_translator() -> bool:
    try:
        completed = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq RenpyThief.exe", "/NH"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = completed.stdout.decode(locale.getpreferredencoding(False), errors="replace")
    return "RenpyThief.exe".casefold() in output.casefold()


def build_custom_command(
    settings: AppSettings, router_script: Path | None = None
) -> list[str]:
    profile = make_launch_profile(settings)
    translator = Path(settings.translator_path).expanduser()
    if not translator.is_file() or translator.name.casefold() != "renpythief.exe":
        raise ValueError("请选择有效的 RenpyThief.exe。")
    script = router_script or find_router_script()
    if script is None or not script.is_file():
        raise FileNotFoundError("找不到补丁路由组件 start_routed_translator.ps1。")

    bridge_mode = (
        profile.payload_profile
        if profile.payload_profile in {"youdao", "baidu", "microsoft"}
        else "openai"
    )
    bridge_payload_profile = (
        profile.payload_profile if bridge_mode == "openai" else "openai"
    )

    command = [
        str(_powershell_path()),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Mode",
        bridge_mode,
        "-BaseUrl",
        profile.base_url,
        "-Model",
        profile.model,
        "-PayloadProfile",
        bridge_payload_profile,
        "-Thinking",
        profile.thinking,
        "-ReasoningEffort",
        profile.reasoning_effort,
        "-BridgeConcurrency",
        str(settings.bridge_concurrency),
        "-UpstreamConcurrency",
        str(settings.upstream_concurrency),
        "-CacheEntries",
        str(settings.cache_entries),
        "-CacheBytes",
        str(settings.cache_mebibytes * 1024 * 1024),
        "-TranslatorPath",
        str(translator.resolve()),
        "-BlockUpdates",
        "true" if _block_updates(settings) else "false",
    ]
    packaged_bridge = script.parent / "translate_bridge.exe"
    if packaged_bridge.is_file():
        command.extend(("-BridgeExecutable", str(packaged_bridge.resolve())))
    return command


def _custom_bridge_environment(
    settings: AppSettings,
    credentials: str | Mapping[str, str] | None,
) -> dict[str, str]:
    """Build the bridge-only environment without putting secrets on argv."""

    if isinstance(credentials, str):
        credential_values = {"api_key": credentials.strip()}
    else:
        credential_values = {
            str(name): str(value).strip()
            for name, value in (credentials or {}).items()
            if isinstance(name, str) and isinstance(value, str)
        }
    profile = make_launch_profile(settings)
    dedicated_fields = {
        "youdao": ("app_key", "app_secret"),
        "baidu": ("app_id", "app_secret"),
        "microsoft": ("subscription_key",),
    }
    required_fields = dedicated_fields.get(profile.payload_profile, ("api_key",))
    if required_fields == ("api_key",) and is_loopback_base_url(profile.base_url):
        required_fields = ()
    if any(not credential_values.get(field, "") for field in required_fields):
        raise ValueError("自定义翻译模式缺少必要凭据。")
    if any(
        "\0" in value or any(character.isspace() for character in value)
        for value in credential_values.values()
    ):
        raise ValueError("凭据字段不能包含空格、换行或 NUL。")

    prompt_mode = str(getattr(settings, "prompt_mode", "template1"))
    custom_prompt = str(getattr(settings, "custom_prompt", ""))
    if profile.payload_profile not in dedicated_fields:
        if prompt_mode not in {"template1", "template2", "custom"}:
            raise ValueError("提示词模式无效。")
        if len(custom_prompt) > 16_384 or "\0" in custom_prompt:
            raise ValueError("自定义提示词过长或包含 NUL。")
        if prompt_mode == "custom" and not custom_prompt.strip():
            raise ValueError("自定义提示词不能为空。")

    # Start from the same allowlist-oriented environment used for RenpyThief,
    # then add only the one selected provider's bridge-only values.
    environment = _sanitized_translator_environment()
    if profile.payload_profile in dedicated_fields:
        serialized = json.dumps(
            credential_values,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(serialized) > 16_384 or "\0" in serialized:
            raise ValueError("凭据数据过长或包含 NUL。")
        environment["UPSTREAM_CREDENTIALS_JSON"] = serialized
    else:
        api_key = credential_values.get("api_key", "")
        if api_key:
            environment["UPSTREAM_API_KEY"] = api_key
        elif not is_loopback_base_url(profile.base_url):
            raise ValueError("非本机 API 必须填写 API Key。")
        environment["UPSTREAM_PROMPT_MODE"] = prompt_mode
        if prompt_mode == "custom":
            environment["UPSTREAM_CUSTOM_PROMPT"] = custom_prompt
    return environment


class _WindowsPidProcess:
    """Small Popen-compatible owner for a process created by guardlaunch."""

    _SYNCHRONIZE = 0x00100000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _INFINITE = 0xFFFFFFFF

    def __init__(self, process_id: int) -> None:
        if os.name != "nt":
            raise OSError("Guarded launch is available only on Windows.")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        self._kernel32 = kernel32
        self.pid = process_id
        access = (
            self._SYNCHRONIZE
            | self._PROCESS_TERMINATE
            | self._PROCESS_QUERY_LIMITED_INFORMATION
        )
        handle = kernel32.OpenProcess(access, False, process_id)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Cannot track guarded RenpyThief")
        self._handle = handle

    def poll(self) -> int | None:
        code = ctypes.c_ulong()
        if not self._kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
        return None if code.value == self._STILL_ACTIVE else int(code.value)

    def wait(self, timeout: float | None = None) -> int:
        milliseconds = (
            self._INFINITE
            if timeout is None
            else max(0, min(0xFFFFFFFE, int(timeout * 1000)))
        )
        result = self._kernel32.WaitForSingleObject(self._handle, milliseconds)
        if result == self._WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        if result != self._WAIT_OBJECT_0:
            raise OSError(ctypes.get_last_error(), "WaitForSingleObject failed")
        code = self.poll()
        return self._STILL_ACTIVE if code is None else code

    def terminate(self) -> None:
        if self.poll() is not None:
            return
        if not self._kernel32.TerminateProcess(self._handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateProcess failed")

    def image_path(self) -> Path:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not self._kernel32.QueryFullProcessImageNameW(
            self._handle, 0, buffer, ctypes.byref(size)
        ):
            raise OSError(ctypes.get_last_error(), "Cannot identify guarded RenpyThief")
        return Path(buffer.value)

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._kernel32.CloseHandle(handle)
            self._handle = None

    def __del__(self) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class _GuardedLaunch:
    process: _WindowsPidProcess
    warning: str | None = None


def _launch_guarded_translator(
    translator: Path, router_script: Path | None = None
) -> _GuardedLaunch:
    script = router_script or find_router_script()
    if script is None or not script.is_file():
        raise FileNotFoundError("找不到补丁更新保护组件目录。")
    router_dir = script.resolve().parent
    helper = router_dir / "guardlaunch.exe"
    guard = router_dir / "versionguard.dll"
    guard_config = router_dir / "versionguard.ini"
    missing = [path.name for path in (helper, guard, guard_config) if not path.is_file()]
    if missing:
        raise FileNotFoundError("缺少更新保护组件：" + "、".join(missing))

    try:
        completed = subprocess.run(
            [str(helper), str(translator.resolve()), str(guard)],
            cwd=str(router_dir),
            env=_sanitized_translator_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
            timeout=50,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "更新保护启动器在总等待期限内没有返回；无法确认目标进程状态。"
        ) from error
    output = completed.stdout.decode("utf-8", errors="replace").strip()
    diagnostics = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        details = " ".join(part for part in (diagnostics, output) if part)
        raise RuntimeError(
            f"更新保护启动失败并已安全中止（代码 {completed.returncode}）：{details}"
        )
    process: _WindowsPidProcess | None = None
    try:
        pid_hint = _GUARDED_PID_HINT_PATTERN.search(output)
        if pid_hint:
            process = _WindowsPidProcess(int(pid_hint.group(1)))
            actual_path = os.path.normcase(str(process.image_path().resolve()))
            expected_path = os.path.normcase(str(translator.resolve()))
            if actual_path != expected_path:
                process.close()
                process = None
                raise RuntimeError("更新保护启动器返回的进程路径不匹配。")
        match = _GUARDED_PID_PATTERN.fullmatch(output)
        if not match or process is None:
            raise RuntimeError(f"更新保护启动器返回了无效结果：{output}")
        warning: str | None = None
        if diagnostics:
            warning_match = _GUARD_WARNING_PATTERN.fullmatch(diagnostics)
            if not warning_match:
                raise RuntimeError(f"更新保护启动器返回了未知诊断：{diagnostics}")
            timeout_seconds = int(warning_match.group(1)) / 1000
            warning = (
                f"更新保护钩子已就绪，但 {timeout_seconds:g} 秒内没有观察到已知版本检查；"
                "RenpyThief 将继续启动，更新保护状态尚未确认。"
            )
        return _GuardedLaunch(process, warning)
    except Exception:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                pass
            finally:
                process.close()
        raise


def _request_window_close(process_id: int) -> bool:
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    posted = False
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(window_handle: int, _parameter: int) -> bool:
        nonlocal posted
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window_handle, ctypes.byref(owner))
        if owner.value == process_id and user32.IsWindowVisible(window_handle):
            if user32.PostMessageW(window_handle, 0x0010, 0, 0):  # WM_CLOSE
                posted = True
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return posted


def _has_visible_window(process_id: int) -> bool:
    if os.name != "nt":
        return True
    user32 = ctypes.windll.user32
    found = False
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(window_handle: int, _parameter: int) -> bool:
        nonlocal found
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window_handle, ctypes.byref(owner))
        if owner.value == process_id and user32.IsWindowVisible(window_handle):
            found = True
            return False
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return found


class PatchLauncher:
    def __init__(self, callback: EventCallback) -> None:
        self._callback = callback
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | _WindowsPidProcess | None = None
        self._translator_pid: int | None = None
        self._mode: TranslationMode | None = None
        self._stop_requested = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def translator_pid(self) -> int | None:
        with self._lock:
            return self._translator_pid

    def _emit(
        self, kind: LaunchEventKind, message: str, translator_pid: int | None = None
    ) -> None:
        try:
            self._callback(LaunchEvent(kind, message, translator_pid))
        except Exception:
            return

    def start(
        self,
        settings: AppSettings,
        credentials: str | Mapping[str, str] | None = None,
    ) -> None:
        settings.normalize()
        mode = TranslationMode(settings.mode)
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("翻译器已经由本补丁启动。")
        if _has_existing_translator():
            raise RuntimeError("RenpyThief 已经在运行；请先正常关闭后再启动。")

        translator = Path(settings.translator_path).expanduser()
        if not translator.is_file() or translator.name.casefold() != "renpythief.exe":
            raise ValueError("请选择有效的 RenpyThief.exe。")

        self._emit(LaunchEventKind.STARTING, "正在启动……")
        if mode is TranslationMode.OFFICIAL:
            guard_warning: str | None = None
            if _block_updates(settings):
                guarded_launch = _launch_guarded_translator(translator)
                process = guarded_launch.process
                guard_warning = guarded_launch.warning
            else:
                process = subprocess.Popen(
                    [str(translator.resolve())],
                    cwd=str(translator.resolve().parent),
                    env=_sanitized_translator_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            with self._lock:
                self._process = process
                self._translator_pid = process.pid
                self._mode = mode
                self._stop_requested = False
            if guard_warning is not None:
                self._emit(LaunchEventKind.WARNING, guard_warning, process.pid)
            threading.Thread(
                target=self._monitor_official,
                args=(process,),
                name="official-process-monitor",
                daemon=True,
            ).start()
            return

        command = build_custom_command(settings)
        environment = _custom_bridge_environment(settings, credentials)
        process = subprocess.Popen(
            command,
            cwd=str(Path(command[5]).resolve().parent),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._lock:
            self._process = process
            self._translator_pid = None
            self._mode = mode
            self._stop_requested = False
        threading.Thread(
            target=self._monitor_custom,
            args=(process,),
            name="custom-process-monitor",
            daemon=True,
        ).start()

    def _monitor_official(
        self, process: subprocess.Popen[bytes] | _WindowsPidProcess
    ) -> None:
        window_deadline = time.monotonic() + 15.0
        while process.poll() is None and time.monotonic() < window_deadline:
            if _has_visible_window(process.pid):
                self._emit(
                    LaunchEventKind.READY,
                    "官方额度模式已启动；本地转发未启用。",
                    process.pid,
                )
                with self._lock:
                    should_stop = self._stop_requested
                if should_stop:
                    _request_window_close(process.pid)
                break
            time.sleep(0.1)
        else:
            with self._lock:
                requested = self._stop_requested
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            with self._lock:
                if self._process is process:
                    self._process = None
                    self._translator_pid = None
                    self._mode = None
            if requested:
                self._emit(LaunchEventKind.EXITED, "启动已取消，RenpyThief 已关闭。")
            else:
                self._emit(
                    LaunchEventKind.ERROR,
                    "RenpyThief 未能在 15 秒内显示可用窗口。",
                )
            return

        return_code = process.wait()
        with self._lock:
            requested = self._stop_requested
            if self._process is process:
                self._process = None
                self._translator_pid = None
                self._mode = None
        if requested or return_code == 0:
            self._emit(LaunchEventKind.EXITED, "RenpyThief 已关闭。")
        else:
            self._emit(
                LaunchEventKind.ERROR,
                f"RenpyThief 异常退出，代码 {return_code}。",
            )

    def _monitor_custom(self, process: subprocess.Popen[bytes]) -> None:
        ready = False
        assert process.stdout is not None
        for raw_line in iter(process.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            match = _PID_PATTERN.search(line)
            if match:
                pid = int(match.group(1))
                with self._lock:
                    self._translator_pid = pid
                    should_stop = self._stop_requested
                self._emit(LaunchEventKind.LOG, "RenpyThief 已启动。", pid)
                if should_stop:
                    _request_window_close(pid)
            guard_warning = _CUSTOM_GUARD_WARNING_PATTERN.search(line)
            if guard_warning:
                timeout_seconds = int(guard_warning.group(1)) / 1000
                self._emit(
                    LaunchEventKind.WARNING,
                    f"更新保护钩子已就绪，但 {timeout_seconds:g} 秒内没有观察到已知版本检查；"
                    "RenpyThief 将继续启动，更新保护状态尚未确认。",
                    self.translator_pid,
                )
            elif _ROUTE_PATTERN.search(line):
                ready = True
                self._emit(
                    LaunchEventKind.READY,
                    "自定义 API 路由已激活，可以拖入游戏。",
                    self.translator_pid,
                )
            elif "WARNING:" in line or "failed" in line.casefold() or "error" in line.casefold():
                self._emit(LaunchEventKind.LOG, line, self.translator_pid)

        return_code = process.wait()
        with self._lock:
            requested = self._stop_requested
            if self._process is process:
                self._process = None
                self._translator_pid = None
                self._mode = None
        if return_code == 0 or requested:
            self._emit(LaunchEventKind.EXITED, "翻译器与本地桥接已关闭。")
        elif not ready:
            self._emit(
                LaunchEventKind.ERROR,
                f"自定义路由启动失败，代码 {return_code}。请查看诊断日志。",
            )
        else:
            self._emit(
                LaunchEventKind.ERROR,
                f"翻译桥接异常退出，代码 {return_code}。",
            )

    def request_stop(self) -> None:
        with self._lock:
            process = self._process
            pid = self._translator_pid
            if process is None or process.poll() is not None:
                return
            self._stop_requested = True
        self._emit(LaunchEventKind.STOPPING, "正在请求 RenpyThief 正常关闭……", pid)
        if pid is None:
            return
        if not _request_window_close(pid) and self._mode is TranslationMode.CUSTOM:
            self._emit(
                LaunchEventKind.LOG,
                "未找到可关闭的 RenpyThief 窗口，请手动关闭它。",
                pid,
            )
