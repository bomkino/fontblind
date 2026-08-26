#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

BUILD_PORTABLE=1
BUILD_ARCH_PACKAGE=0
case "${1:-}" in
  "")
    if command -v makepkg >/dev/null 2>&1; then
      BUILD_ARCH_PACKAGE=1
    fi
    ;;
  --portable-only)
    ;;
  --arch-package-only)
    BUILD_PORTABLE=0
    BUILD_ARCH_PACKAGE=1
    ;;
  --all)
    BUILD_ARCH_PACKAGE=1
    ;;
  *)
    echo "Usage: $(basename "$0") [--portable-only|--arch-package-only|--all]" >&2
    exit 64
    ;;
esac

fail() {
  echo "$*" >&2
  exit 70
}

require_tool() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required build tool: $1" >&2
    exit 69
  }
}

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "The Linux package must be built on Linux." >&2
  exit 69
fi

for tool in python3 curl file tar gzip sha256sum ldd strings; do
  require_tool "$tool"
done
if (( BUILD_ARCH_PACKAGE )); then
  for tool in makepkg bsdtar zstd pacman; do
    require_tool "$tool"
  done
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    echo "Build the Garuda/Arch package as a normal user, not root." >&2
    exit 69
  fi
fi

MACHINE="$(uname -m)"
case "$MACHINE" in
  x86_64)
    ARCH="x86_64"
    APPIMAGE_ARCH="x86_64"
    APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    APPIMAGETOOL_SHA256="a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0"
    APPIMAGE_RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
    APPIMAGE_RUNTIME_SHA256="1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf"
    ;;
  aarch64|arm64)
    ARCH="aarch64"
    APPIMAGE_ARCH="aarch64"
    APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-aarch64.AppImage"
    APPIMAGETOOL_SHA256="1b00524ba8c6b678dc15ef88a5c25ec24def36cdfc7e3abb32ddcd068e8007fe"
    APPIMAGE_RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-aarch64"
    APPIMAGE_RUNTIME_SHA256="7d5d772b7c32f0c84caf0a452a3072a5709027d7eac5856feb89a7a7a8881372"
    ;;
  *)
    echo "Unsupported Linux architecture: $MACHINE" >&2
    exit 69
    ;;
esac

export LC_ALL=C
export TZ=UTC
export PYTHONHASHSEED=0
export PYTHONDONTWRITEBYTECODE=1
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1577836800}"

TEMP_BASE="${TMPDIR:-/tmp}"
TEMP_BASE="${TEMP_BASE%/}"
BUILD_ROOT="$TEMP_BASE/fontblind-linux-build-${UID:-0}-${ARCH}"
SERVER_PID=""
PACKAGE_PID=""

cleanup() {
  for pid in "$PACKAGE_PID" "$SERVER_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  if [[ -n "$BUILD_ROOT" && "$BUILD_ROOT" == "$TEMP_BASE"/fontblind-linux-build-* && -d "$BUILD_ROOT" ]]; then
    rm -rf -- "$BUILD_ROOT"
  fi
}
trap cleanup EXIT

if [[ -e "$BUILD_ROOT" ]]; then
  rm -rf -- "$BUILD_ROOT"
fi
mkdir -m 0700 -p "$BUILD_ROOT"

PYTHON_ENV="$BUILD_ROOT/python-env"
python3 -m venv "$PYTHON_ENV"
PYTHON="$PYTHON_ENV/bin/python"
"$PYTHON" -m pip install --disable-pip-version-check --quiet \
  -r "$APP_DIR/requirements.txt" \
  "pyinstaller==6.22.2"
VERSION="$(PYTHONPATH="$APP_DIR" "$PYTHON" -c 'from fontblind_version import PROGRAM_VERSION; print(PROGRAM_VERSION)')"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  fail "FontBlind returned an invalid package version."
fi

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
  --hidden-import=fontblind_desktop \
  --hidden-import=fontblind_lab \
  --hidden-import=fontblind_instance \
  --hidden-import=fontblind_instance_proof \
  --hidden-import=fontblind_instance_verified \
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
  fail "PyInstaller did not produce the FontBlind server executable."
fi
file "$SERVER_EXECUTABLE" | grep -Eq 'ELF 64-bit.*(x86-64|ARM aarch64)' || fail "Frozen server has the wrong Linux architecture."
if ldd "$SERVER_EXECUTABLE" 2>&1 | grep -q 'not found'; then
  fail "Frozen server has unresolved dynamic libraries."
fi

wait_for_ready() {
  local pid="$1"
  local log="$2"
  local label="$3"
  local line=""
  for _attempt in $(seq 1 240); do
    line="$(grep -m1 '^FONTBLIND_READY ' "$log" || true)"
    if [[ -n "$line" ]]; then
      printf '%s\n' "$line"
      return 0
    fi
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "$label exited before readiness." >&2
      cat "$log" >&2 || true
      return 1
    fi
    sleep 0.05
  done
  echo "$label did not announce readiness." >&2
  cat "$log" >&2 || true
  return 1
}

exercise_browser_shutdown() {
  local pid="$1"
  local url="$2"
  local session_json session_secret can_quit shutdown_json
  session_json="$(curl --fail --silent --show-error "$url/api/session")"
  read -r session_secret can_quit <<< "$(printf '%s' "$session_json" | "$PYTHON" -c 'import json,sys; value=json.load(sys.stdin); assert set(value)=={"ok","session","can_quit"} and value["ok"] is True; print(value["session"], str(value["can_quit"]).lower())')"
  if [[ -z "$session_secret" || "$can_quit" != "true" ]]; then
    fail "Linux browser app did not expose its reviewed quit capability."
  fi
  shutdown_json="$(curl --fail --silent --show-error -X POST -H "X-FontBlind-Session: $session_secret" "$url/api/shutdown")"
  printf '%s' "$shutdown_json" | "$PYTHON" -c 'import json,sys; value=json.load(sys.stdin); assert value == {"ok": True, "shutdown": True}'
  for _attempt in $(seq 1 120); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.05
  done
  fail "Linux browser app did not exit after its authenticated quit request."
}

# Prove the exact frozen server before placing it in any Linux package.
SERVER_LOG="$BUILD_ROOT/server.log"
"$SERVER_EXECUTABLE" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
READY_LINE="$(wait_for_ready "$SERVER_PID" "$SERVER_LOG" "Frozen FontBlind server")"
read -r READY_PREFIX READY_HOST READY_PORT <<< "$READY_LINE"
if [[ "$READY_PREFIX" != "FONTBLIND_READY" || "$READY_HOST" != "127.0.0.1" || ! "$READY_PORT" =~ ^[0-9]+$ ]]; then
  fail "Frozen FontBlind server emitted malformed readiness data."
fi
SERVER_URL="http://127.0.0.1:$READY_PORT"
SMOKE_ROOT="$BUILD_ROOT/frozen-smoke"
mkdir -p "$SMOKE_ROOT"
if [[ -n "${FONTBLIND_CORPUS_DIR:-}" ]]; then
  "$PYTHON" "$APP_DIR/tools/fetch_corpus.py" --output "$FONTBLIND_CORPUS_DIR" --verify-only
  "$PYTHON" "$APP_DIR/release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT" "$FONTBLIND_CORPUS_DIR"
else
  "$PYTHON" "$APP_DIR/release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT"
fi
kill "$SERVER_PID"
wait "$SERVER_PID" || true
SERVER_PID=""

APPDIR="$BUILD_ROOT/FontBlind.AppDir"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/doc/fontblind" \
  "$APPDIR/usr/share/icons/hicolor/scalable/apps" \
  "$APPDIR/usr/share/licenses/fontblind"
cp -a "$SERVER_DIR" "$APPDIR/usr/bin/FontBlindServer"
cp "$APP_DIR/web/favicon.svg" "$APPDIR/fontblind.svg"
cp "$APP_DIR/web/favicon.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/fontblind.svg"
cp "$APP_DIR/linux/README.txt" "$APPDIR/README.txt"
cp "$APP_DIR/linux/README.txt" "$APPDIR/usr/share/doc/fontblind/README.txt"
cp "$APP_DIR/LICENSE.txt" "$APPDIR/usr/share/licenses/fontblind/LICENSE.txt"
sed \
  -e "s/@VERSION@/$VERSION/g" \
  -e 's/@EXEC@/AppRun/g' \
  -e 's/@TRY_EXEC@/AppRun/g' \
  "$APP_DIR/linux/fontblind.desktop.in" > "$APPDIR/fontblind.desktop"
cp "$APPDIR/fontblind.desktop" "$APPDIR/usr/share/applications/fontblind.desktop"
ln -s "fontblind.svg" "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
APPDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$APPDIR/usr/bin/FontBlindServer/FontBlindServer" --fontblind-browser-app "$@"
RUNNER
chmod 0755 "$APPDIR/AppRun"

# Frozen code must not expose the source checkout or private build root.
while IFS= read -r -d '' file_path; do
  if strings "$file_path" | grep -F -e "$APP_DIR" -e "$BUILD_ROOT" -e '/home/runner/' -e '/Users/runner/' >/dev/null; then
    fail "Linux AppDir retained a source or temporary build path: ${file_path#$APPDIR/}"
  fi
done < <(find "$APPDIR" -type f -print0)

# Exercise the actual AppDir entry point and authenticated quit lifecycle.
APPDIR_LOG="$BUILD_ROOT/appdir.log"
"$APPDIR/AppRun" --no-open >"$APPDIR_LOG" 2>&1 &
PACKAGE_PID=$!
APPDIR_READY="$(wait_for_ready "$PACKAGE_PID" "$APPDIR_LOG" "Linux AppDir")"
read -r _APPDIR_PREFIX _APPDIR_HOST APPDIR_PORT <<< "$APPDIR_READY"
exercise_browser_shutdown "$PACKAGE_PID" "http://127.0.0.1:$APPDIR_PORT"
wait "$PACKAGE_PID" || true
PACKAGE_PID=""

# Canonicalise package timestamps after runtime validation.
while IFS= read -r -d '' path; do
  touch -h -d "@$SOURCE_DATE_EPOCH" "$path"
done < <(find "$APPDIR" -print0)

ARTIFACT_DIR="$APP_DIR/output/linux"
mkdir -p "$ARTIFACT_DIR"

write_checksum() {
  local path="$1"
  local name
  name="$(basename "$path")"
  (
    cd "$(dirname "$path")"
    sha256sum "$name" > "$name.sha256"
    sha256sum -c "$name.sha256"
  )
}

if (( BUILD_PORTABLE )); then
  TARBALL_NAME="FontBlind-${VERSION}-linux-${ARCH}.tar.gz"
  TARBALL="$ARTIFACT_DIR/$TARBALL_NAME"
  tar \
    --sort=name \
    --format=posix \
    --mtime="@$SOURCE_DATE_EPOCH" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    --pax-option=delete=atime,delete=ctime \
    -C "$BUILD_ROOT" \
    -cf - FontBlind.AppDir | gzip -n -9 > "$TARBALL"
  write_checksum "$TARBALL"
  tar -tzf "$TARBALL" >/dev/null

  APPIMAGETOOL="$BUILD_ROOT/appimagetool-${APPIMAGE_ARCH}.AppImage"
  APPIMAGE_RUNTIME="$BUILD_ROOT/runtime-${APPIMAGE_ARCH}"
  curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error "$APPIMAGETOOL_URL" -o "$APPIMAGETOOL"
  curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error "$APPIMAGE_RUNTIME_URL" -o "$APPIMAGE_RUNTIME"
  printf '%s  %s\n' "$APPIMAGETOOL_SHA256" "$APPIMAGETOOL" | sha256sum -c -
  printf '%s  %s\n' "$APPIMAGE_RUNTIME_SHA256" "$APPIMAGE_RUNTIME" | sha256sum -c -
  chmod 0755 "$APPIMAGETOOL" "$APPIMAGE_RUNTIME"

  APPIMAGE="$ARTIFACT_DIR/FontBlind-${VERSION}-${APPIMAGE_ARCH}.AppImage"
  APPIMAGE_EXTRACT_AND_RUN=1 \
    ARCH="$APPIMAGE_ARCH" \
    VERSION="$VERSION" \
    APPIMAGETOOL_APP_NAME="FontBlind" \
    "$APPIMAGETOOL" --runtime-file "$APPIMAGE_RUNTIME" "$APPDIR" "$APPIMAGE"
  chmod 0755 "$APPIMAGE"
  file "$APPIMAGE" | grep -q 'ELF 64-bit' || fail "appimagetool produced an invalid AppImage."
  write_checksum "$APPIMAGE"

  APPIMAGE_LOG="$BUILD_ROOT/appimage.log"
  APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE" --no-open >"$APPIMAGE_LOG" 2>&1 &
  PACKAGE_PID=$!
  APPIMAGE_READY="$(wait_for_ready "$PACKAGE_PID" "$APPIMAGE_LOG" "Linux AppImage")"
  read -r _APPIMAGE_PREFIX _APPIMAGE_HOST APPIMAGE_PORT <<< "$APPIMAGE_READY"
  exercise_browser_shutdown "$PACKAGE_PID" "http://127.0.0.1:$APPIMAGE_PORT"
  wait "$PACKAGE_PID" || true
  PACKAGE_PID=""
fi

if (( BUILD_ARCH_PACKAGE )); then
  PKG_WORK="$BUILD_ROOT/arch-package"
  mkdir -p "$PKG_WORK"
  cp -a "$APPDIR" "$PKG_WORK/FontBlind.AppDir"
  sed \
    -e "s/@VERSION@/$VERSION/g" \
    -e 's/@EXEC@/fontblind/g' \
    -e 's/@TRY_EXEC@/fontblind/g' \
    "$APP_DIR/linux/fontblind.desktop.in" > "$PKG_WORK/fontblind.desktop"
  sed \
    -e "s/@VERSION@/$VERSION/g" \
    -e "s/@ARCH@/$ARCH/g" \
    "$APP_DIR/linux/PKGBUILD.in" > "$PKG_WORK/PKGBUILD"
  (
    cd "$PKG_WORK"
    PKGDEST="$ARTIFACT_DIR" makepkg --nodeps --noconfirm --cleanbuild --clean
  )
  PACKAGE="$(find "$ARTIFACT_DIR" -maxdepth 1 -type f -name "fontblind-bin-${VERSION}-1-${ARCH}.pkg.tar.zst" -print -quit)"
  [[ -n "$PACKAGE" && -s "$PACKAGE" ]] || fail "makepkg did not produce the expected Garuda/Arch package."
  pacman -Qp "$PACKAGE" | grep -qx "fontblind-bin $VERSION-1" || fail "Arch package metadata is incoherent."
  bsdtar -tf "$PACKAGE" | grep -qx 'usr/bin/fontblind' || fail "Arch package omitted its launcher."
  bsdtar -tf "$PACKAGE" | grep -qx 'opt/fontblind/AppRun' || fail "Arch package omitted its desktop runtime."
  write_checksum "$PACKAGE"
fi

# No source tree, build root, or runner home may survive in the public artifacts.
for artifact in "$ARTIFACT_DIR"/*; do
  [[ -f "$artifact" ]] || continue
  case "$artifact" in
    *.sha256) continue ;;
  esac
  if strings "$artifact" | grep -F -e "$APP_DIR" -e "$BUILD_ROOT" -e '/home/runner/' -e '/Users/runner/' >/dev/null; then
    fail "Linux artifact retained a source or temporary build path: $(basename "$artifact")"
  fi
done

printf 'Linux package gate passed for FontBlind %s (%s).\n' "$VERSION" "$ARCH"
if (( BUILD_PORTABLE )); then
  echo "Portable artifacts: AppImage + tar.gz"
fi
if (( BUILD_ARCH_PACKAGE )); then
  echo "Garuda/Arch artifact: pkg.tar.zst"
fi
