#Requires -Version 7
<#
.SYNOPSIS
  OmniGrapher ecosystem health diagnostic.
.DESCRIPTION
  Scans Ollama, Devin, Git, workspace, drives, and services. Outputs a JSON report.
#>
param(
    [string]$Workspace = "D:\Upwork\ai_knowledge_base_suite",
    [string]$Output = ""
)

$ErrorActionPreference = "SilentlyContinue"
$report = [ordered]@{ timestamp = (Get-Date -Format "o") }

# --- Drives ---
$report.drives = Get-PSDrive -PSProvider FileSystem | Select-Object Name, Root, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}}

# --- Ollama ---
$ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
$ollamaOk = [bool]$ollamaExe
$report.ollama = @{ exe = $ollamaExe; available = $ollamaOk }
if ($ollamaOk) {
    $report.ollama.version = (& ollama --version).Trim()
    $report.ollama.models = (& ollama list) | Out-String
    try {
        $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method GET -TimeoutSec 5
        $report.ollama.server = "running"
        $report.ollama.model_names = $tags.models.name
    } catch {
        $report.ollama.server = "not responding"
    }
}

# --- Devin ---
$devinDir = "C:\Users\$env:USERNAME\AppData\Local\Programs\Devin"
$devinSessions = "D:\Upwork\ai_knowledge_base_suite\.devin\sessions"
$report.devin = @{
    install_dir = $devinDir
    installed   = Test-Path $devinDir
    exe_exists  = Test-Path "$devinDir\Devin.exe"
    sessions_dir = $devinSessions
    sessions_dir_exists = Test-Path $devinSessions
}

# --- Git ---
$report.git = @{
    repo = Test-Path "$Workspace\.git"
    remotes = (& git -C $Workspace remote -v) | Out-String
}

# --- Workspace ---
$report.workspace = @{
    root = $Workspace
    top_dirs = (Get-ChildItem -Path $Workspace -Directory | Select-Object -ExpandProperty Name)
    readme = Test-Path "$Workspace\README.md"
    omnigrapher_dir = Test-Path "$Workspace\omnigrapher"
}

# --- Services ---
$report.services = @{
    backend_8001 = (Test-NetConnection -ComputerName localhost -Port 8001 -WarningAction SilentlyContinue).TcpTestSucceeded
    nextjs_3002  = (Test-NetConnection -ComputerName localhost -Port 3002 -WarningAction SilentlyContinue).TcpTestSucceeded
    ollama_11434 = (Test-NetConnection -ComputerName localhost -Port 11434 -WarningAction SilentlyContinue).TcpTestSucceeded
}

# --- Backup targets ---
$report.backup_targets = @{
    local = Test-Path "$Workspace\omnigrapher\backups"
    external_g = Test-Path "G:\DO_NOT_DELETE\OmniGrapher_Backups"
    github_origin = ($report.git.remotes -match 'omnigrapher')
}

# --- Output ---
$json = $report | ConvertTo-Json -Depth 5
if (-not $Output) { $Output = "$Workspace\omnigrapher\backups\logs\diagnostic-$(Get-Date -Format 'yyyy-MM-dd_HH-mm-ss').json" }
New-Item -ItemType Directory -Path (Split-Path $Output) -Force | Out-Null
$json | Out-File -FilePath $Output -Encoding utf8
Write-Host "Diagnostic report: $Output" -ForegroundColor Cyan
$report
