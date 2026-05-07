#!/usr/bin/env bash
set -euo pipefail

base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
conf_path="$base/olcrtc.conf"
out_path="$base/sing-box-config.json"

get_conf() {
  local key="$1"
  awk -F= -v key="$key" '
    /^[[:space:]]*($|#)/ { next }
    {
      k=$1; sub(/^[[:space:]]+/, "", k); sub(/[[:space:]]+$/, "", k)
      if (k == key) {
        $1=""; sub(/^=/, "")
        sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, "")
        print; exit
      }
    }
  ' "$conf_path"
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

csv_json_array() {
  local csv="${1:-}" item first=1
  printf '['
  IFS=',' read -ra items <<< "$csv"
  for item in "${items[@]}"; do
    item="$(printf '%s' "$item" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -z "$item" ] && continue
    [ $first -eq 0 ] && printf ','
    printf '"%s"' "$(json_escape "$item")"
    first=0
  done
  printf ']'
}

SOCKS_HOST="$(get_conf SOCKS_HOST)"; SOCKS_HOST="${SOCKS_HOST:-127.0.0.1}"
SOCKS_PORT="$(get_conf SOCKS_PORT)"; SOCKS_PORT="${SOCKS_PORT:-8808}"
DIRECT_PROCESSES="$(get_conf DIRECT_PROCESSES)"; DIRECT_PROCESSES="${DIRECT_PROCESSES:-olcrtc,sing-box}"
DIRECT_DOMAIN_SUFFIXES="$(get_conf DIRECT_DOMAIN_SUFFIXES)"
DIRECT_IPS="$(get_conf DIRECT_IPS)"
PRIVATE_DIRECT="$(get_conf PRIVATE_DIRECT)"; PRIVATE_DIRECT="${PRIVATE_DIRECT:-true}"

processes_json="$(csv_json_array "$DIRECT_PROCESSES")"
domains_json="$(csv_json_array "$DIRECT_DOMAIN_SUFFIXES")"
ips_json="$(csv_json_array "$DIRECT_IPS")"

rules=$'[\n    {"ip_cidr":["172.19.0.2/32"],"port":53,"action":"hijack-dns"},\n    {"protocol":"dns","action":"hijack-dns"}'

if [ "$processes_json" != "[]" ]; then
  rules+=$',\n    {"process_name":'"$processes_json"$',"outbound":"direct"}'
fi
if [ "$domains_json" != "[]" ]; then
  rules+=$',\n    {"domain_suffix":'"$domains_json"$',"outbound":"direct"}'
fi
if [ "$ips_json" != "[]" ]; then
  rules+=$',\n    {"ip_cidr":'"$ips_json"$',"outbound":"direct"}'
fi
if [ "$(printf '%s' "$PRIVATE_DIRECT" | tr '[:upper:]' '[:lower:]')" != "false" ]; then
  rules+=$',\n    {"ip_is_private":true,"outbound":"direct"}'
fi
rules+=$'\n  ]'

cat > "$out_path" <<JSON
{
  "log": { "level": "debug", "timestamp": true, "output": "sing-box.log" },
  "dns": {
    "servers": [
      { "tag": "remote", "address": "tcp://1.1.1.1", "detour": "proxy" },
      { "tag": "local", "address": "local" }
    ],
    "rules": [
      { "domain_suffix": $domains_json, "server": "local" },
      { "domain_suffix": ["local", "lan"], "server": "local" }
    ],
    "final": "remote"
  },
  "inbounds": [{
    "type": "tun",
    "tag": "tun-in",
    "address": ["172.19.0.1/30"],
    "auto_route": true,
    "strict_route": true,
    "stack": "mixed"
  }],
  "outbounds": [
    { "type": "socks", "tag": "proxy", "server": "$(json_escape "$SOCKS_HOST")", "server_port": $SOCKS_PORT, "version": "5" },
    { "type": "direct", "tag": "direct" },
    { "type": "block", "tag": "block" }
  ],
  "route": {
    "auto_detect_interface": true,
    "rules": $rules,
    "final": "proxy",
    "default_domain_resolver": "local"
  }
}
JSON

echo "Generated sing-box-config.json"
