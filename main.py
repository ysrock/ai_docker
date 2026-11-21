from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ai_doker.config import PROJECT_ROOT
from ai_doker.dock_window import AgentDockWindow

ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_mutex(name: str = "Local\\AI_Doker_FloatingDock_Mutex"):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    last_err = kernel32.GetLastError()
    if last_err == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def main() -> int:
    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        return 0

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("AI Doker")
    ico_path = PROJECT_ROOT / "assets" / "ai_doker.ico"
    if ico_path.exists():
        app.setWindowIcon(QIcon(str(ico_path)))
    app.setQuitOnLastWindowClosed(True)

    dock = AgentDockWindow()
    dock.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
