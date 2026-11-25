@echo off
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
    "%ROOT%.venv\Scripts\python.exe" -m ai_docker %*
) else (
    python -m ai_docker %*
)
exit /b %ERRORLEVEL%
