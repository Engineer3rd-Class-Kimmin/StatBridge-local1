@echo off
setlocal
cd /d "%~dp0StatBridge-official"
python tools\build_stat_vector_index.py --rebuild
pause
