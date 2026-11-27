from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import local
from typing import Any

from .web_utils import normalize_web_url

_THREAD_HELD_LOCKS = local()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "agents.json"
CACHE_DIR = PROJECT_ROOT / ".cache" / "icons"

DEFAULT_UI: dict[str, Any] = {
    "theme": "dark",
    "icon_size": 32,
    "button_size": 46,
    "opacity": 0.92,
    "pos": None,
    "show_running_dot": True,
    "focus_existing": True,
    "hide_from_taskbar": True,
    "show_window_list": True,
    "window_chip_width": 138,
    "auto_collapse": True,
    "auto_collapse_seconds": 8,
    "orientation": "horizontal",
}

TASKBAR_HIDE_EXCLUDED_PROCESSES = frozenset({"chrome.exe"})


def is_taskbar_hide_excluded_process(process_name: str | None) -> bool:
    """Return True for processes whose taskbar icons must never be hidden."""
    if not process_name:
        return False
    return os.path.basename(str(process_name).strip()).lower() in TASKBAR_HIDE_EXCLUDED_PROCESSES


def is_taskbar_hide_excluded_agent(agent: dict[str, Any]) -> bool:
    """Return True when an agent targets a process excluded from taskbar hiding."""
    return is_taskbar_hide_excluded_process(agent.get("match_process")) or is_taskbar_hide_excluded_process(
        agent.get("path")
    )


class ConfigLoadError(Exception):
    """Raised when agents.json cannot be read or used."""


def _load_config_unlocked(path: Path = CONFIG_PATH) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        from .discovery import discover_new_agents

        discovered = discover_new_agents([])
        cfg = validate_config({"ui": dict(DEFAULT_UI), "agents": discovered})
        try:
            save_config(cfg, path)
        except OSError as exc:
            raise ConfigLoadError(str(exc)) from exc
        return cfg
    except (OSError, ValueError) as exc:
        raise ConfigLoadError(str(exc)) from exc

    return validate_config(data)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        with _config_file_lock(path):
            return _load_config_unlocked(path)
    except (OSError, ValueError) as exc:
        raise ConfigLoadError(str(exc)) from exc

    return validate_config(data)


def validate_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigLoadError("配置文件根节点必须是 JSON 对象")

    saved_ui = data.get("ui", {})
    if not isinstance(saved_ui, dict):
        raise ConfigLoadError("ui 必须是 JSON 对象")
    ui = {**DEFAULT_UI, **saved_ui}
    size_limits = {
        "icon_size": (8, 512),
        "button_size": (16, 512),
        "window_chip_width": (40, 1024),
    }
    for key, (lo, hi) in size_limits.items():
        raw = ui[key]
        try:
            if isinstance(raw, bool):
                raise TypeError
            size = int(raw)
            if not lo <= size <= hi:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConfigLoadError(f"ui.{key} 必须是 {lo}..{hi} 之间的有效像素尺寸") from exc
    try:
        raw_opacity = ui["opacity"]
        if isinstance(raw_opacity, bool) or not math.isfinite(float(raw_opacity) * 255):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigLoadError("ui.opacity 必须是有限数值") from exc
    try:
        raw_collapse_sec = ui["auto_collapse_seconds"]
        if isinstance(raw_collapse_sec, bool) or not math.isfinite(float(raw_collapse_sec)):
            raise ValueError
        int(raw_collapse_sec)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigLoadError("ui.auto_collapse_seconds 必须是有效整数") from exc
    raw_orientation = ui.get("orientation", "horizontal")
    if not isinstance(raw_orientation, str) or raw_orientation.strip().lower() not in (
        "horizontal",
        "vertical",
    ):
        raise ConfigLoadError("ui.orientation 仅支持 'horizontal' 或 'vertical'")
    ui["orientation"] = raw_orientation.strip().lower()
    if ui["pos"] is not None:
        try:
            x, y = ui["pos"]
            if (
                not isinstance(ui["pos"], list)
                or any(isinstance(n, bool) for n in (x, y))
                or not all(-(2**31) <= int(n) < 2**31 for n in (x, y))
            ):
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConfigLoadError("ui.pos 必须是包含两个有效坐标的数组") from exc
    agents = data.get("agents", [])
    if not isinstance(agents, list) or any(not isinstance(agent, dict) for agent in agents):
        raise ConfigLoadError("agents 必须是 JSON 对象数组")
    seen_ids: set[str] = set()
    for index, agent in enumerate(agents):
        if "match_path" in agent and not isinstance(agent["match_path"], bool):
            raise ConfigLoadError(f"agents[{index}].match_path 必须是布尔值")
        for key in ("id", "name", "type"):
            if key in agent and not isinstance(agent[key], str):
                raise ConfigLoadError(f"agents[{index}].{key} 必须是字符串")
        aid = agent.get("id", "")
        if aid:
            if aid in seen_ids:
                raise ConfigLoadError(f"agents[{index}].id 重复: {aid}")
            seen_ids.add(aid)
        for key in (
            "path",
            "icon",
            "icon_source",
            "url",
            "aumid",
            "match_process",
            "match_title",
            "command",
            "cwd",
            "terminal_title",
            "browser",
            "accent",
        ):
            if agent.get(key) is not None and not isinstance(agent[key], str):
                raise ConfigLoadError(f"agents[{index}].{key} 必须是字符串")
        for key in ("args", "new_instance_args"):
            values = agent.get(key)
            if values is None:
                continue
            if not isinstance(values, list) or any(isinstance(value, (dict, list)) for value in values):
                raise ConfigLoadError(f"agents[{index}].{key} 必须是由基本值组成的数组")
            agent[key] = [str(value) for value in values]
        for key in ("notification_handlers", "notification_keywords"):
            values = agent.get(key, [])
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ConfigLoadError(f"agents[{index}].{key} 必须是字符串数组")
        if agent.get("type") == "web" and agent.get("url"):
            normalized_url = normalize_web_url(agent["url"])
            if not normalized_url:
                raise ConfigLoadError(f"agents[{index}].url 必须是合法的 http(s) URL")
            agent["url"] = normalized_url
    return {**data, "ui": ui, "agents": agents}


@contextmanager
def _config_file_lock(path: Path, timeout_sec: float = 5.0) -> Iterator[None]:
    """Acquire an inter-process advisory lock alongside the target config file (reentrant per thread)."""
    norm = os.path.normcase(os.path.abspath(str(path)))
    held: dict[str, int] | None = getattr(_THREAD_HELD_LOCKS, "counts", None)
    if held is None:
        held = {}
        _THREAD_HELD_LOCKS.counts = held
    if held.get(norm, 0) > 0:
        held[norm] += 1
        try:
            yield
        finally:
            held[norm] -= 1
            if held[norm] <= 0:
                held.pop(norm, None)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    with open(lock_path, "a+b") as lock_file:
        if os.fstat(lock_file.fileno()).st_size == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + timeout_sec
        locked = False
        try:
            while True:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"获取配置锁超时: {lock_path}") from None
                    time.sleep(0.02)
            held[norm] = 1
            yield
        finally:
            held.pop(norm, None)
            if locked:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def update_config(
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
    path: Path = CONFIG_PATH,
    *,
    timeout_sec: float = 5.0,
) -> dict[str, Any]:
    """Read-modify-write config atomically under an inter-process lock."""
    with _config_file_lock(path, timeout_sec=timeout_sec):
        current = _load_config_unlocked(path)
        updated = validate_config(mutator(current))
        save_config(updated, path)
        return updated


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    """Atomically write config via a temp file in the same directory + os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".agents_", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
