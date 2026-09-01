#Requires -Version 7
param(
    [Parameter(Mandatory = $true)]
    [string]$Backup,
    [ValidateSet("Validate", "Files", "SQLite", "PostgresLogical")]
    [string]$Mode = "Validate",
    [string]$Target = "D:\Upwork\ai_knowledge_base_suite",
    [switch]$Confirm,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

function Invoke-Docker($Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: docker $($Arguments -join ' ')" }
}

if (-not (Test-Path $Backup)) { throw "Backup not found: $Backup" }
$native = Join-Path $Backup "native"

if ($Mode -eq "Validate") {
    $required = @(
        "kb.db",
        "knowledge_base.dump",
        "postgres-wal.tar.gz",
        "redis-dump.rdb",
        "chroma-data.tar.gz"
    )
    foreach ($name in $required) {
        $path = Join-Path $native $name
        if (-not (Test-Path $path)) { throw "Required backup artifact missing: $path" }
        if ((Get-Item $path).Length -eq 0) { throw "Backup artifact is empty: $path" }
    }
    Invoke-Docker -Arguments @("cp", (Join-Path $native "kb.db"), "knowledge-base-backend:/tmp/restore-check.db")
    Invoke-Docker -Arguments @("exec", "knowledge-base-backend", "python", "-c", "import sqlite3; c=sqlite3.connect('/tmp/restore-check.db'); assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'; c.close()")
    Invoke-Docker -Arguments @("cp", (Join-Path $native "knowledge_base.dump"), "knowledge-base-postgres:/tmp/restore-check.dump")
    Invoke-Docker -Arguments @("exec", "knowledge-base-postgres", "pg_restore", "--list", "/tmp/restore-check.dump")
    Invoke-Docker -Arguments @("exec", "knowledge-base-backend", "rm", "-f", "/tmp/restore-check.db")
    Invoke-Docker -Arguments @("exec", "knowledge-base-postgres", "rm", "-f", "/tmp/restore-check.dump")
    Write-Host "Backup validation passed: $Backup" -ForegroundColor Green
    return
}

if (-not $Confirm) { throw "Restore is destructive. Re-run with -Confirm to proceed." }

if ($Mode -eq "Files") {
    Get-ChildItem -Path $Backup -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($Backup.Length).TrimStart('\')
        if ($rel -eq "backup-manifest.json" -or $rel.StartsWith("native\")) { return }
        $dest = Join-Path $Target $rel
        if ($WhatIf) { Write-Host "Would restore: $dest"; return }
        New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
        Copy-Item $_.FullName $dest -Force
    }
}

if ($Mode -eq "SQLite") {
    $source = Join-Path $native "kb.db"
    if (-not (Test-Path $source)) { throw "SQLite snapshot missing: $source" }
    if ($WhatIf) { Write-Host "Would restore $source to knowledge_base_pilot\kb.db"; return }
    Copy-Item $source (Join-Path $Target "knowledge_base_pilot\kb.db") -Force
}

if ($Mode -eq "PostgresLogical") {
    $source = Join-Path $native "knowledge_base.dump"
    if (-not (Test-Path $source)) { throw "PostgreSQL dump missing: $source" }
    if ($WhatIf) { Write-Host "Would replace PostgreSQL knowledge_base from $source"; return }
    Invoke-Docker -Arguments @("cp", $source, "knowledge-base-postgres:/tmp/restore.dump")
    Invoke-Docker -Arguments @("exec", "knowledge-base-postgres", "pg_restore", "-U", "awap_user", "-d", "knowledge_base", "--clean", "--if-exists", "--no-owner", "/tmp/restore.dump")
    Invoke-Docker -Arguments @("exec", "knowledge-base-postgres", "rm", "-f", "/tmp/restore.dump")
}

Write-Host "Restore completed in mode $Mode." -ForegroundColor Green
