@echo off
setlocal enabledelayedexpansion

chcp 65001

echo ЕСЛИ У ВАС ЕСТЬ ПРОБЛЕМЫ - Я В КУРСЕ, ПРОЕКТ В БЕТЕ, ПО ПРОБЛЕМАМ В ЧАТ t.me/openlibrecommunity ИЛИ ВООБЩЕ НЕКУДА, ЖДИТЕ РЕЛИЗА
echo.

set CONTAINER_NAME=olcrtc-client
set IMAGE_NAME=docker.io/library/golang:1.26-alpine
set REPO_URL=https://github.com/openlibrecommunity/olcrtc.git
set WORK_DIR=%TEMP%\olcrtc-client
set SOCKS_PORT=8808

echo === OlcRTC Client Deployment Script ===
echo.

where podman >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] podman not found. Install podman manually:
    echo https://podman.io/getting-started/installation
    pause
    exit /b 1
)

echo [+] Using Podman
echo.

set /p ROOM_ID=Enter Telemost Room ID: 
if "%ROOM_ID%"=="" (
    echo [X] Room ID cannot be empty
    pause
    exit /b 1
)

echo.
set /p KEY=Enter Encryption Key (hex): 
if "%KEY%"=="" (
    echo [X] Encryption key cannot be empty
    pause
    exit /b 1
)

echo.
set /p PORT_INPUT=SOCKS5 port [default: 8808]: 
if not "%PORT_INPUT%"=="" set SOCKS_PORT=%PORT_INPUT%

echo.
echo [*] Stopping old instance...
podman stop %CONTAINER_NAME% >nul 2>&1
podman rm %CONTAINER_NAME% >nul 2>&1

echo [*] Cleaning workspace...
rmdir /s /q "%WORK_DIR%" >nul 2>&1
mkdir "%WORK_DIR%"

echo [*] Cloning repository...
git clone --depth 1 %REPO_URL% "%WORK_DIR%"

echo [*] Pulling Go image...
podman pull %IMAGE_NAME%

echo [*] Building OlcRTC...
podman run --rm ^
    -v "%WORK_DIR%":/app ^
    -w /app ^
    %IMAGE_NAME% ^
    sh -c "go mod tidy && go build -o olcrtc cmd/olcrtc/main.go"

if not exist "%WORK_DIR%\olcrtc" (
    echo [X] Build failed
    pause
    exit /b 1
)

echo [*] Starting OlcRTC client...
podman run -d ^
    --name %CONTAINER_NAME% ^
    --restart unless-stopped ^
    -p 127.0.0.1:%SOCKS_PORT%:%SOCKS_PORT% ^
    -v "%WORK_DIR%:/app:Z" ^
    -w /app ^
    %IMAGE_NAME% ^
    ./olcrtc -mode cnc -id "%ROOM_ID%" -key "%KEY%" -socks-port %SOCKS_PORT% -socks-host 0.0.0.0

timeout /NOBREAK /t 2 >nul

echo.
echo [+] Client started successfully!
echo.
echo Container name: %CONTAINER_NAME%
echo Room ID: %ROOM_ID%
echo SOCKS5 proxy: 127.0.0.1:%SOCKS_PORT%
echo.
echo View logs:
echo   podman logs -f %CONTAINER_NAME%
echo.
echo Stop client:
echo   podman stop %CONTAINER_NAME%
echo.
echo Test proxy:
echo   curl -x socks5h://127.0.0.1:%SOCKS_PORT% -fsSL https://ifconfig.me
echo.

pause