#Requires -Version 7
<#
.SYNOPSIS
  Validates that backup destinations are safe and not inside source or the software installation folder.
#>
param(
    [string]$Config = "$PSScriptRoot\..\..\config\backup.json"
)

$cfg = Get-Content $Config -Raw | ConvertFrom-Json -AsHashtable
$safe = $true
$forbidden = @(
    'C:\\Users\\alfa_\\AppData\\Local\\Programs\\Ollama',
    'C:\\Users\\alfa_\\AppData\\Local\\Programs\\Devin',
    'G:\\DO_NOT_DELETE\\ALL_SOFTWARE_INSTALLATION_SOURCES'
)

function check($loc, $name) {
    if (-not $loc) { return }
    foreach ($f in $forbidden) {
        if ($loc -like "$f*") {
            Write-Warning "[$name] Destination $loc is inside a forbidden path: $f"
            $safe = $false
        }
    }
    if (-not (Test-Path (Split-Path $loc))) {
        Write-Warning "[$name] Parent path does not exist: $(Split-Path $loc)"
        $safe = $false
    }
}

check($cfg.local.destination, 'local')
check($cfg.external.destination, 'external')

if ($safe) {
    Write-Host "Backup locations are safe." -ForegroundColor Green
} else {
    throw "Unsafe backup location(s) detected."
}
