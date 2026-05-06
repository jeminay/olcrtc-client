# olcrtc-easy

Форк [olcRTC](https://github.com/openlibrecommunity/olcrtc) с фокусом на простоту развёртывания и удобство использования.

## Что это

olcRTC — инструмент для обхода интернет-ограничений через WebRTC DataChannel. Трафик идёт через публичный TURN-сервер (WB Stream), выглядящий как обычный видеозвонок.

```
Windows PC → sing-box TUN → SOCKS5 → olcRTC → WB Stream (WebRTC) → VPS → Интернет
```

## Отличия от оригинала

### Упаковка и запуск
- **Всё в одном zip** — `olcrtc.exe`, `sing-box.exe`, конфиги, скрипты. Не нужен Python, Go, ffmpeg — распаковал и запустил.
- **PowerShell дашборд** (`start-all.bat` → `start-dashboard.ps1`) — живые метрики в консоли: скорость, счётчики запросов, статус процессов, health check. Одно окно, обновляется в реальном времени.
- **Один клик stop** (`stop-all.bat`) — корректно убивает все процессы.
- **Автогенерация sing-box конфига** (`generate-singbox-config.ps1`) — читает `olcrtc.conf`, генерирует `sing-box-config.json`. Никакого хардкода IP.
- **Автоматический hosts** — скрипт резолвит WB-домены через провайдерский DNS и пишет в hosts перед стартом. Работает под TUN.

### Оптимизации
- **METRICS** — сервер каждые 5 сек пишет метрики в лог: rx/tx скорость, WB Stream state, queue size. Дашборд парсит и показывает.
- **Убраны шумные логи** — MUXDEBUG, WBDEBUG на каждый фрейм вырезаны. Только критичные ошибки и метрики.
- **ICETransportPolicyRelay** — принудительный relay через TURN. Без этого DataChannel не открывается с Windows за NAT.

### Конфигурация
- **Единый `olcrtc.conf`** — ROOM_ID, ключ, SOCKS порт, DNS, прямые маршруты. Всё в одном месте.
- **Прямые маршруты** — olcrtc.exe, sing-box.exe, wb.ru, WB Stream IPs, VPS IP, приватные подсети идут в обход туннеля. Генерируется автоматически.

## Быстрый старт

### 1. Сервер (VPS, Linux amd64)

```bash
# Собрать
GOTOOLCHAIN=local GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o olcrtc-server ./cmd/olcrtc

# Запустить
./olcrtc-server -mode srv -carrier wbstream -transport datachannel -id any \
  -key <ВАШ_КЛЮЧ> -link direct -dns 1.1.1.1:53 -data ./data
```

В логе появится:
```
WB Stream room created: <ROOM_ID>
To connect client use: -id <ROOM_ID>
```

### 2. Клиент (Windows)

1. Скачать zip с релизов, распаковать
2. Открыть `olcrtc.conf`, вставить ROOM_ID и KEY
3. Запустить `start-all.bat` (запросит admin для TUN)
4. Готово — весь трафик идёт через VPS

### Минимальный конфиг `olcrtc.conf`:

```ini
ROOM_ID=<ROOM_ID_из_лога_сервера>
KEY=<32_байта_hex>
SOCKS_HOST=127.0.0.1
SOCKS_PORT=8808
DNS=1.1.1.1:53
DIRECT_PROCESSES=olcrtc.exe,sing-box.exe
DIRECT_DOMAIN_SUFFIXES=wb.ru
DIRECT_IPS=<VPS_IP>/32
PRIVATE_DIRECT=true
```

## Ограничения

- **~115 KB/s** — потолок одного reliable DataChannel через TURN. Пиков до 200 KB/s.
- **ROOM_ID** — при каждом рестарте сервера WB Stream генерит новый. Нужно вручную обновить в конфиге.
- **Только amd64** — Linux сервер, Windows клиент.

## Требования к сети

Провайдер должен пропускать:
- DNS к любому резолверу (провайдерский подойдёт)
- TCP до `wbstream01-el.wb.ru:7880` (LiveKit WebSocket)
- TCP до `stream.wb.ru:443` (API)
- UDP до `wb-stream-turn-1.wb.ru:3478` (TURN/STUN)

Все домены WB Stream доступны из большинства российских сетей.

## Лицензия

Как оригинальный olcRTC.
