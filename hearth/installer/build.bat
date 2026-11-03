@echo off
rem Builds Scorpi-Setup.exe from Setup.cs and obj\app.zip (the app, packed by <app>\scripts\make_release.ps1).
rem   build.bat <version> <output exe> [app folder, default ..\ai]
setlocal
cd /d "%~dp0"
set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=1.0"
set "OUT=%~2"
if "%OUT%"=="" set "OUT=%~dp0Scorpi-Setup.exe"
set "APP=%~3"
if "%APP%"=="" set "APP=..\ai"
if not exist "obj\app.zip" (
  echo obj\app.zip is missing: run %APP%\scripts\make_release.ps1
  exit /b 1
)
> "obj\Version.cs" echo [assembly: System.Reflection.AssemblyVersion("%VERSION%.0.0")]
>> "obj\Version.cs" echo [assembly: System.Reflection.AssemblyFileVersion("%VERSION%.0.0")]
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
set "PICS=%APP%\server-control"
"%CSC%" /nologo /target:winexe /optimize+ /platform:anycpu /codepage:65001 /win32manifest:app.manifest ^
  /win32icon:"%APP%\public\icons\scorpi.ico" ^
  /resource:obj\app.zip,app.zip /resource:"%PICS%\scorpi.png",scorpi.png ^
  /resource:"%PICS%\scorpi-happy.png",scorpi-happy.png /resource:"%PICS%\scorpi-working.png",scorpi-working.png ^
  /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /r:System.Management.dll ^
  /r:System.Windows.Forms.dll /r:System.Drawing.dll ^
  /out:"%OUT%" Setup.cs obj\Version.cs
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo Built "%OUT%"
