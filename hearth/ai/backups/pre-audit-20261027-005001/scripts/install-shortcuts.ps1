# Creates Scorp shortcuts (Desktop + Start menu) and optionally starts the server at sign-in.
# -NoPrompt only (re)creates the shortcuts, e.g. after the icon changed.
param([switch]$NoPrompt)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
$icon = Join-Path $root 'public\icons\scorp.ico'
$appId = 'Scorp.Desktop'  # the window's taskbar identity (desktop.py); the shortcut that carries it gives the taskbar its name and icon
$shell = New-Object -ComObject WScript.Shell

# Windows reads a shortcut's AppUserModelID through IPropertyStore, which WScript.Shell can't write
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential, Pack = 4)] public struct PropertyKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit)] public struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore { void GetCount(out uint c); void GetAt(uint i, out PropertyKey k); void GetValue(ref PropertyKey k, out PropVariant v); void SetValue(ref PropertyKey k, ref PropVariant v); void Commit(); }
[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPersistFile { void GetClassID(out Guid g); [PreserveSig] int IsDirty(); void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint mode); void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool remember); void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f); void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f); }
public static class ShortcutAppId {
  public static void Set(string path, string id) {
    object link = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
    ((IPersistFile)link).Load(path, 2);
    PropertyKey key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
    PropVariant value = new PropVariant { vt = 31, p = Marshal.StringToCoTaskMemUni(id) };
    try { ((IPropertyStore)link).SetValue(ref key, ref value); ((IPropertyStore)link).Commit(); ((IPersistFile)link).Save(path, true); }
    finally { Marshal.FreeCoTaskMem(value.p); }
  }
}
"@

function New-Shortcut($path, $target, $arguments, $description, $id) {
  $s = $shell.CreateShortcut($path)
  $s.TargetPath = $target
  $s.Arguments = $arguments
  $s.WorkingDirectory = $root
  $s.IconLocation = "$icon,0"
  $s.Description = $description
  $s.Save()
  if ($id) { [ShortcutAppId]::Set($path, $id) }
}

$desktop = [Environment]::GetFolderPath('Desktop')
$programs = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'
# shortcuts from when the app had its old name make way for the Scorp ones
foreach ($old in @((Join-Path $desktop 'Hearth.lnk'), (Join-Path $programs 'Hearth.lnk'), (Join-Path $programs 'Hearth Voice.lnk'))) {
  if (Test-Path $old) { Remove-Item $old }
}
New-Shortcut (Join-Path $desktop 'Scorp.lnk') $pythonw "`"$root\desktop.py`"" 'Scorp - your local AI' $appId
New-Shortcut (Join-Path $programs 'Scorp.lnk') $pythonw "`"$root\desktop.py`"" 'Scorp - your local AI' $appId
New-Shortcut (Join-Path $programs 'Scorp Voice.lnk') $pythonw "`"$root\desktop.py`" --path /voice" 'Talk to Scorp' $null
$control = Join-Path $root 'Scorp Server.exe'  # built by server-control\build.bat
if (Test-Path $control) {
  New-Shortcut (Join-Path $desktop 'Scorp Server.lnk') $control '' 'Start, stop and restart the Scorp server and app' $null
  New-Shortcut (Join-Path $programs 'Scorp Server.lnk') $control '' 'Start, stop and restart the Scorp server and app' $null
}
Write-Host 'Shortcuts created on the Desktop and in the Start menu.'
if ($NoPrompt) { exit 0 }

$answer = Read-Host 'Start the Scorp server automatically when you sign in? Recommended for reminders and routines (Y/n)'
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Scorp server.lnk'
$oldStartup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Hearth server.lnk'
if (Test-Path $oldStartup) { Remove-Item $oldStartup; $answer = 'y' }
if ($answer -eq '' -or $answer -match '^[yYjJ]') {
  New-Shortcut $startup $pythonw "`"$root\server.py`"" 'Scorp background server' $null
  Write-Host 'Scorp will start in the background at sign-in.'
} elseif (Test-Path $startup) {
  Remove-Item $startup
}

$fw = Read-Host 'Allow phones and other PCs on your private network to connect (adds a firewall rule, needs admin)? (y/N)'
if ($fw -match '^[yYjJ]') {
  $cmd = "New-NetFirewallRule -DisplayName 'Scorp' -Direction Inbound -Protocol TCP -LocalPort 8000,8443 -Action Allow -Profile Private"
  Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -Command $cmd"
}
