@echo off
cd /d "%~dp0"
if exist "dist\InkStudio-WiFi\InkStudio-WiFi.exe" (
    start "" "dist\InkStudio-WiFi\InkStudio-WiFi.exe"
    exit /b
)
if exist "dist\InkStudio\InkStudio.exe" (
    start "" "dist\InkStudio\InkStudio.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "app.py"
    exit /b
)
echo Please run setup.ps1 first.
pause
