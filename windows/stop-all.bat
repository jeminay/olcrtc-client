@echo off
taskkill /IM olcrtc.exe /F >nul 2>&1
taskkill /IM sing-box.exe /F >nul 2>&1
powershell -NoProfile -Command "Get-Process -Name olcrtc -ErrorAction SilentlyContinue | Stop-Process -Force; Get-Process -Name sing-box -ErrorAction SilentlyContinue | Stop-Process -Force"
echo All processes stopped.
timeout /t 2 /nobreak >nul
