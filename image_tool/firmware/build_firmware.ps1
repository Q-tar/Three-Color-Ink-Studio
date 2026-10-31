param([switch]$Flash, [string]$Port = "COM9", [string]$IdfProfile = "")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if ($IdfProfile) {
    . $IdfProfile
} elseif (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    $epdProfile = "C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1"
    if (-not (Test-Path -LiteralPath $epdProfile)) {
        throw "Open an ESP-IDF 5.4 terminal, or specify -IdfProfile with your ESP-IDF PowerShell profile path."
    }
    . $epdProfile
}
$epdVersion = idf.py --version
if ($LASTEXITCODE -ne 0 -or ($epdVersion -join " ") -notmatch 'ESP-IDF v5\.4(?:\.|\s|$|-)') {
    throw "ESP-IDF 5.4.x is required"
}
idf.py build
if ($LASTEXITCODE -ne 0) { throw "Firmware build failed" }
python "$PSScriptRoot\make_bundle.py"
if ($LASTEXITCODE -ne 0) { throw "Firmware bundle failed" }
if ($Flash) {
    & "$PSScriptRoot\flash_firmware.ps1" -Port $Port
    if ($LASTEXITCODE -ne 0) { throw "Firmware flash failed" }
}
