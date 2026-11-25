from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import time
import webbrowser
from collections import OrderedDict
from ctypes import wintypes
from pathlib import Path
from queue import Full, Queue
from threading import Lock, Thread
from typing import Any, Callable, Sequence

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


def _get_process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf_len = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(buf_len.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_len)):
            return buf.value
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
        process_path = pid_cache.get(pid)
        if process_path is None:
            process_path = _get_process_path(pid)
            pid_cache[pid] = process_path
        windows.append(
            {
                "hwnd": hwnd,
                "pid": pid,
                "process": os.path.basename(process_path),
                "process_path": process_path,
                "title": title,
            }
        )
        return True

    win32gui.EnumWindows(enum_cb, None)
    return windows


_last_focused_by_agent: dict[str, int] = {}


def get_last_focused_hwnd(agent_id: str) -> int | None:
    """Return the most recently focused window HWND recorded for an agent."""
    return _last_focused_by_agent.get(agent_id)


def select_target_window(
    hwnds: Sequence[int],
    *,
    foreground_hwnd: int = 0,
    last_focused_hwnd: int | None = None,
    preferred_hwnds: Sequence[int] | None = None,
    is_iconic: Callable[[int], bool] | None = None,
) -> int:
    """Select which open window to focus: newest alerting window first, else cycle."""
    if not hwnds:
        raise ValueError("hwnds must not be empty")
    iconic = is_iconic if is_iconic is not None else (lambda h: bool(win32gui.IsIconic(h)))

    if preferred_hwnds:
        hwnd_set = set(hwnds)
        for pref in preferred_hwnds:
            if pref in hwnd_set and (pref != foreground_hwnd or iconic(pref)):
                return pref

    if len(hwnds) == 1:
        return hwnds[0]

    anchor = foreground_hwnd if foreground_hwnd in hwnds else last_focused_hwnd
    if anchor in hwnds and not iconic(anchor):
        idx = hwnds.index(anchor)
        return hwnds[(idx + 1) % len(hwnds)]
    if anchor in hwnds:
        return anchor
    return hwnds[0]

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


class _ExecutablePathCache:
    """Resolve filesystem aliases without blocking the GUI or holding up process exit."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._results: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._requests: Queue[str] = Queue(maxsize=128)
        self._pending: set[str] = set()
        self._started = False

    def get(self, path: str) -> str | None:
        with self._lock:
            cached = self._results.get(path)
            if cached is not None and cached[0] > time.monotonic():
                self._results.move_to_end(path)
                return cached[1]
            if path not in self._pending:
                try:
                    self._requests.put_nowait(path)
                except Full:
                    return None
                self._pending.add(path)
                if not self._started:
                    self._started = True
                    for _ in range(2):
                        Thread(target=self._resolve, daemon=True).start()
            return cached[1] if cached is not None else None

    def _resolve(self) -> None:
        while True:
            path = self._requests.get()
            try:
                resolved = os.path.normcase(os.path.realpath(path))
            except (OSError, ValueError):
                resolved = path
            with self._lock:
                self._pending.remove(path)
                self._results[path] = (time.monotonic() + 60, resolved)
                self._results.move_to_end(path)
                if len(self._results) > 256:
                    self._results.popitem(last=False)


_executable_paths = _ExecutablePathCache()
_SQUIRREL_VERSION = re.compile(r"app-\d+(?:\.\d+){2,3}(?:[-+][a-z0-9][a-z0-9.+-]*)?", re.ASCII)


def _executable_path_match(exe_path: str, process_path: str) -> int:
    """Rank exact paths above Squirrel launchers and reject unrelated installs."""
    if not process_path:
        return 0
    expected = os.path.normcase(os.path.normpath(exe_path))
    actual = os.path.normcase(os.path.normpath(process_path))
    if actual == expected:
        return 2
    exp_dir, exp_name = os.path.split(expected)
    act_dir, act_name = os.path.split(actual)
    parent_dir, sub_dir = os.path.split(act_dir)
    versioned = act_name == exp_name and _SQUIRREL_VERSION.fullmatch(sub_dir) is not None
    if versioned and parent_dir == exp_dir:
        return 1
    canonical_expected = _executable_paths.get(expected)
    canonical_actual = _executable_paths.get(actual)
    if canonical_expected is None or canonical_actual is None:
        return 0
    if canonical_expected == canonical_actual:
        return 2
    if versioned and os.path.dirname(os.path.dirname(canonical_actual)) == os.path.dirname(canonical_expected):
        return 1
    return 0


def match_agent_windows(agent: dict[str, Any], windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry, _ in _ranked_agent_windows(agent, windows)]


def _ranked_agent_windows(
    agent: dict[str, Any], windows: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], int]]:
    match_process = (agent.get("match_process") or "").strip().lower()
    match_title = (agent.get("match_title") or "").strip().lower()
    if not match_process and not match_title:
        return []

    # Discovered and manually added executables share this config shape, including old entries.
    exe_path = agent.get("path") or ""
    use_match_path = (
        agent.get("type", "exe") == "exe"
        and agent.get("match_path", True) is not False
        and os.path.isabs(exe_path)
        and os.path.basename(exe_path).lower() == match_process
    )

    matched: list[tuple[dict[str, Any], int]] = []
    for w in windows:
        proc = w["process"].lower()
        title = w["title"].lower()
        if match_process and proc != match_process:
            continue
        if match_title and match_title not in title:
            continue
        rank = _executable_path_match(exe_path, w.get("process_path") or "") if use_match_path else 0
        if use_match_path and not rank:
            continue
        entry = dict(w)
        entry["project"] = extract_project_name(agent, w["title"])
        matched.append((entry, rank))
    matched.sort(key=lambda item: item[0]["hwnd"])
    return matched


def match_agent_window(agent: dict[str, Any], windows: list[dict[str, Any]]) -> int | None:
    matched = match_agent_windows(agent, windows)
    return matched[0]["hwnd"] if matched else None


def get_agent_window_map(agents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    windows = list_visible_windows()
    ranked: dict[str, list[tuple[dict[str, Any], int]]] = {}
    exact_hwnds: set[int] = set()
    for agent in agents:
        if agent.get("enabled", True) is False:
            continue
        aid = agent.get("id")
        if not aid:
            continue
        matched = _ranked_agent_windows(agent, windows)
        if matched:
            ranked[aid] = matched
            exact_hwnds.update(entry["hwnd"] for entry, rank in matched if rank == 2)
    mapping = {}
    for aid, matched in ranked.items():
        selected = [entry for entry, rank in matched if rank != 1 or entry["hwnd"] not in exact_hwnds]
        if selected:
            mapping[aid] = selected
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


def close_hwnd(hwnd: int) -> bool:
    """Politely ask a top-level window to close via asynchronous WM_CLOSE."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return True
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
    args = [str(a) for a in agent.get("args") or []]
    if not force_new:
        return args
    extra = agent.get("new_instance_args")
    if isinstance(extra, list):
        return [*args, *(str(a) for a in extra)]
    exe_name = os.path.basename(agent.get("path", "")).lower()
    if exe_name in _VSCODE_NEW_WINDOW_EXES and "--new-window" not in args and "-n" not in args:
        return [*args, "--new-window"]
    return args


def launch_agent(
    agent: dict[str, Any],
    focus_existing: bool = True,
    *,
    agents: list[dict[str, Any]] | None = None,
    preferred_hwnds: Sequence[int] | None = None,
) -> tuple[bool, str]:
    """Focus existing window(s) (preferring alerting windows, else cycling) if enabled, otherwise launch."""
    if focus_existing:
        if agents is None or not agent.get("id"):
            matched = match_agent_windows(agent, list_visible_windows())
        else:
            matched = get_agent_window_map(agents).get(agent.get("id", ""), [])
        if matched:
            hwnds = [w["hwnd"] for w in matched]
            aid = agent.get("id") or agent.get("name") or ""
            target_hwnd = select_target_window(
                hwnds,
                foreground_hwnd=win32gui.GetForegroundWindow(),
                last_focused_hwnd=_last_focused_by_agent.get(aid),
                preferred_hwnds=preferred_hwnds,
            )
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
