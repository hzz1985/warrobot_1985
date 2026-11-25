# Elevated cleanup: removes the retired TSE_Strategy bot that keeps stealing the
# broker session. Run once as Administrator.
$ErrorActionPreference = "Continue"
$log = "C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\logs\cleanup_elevated.log"
"=== cleanup started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -Append -Encoding utf8 $log

foreach ($procId in 13352, 10004) {
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        "killed pid $procId" | Out-File -Append -Encoding utf8 $log
    } catch {
        "failed pid $procId : $($_.Exception.Message)" | Out-File -Append -Encoding utf8 $log
    }
}

Get-CimInstance Win32_Process -Filter "Name='chromedriver.exe'" | ForEach-Object {
    if ($_.CreationDate -lt (Get-Date).AddHours(-2)) {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
              "killed stale chromedriver $($_.ProcessId)" | Out-File -Append -Encoding utf8 $log
        } catch { "failed chromedriver $($_.ProcessId)" | Out-File -Append -Encoding utf8 $log }
    }
}

$names = @('TSE_PositionManagerLoop','TSE_PM_0845','TSE_AutoBuy','TSE_BuyPlan',
           'TSE_Nightly_Job','TSE_ArchReport','TSE_WeeklyReport','TSE_S1_Nightly',
           'TSE_RobotAuto')
foreach ($n in $names) {
    try {
        Disable-ScheduledTask -TaskName $n -ErrorAction Stop | Out-Null
        "disabled task $n" | Out-File -Append -Encoding utf8 $log
    } catch {
        "failed task $n : $($_.Exception.Message)" | Out-File -Append -Encoding utf8 $log
    }
}
"=== cleanup finished ===" | Out-File -Append -Encoding utf8 $log
