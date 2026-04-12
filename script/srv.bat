@echo off
setlocal enabledelayedexpansion

chcp 65001

echo ЕСЛИ У ВАС ЕСТЬ ПРОБЛЕМЫ - Я В КУРСЕ, ПРОЕКТ В БЕТЕ, ПО ПРОБЛЕМАМ В ЧАТ t.me/openlibrecommunity ИЛИ ВООБЩЕ НЕКУДА, ЖДИТЕ РЕЛИЗА
echo.

set CONTAINER_NAME=olcrtc-server
set IMAGE_NAME=docker.io/library/golang:1.26-alpine
set REPO_URL=https://github.com/openlibrecommunity/olcrtc.git
set WORK_DIR=%TEMP%\olcrtc-deploy

echo === OlcRTC Server Deployment Script ===
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
set /p USE_PROXY=Use SOCKS5 proxy for egress? (y/N): 

set EXTRA_ARGS=

if /I "%USE_PROXY%"=="Y" (
    set /p PROXY_ADDR_INPUT=Enter SOCKS5 proxy address [default: 127.0.0.1]: 
    if "!PROXY_ADDR_INPUT!"=="" (
        set SOCKS_PROXY_ADDR=127.0.0.1
    ) else (
        set SOCKS_PROXY_ADDR=!PROXY_ADDR_INPUT!
    )

    set /p PROXY_PORT_INPUT=Enter SOCKS5 proxy port [default: 1080]: 
    if "!PROXY_PORT_INPUT!"=="" (
        set SOCKS_PROXY_PORT=1080
    ) else (
        set SOCKS_PROXY_PORT=!PROXY_PORT_INPUT!
    )

    echo [*] Will use SOCKS5 proxy: !SOCKS_PROXY_ADDR!:!SOCKS_PROXY_PORT!
    set EXTRA_ARGS=-socks-proxy !SOCKS_PROXY_ADDR! -socks-proxy-port !SOCKS_PROXY_PORT!
)

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
    -v "%WORK_DIR%:/app:Z" ^
    -w /app ^
    %IMAGE_NAME% ^
    sh -c "go mod tidy && go build -o olcrtc cmd/olcrtc/main.go"

if not exist "%WORK_DIR%\olcrtc" (
    echo [X] Build failed
    pause
    exit /b 1
)

set KEY_FILE=%USERPROFILE%\.olcrtc_key

if exist "%KEY_FILE%" (
    echo [*] Loading existing encryption key...
    set /p KEY=<"%KEY_FILE%"
) else (
    echo [*] Generating new encryption key...
    for /f %%i in ('powershell -Command "$bytes = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes); [System.BitConverter]::ToString($bytes) -replace '-'"') do set KEY=%%i
    echo !KEY!> "%KEY_FILE%"
    echo.
    echo ==========================================
    echo NEW ENCRYPTION KEY ^(saved to %KEY_FILE%^):
    echo !KEY!
    echo ==========================================
    echo.
)

echo [*] Starting OlcRTC server...
podman run -d ^
    --name %CONTAINER_NAME% ^
    --restart unless-stopped ^
    -v "%WORK_DIR%:/app:Z" ^
    -w /app ^
    %IMAGE_NAME% ^
    ./olcrtc -mode srv -id "%ROOM_ID%" -key "%KEY%" %EXTRA_ARGS%

timeout /NOBREAK /t 2 >nul

echo.
echo [+] Server started successfully!
echo.
echo Container name: %CONTAINER_NAME%
echo Room ID:        %ROOM_ID%
echo Encryption key: %KEY%

if defined EXTRA_ARGS (
    echo SOCKS5 proxy:   %SOCKS_PROXY_ADDR%:%SOCKS_PROXY_PORT%
)

echo.
echo View logs:
echo   podman logs -f %CONTAINER_NAME%
echo.
echo Stop server:
echo   podman stop %CONTAINER_NAME%
echo.
echo Client command:
echo   ./olcrtc -mode cnc -id "%ROOM_ID%" -key "%KEY%" -socks-port 1080
echo.

pause