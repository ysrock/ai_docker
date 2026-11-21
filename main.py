from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ai_docker.config import ConfigLoadError, PROJECT_ROOT
from ai_docker.dock_window import AgentDockWindow

ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
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


def main() -> int:
    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        return 0

    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
        app.setApplicationName("AI docker")
        ico_path = PROJECT_ROOT / "assets" / "ai_docker.ico"
        if ico_path.exists():
            app.setWindowIcon(QIcon(str(ico_path)))
        app.setQuitOnLastWindowClosed(True)

        try:
            dock = AgentDockWindow()
        except ConfigLoadError as exc:
            QMessageBox.critical(None, "AI docker 配置加载失败", f"无法读取 agents.json：\n{exc}")
            return 1
        dock.show()
        return app.exec()
    finally:
        _kernel32.CloseHandle(wintypes.HANDLE(mutex))


if __name__ == "__main__":
    raise SystemExit(main())
