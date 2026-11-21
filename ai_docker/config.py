from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "agents.json"
CACHE_DIR = PROJECT_ROOT / ".cache" / "icons"

DEFAULT_UI: dict[str, Any] = {
    "orientation": "horizontal",
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


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        from .discovery import discover_new_agents

        discovered = discover_new_agents([])
        cfg = {"ui": dict(DEFAULT_UI), "agents": discovered}
        save_config(cfg, path)
        return cfg
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    ui = dict(DEFAULT_UI)
    ui.update(data.get("ui", {}))
    agents = data.get("agents", [])
    return {"ui": ui, "agents": agents}


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
