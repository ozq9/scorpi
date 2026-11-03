@echo off
rem Builds "Scorp Server.exe" into the ai folder with the C# compiler that ships with Windows (.NET Framework 4).
setlocal
cd /d "%~dp0"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
"%CSC%" /nologo /target:winexe /optimize+ /platform:anycpu ^
  /win32icon:"..\public\icons\scorp.ico" ^
  /r:System.Management.dll /r:System.Web.Extensions.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll ^
  /out:"..\Scorp Server.exe" ScorpServer.cs
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo Built "%~dp0..\Scorp Server.exe"
