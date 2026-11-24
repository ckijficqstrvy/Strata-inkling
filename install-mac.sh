#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  Strata Inkling-2-Mini One-Click Installer for macOS Apple Silicon (M1-M5)
#  Auto-selects SQ-15 (14.35 GB) for 24 GB Macs like MacBook Pro M5 24 GB,
#  SQ-20 (19.35 GB) for >=32 GB Macs, and SQ-10 (10.47 GB) for 16 GB Macs.
# ==============================================================================

ROOT="$(cd "$(dirname "$0")" && pwd)"
STRATA_RELEASE="v0.2.20"
ENGINE_VERSION="0.1.66"
MODEL_PACK="auto"
BACKEND_PORT=8081
GATEWAY_PORT=11435
WITH_VISION=0
LAN_ACCESS=0
SKIP_MODEL=0
LAUNCH_GUI=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pack) MODEL_PACK="$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')"; shift 2 ;;
    --port) BACKEND_PORT="$2"; shift 2 ;;
    --gateway-port) GATEWAY_PORT="$2"; shift 2 ;;
    --vision) WITH_VISION=1; shift ;;
    --lan) LAN_ACCESS=1; shift ;;
    --skip-model) SKIP_MODEL=1; shift ;;
    --no-launch) LAUNCH_GUI=0; shift ;;
    -h|--help)
      echo "Usage: ./install-mac.sh [--pack auto|SQ-10|SQ-15|SQ-20] [--vision] [--lan] [--gateway-port 11435] [--no-launch]"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "============================================================"
echo "  Strata Inkling-2-Mini macOS Apple Silicon 一鍵安裝程式"
echo "  Workspace: $ROOT"
echo "============================================================"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[ERROR] This script is for macOS. On Windows, run Install.cmd instead."
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "[ERROR] Apple Silicon (arm64, M1-M5) is required for Strata Metal."
  exit 1
fi

MACOS_VER="$(sw_vers -productVersion)"
CHIP_NAME="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
MEM_BYTES="$(sysctl -n hw.memsize)"
MEM_GB=$(( (MEM_BYTES + 536870912) / 1073741824 ))

if [[ "$MODEL_PACK" == "auto" || "$MODEL_PACK" == "AUTO" ]]; then
  if (( MEM_GB >= 32 )); then
    MODEL_PACK="SQ-20"
  elif (( MEM_GB >= 22 )); then
    MODEL_PACK="SQ-15"
  else
    MODEL_PACK="SQ-10"
  fi
fi

case "$MODEL_PACK" in
  SQ-20)
    PACK_FILE="Inkling-2-Mini-SQ-20.strata"
    PACK_KEY="inkling-2-mini-sq-20"
    PACK_BYTES="19346685952"
    PACK_SHA="0bbfd52344f0dd4779a7807b5f73554a812f24c035e6877b63cd0d499f7e1b97"
    NEED_GB=22
    ;;
  SQ-15)
    PACK_FILE="Inkling-2-Mini-SQ-15.strata"
    PACK_KEY="inkling-2-mini-sq-15"
    PACK_BYTES="14348316672"
    PACK_SHA="2ea32eed533afba257e2cbee7b073dc2c3da80fe55d1d2d794779b5498e30737"
    NEED_GB=17
    ;;
  SQ-10)
    PACK_FILE="Inkling-2-Mini-SQ-10.strata"
    PACK_KEY="inkling-2-mini-sq-10"
    PACK_BYTES="10471526400"
    PACK_SHA="ac4d452ad03e4930abd9d46f9c5eec22b2f75a1da45a1e49bd24ccd58b065934"
    NEED_GB=13
    ;;
  *)
    echo "[ERROR] Invalid --pack '$MODEL_PACK' (choose SQ-10, SQ-15, or SQ-20)"
    exit 1 ;;
esac

DEFAULT_MODE="fast"
DEFAULT_DRAFT="off"
if [[ "$CHIP_NAME" == *"Max"* || "$CHIP_NAME" == *"Ultra"* ]]; then
  DEFAULT_MODE="dspark"
  DEFAULT_DRAFT="dspark"
fi

echo "  Chip / macOS   : $CHIP_NAME (macOS $MACOS_VER)"
echo "  Unified Memory : ${MEM_GB} GB"
echo "  Engine Package : strata-inkling-${ENGINE_VERSION}-macos-arm64-metal"
echo "  Selected Pack  : ${MODEL_PACK} (${PACK_FILE})"
echo "  Optimal Tuning : --kv fp8 --draft ${DEFAULT_DRAFT} --chunk 1024 --max-context 32768 (Metal 4 MPP)"
echo ""

FREE_GB="$(df -g "$ROOT" | awk 'NR==2 {print $4}')"
MODEL_DIR="$ROOT/models/Inkling-2-Mini-Strata"
MODEL_FILE="$MODEL_DIR/$PACK_FILE"
if (( SKIP_MODEL == 0 )) && [[ ! -f "$MODEL_FILE" ]] && (( FREE_GB < NEED_GB )); then
  echo "[ERROR] Free disk space is ${FREE_GB} GB, need at least ${NEED_GB} GB."
  exit 1
fi

mkdir -p "$ROOT/downloads" "$ROOT/engine" "$ROOT/control" "$MODEL_DIR" "$ROOT/packs/$PACK_KEY/tokenizer" "$ROOT/logs"

# 1. Download & extract macOS Metal strata-inkling engine (0.8 MB, embedded Metal kernels)
ENGINE_BIN="$ROOT/engine/strata-inkling"
ENGINE_TGZ="$ROOT/downloads/strata-inkling-${ENGINE_VERSION}-macos-arm64-metal.tar.gz"
if [[ ! -x "$ENGINE_BIN" ]]; then
  echo "[1/6] Downloading strata-inkling (macOS arm64 Metal)..."
  curl -fSL -C - --retry 5 --connect-timeout 30 \
    "https://github.com/Niko1221/Strata/releases/download/${STRATA_RELEASE}/strata-inkling-${ENGINE_VERSION}-macos-arm64-metal.tar.gz" \
    -o "$ENGINE_TGZ"
  tar -xzf "$ENGINE_TGZ" -C "$ROOT/engine"
  chmod +x "$ENGINE_BIN"
  xattr -dr com.apple.quarantine "$ROOT/engine" 2>/dev/null || true
else
  echo "[1/6] strata-inkling (Metal) already installed."
fi

# 2. Download & extract macOS strata-control (bundled Python 3.12 + serve/server.py)
PYTHON_BIN="$ROOT/control/python/bin/python3"
CONTROL_TGZ="$ROOT/downloads/strata-control-${ENGINE_VERSION}-macos-arm64.tar.gz"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[2/6] Downloading strata-control (macOS arm64 + Python 3.12)..."
  curl -fSL -C - --retry 5 --connect-timeout 30 \
    "https://github.com/Niko1221/Strata/releases/download/${STRATA_RELEASE}/strata-control-${ENGINE_VERSION}-macos-arm64.tar.gz" \
    -o "$CONTROL_TGZ"
  tar -xzf "$CONTROL_TGZ" -C "$ROOT/control"
  xattr -dr com.apple.quarantine "$ROOT/control" 2>/dev/null || true
else
  echo "[2/6] strata-control already installed."
fi

# 3. Download tokenizer & config files
echo "[3/6] Downloading tokenizer & config files..."
for f in SHA256SUMS.txt chat_template.jinja config.json special_tokens_map.json tokenizer.json tokenizer_config.json; do
  if [[ ! -f "$MODEL_DIR/$f" ]]; then
    curl -fSL --retry 3 "https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/$f" -o "$MODEL_DIR/$f"
  fi
done

# 4. Download & verify/self-repair model pack
MODEL_URL="https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/${PACK_FILE}"
if (( SKIP_MODEL == 0 )); then
  CUR_SIZE=0
  if [[ -f "$MODEL_FILE" ]]; then
    CUR_SIZE="$(stat -f%z "$MODEL_FILE")"
  fi
  if [[ "$CUR_SIZE" != "$PACK_BYTES" ]]; then
    echo "[4/6] Downloading ${PACK_FILE} (resumable)..."
    curl --http1.1 -fSL -C - --retry 5 --connect-timeout 30 "$MODEL_URL" -o "$MODEL_FILE"
  else
    echo "[4/6] ${PACK_FILE} already downloaded; verifying SHA-256..."
  fi
  "$PYTHON_BIN" "$ROOT/scripts/Verify-Download.py" "$MODEL_FILE" "$MODEL_URL" "$PACK_SHA" "$PACK_BYTES"

  if (( WITH_VISION == 1 )); then
    VIS_FILE="$MODEL_DIR/Inkling-2-Mini-Vision.strata"
    VIS_URL="https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/Inkling-2-Mini-Vision.strata"
    if [[ ! -f "$VIS_FILE" ]] || [[ "$(stat -f%z "$VIS_FILE")" != "636526592" ]]; then
      echo "Downloading optional Vision shard (0.64 GB)..."
      curl --http1.1 -fSL -C - --retry 5 "$VIS_URL" -o "$VIS_FILE"
    fi
    "$PYTHON_BIN" "$ROOT/scripts/Verify-Download.py" "$VIS_FILE" "$VIS_URL" "6589eff47f9c20bc3a032faa3d449f10bf4f04a84cc6b2ee8a44d51d2b6fc677" "636526592"
  fi
else
  echo "[4/6] Skipped model download (--skip-model)."
fi

# 5. Build tokenizer pack, icons, tuned macOS Metal configs, and gateway config
echo "[5/6] Building tokenizer pack, icons & Metal configs..."
TOK_OUT="$ROOT/packs/$PACK_KEY/tokenizer"
"$PYTHON_BIN" - <<PY
import json, sys
from pathlib import Path
root = Path("$ROOT")
sys.path.insert(0, str(root / "control" / "tools"))
import inkling_tokenizer
inkling_tokenizer.convert_files(
    root / "models" / "Inkling-2-Mini-Strata" / "tokenizer.json",
    root / "models" / "Inkling-2-Mini-Strata" / "chat_template.jinja",
    Path("$TOK_OUT"),
)
gw_path = root / "gateway-config.json"
if not gw_path.is_file():
    gw_path.write_text(json.dumps({
        "enabled": True,
        "host": "0.0.0.0" if $LAN_ACCESS == 1 else "127.0.0.1",
        "port": $GATEWAY_PORT,
        "api_key": "",
        "auto_wake": True,
        "idle_unload_min": 0,
        "default_mode": "$DEFAULT_MODE",
        "default_context": 32768,
        "default_kv": "fp8",
        "default_chunk": 1024,
        "backend_port": $BACKEND_PORT,
    }, indent=2), encoding="utf-8")
# Pre-generate tuned Apple Silicon Metal run config
run_cfg_path = root / "strata-$PACK_KEY.json"
run_cfg_path.write_text(json.dumps({
    "exe": str(root / "engine" / "strata-inkling"),
    "args": [
        "--native", "$MODEL_FILE",
        "--max-context", "32768",
        "--kv", "fp8",
        "--draft", "$DEFAULT_DRAFT",
        "--chunk", "1024",
    ],
    "cwd": str(root / "control"),
    "tokenizer": "$TOK_OUT",
    "model_name": "$PACK_KEY",
    "aliases": ["$PACK_KEY", "inkling-2-mini-sq-15", "inkling-2-mini-sq-10", "inkling-2-mini-sq-20", "inkling-2-mini", "inkling", "strata"],
    "arch": "inkling2_loop",
    "backend": "metal",
    "log": str(root / "logs" / "strata-$PACK_KEY.log"),
    "lib_dirs": [],
    "port": $BACKEND_PORT,
    "drafting": "$DEFAULT_DRAFT",
    "parallel": 1,
    "sampling": {"temperature": 1.0},
    "fit_max_tokens": True,
    "open_browser": False,
    "env": {
        "STRATA_METAL_SPIN": "1",
        "STRATA_METAL_KEEPALIVE_MS": "250",
        "STRATA_METAL_KEEPALIVE_WINDOW_S": "600",
    },
}, indent=2), encoding="utf-8")
PY

"$PYTHON_BIN" "$ROOT/gui/make_icon.py"

# 6. Run Metal selfcheck & build native double-clickable macOS .app bundle
echo "[6/6] Running Metal GPU selfcheck & creating macOS .app launcher..."
if [[ -f "$MODEL_FILE" ]]; then
  "$ENGINE_BIN" --selfcheck --native "$MODEL_FILE" --kv fp8
fi

chmod +x "$ROOT/start-mac.command" "$ROOT/start-gateway-mac.command" 2>/dev/null || true

APP_BUNDLE="$ROOT/Inkling-2-Mini 控制台.app"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Inkling-2-Mini 控制台</string>
  <key>CFBundleDisplayName</key><string>Inkling-2-Mini 控制台</string>
  <key>CFBundleIdentifier</key><string>local.strata.inkling.gui</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

cat > "$APP_BUNDLE/Contents/MacOS/launcher" <<EOF
#!/usr/bin/env bash
exec "$PYTHON_BIN" "$ROOT/gui/app.py" --launch
EOF
chmod +x "$APP_BUNDLE/Contents/MacOS/launcher"

# Generate valid Apple Retina .icns icon from gui/inkling.png if sips + iconutil exist
if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1 && [[ -f "$ROOT/gui/inkling.png" ]]; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$ICONSET"
  sips -z 16 16     "$ROOT/gui/inkling.png" --out "$ICONSET/icon_16x16.png" >/dev/null 2>&1 || true
  sips -z 32 32     "$ROOT/gui/inkling.png" --out "$ICONSET/icon_16x16@2x.png" >/dev/null 2>&1 || true
  sips -z 32 32     "$ROOT/gui/inkling.png" --out "$ICONSET/icon_32x32.png" >/dev/null 2>&1 || true
  sips -z 64 64     "$ROOT/gui/inkling.png" --out "$ICONSET/icon_32x32@2x.png" >/dev/null 2>&1 || true
  sips -z 128 128   "$ROOT/gui/inkling.png" --out "$ICONSET/icon_128x128.png" >/dev/null 2>&1 || true
  sips -z 256 256   "$ROOT/gui/inkling.png" --out "$ICONSET/icon_128x128@2x.png" >/dev/null 2>&1 || true
  sips -z 256 256   "$ROOT/gui/inkling.png" --out "$ICONSET/icon_256x256.png" >/dev/null 2>&1 || true
  sips -z 512 512   "$ROOT/gui/inkling.png" --out "$ICONSET/icon_256x256@2x.png" >/dev/null 2>&1 || true
  sips -z 512 512   "$ROOT/gui/inkling.png" --out "$ICONSET/icon_512x512.png" >/dev/null 2>&1 || true
  if ! iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns" >/dev/null 2>&1; then
    "$PYTHON_BIN" - "$ICONSET" "$APP_BUNDLE/Contents/Resources/AppIcon.icns" <<'PYICNS'
import struct, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
chunks = []
for tag, fn in [(b"icp4","icon_16x16.png"),(b"icp5","icon_32x32.png"),(b"icp6","icon_32x32@2x.png"),(b"ic07","icon_128x128.png"),(b"ic08","icon_256x256.png"),(b"ic09","icon_512x512.png")]:
    p = src / fn
    if p.is_file():
        d = p.read_bytes()
        chunks.append(tag + struct.pack(">I", len(d) + 8) + d)
body = b"".join(chunks)
if body:
    dst.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
PYICNS
  fi
fi

echo ""
echo "============================================================"
echo "  macOS Apple Silicon 安裝完成！"
echo "  1. 雙擊「Inkling-2-Mini 控制台.app」（可拖進 Dock）開啟圖形控制台"
echo "  2. 模型量化包：${MODEL_PACK}（Metal 4 MPP + --chunk 1024 + --draft ${DEFAULT_DRAFT}）"
echo "  3. 開放 Agent 接口（OpenAI / Ollama / LM Studio / Anthropic）："
echo "     - OpenAI / LM Studio : http://127.0.0.1:${GATEWAY_PORT}/v1"
echo "     - Ollama 相容端點    : http://127.0.0.1:${GATEWAY_PORT}"
echo "     - Anthropic API      : http://127.0.0.1:${GATEWAY_PORT}/v1/messages"
echo "============================================================"

if (( LAUNCH_GUI == 1 )); then
  open "$APP_BUNDLE"
fi
