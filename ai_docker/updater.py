from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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
    behind_count: int = 0
    commits: tuple[str, ...] = field(default_factory=tuple)
    has_local_changes: bool = False
    message: str = ""


def _run_git(repo_root: Path, *args: str, timeout: float = GIT_TIMEOUT_SEC) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", "未检测到 Git，请先安装 Git 或手动下载最新版本。"
    except subprocess.TimeoutExpired:
        return -2, "", "连接远端仓库超时，请检查网络后重试。"
    except OSError as exc:
        return -3, "", str(exc)


def check_for_updates(repo_root: Path = PROJECT_ROOT) -> UpdateCheckResult:
    """Fetch remote branch metadata and compare local HEAD against origin."""
    if not shutil.which("git"):
        return UpdateCheckResult(
            status="error",
            message="本机未安装 Git，无法自动检查 Git 仓库更新。",
        )

    rc, inside, _ = _run_git(repo_root, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or inside.lower() != "true":
        return UpdateCheckResult(
            status="not_git",
            message="当前目录不是 Git 克隆仓库，请通过 git clone 安装以启用一键更新。",
        )

    rc, branch, err = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not branch or branch == "HEAD":
        return UpdateCheckResult(
            status="error",
            message=f"当前处于游离 HEAD 状态，无法自动确定跟踪分支。{err}",
        )

    rc, local_sha, _ = _run_git(repo_root, "rev-parse", "--short", "HEAD")
    if rc != 0:
        local_sha = ""

    rc, _, err = _run_git(repo_root, "fetch", "--quiet", "origin", branch, timeout=12.0)
    if rc != 0:
        return UpdateCheckResult(
            status="error",
            local_sha=local_sha,
            branch=branch,
            message=err or "拉取远端更新信息失败，请检查网络连接。",
        )

    remote_ref = f"origin/{branch}"
    rc, remote_sha, err = _run_git(repo_root, "rev-parse", "--short", remote_ref)
    if rc != 0:
        return UpdateCheckResult(
            status="error",
            local_sha=local_sha,
            branch=branch,
            message=err or f"远端分支 {remote_ref} 不存在。",
        )

    rc, behind_str, err = _run_git(repo_root, "rev-list", "--count", f"HEAD..{remote_ref}")
    if rc != 0:
        return UpdateCheckResult(
            status="error",
            local_sha=local_sha,
            remote_sha=remote_sha,
            branch=branch,
            message=err or "无法比较本地与远端提交差异。",
        )

    try:
        behind_count = int(behind_str or "0")
    except ValueError:
        behind_count = 0

    rc_dirty, dirty_out, _ = _run_git(repo_root, "status", "--porcelain", "--untracked-files=no")
    has_local_changes = rc_dirty == 0 and bool(dirty_out)

    if behind_count <= 0:
        return UpdateCheckResult(
            status="up_to_date",
            local_sha=local_sha,
            remote_sha=remote_sha,
            branch=branch,
            behind_count=0,
            has_local_changes=has_local_changes,
            message=f"当前已是最新版本（{branch} @ {local_sha}）。",
        )

    _, log_out, _ = _run_git(
        repo_root, "log", "--pretty=format:%h %s", "-n", "15", f"HEAD..{remote_ref}"
    )
    commits = tuple(line.strip() for line in log_out.splitlines() if line.strip())

    return UpdateCheckResult(
        status="update_available",
        local_sha=local_sha,
        remote_sha=remote_sha,
        branch=branch,
        behind_count=behind_count,
        commits=commits,
        has_local_changes=has_local_changes,
        message=f"发现 {behind_count} 个新提交（{local_sha} -> {remote_sha}）。",
    )


def launch_updater_script(
    wait_pid: int | None = None,
    repo_root: Path = PROJECT_ROOT,
    gui_mode: bool = True,
) -> tuple[bool, str]:
    """Spawn detached update.ps1 to gracefully update and relaunch AI docker."""
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

    creationflags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | CREATE_NO_WINDOW
    )
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
        complete: Callable[[int, UpdateCheckResult, bool], None],
    ) -> None:
        super().__init__()
        self._repo_root = repo_root
        self._generation = generation
        self._silent = silent
        self._complete = complete

    def run(self) -> None:
        try:
            result = check_for_updates(self._repo_root)
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
        self._in_flight = False
        self._pending_non_silent = False
        self._completed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def invalidate(self) -> None:
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
            self._repo_root, self._generation, silent, self._completed.emit
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
