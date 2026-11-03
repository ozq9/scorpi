# Creates Scorpi shortcuts (Desktop + Start menu) and optionally starts Scorpi in the tray at sign-in.
# -NoPrompt only (re)creates the shortcuts, e.g. after the icon changed.
param([switch]$NoPrompt)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
$icon = Join-Path $root 'public\icons\scorpi.ico'
$appId = 'Scorpi.Desktop'  # the window's taskbar identity (desktop.py); the shortcut that carries it gives the taskbar its name and icon
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
$startupDir = [Environment]::GetFolderPath('Startup')
# shortcuts from the app's old names (Hearth, Scorp, and the separate server control) make way for Scorpi's
$hadStartup = $false
foreach ($name in @('Hearth.lnk', 'Hearth Voice.lnk', 'Scorp.lnk', 'Scorp Voice.lnk', 'Scorp Server.lnk')) {
  foreach ($dir in @($desktop, $programs)) {
    $old = Join-Path $dir $name
    if (Test-Path $old) { Remove-Item $old }
  }
}
foreach ($name in @('Hearth.lnk', 'Hearth server.lnk', 'Scorp.lnk', 'Scorp server.lnk')) {
  $old = Join-Path $startupDir $name
  if (Test-Path $old) { Remove-Item $old; $hadStartup = $true }
}

# Scorpi.exe is the one program (built by server-control\build.bat); without it the shortcuts run desktop.py directly
$exe = Join-Path $root 'Scorpi.exe'
if (Test-Path $exe) { $target = $exe; $open = ''; $voice = '--path /voice'; $hidden = '--hidden' }
else { $target = $pythonw; $open = "`"$root\desktop.py`""; $voice = "$open --path /voice"; $hidden = "$open --hidden" }
New-Shortcut (Join-Path $desktop 'Scorpi.lnk') $target $open 'Scorpi - your own AI' $appId
New-Shortcut (Join-Path $programs 'Scorpi.lnk') $target $open 'Scorpi - your own AI' $appId
New-Shortcut (Join-Path $programs 'Scorpi Voice.lnk') $target $voice 'Talk to Scorpi' $null
Write-Host 'Shortcuts created on the Desktop and in the Start menu.'

$startup = Join-Path $startupDir 'Scorpi.lnk'
if ($hadStartup) { New-Shortcut $startup $target $hidden 'Scorpi in the tray' $null }
if ($NoPrompt) { exit 0 }

$answer = Read-Host 'Start Scorpi in the tray when you sign in? Recommended for reminders and routines (Y/n)'
if ($answer -eq '' -or $answer -match '^[yYjJ]') {
  New-Shortcut $startup $target $hidden 'Scorpi in the tray' $null
  Write-Host 'Scorpi will wait in the tray after sign-in.'
} elseif (Test-Path $startup) {
  Remove-Item $startup
}

$fw = Read-Host 'Allow phones and other PCs on your private network to connect (adds a firewall rule, needs admin)? (y/N)'
if ($fw -match '^[yYjJ]') {
  $cmd = "New-NetFirewallRule -DisplayName 'Scorpi' -Direction Inbound -Protocol TCP -LocalPort 8000,8443 -Action Allow -Profile Private"
  Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -Command $cmd"
}