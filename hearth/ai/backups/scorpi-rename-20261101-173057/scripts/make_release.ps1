<#
Packs Scorp into a clean download for the website: the app and its setup, never your own data.

    powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -Version 1.0

Writes site\downloads\Scorp-<version>.zip and site\version.json (read by the download button). Left out: data\ (chats,
settings with API keys, certificates, workspace), .venv\, backups\, __pycache__\ and *.pyc. setup.bat rebuilds the
environment on the user's PC and the server creates data\ on first start.
#>
param([string]$Version = "1.0")

$ErrorActionPreference = "Stop"
$app = Split-Path -Parent $PSScriptRoot
$site = Join-Path (Split-Path -Parent $app) "site"
$out = Join-Path $site "downloads"
$name = "Scorp-$Version"
$stage = Join-Path ([IO.Path]::GetTempPath()) "scorp-release\$name"

$skipDirs = @("data", ".venv", "backups", "__pycache__", "node_modules", ".git")
$skipFiles = @("*.pyc", "*.pyo", "*.log", "*.pem", "*.db", "config.json", ".DS_Store")

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage, $out | Out-Null

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

# A last guard: refuse to ship anything that looks like a key or a private key.
$leak = Get-ChildItem $stage -Recurse -File | Select-String -Pattern 'BEGIN (RSA |EC )?PRIVATE KEY|sk-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{30,}' -List
if ($leak) { $leak | ForEach-Object { Write-Host "  possible secret: $($_.Path)" }; throw "Release stopped: remove the secrets above first." }

$zip = Join-Path $out "$name.zip"
Get-ChildItem $out -Filter "Scorp-*.zip" | Remove-Item -Force
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
Remove-Item (Split-Path -Parent $stage) -Recurse -Force

$file = Get-Item $zip
$info = [ordered]@{
    version = $Version
    file = "downloads/$name.zip"
    bytes = $file.Length
    sha256 = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    date = (Get-Date).ToString("yyyy-MM-dd")
}
$info | ConvertTo-Json | Set-Content (Join-Path $site "version.json") -Encoding utf8
"{0}  {1:N1} MB  ({2} files)" -f $zip, ($file.Length / 1MB), $count
