# Register \ForX-DataBackup: nightly encrypted off-PC backup (scripts\backup_data_offpc.py) to the private
# repo ashique19/ForX-data-backup. Daily 00:15 local (Asia/Dhaka) + at logon (+5 min) with --if-missed, so a
# night the PC was off/offline is caught up at next logon. StartWhenAvailable also runs a missed 00:15. Idempotent.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\pythonw.exe'  # no console window
$action = New-ScheduledTaskAction -Execute $py -Argument 'scripts\backup_data_offpc.py --if-missed' -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -Daily -At '00:15'
$t2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $t2.Delay = 'PT5M'
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'ForX-DataBackup' -Action $action -Trigger @($t1, $t2) -Settings $set `
    -Description 'ForX: nightly age-encrypted data backup to private repo ForX-data-backup (keep 7); log data\backup\backup.log' -Force | Out-Null
Write-Output 'registered \ForX-DataBackup'