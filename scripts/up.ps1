#Requires -Version 5.1
<#
.SYNOPSIS
    Start the OmniGrapher Docker stack, auto-selecting free host ports.

.DESCRIPTION
    Resolves a usable host port for EVERY published service (frontend,
    backend, ai-gateway, ollama, chromadb, redis, postgres). A port held by
    one of our own containers is reused; a port held by anything else (e.g.
    another project's dev server) is skipped - nothing foreign is ever killed.
    Chosen ports are persisted to the repo-root .env so plain
    `docker compose` commands keep using them.

.EXAMPLE
    .\scripts\up.ps1              # start everything, auto-pick ports
    .\scripts\up.ps1 -Open        # also open the app in the browser
#>
param(
    [switch]$Open,
    [int]$Attempts = 10
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$envFile  = Join-Path $repoRoot ".env"

. "$PSScriptRoot\port-utils.ps1"

function Write-Log([string]$Message) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

# ---------------------------------------------------------------------------
# Resolve host ports for every published service
# ---------------------------------------------------------------------------
$services = @(
    @{ Var = "FRONTEND_PORT"; Default = 3000;  Container = "knowledge-base-frontend" },
    @{ Var = "BACKEND_PORT";  Default = 8001;  Container = "knowledge-base-backend"  },
    @{ Var = "GATEWAY_PORT";  Default = 8005;  Container = "omnigrapher-ai-gateway"  },
    @{ Var = "OLLAMA_PORT";   Default = 11434; Container = "knowledge-base-ollama"   },
    @{ Var = "CHROMADB_PORT"; Default = 8002;  Container = "knowledge-base-chromadb" },
    @{ Var = "REDIS_PORT";    Default = 6379;  Container = "knowledge-base-redis"    },
    @{ Var = "POSTGRES_PORT"; Default = 5433;  Container = "knowledge-base-postgres" }
)

$resolved = Resolve-ServicePorts -Services $services -EnvPath $envFile -Attempts $Attempts
Write-EnvFile -Path $envFile -Values $resolved
$frontendPort = $resolved["FRONTEND_PORT"]
$backendPort  = $resolved["BACKEND_PORT"]

Write-Log "Ports: frontend=$frontendPort backend=$backendPort gateway=$($resolved['GATEWAY_PORT']) ollama=$($resolved['OLLAMA_PORT'])"

# ---------------------------------------------------------------------------
# Start the stack
# ---------------------------------------------------------------------------
Write-Log "Starting Docker stack..."
docker compose -f (Join-Path $repoRoot "docker-compose.yml") up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed (exit $LASTEXITCODE)" }

# Wait for the frontend to accept connections (Next dev compile can be slow).
$deadline = (Get-Date).AddSeconds(180)
$up = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$frontendPort" -UseBasicParsing -TimeoutSec 4
        if ($r.StatusCode -lt 500) { $up = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
if ($up) {
    Write-Log "Frontend is live: http://localhost:$frontendPort"
    if ($Open) { Start-Process "http://localhost:$frontendPort" }
} else {
    Write-Log "Frontend container started but not yet responding - Next.js is still compiling. Check: docker logs -f knowledge-base-frontend"
}
