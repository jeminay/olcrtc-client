@echo off
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%A in ("olcrtc.conf") do (
  if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
)
if "%ROOM_ID%"=="" (
  echo ERROR: set ROOM_ID in olcrtc.conf
  pause
  exit /b 1
)
echo Starting olcRTC SOCKS on %SOCKS_HOST%:%SOCKS_PORT% ...
echo Logs: olcrtc.log
start "olcrtc" /min "%~dp0olcrtc.exe" -mode cnc -carrier wbstream -transport datachannel -id %ROOM_ID% -key %KEY% -link direct -dns %DNS% -data "%~dp0data" -socks-host %SOCKS_HOST% -socks-port %SOCKS_PORT% ^>^> "%~dp0olcrtc.log" 2^>^&1
timeout /t 5 /nobreak >nul
echo Test:
echo curl.exe --socks5-hostname %SOCKS_HOST%:%SOCKS_PORT% https://icanhazip.com
pause
