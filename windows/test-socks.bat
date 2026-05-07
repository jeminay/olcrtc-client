@echo off
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%A in ("olcrtc.conf") do (
  if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
)
curl.exe --socks5-hostname %SOCKS_HOST%:%SOCKS_PORT% https://icanhazip.com
pause
