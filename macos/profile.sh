#!/usr/bin/env bash
set -euo pipefail

echo "=== olcRTC profile ==="
echo "Time: $(date)"
echo
echo "1) Public IP through TUN"
curl -4 -L -k -w "\nTOTAL=%{time_total}s DNS=%{time_namelookup}s CONNECT=%{time_connect}s TLS=%{time_appconnect}s STARTTRANSFER=%{time_starttransfer}s SPEED=%{speed_download}B/s\n" -o /dev/null https://icanhazip.com
echo
echo "2) Instagram headers through TUN"
curl -4 -I -L -k -w "\nTOTAL=%{time_total}s DNS=%{time_namelookup}s CONNECT=%{time_connect}s TLS=%{time_appconnect}s STARTTRANSFER=%{time_starttransfer}s SPEED=%{speed_download}B/s\n" https://www.instagram.com
echo
echo "3) Small download 1MB through TUN"
curl -4 -L -k -w "\nTOTAL=%{time_total}s DNS=%{time_namelookup}s CONNECT=%{time_connect}s TLS=%{time_appconnect}s STARTTRANSFER=%{time_starttransfer}s SPEED=%{speed_download}B/s\n" -o /dev/null 'https://speed.cloudflare.com/__down?bytes=1000000'
echo
echo "4) Medium download 10MB through TUN"
curl -4 -L -k -w "\nTOTAL=%{time_total}s DNS=%{time_namelookup}s CONNECT=%{time_connect}s TLS=%{time_appconnect}s STARTTRANSFER=%{time_starttransfer}s SPEED=%{speed_download}B/s\n" -o /dev/null 'https://speed.cloudflare.com/__down?bytes=10000000'
echo
echo "Done. Check olcrtc.log for METRICS lines."
