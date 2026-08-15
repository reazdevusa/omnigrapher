#Requires -Version 5.1
<#
.SYNOPSIS
    Watchdog for the AI Knowledge Base Suite services.

.DESCRIPTION
    Monitors the services recorded in scripts/.services.json. If a service's
    port stops responding, the watchdog restarts it and logs the event to
    logs/watchdog.log.
#>
param(
    [int]$IntervalSeconds = 30,
    [int]$PortTimeoutSeconds = 120
)

$runFile = Join-Path $PSScriptRoot ".services.json"
$logDir  = Join-Path (Join-Path $PSScriptRoot "..") "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "watchdog.log"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
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

Write-Log "Watchdog started. Checking every $IntervalSeconds seconds."

# Wait for the state file to be written (tabs launch in parallel)
$startupDeadline = (Get-Date).AddSeconds(300)
while (-not (Test-Path $runFile) -and (Get-Date) -lt $startupDeadline) {
    Start-Sleep -Seconds 1
}

if (-not (Test-Path $runFile)) {
    Write-Log "Service state file not found. Exiting."
    exit 0
}

while ($true) {

    $state = Get-Content $runFile | ConvertFrom-Json
    $restarted = $false

    foreach ($svc in $state.services) {
        if ($null -eq $svc.port) {
            # No port to monitor (e.g. desktop app); skip
            continue
        }

        $alive = Test-Port -Port $svc.port
        if ($alive) {
            continue
        }

        Write-Log "ALERT: $($svc.name) is not responding on port $($svc.port). Restarting..."

        # Kill the old process tree, if still around
        try {
            Stop-Process -Id $svc.pid -Force -ErrorAction SilentlyContinue
        } catch {}
        try {
            taskkill /PID $svc.pid /T /F 2>&1 | Out-Null
        } catch {}

        Start-Sleep -Seconds 2

        # Restart the service using its recorded command
        $newProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", $svc.cmd -PassThru -WindowStyle Normal
        $svc.pid = $newProc.Id
        $restarted = $true

        try {
            Wait-For-Port -Port $svc.port -TimeoutSeconds $PortTimeoutSeconds
            Write-Log "$($svc.name) restarted successfully on port $($svc.port)."
        } catch {
            Write-Log "ERROR: $($svc.name) did not come back on port $($svc.port): $_"
        }
    }

    if ($restarted) {
        $state | ConvertTo-Json -Depth 5 | Set-Content -Path $runFile -Encoding UTF8
        Write-Log "Service state updated."
    }

    Start-Sleep -Seconds $IntervalSeconds
}
