@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.10 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed
echo Setup complete. Run start.cmd to open Aerial Atlas.
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.10 x64 with Tk, then try again.
pause
exit /b 1
