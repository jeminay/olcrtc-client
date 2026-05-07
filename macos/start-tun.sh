#!/usr/bin/env bash
set -euo pipefail
base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$base"
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Requesting Administrator for TUN..."
  exec sudo -E /bin/bash "$0"
fi

echo "Generating sing-box config from olcrtc.conf..."
/bin/bash "$base/generate-singbox-config.sh"
echo "Starting sing-box TUN..."
echo "Logs: sing-box.log, sing-box-out.log"
"$base/sing-box" run -c "$base/sing-box-config.json" > "$base/sing-box-out.log" 2> "$base/sing-box.log"
