param(
    [int]$WaitPid = 0,
    [switch]$NoLaunch,
    [switch]$GuiMode
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

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

function Invoke-Git {
    param([string[]]$GitArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & git @GitArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $text = (($lines | ForEach-Object { $_.ToString() }) -join "`n").Trim()
    return @{ Code = $code; Output = $text }
}

try {
    Set-Location $Root

    # 1. Wait for exiting AI docker instance (or gracefully close running instance in CLI mode)
    if ($WaitPid -gt 0) {
        Write-Info "Waiting for AI docker (PID $WaitPid) to exit..."
        $deadline = (Get-Date).AddSeconds(12)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)) {
                break
            }
            Start-Sleep -Milliseconds 200
        }
    } elseif (-not $NoLaunch) {
        $running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and (
                    $_.CommandLine -like "*start_dock.pyw*" -or
                    ($_.CommandLine -like "*ai_doker*main.py*") -or
                    ($_.CommandLine -like "*ai_docker*main.py*")
                )
            }
        foreach ($procInfo in $running) {
            $p = Get-Process -Id $procInfo.ProcessId -ErrorAction SilentlyContinue
            if ($p) {
                Write-Info "Closing running AI docker (PID $($p.Id))..."
                $null = $p.CloseMainWindow()
                $null = $p.WaitForExit(5000)
            }
        }
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

    # 3. Fast-forward pull from origin
    Write-Info "Pulling latest changes for branch '$branch'..."
    $pullRes = Invoke-Git @("pull", "--ff-only", "origin", $branch)
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
        & uv sync
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync 执行失败，请手动在项目目录运行 uv sync 检查。"
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
        & $buildScript
    }

    Write-Info "Update complete ($beforeSha -> $afterSha)."

    # 6. Relaunch AI docker
    if (-not $NoLaunch) {
        if (Test-Path $launcherExe) {
            Start-Process -FilePath $launcherExe -WorkingDirectory $Root
        } else {
            $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
            $entry = Join-Path $Root "start_dock.pyw"
            if ((Test-Path $pythonw) -and (Test-Path $entry)) {
                Start-Process -FilePath $pythonw -ArgumentList "`"$entry`"" -WorkingDirectory $Root
            }
        }
    }
} catch {
    Show-UpdateError $_.Exception.Message
    exit 1
}
