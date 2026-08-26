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

for tool in python3 xcrun swiftc codesign ditto plutil file; do
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

cleanup() {
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
echo "Ad hoc signed for local use. Not notarized."
