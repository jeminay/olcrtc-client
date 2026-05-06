# olcRTC Dashboard v16.1
param()

$ErrorActionPreference = 'Continue'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $base

# --- Admin check ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Requesting Administrator..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

# --- Load config ---
$config = @{}
Get-Content (Join-Path $base 'olcrtc.conf') | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq '' -or $line.StartsWith('#') -or -not $line.Contains('=')) { return }
    $idx = $line.IndexOf('=')
    $config[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim()
}

$ROOM_ID = $config['ROOM_ID']
$KEY = $config['KEY']
$SOCKS_HOST = if ($config['SOCKS_HOST']) { $config['SOCKS_HOST'] } else { '127.0.0.1' }
$SOCKS_PORT = if ($config['SOCKS_PORT']) { $config['SOCKS_PORT'] } else { '8808' }
$DNS = if ($config['DNS']) { $config['DNS'] } else { '1.1.1.1:53' }

if (-not $ROOM_ID) {
    Write-Host "ERROR: set ROOM_ID in olcrtc.conf" -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}

function Write-Status($m) { Write-Host $m -ForegroundColor Cyan }
function Write-OK($m) { Write-Host $m -ForegroundColor Green }
function Write-Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Write-Err($m) { Write-Host $m -ForegroundColor Red }

function Resolve-WBHosts {
    $hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
    foreach ($d in @('wbstream01-el.wb.ru', 'wbstream01-e1.wb.ru', 'wb-stream-turn-1.wb.ru', 'stream.wb.ru')) {
        if (Select-String -Path $hosts -Pattern ([regex]::Escape($d)) -Quiet) { continue }
        $r = nslookup $d 2>$null | Select-String 'Address:\s*(\d+\.\d+\.\d+\.\d+)' | Select-Object -Last 1
        if ($r) {
            [System.IO.File]::AppendAllText($hosts, "`r`n$($r.Matches.Groups[1].Value) $d")
            Write-OK "  Added: $($r.Matches.Groups[1].Value) $d"
        }
    }
}

function Test-Socks {
    try {
        $r = curl.exe --socks5-hostname "${SOCKS_HOST}:${SOCKS_PORT}" -4 -s -k --ssl-no-revoke -m 10 https://icanhazip.com 2>$null
        if ($r -match '\d+\.\d+\.\d+\.\d+') { return $r.Trim() }
    } catch {}
    return $null
}

# --- Main ---
Clear-Host
Write-Status "=== olcRTC Dashboard v16.1 ==="
Write-Status "Room: $ROOM_ID`n"

# Step 1
Write-Status "[1/4] Resolving WB Stream..."
Resolve-WBHosts
Write-Host ""

# Step 2 - stop old, start olcrtc
Write-Status "[2/4] Starting olcRTC..."
Get-Process -Name olcrtc -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name sing-box -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

$logFile = Join-Path $base 'olcrtc.log'
if (Test-Path $logFile) { Remove-Item $logFile -Force }

$args = "-mode cnc -carrier wbstream -transport datachannel -id $ROOM_ID -key $KEY -link direct -dns $DNS -data `"$base\data`" -socks-host $SOCKS_HOST -socks-port $SOCKS_PORT"
$olcrtcProc = Start-Process -FilePath "$base\olcrtc.exe" -ArgumentList $args -WindowStyle Hidden -PassThru -RedirectStandardError $logFile
Write-Host "  PID: $($olcrtcProc.Id), waiting..."
Start-Sleep -Seconds 8

# Step 3
Write-Status "[3/4] Testing SOCKS..."
$ip = Test-Socks
if ($ip) { Write-OK "  SOCKS OK - IP: $ip" } else { Write-Err "  SOCKS FAILED" }
Write-Host ""

# Step 4
Write-Status "[4/4] Starting TUN..."
$env:ENABLE_DEPRECATED_LEGACY_DNS_SERVERS = 'true'
$env:ENABLE_DEPRECATED_MISSING_DOMAIN_RESOLVER = 'true'
& powershell -NoProfile -ExecutionPolicy Bypass -File "$base\generate-singbox-config.ps1"
$sbLog = Join-Path $base 'sing-box.log'
if (Test-Path $sbLog) { Remove-Item $sbLog -Force }
$sbOut = Join-Path $base 'sing-box-out.log'
if (Test-Path $sbOut) { Remove-Item $sbOut -Force }
$singboxProc = Start-Process -FilePath "$base\sing-box.exe" -ArgumentList "run","-c","$base\sing-box-config.json" -WindowStyle Hidden -PassThru -RedirectStandardError $sbLog -RedirectStandardOutput $sbOut
Write-Host "  sing-box PID: $($singboxProc.Id)"
Start-Sleep -Seconds 3

$tunIp = Test-Socks
if ($tunIp) { Write-OK "  TUN OK - IP: $tunIp`n" } else { Write-Warn "  TUN pending...`n" }

# --- State ---
$script:reqs = 0; $script:oks = 0; $script:fails = 0
$script:rxBytes = 0.0; $script:txBytes = 0.0
$script:wbState = 'connecting'
$script:lastRxBps = 0.0; $script:lastTxBps = 0.0
$script:logPos = 0
$script:healthy = $true
$script:startTime = Get-Date

# --- Parse new log lines ---
function Update-Metrics {
    if (-not (Test-Path $logFile)) { return }
    try {
        $fs = [System.IO.FileStream]::new($logFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        if ($script:logPos -gt $fs.Length) { $script:logPos = 0 }
        $fs.Position = $script:logPos
        $sr = [System.IO.StreamReader]::new($fs)
        while ($null -ne ($line = $sr.ReadLine())) {
            if ($line -match 'SOCKS request target') { $script:reqs++ }
            if ($line -match ' connected \S+ in ') { $script:oks++ }
            if ($line -match 'connect failed') { $script:fails++ }
            if ($line -match 'METRICS mux rx=([0-9.]+)KB/s tx=([0-9.]+)KB/s') {
                $script:lastRxBps = [double]$Matches[1] * 1024
                $script:lastTxBps = [double]$Matches[2] * 1024
            }
            if ($line -match 'state=(\S+)') {
                $script:wbState = $Matches[1]
            }
        }
        $script:logPos = $fs.Position
        $sr.Dispose(); $fs.Dispose()
    } catch {}
    # Accumulate approximate bytes
    $script:rxBytes += $script:lastRxBps * 5
    $script:txBytes += $script:lastTxBps * 5
}

function Fmt-Bytes($b) {
    if ($b -ge 1GB) { return "{0:N1}GB" -f ($b/1GB) }
    if ($b -ge 1MB) { return "{0:N1}MB" -f ($b/1MB) }
    if ($b -ge 1KB) { return "{0:N1}KB" -f ($b/1KB) }
    return "{0:N0}B" -f $b
}

function Fmt-Speed($bps) {
    if ($bps -ge 1MB) { return "{0:N1}MB/s" -f ($bps/1MB) }
    if ($bps -ge 1KB) { return "{0:N1}KB/s" -f ($bps/1KB) }
    return "{0:N0}B/s" -f $bps
}

# --- Dashboard loop ---
Write-Status "=== Live Dashboard (Ctrl+C to exit) ==="
Write-Host ""

$lastHealth = (Get-Date).AddSeconds(-25)
$dashY = [Console]::CursorTop

try {
    while ($true) {
        $now = Get-Date

        # Health check every 30s
        if (($now - $lastHealth).TotalSeconds -ge 30) {
            $lastHealth = $now
            $testIp = Test-Socks
            $script:healthy = [bool]$testIp
            if ($olcrtcProc.HasExited) { $script:healthy = $false }
            if ($singboxProc.HasExited) { $script:healthy = $false }
        }

        Update-Metrics

        # Build status line
        $olcSt = if ($olcrtcProc.HasExited) { "DEAD" } else { "RUN" }
        $sbSt = if ($singboxProc.HasExited) { "DEAD" } else { "RUN" }
        $hIcon = if ($script:healthy) { "OK" } else { "FAIL" }
        $uptime = ($now - $script:startTime).ToString('hh\:mm\:ss')

        $statusLine = "[{0}] olcrtc:{1} sing-box:{2} health:{3} WB:{4} up:{5}" -f $now.ToString('HH:mm:ss'), $olcSt, $sbSt, $hIcon, $script:wbState, $uptime
        $metricLine = "  rx:{0} tx:{1} | reqs:{2} ok:{3} fail:{4} | dn:{5} up:{6}" -f (Fmt-Speed $script:lastRxBps), (Fmt-Speed $script:lastTxBps), $script:reqs, $script:oks, $script:fails, (Fmt-Bytes $script:rxBytes), (Fmt-Bytes $script:txBytes)

        [Console]::SetCursorPosition(0, $dashY)
        [Console]::Write("{0,-" + [Console]::WindowWidth + "}", $statusLine)
        [Console]::SetCursorPosition(0, $dashY + 1)
        [Console]::Write("{0,-" + [Console]::WindowWidth + "}", $metricLine)

        Start-Sleep -Milliseconds 2000
    }
} finally {
    [Console]::SetCursorPosition(0, $dashY + 3)
    Write-Host ""
    Write-Status "Stopping..."
    Get-Process -Name sing-box -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-Process -Name olcrtc -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-OK "All stopped."
}
