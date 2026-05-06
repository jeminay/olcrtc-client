# olcrtc-easy

**Один клик — и весь трафик Windows идёт через прокси-туннель.**

Форк [olcRTC](https://github.com/openlibrecommunity/olcrtc) — обёртка для Windows, которая поднимает SOCKS5 прокси через WebRTC DataChannel, заворачивает в него весь трафик системы через TUN, и показывает живые метрики в консоли.

## Как это работает

```
Браузер / любое приложение
        ↓
   sing-box TUN (перехватывает весь трафик)
        ↓
   SOCKS5 127.0.0.1:8808
        ↓
   olcRTC клиент (шифрует, упаковывает в WebRTC)
        ↓
   WB Stream — публичный TURN-сервер Wildberries
   (выглядит как обычный видеозвонок)
        ↓
   olcRTC сервер на вашем VPS
        ↓
   Интернет
```

Весь трафик идёт через WebRTC DataChannel через TURN-сервер WB Stream. Для провайдера это выглядит как подключение к видеосервису Wildberries — обычный HTTPS/WebRTC трафик.

Сейчас поддерживается только один канал — **WB Stream** (Wildberries).

## Отличия от оригинального olcRTC

Оригинальный olcRTC — это консольная утилита без упаковки. Этот форк добавляет:

- **Всё в одном zip** — `olcrtc.exe`, `sing-box.exe`, конфиги, скрипты. Распаковал и запустил — не нужен Python, Go, ffmpeg.
- **PowerShell дашборд** — живые метрики в консоли: скорость rx/tx, счётчики запросов (успешных/ошибок), статус процессов, health check каждые 30 сек, uptime.
- **Один клик старт** (`start-all.bat`) — запрашивает admin, резолвит WB-домены, стартует olcrtc + sing-box, показывает дашборд. Одно окно.
- **Один клик стоп** (`stop-all.bat`) — убивает оба процесса.
- **Автогенерация конфига** — `generate-singbox-config.ps1` читает `olcrtc.conf` и генерирует `sing-box-config.json`. Без хардкода IP.
- **Автоматический hosts** — скрипт резолвит WB-домены через провайдерский DNS и пишет в hosts перед стартом.
- **Метрики сервера** — каждые 5 сек логирует rx/tx скорость, WB state, queue size.
- **Чистые логи** — убраны шумные MUXDEBUG/WBDEBUG на каждый фрейм.

## Быстрый старт

### 1. Сервер (VPS, Linux amd64)

```bash
# Собрать
GOTOOLCHAIN=local GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o olcrtc-server ./cmd/olcrtc

# Запустить
./olcrtc-server -mode srv -carrier wbstream -transport datachannel -id any \
  -key <ВАШ_КЛЮЧ_32_байта_hex> -link direct -dns 1.1.1.1:53 -data ./data
```

В логе появится:
```
WB Stream room created: <ROOM_ID>
To connect client use: -id <ROOM_ID>
```

### 2. Клиент (Windows)

1. Скачать zip из [релизов](https://github.com/jeminay/olcrtc-easy/releases), распаковать
2. Открыть `olcrtc.conf`, вставить `ROOM_ID` и `KEY`
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

## Требования к сети

Провайдер должен пропускать WB Stream (wb.ru домены). Большинство российских провайдеров это делают.

## Лицензия

Как оригинальный olcRTC.
