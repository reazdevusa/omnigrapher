@echo off
setlocal

:: Change to the folder containing this batch file (project root)
cd /d "%~dp0"

:: Launch the full stack in Docker Compose.
:: The PowerShell script will detect/start Docker Desktop and pull Ollama models.
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\start-all-services-tabs.ps1"

if errorlevel 1 (
    echo.
    echo Something went wrong. Press any key to close.
    pause > nul
)
