# Register \ForX-NewsCalendar: fetch the free FF weekly calendar JSON into data\calendar\ (observe-only tags).
# At logon (+2 min) and every 6 hours while logged on; offline runs just log and keep the cache. Idempotent.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\pythonw.exe'  # no console window
$action = New-ScheduledTaskAction -Execute $py -Argument 'scripts\fetch_news_calendar.py' -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $t1.Delay = 'PT2M'
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 6)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'ForX-NewsCalendar' -Action $action -Trigger @($t1, $t2) -Settings $set `
    -Description 'ForX: fetch FF weekly calendar JSON (observe-only news tags); logon + every 6h' -Force | Out-Null
Write-Output 'registered \ForX-NewsCalendar'