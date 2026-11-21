from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Any

import win32con
import win32gui
import win32process

from .config import PROJECT_ROOT

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_CLOAKED = 14
OWN_PID = os.getpid()

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


def _get_process_basename(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf_len = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(buf_len.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_len)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _is_window_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    res = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        wintypes.DWORD(DWMWA_CLOAKED),
        ctypes.byref(cloaked),
        ctypes.sizeof(cloaked),
    )
    return res == 0 and cloaked.value != 0


def list_visible_windows() -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    pid_cache: dict[int, str] = {}

    def enum_cb(hwnd: int, _extra: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex_style & win32con.WS_EX_TOOLWINDOW:
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if not title.strip():
            return True
        if _is_window_cloaked(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == OWN_PID:
            return True
        proc_name = pid_cache.get(pid)
        if proc_name is None:
            proc_name = _get_process_basename(pid)
            pid_cache[pid] = proc_name
        windows.append(
            {
                "hwnd": hwnd,
                "pid": pid,
                "process": proc_name,
                "title": title,
            }
        )
        return True

    win32gui.EnumWindows(enum_cb, None)
    return windows


_last_focused_by_agent: dict[str, int] = {}

_LEADING_BADGE_RE = re.compile(r"^\s*(?:[\(\[【]\s*\d+\s*[\)\]】]|[●•\*])\s*")
_INVISIBLE_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_MEANINGFUL_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_SUFFIX_NOISE_RE = re.compile(r"\s*(?:\(Workspace\)|\[SSH:[^\]]*\]|\[WSL:[^\]]*\]|\[Dev Container[^\]]*\])\s*$", re.IGNORECASE)
_APP_SUFFIX_NAMES = {
    "cursor",
    "antigravity",
    "antigravity ide",
    "visual studio code",
    "claude",
    "claude desktop",
    "codex",
    "chatgpt",
    "grok",
    "grok bot",
    "grok / x",
    "workbuddy",
    "google chrome",
    "microsoft edge",
    "windows terminal",
    "powershell",
    "pwsh",
}


def get_foreground_hwnd() -> int:
    try:
        return int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        return 0


def extract_project_name(agent: dict[str, Any], raw_title: str) -> str:
    """Extract the working project / workspace / conversation name from a window title."""
    fallback = (agent.get("name") or "窗口").strip()
    title = _INVISIBLE_CHARS_RE.sub("", raw_title or "").strip()
    if not title:
        return fallback

    # Strip leading unread counters or dirty-file markers
    while True:
        cleaned = _LEADING_BADGE_RE.sub("", title)
        if cleaned == title:
            break
        title = cleaned.strip()

    if not title or not _MEANINGFUL_RE.search(title):
        return fallback

    # Normalize known separators (ASCII dash, em-dash, en-dash, vertical bar)
    parts = [
        p.strip()
        for p in re.split(r"\s+[-—–|]\s+", title)
        if p.strip() and _MEANINGFUL_RE.search(p)
    ]

    # Build app-name set to strip from start/end
    agent_names = set(_APP_SUFFIX_NAMES)
    if fallback:
        agent_names.add(fallback.lower())
    proc_stem = os.path.splitext(agent.get("match_process") or "")[0].strip().lower()
    if proc_stem:
        agent_names.add(proc_stem)

    # Remove trailing and leading pure app-name segments
    while len(parts) > 1 and parts[-1].lower() in agent_names:
        parts.pop()
    while len(parts) > 1 and parts[0].lower() in agent_names:
        parts.pop(0)

    if not parts:
        return fallback

    # For VS Code / Cursor / Antigravity style: "file.py - project_name - Cursor"
    # After stripping "Cursor", the last remaining segment is the workspace/project name.
    candidate = parts[-1]
    candidate = _SUFFIX_NOISE_RE.sub("", candidate).strip()

    if not candidate or candidate.lower() in agent_names or not _MEANINGFUL_RE.search(candidate):
        return fallback

    # Only extract basename when candidate is an actual filesystem path (e.g. C:\workspace\my_proj)
    if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/|~/)", candidate):
        base = os.path.basename(candidate.rstrip("\\/"))
        if base:
            candidate = base

    if not candidate or candidate.lower() in agent_names or not _MEANINGFUL_RE.search(candidate):
        return fallback
    return candidate


def match_agent_windows(agent: dict[str, Any], windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match_process = (agent.get("match_process") or "").strip().lower()
    match_title = (agent.get("match_title") or "").strip().lower()
    if not match_process and not match_title:
        return []

    matched: list[dict[str, Any]] = []
    for w in windows:
        proc = w["process"].lower()
        title = w["title"].lower()
        if match_process and proc != match_process:
            continue
        if match_title and match_title not in title:
            continue
        entry = dict(w)
        entry["project"] = extract_project_name(agent, w["title"])
        matched.append(entry)
    matched.sort(key=lambda item: item["hwnd"])
    return matched


def match_agent_window(agent: dict[str, Any], windows: list[dict[str, Any]]) -> int | None:
    matched = match_agent_windows(agent, windows)
    return matched[0]["hwnd"] if matched else None


def get_agent_window_map(agents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    windows = list_visible_windows()
    mapping: dict[str, list[dict[str, Any]]] = {}
    for agent in agents:
        if agent.get("enabled", True) is False:
            continue
        aid = agent.get("id")
        if not aid:
            continue
        matched = match_agent_windows(agent, windows)
        if matched:
            mapping[aid] = matched
    return mapping


def get_running_agent_ids(agents: list[dict[str, Any]]) -> set[str]:
    return set(get_agent_window_map(agents).keys())


def focus_hwnd(hwnd: int) -> bool:
    if not win32gui.IsWindow(hwnd):
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
        target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
        cur_tid = kernel32.GetCurrentThreadId()

        attached_fg = False
        attached_target = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
            if target_tid and target_tid != cur_tid:
                attached_target = bool(user32.AttachThreadInput(cur_tid, target_tid, True))

            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            # Windows foreground lock fallback: pulse TOPMOST -> NOTOPMOST to raise window
            flags = (
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_SHOWWINDOW
            )
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            win32gui.BringWindowToTop(hwnd)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return True
        finally:
            if attached_target:
                user32.AttachThreadInput(cur_tid, target_tid, False)
            if attached_fg:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
    except Exception:
        return False


def _find_browser(preferred: str | None = None) -> str | None:
    if preferred and Path(preferred).exists():
        return preferred
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


_VSCODE_NEW_WINDOW_EXES = {
    "cursor.exe",
    "code.exe",
    "windsurf.exe",
    "trae.exe",
    "antigravity ide.exe",
    "kiro.exe",
}


def _build_exe_launch_args(agent: dict[str, Any], force_new: bool) -> list[str]:
    args = [str(a) for a in agent.get("args", [])]
    if not force_new:
        return args
    extra = agent.get("new_instance_args")
    if isinstance(extra, list):
        return [*args, *(str(a) for a in extra)]
    exe_name = os.path.basename(agent.get("path", "")).lower()
    if exe_name in _VSCODE_NEW_WINDOW_EXES and "--new-window" not in args and "-n" not in args:
        return [*args, "--new-window"]
    return args


def launch_agent(agent: dict[str, Any], focus_existing: bool = True) -> tuple[bool, str]:
    """Focus existing window(s) (cycling when multiple) if enabled and found, otherwise launch."""
    if focus_existing:
        windows = list_visible_windows()
        matched = match_agent_windows(agent, windows)
        if matched:
            hwnds = [w["hwnd"] for w in matched]
            aid = agent.get("id") or agent.get("name") or ""
            target_hwnd = hwnds[0]
            if len(hwnds) > 1:
                fg_hwnd = win32gui.GetForegroundWindow()
                last_hwnd = _last_focused_by_agent.get(aid)
                anchor = fg_hwnd if fg_hwnd in hwnds else last_hwnd
                if anchor in hwnds and not win32gui.IsIconic(anchor):
                    idx = hwnds.index(anchor)
                    target_hwnd = hwnds[(idx + 1) % len(hwnds)]
                elif anchor in hwnds:
                    target_hwnd = anchor
            if focus_hwnd(target_hwnd):
                _last_focused_by_agent[aid] = target_hwnd
                return True, f"focused:{agent.get('name', '')}"

    force_new = not focus_existing
    atype = agent.get("type", "exe")
    creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        if atype == "exe":
            path = agent.get("path", "")
            if not path or not Path(path).exists():
                return False, f"找不到程序路径: {path}"
            args = _build_exe_launch_args(agent, force_new=force_new)
            cwd = agent.get("cwd") or str(Path(path).parent)
            subprocess.Popen([path, *args], cwd=cwd, creationflags=creationflags)
            return True, f"launched_exe:{path}"

        if atype == "msix":
            aumid = agent.get("aumid", "")
            if not aumid:
                return False, "缺少 MSIX AUMID"
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
            return True, f"launched_msix:{aumid}"

        if atype == "cli":
            command = agent.get("command", "")
            if not command:
                return False, "缺少 CLI 启动命令"
            cwd = agent.get("cwd") or str(Path.home())
            if not Path(cwd).exists():
                cwd = str(Path.home())
            title = agent.get("terminal_title") or agent.get("name") or "Agent CLI"
            wt = shutil.which("wt")
            pwsh = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
            if wt:
                cmd_list = [
                    wt,
                    "-w",
                    "new",
                    "new-tab",
                    "--title",
                    title,
                    "--suppressApplicationTitle",
                    "-d",
                    cwd,
                    pwsh,
                    "-NoLogo",
                    "-NoExit",
                    "-Command",
                    command,
                ]
                subprocess.Popen(cmd_list, cwd=cwd)
            else:
                subprocess.Popen(
                    [pwsh, "-NoLogo", "-NoExit", "-Command", command],
                    cwd=cwd,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            return True, f"launched_cli:{command}"

        if atype == "web":
            url = agent.get("url", "")
            if not url:
                return False, "缺少网页 URL"
            if agent.get("app_mode", True):
                browser = _find_browser(agent.get("browser"))
                if browser:
                    web_cmd = [browser, "--new-window", f"--app={url}"] if force_new else [browser, f"--app={url}"]
                    subprocess.Popen(web_cmd, creationflags=creationflags)
                    return True, f"launched_web_app:{url}"
            webbrowser.open(url)
            return True, f"launched_web:{url}"
    except OSError as exc:
        return False, f"进程启动失败: {exc}"

    return False, f"未知类型: {atype}"


def get_startup_shortcut_path() -> Path:
    startup_dir = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )
    return startup_dir / "AI docker.lnk"


def is_autostart_enabled() -> bool:
    return get_startup_shortcut_path().exists()


def set_autostart(enabled: bool) -> bool:
    lnk_path = get_startup_shortcut_path()
    if not enabled:
        if lnk_path.exists():
            lnk_path.unlink()
        return True

    import win32com.client

    exe_launcher = PROJECT_ROOT / "AI docker.exe"
    ico_path = PROJECT_ROOT / "assets" / "ai_docker.ico"

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(lnk_path))
    if exe_launcher.exists():
        shortcut.Targetpath = str(exe_launcher)
        shortcut.Arguments = ""
        shortcut.IconLocation = f"{exe_launcher},0"
    else:
        pythonw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
        target = str(pythonw) if pythonw.exists() else (shutil.which("pythonw") or "pythonw.exe")
        script = str(PROJECT_ROOT / "start_dock.pyw")
        shortcut.Targetpath = target
        shortcut.Arguments = f'"{script}"'
        if ico_path.exists():
            shortcut.IconLocation = f"{ico_path},0"
    shortcut.WorkingDirectory = str(PROJECT_ROOT)
    shortcut.Description = "AI docker - AI Agent Floating Dock"
    shortcut.save()
    return True
