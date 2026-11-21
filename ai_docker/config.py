from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

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
}


class ConfigLoadError(Exception):
    """Raised when agents.json cannot be parsed as valid JSON."""


def load_config(path: Path = CONFIG_PATH, strict: bool = False) -> dict[str, Any]:
    if not path.exists():
        from .discovery import discover_new_agents

        discovered = discover_new_agents([])
        cfg = {"ui": dict(DEFAULT_UI), "agents": discovered}
        save_config(cfg, path)
        return cfg

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ConfigLoadError("配置文件根节点必须是 JSON 对象")
    except Exception as exc:
        if strict:
            raise ConfigLoadError(str(exc)) from exc
        # Startup fallback: preserve broken file untouched, run with safe default in memory
        return {"ui": dict(DEFAULT_UI), "agents": []}

    ui = dict(DEFAULT_UI)
    if isinstance(data.get("ui"), dict):
        ui.update(data["ui"])
    agents = data.get("agents", [])
    if not isinstance(agents, list):
        agents = []
    return {"ui": ui, "agents": agents}


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
