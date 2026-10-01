@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "statbridge_mcp_server\.venv\Scripts\python.exe" (
  echo [ERROR] Run START_STATBRIDGE.cmd once first to create the Python environment.
  pause
  exit /b 1
)
"statbridge_mcp_server\.venv\Scripts\python.exe" TEST_NCP_MODELS.py
pause
