$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $Csc)) {
    Write-Error "Built-in .NET C# compiler not found: $Csc"
}

$OutExe = Join-Path $Root "AI Doker.exe"
$Icon = Join-Path $Root "assets\ai_doker.ico"
$Src = Join-Path $Root "launcher_src\AiDokerLauncher.cs"

& $Csc /nologo /target:winexe /optimize+ "/win32icon:$Icon" /reference:System.Windows.Forms.dll "/out:$OutExe" $Src
Write-Output "Built: $OutExe"

$StartMenuLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\AI Doker.lnk"
$Wsh = New-Object -ComObject WScript.Shell
$Sc = $Wsh.CreateShortcut($StartMenuLnk)
$Sc.TargetPath = $OutExe
$Sc.WorkingDirectory = $Root
$Sc.IconLocation = "$OutExe,0"
$Sc.Description = "AI Doker - AI Agent Floating Dock"
$Sc.Save()
Write-Output "Created Start Menu shortcut: $StartMenuLnk"
