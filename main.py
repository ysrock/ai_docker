from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ai_docker.config import ConfigLoadError, PROJECT_ROOT
from ai_docker.dock_window import AgentDockWindow

SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
_INSTALL_HASH = hashlib.sha256(
    os.path.normcase(os.path.abspath(str(PROJECT_ROOT))).encode("utf-8")
).hexdigest()
INSTANCE_MUTEX_NAME = f"Local\\AI_docker_Instance_{_INSTALL_HASH}"
UPDATE_MUTEX_NAME = f"Local\\AI_docker_Update_{_INSTALL_HASH}"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def _acquire_single_instance_mutex(
    name: str = "Local\\AI_docker_FloatingDock_Mutex",
) -> int | None:
    handle = _kernel32.CreateMutexW(None, False, name)
    last_err = ctypes.get_last_error()
    if not handle:
        return None
    if last_err == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _is_update_in_progress(name: str = UPDATE_MUTEX_NAME) -> bool:
    handle = _kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == ERROR_ACCESS_DENIED


def main() -> int:
    install_mutex = _acquire_single_instance_mutex(INSTANCE_MUTEX_NAME)
    if install_mutex is None:
        return 0
    mutex: int | None = None
    try:
        if _is_update_in_progress():
            return 0
        mutex = _acquire_single_instance_mutex()
        if mutex is None:
            return 0

        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
        app.setApplicationName("AI docker")
        ico_path = PROJECT_ROOT / "assets" / "ai_docker.ico"
        if ico_path.exists():
            app.setWindowIcon(QIcon(str(ico_path)))
        # AgentDockWindow uses Qt.Tool (excluded from Qt's non-tool window count),
        # so quitOnLastWindowClosed=True would exit the entire app whenever any
        # QDialog or QMessageBox closes. AgentDockWindow.closeEvent/_quit_app handles exit.
        app.setQuitOnLastWindowClosed(False)

        try:
            dock = AgentDockWindow()
        except ConfigLoadError as exc:
            QMessageBox.critical(None, "AI docker 配置加载失败", f"无法读取 agents.json：\n{exc}")
            return 1
        dock.show()
        return app.exec()
    finally:
        if mutex is not None:
            _kernel32.CloseHandle(wintypes.HANDLE(mutex))
        _kernel32.CloseHandle(wintypes.HANDLE(install_mutex))


if __name__ == "__main__":
    raise SystemExit(main())
