#Requires -Version 5.1
<#
.SYNOPSIS
    Start the OmniGrapher Docker stack, auto-selecting a free frontend port.

.DESCRIPTION
    Scans ports 3000-3010. If a port is occupied by OUR frontend container it
    is reused (compose just recreates it); if occupied by anything else (e.g.
    another project's dev server) the loop moves on to the next port. The
    chosen port is exported via FRONTEND_PORT and persisted to the repo-root
    .env so plain `docker compose` commands keep using it.

.EXAMPLE
    .\scripts\up.ps1              # start everything, auto-pick port
    .\scripts\up.ps1 -Open        # also open the app in the browser
    .\scripts\up.ps1 -Base 4000   # scan 4000-4010 instead
#>
param(
    [switch]$Open,
    [int]$Base = 3000,
    [int]$Attempts = 10
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent

function Write-Log([string]$Message) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

# Check the listen table — a wildcard bind (0.0.0.0) with SO_REUSEADDR can
# still allow a loopback bind to succeed, so actually-binding is unreliable.
function Test-PortFree([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return (-not $conn)
}

# Is the port holder our own frontend container?
function Test-OurFrontendOn([int]$Port) {
    try {
        $id = docker ps -q --filter "name=knowledge-base-frontend" --filter "publish=$Port" 2>$null
        return [bool]($id -and $id.Trim())
    } catch { return $false }
}

# What process holds the port (for logging)?
function Get-PortOwner([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if (-not $conn) { return "unknown" }
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if (-not $proc) { return "PID $($conn.OwningProcess)" }
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue).CommandLine
        if ($cmd -and $cmd.Length -gt 90) { $cmd = $cmd.Substring(0, 90) + "..." }
        return "$($proc.Name) (PID $($conn.OwningProcess)) $cmd"
    } catch { return "unknown" }
}

# ---------------------------------------------------------------------------
# Port selection loop
# ---------------------------------------------------------------------------
$port = $null
foreach ($p in $Base..($Base + $Attempts - 1)) {
    if (Test-PortFree $p) {
        $port = $p
        break
    }
    if (Test-OurFrontendOn $p) {
        Write-Log "Port $p is held by our own frontend container — reusing it."
        $port = $p
        break
    }
    Write-Log "Port $p is in use by $(Get-PortOwner $p) — trying next."
}
if (-not $port) {
    throw "No free port found in range $Base-$($Base + $Attempts - 1). Free one or pass -Base."
}
if ($port -ne 3000) {
    Write-Log "Port 3000 unavailable — frontend will run on http://localhost:$port"
} else {
    Write-Log "Frontend port: $port"
}

# Persist so plain `docker compose` keeps the same mapping.
$envFile = Join-Path $repoRoot ".env"
$lines = @()
if (Test-Path $envFile) {
    $lines = Get-Content $envFile | Where-Object { $_ -notmatch '^\s*FRONTEND_PORT\s*=' }
}
$lines += "FRONTEND_PORT=$port"
Set-Content -Path $envFile -Value $lines -Encoding UTF8

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
        $r = Invoke-WebRequest -Uri "http://localhost:$port" -UseBasicParsing -TimeoutSec 4
        if ($r.StatusCode -lt 500) { $up = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
if ($up) {
    Write-Log "Frontend is live: http://localhost:$port"
    if ($Open) { Start-Process "http://localhost:$port" }
} else {
    Write-Log "Frontend container started but not yet responding — Next.js is still compiling. Check: docker logs -f knowledge-base-frontend"
}
