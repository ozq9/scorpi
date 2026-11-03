@echo off
setlocal
cd /d "%~dp0"
title Scorp setup
echo.
echo   Scorp setup
echo   ------------
echo.

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH". Then run setup.bat again.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --upgrade pip >nul

rem Install packages one by one so a single failure never blocks the rest.
for /f "usebackq eol=# tokens=*" %%p in ("requirements.txt") do (
  echo Installing %%p ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q "%%p" || echo    could not install %%p - the related tools stay disabled
)

if not exist "Scorp Server.exe" call "%~dp0server-control\build.bat" >nul

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-shortcuts.ps1"

echo.
echo   All set.
echo   1. Install Ollama (free) from https://ollama.com
echo   2. In a terminal run:  ollama pull qwen3:8b      (and optionally: ollama pull nomic-embed-text)
echo   3. Open Scorp from the desktop shortcut, or run Scorp.bat
echo.
pause
