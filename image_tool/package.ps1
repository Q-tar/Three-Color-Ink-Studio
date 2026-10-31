param([ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Name = 'InkStudio-WiFi')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Resolve DLLs only from this Python environment and Windows. Other tools on
# PATH can supply incompatible ICU/OpenSSL DLLs with the same filename.
$epdOriginalPath = $env:PATH
$epdBasePython = & '.\.venv\Scripts\python.exe' -c 'import sys; print(sys.base_prefix)'
try {
    $env:PATH = @(
        (Join-Path $PSScriptRoot '.venv\Scripts'),
        $epdBasePython,
        (Join-Path $epdBasePython 'DLLs'),
        (Join-Path $env:SystemRoot 'System32'),
        $env:SystemRoot
    ) -join ';'
    & '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --windowed --name $Name --add-data 'assets;assets' app.py
    if ($LASTEXITCODE -ne 0) { throw 'Application packaging failed' }
} finally {
    $env:PATH = $epdOriginalPath
}
$epdOutput = Join-Path $PSScriptRoot "dist\$Name"
New-Item -ItemType Directory -Path "$epdOutput\firmware\bin" -Force | Out-Null
Get-ChildItem -LiteralPath 'firmware\bin' -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination "$epdOutput\firmware\bin" -Force }
Copy-Item -LiteralPath 'firmware\flash_firmware.ps1' -Destination "$epdOutput\firmware\flash_firmware.ps1" -Force
Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.md' | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $epdOutput -Force }
New-Item -ItemType Directory -Path 'logs' -Force | Out-Null
$epdCheckReport = Join-Path $PSScriptRoot 'logs\packaged_self_check.json'
if (Test-Path -LiteralPath $epdCheckReport) { Remove-Item -LiteralPath $epdCheckReport }
$epdCheck = Start-Process -FilePath (Join-Path $epdOutput "$Name.exe") -ArgumentList @('--self-check', ('"' + $epdCheckReport + '"')) -WindowStyle Hidden -PassThru
if (-not $epdCheck.WaitForExit(30000)) {
    $epdCheck.Kill()
    throw 'Packaged runtime self-check timed out'
}
if ($epdCheck.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $epdCheckReport)) { throw 'Packaged runtime self-check failed' }
$epdCheckResult = Get-Content -LiteralPath $epdCheckReport -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $epdCheckResult.ok -or -not $epdCheckResult.frozen) { throw 'Packaged runtime self-check did not pass' }
Write-Host 'Packaged EXE self-check passed: preview, handshake, upload, save, restore, disconnect.'
