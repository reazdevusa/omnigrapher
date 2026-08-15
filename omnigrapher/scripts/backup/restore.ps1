#Requires -Version 7
<#
.SYNOPSIS
  Restores a selected backup layer over the live workspace.
  Use with caution; this overwrites live files.
#>
param(
    [string]$Backup = "D:\Upwork\ai_knowledge_base_suite\omnigrapher\backups\local",
    [string]$Target = "D:\Upwork\ai_knowledge_base_suite",
    [switch]$Confirm,
    [switch]$WhatIf
)

if (-not $Confirm) {
    throw "Restore is destructive. Re-run with -Confirm to proceed."
}

if (-not (Test-Path $Backup)) { throw "Backup not found: $Backup" }

Write-Host "Restoring $Backup -> $Target" -ForegroundColor Yellow
if ($WhatIf) {
    Get-ChildItem -Path $Backup -Recurse -File | ForEach-Object { Write-Host "Would restore: $($_.FullName)" }
    return
}

Get-ChildItem -Path $Backup -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($Backup.Length).TrimStart('\')
    if ($rel -eq 'backup-manifest.json') { return }
    $dest = Join-Path $Target $rel
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item $_.FullName $dest -Force
    Write-Host "Restored: $rel"
}

Write-Host "Restore complete." -ForegroundColor Green
