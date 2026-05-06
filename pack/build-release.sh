#!/bin/bash
# Build olcrtc-easy release package
# Usage: ./pack/build-release.sh [version]
# Example: ./pack/build-release.sh v0.16

set -e
cd "$(dirname "$0")/.."

VERSION="${1:-dev}"
BUILDDIR="build"
PACKDIR="pack"
OUTDIR="release"
NAME="olcrtc-easy-${VERSION}-windows-amd64"

echo "=== olcrtc-easy release builder ==="

# Check Go
if ! command -v go &>/dev/null; then
    if [ -f "/tmp/go125/go/bin/go" ]; then
        export GOROOT=/tmp/go125/go
        export PATH=$GOROOT/bin:$PATH
        export GOTOOLCHAIN=local
    else
        echo "ERROR: Go not found"
        exit 1
    fi
fi

echo "Go: $(go version)"

# Build Linux server
echo ""
echo "[1/3] Building Linux server..."
GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' \
    -o "${BUILDDIR}/olcrtc-server" ./cmd/olcrtc
echo "  → ${BUILDDIR}/olcrtc-server ($(du -h ${BUILDDIR}/olcrtc-server | cut -f1))"

# Build Windows client
echo "[2/3] Building Windows client..."
GOOS=windows GOARCH=amd64 go build -trimpath -ldflags='-s -w' \
    -o "${BUILDDIR}/olcrtc.exe" ./cmd/olcrtc
echo "  → ${BUILDDIR}/olcrtc.exe ($(du -h ${BUILDDIR}/olcrtc.exe | cut -f1))"

# Package
echo "[3/3] Packaging..."
rm -rf "${OUTDIR}"
mkdir -p "${OUTDIR}"

ZIP="${OUTDIR}/${NAME}.zip"
pushd "${PACKDIR}" >/dev/null
zip -9 -r "../${ZIP}" \
    start-all.bat \
    start-dashboard.ps1 \
    start-olcrtc-only.bat \
    start-tun.bat \
    stop-all.bat \
    test-socks.bat \
    profile.bat \
    generate-singbox-config.ps1 \
    olcrtc.conf \
    -x "*.bak" "*.log" "*.exe" "data/*"
popd >/dev/null

# Add binaries to zip
pushd "${BUILDDIR}" >/dev/null
zip -9 -u "../${ZIP}" olcrtc.exe
popd >/dev/null

# Download sing-box if not present
SINGBOX_VERSION="1.13.11"
SINGBOX_URL="https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-windows-amd64.zip"

if [ ! -f "${BUILDDIR}/sing-box.exe" ]; then
    echo ""
    echo "Downloading sing-box v${SINGBOX_VERSION}..."
    TMPZIP=$(mktemp /tmp/sing-box-XXXXXX.zip)
    curl -sL "$SINGBOX_URL" -o "$TMPZIP"
    unzip -jo "$TMPZIP" "sing-box-${SINGBOX_VERSION}-windows-amd64/sing-box.exe" -d "${BUILDDIR}/"
    rm -f "$TMPZIP"
    echo "  → ${BUILDDIR}/sing-box.exe"
fi

# Add sing-box to zip
pushd "${BUILDDIR}" >/dev/null
zip -9 -u "../${ZIP}" sing-box.exe
popd >/dev/null

echo "  → ${ZIP} ($(du -h ${ZIP} | cut -f1))"
echo ""
echo "=== Done ==="
echo ""
echo "Upload to GitHub:"
echo "  gh release create ${VERSION} ${ZIP} --title '${VERSION}' --notes 'Release ${VERSION}'"
echo "  Or use GitHub web UI: https://github.com/jeminay/olcrtc-easy/releases/new"
