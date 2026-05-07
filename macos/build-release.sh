#!/usr/bin/env bash
# Build olcrtc-easy macOS release package
# Usage: ./macos/build-release.sh [version] [arm64|amd64]
# Example: ./macos/build-release.sh v0.16 arm64

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-dev}"
ARCH="${2:-arm64}"
case "$ARCH" in
  arm64|amd64) ;;
  *) echo "ERROR: arch must be arm64 or amd64"; exit 1 ;;
esac

BUILDDIR="build"
PACKDIR="macos"
OUTDIR="release"
STAGE="${OUTDIR}/stage-macos-${ARCH}"
NAME="olcrtc-easy-${VERSION}-macos-${ARCH}"
SINGBOX_VERSION="1.13.11"
SINGBOX_PLATFORM="darwin-${ARCH}"
SINGBOX_URL="https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-${SINGBOX_PLATFORM}.tar.gz"

echo "=== olcrtc-easy macOS release builder ==="

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

echo "[2/4] Building macOS client (${ARCH})..."
GOOS=darwin GOARCH="$ARCH" go build -trimpath -ldflags='-s -w' \
  -o "${BUILDDIR}/olcrtc-darwin-${ARCH}" ./cmd/olcrtc
echo "  -> ${BUILDDIR}/olcrtc-darwin-${ARCH} ($(du -h "${BUILDDIR}/olcrtc-darwin-${ARCH}" | cut -f1))"

if [ ! -f "${BUILDDIR}/sing-box-darwin-${ARCH}" ]; then
  echo "[3/4] Downloading sing-box v${SINGBOX_VERSION} (${ARCH})..."
  tmpdir="$(mktemp -d /tmp/sing-box-macos-XXXXXX)"
  trap 'rm -rf "$tmpdir"' EXIT
  curl -fsSL "$SINGBOX_URL" -o "$tmpdir/sing-box.tar.gz"
  tar -xzf "$tmpdir/sing-box.tar.gz" -C "$tmpdir"
  found="$(find "$tmpdir" -type f -name sing-box | head -n 1)"
  if [ -z "$found" ]; then
    echo "ERROR: sing-box binary not found in archive"
    exit 1
  fi
  cp "$found" "${BUILDDIR}/sing-box-darwin-${ARCH}"
  chmod +x "${BUILDDIR}/sing-box-darwin-${ARCH}"
  rm -rf "$tmpdir"
  trap - EXIT
else
  echo "[3/4] Reusing ${BUILDDIR}/sing-box-darwin-${ARCH}"
fi

echo "[4/4] Packaging..."
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp \
  "${PACKDIR}/start-all.command" \
  "${PACKDIR}/start-dashboard.sh" \
  "${PACKDIR}/start-olcrtc-only.sh" \
  "${PACKDIR}/start-tun.sh" \
  "${PACKDIR}/stop-all.sh" \
  "${PACKDIR}/test-socks.sh" \
  "${PACKDIR}/profile.sh" \
  "${PACKDIR}/generate-singbox-config.sh" \
  "${PACKDIR}/olcrtc.conf" \
  "${PACKDIR}/BUILD.md" \
  "$STAGE/"
cp "${BUILDDIR}/olcrtc-darwin-${ARCH}" "$STAGE/olcrtc"
cp "${BUILDDIR}/sing-box-darwin-${ARCH}" "$STAGE/sing-box"
chmod +x "$STAGE"/*.sh "$STAGE"/*.command "$STAGE/olcrtc" "$STAGE/sing-box"

ZIP="${OUTDIR}/${NAME}.zip"
rm -f "$ZIP"
python3 - "$ZIP" "$STAGE" <<'PY'
import os, stat, sys, zipfile
zip_path, stage = sys.argv[1:3]
with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for root, _, files in os.walk(stage):
        for filename in files:
            path = os.path.join(root, filename)
            arcname = os.path.relpath(path, stage)
            info = zipfile.ZipInfo.from_file(path, arcname)
            mode = os.stat(path).st_mode
            if mode & stat.S_IXUSR:
                info.external_attr = (0o755 & 0xFFFF) << 16
            else:
                info.external_attr = (0o644 & 0xFFFF) << 16
            with open(path, 'rb') as fh:
                zf.writestr(info, fh.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
PY
rm -rf "$STAGE"

echo "  -> ${ZIP} ($(du -h "$ZIP" | cut -f1))"
echo
echo "=== Done ==="
echo
echo "Upload to GitHub:"
echo "  gh release create ${VERSION} ${ZIP} --title '${VERSION}' --notes 'Release ${VERSION}'"
