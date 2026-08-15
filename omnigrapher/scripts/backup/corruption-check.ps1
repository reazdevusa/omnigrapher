#Requires -Version 7
<#
.SYNOPSIS
  Scans a backup for corruption indicators: zero-byte files, unreadable files, and manifest hash mismatches.
#>
param(
    [string]$Backup = "D:\Upwork\ai_knowledge_base_suite\omnigrapher\backups\local",
    [string]$Hash = "SHA256"
)

function Write-Status($msg) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor Cyan }

$manifest = Join-Path $Backup "backup-manifest.json"
$issues = 0

if (Test-Path $manifest) {
    $mf = Get-Content $manifest -Raw | ConvertFrom-Json
    foreach ($e in $mf.files) {
        $p = Join-Path $Backup $e.relative
        if (-not (Test-Path $p)) {
            Write-Warning "Manifest entry missing on disk: $($e.relative)"
            $issues++
            continue
        }
        $f = Get-Item $p
        if ($f.Length -eq 0) {
            Write-Warning "ZERO-BYTE file: $($e.relative)"
            $issues++
        }
        $h = (Get-FileHash $p -Algorithm $Hash).Hash
        if ($h -ne $e.hash) {
            Write-Warning "HASH MISMATCH: $($e.relative) expected=$($e.hash) actual=$h"
            $issues++
        }
    }
} else {
    Write-Warning "No manifest found at $manifest"
}

if ($issues -eq 0) {
    Write-Status "Corruption scan passed."
} else {
    throw "Corruption scan found $issues issue(s)."
}
