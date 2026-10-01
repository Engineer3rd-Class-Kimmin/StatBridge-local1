@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%CD%"
set "MCP=%ROOT%\statbridge_mcp_server"
set "AGENT=%ROOT%\StatBridge-official\src\agent"
set "STATBRIDGE_DATA_DIR=%ROOT%\runtime_data\processed"
set "STATBRIDGE_TABLES_DIR=%ROOT%\runtime_data\tables"
if exist "%MCP%\.venv\Scripts\python.exe" (
  "%MCP%\.venv\Scripts\python.exe" "%ROOT%\TEST_AGENT_FLOW.py"
) else (
  python "%ROOT%\TEST_AGENT_FLOW.py"
)
pause
