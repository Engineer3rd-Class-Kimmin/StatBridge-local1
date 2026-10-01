@echo off
setlocal
cd /d "%~dp0StatBridge-official"
python tools\evaluate_retrieval.py
pause
