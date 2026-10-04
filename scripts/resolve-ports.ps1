#Requires -Version 5.1
<#
.SYNOPSIS
    Resolve free host ports for every published service and persist to .env.

.DESCRIPTION
    For each host-published port in docker-compose.yml, checks the listen
    table. A port held by one of OUR containers is reused; a port held by any
    foreign process is skipped in favour of the next free port. Results are
    written to the repo-root .env so `docker compose` picks them up via
    ${VAR:-default} interpolation.

    Run this BEFORE `docker compose up -d`.
#>
param(
    [int]$Attempts = 10
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\port-utils.ps1"

$repoRoot = Split-Path $PSScriptRoot -Parent
$envFile  = Join-Path $repoRoot ".env"

# Host-published services, resolved in this order. Container names are used
# for the "held by us" check so re-runs keep their existing mapping.
$services = @(
    @{ Var = "FRONTEND_PORT"; Default = 3000;  Container = "knowledge-base-frontend" },
    @{ Var = "BACKEND_PORT";  Default = 8001;  Container = "knowledge-base-backend"  },
    @{ Var = "GATEWAY_PORT";  Default = 8005;  Container = "omnigrapher-ai-gateway"  },
    @{ Var = "OLLAMA_PORT";   Default = 11434; Container = "knowledge-base-ollama"   },
    @{ Var = "CHROMADB_PORT"; Default = 8002;  Container = "knowledge-base-chromadb" },
    @{ Var = "REDIS_PORT";    Default = 6379;  Container = "knowledge-base-redis"    },
    @{ Var = "POSTGRES_PORT"; Default = 5433;  Container = "knowledge-base-postgres" }
)

Write-Host "Resolving host ports (env file: $envFile)..."
$resolved = Resolve-ServicePorts -Services $services -EnvPath $envFile -Attempts $Attempts
Write-EnvFile -Path $envFile -Values $resolved

foreach ($k in $resolved.Keys) {
    Write-Host ("  {0,-15} -> {1}" -f $k, $resolved[$k])
}
