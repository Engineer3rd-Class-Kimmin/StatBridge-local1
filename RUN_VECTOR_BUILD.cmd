@echo off
setlocal
cd /d "%~dp0"
set "VECTOR_PY=%~dp0.venv_runtime\Scripts\python.exe"
if not exist "%VECTOR_PY%" set "VECTOR_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VECTOR_PY%" (
    echo [ERROR] Run START_STATBRIDGE.cmd first to prepare the Python environment.
    pause
    exit /b 1
)
"%VECTOR_PY%" tools\build_stat_vector_index.py
pause
