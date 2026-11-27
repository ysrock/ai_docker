param(
    [int]$WaitPid = 0,
    [switch]$NoLaunch,
    [switch]$GuiMode,
    [string]$PreviewBranch,
    [string]$LocalRevision,
    [string]$TargetRevision
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:GIT_TERMINAL_PROMPT = "0"

function Show-UpdateError {
    param([string]$Message)
    if ($GuiMode) {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.MessageBox]::Show(
                $Message,
                "AI docker 更新失败",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            ) | Out-Null
        } catch {
            Write-Error $Message
        }
    } else {
        Write-Error $Message
    }
}

function Write-Info {
    param([string]$Message)
    if (-not $GuiMode) {
        Write-Output $Message
    }
}

function Invoke-Native {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    $stderrFile = [IO.Path]::GetTempFileName()
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & $FilePath @Arguments 2>$stderrFile
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $stdoutText = (($lines | ForEach-Object { $_.ToString() }) -join "`n").Trim()
    $stderrText = ""
    try {
        if (Test-Path -LiteralPath $stderrFile) {
            $rawErr = Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue
            if ($rawErr) {
                $stderrText = $rawErr.Trim()
            }
        }
    } finally {
        Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
    }
    $combinedText = (@($stdoutText, $stderrText | Where-Object { $_ }) -join "`n").Trim()
    return @{ Code = $code; Output = $stdoutText; Error = $stderrText; Combined = $combinedText }
}

function Invoke-Git {
    param([string[]]$GitArgs)
    return Invoke-Native -FilePath "git" -Arguments $GitArgs
}

function Start-AiDocker {
    param([switch]$PreferPythonFallback)
    $launcherExe = Join-Path $Root "AI docker.exe"
    $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    $entry = Join-Path $Root "start_dock.pyw"
    if ((-not $PreferPythonFallback) -and (Test-Path -LiteralPath $launcherExe)) {
        if (-not ((Test-Path -LiteralPath $pythonw) -and (Test-Path -LiteralPath $entry))) {
            throw "AI docker.exe 缺少运行所需的 .venv\Scripts\pythonw.exe 或 start_dock.pyw，无法重启悬浮窗。"
        }
        $proc = Start-Process -FilePath $launcherExe -WorkingDirectory $Root -PassThru
        if (-not $proc) {
            throw "启动器 AI docker.exe 拉起失败。"
        }
        if ($proc.WaitForExit(500) -and $proc.ExitCode -ne 0) {
            throw "启动器 AI docker.exe 退出异常（退出码 $($proc.ExitCode)），未能重启悬浮窗。"
        }
        return
    }
    if ((Test-Path -LiteralPath $pythonw) -and (Test-Path -LiteralPath $entry)) {
        $proc = Start-Process -FilePath $pythonw -ArgumentList "`"$entry`"" -WorkingDirectory $Root -PassThru
        if (-not $proc) {
            throw "Python 入口 start_dock.pyw 拉起失败。"
        }
        if ($proc.WaitForExit(300) -and $proc.ExitCode -ne 0) {
            throw "Python 入口 start_dock.pyw 退出异常（退出码 $($proc.ExitCode)），未能重启悬浮窗。"
        }
        return
    }
    throw "未找到可用的 AI docker 启动器或 Python 入口，无法重启悬浮窗。"
}

function Test-InstanceMutexHeld {
    param([string]$Name)
    if (-not $Name) {
        return $false
    }
    $existing = $null
    try {
        if ([System.Threading.Mutex]::TryOpenExisting($Name, [ref]$existing)) {
            return $true
        }
        return $false
    } catch [System.UnauthorizedAccessException] {
        return $true
    } finally {
        if ($existing) {
            $existing.Dispose()
        }
    }
}

function Stop-RunningAiDockerGracefully {
    # Qt.Tool does not appear as Process.MainWindowHandle.
    if (-not ('AiDockerWin32' -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AiDockerWin32 {
    public const uint WM_CLOSE = 0x0010;
    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    public static extern IntPtr FindWindowExW(IntPtr parent, IntPtr previous, IntPtr className, string title);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    public static extern IntPtr GetPropW(IntPtr hWnd, string name);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")]
    public static extern bool PostMessageW(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern IntPtr CommandLineToArgvW(string command, out int count);
    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr pointer);
    public static string[] ParseCommandLine(string command) {
        int count;
        IntPtr argv = CommandLineToArgvW(command, out count);
        if (argv == IntPtr.Zero) return new string[0];
        try {
            var args = new string[count];
            for (int i = 0; i < count; i++) {
                args[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * IntPtr.Size));
            }
            return args;
        } finally {
            LocalFree(argv);
        }
    }
}
"@
    }

    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $pathBytes = [Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($Root).ToLowerInvariant())
        $hash = [BitConverter]::ToString($sha.ComputeHash($pathBytes)).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
    $property = "AI docker install $hash"
    $entryPaths = @((Join-Path $Root 'start_dock.pyw'), (Join-Path $Root 'main.py'))
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ($true) {
        $hwnd = [IntPtr]::Zero
        $unverifiedWindow = $false
        $closedAny = $false
        while ($true) {
            $hwnd = [AiDockerWin32]::FindWindowExW([IntPtr]::Zero, $hwnd, [IntPtr]::Zero, "AI docker")
            if ($hwnd -eq [IntPtr]::Zero) { break }
            [uint32]$dockPid = 0
            [void][AiDockerWin32]::GetWindowThreadProcessId($hwnd, [ref]$dockPid)
            if ($dockPid -eq 0) { continue }
            if ([AiDockerWin32]::GetPropW($hwnd, $property) -eq [IntPtr]::Zero) {
                $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$dockPid" -ErrorAction SilentlyContinue
                $argv = if ($procInfo.CommandLine) { @([AiDockerWin32]::ParseCommandLine($procInfo.CommandLine)) }
                if ($argv.Count -ge 2 -and [IO.Path]::GetFileName($argv[0]) -match '^pythonw?\.exe$' -and
                    [IO.Path]::IsPathRooted($argv[1]) -and
                    ([IO.Path]::GetFileName($argv[1]) -in @('start_dock.pyw', 'main.py'))) {
                    if ([IO.Path]::GetFullPath($argv[1]) -notin $entryPaths) {
                        continue
                    }
                } else {
                    $unverifiedWindow = $true
                    continue
                }
            }

            $process = Get-Process -Id $dockPid -ErrorAction SilentlyContinue
            if (-not $process) { continue }
            try {
                [void]$process.Handle
                Write-Info "Closing running AI docker (PID $dockPid)..."
                if (-not [AiDockerWin32]::PostMessageW($hwnd, [AiDockerWin32]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) -or
                    -not $process.WaitForExit(30000)) {
                    throw "无法优雅关闭正在运行的 AI docker（PID $dockPid），请先右键退出后再运行更新。"
                }
                if (-not $NoLaunch) {
                    $script:shouldRestoreOnFail = $true
                }
                $closedAny = $true
                break
            } finally {
                $process.Dispose()
            }
        }
        if ($unverifiedWindow) {
            throw "找到了其他安装或来源不明的 AI docker 窗口。请关闭正在运行的 Dock 后再更新。"
        }
        if (-not $closedAny -and -not (Test-InstanceMutexHeld $script:instanceMutexName)) {
            return
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "检测到同目录 AI docker 正在启动或尚未释放实例锁，请稍后重试更新。"
        }
        Start-Sleep -Milliseconds 100
    }
}

$shouldRestoreOnFail = $false
$runtimeReady = $true
$updateLock = $null
$updateMutex = $null
$hasUpdateMutex = $false
$instanceMutexName = $null
$pendingSyncPath = $null
$createdPendingSync = $false
$targetRef = $null
$launcherBuildFailed = $false

function Release-UpdateMutex {
    if ($script:hasUpdateMutex -and $script:updateMutex) {
        try { $script:updateMutex.ReleaseMutex() } catch { }
        $script:updateMutex.Dispose()
        $script:updateMutex = $null
        $script:hasUpdateMutex = $false
    }
}

try {
    Set-Location -LiteralPath $Root

    $shaRoot = [Security.Cryptography.SHA256]::Create()
    try {
        $rootBytes = [Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($Root).ToLowerInvariant())
        $rootHash = [BitConverter]::ToString($shaRoot.ComputeHash($rootBytes)).Replace('-', '').ToLowerInvariant()
    } finally {
        $shaRoot.Dispose()
    }
    $createdMutex = $false
    $script:instanceMutexName = "Local\AI_docker_Instance_$rootHash"
    $updateMutex = New-Object System.Threading.Mutex($true, "Local\AI_docker_Update_$rootHash", [ref]$createdMutex)
    if (-not $createdMutex) {
        $updateMutex.Dispose()
        $updateMutex = $null
        $shouldRestoreOnFail = $false
        throw "已有其他 AI docker 更新进程正在运行，请稍后重试。"
    }
    $hasUpdateMutex = $true

    # 1. Wait for exiting AI docker instance (or gracefully close running instance)
    if ($WaitPid -gt 0) {
        $shouldRestoreOnFail = (-not $NoLaunch)
        Write-Info "Waiting for AI docker (PID $WaitPid) to exit..."
        $process = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
        if ($process) {
            try {
                if (-not $process.WaitForExit(30000)) {
                    $shouldRestoreOnFail = $false
                    throw "等待旧版 AI docker（PID $WaitPid）退出超时，已中止更新以避免文件冲突。"
                }
            } finally {
                $process.Dispose()
            }
        }
    }

    # 2. Verify Git repository & clean tracked files (local agents.json is gitignored)
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "未找到 git 命令，请先安装 Git。"
    }

    $topRes = Invoke-Git @("rev-parse", "--show-toplevel")
    if ($topRes.Code -ne 0 -or -not $topRes.Output -or (Resolve-Path -LiteralPath $topRes.Output).Path -ne (Resolve-Path -LiteralPath $Root).Path) {
        throw "当前目录不是有效的 Git 仓库：$Root"
    }

    $pendingPathRes = Invoke-Git @("rev-parse", "--git-path", "ai-docker-sync-pending")
    if ($pendingPathRes.Code -ne 0 -or -not $pendingPathRes.Output) {
        throw "无法定位 Git 更新状态目录。"
    }
    $pendingSyncPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($pendingPathRes.Output)
    try {
        $updateLock = [IO.File]::Open("$pendingSyncPath.lock", 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        $shouldRestoreOnFail = $false
        throw "无法锁定更新状态，可能已有其他更新进程正在运行。"
    }
    $runtimeReady = -not (Test-Path -LiteralPath $pendingSyncPath)

    $dirtyRes = Invoke-Git @("status", "--porcelain", "--untracked-files=no")
    if ($dirtyRes.Code -ne 0) {
        throw "无法检查 Git 工作区状态：`n$($dirtyRes.Combined)"
    }
    if ($dirtyRes.Output) {
        throw "检测到本地有未提交的代码改动，为避免覆盖你的修改已取消自动更新。请先提交或 git stash 后再更新。"
    }

    $beforeSha = (Invoke-Git @("rev-parse", "HEAD")).Output
    $branch = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")).Output
    if (-not $branch -or $branch -eq "HEAD") {
        throw "当前处于游离 HEAD 状态，无法自动拉取更新。"
    }

    if (($GuiMode -or $PreviewBranch -or $LocalRevision -or $TargetRevision) -and
        (-not $TargetRevision -or $branch -ne $PreviewBranch -or $beforeSha -ne $LocalRevision)) {
        throw "本地分支或提交与预览不一致，请重新检查更新。"
    }

    $remoteName = "origin"
    $remoteBranch = "refs/heads/$branch"
    $upRes = Invoke-Git @("for-each-ref", "--format=%(upstream:remotename)%09%(upstream:remoteref)", "refs/heads/$branch")
    if ($upRes.Code -ne 0) { throw "无法读取上游分支。" }
    $upstream = $upRes.Output -split "`t", 2
    if ($upstream.Count -eq 2 -and $upstream[0] -and $upstream[1]) {
        $remoteName, $remoteBranch = $upstream
    }

    # 3. Fetch and validate the target before changing the worktree.
    Write-Info "Fetching changes from '$remoteName/$remoteBranch'..."
    $targetRef = "refs/ai-docker/update-$([Guid]::NewGuid().ToString('N'))"
    $fetchRes = Invoke-Git @("fetch", "--no-tags", "--no-write-fetch-head", $remoteName, "${remoteBranch}:$targetRef")
    if ($fetchRes.Code -ne 0) {
        throw "git fetch 失败：`n$($fetchRes.Combined)"
    }
    $targetRes = Invoke-Git @("rev-parse", "--verify", "${targetRef}^{commit}")
    if ($targetRes.Code -ne 0 -or -not $beforeSha) {
        throw "无法确认更新前后的 Git 提交。"
    }
    $targetSha = $targetRes.Output
    if ($TargetRevision -and $targetSha -ne $TargetRevision) {
        throw "远端提交已变化，请重新检查更新后确认新版。"
    }
    $aheadRes = Invoke-Git @("merge-base", "--is-ancestor", $targetSha, $beforeSha)
    if ($aheadRes.Code -eq 0) {
        $targetSha = $beforeSha
    } else {
        $ffRes = Invoke-Git @("merge-base", "--is-ancestor", $beforeSha, $targetSha)
        if ($ffRes.Code -ne 0) {
            throw "当前分支与远端无法快进，请先检查本地分支。"
        }
    }
    $localConfigRes = Invoke-Git @("ls-tree", "--name-only", $targetSha, "--", "agents.json")
    if ($localConfigRes.Code -ne 0 -or $localConfigRes.Output) {
        throw "无法确认目标版本会保留本地 agents.json，已取消更新。"
    }

    $diffRes = Invoke-Git @("diff", "--name-only", $beforeSha, $targetSha)
    if ($diffRes.Code -ne 0) {
        throw "无法检查目标版本变更：`n$($diffRes.Combined)"
    }
    $changedFiles = @($diffRes.Output -split "`r?`n" | Where-Object { $_ })
    $depsChanged = $changedFiles | Where-Object { $_ -in @("pyproject.toml", "uv.lock") }
    $needsSync = (-not $runtimeReady) -or [bool]$depsChanged
    if ($needsSync -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "该版本需要同步 Python 依赖，请安装 uv 后重新运行更新。"
    }

    Stop-RunningAiDockerGracefully
    $currentHead = (Invoke-Git @("rev-parse", "HEAD")).Output
    $currentBranch = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")).Output
    if ($currentHead -ne $beforeSha -or $currentBranch -ne $branch) {
        throw "更新期间本地分支或提交发生变化，已取消更新。"
    }

    if ($targetSha -ne $beforeSha) {
        if ($needsSync -and $runtimeReady) {
            [IO.File]::WriteAllText($pendingSyncPath, $targetSha)
            $createdPendingSync = $true
        }
        $runtimeReady = -not $needsSync
        $mergeRes = Invoke-Git @("merge", "--ff-only", "--no-overwrite-ignore", $targetSha)
        if ($mergeRes.Code -ne 0) {
            $headAfterFailure = (Invoke-Git @("rev-parse", "HEAD")).Output
            if ($createdPendingSync -and $headAfterFailure -eq $beforeSha) {
                Remove-Item -LiteralPath $pendingSyncPath
                $runtimeReady = $true
            }
            throw "git merge --ff-only 失败：`n$($mergeRes.Combined)"
        }
        Write-Info $mergeRes.Output
    }

    # 4. Resume dependency synchronization even if HEAD was already fast-forwarded.
    if ($needsSync) {
        Write-Info "Dependencies changed; running uv sync..."
        $uvRes = Invoke-Native -FilePath "uv" -Arguments @("sync", "--locked")
        if ($uvRes.Code -ne 0) {
            throw "uv sync 执行失败。请在问题解决后重新运行 update.ps1 继续更新：`n$($uvRes.Combined)"
        }
        Remove-Item -LiteralPath $pendingSyncPath
        $runtimeReady = $true
    }

    # 5. Rebuild native launcher if launcher/icon assets changed or exe missing
    $launcherExe = Join-Path $Root "AI docker.exe"
    $launcherChanged = $changedFiles | Where-Object {
        $_ -like "launcher_src/*" -or $_ -like "assets/*" -or $_ -eq "build_launcher.ps1"
    }
    $buildScript = Join-Path $Root "build_launcher.ps1"
    if ((-not $NoLaunch) -and (Test-Path $buildScript) -and ($launcherChanged -or -not (Test-Path $launcherExe))) {
        Write-Info "Rebuilding AI docker.exe launcher..."
        try {
            & $buildScript
            if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
                throw "build_launcher.ps1 exited with code $LASTEXITCODE"
            }
        } catch {
            $launcherBuildFailed = $true
            Write-Info "Warning: build_launcher.ps1 failed ($($_.Exception.Message)); falling back to pythonw."
        }
    }

    # 6. Release update mutex and relaunch AI docker before declaring success
    if (-not $NoLaunch) {
        Release-UpdateMutex
        Start-AiDocker -PreferPythonFallback:$launcherBuildFailed
    }

    Write-Info "Update complete ($beforeSha -> $targetSha)."
} catch {
    Release-UpdateMutex
    if ($runtimeReady -and $shouldRestoreOnFail) {
        try { Start-AiDocker -PreferPythonFallback:$launcherBuildFailed } catch { }
    }
    Show-UpdateError $_.Exception.Message
    exit 1
} finally {
    Release-UpdateMutex
    if ($targetRef) {
        [void](Invoke-Git @("update-ref", "-d", $targetRef))
    }
    if ($updateLock) {
        $updateLock.Dispose()
    }
}
