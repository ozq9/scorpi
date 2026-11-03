<#
Builds Scorpi-Setup.exe for the website: one file that installs everything (see installer\Setup.cs).

    powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -Version 1.0

Steps: builds Scorpi.exe, packs the app (never your own data) into installer\obj\app.zip, builds the installer around it
into site\downloads\Scorpi-Setup.exe and writes site\version.json (read by the download buttons). Left out of the app:
data\ (chats, settings with API keys, certificates, workspace), .venv\, backups\, __pycache__\ and *.pyc; the installer
makes a fresh Python environment on the user's PC and the server creates data\ on first start.
#>
param([string]$Version = "1.0")

$ErrorActionPreference = "Stop"
$app = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $app
$installer = Join-Path $repo "installer"
$site = Join-Path $repo "site"
$out = Join-Path $site "downloads"
$stage = Join-Path ([IO.Path]::GetTempPath()) "scorpi-release\app"

# 1. Scorpi.exe, the one program inside
& cmd /c "`"$app\server-control\build.bat`""
if ($LASTEXITCODE -ne 0) { throw "Scorpi.exe did not build." }

# 2. the app, without anything personal
$skipDirs = @("data", ".venv", "backups", "__pycache__", "node_modules", ".git", "runtime")
$skipFiles = @("*.pyc", "*.pyo", "*.log", "*.pem", "*.db", "config.json", ".DS_Store", "uninstall.exe")
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage, $out, "$installer\obj" | Out-Null
$count = 0
foreach ($f in Get-ChildItem $app -Recurse -File -Force) {
    $rel = $f.FullName.Substring($app.Length + 1)
    $dirs = @($rel -split '\\' | Select-Object -SkipLast 1)
    if ($dirs | Where-Object { $skipDirs -contains $_ }) { continue }
    if ($skipFiles | Where-Object { $f.Name -like $_ }) { continue }
    $dest = Join-Path $stage $rel
    New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
    Copy-Item $f.FullName $dest
    $count++
}

# a last guard: refuse to ship anything that looks like a key or a private key
$leak = Get-ChildItem $stage -Recurse -File | Select-String -Pattern 'BEGIN (RSA |EC )?PRIVATE KEY|sk-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{30,}' -List
if ($leak) { $leak | ForEach-Object { Write-Host "  possible secret: $($_.Path)" }; throw "Release stopped: remove the secrets above first." }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = Join-Path $installer "obj\app.zip"
if (Test-Path $zip) { Remove-Item $zip }
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
Remove-Item (Split-Path -Parent $stage) -Recurse -Force

# 3. the installer around it
$exe = Join-Path $out "Scorpi-Setup.exe"
Get-ChildItem $out -File | Where-Object { $_.Name -ne "Scorpi-Setup.exe" } | Remove-Item -Force  # older downloads (zips)
& cmd /c "`"$installer\build.bat`" $Version `"$exe`""
if ($LASTEXITCODE -ne 0) { throw "Scorpi-Setup.exe did not build." }

$file = Get-Item $exe
$info = [ordered]@{
    version = $Version
    file = "downloads/Scorpi-Setup.exe"
    bytes = $file.Length
    sha256 = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
    date = (Get-Date).ToString("yyyy-MM-dd")
}
[IO.File]::WriteAllText((Join-Path $site "version.json"), ($info | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
"{0}  {1:N1} MB  ({2} app files)" -f $exe, ($file.Length / 1MB), $count
