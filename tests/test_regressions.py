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
from ai_docker.updater import UpdateCheckResult, check_for_updates, launch_updater_script


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

            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT INTO Notification VALUES (4, 1, 'toast', '<toast><text>New general update</text></toast>')")
            self.assertEqual(tracker.poll(agents, windows, 20)["ide"].total_count, 1)
            self.assertEqual(tracker.poll(agents, windows, 0)["ide"].total_count, 1)

    def test_window_alert_recency_combines_toast_flash_and_title_and_refreshes_on_reflash(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "wpn.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE NotificationHandler(RecordId INT, PrimaryId TEXT);
                    CREATE TABLE Notification(Id INT, HandlerId INT, Type TEXT, Payload TEXT);
                    INSERT INTO NotificationHandler VALUES (1, 'ide');
                    INSERT INTO Notification VALUES
                    (1, 1, 'toast', '<toast><text>Alpha build finished</text></toast>');
                """)
            tracker = NotificationTracker(path)
            agents = [{"id": "ide", "notification_handlers": ["ide"]}]
            windows = {
                "ide": [
                    {"hwnd": 10, "project": "Alpha", "title": "Alpha - IDE"},
                    {"hwnd": 20, "project": "Beta", "title": "Beta - IDE"},
                    {"hwnd": 30, "project": "Gamma", "title": "Gamma - IDE"},
                ]
            }
            # 1. Toast on Alpha (10) arrives first, then Beta (20) flashes, then Gamma (30) gets title count
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT INTO Notification VALUES (2, 1, 'toast', '<toast><text>Alpha task ready</text></toast>')")
            tracker.poll(agents, windows, 0)
            tracker.on_shell_flash(20, foreground_hwnd=0)
            windows["ide"][2]["title"] = "(2) Gamma - IDE"

            summary = tracker.poll(agents, windows, 0)["ide"]
            self.assertEqual(summary.alert_hwnds_by_recency, [30, 20, 10])
            self.assertEqual(tracker.get_alert_hwnds(), [30, 20, 10])
            self.assertLess(
                summary.by_hwnd[10].latest_event_order,
                summary.by_hwnd[20].latest_event_order,
            )
            self.assertLess(
                summary.by_hwnd[20].latest_event_order,
                summary.by_hwnd[30].latest_event_order,
            )

            # 2. Re-flashing an already alerting window (10) promotes it to newest
            tracker.on_shell_flash(10, foreground_hwnd=0)
            self.assertEqual(tracker.get_alert_hwnds(), [10, 30, 20])
            summary = tracker.poll(agents, windows, 0)["ide"]
            self.assertEqual(summary.alert_hwnds_by_recency, [10, 30, 20])

            # 3. Clearing the newest window reveals the next newest alerting window
            tracker.clear_window(10)
            self.assertEqual(tracker.get_alert_hwnds(), [30, 20])
            summary = tracker.poll(agents, windows, 0)["ide"]
            self.assertEqual(summary.alert_hwnds_by_recency, [30, 20])


class ConfigTests(unittest.TestCase):
    def test_config_rejects_bool_oversized_and_invalid_agent_fields(self):
        import json

        bad_payloads = [
            {"ui": {"icon_size": True}, "agents": []},
            {"ui": {"button_size": 99999}, "agents": []},
            {"ui": {"opacity": True}, "agents": []},
            {"ui": {"auto_collapse_seconds": False}, "agents": []},
            {"ui": {"orientation": "diagonal"}, "agents": []},
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
                        "ui": {
                            "icon_size": 32,
                            "button_size": 46,
                            "auto_collapse_seconds": 8,
                            "orientation": "VERTICAL",
                            "pos": [100, 80],
                        },
                        "agents": [
                            {"id": "code", "name": "VS Code", "type": "exe", "args": ["--reuse-window", 42]},
                            {"id": "other", "type": "exe", "args": None},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_config(good)
            self.assertEqual(loaded["ui"]["icon_size"], 32)
            self.assertEqual(loaded["ui"]["orientation"], "vertical")
            self.assertEqual(loaded["agents"][0]["args"], ["--reuse-window", "42"])
            if sys.platform == "win32":
                from ai_docker.launcher import _build_exe_launch_args

                self.assertEqual(_build_exe_launch_args(loaded["agents"][1], force_new=False), [])


class UpdaterTests(unittest.TestCase):
    def test_check_for_updates_and_update_ps1_preserves_local_config(self):
        import os
        import shutil
        import subprocess

        if not shutil.which("git"):
            self.skipTest("git not available")

        def run(cmd, cwd):
            res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=True)
            return res.stdout.strip()

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(check_for_updates(root).status, "not_git")

            bare = root / "origin.git"
            bare.mkdir()
            run(["git", "init", "--bare", "-b", "main"], bare)

            upstream = root / "upstream"
            run(["git", "clone", str(bare), str(upstream)], root)
            run(["git", "config", "user.email", "test@example.com"], upstream)
            run(["git", "config", "user.name", "Tester"], upstream)
            (upstream / ".gitignore").write_text("agents.json\n", encoding="utf-8")
            (upstream / "app.txt").write_text("v1\n", encoding="utf-8")
            shutil.copy2(Path(__file__).resolve().parent.parent / "update.ps1", upstream / "update.ps1")
            run(["git", "add", "."], upstream)
            run(["git", "commit", "-m", "init v1"], upstream)
            run(["git", "push", "-u", "origin", "main"], upstream)

            clone = root / "user_clone"
            run(["git", "clone", str(bare), str(clone)], root)
            run(["git", "remote", "rename", "origin", "build/cache"], clone)
            local_cfg = clone / "agents.json"
            local_cfg.write_text('{"ui": {"theme": "light"}, "agents": []}', encoding="utf-8")

            r0 = check_for_updates(clone)
            self.assertEqual(r0.status, "up_to_date")
            self.assertEqual(r0.behind_count, 0)
            self.assertFalse(r0.has_local_changes)

            # Push a new commit upstream
            (upstream / "app.txt").write_text("v2\n", encoding="utf-8")
            run(["git", "commit", "-am", "feat: upgrade to v2"], upstream)
            run(["git", "push", "origin", "main"], upstream)

            r1 = check_for_updates(clone)
            self.assertEqual(r1.status, "update_available")
            self.assertEqual(r1.remote_ref, "build/cache/main")
            self.assertEqual(r1.behind_count, 1)
            self.assertFalse(r1.has_local_changes)
            self.assertTrue(any("feat: upgrade to v2" in c for c in r1.commits))

            nested = clone / "standalone-copy"
            nested.mkdir()
            shutil.copy2(clone / "update.ps1", nested / "update.ps1")
            self.assertEqual(check_for_updates(nested).status, "not_git")

            # Tracked local edit must be flagged
            (clone / "app.txt").write_text("local edit\n", encoding="utf-8")
            r_dirty = check_for_updates(clone)
            self.assertTrue(r_dirty.has_local_changes)
            run(["git", "checkout", "--", "app.txt"], clone)

            # Execute real update.ps1 -NoLaunch on the cloned repo (and verify UTF-8 BOM for PS 5.1)
            update_script = Path(__file__).resolve().parent.parent / "update.ps1"
            self.assertTrue(
                update_script.read_bytes().startswith(b"\xef\xbb\xbf"),
                "update.ps1 must include UTF-8 BOM so Windows PowerShell 5.1 decodes Chinese text",
            )
            if sys.platform == "win32":
                shell = shutil.which("powershell") or shutil.which("pwsh") or "powershell.exe"

                def run_update(directory, env=None, preview=None):
                    args = ["-NoLaunch"]
                    if preview is not None:
                        args.extend([
                            "-PreviewBranch", preview.branch,
                            "-LocalRevision", preview.local_sha,
                            "-TargetRevision", preview.remote_sha,
                        ])
                    return subprocess.run(
                        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(directory / "update.ps1"), *args],
                        cwd=str(directory),
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )

                nested_update = run_update(nested)
                self.assertNotEqual(nested_update.returncode, 0)
                self.assertEqual((clone / "app.txt").read_text(encoding="utf-8").strip(), "v1")

                proc = run_update(clone, preview=r1)
                self.assertEqual(proc.returncode, 0, f"update.ps1 failed: {proc.stdout}\n{proc.stderr}")
                self.assertEqual((clone / "app.txt").read_text(encoding="utf-8").strip(), "v2")
                self.assertEqual(local_cfg.read_text(encoding="utf-8"), '{"ui": {"theme": "light"}, "agents": []}')
                self.assertEqual(check_for_updates(clone).status, "up_to_date")

                (upstream / "uv.lock").write_text("fixture\n", encoding="utf-8")
                run(["git", "add", "uv.lock"], upstream)
                run(["git", "commit", "-m", "change dependencies"], upstream)
                run(["git", "push", "origin", "main"], upstream)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                (bin_dir / "uv.cmd").write_text(
                    '@echo off\n'
                    'echo %*>>"%~dp0calls.txt"\n'
                    'if exist "%~dp0allow-sync" exit /b 0\n'
                    'exit /b 42\n',
                    encoding="ascii",
                )
                env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
                failed_sync = run_update(clone, env)
                self.assertNotEqual(failed_sync.returncode, 0)
                self.assertTrue((clone / "uv.lock").exists())
                marker = clone / ".git" / "ai-docker-sync-pending"
                self.assertTrue(marker.is_file())
                (bin_dir / "allow-sync").touch()
                retry = run_update(clone, env)
                self.assertEqual(retry.returncode, 0, f"retry failed: {retry.stdout}\n{retry.stderr}")
                self.assertEqual((bin_dir / "calls.txt").read_text().splitlines(), ["sync --locked", "sync --locked"])
                self.assertFalse(marker.exists())

            # Diverged branch (local ahead + remote behind) must set ahead_count > 0 and can_fast_forward=False
            run(["git", "config", "user.email", "user@example.com"], clone)
            run(["git", "config", "user.name", "LocalUser"], clone)
            (clone / "local_only.txt").write_text("local\n", encoding="utf-8")
            run(["git", "add", "local_only.txt"], clone)
            run(["git", "commit", "-m", "local commit"], clone)
            (upstream / "app.txt").write_text("v3\n", encoding="utf-8")
            run(["git", "commit", "-am", "feat: v3"], upstream)
            run(["git", "push", "origin", "main"], upstream)
            r_div = check_for_updates(clone)
            self.assertEqual(r_div.status, "update_available")
            self.assertEqual(r_div.ahead_count, 1)
            self.assertEqual(r_div.behind_count, 1)
            self.assertFalse(r_div.can_fast_forward)

            if sys.platform == "win32":
                protected = root / "protected_clone"
                run(["git", "clone", str(bare), str(protected)], root)
                protected_cfg = protected / "agents.json"
                protected_cfg.write_bytes(b'{"agents": []}\n')
                protected_sha = run(["git", "rev-parse", "HEAD"], protected)
                run(["git", "commit", "--allow-empty", "-m", "previewed update"], upstream)
                run(["git", "push", "origin", "main"], upstream)
                preview = check_for_updates(protected)
                self.assertEqual(preview.status, "update_available")
                run(["git", "commit", "--allow-empty", "-m", "published after preview"], upstream)
                run(["git", "push", "origin", "main"], upstream)
                stale_preview = run_update(protected, preview=preview)
                self.assertNotEqual(stale_preview.returncode, 0)
                self.assertEqual(run(["git", "rev-parse", "HEAD"], protected), protected_sha)

                preview = check_for_updates(protected)
                old_dock = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.25)"])
                spawn = subprocess.Popen
                workers = []

                def track_worker(*args, **kwargs):
                    process = spawn(*args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
                    workers.append(process)
                    return process

                try:
                    with patch("ai_docker.updater.subprocess.Popen", side_effect=track_worker):
                        ok, message = launch_updater_script(
                            wait_pid=old_dock.pid, repo_root=protected, gui_mode=False, update=preview
                        )
                    self.assertTrue(ok, message)
                    self.assertEqual(workers[0].wait(timeout=20), 0)
                finally:
                    old_dock.wait(timeout=5)
                    for worker in workers:
                        if worker.poll() is None:
                            worker.terminate()
                            worker.wait(timeout=5)
                protected_sha = run(["git", "rev-parse", "HEAD"], protected)
                self.assertEqual(protected_sha, preview.remote_sha)
                (upstream / "agents.json").write_bytes(b'{"agents": [null]}\n')
                run(["git", "add", "-f", "agents.json"], upstream)
                run(["git", "commit", "-m", "add conflicting config"], upstream)
                run(["git", "push", "origin", "main"], upstream)
                blocked = run_update(protected)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("agents.json", blocked.stdout + blocked.stderr)
                self.assertEqual(run(["git", "rev-parse", "HEAD"], protected), protected_sha)
                self.assertEqual(protected_cfg.read_bytes(), b'{"agents": []}\n')


@unittest.skipUnless(sys.platform == "win32", "Windows integration")
class WindowsTests(unittest.TestCase):
    def test_shutdown_skips_queued_icon_and_update_jobs(self):
        from PySide6.QtCore import QObject, QRunnable, QThreadPool
        from PySide6.QtWidgets import QApplication

        from ai_docker import icons, updater

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        pool = QThreadPool.globalInstance()
        self.assertTrue(pool.waitForDone(3000))
        thread_count = pool.maxThreadCount()
        pool.setMaxThreadCount(1)
        release = threading.Event()

        class Blocker(QRunnable):
            def run(self):
                release.wait(4)

        parent = QObject()
        loader = icons.WebIconLoader(parent)
        checker = updater.UpdateChecker(parent)
        try:
            with patch.object(icons, "fetch_web_icon_to_png") as fetch, patch.object(updater, "check_for_updates") as check:
                pool.start(Blocker())
                loader.resolve("https://fixture.invalid/")
                checker.check_async()
                loader.invalidate()
                checker.invalidate()
                release.set()
                self.assertTrue(pool.waitForDone(3000))
                app.processEvents()
                fetch.assert_not_called()
                check.assert_not_called()
        finally:
            release.set()
            pool.waitForDone(3000)
            pool.setMaxThreadCount(thread_count)

    def test_failed_config_save_leaves_live_taskbar_unchanged(self):
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        config = {"ui": dict(DEFAULT_UI), "agents": [{"id": "editor", "name": "Editor", "type": "exe"}]}
        with (
            patch.object(dock_window, "load_config", return_value=config),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "save_config", side_effect=OSError("fixture")) as save,
            patch.object(dock_window.QMessageBox, "warning"),
        ):
            dock = dock_window.AgentDockWindow()
            try:
                with patch.object(dock, "_refresh_state") as refresh:
                    dock._toggle_hide_from_taskbar(False)
                    dock._set_agent_released("editor", True)
                    self.assertIs(dock.config, config)
                    self.assertNotIn("hide_from_taskbar", dock._buttons["editor"].agent)
                    refresh.assert_not_called()
                    dock._taskbar_hider.restore_all.assert_not_called()

                    save.side_effect = None
                    dock._toggle_hide_from_taskbar(False)
                    self.assertFalse(dock.config["ui"]["hide_from_taskbar"])
                    self.assertTrue(config["ui"]["hide_from_taskbar"])
                    refresh.assert_called_once()
                    dock._taskbar_hider.restore_all.assert_called_once()
            finally:
                dock.close()

    def test_multicolumn_menu_highlight_stays_in_the_selected_column(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication

        from ai_docker.theme import AppleGlassMenu

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        menu = AppleGlassMenu()
        for index in range(90):
            menu.addAction(f"Agent {index:02}").setCheckable(True)
        try:
            menu.popup(QPoint(0, 0))
            app.processEvents()
            first = menu.actionGeometry(menu.actions()[0])
            selected = next(action for action in menu.actions() if menu.actionGeometry(action).left() > first.left())
            second = menu.actionGeometry(selected)
            menu.setActiveAction(None)
            before = menu.grab()
            menu.setActiveAction(selected)
            after = menu.grab()

            def color(pixmap, x, y):
                ratio = pixmap.devicePixelRatio()
                return pixmap.toImage().pixel(round(x * ratio), round(y * ratio))

            y = second.center().y()
            self.assertNotEqual(color(before, second.right() - 10, y), color(after, second.right() - 10, y))
            self.assertEqual(color(before, first.right() - 10, y), color(after, first.right() - 10, y))
        finally:
            menu.close()

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

        exact = {**squirrel_agent, "id": "exact", "path": squirrel_windows[0]["process_path"]}
        with patch.object(launcher, "list_visible_windows", return_value=squirrel_windows):
            self.assertEqual(set(launcher.get_agent_window_map([squirrel_agent, exact])), {"exact"})
        unrelated = {**squirrel_windows[0], "process_path": r"C:\Apps\SquirrelApp\app-dev\SquirrelApp.exe"}
        with patch.object(launcher._executable_paths, "get", return_value=None) as lookup:
            self.assertIsNone(launcher.match_agent_window(squirrel_agent, [unrelated]))
            lookup.reset_mock()
            self.assertIsNone(launcher.match_agent_window({**squirrel_agent, "match_title": "absent"}, [unrelated]))
            lookup.assert_not_called()

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

            def fake_fetch(url, force, *, cancelled=None):
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

    def test_silent_update_check_updates_menu_and_dialog_guards_dirty_tree(self):
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication, QPushButton

        from ai_docker import dialogs, dock_window
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        config = {
            "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": False},
            "agents": [{"id": "a1", "name": "Agent 1", "type": "exe", "path": "", "match_process": "a1.exe"}],
        }
        with (
            patch.object(dock_window, "load_config", return_value=config),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value={}),
        ):
            dock = dock_window.AgentDockWindow()
            dock._status_timer.stop()
            dock._startup_update_timer.stop()
            try:
                fake = UpdateCheckResult(
                    status="update_available",
                    local_sha="1111111",
                    remote_sha="2222222",
                    branch="main",
                    behind_count=2,
                    commits=("2222222 feat: second", "1111112 fix: first"),
                    has_local_changes=False,
                    message="发现 2 个新提交",
                )
                dock._on_update_check_finished(fake, silent=True)
                gmenu = dock._build_global_menu()
                sys_act = next(a for a in gmenu.actions() if a.menu() is not None and "配置与系统" in a.text())
                self.assertIn("有 2 个新更新", sys_act.text())
                sys_items = [a.text() for a in sys_act.menu().actions() if not a.isSeparator()]
                self.assertTrue(any("发现新更新（2 条）" in t for t in sys_items))
                gmenu.deleteLater()

                # Dirty tree disables primary update button in UpdateDialog
                dirty_res = UpdateCheckResult(
                    status="update_available",
                    local_sha="1111111",
                    remote_sha="2222222",
                    branch="main",
                    behind_count=1,
                    commits=("2222222 feat: new",),
                    has_local_changes=True,
                )
                dlg = dialogs.UpdateDialog(dirty_res, theme="light", parent=dock)
                primary = dlg.findChild(QPushButton, "primaryBtn")
                self.assertIsNotNone(primary)
                self.assertFalse(primary.isEnabled())
                dlg.deleteLater()

                # Diverged branch (ahead > 0) also disables primary update button
                div_res = UpdateCheckResult(
                    status="update_available",
                    local_sha="1111111",
                    remote_sha="2222222",
                    branch="main",
                    behind_count=1,
                    ahead_count=2,
                    commits=("2222222 feat: new",),
                    has_local_changes=False,
                )
                dlg_div = dialogs.UpdateDialog(div_res, theme="dark", parent=dock)
                primary_div = dlg_div.findChild(QPushButton, "primaryBtn")
                self.assertIsNotNone(primary_div)
                self.assertFalse(primary_div.isEnabled())
                dlg_div.deleteLater()

                # Ampersands in dynamic project/agent names are escaped to avoid Qt mnemonics
                dock._agent_windows = {"a1": [{"hwnd": 55, "project": "R&D", "title": "R&D"}]}
                amenu = dock._build_agent_menu({"id": "a1", "name": "A&B", "type": "exe"})
                texts = [a.text() for a in amenu.actions() if not a.isSeparator()]
                self.assertIn("切换到窗口：R&&D", texts)
                self.assertIn("新开实例：A&&B", texts)
                amenu.deleteLater()
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)

    def test_add_web_agent_entry_dialog_and_url_drop(self):
        from PySide6.QtCore import QMimeData, QThreadPool, QUrl
        from PySide6.QtWidgets import QApplication

        from ai_docker import dialogs, dock_window
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])

        # URL normalization & smart naming
        sample_url = "https://quick.example.com/sn/account/000000/start/chat?conversationId=demo-123"
        self.assertEqual(dialogs.normalize_web_url(sample_url), sample_url)
        self.assertIsNone(dialogs.normalize_web_url("javascript:alert(1)"))
        self.assertIsNone(dialogs.normalize_web_url("not a valid url"))
        self.assertEqual(dialogs.suggest_web_agent_name(sample_url), "Quick")
        self.assertEqual(dialogs.suggest_web_agent_name("https://chat.deepseek.com/"), "Deepseek")
        self.assertEqual(dialogs.suggest_web_agent_name("https://www.kimi.com/"), "Kimi")

        with TemporaryDirectory() as directory:
            url_file = Path(directory) / "Quick.url"
            url_file.write_text(f"[InternetShortcut]\nURL={sample_url}\n", encoding="utf-8")
            self.assertEqual(dialogs.read_internet_shortcut_url(str(url_file)), sample_url)

            # Dialog opens directly on web tab, preserves query params, and adapts to Edge
            with patch.object(dialogs, "_find_browser", return_value=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
                dlg = dialogs.AddAgentDialog([], theme="light", initial_type="web", initial_url=sample_url)
                self.assertEqual(dlg._type_combo.currentIndex(), 1)
                self.assertEqual(dlg._url_edit.text(), sample_url)
                dlg._web_match_title_edit.setText("Quick Chat")
                dlg._web_icon_edit.setText("https://quick.example.com/logo.png")
                dlg._on_submit()
                created = dlg.created_agent
                self.assertIsNotNone(created)
                self.assertEqual(created["type"], "web")
                self.assertEqual(created["url"], sample_url)
                self.assertEqual(created["name"], "Quick")
                self.assertEqual(created["match_process"], "msedge.exe")
                self.assertEqual(created["notification_handlers"], ["MSEdge"])
                self.assertEqual(created["match_title"], "Quick Chat")
                self.assertEqual(created["icon_source"], "https://quick.example.com/logo.png")
                dlg.deleteLater()

            # Global menu exposes dedicated Web Agent entry & drop payload extracts browser URLs / .url files
            config = {
                "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": False},
                "agents": [{"id": "a1", "name": "Agent 1", "type": "exe", "path": "", "match_process": "a1.exe"}],
            }
            with (
                patch.object(dock_window, "load_config", return_value=config),
                patch.object(dock_window, "TaskbarHider"),
                patch.object(dock_window, "NotificationTracker"),
                patch.object(dock_window, "get_agent_window_map", return_value={}),
            ):
                dock = dock_window.AgentDockWindow()
                dock._status_timer.stop()
                dock._startup_update_timer.stop()
                try:
                    gmenu = dock._build_global_menu()
                    agent_act = next(a for a in gmenu.actions() if a.menu() is not None and a.text() == "Agent 管理")
                    agent_items = [a.text() for a in agent_act.menu().actions() if not a.isSeparator()]
                    self.assertIn("添加网页 Agent (URL)...", agent_items)
                    gmenu.deleteLater()

                    mime_web = QMimeData()
                    mime_web.setUrls([QUrl(sample_url)])
                    exes, dropped_url = dock._extract_drop_payload(mime_web)
                    self.assertEqual(exes, [])
                    self.assertEqual(dropped_url, sample_url)

                    mime_shortcut = QMimeData()
                    mime_shortcut.setUrls([QUrl.fromLocalFile(str(url_file))])
                    exes2, dropped_url2 = dock._extract_drop_payload(mime_shortcut)
                    self.assertEqual(exes2, [])
                    self.assertEqual(dropped_url2, sample_url)
                finally:
                    dock.close()
                    QThreadPool.globalInstance().waitForDone(3000)

    def test_cli_ipc_controls_live_dock_window(self):
        import ctypes
        from ctypes import wintypes

        import win32con
        import win32gui
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window, ipc
        from ai_docker.config import DEFAULT_UI

        # 1. Verify real Win32 property discovery + PostMessageW via native Win32 HWND
        test_prop = "AI docker test IPC prop"
        hwnd = win32gui.CreateWindowEx(
            0, "Static", "AI docker IPC test", win32con.WS_POPUP, 0, 0, 20, 20, 0, 0, 0, None
        )
        self.addCleanup(win32gui.DestroyWindow, hwnd)
        self.assertTrue(dock_window.user32.SetPropW(hwnd, test_prop, 1))
        self.addCleanup(dock_window.user32.RemovePropW, hwnd, test_prop)
        found = ipc.find_dock_window(test_prop)
        self.assertIsNotNone(found)
        self.assertEqual(found[0], hwnd)
        self.assertTrue(ipc.send_dock_command(ipc.IPC_CMD_RELOAD, test_prop))

        # 2. Verify AgentDockWindow.nativeEvent dispatches collapse / expand / silent reload
        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        config = {
            "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": False},
            "agents": [{"id": "a1", "name": "Agent 1", "type": "exe", "path": "", "match_process": "a1.exe"}],
        }
        with (
            patch.object(dock_window, "load_config", return_value=config),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value={}),
        ):
            dock = dock_window.AgentDockWindow()
            dock._taskbar_hider.hidden_count = 0
            dock._notif_tracker.poll.return_value = {}
            dock._status_timer.stop()
            dock._startup_update_timer.stop()
            dock._ipc_msg = 0xC123

            def dispatch_ipc(cmd: int) -> None:
                msg = wintypes.MSG()
                msg.message = dock._ipc_msg
                msg.wParam = cmd
                handled, _ = dock.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
                self.assertTrue(handled)
                app.processEvents()

            try:
                dispatch_ipc(ipc.IPC_CMD_COLLAPSE)
                self.assertTrue(dock._collapsed)

                dispatch_ipc(ipc.IPC_CMD_EXPAND)
                self.assertFalse(dock._collapsed)

                with patch.object(dock, "_reload_all") as mock_reload:
                    dispatch_ipc(ipc.IPC_CMD_RELOAD)
                    mock_reload.assert_called_once_with(force_refresh_icons=False, silent=True)
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)

    def test_multi_window_agent_click_focuses_newest_alerting_window_first(self):
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window, launcher
        from ai_docker.config import DEFAULT_UI

        # 1. Pure window-selection logic: preferred alerting order, closed fallback, foreground skip, cycling
        self.assertEqual(
            launcher.select_target_window(
                [10, 20, 30],
                foreground_hwnd=0,
                preferred_hwnds=[30, 10],
                is_iconic=lambda _h: False,
            ),
            30,
        )
        self.assertEqual(
            launcher.select_target_window(
                [10, 20, 30],
                foreground_hwnd=30,
                preferred_hwnds=[30, 10],
                is_iconic=lambda _h: False,
            ),
            10,
        )
        self.assertEqual(
            launcher.select_target_window(
                [10, 20, 30],
                foreground_hwnd=30,
                preferred_hwnds=[30, 10],
                is_iconic=lambda h: h == 30,
            ),
            30,
        )
        self.assertEqual(
            launcher.select_target_window(
                [10, 20, 30],
                foreground_hwnd=10,
                preferred_hwnds=[999],
                is_iconic=lambda _h: False,
            ),
            20,
        )

        # 2. End-to-end click sequence through AgentDockWindow._on_agent_clicked
        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        agent = {"id": "ide-multi", "name": "IDE Multi", "type": "exe", "path": "", "match_process": "ide.exe"}
        config = {
            "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": False, "focus_existing": True},
            "agents": [agent],
        }
        win_list = [
            {"hwnd": 10, "project": "P1", "title": "P1 - IDE"},
            {"hwnd": 20, "project": "P2", "title": "P2 - IDE"},
            {"hwnd": 30, "project": "P3", "title": "P3 - IDE"},
        ]
        focused_calls: list[int] = []
        fg_state = {"hwnd": 0}

        def fake_focus(hwnd: int) -> bool:
            focused_calls.append(hwnd)
            fg_state["hwnd"] = hwnd
            return True

        with TemporaryDirectory() as directory:
            tracker = NotificationTracker(Path(directory) / "absent.sqlite")
            with (
                patch.object(dock_window, "load_config", return_value=config),
                patch.object(dock_window, "TaskbarHider"),
                patch.object(dock_window, "NotificationTracker", return_value=tracker),
                patch.object(dock_window, "get_agent_window_map", return_value={"ide-multi": win_list}),
                patch.object(launcher, "get_agent_window_map", return_value={"ide-multi": win_list}),
                patch.object(launcher, "focus_hwnd", side_effect=fake_focus),
                patch.object(launcher.win32gui, "GetForegroundWindow", side_effect=lambda: fg_state["hwnd"]),
                patch.object(launcher.win32gui, "IsIconic", return_value=False),
            ):
                dock = dock_window.AgentDockWindow()
                dock._taskbar_hider.hidden_count = 0
                dock._status_timer.stop()
                dock._startup_update_timer.stop()
                try:
                    # Window 10 alerts first, then Window 30 alerts later
                    tracker.on_shell_flash(10, foreground_hwnd=0)
                    tracker.on_shell_flash(30, foreground_hwnd=0)
                    dock._refresh_state()

                    # First click jumps to newest alerting window (30) and preserves 10's alert
                    dock._on_agent_clicked(agent)
                    self.assertEqual(focused_calls[-1], 30)
                    self.assertEqual(tracker.get_alert_hwnds(), [10])

                    # Second click jumps to remaining alerting window (10) and clears all alerts
                    dock._on_agent_clicked(agent)
                    self.assertEqual(focused_calls[-1], 10)
                    self.assertEqual(tracker.get_alert_hwnds(), [])

                    # Third click (no alerts left) resumes normal multi-window cycling: 10 -> 20
                    dock._on_agent_clicked(agent)
                    self.assertEqual(focused_calls[-1], 20)
                finally:
                    launcher._last_focused_by_agent.pop("ide-multi", None)
                    dock.close()
                    QThreadPool.globalInstance().waitForDone(3000)

    def test_vertical_layout_aligns_window_strips_and_mirrors_on_right_edge(self):
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication, QBoxLayout

        from ai_docker import dock_window
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        screen = app.primaryScreen()
        geo = screen.availableGeometry()
        agents = [
            {"id": "a1", "name": "Agent 1", "type": "exe", "path": "", "match_process": "a1.exe"},
            {"id": "a2", "name": "Agent 2", "type": "exe", "path": "", "match_process": "a2.exe"},
            {"id": "a3", "name": "Agent 3", "type": "exe", "path": "", "match_process": "a3.exe"},
        ]
        win_map = {
            "a1": [
                {"hwnd": 101, "project": "Alpha", "title": "Alpha"},
                {"hwnd": 102, "project": "Beta", "title": "Beta"},
            ],
            "a3": [
                {"hwnd": 301 + idx, "project": f"Proj{idx}", "title": f"Proj{idx}"}
                for idx in range(7)
            ],
        }
        left_pos = [geo.left() + 24, geo.top() + 60]
        config = {
            "ui": {
                **DEFAULT_UI,
                "orientation": "vertical",
                "hide_from_taskbar": False,
                "auto_collapse": False,
                "window_chip_width": 106,
                "pos": left_pos,
            },
            "agents": agents,
        }

        with (
            patch.object(dock_window, "load_config", return_value=config),
            patch.object(dock_window, "save_config"),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value=win_map),
        ):
            dock = dock_window.AgentDockWindow()
            dock._taskbar_hider.hidden_count = 0
            dock._notif_tracker.poll.return_value = {}
            dock._status_timer.stop()
            dock._startup_update_timer.stop()
            try:
                dock.show()
                dock._refresh_state()
                app.processEvents()

                self.assertTrue(dock._is_vertical())
                self.assertFalse(dock._vertical_mirrored)
                self.assertEqual(dock._top_bar.row_layout.direction(), QBoxLayout.TopToBottom)
                self.assertTrue(dock._shelf.isVisible())

                strips = dock._shelf.findChildren(dock_window.AgentHorizontalStrip)
                self.assertEqual(len(strips), 2)
                btn_a1 = dock._buttons["a1"]
                btn_a3 = dock._buttons["a3"]

                y_btn_a1 = btn_a1.mapTo(dock, btn_a1.rect().center()).y()
                y_strip_a1 = strips[0].mapTo(dock, strips[0].rect().center()).y()
                self.assertLessEqual(abs(y_btn_a1 - y_strip_a1), 1)

                y_btn_a3 = btn_a3.mapTo(dock, btn_a3.rect().center()).y()
                y_strip_a3 = strips[1].mapTo(dock, strips[1].rect().center()).y()
                self.assertLessEqual(abs(y_btn_a3 - y_strip_a3), 1)
                self.assertGreater(dock._top_bar._slot_widgets["a3"].height(), 46)

                amenu = dock._build_agent_menu(agents[0])
                move_act = next(a for a in amenu.actions() if a.menu() is not None and a.text() == "调整位置")
                move_texts = [a.text() for a in move_act.menu().actions() if not a.isSeparator()]
                self.assertIn("向上移一位", move_texts)
                self.assertIn("移到最顶", move_texts)
                amenu.deleteLater()

                gmenu = dock._build_global_menu()
                beh_act = next(a for a in gmenu.actions() if a.menu() is not None and a.text() == "显示与行为")
                beh_texts = [a.text() for a in beh_act.menu().actions() if not a.isSeparator()]
                self.assertIn("布局方向", beh_texts)
                gmenu.deleteLater()

                # Switching back to horizontal restores top bar + bottom cards
                dock._set_orientation("horizontal")
                app.processEvents()
                self.assertFalse(dock._is_vertical())
                self.assertEqual(dock._top_bar.row_layout.direction(), QBoxLayout.LeftToRight)
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)

        # Right-edge vertical mirror keeps icon bar anchored on the right and expands strips leftward
        right_anchor = [geo.right() - 80, geo.top() + 80]
        mirrored_cfg = {
            "ui": {
                **DEFAULT_UI,
                "orientation": "vertical",
                "hide_from_taskbar": False,
                "auto_collapse": True,
                "window_chip_width": 106,
                "pos": right_anchor,
            },
            "agents": agents,
        }
        with (
            patch.object(dock_window, "load_config", return_value=mirrored_cfg),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value=win_map),
        ):
            dock = dock_window.AgentDockWindow()
            dock._taskbar_hider.hidden_count = 0
            dock._notif_tracker.poll.return_value = {}
            dock._status_timer.stop()
            dock._collapse_timer.stop()
            dock._startup_update_timer.stop()
            try:
                dock.show()
                dock._refresh_state()
                app.processEvents()

                self.assertTrue(dock._vertical_mirrored)
                order = [dock._root_layout.itemAt(i).widget() for i in range(dock._root_layout.count())]
                self.assertLess(order.index(dock._shelf), order.index(dock._top_bar))
                self.assertEqual(dock.x() + dock.width(), right_anchor[0] + dock._orb.width())

                dock._collapse_dock(force=True)
                self.assertTrue(dock._collapsed)
                self.assertEqual(dock.x(), right_anchor[0])
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)

    def test_window_context_menu_closes_window_and_preserves_other_alerts(self):
        import win32con
        import win32gui
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window, launcher
        from ai_docker.config import DEFAULT_UI

        hwnd = win32gui.CreateWindowEx(
            0, "Static", "Close test", win32con.WS_POPUP, 0, 0, 20, 20, 0, 0, 0, None
        )
        self.addCleanup(lambda: win32gui.IsWindow(hwnd) and win32gui.DestroyWindow(hwnd))
        self.assertFalse(launcher.close_hwnd(0))
        self.assertTrue(launcher.close_hwnd(hwnd))
        win32gui.PumpWaitingMessages()
        self.assertFalse(win32gui.IsWindow(hwnd))

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        agent = {"id": "ide-close", "name": "IDE Close", "type": "exe", "path": "", "match_process": "ide.exe"}
        config = {
            "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": False},
            "agents": [agent],
        }
        win_list = [
            {"hwnd": 10, "project": "P1", "title": "P1 - IDE"},
            {"hwnd": 20, "project": "P2", "title": "P2 - IDE"},
        ]
        with TemporaryDirectory() as directory:
            tracker = NotificationTracker(Path(directory) / "absent.sqlite")
            with (
                patch.object(dock_window, "load_config", return_value=config),
                patch.object(dock_window, "TaskbarHider"),
                patch.object(dock_window, "NotificationTracker", return_value=tracker),
                patch.object(dock_window, "get_agent_window_map", return_value={"ide-close": win_list}),
            ):
                dock = dock_window.AgentDockWindow()
                dock._taskbar_hider.hidden_count = 0
                dock._status_timer.stop()
                dock._startup_update_timer.stop()
                try:
                    tracker.on_shell_flash(10, foreground_hwnd=0)
                    tracker.on_shell_flash(20, foreground_hwnd=0)
                    dock._refresh_state()

                    menu = dock._build_agent_menu(agent, target_hwnd=10)
                    close_act = next((a for a in menu.actions() if a.text() == "关闭该窗口"), None)
                    self.assertIsNotNone(close_act)
                    self.assertTrue(close_act.property("destructive"))

                    with patch.object(dock_window, "close_hwnd", return_value=True) as mock_close:
                        close_act.trigger()
                    mock_close.assert_called_once_with(10)
                    self.assertEqual(tracker.get_alert_hwnds(), [20])

                    with (
                        patch.object(dock_window, "close_hwnd", return_value=False),
                        patch.object(dock_window.QMessageBox, "warning") as mock_warn,
                    ):
                        close_act.trigger()
                    mock_warn.assert_called_once()
                    self.assertEqual(tracker.get_alert_hwnds(), [20])
                    menu.deleteLater()
                finally:
                    dock.close()
                    QThreadPool.globalInstance().waitForDone(3000)

    def test_vertical_reload_anchor_cross_screen_rollback_and_max_cols(self):
        from PySide6.QtCore import QEvent, QPoint, QPointF, QThreadPool, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        screen = app.primaryScreen()
        geo = screen.availableGeometry()
        right_anchor = [geo.right() - 80, geo.top() + 80]
        agents = [
            {"id": "a1", "name": "Agent 1", "type": "exe", "path": "", "match_process": "a1.exe"},
            {"id": "a2", "name": "Agent 2", "type": "exe", "path": "", "match_process": "a2.exe"},
        ]
        win_map = {
            "a1": [
                {"hwnd": 101, "project": "Alpha", "title": "Alpha"},
                {"hwnd": 102, "project": "Beta", "title": "Beta"},
            ]
        }
        vert_cfg = {
            "ui": {
                **DEFAULT_UI,
                "orientation": "vertical",
                "hide_from_taskbar": False,
                "auto_collapse": False,
                "window_chip_width": 106,
                "pos": right_anchor,
            },
            "agents": agents,
        }
        horiz_cfg = {
            "ui": {**vert_cfg["ui"], "orientation": "horizontal"},
            "agents": agents,
        }

        with (
            patch.object(dock_window, "load_config", side_effect=[vert_cfg, horiz_cfg]),
            patch.object(dock_window, "save_config", side_effect=OSError("locked")),
            patch.object(dock_window.QMessageBox, "warning"),
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value=win_map),
        ):
            dock = dock_window.AgentDockWindow()
            dock._taskbar_hider.hidden_count = 0
            dock._notif_tracker.poll.return_value = {}
            dock._status_timer.stop()
            dock._startup_update_timer.stop()
            try:
                dock.show()
                dock._refresh_state()
                app.processEvents()

                # Narrow-screen / wide-chip guard allows shrinking below 4 columns
                self.assertEqual(dock._vertical_max_cols(geo.width()), 1)
                self.assertTrue(dock._vertical_mirrored)

                # Cross-screen drag rollback restores original position and mirrored layout if save fails
                origin = dock.pos()
                prev_anchor = dock._collapsed_anchor
                dock._drag_origin = origin
                dock._drag_offset = QPoint(0, 0)
                dock.move(geo.left() + 10, origin.y())
                release_evt = QMouseEvent(
                    QEvent.Type.MouseButtonRelease,
                    QPointF(5, 5),
                    QPointF(5, 5),
                    Qt.LeftButton,
                    Qt.NoButton,
                    Qt.NoModifier,
                )
                dock.mouseReleaseEvent(release_evt)
                self.assertTrue(dock._vertical_mirrored)
                self.assertEqual(dock._collapsed_anchor, prev_anchor)
                self.assertEqual(dock.pos(), origin)

                # Reloading from right-mirrored vertical to horizontal preserves right-edge anchor
                dock._reload_all(force_refresh_icons=False, silent=True)
                app.processEvents()
                self.assertFalse(dock._is_vertical())
                self.assertEqual(dock._collapsed_anchor, QPoint(right_anchor[0], right_anchor[1]))
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)

    def test_drag_grip_double_click_collapses_to_orb_without_saving_config(self):
        from PySide6.QtCore import QEvent, QPoint, QPointF, QThreadPool, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        from ai_docker import dock_window
        from ai_docker.config import DEFAULT_UI

        app = QApplication.instance() or QApplication(["regressions", "-platform", "offscreen"])
        screen = app.primaryScreen()
        geo = screen.availableGeometry()
        agents = [{"id": "a1", "name": "Agent 1", "type": "exe", "path": "", "match_process": "a1.exe"}]
        win_map = {"a1": [{"hwnd": 101, "project": "Alpha", "title": "Alpha"}]}

        def send_grip_dblclick(dock_obj: dock_window.AgentDockWindow) -> None:
            grip = dock_obj._grip
            local_pt = QPointF(grip.rect().center())
            global_pt = QPointF(grip.mapToGlobal(grip.rect().center()))
            for evt_type, btn, btns in (
                (QEvent.Type.MouseButtonPress, Qt.LeftButton, Qt.LeftButton),
                (QEvent.Type.MouseButtonRelease, Qt.LeftButton, Qt.NoButton),
                (QEvent.Type.MouseButtonDblClick, Qt.LeftButton, Qt.LeftButton),
                (QEvent.Type.MouseButtonRelease, Qt.LeftButton, Qt.NoButton),
            ):
                evt = QMouseEvent(evt_type, local_pt, global_pt, btn, btns, Qt.NoModifier)
                app.sendEvent(grip, evt)

        # 1. Horizontal mode: double-clicking DragGrip collapses immediately without writing config or bouncing open
        horiz_cfg = {
            "ui": {**DEFAULT_UI, "hide_from_taskbar": False, "auto_collapse": True, "pos": [geo.left() + 30, geo.top() + 40]},
            "agents": agents,
        }
        with (
            patch.object(dock_window, "load_config", return_value=horiz_cfg),
            patch.object(dock_window, "save_config") as save_mock,
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value=win_map),
        ):
            dock = dock_window.AgentDockWindow()
            dock._taskbar_hider.hidden_count = 0
            dock._notif_tracker.poll.return_value = {}
            dock._status_timer.stop()
            dock._collapse_timer.stop()
            dock._startup_update_timer.stop()
            try:
                dock.show()
                dock._refresh_state()
                app.processEvents()
                self.assertFalse(dock._collapsed)

                send_grip_dblclick(dock)
                self.assertTrue(dock._collapsed)
                self.assertTrue(dock._orb.isVisible())
                save_mock.assert_not_called()

                # Cooldown prevents immediate hover bounce
                dock._orb.hovered.emit()
                self.assertTrue(dock._collapsed)
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)

        # 2. Right-mirrored vertical mode: double-clicking DragGrip collapses to the right-edge anchor
        right_anchor = [geo.right() - 80, geo.top() + 80]
        vert_cfg = {
            "ui": {
                **DEFAULT_UI,
                "orientation": "vertical",
                "hide_from_taskbar": False,
                "auto_collapse": True,
                "window_chip_width": 106,
                "pos": right_anchor,
            },
            "agents": agents,
        }
        with (
            patch.object(dock_window, "load_config", return_value=vert_cfg),
            patch.object(dock_window, "save_config") as save_mock,
            patch.object(dock_window, "TaskbarHider"),
            patch.object(dock_window, "NotificationTracker"),
            patch.object(dock_window, "get_agent_window_map", return_value=win_map),
        ):
            dock = dock_window.AgentDockWindow()
            dock._taskbar_hider.hidden_count = 0
            dock._notif_tracker.poll.return_value = {}
            dock._status_timer.stop()
            dock._collapse_timer.stop()
            dock._startup_update_timer.stop()
            try:
                dock.show()
                dock._refresh_state()
                app.processEvents()
                self.assertTrue(dock._vertical_mirrored)

                send_grip_dblclick(dock)
                self.assertTrue(dock._collapsed)
                self.assertEqual(dock.pos(), QPoint(right_anchor[0], right_anchor[1]))
                save_mock.assert_not_called()
            finally:
                dock.close()
                QThreadPool.globalInstance().waitForDone(3000)


class CliTests(unittest.TestCase):
    def test_cli_crud_visibility_order_config_and_json(self):
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout

        from ai_docker import cli

        def run_cli(*argv: str) -> tuple[int, str, str]:
            out, err = io.StringIO(), io.StringIO()
            with (
                patch.object(cli, "get_agent_window_map", return_value={}),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                code = cli.main(list(argv))
            return code, out.getvalue(), err.getvalue()

        with TemporaryDirectory() as directory:
            cfg_path = Path(directory) / "agents.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "ui": {"theme": "dark", "auto_collapse": True, "auto_collapse_seconds": 8},
                        "agents": [
                            {"id": "cursor", "name": "Cursor", "type": "exe", "path": r"C:\Apps\Cursor.exe", "match_process": "Cursor.exe", "enabled": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            # schema --json
            rc, out, _ = run_cli("schema", "--json")
            self.assertEqual(rc, 0)
            schema_resp = json.loads(out)
            self.assertTrue(schema_resp["ok"])
            self.assertIn("theme", schema_resp["data"]["ui_keys"])

            # add web (flag before subcommand also works)
            url = "https://quick.example.com/sn/account/000/start/chat?conversationId=test-1"
            rc, out, _ = run_cli("--json", "--config", str(cfg_path), "add", "web", url, "--name", "Quick", "--match-title", "Quick Chat")
            self.assertEqual(rc, 0)
            added = json.loads(out)
            self.assertTrue(added["ok"])
            self.assertEqual(added["data"]["agent"]["id"], "quick")
            self.assertEqual(added["data"]["agent"]["url"], url)
            self.assertEqual(added["data"]["agent"]["match_title"], "Quick Chat")

            # add cli
            rc, out, _ = run_cli("add", "cli", "codex", "--name", "Codex CLI", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["data"]["agent"]["type"], "cli")

            # move quick to first
            rc, out, _ = run_cli("move", "quick", "first", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["data"]["visible_order"][0], "quick")

            # hide & last-visible guard
            rc, out, _ = run_cli("hide", "quick", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            rc, out, _ = run_cli("list", "--config", str(cfg_path), "--json")
            self.assertEqual([a["id"] for a in json.loads(out)["data"]["agents"]], ["cursor", "codex-cli-cli"])
            rc, out, _ = run_cli("show", "quick", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)

            # release & reclaim --all
            rc, out, _ = run_cli("release", "cursor", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(out)["data"]["agent"]["hide_from_taskbar"])
            rc, out, _ = run_cli("reclaim", "--all", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["data"]["reclaimed_ids"], ["cursor"])

            # config set valid & invalid
            rc, out, _ = run_cli("config", "set", "theme", "light", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["data"]["value"], "light")
            rc, out, _ = run_cli("config", "set", "orientation", "vertical", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["data"]["value"], "vertical")
            rc, out, _ = run_cli("config", "set", "orientation", "diagonal", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 2)
            self.assertFalse(json.loads(out)["ok"])
            rc, out, _ = run_cli("config", "set", "icon_size", "99999", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 2)
            self.assertFalse(json.loads(out)["ok"])

            # remove
            rc, out, _ = run_cli("remove", "quick", "--config", str(cfg_path), "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["data"]["removed"]["id"], "quick")
