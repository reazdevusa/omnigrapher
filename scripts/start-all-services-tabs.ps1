#Requires -Version 5.1
<#
.SYNOPSIS
    Start the full AI Knowledge Base Suite via Docker Compose.

.DESCRIPTION
    Verifies Docker, brings up all services (Postgres, ChromaDB, Redis,
    Ollama, FastAPI backend, Celery worker, Next.js frontend), pulls the
    required Ollama models if they are missing, and opens the app.

.EXAMPLE
    .\scripts\start-all-services-tabs.ps1
#>
param(
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 3000,
    [string]$ComposeFile = "$PSScriptRoot\..\docker-compose.yml"
)

$ErrorActionPreference = "Stop"

# Run from the directory that contains this script.
Set-Location -LiteralPath $PSScriptRoot

function Write-Log {
    param(
        [string]$Message,
        [ConsoleColor]$Color = [ConsoleColor]::White
    )
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] $Message" -ForegroundColor $Color
}

function Wait-For-HttpEndpoint {
    param([string]$Uri, [int]$TimeoutSeconds = 120)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Log "Waiting for $Uri to become healthy (up to ${TimeoutSeconds}s)..." Cyan
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host ""  # newline after dots
                Write-Log "$Uri is healthy." Green
                return
            }
        } catch {
            # Expected while the container is still booting; do not throw.
        }
        Write-Host "." -ForegroundColor Yellow -NoNewline
        Start-Sleep -Seconds 3
    }
    Write-Host ""  # newline after dots
    throw "Timed out waiting for $Uri"
}

function Test-DockerDaemon {
    try {
        docker info *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$DockerDesktopPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

Write-Log "[1/5] Verifying Docker daemon..." Cyan
if (-not (Test-DockerDaemon)) {
    $dockerPaths = @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    $DockerDesktopPath = $dockerPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $DockerDesktopPath) {
        throw "Docker Desktop is not running and could not be found."
    }
    Write-Log "Docker is offline. Starting Docker Desktop..." Yellow
    Start-Process -FilePath $DockerDesktopPath -WindowStyle Hidden | Out-Null

    $retries = 30  # 30 * 4s = 120s
    $ready = $false
    for ($i = 0; $i -lt $retries; $i++) {
        if (Test-DockerDaemon) { $ready = $true; break }
        Write-Host "." -ForegroundColor Yellow -NoNewline
        Start-Sleep -Seconds 4
    }
    if (-not $ready) { throw "Docker daemon did not become ready within 120 seconds." }
    Write-Host " ready" -ForegroundColor Green
} else {
    Write-Log "Docker daemon is healthy." Green
}

if (-not (Test-Path $ComposeFile)) {
    throw "Docker Compose file was not found at $ComposeFile"
}

Write-Log "[2/5] Starting Docker Compose stack..." Cyan
docker compose -f $ComposeFile up -d
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up -d failed with exit code $LASTEXITCODE"
}

Write-Log "[3/5] Waiting for Ollama to be ready..." Cyan
Wait-For-HttpEndpoint -Uri "http://localhost:11434/api/tags" -TimeoutSeconds 600

Write-Log "[4/5] Checking/pulling required Ollama models..." Cyan
$embeddingModel = "nomic-embed-text:latest"
$primaryModel = "llama3.2:latest"
$availableModels = (Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 10).models.name

if ($embeddingModel -notin $availableModels) {
    Write-Log "Pulling $embeddingModel..." Yellow
    docker exec knowledge-base-ollama ollama pull nomic-embed-text | Out-Null
}
if ($primaryModel -notin $availableModels) {
    Write-Log "Pulling $primaryModel..." Yellow
    docker exec knowledge-base-ollama ollama pull llama3.2 | Out-Null
}

$availableModels = (Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 10).models.name
if ($embeddingModel -notin $availableModels) {
    throw "Required Ollama embedding model is missing after auto-pull: $embeddingModel"
}
Write-Log "Ollama models verified." Green

Write-Log "[5/5] Waiting for backend and frontend to be ready..." Cyan
Wait-For-HttpEndpoint -Uri "http://localhost:$BackendPort/" -TimeoutSeconds 120
$frontendUrl = "http://localhost:$FrontendPort/"
Wait-For-HttpEndpoint -Uri $frontendUrl -TimeoutSeconds 120
Write-Log "Backend and frontend are healthy." Green

Start-Process $frontendUrl
Write-Log "Opening $frontendUrl" Green

# Open a dedicated, always-on log window for the main services.
$logCommand = "Set-Location '$PSScriptRoot'; docker compose -f '$ComposeFile' logs -f backend frontend ollama"
Start-Process powershell -ArgumentList "-NoExit -Command $logCommand"
