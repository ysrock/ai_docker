from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .config import (
    CONFIG_PATH,
    DEFAULT_UI,
    PROJECT_ROOT,
    ConfigLoadError,
    is_taskbar_hide_excluded_agent,
    load_config,
    save_config,
    update_config,
    validate_config,
)
from .discovery import build_agent_from_file, discover_new_agents, slugify_id
from .ipc import (
    IPC_CMD_CLEAR_NOTIFICATIONS,
    IPC_CMD_COLLAPSE,
    IPC_CMD_EXPAND,
    IPC_CMD_RELOAD,
    close_dock_window,
    find_dock_window,
    send_dock_command,
    send_dock_command_status,
)
from .launcher import get_agent_window_map, launch_agent
from .web_utils import (
    detect_web_browser_matching,
    normalize_web_url,
    suggest_web_agent_name,
)


class CliError(Exception):
    """Structured CLI error mapped to JSON or human-readable stderr output."""

    def __init__(self, message: str, code: str = "error", exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code


_UI_TYPES: dict[str, type] = {
    "theme": str,
    "icon_size": int,
    "button_size": int,
    "opacity": float,
    "show_running_dot": bool,
    "focus_existing": bool,
    "hide_from_taskbar": bool,
    "show_window_list": bool,
    "window_chip_width": int,
    "auto_collapse": bool,
    "auto_collapse_seconds": int,
    "orientation": str,
}


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _parse_ui_value(key: str, raw: str) -> Any:
    if key not in _UI_TYPES:
        allowed = ", ".join(sorted(_UI_TYPES))
        raise CliError(f"不支持的 ui 配置键 '{key}'，可用键：{allowed}", code="invalid_key", exit_code=2)
    expected = _UI_TYPES[key]
    val = raw.strip()
    if expected is bool:
        low = val.lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise CliError(f"ui.{key} 需要布尔值 (true/false)，收到：{raw!r}", code="invalid_value", exit_code=2)
    if expected is int:
        try:
            return int(val)
        except ValueError as exc:
            raise CliError(f"ui.{key} 需要整数，收到：{raw!r}", code="invalid_value", exit_code=2) from exc
    if expected is float:
        try:
            return float(val)
        except ValueError as exc:
            raise CliError(f"ui.{key} 需要浮点数，收到：{raw!r}", code="invalid_value", exit_code=2) from exc
    if key == "theme" and val.lower() not in ("dark", "light"):
        raise CliError("ui.theme 仅支持 'dark' 或 'light'", code="invalid_value", exit_code=2)
    if key == "orientation" and val.lower() not in ("horizontal", "vertical"):
        raise CliError("ui.orientation 仅支持 'horizontal' 或 'vertical'", code="invalid_value", exit_code=2)
    return val.lower() if key in ("theme", "orientation") else val


def _is_default_config(cfg_path: Path) -> bool:
    try:
        return os.path.normcase(str(cfg_path.resolve())) == os.path.normcase(str(CONFIG_PATH.resolve()))
    except OSError:
        return cfg_path == CONFIG_PATH


def _mutate_and_notify(
    cfg_path: Path,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
    notify: bool,
) -> tuple[dict[str, Any], bool]:
    """Read-modify-write config atomically under inter-process lock and hot-reload dock."""
    try:
        updated = update_config(mutator, cfg_path)
    except ConfigLoadError as exc:
        raise CliError(f"配置值非法：{exc}", code="invalid_value", exit_code=2) from exc
    except (OSError, TimeoutError) as exc:
        raise CliError(f"保存配置失败：{exc}", code="io_error") from exc
    notified = send_dock_command(IPC_CMD_RELOAD) if (notify and _is_default_config(cfg_path)) else False
    return updated, notified


def _find_agent(cfg: dict[str, Any], agent_id: str) -> tuple[int, dict[str, Any]]:
    for idx, ag in enumerate(cfg.get("agents", [])):
        if ag.get("id") == agent_id:
            return idx, ag
    raise CliError(f"找不到 Agent：{agent_id}", code="not_found")


def _agent_target(agent: dict[str, Any]) -> str:
    return (
        agent.get("url")
        or agent.get("path")
        or agent.get("command")
        or agent.get("aumid")
        or ""
    )


def _agent_summary(agent: dict[str, Any], windows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": agent.get("id", ""),
        "name": agent.get("name", ""),
        "type": agent.get("type", "exe"),
        "enabled": agent.get("enabled", True) is not False,
        "released_to_taskbar": (agent.get("hide_from_taskbar", True) is False) or is_taskbar_hide_excluded_agent(agent),
        "running": len(windows) > 0,
        "window_count": len(windows),
        "projects": [w.get("project", "") for w in windows],
        "target": _agent_target(agent),
    }


def _cmd_list(args: argparse.Namespace, cfg_path: Path, _notify: bool) -> tuple[dict[str, Any], str]:
    cfg = load_config(cfg_path)
    agents = cfg.get("agents", [])
    win_map = get_agent_window_map(agents)
    items: list[dict[str, Any]] = []
    for ag in agents:
        enabled = ag.get("enabled", True) is not False
        if not args.all and not enabled:
            continue
        wins = win_map.get(ag.get("id", ""), [])
        if args.running and not wins:
            continue
        items.append(_agent_summary(ag, wins))

    lines = []
    for it in items:
        state = f"running({it['window_count']})" if it["running"] else ("idle" if it["enabled"] else "hidden")
        rel = " [released]" if it["released_to_taskbar"] else ""
        lines.append(f"{it['id']:<20} {it['type']:<5} {state:<12} {it['name']}{rel}")
    text = "\n".join(lines) if lines else "(无匹配的 Agent)"
    return {"agents": items, "count": len(items)}, text


def _cmd_status(_args: argparse.Namespace, cfg_path: Path, _notify: bool) -> tuple[dict[str, Any], str]:
    cfg = load_config(cfg_path)
    agents = cfg.get("agents", [])
    win_map = get_agent_window_map(agents)
    dock_info = find_dock_window() if _is_default_config(cfg_path) else None
    enabled_count = sum(1 for a in agents if a.get("enabled", True) is not False)

    windows_by_agent = {
        aid: [
            {"hwnd": w["hwnd"], "project": w.get("project", ""), "title": w.get("title", "")}
            for w in wins
        ]
        for aid, wins in win_map.items()
    }
    data = {
        "version": __version__,
        "config_path": str(cfg_path),
        "dock_running": dock_info is not None,
        "dock_hwnd": dock_info[0] if dock_info else None,
        "dock_pid": dock_info[1] if dock_info else None,
        "ui": cfg.get("ui", {}),
        "total_agents": len(agents),
        "enabled_agents": enabled_count,
        "running_agents": sorted(win_map.keys()),
        "windows_by_agent": windows_by_agent,
    }
    dock_str = f"运行中 (PID {dock_info[1]})" if dock_info else "未运行"
    text = (
        f"AI docker v{__version__} | 悬浮窗: {dock_str} | 主题: {cfg['ui'].get('theme')} | "
        f"Agent: {enabled_count}/{len(agents)} 启用, {len(win_map)} 运行中"
    )
    return data, text


def _cmd_add(args: argparse.Namespace, cfg_path: Path, notify: bool) -> tuple[dict[str, Any], str]:
    kind = args.add_type
    created_holder: dict[str, Any] = {}

    def _build_and_append(cfg: dict[str, Any]) -> dict[str, Any]:
        agents = list(cfg.get("agents", []))
        existing_ids = {a.get("id", "") for a in agents}
        if kind == "web":
            url = normalize_web_url(args.url)
            if not url:
                raise CliError(f"无效的网页地址：{args.url!r}", code="invalid_url", exit_code=2)
            name = (args.name or "").strip() or suggest_web_agent_name(url)
            aid = slugify_id(args.id or name, existing_ids)
            match_title = (args.match_title or "").strip() or name
            match_process, notif_handlers = detect_web_browser_matching(args.browser)
            entry: dict[str, Any] = {
                "id": aid,
                "name": name,
                "type": "web",
                "url": url,
                "app_mode": not args.no_app_mode,
                "match_process": match_process,
                "match_title": match_title,
                "notification_handlers": notif_handlers,
                "notification_keywords": [match_title.lower()],
                "enabled": True,
            }
            if args.browser:
                entry["browser"] = args.browser
            if args.icon:
                entry["icon_source"] = args.icon
        elif kind == "exe":
            entry = build_agent_from_file(args.path, agents)
            if not entry:
                raise CliError(f"找不到有效的 .exe 或 .lnk 文件：{args.path}", code="invalid_path", exit_code=2)
            if args.name:
                entry["name"] = args.name.strip()
            if args.id or args.name:
                entry["id"] = slugify_id(args.id or entry["name"], existing_ids)
            if args.match_process:
                entry["match_process"] = args.match_process.strip()
            if args.no_match_path:
                entry["match_path"] = False
            if args.args:
                entry["args"] = list(args.args)
        elif kind == "cli":
            cmd = args.cli_command.strip()
            if not cmd:
                raise CliError("CLI 命令不能为空", code="invalid_command", exit_code=2)
            name = (args.name or "").strip() or cmd.split()[0]
            aid = slugify_id(args.id or f"{name}-cli", existing_ids)
            title = (args.title or "").strip() or name
            entry = {
                "id": aid,
                "name": name,
                "type": "cli",
                "command": cmd,
                "terminal_title": title,
                "cwd": args.cwd or str(Path.home()),
                "cli_badge": True,
                "match_process": "WindowsTerminal.exe",
                "match_title": title,
                "notification_handlers": ["Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"],
                "notification_keywords": [name.lower()],
                "enabled": True,
            }
            if args.icon:
                entry["icon_source"] = args.icon
        elif kind == "msix":
            aumid = args.aumid.strip()
            if "!" not in aumid:
                raise CliError("MSIX AUMID 格式应为 PackageFamilyName!AppId", code="invalid_aumid", exit_code=2)
            name = (args.name or "").strip() or aumid.split("!", 1)[1]
            aid = slugify_id(args.id or name, existing_ids)
            entry = {
                "id": aid,
                "name": name,
                "type": "msix",
                "aumid": aumid,
                "match_process": (args.match_process or "").strip() or f"{name}.exe",
                "notification_handlers": [aumid],
                "enabled": True,
            }
        else:
            raise CliError(f"不支持的 Agent 类型：{kind}", code="invalid_type", exit_code=2)

        created_holder["entry"] = entry
        agents.append(entry)
        return {**cfg, "agents": agents}

    _, notified = _mutate_and_notify(cfg_path, _build_and_append, notify)
    entry = created_holder["entry"]
    return {"agent": entry, "dock_notified": notified}, f"已添加 {entry['type']} Agent: {entry['name']} ({entry['id']})"


def _cmd_remove(args: argparse.Namespace, cfg_path: Path, notify: bool) -> tuple[dict[str, Any], str]:
    removed_holder: dict[str, Any] = {}

    def _remove(cfg: dict[str, Any]) -> dict[str, Any]:
        agents = list(cfg.get("agents", []))
        idx, target = _find_agent(cfg, args.agent_id)
        if target.get("enabled", True) is not False:
            remaining_visible = sum(
                1 for i, a in enumerate(agents) if i != idx and a.get("enabled", True) is not False
            )
            if remaining_visible == 0 and not args.force:
                raise CliError(
                    "至少需要保留 1 个可见 Agent（如确需删除请加 --force）",
                    code="last_visible_agent",
                )
        removed_holder["removed"] = agents.pop(idx)
        return {**cfg, "agents": agents}

    _, notified = _mutate_and_notify(cfg_path, _remove, notify)
    removed = removed_holder["removed"]
    return {"removed": removed, "dock_notified": notified}, f"已移除 Agent: {removed.get('name')} ({removed.get('id')})"


def _cmd_visibility(args: argparse.Namespace, cfg_path: Path, notify: bool, visible: bool) -> tuple[dict[str, Any], str]:
    updated_holder: dict[str, Any] = {}

    def _set_vis(cfg: dict[str, Any]) -> dict[str, Any]:
        agents = list(cfg.get("agents", []))
        idx, target = _find_agent(cfg, args.agent_id)
        if not visible:
            visible_count = sum(1 for a in agents if a.get("enabled", True) is not False)
            if target.get("enabled", True) is not False and visible_count <= 1:
                raise CliError("至少需要保留 1 个可见 Agent，无法隐藏最后一个图标", code="last_visible_agent")
        agents[idx] = {**target, "enabled": visible}
        updated_holder["agent"] = agents[idx]
        return {**cfg, "agents": agents}

    _, notified = _mutate_and_notify(cfg_path, _set_vis, notify)
    target = updated_holder["agent"]
    action = "显示" if visible else "隐藏"
    return {"agent": target, "dock_notified": notified}, f"已{action} Agent: {target.get('name')} ({target.get('id')})"


def _cmd_taskbar_release(
    args: argparse.Namespace, cfg_path: Path, notify: bool, released: bool
) -> tuple[dict[str, Any], str]:
    if not released and not getattr(args, "all", False) and not args.agent_id:
        raise CliError("请指定 agent_id（或使用 reclaim --all）", code="missing_argument", exit_code=2)

    state_holder: dict[str, Any] = {}

    def _set_rel(cfg: dict[str, Any]) -> dict[str, Any]:
        agents = list(cfg.get("agents", []))
        if not released and getattr(args, "all", False):
            changed_ids: list[str] = []
            for i, ag in enumerate(agents):
                if ag.get("hide_from_taskbar", True) is False:
                    agents[i] = {**ag, "hide_from_taskbar": True}
                    changed_ids.append(ag.get("id", ""))
            state_holder["reclaimed_ids"] = changed_ids
            return {**cfg, "agents": agents}

        idx, target = _find_agent(cfg, args.agent_id)
        agents[idx] = {**target, "hide_from_taskbar": (not released)}
        state_holder["agent"] = agents[idx]
        return {**cfg, "agents": agents}

    _, notified = _mutate_and_notify(cfg_path, _set_rel, notify)
    if "reclaimed_ids" in state_holder:
        changed_ids = state_holder["reclaimed_ids"]
        return {"reclaimed_ids": changed_ids, "dock_notified": notified}, f"已重新收进 {len(changed_ids)} 个 Agent"
    target = state_holder["agent"]
    verb = "释放到任务栏" if released else "重新收进悬浮窗"
    return {"agent": target, "dock_notified": notified}, f"已{verb}: {target.get('name')} ({target.get('id')})"


def _cmd_move(args: argparse.Namespace, cfg_path: Path, notify: bool) -> tuple[dict[str, Any], str]:
    move_holder: dict[str, Any] = {}

    def _move(cfg: dict[str, Any]) -> dict[str, Any]:
        agents = list(cfg.get("agents", []))
        visible_slots = [i for i, a in enumerate(agents) if a.get("enabled", True) is not False]
        visible_items = [agents[i] for i in visible_slots]
        cur_idx = next((i for i, a in enumerate(visible_items) if a.get("id") == args.agent_id), -1)
        if cur_idx < 0:
            raise CliError(f"找不到可见 Agent：{args.agent_id}", code="not_found")

        pos_raw = str(args.position).strip().lower()
        if pos_raw == "first":
            new_idx = 0
        elif pos_raw == "last":
            new_idx = len(visible_items) - 1
        elif pos_raw == "left":
            new_idx = max(0, cur_idx - 1)
        elif pos_raw == "right":
            new_idx = min(len(visible_items) - 1, cur_idx + 1)
        else:
            try:
                new_idx = max(0, min(len(visible_items) - 1, int(pos_raw)))
            except ValueError as exc:
                raise CliError(
                    "位置参数需为整数索引，或 first / last / left / right",
                    code="invalid_position",
                    exit_code=2,
                ) from exc

        moved = visible_items.pop(cur_idx)
        visible_items.insert(new_idx, moved)
        for slot, item in zip(visible_slots, visible_items):
            agents[slot] = item
        move_holder["index"] = new_idx
        move_holder["order"] = [a.get("id", "") for a in visible_items]
        return {**cfg, "agents": agents}

    _, notified = _mutate_and_notify(cfg_path, _move, notify)
    return (
        {
            "agent_id": args.agent_id,
            "index": move_holder["index"],
            "visible_order": move_holder["order"],
            "dock_notified": notified,
        },
        f"已将 {args.agent_id} 移动到可见位置 {move_holder['index']}",
    )


def _cmd_open(args: argparse.Namespace, cfg_path: Path, _notify: bool) -> tuple[dict[str, Any], str]:
    cfg = load_config(cfg_path)
    _, target = _find_agent(cfg, args.agent_id)
    focus_existing = False if args.new else bool(cfg["ui"].get("focus_existing", True))
    ok, msg = launch_agent(target, focus_existing=focus_existing)
    if not ok:
        raise CliError(msg, code="launch_failed")
    return {"agent_id": args.agent_id, "detail": msg}, f"已执行: {msg}"


def _cmd_discover(args: argparse.Namespace, cfg_path: Path, notify: bool) -> tuple[dict[str, Any], str]:
    if not args.apply:
        cfg = load_config(cfg_path)
        found = discover_new_agents(list(cfg.get("agents", [])))
        notified = False
    else:
        found_holder: dict[str, list[dict[str, Any]]] = {"found": []}

        def _apply_discover(cfg: dict[str, Any]) -> dict[str, Any]:
            existing = list(cfg.get("agents", []))
            discovered = discover_new_agents(existing)
            found_holder["found"] = discovered
            if not discovered:
                return cfg
            return {**cfg, "agents": [*existing, *discovered]}

        _, notified = _mutate_and_notify(cfg_path, _apply_discover, notify)
        found = found_holder["found"]

    lines = [f"{a['id']:<20} {a['type']:<5} {a['name']} -> {_agent_target(a)}" for a in found]
    if args.apply:
        header = f"已扫描并导入 {len(found)} 个新 Agent" if found else "未发现新的本机 Agent"
    else:
        header = f"发现 {len(found)} 个未收录的本机 Agent（加 --apply 可直接导入）" if found else "未发现新的本机 Agent"
    text = "\n".join([header, *lines])
    return {"discovered": found, "applied": bool(args.apply), "dock_notified": notified}, text


def _cmd_config(args: argparse.Namespace, cfg_path: Path, notify: bool) -> tuple[dict[str, Any], str]:
    if args.config_action == "get":
        cfg = load_config(cfg_path)
        ui = cfg.get("ui", {})
        if args.key:
            if args.key not in ui:
                raise CliError(f"未找到配置项 ui.{args.key}", code="not_found")
            return {"key": args.key, "value": ui[args.key]}, f"{args.key} = {json.dumps(ui[args.key], ensure_ascii=False)}"
        return {"ui": ui}, json.dumps(ui, ensure_ascii=False, indent=2)

    if args.config_action == "set":
        parsed_val = _parse_ui_value(args.key, args.value)

        def _set_ui(cfg: dict[str, Any]) -> dict[str, Any]:
            return {**cfg, "ui": {**cfg.get("ui", {}), args.key: parsed_val}}

        _, notified = _mutate_and_notify(cfg_path, _set_ui, notify)
        return {"key": args.key, "value": parsed_val, "dock_notified": notified}, f"已更新 ui.{args.key} = {parsed_val!r}"

    raise CliError("未知 config 子命令", code="invalid_command", exit_code=2)


def _cmd_dock(args: argparse.Namespace, _cfg_path: Path, _notify: bool) -> tuple[dict[str, Any], str]:
    action = args.dock_action
    if action == "start":
        existing = find_dock_window()
        if existing is not None:
            return {"running": True, "hwnd": existing[0], "pid": existing[1], "started": False}, f"AI docker 已在运行 (PID {existing[1]})"
        launcher_exe = PROJECT_ROOT / "AI docker.exe"
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        if launcher_exe.exists():
            subprocess.Popen([str(launcher_exe)], cwd=str(PROJECT_ROOT), creationflags=creationflags)
            return {"started": True, "launcher": str(launcher_exe)}, f"已启动 {launcher_exe.name}"
        pythonw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
        entry = PROJECT_ROOT / "start_dock.pyw"
        exe = str(pythonw) if pythonw.exists() else sys.executable
        subprocess.Popen([exe, str(entry)], cwd=str(PROJECT_ROOT), creationflags=creationflags)
        return {"started": True, "launcher": exe}, "已启动 AI docker"

    if action == "stop":
        ok, msg = close_dock_window()
        if not ok:
            raise CliError(msg, code="dock_not_running")
        return {"stopped": True, "message": msg}, msg

    cmd_map = {
        "reload": (IPC_CMD_RELOAD, "已通知悬浮窗热重载配置"),
        "expand": (IPC_CMD_EXPAND, "已通知悬浮窗展开"),
        "collapse": (IPC_CMD_COLLAPSE, "已通知悬浮窗收缩为悬浮图标"),
        "clear-notifications": (IPC_CMD_CLEAR_NOTIFICATIONS, "已通知悬浮窗清除全部通知角标"),
    }
    ipc_code, ok_msg = cmd_map[action]
    sent, err_code, err_msg = send_dock_command_status(ipc_code)
    if not sent:
        raise CliError(err_msg, code=err_code)
    return {"action": action, "dock_notified": True}, ok_msg


def _cmd_update(args: argparse.Namespace, _cfg_path: Path, _notify: bool) -> tuple[dict[str, Any], str]:
    # Lazy import to avoid pulling PySide6 on fast CLI commands
    from .updater import check_for_updates, launch_updater_script

    if args.update_action == "check":
        res = check_for_updates()
        data = {
            "status": res.status,
            "local_sha": res.local_sha,
            "remote_sha": res.remote_sha,
            "branch": res.branch,
            "remote_ref": res.remote_ref,
            "behind_count": res.behind_count,
            "ahead_count": res.ahead_count,
            "has_local_changes": res.has_local_changes,
            "can_fast_forward": res.can_fast_forward,
            "commits": list(res.commits),
            "message": res.message,
        }
        lines = [res.message, *res.commits]
        return data, "\n".join(lines)

    if args.update_action == "apply":
        ok, detail = launch_updater_script(wait_pid=None, gui_mode=False)
        if not ok:
            raise CliError(detail, code="update_failed")
        return {"started": True, "script": detail}, f"已拉起更新脚本：{detail}"

    raise CliError("未知 update 子命令", code="invalid_command", exit_code=2)


def _cmd_schema(_args: argparse.Namespace, _cfg_path: Path, _notify: bool) -> tuple[dict[str, Any], str]:
    schema = {
        "name": "ai-docker",
        "version": __version__,
        "global_flags": ["--json", "--config <path>", "--no-notify"],
        "commands": [
            "list [--all] [--running]",
            "status",
            "add web <url> [--id ID] [--name NAME] [--match-title TITLE] [--icon ICON] [--no-app-mode] [--browser PATH]",
            "add exe <path> [--id ID] [--name NAME] [--args ARG...] [--match-process PROC] [--no-match-path]",
            "add cli <command> [--id ID] [--name NAME] [--cwd DIR] [--icon ICON] [--title TITLE]",
            "add msix <aumid> [--id ID] [--name NAME] [--match-process PROC]",
            "remove <agent_id> [--force]",
            "show <agent_id>",
            "hide <agent_id>",
            "release <agent_id>",
            "reclaim [<agent_id> | --all]",
            "move <agent_id> <index|first|last|left|right>",
            "open <agent_id> [--new]",
            "discover [--apply]",
            "config get [key]",
            "config set <key> <value>",
            "dock start|stop|reload|expand|collapse|clear-notifications",
            "update check|apply",
            "schema",
        ],
        "ui_keys": {k: t.__name__ for k, t in _UI_TYPES.items()},
        "default_ui": DEFAULT_UI,
    }
    return schema, json.dumps(schema, ensure_ascii=False, indent=2)


class _ArgParseError(Exception):
    """Raised on argparse validation error so --json mode can emit structured JSON."""


class _JsonAwareArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgParseError(message)


def build_parser() -> argparse.ArgumentParser:
    common = _JsonAwareArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="以结构化 JSON 输出（推荐 AI Agent 调用）",
    )
    common.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="指定配置文件路径（默认项目根目录 agents.json）",
    )
    common.add_argument(
        "--no-notify",
        action="store_true",
        default=argparse.SUPPRESS,
        help="修改配置后不向运行中的悬浮窗发送热重载信号",
    )

    parser = _JsonAwareArgumentParser(
        prog="ai-docker",
        description="AI docker CLI：面向自动化与 AI Agent 的悬浮窗控制接口",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"ai-docker {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", parents=[common], help="列出已配置的 Agent")
    p_list.add_argument("--all", action="store_true", help="包含已隐藏的 Agent")
    p_list.add_argument("--running", action="store_true", help="仅列出当前运行中的 Agent")
    p_list.set_defaults(handler=_cmd_list)

    p_status = sub.add_parser("status", parents=[common], help="查看悬浮窗运行状态、UI 配置与打开窗口")
    p_status.set_defaults(handler=_cmd_status)

    p_add = sub.add_parser("add", parents=[common], help="添加新 Agent (web / exe / cli / msix)")
    add_sub = p_add.add_subparsers(dest="add_type", required=True)

    p_add_web = add_sub.add_parser("web", parents=[common], help="添加网页 Agent")
    p_add_web.add_argument("url", help="网页 URL（支持带路径与查询参数）")
    p_add_web.add_argument("--id", help="自定义 Agent ID")
    p_add_web.add_argument("--name", help="显示名称（留空自动从域名提取）")
    p_add_web.add_argument("--match-title", help="浏览器窗口标题匹配关键字")
    p_add_web.add_argument("--icon", help="自定义图标 URL 或本地路径")
    p_add_web.add_argument("--browser", help="指定浏览器可执行文件路径")
    p_add_web.add_argument("--no-app-mode", action="store_true", help="使用默认浏览器标签页打开而非独立应用窗口")
    p_add_web.set_defaults(handler=_cmd_add)

    p_add_exe = add_sub.add_parser("exe", parents=[common], help="添加桌面应用 (.exe / .lnk)")
    p_add_exe.add_argument("path", help="本地 .exe 或 .lnk 路径")
    p_add_exe.add_argument("--id", help="自定义 Agent ID")
    p_add_exe.add_argument("--name", help="显示名称")
    p_add_exe.add_argument("--match-process", help="覆盖匹配的进程文件名")
    p_add_exe.add_argument("--no-match-path", action="store_true", help="关闭安装路径严格匹配")
    p_add_exe.add_argument("--args", nargs="*", help="启动附加参数")
    p_add_exe.set_defaults(handler=_cmd_add)

    p_add_cli = add_sub.add_parser("cli", parents=[common], help="添加终端 CLI Agent")
    p_add_cli.add_argument("cli_command", metavar="command", help="启动命令（如 claude / gemini / opencode）")
    p_add_cli.add_argument("--id", help="自定义 Agent ID")
    p_add_cli.add_argument("--name", help="显示名称")
    p_add_cli.add_argument("--title", help="终端窗口标题")
    p_add_cli.add_argument("--cwd", help="默认工作目录")
    p_add_cli.add_argument("--icon", help="图标来源（官网 URL / AUMID / 本地路径）")
    p_add_cli.set_defaults(handler=_cmd_add)

    p_add_msix = add_sub.add_parser("msix", parents=[common], help="添加 Windows 商店 / MSIX Agent")
    p_add_msix.add_argument("aumid", help="PackageFamilyName!AppId")
    p_add_msix.add_argument("--id", help="自定义 Agent ID")
    p_add_msix.add_argument("--name", help="显示名称")
    p_add_msix.add_argument("--match-process", help="匹配的进程名（如 Claude.exe）")
    p_add_msix.set_defaults(handler=_cmd_add)

    p_rm = sub.add_parser("remove", parents=[common], help="从配置中删除指定 Agent")
    p_rm.add_argument("agent_id", help="Agent ID")
    p_rm.add_argument("--force", action="store_true", help="允许删除最后一个可见 Agent")
    p_rm.set_defaults(handler=_cmd_remove)

    p_show = sub.add_parser("show", parents=[common], help="在悬浮窗显示指定 Agent")
    p_show.add_argument("agent_id", help="Agent ID")
    p_show.set_defaults(handler=lambda a, p, n: _cmd_visibility(a, p, n, True))

    p_hide = sub.add_parser("hide", parents=[common], help="在悬浮窗隐藏指定 Agent")
    p_hide.add_argument("agent_id", help="Agent ID")
    p_hide.set_defaults(handler=lambda a, p, n: _cmd_visibility(a, p, n, False))

    p_rel = sub.add_parser("release", parents=[common], help="释放指定 Agent 回 Windows 系统任务栏")
    p_rel.add_argument("agent_id", help="Agent ID")
    p_rel.set_defaults(handler=lambda a, p, n: _cmd_taskbar_release(a, p, n, True))

    p_rec = sub.add_parser("reclaim", parents=[common], help="将已释放的 Agent 重新收进悬浮窗")
    p_rec.add_argument("agent_id", nargs="?", help="Agent ID（配合 --all 时可省略）")
    p_rec.add_argument("--all", action="store_true", help="全部重新收进悬浮窗")
    p_rec.set_defaults(handler=lambda a, p, n: _cmd_taskbar_release(a, p, n, False))

    p_move = sub.add_parser("move", parents=[common], help="调整可见 Agent 图标顺序")
    p_move.add_argument("agent_id", help="Agent ID")
    p_move.add_argument("position", help="目标索引 (0..N-1) 或 first / last / left / right")
    p_move.set_defaults(handler=_cmd_move)

    p_open = sub.add_parser("open", parents=[common], help="唤醒或启动指定 Agent")
    p_open.add_argument("agent_id", help="Agent ID")
    p_open.add_argument("--new", action="store_true", help="强制新开一个实例/窗口")
    p_open.set_defaults(handler=_cmd_open)

    p_disc = sub.add_parser("discover", parents=[common], help="扫描本机未收录的 AI Agent")
    p_disc.add_argument("--apply", action="store_true", help="直接将扫描结果写入配置并热重载")
    p_disc.set_defaults(handler=_cmd_discover)

    p_cfg = sub.add_parser("config", parents=[common], help="读取或修改 ui 全局配置")
    cfg_sub = p_cfg.add_subparsers(dest="config_action", required=True)
    p_cfg_get = cfg_sub.add_parser("get", parents=[common], help="获取 ui 配置")
    p_cfg_get.add_argument("key", nargs="?", help="可选配置键名")
    p_cfg_get.set_defaults(handler=_cmd_config)
    p_cfg_set = cfg_sub.add_parser("set", parents=[common], help="设置 ui 配置")
    p_cfg_set.add_argument("key", help="配置键名（如 theme / auto_collapse / auto_collapse_seconds）")
    p_cfg_set.add_argument("value", help="新值")
    p_cfg_set.set_defaults(handler=_cmd_config)

    p_dock = sub.add_parser("dock", parents=[common], help="控制运行中的悬浮窗进程")
    p_dock.add_argument(
        "dock_action",
        choices=("start", "stop", "reload", "expand", "collapse", "clear-notifications"),
        help="控制动作",
    )
    p_dock.set_defaults(handler=_cmd_dock)

    p_upd = sub.add_parser("update", parents=[common], help="检查或应用版本更新")
    p_upd.add_argument("update_action", choices=("check", "apply"), help="check 检查更新 / apply 一键更新")
    p_upd.set_defaults(handler=_cmd_update)

    p_schema = sub.add_parser("schema", parents=[common], help="输出完整 CLI 命令与配置 Schema（便于 AI 自发现）")
    p_schema.set_defaults(handler=_cmd_schema)

    return parser


def _wants_json_before_double_dash(raw_argv: list[str]) -> bool:
    for token in raw_argv:
        if token == "--":
            break
        if token == "--json":
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    if argv is None:
        env_argv = os.environ.pop("AI_DOCKER_ARGV_JSON", None)
        if env_argv is not None:
            try:
                decoded = json.loads(env_argv)
            except ValueError:
                decoded = None
            if isinstance(decoded, list):
                raw_argv = [str(item) for item in decoded]
            elif isinstance(decoded, str):
                raw_argv = [decoded]
            else:
                raw_argv = list(sys.argv[1:])
        else:
            raw_argv = list(sys.argv[1:])
    else:
        raw_argv = list(argv)
    want_json_early = _wants_json_before_double_dash(raw_argv)

    try:
        args = parser.parse_args(raw_argv)
    except _ArgParseError as exc:
        if want_json_early:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "command": "",
                        "code": "invalid_arguments",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            parser.print_usage(sys.stderr)
            print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        return int(exc.code or 0)

    as_json = bool(getattr(args, "json", False))
    cfg_path = Path(getattr(args, "config", CONFIG_PATH))
    notify = not bool(getattr(args, "no_notify", False))
    cmd_name = getattr(args, "command", "")

    try:
        data, text = args.handler(args, cfg_path, notify)
    except ConfigLoadError as exc:
        err = CliError(f"配置文件加载失败：{exc}", code="config_error")
        if as_json:
            print(json.dumps({"ok": False, "command": cmd_name, "code": err.code, "error": err.message}, ensure_ascii=False))
        else:
            print(f"错误: {err.message}", file=sys.stderr)
        return err.exit_code
    except CliError as exc:
        if as_json:
            print(json.dumps({"ok": False, "command": cmd_name, "code": exc.code, "error": exc.message}, ensure_ascii=False))
        else:
            print(f"错误: {exc.message}", file=sys.stderr)
        return exc.exit_code

    if as_json:
        print(json.dumps({"ok": True, "command": cmd_name, "data": data}, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
