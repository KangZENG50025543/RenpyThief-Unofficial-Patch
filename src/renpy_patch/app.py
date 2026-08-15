from __future__ import annotations

import ctypes
import sys

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow, center_window


_MUTEX_HANDLE: int | None = None


def _acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "RenpyThiefUnofficialPatch.Gui.v1")
    if not handle:
        return True
    _MUTEX_HANDLE = handle
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    smoke_test = "--smoke-test" in arguments
    qt_arguments = [argument for argument in arguments if argument != "--smoke-test"]
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    application = QApplication(qt_arguments)
    application.setApplicationName("RenpyThief 非官方翻译补丁")
    application.setOrganizationName("RenpyThiefUnofficialPatch")
    if not _acquire_single_instance():
        if smoke_test:
            sys.stderr.write("another patch window is already running\n")
            return 2
        QMessageBox.information(None, "补丁已经打开", "请使用已经打开的补丁窗口。")
        return 0
    window = MainWindow()
    center_window(window)
    window.show()
    if smoke_test:
        QTimer.singleShot(250, window.close)
    return application.exec_()
