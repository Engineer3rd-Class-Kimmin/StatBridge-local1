@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title StatBridge Portable Launcher
cd /d "%~dp0"
set "ROOT=%CD%"
set "MCP=%ROOT%\data\statbridge_mcp_server"
set "AGENT=%ROOT%\src\agent"
set "FRONT=%AGENT%\frontend"
set "VENV=%ROOT%\.venv_runtime"
set "STATBRIDGE_DATA_DIR=%ROOT%\data\runtime_data\processed"
set "STATBRIDGE_TABLES_DIR=%ROOT%\data\runtime_data\tables"
set "STATBRIDGE_VECTOR_PATH=%ROOT%\data\vector_store_349"
set "PYTHONPATH=%AGENT%;%MCP%"
set "PATH=%ProgramFiles%\nodejs;%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Launcher;%PATH%"
cls
echo ============================================================
echo   StatBridge Portable Runtime
echo   First run installs missing local dependencies automatically.
echo ============================================================
echo.
if not exist "%MCP%\server.py" goto :MISSING
if not exist "%AGENT%\bridge_api.py" goto :MISSING
if not exist "%FRONT%\package.json" goto :MISSING
if not exist "%ROOT%\scripts\windows\portable_runtime.py" goto :MISSING
if not exist "%STATBRIDGE_DATA_DIR%\bok_table_master.csv" goto :MISSING

echo [1/7] Python 3.11+ detection
:PYTHON_DETECT
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
    echo [INFO] Python installation finished. Refreshing detection...
    set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Launcher;%PATH%"
    if not exist "%LocalAppData%\Programs\Python\Python312\python.exe" goto :NO_PYTHON
    goto :PYTHON_DETECT
)
echo [OK] !BASE_PY!

echo.
echo [2/7] Machine-local Python environment
set "PYTHON_EXE=%VENV%\Scripts\python.exe"
if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" -c "import sys;raise SystemExit(0 if sys.version_info[:2] in [(3,11),(3,12),(3,13),(3,14)] else 1)" >nul 2>nul
    if errorlevel 1 (
        set "BROKEN_VENV=%ROOT%\.venv_runtime.invalid.!RANDOM!"
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
"%PYTHON_EXE%" "%ROOT%\scripts\windows\portable_runtime.py" check-keys
if errorlevel 1 goto :API_KEYS_REQUIRED
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
    echo [INFO] Node.js installation finished. Refreshing PATH...
    set "PATH=%ProgramFiles%\nodejs;%PATH%"
    if not exist "%ProgramFiles%\nodejs\node.exe" goto :NO_NODE
    goto :NODE_SETUP
)
where npm.cmd >nul 2>nul
if errorlevel 1 goto :NO_NODE
node -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>=24?0:1)"
if errorlevel 1 (
    echo [ERROR] Node.js 20.19+, 22.12+, or newer LTS is required. Update Node.js LTS and retry.
    goto :NO_NODE
)
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
if not defined STATBRIDGE_API_PORT set "STATBRIDGE_API_PORT=8000"
if not defined STATBRIDGE_UI_PORT set "STATBRIDGE_UI_PORT=5173"
set "BROWSER_OPTION="
if defined STATBRIDGE_NO_BROWSER set "BROWSER_OPTION=--no-browser"
set "VECTOR_SETUP="
"%PYTHON_EXE%" "%ROOT%\scripts\windows\portable_runtime.py" check-index
if errorlevel 1 set "VECTOR_SETUP=--build-index"
"%PYTHON_EXE%" "%ROOT%\scripts\windows\portable_runtime.py" start --api-port %STATBRIDGE_API_PORT% --ui-port %STATBRIDGE_UI_PORT% !BROWSER_OPTION! !VECTOR_SETUP!
if errorlevel 1 goto :FAIL
echo.
echo [7/7] Ready. UI: http://127.0.0.1:%STATBRIDGE_UI_PORT%/
echo To stop: run STOP_STATBRIDGE.cmd
if not defined STATBRIDGE_NO_PAUSE pause
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
"%PYTHON_EXE%" "%ROOT%\scripts\windows\portable_runtime.py" check-keys
if errorlevel 1 (
    echo [ERROR] Enter actual KOSIS and NCP keys, not placeholder text.
    goto :FAIL
)
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
:FAIL
echo.
if not defined STATBRIDGE_NO_PAUSE pause
exit /b 1
