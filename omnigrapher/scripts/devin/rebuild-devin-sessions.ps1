#Requires -Version 7
<#
.SYNOPSIS
  Rebuilds the local .devin session landing zone and pins OmniGrapher as the canonical project.
#>
param(
    [string]$Workspace = "D:\Upwork\ai_knowledge_base_suite",
    [string]$Repo = "https://github.com/reazdevusa/omnigrapher"
)

$ErrorActionPreference = "Stop"

$devinDir = Join-Path $Workspace ".devin"
$sessionsDir = Join-Path $devinDir "sessions"
$metadataDir = Join-Path $Workspace "omnigrapher" ".devin" "metadata"

foreach ($d in @($devinDir, $sessionsDir, $metadataDir)) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}

$workspaceJson = Join-Path $devinDir "workspace.json"
$metaJson = Join-Path $metadataDir "workspace.json"

$payload = [ordered]@{
    project      = "OmniGrapher"
    repository   = $Repo
    workspace_root = $Workspace
    restored_at  = (Get-Date -Format "o")
    note         = "Local sessions are a landing zone; chat history is in Devin Cloud and must be re-linked via the Devin Cloud UI."
} | ConvertTo-Json -Depth 3

$payload | Out-File -FilePath $workspaceJson -Encoding utf8
$payload | Out-File -FilePath $metaJson -Encoding utf8

# Place a git-ignored .gitkeep in sessions
"# Session landing zone" | Out-File -FilePath (Join-Path $sessionsDir ".gitkeep") -Encoding utf8

Write-Host "Devin local metadata rebuilt at: $devinDir" -ForegroundColor Green
Write-Host "Next manual step: open Devin Cloud, connect GitHub to reazdevusa/omnigrapher." -ForegroundColor Yellow
