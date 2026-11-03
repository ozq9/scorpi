@echo off
rem Opens Hearth in its own app window (starts the server in the background if needed).
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" desktop.py %*
) else (
  start "" pythonw desktop.py %*
)
