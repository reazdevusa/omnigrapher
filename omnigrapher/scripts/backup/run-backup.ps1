#Requires -Version 7
<#
.SYNOPSIS
  OmniGrapher multi-layer backup engine.
.DESCRIPTION
  Reads omnigrapher/config/backup.json and runs the local, external, and git layers.
  Creates integrity manifests and prunes old backups per retention policy.
#>
param(
    [string]$Config = "$PSScriptRoot\..\..\config\backup.json",
    [switch]$NoExternal,
    [switch]$NoGit,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Status($msg) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor Cyan }

function New-BackupManifest($Path, $Algorithm) {
    $files = Get-ChildItem -Path $Path -Recurse -File | Select-Object FullName, Length, LastWriteTime
    $hashes = @()
    foreach ($f in $files) {
        $h = Get-FileHash -Path $f.FullName -Algorithm $Algorithm
        $hashes += @{ relative = $f.FullName.Substring($Path.Length).TrimStart('\'); hash = $h.Hash; size = $f.Length }
    }
    return @{ generated = (Get-Date -Format "o"); algorithm = $Algorithm; files = $hashes }
}

function Get-FolderSizeGB($Path) {
    $b = (Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    return [math]::Round($b / 1GB, 2)
}

function Remove-OldBackups($Base, $Keep) {
    $dirs = Get-ChildItem -Path $Base -Directory | Sort-Object CreationTime -Descending | Select-Object -Skip $Keep
    foreach ($d in $dirs) {
        Write-Status "Pruning old backup: $($d.FullName)"
        Remove-Item $d.FullName -Recurse -Force
    }
}

function Invoke-Robocopy($Src, $Dst) {
    New-Item -ItemType Directory -Path $Dst -Force | Out-Null
    robocopy $Src $Dst /E /R:2 /W:2 /MT:8 /NP /NFL /NDL 2>&1 | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        throw "robocopy failed with exit code $rc for $Src -> $Dst"
    }
}

if (-not (Test-Path $Config)) { throw "Backup config not found: $Config" }
$cfg = Get-Content $Config -Raw | ConvertFrom-Json -AsHashtable
$ws = $cfg.workspace
$ts = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logDir = "$ws\omnigrapher\backups\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = "$logDir\backup-$ts.log"
Start-Transcript -Path $log -Append -Force | Out-Null

$manifest = [ordered]@{
    started = (Get-Date -Format "o")
    layers  = [ordered]@()
}

# --- Layer 1: Local ---
if ($cfg.local.enabled) {
    Write-Status "Starting Layer 1: Local backup"
    $dest = "$ws\omnigrapher\backups\local\$ts"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    foreach ($rel in $cfg.local.include) {
        $src = Join-Path $ws $rel
        if (-not (Test-Path $src)) { Write-Warning "Source missing: $src"; continue }
        $dst = Join-Path $dest $rel
        New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
        Write-Status "Copying $rel to local backup..."
        if ((Get-Item $src) -is [System.IO.DirectoryInfo]) {
            Invoke-Robocopy -Src $src -Dst $dst
        } else {
            Copy-Item $src $dst -Force
        }
    }
    $localManifest = New-BackupManifest -Path $dest -Algorithm $cfg.integrity.hash_algorithm
    $localManifest | Out-File -FilePath "$dest\$($cfg.integrity.manifest_file)" -Encoding utf8
    $manifest.layers += @{ layer = "local"; destination = $dest; manifest = "$dest\$($cfg.integrity.manifest_file)"; size_gb = (Get-FolderSizeGB $dest) }
    Write-Status "Layer 1 complete: $dest"
    Remove-OldBackups -Base "$ws\omnigrapher\backups\local" -Keep $cfg.retention.local
}

# --- Layer 2: External ---
if ($cfg.external.enabled -and -not $NoExternal) {
    if (Test-Path $cfg.external.drive) {
        Write-Status "Starting Layer 2: External HDD backup"
        $dest = Join-Path $cfg.external.destination $ts
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        foreach ($rel in $cfg.external.include) {
            $src = Join-Path $ws $rel
            if (-not (Test-Path $src)) { Write-Warning "Source missing: $src"; continue }
            $dst = Join-Path $dest $rel
            New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
            Write-Status "Copying $rel to external backup..."
            if ((Get-Item $src) -is [System.IO.DirectoryInfo]) {
                Invoke-Robocopy -Src $src -Dst $dst
            } else {
                Copy-Item $src $dst -Force
            }
        }
        $extManifest = New-BackupManifest -Path $dest -Algorithm $cfg.integrity.hash_algorithm
        $extManifest | Out-File -FilePath "$dest\$($cfg.integrity.manifest_file)" -Encoding utf8
        $manifest.layers += @{ layer = "external"; destination = $dest; manifest = "$dest\$($cfg.integrity.manifest_file)"; size_gb = (Get-FolderSizeGB $dest) }
        Write-Status "Layer 2 complete: $dest"
        Remove-OldBackups -Base $cfg.external.destination -Keep $cfg.retention.external
    } else {
        Write-Warning "External drive $($cfg.external.drive) not available; skipping external layer."
    }
}

# --- Layer 3: Git ---
if ($cfg.git.enabled -and -not $NoGit) {
    Write-Status "Starting Layer 3: GitHub versioned backup (commit only)"
    if (Test-Path "$ws\.git") {
        & git -C $ws add .
        $msg = "auto-backup $ts`n`nGenerated with OmniGrapher"
        & git -C $ws commit -m $msg --quiet
        if ($LASTEXITCODE -eq 0) {
            $sha = git -C $ws rev-parse HEAD
            $manifest.layers += @{ layer = "git"; commit = $sha }
            Write-Status "Layer 3 complete: commit $sha"
        } else {
            Write-Warning "Git commit produced no changes or failed."
        }
    } else {
        Write-Warning "No .git repository at $ws; skipping git layer."
    }
}

$manifest.finished = (Get-Date -Format "o")
$manifest | ConvertTo-Json -Depth 5 | Out-File -FilePath "$logDir\manifest-$ts.json" -Encoding utf8
Write-Status "Backup manifest: $logDir\manifest-$ts.json"
Stop-Transcript | Out-Null
