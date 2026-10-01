@echo off
chcp 65001 >nul
echo Stopping StatBridge local ports 5173 and 8000...
for %%T in ("StatBridge Agent API" "StatBridge MCP" "StatBridge Frontend") do taskkill /FI "WINDOWTITLE eq *%%~T*" /T /F >nul 2>nul
for %%P in (5173 8000) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P .*LISTENING"') do taskkill /PID %%A /F >nul 2>nul
)
echo Done.
pause
