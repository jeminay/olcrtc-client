# macOS сборка и релиз

## Зависимости

- Go 1.25+ (`GOTOOLCHAIN=local`, если используется локальный Go)
- curl
- tar
- python3

## Сборка пакета

```bash
# Apple Silicon
./macos/build-release.sh v0.16 arm64

# Intel Mac
./macos/build-release.sh v0.16 amd64
```

Результат:

```text
release/olcrtc-client-v0.16-macos-arm64.zip
release/olcrtc-client-v0.16-macos-amd64.zip
```

Скрипт собирает:

- Linux server binary: `build/olcrtc-server`
- macOS client binary: `build/olcrtc-darwin-<arch>`
- portable zip with `olcrtc`, `sing-box`, config and scripts

## Запуск на macOS

1. Распаковать zip
2. Заполнить `olcrtc.conf`: `ROOM_ID`, `KEY`, `DIRECT_IPS`
3. Запустить:

```bash
./start-all.command
```

`start-all.command` запросит `sudo`, потому что `sing-box` TUN на macOS требует прав администратора.

## Содержимое macos/

| Файл | Описание |
|------|----------|
| `start-all.command` | Точка входа для Finder/Terminal |
| `start-dashboard.sh` | Старт olcrtc + sing-box + live dashboard |
| `start-olcrtc-only.sh` | Только SOCKS5 прокси без TUN |
| `start-tun.sh` | Только sing-box TUN, если olcrtc уже запущен |
| `stop-all.sh` | Остановка `olcrtc` и `sing-box` |
| `test-socks.sh` | Проверка SOCKS5 |
| `profile.sh` | Замер скорости |
| `generate-singbox-config.sh` | Генерация `sing-box-config.json` из `olcrtc.conf` |
| `olcrtc.conf` | Шаблон конфига |
| `build-release.sh` | Скрипт сборки релиза |
