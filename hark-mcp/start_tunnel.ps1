# Expose hark-mcp (127.0.0.1:8765) through an OUTBOUND Cloudflare Tunnel (no open ports, works behind NAT).
#
# Quick tunnel (random https://xxxx.trycloudflare.com, changes on every restart):
#   powershell -ExecutionPolicy Bypass -File .\hark-mcp\start_tunnel.ps1
# Named tunnel (stable hostname on your own Cloudflare domain):
#   powershell -ExecutionPolicy Bypass -File .\hark-mcp\start_tunnel.ps1 -Named -Hostname mcp.example.com
param(
    [int]$Port = 8765,
    [switch]$Named,
    [string]$TunnelName = "hark-mcp",
    [string]$Hostname = "",
    [ValidateSet("http2", "quic", "auto")] [string]$Protocol = "http2"   # http2 = TCP 443, more reliable when UDP is filtered
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1) cloudflared ------------------------------------------------------------------
$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) {
    $cf = Join-Path $Here "bin\cloudflared.exe"
    if (-not (Test-Path $cf)) {
        New-Item -ItemType Directory -Force (Split-Path $cf) | Out-Null
        Write-Host "Downloading cloudflared..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cf
        # alternative: winget install --id Cloudflare.cloudflared
    }
}
Write-Host "cloudflared: $cf"
$ErrorActionPreference = "Continue"
pm2 delete hark-tunnel 2>$null | Out-Null

if (-not $Named) {
    # 2a) quick tunnel under pm2 ---------------------------------------------------
    pm2 start "$cf" --name hark-tunnel --interpreter none -- tunnel --no-autoupdate --protocol $Protocol --url "http://127.0.0.1:$Port"
    pm2 save
    Write-Host "Waiting for the trycloudflare URL..."
    $url = $null
    for ($i = 0; $i -lt 30 -and -not $url; $i++) {
        Start-Sleep -Seconds 2
        $log = (pm2 logs hark-tunnel --nostream --lines 80 2>&1) -join "`n"
        if ($log -match "https://[a-z0-9-]+\.trycloudflare\.com") { $url = $Matches[0] }
    }
    if ($url) {
        Write-Host ""
        Write-Host "MCP URL to give Hark (in chat):  $url/mcp" -ForegroundColor Green
        Write-Host "The token goes ONLY into Hark's secure form."
        Write-Host "Note: this URL changes whenever the tunnel restarts. Use -Named for a stable one."
    } else {
        Write-Host "No URL yet. Check: pm2 logs hark-tunnel --nostream --lines 50" -ForegroundColor Yellow
        Write-Host "If Cloudflare is unreachable from this network, try -Protocol quic, or use ngrok (see README_FA.md)."
    }
    exit 0
}

# 2b) named tunnel -----------------------------------------------------------------
if (-not $Hostname) { throw "-Named needs -Hostname, e.g. mcp.yourdomain.com (domain must be on Cloudflare)" }
$cfdir = Join-Path $env:USERPROFILE ".cloudflared"
if (-not (Test-Path (Join-Path $cfdir "cert.pem"))) {
    Write-Host "A browser will open: log in to Cloudflare and authorize your domain."
    & $cf tunnel login
}
$exists = (& $cf tunnel list 2>&1 | Out-String) -match "\s$TunnelName\s"
if (-not $exists) { & $cf tunnel create $TunnelName }
& $cf tunnel route dns $TunnelName $Hostname
$tid = ((& $cf tunnel list 2>&1 | Out-String) -split "`n" | Where-Object { $_ -match "\s$TunnelName\s" } | Select-Object -First 1).Trim().Split(" ")[0]
$cfg = Join-Path $cfdir "hark-mcp.yml"
@"
tunnel: $tid
credentials-file: $cfdir\$tid.json
protocol: $Protocol
ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:$Port
  - service: http_status:404
"@ | Set-Content -Encoding UTF8 $cfg
pm2 start "$cf" --name hark-tunnel --interpreter none -- tunnel --no-autoupdate --config "$cfg" run $TunnelName
pm2 save
Write-Host ""
Write-Host "MCP URL to give Hark (in chat):  https://$Hostname/mcp" -ForegroundColor Green
Write-Host "The token goes ONLY into Hark's secure form."
