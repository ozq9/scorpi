@echo off
rem Runs the Scorp server in this console window (shows addresses and the pairing PIN).
cd /d "%~dp0"
title Scorp server
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" server.py %*
) else (
  python server.py %*
)
pause
