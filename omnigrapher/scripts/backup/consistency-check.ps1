#Requires -Version 7
<#
.SYNOPSIS
  Verifies that a backup destination matches the source.
#>
param(
    [string]$Source = "D:\Upwork\ai_knowledge_base_suite",
    [string]$Backup = "D:\Upwork\ai_knowledge_base_suite\omnigrapher\backups\local",
    [string]$Hash = "SHA256"
)

function Write-Status($msg) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor Cyan }

$sourceFiles = Get-ChildItem -Path $Source -Recurse -File
$backupFiles = Get-ChildItem -Path $Backup -Recurse -File

Write-Status "Comparing $Backup vs $Source using $Hash"

$ok = 0
$bad = 0
$extra = 0
$missing = 0

foreach ($bf in $backupFiles) {
    $rel = $bf.FullName.Substring($Backup.Length).TrimStart('\')
    if ($rel -eq 'backup-manifest.json') { continue }
    $sf = Join-Path $Source $rel
    if (-not (Test-Path $sf)) {
        Write-Warning "Extra in backup: $rel"
        $extra++
        continue
    }
    $bh = (Get-FileHash $bf.FullName -Algorithm $Hash).Hash
    $sh = (Get-FileHash $sf -Algorithm $Hash).Hash
    if ($sh -ne $bh) {
        Write-Warning "HASH MISMATCH: $rel"
        $bad++
    } else {
        $ok++
    }
}

foreach ($sf in $sourceFiles) {
    $rel = $sf.FullName.Substring($Source.Length).TrimStart('\')
    $bf = Join-Path $Backup $rel
    if (-not (Test-Path $bf)) {
        Write-Warning "Missing from backup: $rel"
        $missing++
    }
}

Write-Status "OK: $ok | Mismatched: $bad | Extra: $extra | Missing: $missing"
