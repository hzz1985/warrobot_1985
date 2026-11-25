# hark-mcp installer for Windows Server.
# Run in PowerShell (as Administrator) from anywhere:
#   powershell -ExecutionPolicy Bypass -File .\hark-mcp\install.ps1
# Options:
#   -BotDir "C:\...\WarRobot"   bot folder (default below)
#   -Port 8765                  local port
#   -NewToken                   rotate the token even if one already exists
param(
    [string]$BotDir = "C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot",
    [int]$Port = 8765,
    [switch]$NewToken
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here
Write-Host "== hark-mcp install in $Here" -ForegroundColor Cyan

# 1) Python + venv ---------------------------------------------------------------
$py = $null
foreach ($c in @("py -3", "python", "python3")) {
    try { $v = & cmd /c "$c --version" 2>&1; if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3\.(\d+)") { if ([int]$Matches[1] -ge 10) { $py = $c; break } } } catch {}
}
if (-not $py) { throw "Python 3.10+ not found. Install it from python.org (tick 'Add to PATH') and run again." }
Write-Host "Python: $py"
$venv = Join-Path $Here ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & cmd /c "$py -m venv `"$venv`""
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
$vpy = Join-Path $venv "Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r (Join-Path $Here "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed (check internet / pip mirror)" }
Write-Host "Dependencies installed." -ForegroundColor Green

# 2) Token -----------------------------------------------------------------------
$tok = [Environment]::GetEnvironmentVariable("HARK_MCP_TOKEN", "User")
$generated = $false
if ($NewToken -or -not $tok -or $tok.Length -lt 32) {
    $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $tok = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    [Environment]::SetEnvironmentVariable("HARK_MCP_TOKEN", $tok, "User")
    try { [Environment]::SetEnvironmentVariable("HARK_MCP_TOKEN", $tok, "Machine") } catch { Write-Host "(machine env not set: not admin; user env is enough)" }
    $generated = $true
}
[Environment]::SetEnvironmentVariable("HARK_MCP_BOT_DIR", $BotDir, "User")
if (-not [Environment]::GetEnvironmentVariable("HARK_MCP_LIVE_TRADING", "User")) {
    [Environment]::SetEnvironmentVariable("HARK_MCP_LIVE_TRADING", "0", "User")
}
$env:HARK_MCP_TOKEN = $tok
$env:HARK_MCP_BOT_DIR = $BotDir
$env:HARK_MCP_PORT = "$Port"
$env:HARK_MCP_HOST = "127.0.0.1"
$env:HARK_MCP_LIVE_TRADING = [Environment]::GetEnvironmentVariable("HARK_MCP_LIVE_TRADING", "User")

# 3) pm2 -------------------------------------------------------------------------
if (-not (Get-Command pm2 -ErrorAction SilentlyContinue)) { throw "pm2 not found in PATH (npm i -g pm2)" }
$ErrorActionPreference = "Continue"
pm2 delete hark-mcp 2>$null | Out-Null
pm2 start (Join-Path $Here "server.py") --name hark-mcp --interpreter "$vpy" --update-env
pm2 save
$ErrorActionPreference = "Stop"

Start-Sleep -Seconds 4
try {
    Invoke-WebRequest -UseBasicParsing -Method Post "http://127.0.0.1:$Port/mcp" | Out-Null
    Write-Host "WARNING: server answered without a token?!" -ForegroundColor Red
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 401) { Write-Host "Server is running on http://127.0.0.1:$Port/mcp (401 without token = correct)." -ForegroundColor Green }
    else { Write-Host "Server did not answer yet. Check: pm2 logs hark-mcp --lines 50 --nostream" -ForegroundColor Yellow }
}

if ($generated) {
    Write-Host ""
    Write-Host "================ YOUR HARK-MCP TOKEN (shown only once) ================" -ForegroundColor Yellow
    Write-Host $tok
    Write-Host "=======================================================================" -ForegroundColor Yellow
    Write-Host "Enter it ONLY in Hark's secure form - never paste it into the chat."
    Write-Host "Lost it? Run again with -NewToken to make a new one."
} else {
    Write-Host "Existing token kept (not shown). Use -NewToken to rotate it."
}
Write-Host "Next: .\start_tunnel.ps1" -ForegroundColor Cyan
