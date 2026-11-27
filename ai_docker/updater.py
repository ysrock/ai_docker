from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from .config import PROJECT_ROOT

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GIT_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class UpdateCheckResult:
    status: str  # "up_to_date" | "update_available" | "not_git" | "error"
    local_sha: str = ""
    remote_sha: str = ""
    branch: str = ""
    remote_ref: str = ""
    behind_count: int = 0
    ahead_count: int = 0
    commits: tuple[str, ...] = field(default_factory=tuple)
    has_local_changes: bool = False
    message: str = ""

    @property
    def can_fast_forward(self) -> bool:
        return (not self.has_local_changes) and self.ahead_count == 0


def _run_git(
    repo_root: Path,
    *args: str,
    timeout: float = GIT_TIMEOUT_SEC,
    cancelled: Event | None = None,
) -> tuple[int, str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            ["git", *args],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + timeout
        while True:
            if cancelled is not None and cancelled.is_set():
                proc.kill()
                try:
                    proc.communicate(timeout=1.0)
                except Exception:
                    pass
                return -1, "", "已取消检查更新。"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                try:
                    proc.communicate(timeout=1.0)
                except Exception:
                    pass
                return -2, "", "连接远端仓库超时，请检查网络后重试。"
            try:
                stdout, stderr = proc.communicate(timeout=min(0.05, remaining))
                return proc.returncode, stdout.strip(), stderr.strip()
            except subprocess.TimeoutExpired:
                continue
    except FileNotFoundError:
        return -1, "", "未检测到 Git，请先安装 Git 或手动下载最新版本。"
    except OSError as exc:
        return -3, "", str(exc)
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass


def check_for_updates(repo_root: Path = PROJECT_ROOT, *, cancelled: Event | None = None) -> UpdateCheckResult:
    """Fetch remote branch metadata and compare local HEAD against its upstream."""
    def git(*args: str, timeout: float = GIT_TIMEOUT_SEC) -> tuple[int, str, str]:
        if cancelled is not None and cancelled.is_set():
            return -1, "", "已取消检查更新。"
        return _run_git(repo_root, *args, timeout=timeout, cancelled=cancelled)

    if not shutil.which("git"):
        return UpdateCheckResult(
            status="error",
            message="本机未安装 Git，无法自动检查 Git 仓库更新。",
        )

    rc, worktree_root, _ = git("rev-parse", "--show-toplevel")
    if rc != 0 or not worktree_root or Path(worktree_root).resolve() != repo_root.resolve():
        return UpdateCheckResult(
            status="not_git",
            message="当前目录不是 Git 克隆仓库，请通过 git clone 安装以启用一键更新。",
        )

    rc, branch, err = git("symbolic-ref", "--quiet", "--short", "HEAD")
    if rc != 0 or not branch or branch == "HEAD":
        return UpdateCheckResult(
            status="error",
            message=f"当前处于游离 HEAD 状态，无法自动确定跟踪分支。{err}",
        )

    rc, local_sha, err = git("rev-parse", "--verify", "HEAD")
    if rc != 0:
        return UpdateCheckResult(status="error", branch=branch, message=err or "无法读取当前提交。")

    rc, upstream, err = git(
        "for-each-ref", "--format=%(upstream:remotename)%09%(upstream:remoteref)", f"refs/heads/{branch}"
    )
    if rc != 0:
        return UpdateCheckResult(status="error", branch=branch, message=err or "无法读取上游分支。")
    remote_name, _, remote_branch = upstream.partition("\t")
    if not remote_name or not remote_branch:
        remote_name, remote_branch = "origin", f"refs/heads/{branch}"
    remote_ref = f"{remote_name}/{remote_branch.removeprefix('refs/heads/')}"

    # Fetch into a private ref so concurrent Git operations cannot change the preview.
    target_ref = f"refs/ai-docker/check-{uuid4().hex}"
    try:
        rc, _, err = git(
            "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
            remote_name, f"{remote_branch}:{target_ref}", timeout=12.0,
        )
        if rc != 0:
            return UpdateCheckResult(
                status="error", local_sha=local_sha, branch=branch, remote_ref=remote_ref,
                message=err or "拉取远端更新信息失败，请检查网络连接。",
            )
        rc, remote_sha, err = git("rev-parse", "--verify", f"{target_ref}^{{commit}}")
    finally:
        _run_git(repo_root, "update-ref", "-d", target_ref, timeout=2.0)
    if rc != 0:
        return UpdateCheckResult(
            status="error",
            local_sha=local_sha,
            branch=branch,
            remote_ref=remote_ref,
            message=err or f"远端分支 {remote_ref} 不存在。",
        )

    rc, counts_out, err = git("rev-list", "--left-right", "--count", f"{local_sha}...{remote_sha}")
    if rc != 0:
        return UpdateCheckResult(
            status="error",
            local_sha=local_sha,
            remote_sha=remote_sha,
            branch=branch,
            remote_ref=remote_ref,
            message=err or "无法比较本地与远端提交差异。",
        )

    ahead_count = 0
    behind_count = 0
    parts = counts_out.split()
    if len(parts) == 2:
        try:
            ahead_count = int(parts[0])
            behind_count = int(parts[1])
        except ValueError:
            ahead_count = behind_count = 0

    rc_dirty, dirty_out, dirty_error = git("status", "--porcelain", "--untracked-files=no")
    if rc_dirty != 0:
        return UpdateCheckResult(status="error", branch=branch, message=dirty_error or "无法检查工作区状态。")
    has_local_changes = bool(dirty_out)

    if behind_count <= 0:
        return UpdateCheckResult(
            status="up_to_date",
            local_sha=local_sha,
            remote_sha=remote_sha,
            branch=branch,
            remote_ref=remote_ref,
            behind_count=0,
            ahead_count=ahead_count,
            has_local_changes=has_local_changes,
            message=f"当前已是最新版本（{branch} @ {local_sha[:12]}）。",
        )

    _, log_out, _ = git("log", "--pretty=format:%h %s", "-n", "15", f"{local_sha}..{remote_sha}")
    commits = tuple(line.strip() for line in log_out.splitlines() if line.strip())

    return UpdateCheckResult(
        status="update_available",
        local_sha=local_sha,
        remote_sha=remote_sha,
        branch=branch,
        remote_ref=remote_ref,
        behind_count=behind_count,
        ahead_count=ahead_count,
        commits=commits,
        has_local_changes=has_local_changes,
        message=f"发现 {behind_count} 个新提交（{local_sha[:12]} -> {remote_sha[:12]}）。",
    )


def launch_updater_script(
    wait_pid: int | None = None,
    repo_root: Path = PROJECT_ROOT,
    gui_mode: bool = True,
    *,
    update: UpdateCheckResult | None = None,
) -> tuple[bool, str]:
    """Spawn detached update.ps1 to gracefully update and relaunch AI docker."""
    if gui_mode and update is None:
        return False, "缺少已确认的更新预览，请重新检查更新。"
    script = repo_root / "update.ps1"
    if not script.exists():
        return False, f"找不到更新脚本：{script}"

    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
    cmd = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    if wait_pid:
        cmd.extend(["-WaitPid", str(int(wait_pid))])
    if gui_mode:
        cmd.append("-GuiMode")
    if update is not None:
        cmd.extend([
            "-PreviewBranch", update.branch,
            "-LocalRevision", update.local_sha,
            "-TargetRevision", update.remote_sha,
        ])

    # DETACHED_PROCESS can make PowerShell exit before executing -File.
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, cwd=str(repo_root), creationflags=creationflags)
        return True, str(script)
    except OSError as exc:
        return False, f"启动更新脚本失败：{exc}"


class _UpdateCheckRunnable(QRunnable):
    def __init__(
        self,
        repo_root: Path,
        generation: int,
        silent: bool,
        cancelled: Event,
        complete: Callable[[int, UpdateCheckResult, bool], None],
    ) -> None:
        super().__init__()
        self._repo_root = repo_root
        self._generation = generation
        self._silent = silent
        self._cancelled = cancelled
        self._complete = complete

    def run(self) -> None:
        try:
            if self._cancelled.is_set():
                result = UpdateCheckResult(status="error")
            else:
                result = check_for_updates(self._repo_root, cancelled=self._cancelled)
        except Exception as exc:
            result = UpdateCheckResult(status="error", message=str(exc))
        try:
            self._complete(self._generation, result, self._silent)
        except RuntimeError:
            pass


class UpdateChecker(QObject):
    """Run git update checks on QThreadPool and emit results on the GUI thread."""

    finished = Signal(object, bool)
    _completed = Signal(int, object, bool)

    def __init__(self, parent: QObject, repo_root: Path = PROJECT_ROOT) -> None:
        super().__init__(parent)
        self._repo_root = repo_root
        self._generation = 0
        self._cancelled = Event()
        self._in_flight = False
        self._pending_non_silent = False
        self._completed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    def invalidate(self) -> None:
        self._cancelled.set()
        self._cancelled = Event()
        self._generation += 1
        self._pending_non_silent = False

    def check_async(self, *, silent: bool = False) -> bool:
        if self._in_flight:
            if not silent:
                self._pending_non_silent = True
            return False
        self._in_flight = True
        self._pending_non_silent = False
        task = _UpdateCheckRunnable(
            self._repo_root, self._generation, silent, self._cancelled, self._completed.emit
        )
        QThreadPool.globalInstance().start(task)
        return True

    @Slot(int, object, bool)
    def _deliver(self, generation: int, result: UpdateCheckResult, silent: bool) -> None:
        self._in_flight = False
        effective_silent = silent and (not self._pending_non_silent)
        self._pending_non_silent = False
        if generation != self._generation:
            return
        self.finished.emit(result, effective_silent)
