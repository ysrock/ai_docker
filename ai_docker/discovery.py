from __future__ import annotations

import ctypes
import os
import re
import shlex
import shutil
import sys
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Any

# Known AI Agent keyword catalog for auto-discovery
DESKTOP_KEYWORDS = (
    "cursor",
    "antigravity",
    "workbuddy",
    "codebuddy",
    "windsurf",
    "trae",
    "kiro",
    "claude",
    "codex",
    "chatgpt",
    "grok",
    "perplexity",
    "deepseek",
    "cherrystudio",
    "cherry studio",
    "anythingllm",
    "lm studio",
    "ollama",
    "doubao",
    "yuanbao",
    "kimi",
    "qoder",
    "zed",
    "visual studio code",
    "vs code",
)

SKIP_EXE_NAMES = (
    "uninstall",
    "unins000",
    "update",
    "updater",
    "installer",
    "setup",
    "helper",
    "crashpad",
    "squirrel",
    "repair",
)

CLI_CATALOG: list[dict[str, Any]] = [
    {
        "cmd": "claude",
        "name": "Claude Code",
        "icon_source": "Claude_pzs8sxrjxfjjc!Claude",
        "notification_keywords": ["claude"],
    },
    {
        "cmd": "codex",
        "name": "Codex CLI",
        "icon_source": "OpenAI.Codex_2p2nqsd0c76g0!App",
        "notification_keywords": ["codex"],
    },
    {
        "cmd": "gemini",
        "name": "Gemini CLI",
        "icon_source": "https://gemini.google.com",
        "notification_keywords": ["gemini"],
    },
    {
        "cmd": "opencode",
        "name": "OpenCode",
        "icon_source": "https://opencode.ai",
        "notification_keywords": ["opencode"],
    },
    {
        "cmd": "aider",
        "name": "Aider",
        "icon_source": "https://aider.chat",
        "notification_keywords": ["aider"],
    },
    {
        "cmd": "goose",
        "name": "Goose CLI",
        "icon_source": "https://block.github.io/goose",
        "notification_keywords": ["goose"],
    },
    {
        "cmd": "qoder",
        "name": "Qoder CLI",
        "icon_source": "",
        "notification_keywords": ["qoder"],
    },
    {
        "cmd": "kiro-cli",
        "name": "Kiro CLI",
        "icon_source": "https://kiro.dev",
        "notification_keywords": ["kiro"],
    },
]

MSIX_CATALOG: list[dict[str, Any]] = [
    {
        "prefix": "Claude_",
        "appid": "Claude",
        "name": "Claude",
        "match_process": "Claude.exe",
    },
    {
        "prefix": "OpenAI.Codex_",
        "appid": "App",
        "name": "Codex",
        "match_process": "ChatGPT.exe",
    },
    {
        "prefix": "OpenAI.ChatGPT-Desktop_",
        "appid": "ChatGPT",
        "name": "ChatGPT",
        "match_process": "ChatGPT Classic.exe",
    },
    {
        "prefix": "Perplexity",
        "appid": "App",
        "name": "Perplexity",
        "match_process": "Perplexity.exe",
    },
]


def slugify_id(name: str, existing_ids: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"
    if base not in existing_ids:
        return base
    idx = 2
    while f"{base}-{idx}" in existing_ids:
        idx += 1
    return f"{base}-{idx}"


def _norm_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(p.strip())) if p else ""


def _is_skipped_exe(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in SKIP_EXE_NAMES)


def _split_windows_args(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if sys.platform == "win32":
        try:
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32
            shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
            shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
            kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
            kernel32.LocalFree.restype = wintypes.HLOCAL
            argc = ctypes.c_int(0)
            argv = shell32.CommandLineToArgvW(f"ai_docker_arg0 {text}", ctypes.byref(argc))
            if argv:
                try:
                    return [argv[i] for i in range(1, argc.value)]
                finally:
                    kernel32.LocalFree(argv)
        except Exception:
            pass
    try:
        return shlex.split(text, posix=False)
    except ValueError:
        return [text]


def _resolve_lnk_details(lnk_path: Path, shell: Any = None) -> tuple[str, str, list[str], str]:
    """Return (target_exe_path, display_name, args, working_dir) for a .lnk shortcut."""
    try:
        if shell is None:
            import win32com.client

            shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(lnk_path))
        target = (sc.Targetpath or "").strip()
        args = _split_windows_args(sc.Arguments or "")
        work_dir = (sc.WorkingDirectory or "").strip()
        return target, lnk_path.stem, args, work_dir
    except Exception:
        return "", lnk_path.stem, [], ""


def _resolve_lnk(lnk_path: Path, shell: Any = None) -> tuple[str, str]:
    """Return (target_exe_path, display_name) for a .lnk shortcut."""
    target, stem, _, _ = _resolve_lnk_details(lnk_path, shell=shell)
    return target, stem


def build_agent_from_file(
    file_path: str, existing_agents: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Build a valid Agent config dict from a dropped or selected .exe / .lnk file."""
    p = Path(file_path)
    if not p.is_file():
        return None

    display_name = p.stem
    target_exe = str(p)
    args: list[str] = []
    work_dir = ""

    if p.suffix.lower() == ".lnk":
        resolved, stem, args, work_dir = _resolve_lnk_details(p)
        if not resolved:
            return None
        target_exe = resolved
        display_name = stem
    elif p.suffix.lower() != ".exe":
        return None

    exe_path = Path(target_exe)
    if not exe_path.is_file() or exe_path.suffix.lower() != ".exe":
        return None
    existing_ids = {a.get("id", "") for a in existing_agents}
    entry: dict[str, Any] = {
        "id": slugify_id(display_name, existing_ids),
        "name": display_name,
        "type": "exe",
        "path": str(exe_path),
        "match_process": exe_path.name,
        "enabled": True,
    }
    if args:
        entry["args"] = args
    if work_dir and Path(work_dir).is_dir():
        entry["cwd"] = work_dir
    return entry


def _scan_shortcuts() -> list[tuple[str, str, list[str], str]]:
    """Scan Desktop & Start Menu shortcuts (fast, small directories) for AI agents."""
    userprofile = Path(os.environ.get("USERPROFILE", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    programdata = Path(os.environ.get("PROGRAMDATA", ""))

    roots = [
        userprofile / "Desktop",
        Path("C:/Users/Public/Desktop"),
        appdata / "Microsoft/Windows/Start Menu/Programs",
        programdata / "Microsoft/Windows/Start Menu/Programs",
    ]
    results: list[tuple[str, str, list[str], str]] = []
    shell: Any = None
    try:
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        shell = None

    for root in roots:
        if not root.exists():
            continue
        try:
            for item in root.rglob("*.lnk"):
                low = item.stem.lower()
                if not any(k in low for k in DESKTOP_KEYWORDS):
                    continue
                if _is_skipped_exe(low):
                    continue
                target, disp, args, work_dir = _resolve_lnk_details(item, shell=shell)
                if target and target.lower().endswith(".exe") and Path(target).is_file():
                    if not _is_skipped_exe(Path(target).name):
                        results.append((target, disp, args, work_dir))
        except Exception:
            continue
    return results


def _scan_program_dirs() -> list[tuple[str, str]]:
    """Shallow scan (depth <= 2) of standard program directories."""
    localappdata = Path(os.environ.get("LOCALAPPDATA", ""))
    roots = [
        localappdata / "Programs",
        Path("C:/Program Files"),
        Path("D:/Program Files"),
        Path("E:/Programs"),
        Path("D:/Programs"),
    ]
    results: list[tuple[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                low = child.name.lower()
                if not any(k in low for k in DESKTOP_KEYWORDS):
                    continue
                for sub in child.iterdir():
                    if sub.is_file() and sub.suffix.lower() == ".exe" and not _is_skipped_exe(sub.name):
                        results.append((str(sub), sub.stem))
        except Exception:
            continue
    return results


def _scan_msix_packages() -> list[dict[str, Any]]:
    """Fast registry scan of installed MSIX families for known AI desktop apps."""
    found: list[dict[str, Any]] = []
    reg_path = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Families"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            idx = 0
            while True:
                try:
                    family = winreg.EnumKey(key, idx)
                    idx += 1
                except OSError:
                    break
                for spec in MSIX_CATALOG:
                    if family.startswith(spec["prefix"]):
                        aumid = f"{family}!{spec['appid']}"
                        found.append(
                            {
                                "name": spec["name"],
                                "type": "msix",
                                "aumid": aumid,
                                "match_process": spec["match_process"],
                                "notification_handlers": [aumid],
                            }
                        )
    except Exception:
        pass
    return found


def discover_new_agents(existing_agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Discover installed local AI Agents not yet present in `existing_agents`."""
    existing_ids = {a.get("id", "") for a in existing_agents}
    existing_paths = {_norm_path(a.get("path", "")) for a in existing_agents if a.get("path")}
    existing_aumids = {(a.get("aumid") or "").lower() for a in existing_agents if a.get("aumid")}
    existing_cmds = {(a.get("command") or "").strip().lower() for a in existing_agents if a.get("type") == "cli"}

    seen_paths = set(existing_paths)
    seen_aumids = set(existing_aumids)
    seen_cmds = set(existing_cmds)
    used_ids = set(existing_ids)

    candidates: list[dict[str, Any]] = []

    # 1. Desktop Win32 apps (shortcuts + shallow program dirs)
    desktop_hits: list[tuple[str, str, list[str], str]] = []
    for item in [*_scan_shortcuts(), *_scan_program_dirs()]:
        if len(item) == 4:
            desktop_hits.append(item)
        else:
            desktop_hits.append((item[0], item[1], [], ""))
    for exe_path, disp_name, args, work_dir in desktop_hits:
        norm = _norm_path(exe_path)
        if not norm or norm in seen_paths:
            continue
        seen_paths.add(norm)
        aid = slugify_id(disp_name, used_ids)
        used_ids.add(aid)
        entry: dict[str, Any] = {
            "id": aid,
            "name": disp_name,
            "type": "exe",
            "path": exe_path,
            "match_process": os.path.basename(exe_path),
            "enabled": True,
        }
        if args:
            entry["args"] = list(args)
        if work_dir and Path(work_dir).is_dir():
            entry["cwd"] = work_dir
        candidates.append(entry)

    # 2. MSIX / Store apps
    for item in _scan_msix_packages():
        aumid_low = item["aumid"].lower()
        if aumid_low in seen_aumids:
            continue
        seen_aumids.add(aumid_low)
        aid = slugify_id(item["name"], used_ids)
        used_ids.add(aid)
        candidates.append(
            {
                "id": aid,
                "name": item["name"],
                "type": "msix",
                "aumid": item["aumid"],
                "match_process": item["match_process"],
                "notification_handlers": item["notification_handlers"],
                "enabled": True,
            }
        )

    # 3. CLI agents available on PATH
    default_cwd = str(Path.home())
    for spec in CLI_CATALOG:
        cmd = spec["cmd"]
        if cmd.lower() in seen_cmds:
            continue
        found_bin = shutil.which(cmd)
        if not found_bin:
            continue
        seen_cmds.add(cmd.lower())
        aid = slugify_id(f"{spec['name']}-cli", used_ids)
        used_ids.add(aid)
        entry: dict[str, Any] = {
            "id": aid,
            "name": spec["name"],
            "type": "cli",
            "command": cmd,
            "terminal_title": spec["name"],
            "cwd": default_cwd,
            "cli_badge": True,
            "match_process": "WindowsTerminal.exe",
            "match_title": spec["name"],
            "notification_handlers": ["Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"],
            "notification_keywords": spec.get("notification_keywords", [cmd]),
            "enabled": True,
        }
        if spec.get("icon_source"):
            entry["icon_source"] = spec["icon_source"]
        candidates.append(entry)

    return candidates
