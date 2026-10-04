#Requires -Version 5.1
<#
.SYNOPSIS
    Shared port helpers for the OmniGrapher launch scripts.

.DESCRIPTION
    Dot-source this file:
        . "$PSScriptRoot\port-utils.ps1"

    Provides:
      Test-PortFree        - is nothing listening on this TCP port?
      Get-PortOwner        - describe the process holding a port (for logs)
      Test-OurContainerOn  - is the port published by one of OUR containers?
      Read-EnvFile         - .env -> ordered hashtable
      Write-EnvFile        - merge values into .env, preserving other keys
      Get-EnvPort          - read one port from .env with a default
      Resolve-ServicePorts - pick a usable host port for each service
#>

# Check the listen table - a wildcard bind (0.0.0.0) with SO_REUSEADDR can
# still allow a loopback bind to succeed, so actually-binding is unreliable.
function Test-PortFree([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return (-not $conn)
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

# Is the port holder one of OUR containers (safe to reuse/recreate)?
function Test-OurContainerOn([string]$ContainerName, [int]$Port) {
    try {
        $id = docker ps -q --filter "name=$ContainerName" --filter "publish=$Port" 2>$null
        return [bool]($id -and $id.Trim())
    } catch { return $false }
}

# Read a .env file into an ordered hashtable (comments/blank lines preserved separately).
function Read-EnvFile([string]$Path) {
    $map = [ordered]@{}
    if (Test-Path $Path) {
        foreach ($line in (Get-Content $Path -ErrorAction SilentlyContinue)) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
                $map[$Matches[1]] = $Matches[2]
            }
        }
    }
    return $map
}

# Merge $Values into the .env at $Path, preserving unrelated keys/comments.
function Write-EnvFile([string]$Path, [hashtable]$Values) {
    $lines = @()
    if (Test-Path $Path) {
        $keys = ($Values.Keys | ForEach-Object { [regex]::Escape($_) }) -join '|'
        # @() wrap: when every line is filtered out, Where-Object returns $null
        # and += would silently turn $lines into a concatenated string.
        $lines = @(Get-Content $Path -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch "^\s*($keys)\s*=" })
    }
    foreach ($k in $Values.Keys) { $lines += "$k=$($Values[$k])" }
    Set-Content -Path $Path -Value $lines -Encoding UTF8
}

# One port value from .env (or $Default when unset/unparseable).
function Get-EnvPort([string]$Var, [int]$Default, [string]$EnvPath) {
    $map = Read-EnvFile $EnvPath
    if ($map.Contains($Var) -and $map[$Var] -match '^\d+$') { return [int]$map[$Var] }
    return $Default
}

<#
Resolve a usable host port for each service.

$Services: array of @{ Var; Default; Container } entries, resolved in order.
$EnvPath : .env file whose existing values are preferred when still usable.

Rule per service, first hit wins:
  1. the port already recorded in .env (keeps mappings stable across runs)
  2. any port our own container currently publishes (compose will recreate it)
  3. the first free port in Default..Default+Attempts-1 that no other service
     claimed this run

Returns ordered hashtable Var -> Port.
#>
function Resolve-ServicePorts([array]$Services, [string]$EnvPath, [int]$Attempts = 10) {
    $envMap = Read-EnvFile $EnvPath
    $claimed = @{}
    $result = [ordered]@{}

    foreach ($svc in $Services) {
        $var = $svc.Var; $container = $svc.Container; $default = [int]$svc.Default
        $chosen = $null

        # 1+2: prefer the recorded/existing mapping if it is free or already ours
        $candidates = @()
        if ($envMap.Contains($var) -and $envMap[$var] -match '^\d+$') { $candidates += [int]$envMap[$var] }
        $candidates += ($default..($default + $Attempts - 1)) | Where-Object { $_ -notin $candidates }

        foreach ($p in $candidates) {
            if ($claimed.ContainsKey($p)) { continue }
            if (Test-OurContainerOn $container $p) {
                if ($p -ne $default) { Write-Host "  $var : $p held by our own $container - reusing" }
                $chosen = $p; break
            }
            if (Test-PortFree $p) { $chosen = $p; break }
            Write-Host "  $var : port $p in use by $(Get-PortOwner $p) - trying next"
        }

        if ($null -eq $chosen) {
            throw "No free port found for $var in range $default-$($default + $Attempts - 1)."
        }
        if ($chosen -ne $default) {
            Write-Host "  $var : $default unavailable, using $chosen"
        }
        $claimed[$chosen] = $var
        $result[$var] = $chosen
    }
    return $result
}
