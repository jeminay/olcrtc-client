# Сборка и релиз

## Зависимости

- Go 1.25+ (с `GOTOOLCHAIN=local` если не системный)
- zip
- sing-box.exe (скачать отдельно)

## Сборка бинарников

```bash
# Linux сервер
GOTOOLCHAIN=local GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o build/olcrtc-server ./cmd/olcrtc

# Windows клиент
GOTOOLCHAIN=local GOOS=windows GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o build/olcrtc.exe ./cmd/olcrtc
```

## Упаковка релиза

Скрипт собирает бинарники и пакует zip:

```bash
./pack/build-release.sh v0.16
```

Результат: `release/olcrtc-easy-v0.16-windows-amd64.zip`

### sing-box

sing-box не входит в репозиторий (лицензия). Скачай вручную:

1. https://github.com/SagerNet/sing-box/releases — скачать `sing-box-<version>-windows-amd64.zip`
2. Распаковать `sing-box.exe` рядом
3. Добавить в релиз:

```bash
zip -u release/olcrtc-easy-v0.16-windows-amd64.zip sing-box.exe
```

## Публикация на GitHub

```bash
# Через gh CLI
gh release create v0.16 release/olcrtc-easy-v0.16-windows-amd64.zip \
  --title "v0.16" \
  --notes "Описание изменений"

# Или через веб: https://github.com/jeminay/olcrtc-easy/releases/new
```

## Содержимое pack/

| Файл | Описание |
|------|----------|
| `start-all.bat` | Точка входа — запускает PowerShell дашборд |
| `start-dashboard.ps1` | Дашборд: старт olcrtc + sing-box + живые метрики |
| `start-olcrtc-only.bat` | Только SOCKS5 прокси (без TUN) |
| `start-tun.bat` | Только TUN (olcrtc уже запущен) |
| `stop-all.bat` | Остановка всех процессов |
| `test-socks.bat` | Проверка SOCKS5 |
| `profile.bat` | Замер скорости скачивания |
| `generate-singbox-config.ps1` | Генерация sing-box-config.json из olcrtc.conf |
| `olcrtc.conf` | Шаблон конфига |
| `build-release.sh` | Скрипт сборки релиза |
