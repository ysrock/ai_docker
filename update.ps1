param(
    [int]$WaitPid = 0,
    [switch]$NoLaunch,
    [switch]$GuiMode
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
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & $FilePath @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $text = (($lines | ForEach-Object { $_.ToString() }) -join "`n").Trim()
    return @{ Code = $code; Output = $text }
}

function Invoke-Git {
    param([string[]]$GitArgs)
    return Invoke-Native -FilePath "git" -Arguments $GitArgs
}

function Start-AiDocker {
    $launcherExe = Join-Path $Root "AI docker.exe"
    if (Test-Path $launcherExe) {
        Start-Process -FilePath $launcherExe -WorkingDirectory $Root
        return
    }
    $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    $entry = Join-Path $Root "start_dock.pyw"
    if ((Test-Path $pythonw) -and (Test-Path $entry)) {
        Start-Process -FilePath $pythonw -ArgumentList "`"$entry`"" -WorkingDirectory $Root
    }
}

function Stop-RunningAiDockerGracefully {
    # Qt.Tool windows do not populate Process.MainWindowHandle; send WM_CLOSE via Win32 FindWindowW
    try {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AiDockerWin32 {
    public const uint WM_CLOSE = 0x0010;
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindowW(string lpClassName, string lpWindowName);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")]
    public static extern bool PostMessageW(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
}
"@ -ErrorAction SilentlyContinue
    } catch {
        # Type already defined in session
    }

    $hwnd = [AiDockerWin32]::FindWindowW($null, "AI docker")
    if ($hwnd -ne [IntPtr]::Zero) {
        [uint32]$dockPid = 0
        [void][AiDockerWin32]::GetWindowThreadProcessId($hwnd, [ref]$dockPid)
        if ($dockPid -gt 0) {
            $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$dockPid" -ErrorAction SilentlyContinue
            $cmdLine = if ($procInfo) { [string]$procInfo.CommandLine } else { "" }
            $matchesRoot = $cmdLine -and (
                $cmdLine.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                $cmdLine -like "*start_dock.pyw*"
            )
            if ($matchesRoot) {
                Write-Info "Closing running AI docker (PID $dockPid)..."
                [void][AiDockerWin32]::PostMessageW($hwnd, [AiDockerWin32]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
                $deadline = (Get-Date).AddSeconds(8)
                while ((Get-Date) -lt $deadline) {
                    if (-not (Get-Process -Id $dockPid -ErrorAction SilentlyContinue)) {
                        return
                    }
                    Start-Sleep -Milliseconds 200
                }
                throw "无法优雅关闭正在运行的 AI docker（PID $dockPid），请先右键退出后再运行更新。"
            }
        }
    }
}

$shouldRestoreOnFail = $false

try {
    Set-Location $Root

    # 1. Wait for exiting AI docker instance (or gracefully close running instance in CLI mode)
    if ($WaitPid -gt 0) {
        $shouldRestoreOnFail = (-not $NoLaunch)
        Write-Info "Waiting for AI docker (PID $WaitPid) to exit..."
        $deadline = (Get-Date).AddSeconds(12)
        $exited = $false
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)) {
                $exited = $true
                break
            }
            Start-Sleep -Milliseconds 200
        }
        if (-not $exited) {
            throw "等待旧版 AI docker（PID $WaitPid）退出超时，已中止更新以避免文件冲突。"
        }
    } elseif (-not $NoLaunch) {
        Stop-RunningAiDockerGracefully
    }

    # 2. Verify Git repository & clean tracked files (local agents.json is gitignored)
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "未找到 git 命令，请先安装 Git。"
    }

    $insideRes = Invoke-Git @("rev-parse", "--is-inside-work-tree")
    if ($insideRes.Code -ne 0 -or $insideRes.Output -ne "true") {
        throw "当前目录不是有效的 Git 仓库：$Root"
    }

    $dirtyRes = Invoke-Git @("status", "--porcelain", "--untracked-files=no")
    if ($dirtyRes.Code -eq 0 -and $dirtyRes.Output) {
        throw "检测到本地有未提交的代码改动，为避免覆盖你的修改已取消自动更新。请先提交或 git stash 后再更新。"
    }

    $beforeSha = (Invoke-Git @("rev-parse", "HEAD")).Output
    $branch = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")).Output
    if (-not $branch -or $branch -eq "HEAD") {
        throw "当前处于游离 HEAD 状态，无法自动拉取更新。"
    }

    # Resolve configured upstream @{u}, falling back to origin/<branch>
    $remoteName = "origin"
    $remoteBranch = $branch
    $upRes = Invoke-Git @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if ($upRes.Code -eq 0 -and $upRes.Output -like "*/*") {
        $slash = $upRes.Output.IndexOf("/")
        $remoteName = $upRes.Output.Substring(0, $slash)
        $remoteBranch = $upRes.Output.Substring($slash + 1)
    }

    # 3. Fast-forward pull from upstream
    Write-Info "Pulling latest changes from '$remoteName/$remoteBranch'..."
    $pullRes = Invoke-Git @("pull", "--ff-only", $remoteName, $remoteBranch)
    if ($pullRes.Code -ne 0) {
        throw "git pull --ff-only 失败：`n$($pullRes.Output)"
    }
    Write-Info $pullRes.Output

    $afterSha = (Invoke-Git @("rev-parse", "HEAD")).Output
    $changedFiles = @()
    if ($beforeSha -and $afterSha -and $beforeSha -ne $afterSha) {
        $diffRes = Invoke-Git @("diff", "--name-only", $beforeSha, $afterSha)
        if ($diffRes.Code -eq 0 -and $diffRes.Output) {
            $changedFiles = @($diffRes.Output -split "`r?`n" | Where-Object { $_ })
        }
    }

    # 4. Sync Python dependencies if lock/manifest changed
    $depsChanged = $changedFiles | Where-Object { $_ -in @("pyproject.toml", "uv.lock") }
    if ($depsChanged -and (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Info "Dependencies changed; running uv sync..."
        $uvRes = Invoke-Native -FilePath "uv" -Arguments @("sync")
        if ($uvRes.Code -ne 0) {
            throw "uv sync 执行失败，请手动在项目目录运行 uv sync 检查：`n$($uvRes.Output)"
        }
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
        } catch {
            Write-Info "Warning: build_launcher.ps1 failed ($($_.Exception.Message)); falling back to pythonw."
        }
    }

    Write-Info "Update complete ($beforeSha -> $afterSha)."

    # 6. Relaunch AI docker
    if (-not $NoLaunch) {
        Start-AiDocker
    }
} catch {
    Show-UpdateError $_.Exception.Message
    if ($GuiMode -and $shouldRestoreOnFail) {
        try { Start-AiDocker } catch { }
    }
    exit 1
}
