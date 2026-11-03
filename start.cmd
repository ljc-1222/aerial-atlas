@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
start "" /b ".venv\Scripts\pythonw.exe" -m aerial_atlas --project-dir "%~dp0." %*
