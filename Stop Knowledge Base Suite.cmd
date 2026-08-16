@echo off
setlocal

:: Change to the folder containing this batch file (project root)
cd /d "%~dp0"

:: Only run docker compose down if the Docker daemon is actually reachable.
:: This prevents the ugly "failed to connect to docker API" error when
:: Docker Desktop is not running (no containers are active in that case).
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo Docker Desktop is not running. No containers to stop.
    goto :finish
)

:: Stop the full Docker Compose stack
docker compose -f "docker-compose.yml" down

:finish
echo.
echo Done.
pause
