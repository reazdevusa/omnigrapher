@echo off
setlocal

:: Change to the folder containing this batch file (project root)
cd /d "%~dp0"

:: Stop the full Docker Compose stack
docker compose -f "docker-compose.yml" down

echo.
echo Done.
pause
