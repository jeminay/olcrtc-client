#!/usr/bin/env bash
set -euo pipefail
base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$base"
get_conf() {
  local key="$1"
  awk -F= -v key="$key" '/^[[:space:]]*($|#)/ { next } { k=$1; sub(/^[[:space:]]+/, "", k); sub(/[[:space:]]+$/, "", k); if (k == key) { $1=""; sub(/^=/, ""); sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, ""); print; exit } }' "$base/olcrtc.conf"
}
ROOM_ID="$(get_conf ROOM_ID)"
KEY="$(get_conf KEY)"
SOCKS_HOST="$(get_conf SOCKS_HOST)"; SOCKS_HOST="${SOCKS_HOST:-127.0.0.1}"
SOCKS_PORT="$(get_conf SOCKS_PORT)"; SOCKS_PORT="${SOCKS_PORT:-8808}"
DNS="$(get_conf DNS)"; DNS="${DNS:-1.1.1.1:53}"
if [ -z "$ROOM_ID" ]; then echo "ERROR: set ROOM_ID in olcrtc.conf"; exit 1; fi
if [ -z "$KEY" ]; then echo "ERROR: set KEY in olcrtc.conf"; exit 1; fi
pkill -x olcrtc 2>/dev/null || true
rm -f olcrtc.log
echo "Starting olcRTC SOCKS on ${SOCKS_HOST}:${SOCKS_PORT} ..."
echo "Logs: olcrtc.log"
"$base/olcrtc" -mode cnc -carrier wbstream -transport datachannel -id "$ROOM_ID" -key "$KEY" -link direct -dns "$DNS" -data "$base/data" -socks-host "$SOCKS_HOST" -socks-port "$SOCKS_PORT" >> "$base/olcrtc.log" 2>&1 &
echo $! > "$base/olcrtc.pid"
sleep 5
echo "Test:"
echo "curl --socks5-hostname ${SOCKS_HOST}:${SOCKS_PORT} https://icanhazip.com"
