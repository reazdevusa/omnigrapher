#Requires -Version 5.1
<#
.SYNOPSIS
    Start the full AI Knowledge Base Suite with one click.

.DESCRIPTION
    Starts Ollama (if not already running), the FastAPI backend, the Next.js
    frontend, and optionally the desktop app and watchdog monitor.  Service PIDs
    are saved to scripts/.services.json so they can be stopped later.

.EXAMPLE
    .\scripts\start-all-services.ps1
    .\scripts\start-all-services.ps1 -Desktop
#>
param(
    [switch]$NoWatchdog,
    [switch]$Desktop,
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 3000,
    [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe",
    [string]$BackendDir = "$PSScriptRoot\..\knowledge_base_pilot",
    [string]$FrontendDir = "$PSScriptRoot\..\web_app_nextjs",
    [string]$DesktopDir = "$PSScriptRoot\..\desktop_app",
    [string]$ComposeFile = "$PSScriptRoot\..\docker-compose.yml"
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] $Message"
}

function Test-Port {
    param([string]$HostName = "127.0.0.1", [int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $connect = $client.ConnectAsync($HostName, $Port)
        if ($connect.Wait(1500)) {
            $client.Close()
            return $true
        }
        $client.Dispose()
    } catch {}
    return $false
}

function Wait-For-Port {
    param([int]$Port, [int]$TimeoutSeconds = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port -Port $Port) { return }
        Start-Sleep -Seconds 1
    }
    throw "Timed out waiting for service on port $Port"
}

function Test-HttpEndpoint {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

$runFile = Join-Path $PSScriptRoot ".services.json"
$logDir  = Join-Path (Join-Path $PSScriptRoot "..") "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Clean up any previous run
if (Test-Path $runFile) {
    Write-Log "Found previous service state; stopping it first..."
    & "$PSScriptRoot\stop-all-services.ps1" -Quiet
}

$state = [ordered]@{
    startedAt = (Get-Date -Format "o")
    services  = @()
}

# ---------------------------------------------------------------------------
# 1. Docker infrastructure (PostgreSQL, ChromaDB, Redis)
# ---------------------------------------------------------------------------
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Log "Starting Docker infrastructure..."
    docker compose -f $ComposeFile up -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up -d failed with exit code $LASTEXITCODE"
    }
    Wait-For-Port -Port 5433 -TimeoutSeconds 120
    Wait-For-Port -Port 8002 -TimeoutSeconds 120
    Wait-For-Port -Port 6379 -TimeoutSeconds 120
    Write-Log "Docker infrastructure is ready."
} else {
    Write-Log "Docker not found in PATH. Assuming infrastructure is already running."
}

# ---------------------------------------------------------------------------
# 2. Ollama
# ---------------------------------------------------------------------------
$ollamaRunning = Test-Port -Port 11434
if (-not $ollamaRunning) {
    Write-Log "Starting Ollama..."
    $ollamaProc = Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "ollama serve" -PassThru -WindowStyle Normal
    $state.services += [ordered]@{
        name = "ollama"
        pid  = $ollamaProc.Id
        port = 11434
        cmd  = "ollama serve"
    }
    Wait-For-Port -Port 11434 -TimeoutSeconds 60
    Write-Log "Ollama is ready."
} else {
    Write-Log "Ollama is already running on port 11434."
}

# ---------------------------------------------------------------------------
# 2. Backend
# ---------------------------------------------------------------------------
if (-not (Test-Path $Python)) {
    Write-Log "Virtual-env python not found at $Python; falling back to 'python'."
    $Python = "python"
}

$backendHealthy = $false
if (Test-Port -Port $BackendPort) {
    $backendHealthy = Test-HttpEndpoint -Uri "http://localhost:$BackendPort/"
}
if ($backendHealthy) {
    Write-Log "Backend is already responding on port $BackendPort; skipping."
} else {
    if (Test-Port -Port $BackendPort) {
        $stalePid = (Get-NetTCPConnection -LocalPort $BackendPort -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
        if ($stalePid -and $stalePid -gt 0) {
            Write-Log "Port $BackendPort is occupied by unhealthy PID $stalePid; terminating it..."
            try { Stop-Process -Id $stalePid -Force -ErrorAction SilentlyContinue } catch {}
            try { taskkill /PID $stalePid /T /F 2>&1 | Out-Null } catch {}
            Start-Sleep -Seconds 2
        }
    }
    Write-Log "Starting backend on http://localhost:$BackendPort ..."
    $backendCmd = "cd '$BackendDir'; & '$Python' -m pip install -r requirements.txt; if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }; & '$Python' -m uvicorn app.main:app --reload --host 0.0.0.0 --port $BackendPort"
    $backendProc = Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $backendCmd -PassThru -WindowStyle Normal
    $state.services += [ordered]@{
        name = "backend"
        pid  = $backendProc.Id
        port = $BackendPort
        cmd  = $backendCmd
    }
    Wait-For-Port -Port $BackendPort -TimeoutSeconds 120
    Write-Log "Backend is ready."
}

# ---------------------------------------------------------------------------
# 3. Celery ingestion worker
# ---------------------------------------------------------------------------
Write-Log "Starting Celery ingestion worker..."
$celeryCmd = "cd '$BackendDir'; & '$Python' -m celery -A app.celery_app worker -Q ingestion -l info --pool=solo"
$celeryProc = Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $celeryCmd -PassThru -WindowStyle Normal
$state.services += [ordered]@{
    name = "celery"
    pid  = $celeryProc.Id
    port = $null
    cmd  = $celeryCmd
}
Write-Log "Celery worker is running."

# ---------------------------------------------------------------------------
# 4. Next.js frontend
# ---------------------------------------------------------------------------
$frontendHealthy = $false
if (Test-Port -Port $FrontendPort) {
    $frontendHealthy = Test-HttpEndpoint -Uri "http://localhost:$FrontendPort/"
}
if ($frontendHealthy) {
    Write-Log "Frontend is already responding on port $FrontendPort; skipping."
} else {
    if (Test-Port -Port $FrontendPort) {
        $stalePid = (Get-NetTCPConnection -LocalPort $FrontendPort -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
        if ($stalePid -and $stalePid -gt 0) {
            Write-Log "Port $FrontendPort is occupied by unhealthy PID $stalePid; terminating it..."
            try { Stop-Process -Id $stalePid -Force -ErrorAction SilentlyContinue } catch {}
            try { taskkill /PID $stalePid /T /F 2>&1 | Out-Null } catch {}
            Start-Sleep -Seconds 2
        }
    }
    Write-Log "Starting Next.js frontend on http://localhost:$FrontendPort ..."
    $frontendCmd = "cd '$FrontendDir'; npm run dev -- --port $FrontendPort"
    $frontendProc = Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $frontendCmd -PassThru -WindowStyle Normal
    $state.services += [ordered]@{
        name = "frontend"
        pid  = $frontendProc.Id
        port = $FrontendPort
        cmd  = $frontendCmd
    }
    Wait-For-Port -Port $FrontendPort -TimeoutSeconds 120
    Write-Log "Frontend is ready."
}

# ---------------------------------------------------------------------------
# 5. Desktop app (optional)
# ---------------------------------------------------------------------------
if ($Desktop) {
    Write-Log "Starting desktop app..."
    $desktopCmd = "cd '$DesktopDir\src'; & '$Python' main.py"
    $desktopProc = Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $desktopCmd -PassThru -WindowStyle Normal
    $state.services += [ordered]@{
        name = "desktop"
        pid  = $desktopProc.Id
        port = $null
        cmd  = $desktopCmd
    }
}

# Persist state before launching watchdog
$state | ConvertTo-Json -Depth 5 | Set-Content -Path $runFile -Encoding UTF8
Write-Log "Service state saved to $runFile"

# ---------------------------------------------------------------------------
# 6. Watchdog
# ---------------------------------------------------------------------------
if (-not $NoWatchdog) {
    Write-Log "Starting watchdog monitor..."
    $wdCmd = "cd '$PSScriptRoot'; powershell.exe -ExecutionPolicy Bypass -File watchdog.ps1"
    $wdProc = Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $wdCmd -PassThru -WindowStyle Normal
    $state.services += [ordered]@{
        name = "watchdog"
        pid  = $wdProc.Id
        port = $null
        cmd  = $wdCmd
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content -Path $runFile -Encoding UTF8
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Log "All services are running. Opening http://localhost:$FrontendPort ..."
Start-Process "http://localhost:$FrontendPort"
Write-Log "To stop everything, run: scripts\stop-all-services.ps1"
