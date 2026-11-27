# Rollback of the F3 dashboard change (Hark, 2026-11-27). Touches ONLY the dashboard (TeseRobot\webapp).
# Never touches warsched / warrobot / config / order flow.
# Run:  powershell -ExecutionPolicy Bypass -File "<this folder>\rollback.ps1"
$ErrorActionPreference = "Stop"
$B   = Split-Path -Parent $MyInvocation.MyCommand.Path
$WEB = "C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp"
# 1) restore the changed files
Copy-Item "$B\webapp\app.py" "$WEB\app.py" -Force
Copy-Item "$B\webapp\warrobot_data.py" "$WEB\warrobot_data.py" -Force
Copy-Item "$B\webapp\templates\*" "$WEB\templates\" -Force
# 2) move the added files into the backup (not deleted)
$moved = Join-Path $B "removed_on_rollback"
New-Item -ItemType Directory -Force -Path $moved | Out-Null
foreach ($rel in @("f3_data.py", "f3_funnel.py", "templates\_f3_panel.html", "cache\f3_funnel.json", "cache\f3_universe.json")) {
  $p = Join-Path $WEB $rel
  if (Test-Path $p) { Move-Item $p (Join-Path $moved ($rel -replace '\\','_')) -Force }
}
# 3) restart ONLY the dashboard children (web_supervisor.py under pm2 'tse-web' respawns them within ~3 s)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*TeseRobot\webapp\app.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 8
try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5077/health -TimeoutSec 60).StatusCode } catch { "health check failed: $_" }
"rollback done"