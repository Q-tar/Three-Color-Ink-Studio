param(
    [Parameter(Mandatory=$true)][ValidatePattern('^COM\d+$')][string]$Port,
    [string]$PythonPath = '',
    [string]$EspToolPath = '',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$env:PYTHONIOENCODING = 'utf-8'
if (-not $PythonPath) {
    if ($env:IDF_PYTHON_ENV_PATH -and (Test-Path -LiteralPath "$env:IDF_PYTHON_ENV_PATH\Scripts\python.exe")) {
        $PythonPath = "$env:IDF_PYTHON_ENV_PATH\Scripts\python.exe"
    } elseif (Test-Path -LiteralPath 'C:\Espressif\tools\python\v5.4.4\venv\Scripts\python.exe') {
        $PythonPath = 'C:\Espressif\tools\python\v5.4.4\venv\Scripts\python.exe'
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonPath = (Get-Command python).Source
    } else { throw 'Python not found. Open an ESP-IDF terminal or specify -PythonPath.' }
}
if (-not $EspToolPath) {
    if ($env:IDF_PATH -and (Test-Path -LiteralPath "$env:IDF_PATH\components\esptool_py\esptool\esptool.py")) {
        $EspToolPath = "$env:IDF_PATH\components\esptool_py\esptool\esptool.py"
    } elseif (Test-Path -LiteralPath 'D:\Software\ESP-IDF\.espressif\.espressif\v5.4.4\esp-idf\components\esptool_py\esptool\esptool.py') {
        $EspToolPath = 'D:\Software\ESP-IDF\.espressif\.espressif\v5.4.4\esp-idf\components\esptool_py\esptool\esptool.py'
    }
}
$epdManifestPath = Join-Path $PSScriptRoot 'bin\bundle.json'
if (-not (Test-Path -LiteralPath $PythonPath)) { throw "IDF Python not found: $PythonPath" }
if ($EspToolPath) {
    if (-not (Test-Path -LiteralPath $EspToolPath)) { throw "esptool not found: $EspToolPath" }
    $epdToolArgs = @($EspToolPath)
} else {
    & $PythonPath -c 'import esptool' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'esptool is missing. Install it with python -m pip install esptool==5.2.0, or specify -EspToolPath.' }
    $epdToolArgs = @('-m', 'esptool')
}
if (-not (Test-Path -LiteralPath $epdManifestPath)) { throw 'Build the receiver firmware first with build_firmware.ps1' }
$epdManifest = Get-Content -LiteralPath $epdManifestPath -Raw | ConvertFrom-Json
if ($epdManifest.chip -ne 'esp32c3') { throw 'Firmware is not for ESP32-C3' }
$epdArgs = $epdToolArgs + @('--chip', 'esp32c3', '--port', $Port, '--baud', '460800', '--before', 'default_reset', '--after', 'hard_reset', 'write_flash', '--flash_mode', $epdManifest.flash_mode, '--flash_freq', $epdManifest.flash_freq, '--flash_size', $epdManifest.flash_size)
foreach ($epdFile in $epdManifest.files) {
    $epdBinary = Join-Path $PSScriptRoot ('bin\' + $epdFile.file)
    if (-not (Test-Path -LiteralPath $epdBinary)) { throw "Missing binary: $epdBinary" }
    if ((Get-FileHash -LiteralPath $epdBinary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $epdFile.sha256) {
        throw "Binary integrity check failed: $epdBinary"
    }
    $epdArgs += @($epdFile.offset, $epdBinary)
}
if ($CheckOnly) {
    Write-Host "Firmware hashes and tool paths verified. No serial port opened. Python: $PythonPath"
    exit 0
}
& $PythonPath @epdArgs
if ($LASTEXITCODE -ne 0) { throw 'Flash failed. Close other serial monitors and check the selected port.' }
Write-Host 'Receiver firmware installed. Open Ink Studio and connect to the device.'
