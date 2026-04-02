#!/bin/bash
set -euo pipefail

VERSION="1.0.0"
APP_NAME="Sony Photos Sync"
RCLONE_VERSION="1.68.2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$SCRIPT_DIR"

echo "============================================"
echo "  Sony Photos Sync — Build v${VERSION}"
echo "============================================"
echo ""

# --- Step 1: Python dependencies ---
echo "[1/6] Installing Python dependencies..."
pip3 install --quiet pyftpdlib watchdog pyyaml rumps py2app

# --- Step 2: Download rclone binaries ---
echo "[2/6] Downloading rclone binaries..."
mkdir -p resources/rclone_arm64 resources/rclone_x86_64

download_rclone() {
    local ARCH="$1"
    local RCLONE_ARCH="$2"
    local DEST="resources/rclone_${ARCH}/rclone"

    if [ -f "$DEST" ]; then
        echo "  rclone_${ARCH}: already exists, skipping"
        return
    fi

    local URL="https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-${RCLONE_ARCH}.zip"
    local TMP_ZIP="/tmp/rclone_${ARCH}_$$.zip"
    local TMP_DIR="/tmp/rclone_extract_${ARCH}_$$"

    echo "  Downloading rclone ${ARCH}..."
    curl -L -o "$TMP_ZIP" "$URL"
    mkdir -p "$TMP_DIR"
    unzip -o -q "$TMP_ZIP" -d "$TMP_DIR"
    cp "$TMP_DIR"/rclone-*/rclone "$DEST"
    chmod +x "$DEST"
    rm -rf "$TMP_ZIP" "$TMP_DIR"
    echo "  rclone_${ARCH}: OK"
}

download_rclone "arm64" "osx-arm64"
download_rclone "x86_64" "osx-amd64"

# --- Step 3: Create icon if missing ---
echo "[3/6] Checking icon assets..."
if [ ! -f "resources/icon.png" ]; then
    echo "  WARNING: resources/icon.png not found."
    echo "  Creating a placeholder icon..."
    # Create a simple 18x18 camera icon using Python
    python3 -c "
import struct, zlib

def create_png(w, h, pixels):
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    raw = b''
    for y in range(h):
        raw += b'\x00'
        for x in range(w):
            raw += bytes(pixels[y][x])
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')

W, H = 18, 18
px = [[(0,0,0,0)]*W for _ in range(H)]
# Simple camera shape
for y in range(5, 15):
    for x in range(2, 16):
        px[y][x] = (0, 0, 0, 200)
for x in range(6, 12):
    px[3][x] = (0, 0, 0, 200)
    px[4][x] = (0, 0, 0, 200)
# Lens circle
for y in range(7, 13):
    for x in range(6, 12):
        dx, dy = x - 9, y - 10
        if dx*dx + dy*dy <= 6:
            px[y][x] = (80, 80, 80, 200)

with open('resources/icon.png', 'wb') as f:
    f.write(create_png(W, H, px))
print('  Placeholder icon created')
"
fi

if [ ! -f "resources/app_icon.icns" ]; then
    echo "  No app_icon.icns found — will use default py2app icon."
fi

# --- Step 4: Build .app with py2app ---
echo "[4/6] Building .app bundle..."
rm -rf build dist
python3 setup_py2app.py py2app 2>&1 | tail -5

# --- Step 5: Copy rclone into .app ---
echo "[5/6] Bundling rclone binaries into .app..."
RESOURCES_DIR="dist/${APP_NAME}.app/Contents/Resources"
cp -r resources/rclone_arm64 "$RESOURCES_DIR/"
cp -r resources/rclone_x86_64 "$RESOURCES_DIR/"
chmod +x "$RESOURCES_DIR/rclone_arm64/rclone"
chmod +x "$RESOURCES_DIR/rclone_x86_64/rclone"
echo "  Bundled rclone for arm64 + x86_64"

# --- Step 6: Create DMG ---
echo "[6/6] Creating DMG..."
DMG_NAME="${APP_NAME// /-}-${VERSION}.dmg"

if command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "${APP_NAME}" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "${APP_NAME}.app" 150 190 \
        --app-drop-link 450 190 \
        --no-internet-enable \
        "dist/${DMG_NAME}" \
        "dist/${APP_NAME}.app" \
        || true  # create-dmg returns non-zero even on success sometimes
else
    echo "  create-dmg not found, creating simple DMG with hdiutil..."
    hdiutil create -volname "${APP_NAME}" \
        -srcfolder "dist/${APP_NAME}.app" \
        -ov -format UDZO \
        "dist/${DMG_NAME}"
fi

echo ""
echo "============================================"
echo "  Build Complete!"
echo "============================================"
echo ""
echo "  App: dist/${APP_NAME}.app"
echo "  DMG: dist/${DMG_NAME}"
echo ""
echo "  To test: open \"dist/${APP_NAME}.app\""
echo ""
echo "  Note: On first launch, macOS may block the app."
echo "  Right-click → Open → Open to bypass Gatekeeper."
echo ""
