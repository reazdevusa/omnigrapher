#Requires -Version 5.1
<#
.SYNOPSIS
    Stop all services started by start-all-services.ps1.

.DESCRIPTION
    Reads scripts/.services.json and terminates each recorded process tree.
    Ollama is only stopped if it was started by the start script.
#>
param(
    [switch]$Quiet,
    [string]$ComposeFile = "$PSScriptRoot\..\docker-compose.yml"
)

function Write-Log {
    param([string]$Message)
    if (-not $Quiet) {
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host "[$ts] $Message"
    }
}

$runFile = Join-Path $PSScriptRoot ".services.json"

if (-not (Test-Path $runFile)) {
    Write-Log "No running services state found."
    exit 0
}

$state = Get-Content $runFile | ConvertFrom-Json
$state.services | ForEach-Object {
    $svc = $_
    Write-Log "Stopping $($svc.name) (PID $($svc.pid))..."
    try {
        Stop-Process -Id $svc.pid -Force -ErrorAction SilentlyContinue
    } catch {}
    try {
        taskkill /PID $svc.pid /T /F 2>&1 | Out-Null
    } catch {}
}

Remove-Item $runFile -Force -ErrorAction SilentlyContinue

# Forcefully kill any leftover processes still occupying key ports (8001, 3000)
# even if they were not tracked in .services.json (e.g. orphaned uvicorn / next dev).
foreach ($port in @(8001, 3000)) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
        if ($conn.OwningProcess -and $conn.OwningProcess -gt 0) {
            Write-Log "Force-killing leftover process PID $($conn.OwningProcess) on port $port..."
            try { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
            try { taskkill /PID $conn.OwningProcess /T /F 2>&1 | Out-Null } catch {}
        }
    }
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Log "Stopping Docker infrastructure..."
    docker compose -f $ComposeFile down
}

Write-Log "All tracked services stopped."
