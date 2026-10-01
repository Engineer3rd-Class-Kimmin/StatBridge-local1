@echo off
chcp 65001 >nul
set "STOP_PY=%~dp0.venv_runtime\Scripts\python.exe"
if not exist "%STOP_PY%" set "STOP_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%STOP_PY%" exit /b 0
"%STOP_PY%" "%~dp0scripts\windows\portable_runtime.py" stop
echo Done.
pause
