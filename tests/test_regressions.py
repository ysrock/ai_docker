from __future__ import annotations

import sqlite3
import sys
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ai_docker.config import ConfigLoadError, load_config
from ai_docker.notifications import NotificationTracker


class NotificationTests(unittest.TestCase):
    def test_latest_toast_follows_database_order_and_reveals_surviving_alerts(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "wpn.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE NotificationHandler(RecordId INT, PrimaryId TEXT);
                    CREATE TABLE Notification(Id INT, HandlerId INT, Type TEXT, Payload TEXT);
                    INSERT INTO NotificationHandler VALUES (1, 'alpha'), (2, 'bravo');
                """)
            tracker = NotificationTracker(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    INSERT INTO Notification VALUES
                    (1, 1, 'toast', '<toast><text>First workspace</text></toast>'),
                    (2, 2, 'toast', '<toast><text>Bravo message</text></toast>'),
                    (3, 1, 'toast', '<toast><text>Second workspace</text></toast>');
                """)
            agents = [
                {"id": "alpha", "notification_handlers": ["alpha"]},
                {"id": "bravo", "notification_handlers": ["bravo"]},
            ]
            windows = {
                "alpha": [{"hwnd": 1, "project": "First"}, {"hwnd": 2, "project": "Second"}],
                "bravo": [{"hwnd": 3}],
            }
            summaries = tracker.poll(agents, windows, 0)
            alpha = summaries["alpha"]
            self.assertEqual((alpha.total_count, alpha.latest_text), (2, "Second workspace"))
            self.assertGreater(alpha.latest_event_order, summaries["bravo"].latest_event_order)

            tracker.clear_window(2)
            summaries = tracker.poll(agents, windows, 0)
            alpha = summaries["alpha"]
            self.assertEqual((alpha.total_count, alpha.latest_text), (1, "First workspace"))
            self.assertLess(alpha.latest_event_order, summaries["bravo"].latest_event_order)

    def test_title_count_stays_dismissed_until_changed(self):
        with TemporaryDirectory() as directory:
            tracker = NotificationTracker(Path(directory) / "absent.sqlite")
            agents = [{"id": "editor"}]
            window = {"hwnd": 42, "title": "(3) Project"}
            windows = {"editor": [window]}
            self.assertEqual(tracker.poll(agents, windows, 0)["editor"].total_count, 3)
            tracker.clear_agent("editor", [42])
            self.assertFalse(tracker.poll(agents, windows, 0)["editor"].has_alert)

            window["title"] = "(4) Project"
            self.assertEqual(tracker.poll(agents, windows, 0)["editor"].total_count, 4)
            self.assertFalse(tracker.poll(agents, windows, 42)["editor"].has_alert)
            self.assertFalse(tracker.poll(agents, windows, 0)["editor"].has_alert)

            window["title"] = "Project"
            tracker.poll(agents, windows, 0)
            window["title"] = "(4) Project"
            self.assertEqual(tracker.poll(agents, windows, 0)["editor"].total_count, 4)

    def test_decreasing_title_or_badge_count_does_not_realert(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "wpn.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE NotificationHandler(RecordId INT, PrimaryId TEXT);
                    CREATE TABLE Notification(Id INT, HandlerId INT, Type TEXT, Payload TEXT);
                    INSERT INTO NotificationHandler VALUES (1, 'chat');
                    INSERT INTO Notification VALUES (1, 1, 'badge', '<badge value="3"/>');
                """)
            tracker = NotificationTracker(path)
            agents = [{"id": "chat", "notification_handlers": ["chat"]}]
            window = {"hwnd": 77, "title": "(3) Chat"}
            windows = {"chat": [window]}

            self.assertEqual(tracker.poll(agents, windows, 0)["chat"].total_count, 3)
            tracker.clear_agent("chat", [77])
            self.assertFalse(tracker.poll(agents, windows, 0)["chat"].has_alert)

            # Reading one message elsewhere drops count 3 -> 2; must NOT re-alert
            window["title"] = "(2) Chat"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("""UPDATE Notification SET Payload = '<badge value="2"/>' WHERE Id = 1""")
            self.assertFalse(tracker.poll(agents, windows, 0)["chat"].has_alert)

            # Receiving a new message bumps count 2 -> 3; MUST re-alert
            window["title"] = "(3) Chat"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("""UPDATE Notification SET Payload = '<badge value="3"/>' WHERE Id = 1""")
            self.assertEqual(tracker.poll(agents, windows, 0)["chat"].total_count, 3)

    def test_multi_window_agent_clears_unattributed_alert_on_focus_without_clearing_other_window(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "wpn.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE NotificationHandler(RecordId INT, PrimaryId TEXT);
                    CREATE TABLE Notification(Id INT, HandlerId INT, Type TEXT, Payload TEXT);
                    INSERT INTO NotificationHandler VALUES (1, 'ide');
                """)
            tracker = NotificationTracker(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    INSERT INTO Notification VALUES
                    (1, 1, 'toast', '<toast><text>General update ready</text></toast>'),
                    (2, 1, 'toast', '<toast><text>Beta build done</text></toast>'),
                    (3, 1, 'badge', '<badge value="2"/>');
                """)
            agents = [{"id": "ide", "notification_handlers": ["ide"]}]
            windows = {
                "ide": [
                    {"hwnd": 10, "project": "Alpha", "title": "Alpha - IDE"},
                    {"hwnd": 20, "project": "Beta", "title": "Beta - IDE"},
                ]
            }
            s1 = tracker.poll(agents, windows, 0)["ide"]
            self.assertTrue(s1.has_alert)
            self.assertEqual(s1.total_count, 2)

            # Focusing window 10 clears unattributed agent toast & badge, but preserves Beta's window toast
            s2 = tracker.poll(agents, windows, 10)["ide"]
            self.assertTrue(s2.has_alert)
            self.assertEqual(s2.total_count, 1)
            self.assertIn(20, s2.by_hwnd)
            self.assertNotIn(10, s2.by_hwnd)

            # Focusing window 20 clears the remaining Beta window toast
            s3 = tracker.poll(agents, windows, 20)["ide"]
            self.assertFalse(s3.has_alert)


class ConfigTests(unittest.TestCase):
    def test_config_rejects_bool_oversized_and_invalid_agent_fields(self):
        import json

        bad_payloads = [
            {"ui": {"icon_size": True}, "agents": []},
            {"ui": {"button_size": 99999}, "agents": []},
            {"ui": {"opacity": True}, "agents": []},
            {"ui": {"auto_collapse_seconds": False}, "agents": []},
            {"ui": {"pos": [True, 10]}, "agents": []},
            {"ui": {}, "agents": [{"id": "a", "name": "A", "type": "exe", "args": "--bad"}]},
            {"ui": {}, "agents": [{"id": "a", "name": "A", "type": "cli", "command": 123}]},
        ]
        with TemporaryDirectory() as directory:
            for idx, payload in enumerate(bad_payloads):
                p = Path(directory) / f"bad_{idx}.json"
                p.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ConfigLoadError, msg=f"Payload #{idx} should fail"):
                    load_config(p)

            good = Path(directory) / "good.json"
            good.write_text(
                json.dumps(
                    {
                        "ui": {"icon_size": 32, "button_size": 46, "auto_collapse_seconds": 8, "pos": [100, 80]},
                        "agents": [{"id": "code", "name": "VS Code", "type": "exe", "args": ["--reuse-window"]}],
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_config(good)
            self.assertEqual(loaded["ui"]["icon_size"], 32)
            self.assertEqual(loaded["agents"][0]["args"], ["--reuse-window"])


@unittest.skipUnless(sys.platform == "win32", "Windows integration")
class WindowsTests(unittest.TestCase):
    def test_discovered_installations_match_distinct_process_paths(self):
        from ai_docker import discovery, launcher

        installs = [(r"C:\First\Code.exe", "Visual Studio Code"), (r"D:\Second\Code.exe", "VS Code")]
        with (
            patch.object(discovery, "_scan_shortcuts", return_value=installs),
            patch.object(discovery, "_scan_program_dirs", return_value=[]),
            patch.object(discovery, "_scan_msix_packages", return_value=[]),
            patch.object(discovery.shutil, "which", return_value=None),
        ):
            first, second = discovery.discover_new_agents([])
        windows = [
            {"hwnd": 1, "process": "Code.exe", "process_path": "c:/FIRST/code.exe", "title": "First project"},
            {"hwnd": 2, "process": "Code.exe", "process_path": r"D:\Second\Code.exe", "title": "Second project"},
        ]
        self.assertEqual(launcher.match_agent_window(first, windows), 1)
        self.assertEqual(launcher.match_agent_window(second, windows), 2)
        self.assertEqual(len(launcher.match_agent_windows({**first, "match_path": False}, windows)), 2)

        squirrel_agent = {
            "id": "sq",
            "name": "SquirrelApp",
            "type": "exe",
            "path": r"C:\Apps\SquirrelApp\SquirrelApp.exe",
            "match_process": "SquirrelApp.exe",
        }
        squirrel_windows = [
            {
                "hwnd": 11,
                "process": "SquirrelApp.exe",
                "process_path": r"C:\Apps\SquirrelApp\app-2.3.4\SquirrelApp.exe",
                "title": "Workspace",
            },
            {
                "hwnd": 12,
                "process": "SquirrelApp.exe",
                "process_path": r"D:\Other\SquirrelApp\SquirrelApp.exe",
                "title": "Other",
            },
        ]
        self.assertEqual(launcher.match_agent_window(squirrel_agent, squirrel_windows), 11)

    def test_taskbar_only_restores_eligible_tracked_windows(self):
        import win32con as flags
        import win32gui as win32

        from ai_docker import taskbar

        def create_window(style=0, owner=0):
            hwnd = win32.CreateWindowEx(style, "Static", "Taskbar test", flags.WS_POPUP, 0, 0, 20, 20, owner, 0, 0, None)
            self.addCleanup(win32.DestroyWindow, hwnd)
            return hwnd

        main = create_window()
        dialog = create_window(owner=main)
        explicit = create_window(flags.WS_EX_APPWINDOW, main)
        tool = create_window(flags.WS_EX_TOOLWINDOW)
        background = create_window(flags.WS_EX_NOACTIVATE)
        native = MagicMock()
        with (
            patch.object(win32, "FindWindow", return_value=1),
            patch.object(taskbar.pythoncom, "CoCreateInstance", return_value=native),
        ):
            hider = taskbar.TaskbarHider()
            hider.sync_hidden_windows({main, dialog, explicit, tool, background})
            self.assertCountEqual([call.args[0] for call in native.DeleteTab.call_args_list], [main, explicit])
            self.assertFalse(hider.show_window(dialog))
            native.AddTab.side_effect = [OSError("temporary failure"), None, None]
            self.assertEqual(hider.restore_all(), 1)
            self.assertEqual(hider.hidden_count, 1)
            self.assertEqual(hider.restore_all(), 1)
            self.assertEqual(hider.hidden_count, 0)
            self.assertNotIn(dialog, [call.args[0] for call in native.AddTab.call_args_list])

    def test_dock_keeps_responding_during_web_icon_download_and_ignores_stale_result(self):
        from PIL import Image
        from PySide6.QtCore import QThreadPool, QTimer
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window, icons
        from ai_docker.config import DEFAULT_UI
        from ai_docker.notifications import AgentNotificationSummary

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        agent = {"id": "web", "name": "Web", "type": "web", "url": "https://fixture.invalid/"}
        config = {"ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": False}, "agents": [agent]}
        worker_started = threading.Event()
        release_worker = threading.Event()
        calls = []
        timer_ticks = []

        def wait_until(predicate):
            deadline = time.monotonic() + 3
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            self.assertTrue(predicate())

        with TemporaryDirectory() as directory:
            cached = Path(directory) / "web.png"
            Image.new("RGBA", (64, 64), "red").save(cached)

            def fake_fetch(url, force):
                calls.append((url, force, threading.get_ident()))
                if not force:
                    worker_started.set()
                    release_worker.wait(3)
                return cached

            with (
                patch.object(icons, "fetch_web_icon_to_png", side_effect=fake_fetch),
                patch.object(dock_window, "load_config", return_value=config),
                patch.object(dock_window, "TaskbarHider"),
                patch.object(dock_window, "NotificationTracker"),
                patch.object(dock_window, "get_agent_window_map", return_value={}),
            ):
                dock = dock_window.AgentDockWindow()
                dock._taskbar_hider.hidden_count = 0
                notifications = {"web": AgentNotificationSummary(has_alert=True, latest_event_order=1)}
                dock._notif_tracker.poll.return_value = notifications
                try:
                    initial_icon = dock._icons["web"].cacheKey()
                    QTimer.singleShot(0, lambda: timer_ticks.append(True))
                    wait_until(lambda: worker_started.is_set() and timer_ticks)
                    self.assertEqual(initial_icon, dock._icons["web"].cacheKey())
                    self.assertNotEqual(calls[0][2], threading.get_ident())

                    dock._rebuild_dock(force_refresh_icons=True)
                    dock._last_notif_map = notifications
                    placeholder = dock._icons["web"].cacheKey()
                    ready = MagicMock()
                    dock._web_icons.ready.connect(ready)
                    self.assertEqual(len(calls), 1)
                    release_worker.set()
                    wait_until(lambda: dock._icons["web"].cacheKey() != placeholder)
                    self.assertEqual([force for _, force, _ in calls], [False, True])
                    ready.assert_called_once_with(agent["url"])
                    self.assertEqual(dock._orb._icon.cacheKey(), dock._icons["web"].cacheKey())
                finally:
                    release_worker.set()
                    dock.close()
                    QThreadPool.globalInstance().waitForDone(3000)

    def test_manual_collapse_cooldown_startup_clamp_and_worker_exception_recovery(self):
        from PySide6.QtCore import QPoint, QThreadPool
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window, icons
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        screen = app.primaryScreen()
        geo = screen.availableGeometry()
        right_edge = [geo.right() - 50, geo.top() + 40]
        agents = [
            {"id": f"a{i}", "name": f"Agent {i}", "type": "exe", "path": "", "match_process": f"a{i}.exe"}
            for i in range(6)
        ]
        config = {
            "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": True, "pos": right_edge},
            "agents": agents,
        }

        with (
            patch.object(dock_window, "load_config", return_value=config),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value={}),
        ):
            dock = dock_window.AgentDockWindow()
            dock._status_timer.stop()
            dock._collapse_timer.stop()
            try:
                # Startup expanded window must clamp inside screen while preserving right-edge anchor
                self.assertEqual(dock._collapsed_anchor, QPoint(right_edge[0], right_edge[1]))
                self.assertLessEqual(dock.x() + dock.width(), geo.right() + 1)

                # Manual collapse sets brief cooldown so immediate hover from closing menu does not bounce open
                dock._collapse_dock(force=True)
                self.assertTrue(dock._collapsed)
                self.assertEqual(dock.pos(), QPoint(right_edge[0], right_edge[1]))
                dock._orb.hovered.emit()
                self.assertTrue(dock._collapsed)

                # Once cooldown clears (e.g., mouse left or timer elapsed), hovering expands immediately
                dock._expand_cooldown_until = 0.0
                dock._orb.hovered.emit()
                self.assertFalse(dock._collapsed)
                self.assertLessEqual(dock.x() + dock.width(), geo.right() + 1)

                # Worker exception other than OSError must still release _in_flight
                bad_url = "https://boom.invalid/"
                with patch.object(icons, "fetch_web_icon_to_png", side_effect=RuntimeError("unexpected")):
                    dock._web_icons.resolve(bad_url)
                    QThreadPool.globalInstance().waitForDone(3000)
                    app.processEvents()
                    self.assertNotIn(bad_url, dock._web_icons._in_flight)
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)

    def test_closing_discover_dialog_keeps_tool_dock_alive_and_expands(self):
        from PySide6.QtCore import QThreadPool, QTimer
        from PySide6.QtWidgets import QApplication

        from ai_docker import dialogs, dock_window
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        prev_quit_setting = app.quitOnLastWindowClosed()
        app.setQuitOnLastWindowClosed(False)
        discovered = [
            {
                "id": "visual-studio-code",
                "name": "Visual Studio Code",
                "type": "exe",
                "path": r"C:\Apps\Code.exe",
                "match_process": "Code.exe",
                "enabled": True,
            }
        ]
        config = {
            "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": True},
            "agents": [{"id": "base", "name": "Base", "type": "exe", "path": "", "match_process": "base.exe"}],
        }

        with (
            patch.object(dock_window, "load_config", return_value=config),
            patch.object(dock_window, "save_config"),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value={}),
            patch.object(dialogs, "discover_new_agents", return_value=discovered),
        ):
            dock = dock_window.AgentDockWindow()
            dock._taskbar_hider.hidden_count = 0
            dock._notif_tracker.poll.return_value = {}
            dock._status_timer.stop()
            dock._collapse_timer.stop()
            dock.show()
            dock._collapse_dock(force=True)

            def open_and_accept():
                def accept_active():
                    for w in app.topLevelWidgets():
                        if isinstance(w, dialogs.DiscoverAgentsDialog) and w.isVisible():
                            w._on_confirm()
                            return

                QTimer.singleShot(40, accept_active)
                dock._open_discover_dialog()
                # If closing the modal dialog accidentally triggered QApplication.quit(), exit(42) will never run
                QTimer.singleShot(80, lambda: app.exit(42))

            try:
                QTimer.singleShot(20, open_and_accept)
                rc = app.exec()
                self.assertEqual(rc, 42, "Closing modal dialog must NOT quit the Qt.Tool dock app")
                self.assertIn("visual-studio-code", dock._buttons)
                self.assertFalse(dock._collapsed, "Adding an agent from dialog should expand the collapsed orb")
            finally:
                dock.close()
                app.setQuitOnLastWindowClosed(prev_quit_setting)
                QThreadPool.globalInstance().waitForDone(3000)

    def test_context_menus_and_submenus_use_themed_apple_glass(self):
        from PySide6.QtCore import QThreadPool, Qt
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window
        from ai_docker.config import DEFAULT_UI
        from ai_docker.theme import AppleGlassMenu

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        for theme in ("dark", "light"):
            config = {
                "ui": {**DEFAULT_UI, "theme": theme, "hide_from_taskbar": False, "auto_collapse": False},
                "agents": [
                    {"id": "a1", "name": "Agent 1", "type": "exe", "path": "", "match_process": "a1.exe"},
                    {"id": "a2", "name": "Agent 2", "type": "exe", "path": "", "match_process": "a2.exe"},
                ],
            }
            with (
                patch.object(dock_window, "load_config", return_value=config),
                patch.object(dock_window, "TaskbarHider"),
                patch.object(dock_window, "NotificationTracker"),
                patch.object(dock_window, "get_agent_window_map", return_value={}),
            ):
                dock = dock_window.AgentDockWindow()
                dock._status_timer.stop()
                try:
                    def check_menu_tree(m):
                        self.assertIsInstance(m, AppleGlassMenu)
                        self.assertEqual(m._theme, theme)
                        self.assertTrue(m.testAttribute(Qt.WA_TranslucentBackground))
                        self.assertTrue(bool(m.windowFlags() & Qt.FramelessWindowHint))
                        for act in m.actions():
                            if act.menu() is not None:
                                check_menu_tree(act.menu())

                    gmenu = dock._build_global_menu()
                    amenu = dock._build_agent_menu(config["agents"][0])
                    top_submenus = [a.text() for a in gmenu.actions() if a.menu() is not None]
                    self.assertEqual(top_submenus, ["Agent 管理", "显示与行为", "配置与系统"])
                    check_menu_tree(gmenu)
                    check_menu_tree(amenu)
                    gmenu.deleteLater()
                    amenu.deleteLater()
                finally:
                    dock.close()
                    QThreadPool.globalInstance().waitForDone(3000)
