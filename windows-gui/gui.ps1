param([string]$AppRoot)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = 'Continue'

if ([string]::IsNullOrWhiteSpace($AppRoot)) { $AppRoot = Split-Path -Parent $PSCommandPath }
$root = $AppRoot

function Is-Admin {
  $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Is-Admin)) {
  Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList @(
    '-NoProfile','-STA','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"",'-AppRoot',"`"$root`""
  )
  exit
}

$confPath = Join-Path $root 'olcrtc-gui.conf'
$olc = Join-Path $root 'olcrtc.exe'
$sb = Join-Path $root 'sing-box.exe'
$olcLog = Join-Path $root 'olcrtc.log'
$olcErr = Join-Path $root 'olcrtc.err.log'
$sbOut = Join-Path $root 'sing-box.out.log'
$sbErr = Join-Path $root 'sing-box.err.log'
$sbLog = Join-Path $root 'sing-box.log'
$runtimeLog = Join-Path $root 'gui.log'

function Default-Conf {
  [pscustomobject]@{
    room_id=''
    key=''
    socks_host='127.0.0.1'
    socks_port=8808
    dns='1.1.1.1:53'
    direct_domains='wb.ru'
    direct_ips='164.215.97.172/32,185.62.200.94/32,185.62.202.8/32,194.1.214.97/32'
    private_direct=$true
  }
}
function Load-Conf {
  if (Test-Path $confPath) {
    try { return (Get-Content $confPath -Raw | ConvertFrom-Json) } catch {}
  }
  return Default-Conf
}
function Save-Conf($c) { $c | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $confPath }
function Csv($s) { @($s -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
function Quote-Arg([string]$s) {
  if ($null -eq $s) { return '""' }
  if ($s -match '[\s"]') { return '"' + ($s -replace '"','\"') + '"' }
  return $s
}
function Join-Args($arr) { ($arr | ForEach-Object { Quote-Arg ([string]$_) }) -join ' ' }

function Write-SingBoxConfig($cc) {
  $directProcesses = @('olcrtc.exe','sing-box.exe')
  $directDomains = Csv $cc.direct_domains
  $directIPs = Csv $cc.direct_ips
  $rules = New-Object System.Collections.ArrayList
  [void]$rules.Add([ordered]@{ ip_cidr=@('172.19.0.2/32'); port=53; action='hijack-dns' })
  [void]$rules.Add([ordered]@{ protocol='dns'; action='hijack-dns' })
  [void]$rules.Add([ordered]@{ process_name=$directProcesses; outbound='direct' })
  if ($directDomains.Count -gt 0) { [void]$rules.Add([ordered]@{ domain_suffix=$directDomains; outbound='direct' }) }
  if ($directIPs.Count -gt 0) { [void]$rules.Add([ordered]@{ ip_cidr=$directIPs; outbound='direct' }) }
  if ($cc.private_direct) { [void]$rules.Add([ordered]@{ ip_is_private=$true; outbound='direct' }) }

  $cfg = [ordered]@{
    log = [ordered]@{ level='debug'; timestamp=$true; output=$sbLog }
    dns = [ordered]@{
      servers = @(
        [ordered]@{ tag='remote'; address='tcp://1.1.1.1'; detour='proxy' },
        [ordered]@{ tag='local'; address='local' }
      )
      rules = @(
        [ordered]@{ domain_suffix=$directDomains; server='local' },
        [ordered]@{ domain_suffix=@('local','lan'); server='local' }
      )
      final = 'remote'
    }
    inbounds = @([ordered]@{
      type='tun'; tag='tun-in'; interface_name='singbox-tun'; address=@('172.19.0.1/30')
      auto_route=$true; strict_route=$true; stack='mixed'
    })
    outbounds = @(
      [ordered]@{ type='socks'; tag='proxy'; server=$cc.socks_host; server_port=[int]$cc.socks_port; version='5' },
      [ordered]@{ type='direct'; tag='direct' },
      [ordered]@{ type='block'; tag='block' }
    )
    route = [ordered]@{ auto_detect_interface=$true; rules=$rules; final='proxy'; default_domain_resolver='local' }
  }
  $path = Join-Path $root 'sing-box-config.json'
  $json = $cfg | ConvertTo-Json -Depth 20
  [IO.File]::WriteAllText($path, $json, [Text.UTF8Encoding]::new($false))
  return $path
}

function Resolve-WBHosts {
  $hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
  foreach ($d in @('wbstream01-el.wb.ru','wbstream01-e1.wb.ru','wb-stream-turn-1.wb.ru','stream.wb.ru')) {
    if (Select-String -Path $hosts -Pattern ([regex]::Escape($d)) -Quiet -ErrorAction SilentlyContinue) { continue }
    $r = nslookup $d 2>$null | Select-String 'Address:\s*(\d+\.\d+\.\d+\.\d+)' | Select-Object -Last 1
    if ($r) {
      [IO.File]::AppendAllText($hosts, "`r`n$($r.Matches.Groups[1].Value) $d")
      Add-Log "hosts: added $($r.Matches.Groups[1].Value) $d"
    }
  }
}

function Test-Socks($cc) {
  try {
    $out = & curl.exe --socks5-hostname "$($cc.socks_host):$($cc.socks_port)" -4 -s -k --ssl-no-revoke -m 12 https://icanhazip.com 2>$null
    if ($out -match '\d+\.\d+\.\d+\.\d+') { return $out.Trim() }
  } catch {}
  return $null
}

$c = Load-Conf
$form = New-Object Windows.Forms.Form
$form.Text = 'olcRTC Easy - Windows VPN MVP'
$form.Size = New-Object Drawing.Size(920, 680)
$form.MinimumSize = New-Object Drawing.Size(850, 620)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Segoe UI', 9)

function Add-Label($text,$x,$y,$w=105) { $l=New-Object Windows.Forms.Label; $l.Text=$text; $l.Location=New-Object Drawing.Point($x,$y); $l.Size=New-Object Drawing.Size($w,22); $form.Controls.Add($l); return $l }
function Add-Text($text,$x,$y,$w,$password=$false) { $t=New-Object Windows.Forms.TextBox; $t.Text=$text; $t.Location=New-Object Drawing.Point($x,$y); $t.Size=New-Object Drawing.Size($w,24); if($password){$t.UseSystemPasswordChar=$true}; $form.Controls.Add($t); return $t }

Add-Label 'Room ID' 18 20; $roomBox = Add-Text $c.room_id 130 18 720
Add-Label 'Key' 18 55; $keyBox = Add-Text $c.key 130 53 720 $true
Add-Label 'SOCKS host' 18 90; $hostBox = Add-Text $c.socks_host 130 88 145
Add-Label 'SOCKS port' 300 90 85; $portBox = Add-Text ([string]$c.socks_port) 390 88 80
Add-Label 'DNS' 495 90 45; $dnsBox = Add-Text $c.dns 540 88 145
Add-Label 'Direct IPs' 18 125; $directIPBox = Add-Text $c.direct_ips 130 123 720

$status = New-Object Windows.Forms.Label
$status.Text = 'Status: stopped'
$status.Location = New-Object Drawing.Point(18,158)
$status.Size = New-Object Drawing.Size(830,24)
$status.ForeColor = [Drawing.Color]::DarkRed
$form.Controls.Add($status)

$logBox = New-Object Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ScrollBars = 'Vertical'
$logBox.ReadOnly = $true
$logBox.WordWrap = $false
$logBox.Location = New-Object Drawing.Point(18,190)
$logBox.Size = New-Object Drawing.Size(865,370)
$form.Controls.Add($logBox)

$saveBtn = New-Object Windows.Forms.Button; $saveBtn.Text='Save'; $saveBtn.Location=New-Object Drawing.Point(18,578); $saveBtn.Size=New-Object Drawing.Size(90,34); $form.Controls.Add($saveBtn)
$connectBtn = New-Object Windows.Forms.Button; $connectBtn.Text='Connect'; $connectBtn.Location=New-Object Drawing.Point(122,578); $connectBtn.Size=New-Object Drawing.Size(105,34); $form.Controls.Add($connectBtn)
$disconnectBtn = New-Object Windows.Forms.Button; $disconnectBtn.Text='Disconnect'; $disconnectBtn.Location=New-Object Drawing.Point(242,578); $disconnectBtn.Size=New-Object Drawing.Size(105,34); $disconnectBtn.Enabled=$false; $form.Controls.Add($disconnectBtn)
$testBtn = New-Object Windows.Forms.Button; $testBtn.Text='Test IP'; $testBtn.Location=New-Object Drawing.Point(362,578); $testBtn.Size=New-Object Drawing.Size(90,34); $form.Controls.Add($testBtn)
$openLogsBtn = New-Object Windows.Forms.Button; $openLogsBtn.Text='Open Logs'; $openLogsBtn.Location=New-Object Drawing.Point(466,578); $openLogsBtn.Size=New-Object Drawing.Size(100,34); $form.Controls.Add($openLogsBtn)
$exitBtn = New-Object Windows.Forms.Button; $exitBtn.Text='Exit'; $exitBtn.Location=New-Object Drawing.Point(580,578); $exitBtn.Size=New-Object Drawing.Size(90,34); $form.Controls.Add($exitBtn)

$global:olcProc = $null
$global:sbProc = $null
$global:offsets = @{}

function Add-Log($s) {
  $line = (Get-Date -Format HH:mm:ss) + ' ' + $s + "`r`n"
  $logBox.AppendText($line)
  Add-Content -Path $runtimeLog -Value $line -ErrorAction SilentlyContinue
}
function Cur-Conf {
  $port = 8808
  [void][int]::TryParse($portBox.Text.Trim(), [ref]$port)
  [pscustomobject]@{
    room_id=$roomBox.Text.Trim()
    key=$keyBox.Text.Trim()
    socks_host=if($hostBox.Text.Trim()){ $hostBox.Text.Trim() } else { '127.0.0.1' }
    socks_port=$port
    dns=if($dnsBox.Text.Trim()){ $dnsBox.Text.Trim() } else { '1.1.1.1:53' }
    direct_domains='wb.ru'
    direct_ips=$directIPBox.Text.Trim()
    private_direct=$true
  }
}
function Set-Status($text,$color) { $status.Text = 'Status: ' + $text; $status.ForeColor = $color }

function Stop-Tree($p) {
  try {
    if($p -and -not $p.HasExited) { & taskkill.exe /PID $p.Id /T /F | Out-Null }
  } catch {
    try { if($p -and -not $p.HasExited){ $p.Kill() } } catch {}
  }
}
function Stop-All {
  Stop-Tree $global:sbProc
  Stop-Tree $global:olcProc
  try { Get-Process -Name sing-box -ErrorAction SilentlyContinue | Stop-Process -Force } catch {}
  try { Get-Process -Name olcrtc -ErrorAction SilentlyContinue | Stop-Process -Force } catch {}
  $global:sbProc=$null; $global:olcProc=$null
  $connectBtn.Enabled=$true; $disconnectBtn.Enabled=$false
  Set-Status 'stopped' ([Drawing.Color]::DarkRed)
  Add-Log 'Disconnected'
}

function Start-HiddenProcess($file,$argList,$stdout,$stderr,$envs=@{}) {
  New-Item -ItemType File -Force -Path $stdout | Out-Null
  New-Item -ItemType File -Force -Path $stderr | Out-Null
  $cleanArgs = @($argList | Where-Object { $null -ne $_ -and ([string]$_).Length -gt 0 } | ForEach-Object { [string]$_ })
  $argString = Join-Args $cleanArgs
  $psi = New-Object Diagnostics.ProcessStartInfo
  $psi.FileName = $file
  $psi.Arguments = $argString
  $psi.WorkingDirectory = $root
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.EnvironmentVariables['ENABLE_DEPRECATED_LEGACY_DNS_SERVERS'] = 'true'
  $psi.EnvironmentVariables['ENABLE_DEPRECATED_MISSING_DOMAIN_RESOLVER'] = 'true'
  foreach($k in $envs.Keys){ $psi.EnvironmentVariables[$k] = $envs[$k] }
  $p = New-Object Diagnostics.Process
  $p.StartInfo = $psi
  $outWriter = [IO.StreamWriter]::new($stdout, $true, [Text.UTF8Encoding]::new($false))
  $errWriter = [IO.StreamWriter]::new($stderr, $true, [Text.UTF8Encoding]::new($false))
  Register-ObjectEvent $p OutputDataReceived DataReceived -Action { if($EventArgs.Data){ $Event.MessageData.WriteLine($EventArgs.Data); $Event.MessageData.Flush() } } -MessageData $outWriter | Out-Null
  Register-ObjectEvent $p ErrorDataReceived DataReceived -Action { if($EventArgs.Data){ $Event.MessageData.WriteLine($EventArgs.Data); $Event.MessageData.Flush() } } -MessageData $errWriter | Out-Null
  [void]$p.Start()
  $p.BeginOutputReadLine()
  $p.BeginErrorReadLine()
  Add-Log ('RUN: ' + $file + ' ' + $argString)
  return $p
}

function Wait-Socks($cc, [int]$seconds) {
  for($i=1; $i -le $seconds; $i++) {
    if($global:olcProc -and $global:olcProc.HasExited) {
      Add-Log "olcRTC exited early, cmd exit code=$($global:olcProc.ExitCode)"
      return $null
    }
    $ip = Test-Socks $cc
    if($ip) { return $ip }
    Start-Sleep -Seconds 1
  }
  return $null
}

$saveBtn.Add_Click({ Save-Conf (Cur-Conf); Add-Log "Saved config: $confPath" })

$connectBtn.Add_Click({
  $cc = Cur-Conf
  Save-Conf $cc
  if([string]::IsNullOrWhiteSpace($cc.room_id) -or [string]::IsNullOrWhiteSpace($cc.key)) { Add-Log 'ERROR: Room ID and Key are required'; return }
  Stop-All
  $connectBtn.Enabled=$false; $disconnectBtn.Enabled=$true
  Set-Status 'connecting...' ([Drawing.Color]::DarkOrange)
  Remove-Item $olcLog,$olcErr,$sbOut,$sbErr,$sbLog -ErrorAction SilentlyContinue
  $global:offsets = @{}
  try {
    Resolve-WBHosts
    $dataDir = Join-Path $root 'data'; New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $olcArgs = @('-mode','cnc','-carrier','wbstream','-transport','datachannel','-id',$cc.room_id,'-key',$cc.key,'-link','direct','-dns',$cc.dns,'-data',$dataDir,'-socks-host',$cc.socks_host,'-socks-port',[string]$cc.socks_port,'-debug')
    Add-Log 'Starting olcRTC...'
    $global:olcProc = Start-HiddenProcess $olc $olcArgs $olcLog $olcErr
    Add-Log "olcRTC wrapper PID: $($global:olcProc.Id)"
    $ip = Wait-Socks $cc 30
    if($ip){ Add-Log "SOCKS OK: $ip" } else { Add-Log 'SOCKS not ready after 30s; starting TUN anyway. Check olcrtc.log / olcrtc.err.log.' }
    $sbConf = Write-SingBoxConfig $cc
    Add-Log 'Starting sing-box TUN...'
    $global:sbProc = Start-HiddenProcess $sb @('run','-c',$sbConf) $sbOut $sbErr
    Add-Log "sing-box PID: $($global:sbProc.Id)"
    Start-Sleep -Seconds 2
    Set-Status 'connected' ([Drawing.Color]::DarkGreen)
    Add-Log 'Connected. If traffic does not switch, check logs and run Test IP.'
  } catch {
    Add-Log ('ERROR: ' + $_.Exception.Message)
    Stop-All
  }
})

$disconnectBtn.Add_Click({ Stop-All })
$testBtn.Add_Click({ $cc=Cur-Conf; $ip=Test-Socks $cc; if($ip){ Add-Log "TEST SOCKS IP: $ip" } else { Add-Log 'TEST FAILED: SOCKS did not return IP' } })
$openLogsBtn.Add_Click({ Start-Process explorer.exe $root })
$exitBtn.Add_Click({ Stop-All; $form.Close() })

function Append-NewFile($path,$prefix) {
  if(-not (Test-Path $path)) { return }
  try {
    $txt = Get-Content $path -Raw -ErrorAction SilentlyContinue
    if($null -eq $txt) { return }
    $last = 0
    if($global:offsets.ContainsKey($path)) { $last = [int]$global:offsets[$path] }
    if($txt.Length -gt $last) {
      $chunk = $txt.Substring($last)
      if($chunk.Trim().Length -gt 0) { $logBox.AppendText(($chunk -replace "`n","`r`n")) }
      $global:offsets[$path] = $txt.Length
    }
  } catch {}
}

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1500
$timer.Add_Tick({
  Append-NewFile $olcLog '[olcrtc] '
  Append-NewFile $olcErr '[olcrtc] '
  Append-NewFile $sbOut '[sing-box] '
  Append-NewFile $sbErr '[sing-box-err] '
  Append-NewFile $sbLog '[sing-box-core] '
  if($global:olcProc -and $global:sbProc) {
    if($global:olcProc.HasExited) {
      Set-Status "olcRTC exited code=$($global:olcProc.ExitCode)" ([Drawing.Color]::DarkRed)
    } elseif($global:sbProc.HasExited) {
      Set-Status "sing-box exited code=$($global:sbProc.ExitCode)" ([Drawing.Color]::DarkRed)
    } else {
      Set-Status 'connected' ([Drawing.Color]::DarkGreen)
    }
  }
})
$timer.Start()

$form.Add_FormClosing({ Stop-All })
Add-Log "Runtime: $root"
Add-Log "Config: $confPath"
Add-Log 'Running as Administrator. Fill Room ID + Key, then Connect.'
[void]$form.ShowDialog()
