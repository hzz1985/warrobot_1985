$log = "C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\logs\session_watch.log"
if (Test-Path $log) { Remove-Item $log -Force }
for ($i = 0; $i -lt 45; $i++) {
    try {
        $j = (Invoke-WebRequest 'http://127.0.0.1:9222/json' -TimeoutSec 5 -UseBasicParsing).Content | ConvertFrom-Json
        $p = $j | Where-Object { $_.type -eq 'page' } | Select-Object -First 1
        $u = if ($p) { $p.url } else { 'NO_PAGE' }
    } catch { $u = 'CDP_FAIL' }
    "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $u | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds 20
}
