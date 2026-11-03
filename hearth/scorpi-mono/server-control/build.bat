@echo off
rem Builds Scorpi.exe into the ai folder with the C# compiler that ships with Windows (.NET Framework 4).
setlocal
cd /d "%~dp0"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
"%CSC%" /nologo /target:winexe /optimize+ /platform:anycpu /codepage:65001 ^
  /win32icon:"..\public\icons\scorpi.ico" /resource:scorpi.png,scorpi.png ^
  /r:System.Management.dll /r:System.Web.Extensions.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll ^
  /out:"..\Scorpi.exe" Scorpi.cs
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo Built "%~dp0..\Scorpi.exe"
