@echo off
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting Administrator for TUN...
  powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)
set ENABLE_DEPRECATED_LEGACY_DNS_SERVERS=true
set ENABLE_DEPRECATED_MISSING_DOMAIN_RESOLVER=true

REM Load config variables
for /f "usebackq tokens=1,* delims==" %%A in ("olcrtc.conf") do (
  if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
)

REM Optional hosts fixes from config: WB_HOSTS=ip domain,ip domain
if not "%WB_HOSTS%"=="" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$hosts='%SystemRoot%\System32\drivers\etc\hosts'; '%WB_HOSTS%'.Split(',') | %% { $e=$_.Trim(); if($e){ $parts=$e.Split(' ',[System.StringSplitOptions]::RemoveEmptyEntries); if($parts.Count -ge 2){ $ip=$parts[0]; $name=$parts[1]; if(-not (Select-String -Path $hosts -Pattern ([regex]::Escape($name)) -Quiet)){ Add-Content -Path $hosts -Value ($ip+' '+$name) } } } }"
)

echo Generating sing-box config from olcrtc.conf...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0generate-singbox-config.ps1"
if %errorlevel% neq 0 pause & exit /b 1

echo Starting sing-box TUN...
echo Logs: sing-box.log
"%~dp0sing-box.exe" run -c "%~dp0sing-box-config.json"
pause
