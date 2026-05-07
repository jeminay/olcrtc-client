#!/usr/bin/env bash
# Build olcRTC Client Windows GUI MVP
# Usage: ./windows-gui/build-release.sh [version]

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-v0.1-mvp}"
BUILDDIR="build"
GUIDIR="windows-gui"
ASSETDIR="${GUIDIR}/assets"
OUTDIR="release"
NAME="olcrtc-client-gui-${VERSION}-windows-amd64"
SINGBOX_VERSION="1.13.11"
SINGBOX_URL="https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-windows-amd64.zip"

echo "=== olcRTC Client Windows GUI builder ==="

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
mkdir -p "$BUILDDIR" "$ASSETDIR" "$OUTDIR"

echo "[1/4] Building olcrtc.exe..."
GOOS=windows GOARCH=amd64 go build -trimpath -ldflags='-s -w' \
  -o "${BUILDDIR}/olcrtc.exe" ./cmd/olcrtc
cp "${BUILDDIR}/olcrtc.exe" "${ASSETDIR}/olcrtc.exe"
echo "  -> ${ASSETDIR}/olcrtc.exe ($(du -h "${ASSETDIR}/olcrtc.exe" | cut -f1))"

echo "[2/4] Preparing sing-box.exe..."
if [ ! -f "${BUILDDIR}/sing-box.exe" ]; then
  tmpzip="$(mktemp /tmp/sing-box-windows-XXXXXX.zip)"
  curl -fsSL "$SINGBOX_URL" -o "$tmpzip"
  python3 - "$tmpzip" "${BUILDDIR}/sing-box.exe" <<'PY'
import sys, zipfile
archive, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(archive) as zf:
    name = next((n for n in zf.namelist() if n.endswith('/sing-box.exe') or n == 'sing-box.exe'), None)
    if not name:
        raise SystemExit('sing-box.exe not found')
    with zf.open(name) as src, open(out, 'wb') as dst:
        dst.write(src.read())
PY
  rm -f "$tmpzip"
fi
cp "${BUILDDIR}/sing-box.exe" "${ASSETDIR}/sing-box.exe"
echo "  -> ${ASSETDIR}/sing-box.exe ($(du -h "${ASSETDIR}/sing-box.exe" | cut -f1))"

echo "[3/4] Building GUI single exe..."
(
  cd "$GUIDIR"
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags='-H windowsgui -s -w' \
    -o "../${OUTDIR}/${NAME}.exe" .
)
echo "  -> ${OUTDIR}/${NAME}.exe ($(du -h "${OUTDIR}/${NAME}.exe" | cut -f1))"

echo "[4/4] Packaging zip..."
python3 - "${OUTDIR}/${NAME}.zip" "${OUTDIR}/${NAME}.exe" "${GUIDIR}/README.md" <<'PY'
import os, sys, zipfile
zip_path, exe_path, readme_path = sys.argv[1:4]
with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    zf.write(exe_path, os.path.basename(exe_path))
    zf.write(readme_path, 'README.md')
PY

echo "  -> ${OUTDIR}/${NAME}.zip ($(du -h "${OUTDIR}/${NAME}.zip" | cut -f1))"
echo "=== Done ==="
