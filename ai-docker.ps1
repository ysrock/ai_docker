$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$prevPyPath = $env:PYTHONPATH
$prevArgvJson = $env:AI_DOCKER_ARGV_JSON
$env:PYTHONPATH = if ($prevPyPath) { "$Root;$prevPyPath" } else { $Root }
$env:AI_DOCKER_ARGV_JSON = ConvertTo-Json -Compress -InputObject @($args)
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$PythonExe = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
try {
    & $PythonExe -m ai_docker
    exit $LASTEXITCODE
} finally {
    if ($null -eq $prevArgvJson) {
        Remove-Item Env:AI_DOCKER_ARGV_JSON -ErrorAction SilentlyContinue
    } else {
        $env:AI_DOCKER_ARGV_JSON = $prevArgvJson
    }
    if ($null -eq $prevPyPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $prevPyPath
    }
}
