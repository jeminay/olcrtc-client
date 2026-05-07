# olcrtc-client

**Один клик - и весь трафик Windows/macOS идёт через WebRTC-туннель.**

Форк [olcRTC](https://github.com/openlibrecommunity/olcrtc) с portable-обвязками для Windows и macOS. Поднимает SOCKS5-прокси через WebRTC DataChannel, заворачивает системный трафик через `sing-box` TUN и показывает статус/логи.

Сейчас основной готовый пользовательский клиент - **Windows GUI MVP**.

## Как это работает

```text
Браузер / любое приложение
        ↓
   sing-box TUN (перехватывает системный трафик)
        ↓
   SOCKS5 127.0.0.1:8808
        ↓
   olcRTC клиент (шифрует, упаковывает в WebRTC)
        ↓
   WB Stream - публичный TURN/WebRTC-сервис Wildberries
        ↓
   olcRTC сервер на VPS
        ↓
   Интернет
```

Для провайдера трафик выглядит как обычное WebRTC-подключение к WB Stream. Сейчас поддерживаемый carrier для easy-пакетов - **WB Stream**.

## Готовый релиз

Windows GUI v0.1.0:

- Release: https://github.com/jeminay/olcrtc-client/releases/tag/v0.1.0
- Download: https://github.com/jeminay/olcrtc-client/releases/download/v0.1.0/olcrtc-client-gui-v0.1.0-windows-amd64.zip

## Быстрый старт - Windows GUI

1. Скачать `olcrtc-client-gui-v0.1.0-windows-amd64.zip` из релиза.
2. Распаковать в отдельную папку, например `C:\olcrtc-client`.
3. Запустить `olcrtc-client-gui-v0.1.0-windows-amd64.exe`.
4. Вставить:
   - `ROOM_ID` из лога сервера
   - `KEY` - 32-byte hex key
5. Нажать **Connect**.
6. В статусе должно быть `connected`.
7. `Test IP` должен показать IP VPS.

GUI работает в portable-режиме: `olcrtc.exe`, `sing-box.exe`, конфиг, логи и `data/` лежат рядом с GUI `.exe`, а не в AppData.

## Сервер (VPS, Linux amd64)

```bash
GOTOOLCHAIN=local GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o olcrtc-server ./cmd/olcrtc

./olcrtc-server -mode srv -carrier wbstream -transport datachannel -id any \
  -key <ВАШ_КЛЮЧ_32_байта_hex> -link direct -dns 1.1.1.1:53 -data ./data
```

В логе появится:

```text
WB Stream room created: <ROOM_ID>
To connect client use: -id <ROOM_ID>
```

`ROOM_ID` меняется после каждого рестарта сервера.

## Альтернативные portable-пакеты

### Windows scripts

Папка `windows/` содержит bat/PowerShell-пакет без GUI:

```bash
./windows/build-release.sh v0.16
```

Внутри пакета:

- `start-all.bat` - старт PowerShell dashboard
- `start-dashboard.ps1` - olcrtc + sing-box + live metrics
- `start-olcrtc-only.bat` - только SOCKS5
- `start-tun.bat` - только TUN
- `stop-all.bat` - остановка
- `test-socks.bat` - проверка SOCKS

### macOS scripts

Папка `macos/` содержит portable shell-пакет:

```bash
# Apple Silicon
./macos/build-release.sh v0.16 arm64

# Intel Mac
./macos/build-release.sh v0.16 amd64
```

macOS-пакет собирается и валидируется, но end-to-end на реальном Mac пока не проверялся.

## Сборка Windows GUI

```bash
./windows-gui/build-release.sh v0.1.0
```

Результат:

```text
release/olcrtc-client-gui-v0.1.0-windows-amd64.exe
release/olcrtc-client-gui-v0.1.0-windows-amd64.zip
```

GUI single-exe включает embedded assets:

- `olcrtc.exe`
- `sing-box.exe`
- PowerShell WinForms GUI runner

## Конфиг

Минимальные поля:

```ini
ROOM_ID=<ROOM_ID_из_лога_сервера>
KEY=<32_байта_hex>
SOCKS_HOST=127.0.0.1
SOCKS_PORT=8808
DNS=1.1.1.1:53
DIRECT_PROCESSES=olcrtc.exe,sing-box.exe
DIRECT_DOMAIN_SUFFIXES=wb.ru
DIRECT_IPS=<VPS_IP>/32,<WB_TURN_IP>/32,<WB_API_IP>/32,<WB_LIVEKIT_IP>/32
PRIVATE_DIRECT=true
```

Важно: `olcrtc`, `sing-box`, WB-домены/IP и VPS IP должны идти direct, иначе можно завернуть control-plane обратно в туннель и сломать подключение.

## Логи Windows GUI

Все рядом с GUI `.exe`:

- `olcrtc.log`
- `olcrtc.err.log`
- `sing-box.log`
- `sing-box.err.log`
- `sing-box.out.log`
- `gui.log`

Если SOCKS работает, но системный IP не меняется - смотреть `sing-box.err.log`.

## Требования к сети

Провайдер должен пропускать WB Stream (`wb.ru` домены). Большинство российских провайдеров это делают.

## Лицензия

Как оригинальный olcRTC.
