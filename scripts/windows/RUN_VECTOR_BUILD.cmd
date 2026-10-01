@echo off
setlocal
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
call "%ROOT%\RUN_VECTOR_BUILD.cmd"
