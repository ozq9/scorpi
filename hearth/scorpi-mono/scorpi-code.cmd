@echo off
rem Scorpi's coding agent in the current folder:  scorpi-code [task]   (scorpi-code --help for options)
rem Uses the same models, keys and settings as the Scorpi app. Add this folder to PATH to use it anywhere.
setlocal
set "DIR=%~dp0"
set "PY=%DIR%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONPATH=%DIR%;%PYTHONPATH%"
"%PY%" -X utf8 -m scorpi.cli %*
