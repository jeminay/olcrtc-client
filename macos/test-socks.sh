#!/usr/bin/env bash
set -euo pipefail
base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
get_conf() {
  local key="$1"
  awk -F= -v key="$key" '/^[[:space:]]*($|#)/ { next } { k=$1; sub(/^[[:space:]]+/, "", k); sub(/[[:space:]]+$/, "", k); if (k == key) { $1=""; sub(/^=/, ""); sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, ""); print; exit } }' "$base/olcrtc.conf"
}
SOCKS_HOST="$(get_conf SOCKS_HOST)"; SOCKS_HOST="${SOCKS_HOST:-127.0.0.1}"
SOCKS_PORT="$(get_conf SOCKS_PORT)"; SOCKS_PORT="${SOCKS_PORT:-8808}"
curl --socks5-hostname "${SOCKS_HOST}:${SOCKS_PORT}" https://icanhazip.com
