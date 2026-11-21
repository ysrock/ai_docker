from __future__ import annotations

import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from itertools import count
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
    latest_event_order: int = 0


@dataclass(frozen=True)
class _Toast:
    count: int = 0
    order: int = 0
    text: str = ""


_NO_TOAST = _Toast()


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
        self._event_orders = count(1)
        self._flashing_hwnds: dict[int, int] = {}
        self._window_toasts: dict[int, _Toast] = {}
        self._agent_toasts: dict[str, _Toast] = {}
        self._title_alerts: dict[int, tuple[int, int]] = {}
        self._dismissed_title_counts: dict[int, int] = {}
        self._badge_alerts: dict[str, tuple[int, int]] = {}
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
        if hwnd and hwnd != foreground_hwnd and hwnd not in self._flashing_hwnds:
            self._flashing_hwnds[hwnd] = next(self._event_orders)

    def clear_window(self, hwnd: int, agent_id: str | None = None) -> None:
        self._flashing_hwnds.pop(hwnd, None)
        self._window_toasts.pop(hwnd, None)
        title_count, _ = self._title_alerts.get(hwnd, (0, 0))
        if title_count:
            self._dismissed_title_counts[hwnd] = title_count
        if agent_id:
            self.clear_agent(agent_id)

    def clear_agent_unattributed(self, agent_id: str) -> None:
        """Clear agent-level unattributed toasts and dismiss current Windows badge without clearing per-window alerts."""
        self._agent_toasts.pop(agent_id, None)
        cur_badge, _ = self._badge_alerts.get(agent_id, (0, 0))
        if cur_badge > 0:
            self._dismissed_badge_counts[agent_id] = cur_badge

    def clear_agent(self, agent_id: str, hwnds: list[int] | None = None) -> None:
        self.clear_agent_unattributed(agent_id)
        for hwnd in hwnds or ():
            self.clear_window(hwnd)

    def _poll_wpn_db(
        self,
        agents: list[dict[str, Any]],
        agent_windows: dict[str, list[dict[str, Any]]],
        foreground_hwnd: int,
    ) -> dict[str, int] | None:
        """Poll new toasts since watermark and active badge rows. Returns active badge counts per agent."""
        active_badges: dict[str, int] = {}
        conn = self._connect_ro()
        if conn is None:
            return None

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
                order = next(self._event_orders)
                notified_windows: set[int] = set()

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
                        if target_hwnd not in notified_windows:
                            previous = self._window_toasts.get(target_hwnd, _NO_TOAST)
                            self._window_toasts[target_hwnd] = _Toast(previous.count + 1, order, text)
                            notified_windows.add(target_hwnd)
                    else:
                        previous = self._agent_toasts.get(aid, _NO_TOAST)
                        self._agent_toasts[aid] = _Toast(previous.count + 1, order, text)
        except Exception:
            return None
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
        # Title counters and badges are snapshots. Changes seen in the same poll share an order.
        snapshot_order = next(self._event_orders)
        live_hwnds: set[int] = set()
        foreground_agents: set[str] = set()
        for aid, wins in agent_windows.items():
            for w in wins:
                h = w["hwnd"]
                live_hwnds.add(h)
                title_count = parse_title_unread_count(w.get("title", ""))
                old_count, old_order = self._title_alerts.get(h, (0, 0))
                if title_count:
                    order = snapshot_order if title_count > old_count else old_order
                    self._title_alerts[h] = title_count, order
                else:
                    self._title_alerts.pop(h, None)
                dismissed_title = self._dismissed_title_counts.get(h)
                if dismissed_title is not None:
                    if title_count == 0 or title_count > dismissed_title:
                        self._dismissed_title_counts.pop(h, None)
                    elif title_count < dismissed_title:
                        self._dismissed_title_counts[h] = title_count

                if h == foreground_hwnd:
                    self.clear_window(h)
                    foreground_agents.add(aid)

        for states in (self._flashing_hwnds, self._window_toasts, self._title_alerts, self._dismissed_title_counts):
            for h in states.keys() - live_hwnds:
                del states[h]

        active_badges = self._poll_wpn_db(agents, agent_windows, foreground_hwnd)
        if active_badges is not None:
            for aid in self._badge_alerts.keys() - active_badges.keys():
                del self._badge_alerts[aid]
            for aid, badge_count in active_badges.items():
                old_count, old_order = self._badge_alerts.get(aid, (0, 0))
                order = snapshot_order if badge_count > old_count else old_order
                self._badge_alerts[aid] = badge_count, order
            for aid, dismissed_count in list(self._dismissed_badge_counts.items()):
                cur_badge = active_badges.get(aid, 0)
                if cur_badge == 0 or cur_badge > dismissed_count:
                    del self._dismissed_badge_counts[aid]
                elif cur_badge < dismissed_count:
                    self._dismissed_badge_counts[aid] = cur_badge
        for aid in foreground_agents:
            self.clear_agent_unattributed(aid)

        summaries: dict[str, AgentNotificationSummary] = {}
        for agent in agents:
            if agent.get("enabled", True) is False:
                continue
            aid = agent.get("id", "")
            wins = agent_windows.get(aid, [])
            summary = AgentNotificationSummary()

            win_total = 0
            any_flash = False
            agent_toast = self._agent_toasts.get(aid, _NO_TOAST)
            latest_toast = agent_toast
            event_order = agent_toast.order

            for w in wins:
                h = w["hwnd"]
                title_count, title_order = self._title_alerts.get(h, (0, 0))
                if self._dismissed_title_counts.get(h) == title_count:
                    title_count = title_order = 0
                toast = self._window_toasts.get(h, _NO_TOAST)
                flash_order = self._flashing_hwnds.get(h, 0)
                w_cnt = max(title_count, toast.count)

                if w_cnt > 0 or flash_order:
                    summary.by_hwnd[h] = WindowNotificationState(
                        count=w_cnt,
                        flashing=bool(flash_order),
                        latest_text=toast.text,
                    )
                    win_total += w_cnt
                    any_flash |= bool(flash_order)
                    event_order = max(event_order, title_order, toast.order, flash_order)
                    if toast.order > latest_toast.order:
                        latest_toast = toast

            badge_count, badge_order = self._badge_alerts.get(aid, (0, 0))
            if self._dismissed_badge_counts.get(aid) == badge_count:
                badge_count = badge_order = 0
            total = max(win_total + agent_toast.count, badge_count)

            # If an agent has a single open window and an agent-level alert/badge, mirror it onto that window too
            if len(wins) == 1 and (agent_toast.count > 0 or badge_count > 0):
                sole_h = wins[0]["hwnd"]
                cur_state = summary.by_hwnd.get(sole_h, WindowNotificationState())
                cur_state.count = max(cur_state.count, total)
                if latest_toast.text and not cur_state.latest_text:
                    cur_state.latest_text = latest_toast.text
                summary.by_hwnd[sole_h] = cur_state

            summary.total_count = total
            summary.has_alert = total > 0 or any_flash
            summary.latest_text = latest_toast.text
            summary.latest_event_order = max(event_order, badge_order)
            summaries[aid] = summary

        return summaries
