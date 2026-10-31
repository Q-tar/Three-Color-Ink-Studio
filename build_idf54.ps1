param([string]$Port = "", [switch]$Flash, [switch]$SoftwareSpi)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$epdIdfProfile = "C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1"
if (Test-Path -LiteralPath $epdIdfProfile) {
    . $epdIdfProfile
} else {
    $epdIdfRoot = "D:\Software\ESP-IDF\.espressif\.espressif\v5.4.4\esp-idf"
    if (-not (Test-Path -LiteralPath "$epdIdfRoot\export.ps1")) {
        $epdIdfRoot = "D:\Software\ESP-IDF\.espressif\v5.4\esp-idf"
    }
    . "$epdIdfRoot\export.ps1"
}
if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    throw "ESP-IDF environment is incomplete. Install ESP-IDF 5.4 ESP32-C3 tools using Espressif Installation Manager."
}
$epdIdfVersion = idf.py --version
if ($LASTEXITCODE -ne 0) { throw "Cannot determine IDF version" }
Write-Host $epdIdfVersion
if (($epdIdfVersion -join " ") -notmatch 'ESP-IDF v5\.4(?:\.|\s|$|-)') {
    throw "This project requires ESP-IDF 5.4.x. Check the selected environment."
}
if (-not (Test-Path -LiteralPath "sdkconfig")) {
    idf.py set-target esp32c3
    if ($LASTEXITCODE -ne 0) { throw "Target setup failed" }
} elseif (-not (Select-String -LiteralPath "sdkconfig" -Pattern '^CONFIG_IDF_TARGET="esp32c3"$' -Quiet)) {
    throw "Existing sdkconfig target is not ESP32-C3. Review it before changing target."
}
$epdSpiOption = if ($SoftwareSpi) { "ON" } else { "OFF" }
idf.py -D "EPD_SOFTWARE_SPI=$epdSpiOption" build
if ($LASTEXITCODE -ne 0) { throw "Build failed" }
if ($Flash) {
    if (-not $Port) { throw "Specify -Port COMx" }
    idf.py -p $Port flash monitor
    if ($LASTEXITCODE -ne 0) { throw "Flash or monitor failed. Check the port and close other serial monitors." }
}
