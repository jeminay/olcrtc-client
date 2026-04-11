#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$PROJECT_ROOT/build"

mkdir -p "$BUILD_DIR"

echo "=== Building OlcRTC ==="
echo ""

echo "[1/2] Building olcrtc binary..."
cd "$PROJECT_ROOT"
go build -o "$BUILD_DIR/olcrtc" ./cmd/olcrtc/main.go
echo "✓ olcrtc binary built: $BUILD_DIR/olcrtc"

echo ""
echo "[2/2] Building UI binary..."
cd "$PROJECT_ROOT/ui"
go build -o "$BUILD_DIR/olcrtc-ui" .
echo "✓ UI binary built: $BUILD_DIR/olcrtc-ui"

echo ""
echo "=== Build Complete ==="
echo "Binaries ready:"
echo "  - $BUILD_DIR/olcrtc"
echo "  - $BUILD_DIR/olcrtc-ui"
