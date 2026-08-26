#!/bin/zsh
set -euo pipefail

APP_DIR="${0:A:h}"
cd "$APP_DIR"

INSTALL_APP=1
if [[ "${1:-}" == "--no-install" ]]; then
  INSTALL_APP=0
  shift
fi
if (( $# != 0 )); then
  echo "Usage: ${0:t} [--no-install]" >&2
  exit 64
fi

for tool in python3 xcrun swiftc codesign ditto plutil file curl; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing required build tool: $tool" >&2
    exit 69
  fi
done

ARCHITECTURE="$(uname -m)"
case "$ARCHITECTURE" in
  arm64|x86_64) ;;
  *)
    echo "Unsupported macOS architecture: $ARCHITECTURE" >&2
    exit 69
    ;;
esac

TEMP_BASE="${TMPDIR:-/tmp}"
TEMP_BASE="${TEMP_BASE%/}"
BUILD_ROOT="$(mktemp -d "$TEMP_BASE/fontblind-macos-build.XXXXXX")"
INSTALL_STAGING=""
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$INSTALL_STAGING" && "$INSTALL_STAGING" == /Applications/.FontBlind.install.* && -e "$INSTALL_STAGING" ]]; then
    rm -rf -- "$INSTALL_STAGING"
  fi
  if [[ -n "$BUILD_ROOT" && "$BUILD_ROOT" == "$TEMP_BASE"/fontblind-macos-build.* && -d "$BUILD_ROOT" ]]; then
    rm -rf -- "$BUILD_ROOT"
  fi
}
trap cleanup EXIT

PYTHON_ENV="$BUILD_ROOT/python-env"
python3 -m venv "$PYTHON_ENV"
PYTHON="$PYTHON_ENV/bin/python"
"$PYTHON" -m pip install --disable-pip-version-check --quiet \
  -r "$APP_DIR/requirements.txt" \
  "pyinstaller==6.22.2"

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --noupx \
  --name FontBlindServer \
  --paths "$APP_DIR" \
  --add-data "$APP_DIR/web:web" \
  --collect-all fontTools \
  --collect-all uharfbuzz \
  --collect-all brotli \
  --hidden-import=fontblind_lab \
  --hidden-import=fontblind_instance \
  --hidden-import=fontblind_instance_http \
  --hidden-import=fontblind_runtime \
  --distpath "$BUILD_ROOT/server-dist" \
  --workpath "$BUILD_ROOT/server-work" \
  --specpath "$BUILD_ROOT" \
  --log-level WARN \
  "$APP_DIR/fontblind_entry.py"

SERVER_DIR="$BUILD_ROOT/server-dist/FontBlindServer"
SERVER_EXECUTABLE="$SERVER_DIR/FontBlindServer"
if [[ ! -x "$SERVER_EXECUTABLE" ]]; then
  echo "PyInstaller did not produce the FontBlind server executable." >&2
  exit 70
fi

# Launch the exact frozen server that will be embedded in the app. A valid ZIP
# is not enough: imports, local routing, injected controls, and response headers
# must work before the bundle is signed and published.
SERVER_LOG="$BUILD_ROOT/server.log"
SERVER_HEADERS="$BUILD_ROOT/headers.txt"
SERVER_INDEX="$BUILD_ROOT/index.html"
SERVER_SCRIPT="$BUILD_ROOT/instance-export.js"
SERVER_SESSION="$BUILD_ROOT/session.json"
"$SERVER_EXECUTABLE" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
READY_LINE=""
for _attempt in {1..160}; do
  READY_LINE="$(grep -m1 '^FONTBLIND_READY ' "$SERVER_LOG" || true)"
  if [[ -n "$READY_LINE" ]]; then
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    echo "Frozen FontBlind server exited before readiness." >&2
    cat "$SERVER_LOG" >&2 || true
    exit 70
  fi
  sleep 0.05
done
if [[ -z "$READY_LINE" ]]; then
  echo "Frozen FontBlind server did not announce readiness." >&2
  cat "$SERVER_LOG" >&2 || true
  exit 70
fi
read -r READY_PREFIX READY_HOST READY_PORT <<< "$READY_LINE"
if [[ "$READY_PREFIX" != "FONTBLIND_READY" || "$READY_HOST" != "127.0.0.1" || ! "$READY_PORT" == <-> ]]; then
  echo "Frozen FontBlind server emitted malformed readiness data." >&2
  exit 70
fi
SERVER_URL="http://127.0.0.1:$READY_PORT"
curl --fail --silent --show-error --dump-header "$SERVER_HEADERS" "$SERVER_URL/" >"$SERVER_INDEX"
curl --fail --silent --show-error "$SERVER_URL/instance-export.js" >"$SERVER_SCRIPT"
curl --fail --silent --show-error "$SERVER_URL/api/session" >"$SERVER_SESSION"
grep -q '<script src="/instance-export.js" defer></script>' "$SERVER_INDEX"
grep -q 'FREEZE A STATIC INSTANCE' "$SERVER_SCRIPT"
grep -qi '^Cache-Control: no-store, max-age=0' "$SERVER_HEADERS"
grep -qi '^Content-Security-Policy:' "$SERVER_HEADERS"
"$PYTHON" - "$SERVER_SESSION" <<'PY'
import json
import pathlib
import re
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if value.get("ok") is not True or not isinstance(value.get("session"), str):
    raise SystemExit("frozen server returned an invalid session contract")
if len(value["session"]) < 32 or re.search(r"\s", value["session"]):
    raise SystemExit("frozen server returned a weak session contract")
PY
kill "$SERVER_PID"
wait "$SERVER_PID" || true
SERVER_PID=""

BUNDLE="$BUILD_ROOT/FontBlind.app"
CONTENTS="$BUNDLE/Contents"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources/server"
ditto "$SERVER_DIR" "$CONTENTS/Resources/server"
cp "$APP_DIR/macos/FontBlind.icns" "$CONTENTS/Resources/FontBlind.icns"
cp "$APP_DIR/macos/Info.plist" "$CONTENTS/Info.plist"
plutil -lint "$CONTENTS/Info.plist" >/dev/null

SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
swiftc \
  -parse-as-library \
  -O \
  -whole-module-optimization \
  -swift-version 5 \
  -target "$ARCHITECTURE-apple-macosx13.0" \
  -sdk "$SDK_PATH" \
  -framework AppKit \
  -framework WebKit \
  -framework UniformTypeIdentifiers \
  "$APP_DIR/macos/FontBlindApp.swift" \
  -o "$CONTENTS/MacOS/FontBlind"

INNER_SERVER="$CONTENTS/Resources/server/FontBlindServer"
while IFS= read -r -d '' candidate; do
  if [[ "$candidate" == "$INNER_SERVER" ]]; then
    continue
  fi
  if file -b "$candidate" | grep -q '^Mach-O'; then
    codesign --force --sign - "$candidate"
  fi
done < <(find "$CONTENTS/Resources/server" -type f -print0)

codesign --force --sign - --identifier dog.pitch.fontblind.server "$INNER_SERVER"
codesign --force --sign - --identifier dog.pitch.fontblind "$BUNDLE"
codesign --verify --deep --strict --verbose=2 "$BUNDLE"

ARTIFACT_DIR="$APP_DIR/output/macos"
mkdir -p "$ARTIFACT_DIR"
ARCHIVE_TEMP="$BUILD_ROOT/FontBlind.zip"
ditto -c -k --sequesterRsrc --keepParent "$BUNDLE" "$ARCHIVE_TEMP"
unzip -tq "$ARCHIVE_TEMP" >/dev/null
ditto "$ARCHIVE_TEMP" "$ARTIFACT_DIR/FontBlind.zip"
shasum -a 256 "$ARTIFACT_DIR/FontBlind.zip" > "$ARTIFACT_DIR/FontBlind.zip.sha256"

if (( INSTALL_APP )); then
  INSTALL_TARGET="/Applications/FontBlind.app"
  if [[ -L "$INSTALL_TARGET" ]]; then
    echo "Refusing to replace symlink at $INSTALL_TARGET" >&2
    exit 73
  fi
  if pgrep -f '^/Applications/FontBlind\.app/Contents/MacOS/FontBlind([[:space:]]|$)' >/dev/null 2>&1; then
    echo "Quit the installed FontBlind app, then run this builder again." >&2
    exit 75
  fi

  INSTALL_STAGING="/Applications/.FontBlind.install.$$"
  if [[ -e "$INSTALL_STAGING" ]]; then
    echo "Unexpected install staging path already exists: $INSTALL_STAGING" >&2
    exit 73
  fi
  if ! ditto "$BUNDLE" "$INSTALL_STAGING"; then
    echo "Could not write to /Applications. Artifact remains at $ARTIFACT_DIR/FontBlind.zip" >&2
    exit 73
  fi
  codesign --verify --deep --strict --verbose=2 "$INSTALL_STAGING"

  BACKUP=""
  if [[ -e "$INSTALL_TARGET" ]]; then
    BACKUP="/Applications/FontBlind.backup-$(date +%Y%m%d-%H%M%S).app"
    if [[ -e "$BACKUP" ]]; then
      echo "Backup path already exists: $BACKUP" >&2
      exit 73
    fi
    mv "$INSTALL_TARGET" "$BACKUP"
  fi

  if ! mv "$INSTALL_STAGING" "$INSTALL_TARGET"; then
    if [[ -n "$BACKUP" && ! -e "$INSTALL_TARGET" && -e "$BACKUP" ]]; then
      mv "$BACKUP" "$INSTALL_TARGET"
    fi
    echo "Install failed; previous app was restored when present." >&2
    exit 73
  fi
  INSTALL_STAGING=""
  codesign --verify --deep --strict --verbose=2 "$INSTALL_TARGET"
  echo "Installed: $INSTALL_TARGET"
  if [[ -n "$BACKUP" ]]; then
    echo "Previous app preserved: $BACKUP"
  fi
fi

echo "Packaged: $ARTIFACT_DIR/FontBlind.zip"
echo "Frozen server smoke passed. Ad hoc signed for local use. Not notarized."
