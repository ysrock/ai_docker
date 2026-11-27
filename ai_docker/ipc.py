from __future__ import annotations

import ctypes
import hashlib
import os
import time
from ctypes import wintypes

from .config import PROJECT_ROOT

INSTALL_PROPERTY = "AI docker install " + hashlib.sha256(
    os.path.normcase(str(PROJECT_ROOT)).encode("utf-8")
).hexdigest()

IPC_CONTROL_MESSAGE_NAME = "AI_docker_CLI_Control"
IPC_CMD_RELOAD = 1
IPC_CMD_EXPAND = 2
IPC_CMD_COLLAPSE = 3
IPC_CMD_CLEAR_NOTIFICATIONS = 4

WM_CLOSE = 0x0010
ERROR_ACCESS_DENIED = 5

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
_user32.GetPropW.restype = wintypes.HANDLE
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL


def find_dock_window(install_property: str = INSTALL_PROPERTY) -> tuple[int, int] | None:
    """Locate the running AI docker window for this installation via its Win32 window property."""
    found: list[tuple[int, int]] = []

    @_EnumWindowsProc
    def _cb(hwnd: int, _lparam: int) -> bool:
        if _user32.GetPropW(hwnd, install_property):
            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((int(hwnd), int(pid.value)))
            return False
        return True

    _user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def send_dock_command_status(
    cmd: int, install_property: str = INSTALL_PROPERTY
) -> tuple[bool, str, str]:
    """Post a control command to the running AI docker window and return detailed status."""
    target = find_dock_window(install_property)
    if target is None:
        return False, "dock_not_running", "未检测到正在运行的 AI docker 实例"
    hwnd, pid = target
    msg_id = int(_user32.RegisterWindowMessageW(IPC_CONTROL_MESSAGE_NAME))
    if not msg_id:
        return False, "ipc_failed", "注册 Win32 控制消息失败"
    ctypes.set_last_error(0)
    if _user32.PostMessageW(hwnd, msg_id, int(cmd), 0):
        return True, "ok", ""
    err = ctypes.get_last_error()
    if err == ERROR_ACCESS_DENIED:
        return (
            False,
            "permission_denied",
            f"无法向 AI docker (PID {pid}) 发送控制消息（可能因管理员权限/UIPI 隔离被系统拦截）",
        )
    return False, "ipc_failed", f"向 AI docker (PID {pid}) 发送控制消息失败（错误码 {err}）"


def send_dock_command(cmd: int, install_property: str = INSTALL_PROPERTY) -> bool:
    """Post a control command to the running AI docker window, if present."""
    ok, _, _ = send_dock_command_status(cmd, install_property)
    return ok


def close_dock_window(
    install_property: str = INSTALL_PROPERTY,
    timeout_sec: float = 6.0,
) -> tuple[bool, str]:
    """Gracefully close the running AI docker window via WM_CLOSE and wait for exit."""
    target = find_dock_window(install_property)
    if target is None:
        return False, "AI docker 当前未运行"
    hwnd, pid = target
    ctypes.set_last_error(0)
    if not _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
        err = ctypes.get_last_error()
        if err == ERROR_ACCESS_DENIED:
            return False, f"向 AI docker (PID {pid}) 发送关闭消息被权限/UIPI 拦截"
        return False, f"向 AI docker (PID {pid}) 发送关闭消息失败"

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _user32.IsWindow(hwnd):
            return True, f"已优雅关闭 AI docker (PID {pid})"
        time.sleep(0.1)
    return False, f"等待 AI docker (PID {pid}) 退出超时"
