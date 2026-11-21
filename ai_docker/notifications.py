from __future__ import annotations

import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WPN_DB_PATH = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft"
    / "Windows"
    / "Notifications"
    / "wpndatabase.db"
)

TITLE_COUNT_RE = re.compile(r"^\s*(?:[\(\[【]\s*(\d+)\s*[\)\]】])\s*")


@dataclass
class WindowNotificationState:
    count: int = 0
    flashing: bool = False
    latest_text: str = ""

    @property
    def has_alert(self) -> bool:
        return self.count > 0 or self.flashing


@dataclass
class AgentNotificationSummary:
    total_count: int = 0
    has_alert: bool = False
    latest_text: str = ""
    by_hwnd: dict[int, WindowNotificationState] = field(default_factory=dict)


def parse_title_unread_count(title: str) -> int:
    """Extract leading unread count like '(3) Grok' or '[2] WorkBuddy' from window title."""
    m = TITLE_COUNT_RE.match(title or "")
    if not m:
        return 0
    try:
        return max(0, int(m.group(1)))
    except ValueError:
        return 0


def _extract_toast_text(payload: bytes | str | None) -> str:
    if not payload:
        return ""
    raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    try:
        root = ET.fromstring(raw)
        texts = [el.text.strip() for el in root.iter() if el.tag.endswith("text") and el.text and el.text.strip()]
        return " - ".join(texts)
    except Exception:
        return raw[:120]


def _extract_badge_count(payload: bytes | str | None) -> int:
    if not payload:
        return 0
    raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    try:
        root = ET.fromstring(raw)
        val = (root.attrib.get("value") or "").strip().lower()
        if val.isdigit():
            return int(val)
        if val in ("alert", "attention", "newmessage", "activity"):
            return 1
        return 0
    except Exception:
        return 0


class NotificationTracker:
    """Tracks real-time notification badges per Agent and per open window HWND.

    Combines:
      1. Windows Shell Hook HSHELL_FLASH events (exact HWND flashing on taskbar)
      2. Windows Push Notification DB (wpndatabase.db) live new toasts & active badges
      3. Window title leading unread counters such as '(3) Grok'
    """

    def __init__(self, db_path: Path = WPN_DB_PATH) -> None:
        self._db_path = db_path
        self._last_toast_id: int = self._read_max_notification_id()
        self._flashing_hwnds: set[int] = set()
        self._window_toast_counts: dict[int, int] = {}
        self._window_toast_texts: dict[int, str] = {}
        self._agent_toast_counts: dict[str, int] = {}
        self._agent_toast_texts: dict[str, str] = {}
        self._last_active_badges: dict[str, int] = {}
        self._dismissed_badge_counts: dict[str, int] = {}

    def _connect_ro(self) -> sqlite3.Connection | None:
        if not self._db_path.exists():
            return None
        try:
            return sqlite3.connect(
                f"file:{self._db_path}?mode=ro", uri=True, timeout=0.4
            )
        except Exception:
            return None

    def _read_max_notification_id(self) -> int:
        conn = self._connect_ro()
        if conn is None:
            return 0
        try:
            row = conn.execute("SELECT COALESCE(MAX(Id), 0) FROM Notification").fetchone()
            return int(row[0]) if row and row[0] else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def on_shell_flash(self, hwnd: int, foreground_hwnd: int = 0) -> None:
        if hwnd and hwnd != foreground_hwnd:
            self._flashing_hwnds.add(hwnd)

    def clear_window(self, hwnd: int, agent_id: str | None = None) -> None:
        self._flashing_hwnds.discard(hwnd)
        self._window_toast_counts.pop(hwnd, None)
        self._window_toast_texts.pop(hwnd, None)
        if agent_id:
            self._agent_toast_counts.pop(agent_id, None)
            self._agent_toast_texts.pop(agent_id, None)
            cur_badge = self._last_active_badges.get(agent_id, 0)
            if cur_badge > 0:
                self._dismissed_badge_counts[agent_id] = cur_badge

    def clear_agent(self, agent_id: str, hwnds: list[int] | None = None) -> None:
        self._agent_toast_counts.pop(agent_id, None)
        self._agent_toast_texts.pop(agent_id, None)
        cur_badge = self._last_active_badges.get(agent_id, 0)
        if cur_badge > 0:
            self._dismissed_badge_counts[agent_id] = cur_badge
        if hwnds:
            for h in hwnds:
                self._flashing_hwnds.discard(h)
                self._window_toast_counts.pop(h, None)
                self._window_toast_texts.pop(h, None)

    def _poll_wpn_db(
        self,
        agents: list[dict[str, Any]],
        agent_windows: dict[str, list[dict[str, Any]]],
        foreground_hwnd: int,
    ) -> dict[str, int]:
        """Poll new toasts since watermark and active badge rows. Returns active badge counts per agent."""
        active_badges: dict[str, int] = {}
        conn = self._connect_ro()
        if conn is None:
            return active_badges

        # Map lowercase handler PrimaryId -> list of matching agents
        handler_to_agents: dict[str, list[dict[str, Any]]] = {}
        for agent in agents:
            if agent.get("enabled", True) is False:
                continue
            for hid in agent.get("notification_handlers", []):
                handler_to_agents.setdefault(hid.lower(), []).append(agent)

        try:
            # 1. Read active badge notifications
            badge_rows = conn.execute(
                """
                SELECT h.PrimaryId, n.Payload
                FROM Notification n
                JOIN NotificationHandler h ON n.HandlerId = h.RecordId
                WHERE n.Type = 'badge'
                """
            ).fetchall()
            for primary_id, payload in badge_rows:
                matched_agents = handler_to_agents.get(str(primary_id).lower(), [])
                if not matched_agents:
                    continue
                cnt = _extract_badge_count(payload)
                if cnt > 0:
                    for ag in matched_agents:
                        aid = ag.get("id", "")
                        active_badges[aid] = max(active_badges.get(aid, 0), cnt)

            # 2. Self-heal watermark if Windows recreated/purged Notification DB
            max_row = conn.execute("SELECT COALESCE(MAX(Id), 0) FROM Notification").fetchone()
            db_max_id = int(max_row[0]) if max_row and max_row[0] else 0
            if db_max_id < self._last_toast_id:
                self._last_toast_id = db_max_id

            # 3. Read newly arrived toast notifications since watermark
            toast_rows = conn.execute(
                """
                SELECT n.Id, h.PrimaryId, n.Payload
                FROM Notification n
                JOIN NotificationHandler h ON n.HandlerId = h.RecordId
                WHERE n.Type = 'toast' AND n.Id > ?
                ORDER BY n.Id ASC
                """,
                (self._last_toast_id,),
            ).fetchall()

            for nid, primary_id, payload in toast_rows:
                self._last_toast_id = max(self._last_toast_id, int(nid))
                matched_agents = handler_to_agents.get(str(primary_id).lower(), [])
                if not matched_agents:
                    continue
                text = _extract_toast_text(payload)
                text_low = text.lower()

                for ag in matched_agents:
                    aid = ag.get("id", "")
                    # Optional keyword filter for shared handlers like Chrome / WindowsTerminal
                    keywords = ag.get("notification_keywords", [])
                    if keywords and not any(k.lower() in text_low for k in keywords):
                        continue

                    wins = agent_windows.get(aid, [])
                    target_hwnd = self._match_toast_to_window(text_low, wins)

                    # Do not badge if user is already focused on the target window
                    if target_hwnd and target_hwnd == foreground_hwnd:
                        continue
                    if not target_hwnd and any(w["hwnd"] == foreground_hwnd for w in wins) and len(wins) == 1:
                        continue

                    if target_hwnd:
                        self._window_toast_counts[target_hwnd] = (
                            self._window_toast_counts.get(target_hwnd, 0) + 1
                        )
                        if text:
                            self._window_toast_texts[target_hwnd] = text
                    else:
                        self._agent_toast_counts[aid] = self._agent_toast_counts.get(aid, 0) + 1
                        if text:
                            self._agent_toast_texts[aid] = text
        except Exception:
            pass
        finally:
            conn.close()

        return active_badges

    @staticmethod
    def _match_toast_to_window(toast_text_low: str, wins: list[dict[str, Any]]) -> int | None:
        if not wins:
            return None
        if len(wins) == 1:
            return wins[0]["hwnd"]

        # Prefer matching a specific open window by project name
        best_hwnd: int | None = None
        best_len = 0
        for w in wins:
            proj = (w.get("project") or "").strip().lower()
            if len(proj) >= 2 and proj in toast_text_low and len(proj) > best_len:
                best_len = len(proj)
                best_hwnd = w["hwnd"]
        return best_hwnd

    def poll(
        self,
        agents: list[dict[str, Any]],
        agent_windows: dict[str, list[dict[str, Any]]],
        foreground_hwnd: int,
    ) -> dict[str, AgentNotificationSummary]:
        """Refresh and aggregate notifications for all agents and their open windows."""
        # Prune closed HWNDs & auto-clear currently focused window
        all_live_hwnds: set[int] = set()
        for aid, wins in agent_windows.items():
            for w in wins:
                h = w["hwnd"]
                all_live_hwnds.add(h)
                if h == foreground_hwnd:
                    self.clear_window(h, agent_id=aid if len(wins) == 1 else None)

        self._flashing_hwnds.intersection_update(all_live_hwnds)
        for h in list(self._window_toast_counts.keys()):
            if h not in all_live_hwnds:
                self._window_toast_counts.pop(h, None)
                self._window_toast_texts.pop(h, None)

        active_badges = self._poll_wpn_db(agents, agent_windows, foreground_hwnd)
        self._last_active_badges = dict(active_badges)
        # Clear dismissed marker when badge count drops to 0 or changes
        for aid, dismissed_val in list(self._dismissed_badge_counts.items()):
            cur_val = active_badges.get(aid, 0)
            if cur_val == 0 or cur_val != dismissed_val:
                self._dismissed_badge_counts.pop(aid, None)

        summaries: dict[str, AgentNotificationSummary] = {}
        for agent in agents:
            if agent.get("enabled", True) is False:
                continue
            aid = agent.get("id", "")
            wins = agent_windows.get(aid, [])
            summary = AgentNotificationSummary()

            win_total = 0
            any_flash = False
            latest_text = self._agent_toast_texts.get(aid, "")

            for w in wins:
                h = w["hwnd"]
                title_cnt = parse_title_unread_count(w.get("title", ""))
                toast_cnt = self._window_toast_counts.get(h, 0)
                flashing = h in self._flashing_hwnds
                w_cnt = max(title_cnt, toast_cnt)
                w_text = self._window_toast_texts.get(h, "")

                if w_cnt > 0 or flashing:
                    summary.by_hwnd[h] = WindowNotificationState(
                        count=w_cnt,
                        flashing=flashing,
                        latest_text=w_text,
                    )
                    win_total += w_cnt
                    if flashing:
                        any_flash = True
                    if w_text and not latest_text:
                        latest_text = w_text

            agent_unassigned = self._agent_toast_counts.get(aid, 0)
            raw_badge = active_badges.get(aid, 0)
            badge_cnt = 0 if raw_badge and self._dismissed_badge_counts.get(aid) == raw_badge else raw_badge
            total = max(win_total + agent_unassigned, badge_cnt)

            # If an agent has a single open window and an agent-level alert/badge, mirror it onto that window too
            if len(wins) == 1 and (agent_unassigned > 0 or badge_cnt > 0):
                sole_h = wins[0]["hwnd"]
                cur_state = summary.by_hwnd.get(sole_h, WindowNotificationState())
                cur_state.count = max(cur_state.count, total)
                if latest_text and not cur_state.latest_text:
                    cur_state.latest_text = latest_text
                summary.by_hwnd[sole_h] = cur_state

            summary.total_count = total
            summary.has_alert = total > 0 or any_flash
            summary.latest_text = latest_text
            summaries[aid] = summary

        return summaries
