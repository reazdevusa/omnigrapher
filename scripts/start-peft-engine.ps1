#Requires -Version 5.1
<#
.SYNOPSIS
    Start the OmniGrapher PEFT Engine on a free port.

.DESCRIPTION
    Scans 8003-8012 for a free port (never kills a foreign process), records
    the choice as PEFT_ENGINE_PORT in the repo-root .env so the ai-gateway's
    PEFT_ENGINE_URL follows on the next `docker compose up`, then launches
    the uvicorn server in this window.
#>
param(
    [int]$Base = 8003,
    [int]$Attempts = 10
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\port-utils.ps1"

$repoRoot = Split-Path $PSScriptRoot -Parent
$envFile  = Join-Path $repoRoot ".env"
$engineDir = Join-Path $repoRoot "peft_engine"

# Prefer the port already recorded in .env if it is still free.
$port = $null
$existing = Get-EnvPort "PEFT_ENGINE_PORT" $Base $envFile
if (Test-PortFree $existing) {
    $port = $existing
} else {
    foreach ($p in $Base..($Base + $Attempts - 1)) {
        if (Test-PortFree $p) { $port = $p; break }
        Write-Host "Port $p in use by $(Get-PortOwner $p) - trying next."
    }
}
if (-not $port) { throw "No free port found in range $Base-$($Base + $Attempts - 1)." }

Write-EnvFile -Path $envFile -Values @{ PEFT_ENGINE_PORT = $port }
if ($port -ne $Base) {
    Write-Host "PEFT Engine port moved to $port - restart the stack (scripts\up.ps1) so the gateway picks it up."
}

Write-Host "Starting OmniGrapher PEFT Engine (Multi-LoRA Adapter Server)..."
Write-Host "Base Model: Qwen2.5-3B-Instruct (4-bit quantized)"
Write-Host "Port:       $port"
Write-Host ""

Set-Location $engineDir
& ".\.venv\Scripts\python.exe" -m uvicorn peft_engine.main:app --host 0.0.0.0 --port $port
