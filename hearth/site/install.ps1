# Installs Scorpi from PowerShell:  irm https://scorpi-atp.pages.dev/install.ps1 | iex
# Downloads Scorpi-Setup.exe, checks its SHA-256 against version.json, and runs it with --quiet.
# Optional: set $env:SCORPI_SETUP_ARGS first, e.g. '--no-desktop --startup' or '--dir D:\Apps\Scorpi'.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$base = 'https://scorpi-atp.pages.dev'
$v = Invoke-RestMethod "$base/version.json"
$exe = Join-Path $env:TEMP "Scorpi-Setup-$($v.version).exe"

Write-Host "Scorpi is in beta: expect rough edges. Feedback is very welcome at $base/#feedback"
Write-Host "Downloading Scorpi $($v.version) beta..."
Invoke-WebRequest "$base/$($v.file)" -OutFile $exe -UseBasicParsing

$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $v.sha256.ToLower()) {
    Remove-Item $exe -ErrorAction SilentlyContinue
    throw "Checksum mismatch: got $hash, expected $($v.sha256). Nothing was installed."
}

Write-Host 'Installing. Scorpi downloads Python and its packages (about 300 MB), so this takes a few minutes...'
$setupArgs = '--quiet'
if ($env:SCORPI_SETUP_ARGS) { $setupArgs += ' ' + $env:SCORPI_SETUP_ARGS }
$p = Start-Process -FilePath $exe -ArgumentList $setupArgs -Wait -PassThru
Remove-Item $exe -ErrorAction SilentlyContinue

switch ($p.ExitCode) {
    0 { Write-Host "Scorpi is installed. Open it from the Start menu. Tell us how it goes: $base/#feedback" }
    3 { Write-Host 'Scorpi is installed, but an optional package failed. Everything else works; the setup log has the details.' }
    default { throw "Setup failed with exit code $($p.ExitCode)." }
}
