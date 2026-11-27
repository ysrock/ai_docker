@echo off
setlocal DisableDelayedExpansion
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%;%PYTHONPATH%"
set "PY_EXE=python"
if exist "%ROOT%.venv\Scripts\python.exe" set "PY_EXE=%ROOT%.venv\Scripts\python.exe"
"%PY_EXE%" -m ai_docker %*
exit /b %ERRORLEVEL%
