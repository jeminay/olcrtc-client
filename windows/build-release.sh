#!/usr/bin/env bash
# Build olcrtc-easy Windows release package
# Usage: ./windows/build-release.sh [version]
# Example: ./windows/build-release.sh v0.16

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-dev}"
BUILDDIR="build"
PACKDIR="windows"
OUTDIR="release"
NAME="olcrtc-easy-${VERSION}-windows-amd64"
SINGBOX_VERSION="1.13.11"
SINGBOX_URL="https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-windows-amd64.zip"

echo "=== olcrtc-easy Windows release builder ==="

# Prefer the local Go 1.25 toolchain when available.
if [ -f "/tmp/go125/go/bin/go" ]; then
    export GOROOT=/tmp/go125/go
    export PATH=$GOROOT/bin:$PATH
    export GOTOOLCHAIN=local
elif ! command -v go >/dev/null 2>&1; then
    echo "ERROR: Go not found"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found"
    exit 1
fi

echo "Go: $(go version)"
mkdir -p "$BUILDDIR" "$OUTDIR"

echo
echo "[1/4] Building Linux server..."
GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' \
    -o "${BUILDDIR}/olcrtc-server" ./cmd/olcrtc
echo "  -> ${BUILDDIR}/olcrtc-server ($(du -h "${BUILDDIR}/olcrtc-server" | cut -f1))"

echo "[2/4] Building Windows client..."
GOOS=windows GOARCH=amd64 go build -trimpath -ldflags='-s -w' \
    -o "${BUILDDIR}/olcrtc.exe" ./cmd/olcrtc
echo "  -> ${BUILDDIR}/olcrtc.exe ($(du -h "${BUILDDIR}/olcrtc.exe" | cut -f1))"

echo "[3/4] Preparing sing-box..."
if [ ! -f "${BUILDDIR}/sing-box.exe" ]; then
    echo "  Downloading sing-box v${SINGBOX_VERSION}..."
    tmpzip="$(mktemp /tmp/sing-box-windows-XXXXXX.zip)"
    curl -fsSL "$SINGBOX_URL" -o "$tmpzip"
    python3 - "$tmpzip" "${BUILDDIR}/sing-box.exe" <<'PY'
import sys, zipfile
archive, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(archive) as zf:
    name = next((n for n in zf.namelist() if n.endswith('/sing-box.exe') or n == 'sing-box.exe'), None)
    if not name:
        raise SystemExit('sing-box.exe not found in archive')
    with zf.open(name) as src, open(out, 'wb') as dst:
        dst.write(src.read())
PY
    rm -f "$tmpzip"
fi
echo "  -> ${BUILDDIR}/sing-box.exe ($(du -h "${BUILDDIR}/sing-box.exe" | cut -f1))"

echo "[4/4] Packaging..."
ZIP="${OUTDIR}/${NAME}.zip"
rm -f "$ZIP"
python3 - "$ZIP" "$PACKDIR" "$BUILDDIR" <<'PY'
import os, sys, zipfile
zip_path, packdir, builddir = sys.argv[1:4]
files = [
    'start-all.bat',
    'start-dashboard.ps1',
    'start-olcrtc-only.bat',
    'start-tun.bat',
    'stop-all.bat',
    'test-socks.bat',
    'profile.bat',
    'generate-singbox-config.ps1',
    'olcrtc.conf',
    'BUILD.md',
]
with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for name in files:
        zf.write(os.path.join(packdir, name), name)
    zf.write(os.path.join(builddir, 'olcrtc.exe'), 'olcrtc.exe')
    zf.write(os.path.join(builddir, 'sing-box.exe'), 'sing-box.exe')
PY

echo "  -> ${ZIP} ($(du -h "$ZIP" | cut -f1))"
echo
echo "=== Done ==="
echo
echo "Upload to GitHub:"
echo "  gh release create ${VERSION} ${ZIP} --title '${VERSION}' --notes 'Release ${VERSION}'"
echo "  Or use GitHub web UI: https://github.com/jeminay/olcrtc-easy/releases/new"
