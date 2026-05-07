#!/usr/bin/env bash
set -euo pipefail
pkill -x olcrtc 2>/dev/null || true
pkill -x sing-box 2>/dev/null || true
rm -f "$(dirname "$0")/olcrtc.pid"
echo "All processes stopped."
