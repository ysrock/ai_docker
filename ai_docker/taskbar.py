from __future__ import annotations

from typing import Any

import pythoncom
import win32con
import win32gui
from win32com.shell import shell

from .config import (
    TASKBAR_HIDE_EXCLUDED_PROCESSES,
    is_taskbar_hide_excluded_agent,
    is_taskbar_hide_excluded_process,
)

__all__ = [
    "TASKBAR_HIDE_EXCLUDED_PROCESSES",
    "TaskbarHider",
    "is_taskbar_hide_excluded_agent",
    "is_taskbar_hide_excluded_process",
]


def _has_taskbar_style(hwnd: int) -> bool:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if style & win32con.WS_EX_TOOLWINDOW:
        return False
    if style & win32con.WS_EX_APPWINDOW:
        return True
    return not (style & win32con.WS_EX_NOACTIVATE or win32gui.GetWindow(hwnd, win32con.GW_OWNER))


class TaskbarHider:
    """Manages hiding/restoring running windows on the Windows taskbar via ITaskbarList."""

    def __init__(self) -> None:
        self._tb: Any = None
        self._tray_hwnd: int = 0
        self._hidden_hwnds: set[int] = set()
        self._last_error: str = ""
        self._ensure_init()

    @property
    def available(self) -> bool:
        return self._ensure_init()

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def hidden_count(self) -> int:
        return len(self._hidden_hwnds)

    def _ensure_init(self) -> bool:
        current_tray = win32gui.FindWindow("Shell_TrayWnd", None)
        if not current_tray:
            self._tb = None
            self._tray_hwnd = 0
            self._last_error = "Shell_TrayWnd not found on current desktop"
            return False

        # Re-initialize automatically if explorer.exe restarted
        if self._tb is not None and current_tray == self._tray_hwnd:
            return True

        try:
            pythoncom.CoInitialize()
        except Exception:
            # Qt main thread may already have COM initialized; ignore benign status
            pass

        try:
            tb = pythoncom.CoCreateInstance(
                shell.CLSID_TaskbarList,
                None,
                pythoncom.CLSCTX_INPROC_SERVER,
                shell.IID_ITaskbarList,
            )
            tb.HrInit()
            self._tb = tb
            self._tray_hwnd = current_tray
            self._last_error = ""
            return True
        except Exception as exc:
            self._tb = None
            self._tray_hwnd = 0
            self._last_error = f"ITaskbarList init failed: {exc}"
            return False

    def hide_window(self, hwnd: int) -> bool:
        if not self._ensure_init() or not win32gui.IsWindow(hwnd):
            return False
        try:
            if not _has_taskbar_style(hwnd):
                self._hidden_hwnds.discard(hwnd)
                return False
            self._tb.DeleteTab(hwnd)
            self._hidden_hwnds.add(hwnd)
            return True
        except Exception as exc:
            self._last_error = f"DeleteTab({hwnd}) failed: {exc}"
            return False

    def show_window(self, hwnd: int) -> bool:
        if hwnd not in self._hidden_hwnds:
            return False
        if not win32gui.IsWindow(hwnd):
            self._hidden_hwnds.discard(hwnd)
            return False
        if not self._ensure_init():
            return False
        try:
            if not _has_taskbar_style(hwnd):
                self._hidden_hwnds.discard(hwnd)
                return False
            self._tb.AddTab(hwnd)
            self._hidden_hwnds.discard(hwnd)
            return True
        except Exception as exc:
            self._last_error = f"AddTab({hwnd}) failed: {exc}"
            return False

    def sync_hidden_windows(self, target_hwnds: set[int]) -> None:
        """Keep exactly `target_hwnds` hidden from the Windows taskbar."""
        if not self._ensure_init():
            return

        # 1. Remove closed windows from tracking set
        stale = {h for h in self._hidden_hwnds if not win32gui.IsWindow(h)}
        self._hidden_hwnds.difference_update(stale)

        # 2. Restore any previously hidden window that no longer matches
        to_restore = self._hidden_hwnds - target_hwnds
        for hwnd in list(to_restore):
            self.show_window(hwnd)

        # 3. Hide all matching windows (idempotent; suppresses taskbar re-appearances)
        for hwnd in target_hwnds:
            self.hide_window(hwnd)

    def restore_all(self) -> int:
        """Restore every window hidden by this manager back to the Windows taskbar."""
        restored = 0
        for hwnd in list(self._hidden_hwnds):
            if self.show_window(hwnd):
                restored += 1
        return restored

    def close(self) -> None:
        self.restore_all()
        self._tb = None
        self._tray_hwnd = 0
