#Requires -Version 7
<#
.SYNOPSIS
  OmniGrapher Ollama repair + model-pipeline bootstrap.
.DESCRIPTION
  Downloads the Windows portable Ollama zip if ollama.exe is missing,
  installs it to the user-local PATH location, moves models to a safe
  drive, starts ollama serve, and pulls the required models.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:USERPROFILE\AppData\Local\Programs\Ollama",
    [string]$ModelHome = "D:\.ollama",
    [string]$OllamaZip = "$env:TEMP\ollama-windows-amd64.zip",
    [string]$DownloadUrl = "https://ollama.com/download/ollama-windows-amd64.zip",
    [string[]]$Models = @("llama3.2", "nomic-embed-text")
)

$ErrorActionPreference = "Stop"

function Write-Status($msg) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor Cyan }

# Ensure model home is on D: (safe, local, not C overflow)
$modelsDir = Join-Path $ModelHome "models"
New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $modelsDir, "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $modelsDir, "Process")

# Preserve existing .ollama identity keys if present
$sourceOllama = "$env:USERPROFILE\.ollama"
if (Test-Path $sourceOllama) {
    $destOllama = Join-Path $ModelHome ".ollama-identity"
    New-Item -ItemType Directory -Path $destOllama -Force | Out-Null
    Get-ChildItem -Path $sourceOllama -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^id_' } | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $destOllama $_.Name) -Force -ErrorAction SilentlyContinue
    }
}

# Install if missing
$ollamaExe = Join-Path $InstallDir "ollama.exe"
if (-not (Test-Path $ollamaExe)) {
    Write-Status "Ollama not found. Downloading portable zip..."
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    if (Get-Command curl -ErrorAction SilentlyContinue) {
        curl.exe -L -o $OllamaZip $DownloadUrl
    } else {
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $OllamaZip -UseBasicParsing
    }
    Write-Status "Extracting to $InstallDir..."
    Expand-Archive -Path $OllamaZip -DestinationPath $InstallDir -Force
    Remove-Item $OllamaZip -Force -ErrorAction SilentlyContinue
} else {
    Write-Status "Ollama binary already present at $ollamaExe"
}

# Ensure PATH
$path = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($path -notlike "*$InstallDir*") {
    Write-Status "Adding $InstallDir to user PATH..."
    [Environment]::SetEnvironmentVariable("PATH", "$path;$InstallDir", "User")
    $env:PATH = "$env:PATH;$InstallDir"
}

# Validate
$version = & $ollamaExe --version 2>&1
Write-Status "Ollama version: $version"

# Start server
$server = Start-Process -FilePath $ollamaExe -ArgumentList "serve" -NoNewWindow -PassThru -RedirectStandardOutput "$ModelHome\ollama-serve.log" -RedirectStandardError "$ModelHome\ollama-serve.err.log"
Write-Status "Ollama serve PID: $($server.Id)"

# Wait for API
$ready = $false
$deadline = (Get-Date).AddSeconds(60)
while (-not $ready -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method GET -ErrorAction SilentlyContinue
        $ready = $true
    } catch { $ready = $false }
}
if (-not $ready) { throw "Ollama server did not become ready within 60s" }

# Pull required models
foreach ($m in $Models) {
    Write-Status "Pulling model $m..."
    & $ollamaExe pull $m
    if ($LASTEXITCODE -ne 0) { Write-Warning "Failed to pull $m" }
}

# Report
Write-Status "Installed models:"
& $ollamaExe list

# Persist a status file
$status = [PSCustomObject]@{
    repaired_at = (Get-Date -Format "o")
    install_dir = $InstallDir
    model_home  = $ModelHome
    models      = & $ollamaExe list --format json 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
} | ConvertTo-Json -Depth 3
$status | Out-File -FilePath "$ModelHome\ollama-status.json" -Encoding utf8
