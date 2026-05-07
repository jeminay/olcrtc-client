$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$confPath = Join-Path $base 'olcrtc.conf'
$config = @{}
Get-Content $confPath | ForEach-Object {
  $line = $_.Trim()
  if ($line -eq '' -or $line.StartsWith('#') -or -not $line.Contains('=')) { return }
  $idx = $line.IndexOf('=')
  $key = $line.Substring(0, $idx).Trim()
  $val = $line.Substring($idx + 1).Trim()
  $config[$key] = $val
}
function Csv($name) {
  if (-not $config.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($config[$name])) { return @() }
  return @($config[$name].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
}
$directProcesses = Csv 'DIRECT_PROCESSES'
if ($directProcesses.Count -eq 0) { $directProcesses = @('olcrtc.exe','sing-box.exe') }
$directDomains = Csv 'DIRECT_DOMAIN_SUFFIXES'
$directIPs = Csv 'DIRECT_IPS'
$privateDirect = $true
if ($config.ContainsKey('PRIVATE_DIRECT') -and $config['PRIVATE_DIRECT'].ToLower() -eq 'false') { $privateDirect = $false }
$rules = @()
$rules += [ordered]@{ ip_cidr = @('172.19.0.2/32'); port = 53; action = 'hijack-dns' }
$rules += [ordered]@{ protocol = 'dns'; action = 'hijack-dns' }
if ($directProcesses.Count -gt 0) { $rules += [ordered]@{ process_name = $directProcesses; outbound = 'direct' } }
if ($directDomains.Count -gt 0) { $rules += [ordered]@{ domain_suffix = $directDomains; outbound = 'direct' } }
if ($directIPs.Count -gt 0) { $rules += [ordered]@{ ip_cidr = $directIPs; outbound = 'direct' } }
if ($privateDirect) { $rules += [ordered]@{ ip_is_private = $true; outbound = 'direct' } }
$sing = [ordered]@{
  log = [ordered]@{ level = 'debug'; timestamp = $true; output = 'sing-box.log' }
  dns = [ordered]@{
    servers = @(
      [ordered]@{ tag = 'remote'; address = 'tcp://1.1.1.1'; detour = 'proxy' },
      [ordered]@{ tag = 'local'; address = 'local' }
    )
    rules = @(
      [ordered]@{ domain_suffix = $directDomains; server = 'local' },
      [ordered]@{ domain_suffix = @('local','lan'); server = 'local' }
    )
    final = 'remote'
  }
  inbounds = @([ordered]@{
    type = 'tun'; tag = 'tun-in'; interface_name = 'singbox-tun'; address = @('172.19.0.1/30')
    auto_route = $true; strict_route = $true; stack = 'mixed'
  })
  outbounds = @(
    [ordered]@{ type = 'socks'; tag = 'proxy'; server = $config['SOCKS_HOST']; server_port = [int]$config['SOCKS_PORT']; version = '5' },
    [ordered]@{ type = 'direct'; tag = 'direct' },
    [ordered]@{ type = 'block'; tag = 'block' }
  )
  route = [ordered]@{ auto_detect_interface = $true; rules = $rules; final = 'proxy'; default_domain_resolver = 'local' }
}
$json = $sing | ConvertTo-Json -Depth 20
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText((Join-Path $base 'sing-box-config.json'), $json, $utf8NoBom)
Write-Host 'Generated sing-box-config.json'
