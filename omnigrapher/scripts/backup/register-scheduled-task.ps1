#Requires -Version 7
<#
.SYNOPSIS
  Registers the OmniGrapher backup engine with Windows Task Scheduler.
#>
param(
    [string]$Name = "OmniGrapher-Backup",
    [string]$Script = "$PSScriptRoot\run-backup.ps1",
    [string]$IntervalHours = 6
)

$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
Write-Host "Scheduled task '$Name' registered to run every $IntervalHours hour(s)." -ForegroundColor Green
