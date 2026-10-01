@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title StatBridge Portable Launcher
cd /d "%~dp0"
set "ROOT=%CD%"
set "MCP=%ROOT%\data\statbridge_mcp_server"
set "AGENT=%ROOT%\src\agent"
set "FRONT=%AGENT%\frontend"
set "RUN=%ROOT%\scripts\windows\runtime"
set "VENV=%ROOT%\.venv_runtime"
set "STATBRIDGE_DATA_DIR=%ROOT%\data\runtime_data\processed"
set "STATBRIDGE_TABLES_DIR=%ROOT%\data\runtime_data\tables"
set "STATBRIDGE_VECTOR_PATH=%ROOT%\data\vector_store"
set "PYTHONPATH=%AGENT%;%MCP%"
cls
echo ============================================================
echo   StatBridge Portable Runtime
echo   First run installs missing local dependencies automatically.
echo ============================================================
echo.
if not exist "%MCP%\server.py" goto :MISSING
if not exist "%AGENT%\bridge_api.py" goto :MISSING
if not exist "%FRONT%\package.json" goto :MISSING
if not exist "%RUN%\RUN_AGENT.cmd" goto :MISSING
if not exist "%STATBRIDGE_DATA_DIR%\bok_table_master.csv" goto :MISSING

echo [1/7] Python 3.11+ detection
set "BASE_PY="
for %%V in (3.13 3.12 3.11) do if not defined BASE_PY (
    py -%%V -c "import sys;raise SystemExit(0 if sys.version_info[:2] in [(3,11),(3,12),(3,13),(3,14)] else 1)" >nul 2>nul
    if !errorlevel! equ 0 set "BASE_PY=py -%%V"
)
if not defined BASE_PY (
    python -c "import sys;raise SystemExit(0 if sys.version_info[:2] in [(3,11),(3,12),(3,13),(3,14)] else 1)" >nul 2>nul
    if !errorlevel! equ 0 set "BASE_PY=python"
)
if not defined BASE_PY (
    where winget >nul 2>nul
    if errorlevel 1 goto :NO_PYTHON
    echo Python 3.12 is missing. Installing with Windows Package Manager...
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :NO_PYTHON
    echo [INFO] Python installation finished. Restarting launcher...
    start "" "%~f0"
    exit /b 0
)
echo [OK] !BASE_PY!

echo.
echo [2/7] Machine-local Python environment
set "PYTHON_EXE=%VENV%\Scripts\python.exe"
if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" -c "import sys;raise SystemExit(0 if sys.version_info[:2] in [(3,11),(3,12),(3,13),(3,14)] else 1)" >nul 2>nul
    if errorlevel 1 (
        set "BROKEN_VENV=%MCP%\.venv_runtime.invalid.!RANDOM!"
        echo [INFO] Incompatible environment found. Moving it aside...
        move "%VENV%" "!BROKEN_VENV!" >nul
    )
)
if not exist "%PYTHON_EXE%" (
    echo Creating a Python environment for this computer...
    call !BASE_PY! -m venv "%VENV%"
    if errorlevel 1 goto :VENV_FAILED
)
"%PYTHON_EXE%" -c "import sys;print(sys.executable)" >nul 2>nul
if errorlevel 1 goto :VENV_FAILED
echo [OK] %PYTHON_EXE%

echo.
echo [3/7] Python dependencies
"%PYTHON_EXE%" -c "import mcp,pandas,requests,dotenv,fastapi,uvicorn,chromadb,langgraph" >nul 2>nul
if errorlevel 1 (
    echo Installing required Python packages. This can take several minutes on first run...
    "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r "%MCP%\requirements.txt"
    if errorlevel 1 goto :PIP_FAILED
)
echo [OK] Python dependencies ready.

echo.
echo [4/7] API environment
if not exist "%MCP%\.env" copy /y "%MCP%\.env.example" "%MCP%\.env" >nul
set "KOSIS_VALUE="
set "NCP_VALUE="
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"KOSIS_API_KEY=" "%MCP%\.env" 2^>nul') do set "KOSIS_VALUE=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"NCP_CLOVA_API_KEY=" "%MCP%\.env" 2^>nul') do set "NCP_VALUE=%%B"
if not defined KOSIS_VALUE echo [WARN] KOSIS_API_KEY is empty. Add it to data\statbridge_mcp_server\.env.
if not defined NCP_VALUE echo [WARN] NCP_CLOVA_API_KEY is empty. Add it to data\statbridge_mcp_server\.env.
if not defined KOSIS_VALUE goto :API_KEYS_REQUIRED
if not defined NCP_VALUE goto :API_KEYS_REQUIRED
if defined KOSIS_VALUE if defined NCP_VALUE echo [OK] KOSIS and NCP keys found.

:NODE_SETUP
echo.
echo [5/7] Node.js and frontend dependencies
where node >nul 2>nul
if errorlevel 1 (
    where winget >nul 2>nul
    if errorlevel 1 goto :NO_NODE
    echo Node.js LTS is missing. Installing with Windows Package Manager...
    winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :NO_NODE
    echo [INFO] Node.js installation finished. Restarting launcher...
    start "" "%~f0"
    exit /b 0
)
where npm.cmd >nul 2>nul
if errorlevel 1 goto :NO_NODE
if not exist "%FRONT%\node_modules\.bin\vite.cmd" (
    echo Installing frontend packages. This can take several minutes on first run...
    pushd "%FRONT%"
    if exist "%FRONT%\package-lock.json" (call npm.cmd ci) else (call npm.cmd install)
    if errorlevel 1 (popd & goto :NPM_FAILED)
    popd
)
echo [OK] Node.js and frontend dependencies ready.

echo.
echo [6/7] Starting services
for %%T in ("StatBridge Agent API" "StatBridge MCP" "StatBridge Frontend") do taskkill /FI "WINDOWTITLE eq *%%~T*" /T /F >nul 2>nul
call :STOP_PORT 8000
call :STOP_PORT 5173
start "StatBridge Agent API" /D "%AGENT%" cmd.exe /k call "%RUN%\RUN_AGENT.cmd"
set "AGENT_READY="
for /L %%I in (1,1,40) do if not defined AGENT_READY (
    "%PYTHON_EXE%" -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=1)" >nul 2>nul
    if !errorlevel! equ 0 (set "AGENT_READY=1") else timeout /t 1 /nobreak >nul
)
if not defined AGENT_READY goto :AGENT_FAILED
start "StatBridge MCP" /D "%MCP%" cmd.exe /k call "%RUN%\RUN_MCP.cmd"
start "StatBridge Frontend" /D "%FRONT%" cmd.exe /k call "%RUN%\RUN_FRONTEND.cmd"
set "FRONT_READY="
for /L %%I in (1,1,40) do if not defined FRONT_READY (
    "%PYTHON_EXE%" -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:5173/',timeout=1)" >nul 2>nul
    if !errorlevel! equ 0 (set "FRONT_READY=1") else timeout /t 1 /nobreak >nul
)
if not defined FRONT_READY goto :FRONT_FAILED

echo.
echo [7/7] Runtime verification
"%PYTHON_EXE%" -c "import json,urllib.request;d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=5));assert d.get('status')=='ok';print('[OK] Agent health:',d.get('status'))"
if errorlevel 1 goto :AGENT_FAILED
echo [OK] Frontend: http://127.0.0.1:5173/
start "" "http://127.0.0.1:5173/"
echo.
echo ============================================================
echo [OK] StatBridge is ready.
echo UI:    http://127.0.0.1:5173/
echo Agent: http://127.0.0.1:8000/api/health
echo To stop: run STOP_STATBRIDGE.cmd
echo ============================================================
echo.
pause
exit /b 0

:STOP_PORT
for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%~1 .*LISTENING"') do taskkill /PID %%A /F >nul 2>nul
exit /b 0
:MISSING
echo [ERROR] Required package files are missing. Keep this CMD at the StatBridge package root.
goto :FAIL
:NO_PYTHON
echo [ERROR] Python 3.11+ could not be installed. Install Python 3.12 and run this file again.
goto :FAIL
:NO_NODE
echo [ERROR] Node.js LTS could not be installed. Install Node.js LTS and run this file again.
goto :FAIL
:VENV_FAILED
echo [ERROR] Failed to create the machine-local Python environment: %VENV%
goto :FAIL
:PIP_FAILED
echo [ERROR] Python package installation failed. Check internet access and retry.
goto :FAIL
:NPM_FAILED
echo [ERROR] Frontend package installation failed. Check internet access and retry.
goto :FAIL
:API_KEYS_REQUIRED
echo.
echo [ACTION REQUIRED] API keys are missing.
echo 1. The settings file has been prepared here:
echo    %MCP%\.env
echo 2. Enter both KOSIS_API_KEY and NCP_CLOVA_API_KEY.
echo 3. Save the file, close Notepad, then press any key here.
start "StatBridge API Keys" /wait notepad.exe "%MCP%\.env"
set "KOSIS_VALUE="
set "NCP_VALUE="
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"KOSIS_API_KEY=" "%MCP%\.env" 2^>nul') do set "KOSIS_VALUE=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"NCP_CLOVA_API_KEY=" "%MCP%\.env" 2^>nul') do set "NCP_VALUE=%%B"
if not defined KOSIS_VALUE (
    echo [ERROR] KOSIS_API_KEY is still empty.
    goto :FAIL
)
if not defined NCP_VALUE (
    echo [ERROR] NCP_CLOVA_API_KEY is still empty.
    goto :FAIL
)
echo [OK] API keys found. Continuing setup...
goto :NODE_SETUP
:AGENT_FAILED
echo [ERROR] Agent API did not become healthy. Check the 'StatBridge Agent API' window.
goto :FAIL
:FRONT_FAILED
echo [ERROR] Frontend did not start. Check the 'StatBridge Frontend' window.
:FAIL
echo.
pause
exit /b 1
