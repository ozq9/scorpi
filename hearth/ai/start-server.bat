@echo off
rem Runs the Scorpi server in this console window (shows addresses and the pairing PIN).
cd /d "%~dp0"
title Scorpi server
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" server.py %*
) else (
  python server.py %*
)
pause
