#!/usr/bin/env bash
set -euo pipefail

base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$base"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Requesting Administrator for macOS TUN..."
  exec sudo -E /bin/bash "$0"
fi

get_conf() {
  local key="$1"
  awk -F= -v key="$key" '
    /^[[:space:]]*($|#)/ { next }
    { k=$1; sub(/^[[:space:]]+/, "", k); sub(/[[:space:]]+$/, "", k); if (k == key) { $1=""; sub(/^=/, ""); sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, ""); print; exit } }
  ' "$base/olcrtc.conf"
}

ROOM_ID="$(get_conf ROOM_ID)"
KEY="$(get_conf KEY)"
SOCKS_HOST="$(get_conf SOCKS_HOST)"; SOCKS_HOST="${SOCKS_HOST:-127.0.0.1}"
SOCKS_PORT="$(get_conf SOCKS_PORT)"; SOCKS_PORT="${SOCKS_PORT:-8808}"
DNS="$(get_conf DNS)"; DNS="${DNS:-1.1.1.1:53}"

if [ -z "$ROOM_ID" ]; then
  echo "ERROR: set ROOM_ID in olcrtc.conf"
  read -r -p "Press Enter to exit..." _ || true
  exit 1
fi
if [ -z "$KEY" ]; then
  echo "ERROR: set KEY in olcrtc.conf"
  read -r -p "Press Enter to exit..." _ || true
  exit 1
fi

resolve_wb_hosts() {
  local hosts=/etc/hosts d ip
  for d in wbstream01-el.wb.ru wbstream01-e1.wb.ru wb-stream-turn-1.wb.ru stream.wb.ru; do
    if grep -q "[[:space:]]$d\$" "$hosts" 2>/dev/null; then
      continue
    fi
    ip="$(dscacheutil -q host -a name "$d" 2>/dev/null | awk '/ip_address:/ {print $2; exit}')"
    if [ -n "$ip" ]; then
      printf '\n%s %s\n' "$ip" "$d" >> "$hosts"
      echo "  Added: $ip $d"
    fi
  done
}

test_socks() {
  curl --socks5-hostname "${SOCKS_HOST}:${SOCKS_PORT}" -4 -s -k --connect-timeout 10 --max-time 15 https://icanhazip.com 2>/dev/null | tr -d '\r' | awk 'NF {print; exit}'
}

cleanup() {
  echo
  echo "Stopping..."
  [ -n "${singbox_pid:-}" ] && kill "$singbox_pid" 2>/dev/null || true
  [ -n "${olcrtc_pid:-}" ] && kill "$olcrtc_pid" 2>/dev/null || true
  pkill -x sing-box 2>/dev/null || true
  pkill -x olcrtc 2>/dev/null || true
  echo "All stopped."
}
trap cleanup EXIT INT TERM

clear || true
echo "=== olcRTC Dashboard macOS ==="
echo "Room: $ROOM_ID"
echo

echo "[1/4] Resolving WB Stream..."
resolve_wb_hosts || true
echo

echo "[2/4] Starting olcRTC..."
pkill -x olcrtc 2>/dev/null || true
pkill -x sing-box 2>/dev/null || true
sleep 0.5
rm -f olcrtc.log sing-box.log sing-box-out.log
"$base/olcrtc" -mode cnc -carrier wbstream -transport datachannel -id "$ROOM_ID" -key "$KEY" -link direct -dns "$DNS" -data "$base/data" -socks-host "$SOCKS_HOST" -socks-port "$SOCKS_PORT" >> "$base/olcrtc.log" 2>&1 &
olcrtc_pid=$!
echo "  PID: $olcrtc_pid, waiting..."
sleep 8

echo "[3/4] Testing SOCKS..."
ip="$(test_socks || true)"
if [ -n "$ip" ]; then
  echo "  SOCKS OK - IP: $ip"
else
  echo "  SOCKS FAILED"
fi
echo

echo "[4/4] Starting TUN..."
/bin/bash "$base/generate-singbox-config.sh"
"$base/sing-box" run -c "$base/sing-box-config.json" > "$base/sing-box-out.log" 2> "$base/sing-box.log" &
singbox_pid=$!
echo "  sing-box PID: $singbox_pid"
sleep 3

tun_ip="$(test_socks || true)"
if [ -n "$tun_ip" ]; then
  echo "  TUN OK - IP: $tun_ip"
else
  echo "  TUN pending..."
fi
echo

echo "=== Live Dashboard (Ctrl+C to exit) ==="
start_ts=$(date +%s)
last_health=0
healthy="OK"
reqs=0
oks=0
fails=0
while true; do
  now=$(date +%s)
  if [ $((now - last_health)) -ge 30 ]; then
    last_health=$now
    if [ -n "$(test_socks || true)" ] && kill -0 "$olcrtc_pid" 2>/dev/null && kill -0 "$singbox_pid" 2>/dev/null; then
      healthy="OK"
    else
      healthy="FAIL"
    fi
  fi

  reqs=$(grep -c 'SOCKS request target\| connect ' olcrtc.log 2>/dev/null || echo 0)
  oks=$(grep -c ' connected .* in ' olcrtc.log 2>/dev/null || echo 0)
  fails=$(grep -c 'connect failed' olcrtc.log 2>/dev/null || echo 0)
  metrics="$(grep 'METRICS mux' olcrtc.log 2>/dev/null | tail -n 1 || true)"
  wb="$(grep 'METRICS wb' olcrtc.log 2>/dev/null | tail -n 1 | sed -n 's/.*state=\([^ ]*\).*/\1/p' || true)"; wb="${wb:-connecting}"
  rx="$(printf '%s' "$metrics" | sed -n 's/.*rx=\([0-9.]*KB\/s\).*/\1/p')"; rx="${rx:-0.0KB/s}"
  tx="$(printf '%s' "$metrics" | sed -n 's/.*tx=\([0-9.]*KB\/s\).*/\1/p')"; tx="${tx:-0.0KB/s}"
  up=$((now - start_ts))
  printf '\r[%s] olcrtc:%s sing-box:%s health:%s WB:%s up:%02d:%02d:%02d | rx:%s tx:%s reqs:%s ok:%s fail:%s   ' \
    "$(date +%H:%M:%S)" \
    "$(kill -0 "$olcrtc_pid" 2>/dev/null && echo RUN || echo DEAD)" \
    "$(kill -0 "$singbox_pid" 2>/dev/null && echo RUN || echo DEAD)" \
    "$healthy" "$wb" $((up/3600)) $(((up/60)%60)) $((up%60)) "$rx" "$tx" "$reqs" "$oks" "$fails"
  sleep 2
done
