#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if (( $# != 0 )); then
  echo "Usage: $(basename "$0")" >&2
  exit 64
fi

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
  echo "The Garuda/Arch package must be built on Linux." >&2
  exit 69
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "FontBlind's supported Garuda package is x86_64 only." >&2
  exit 69
fi
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Build the Garuda package as a normal user, not root." >&2
  exit 69
fi

for tool in python3 curl file ldd strings makepkg bsdtar zstd pacman sha256sum cmp; do
  require_tool "$tool"
done

export LC_ALL=C
export TZ=UTC
export PYTHONHASHSEED=0
export PYTHONDONTWRITEBYTECODE=1
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1577836800}"

TEMP_BASE="${TMPDIR:-/tmp}"
TEMP_BASE="${TEMP_BASE%/}"
BUILD_ROOT="$TEMP_BASE/fontblind-garuda-build-${UID:-0}-x86_64"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$BUILD_ROOT" && "$BUILD_ROOT" == "$TEMP_BASE"/fontblind-garuda-build-* && -d "$BUILD_ROOT" ]]; then
    rm -rf -- "$BUILD_ROOT"
  fi
}
trap cleanup EXIT

rm -rf -- "$BUILD_ROOT"
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
[[ -x "$SERVER_EXECUTABLE" ]] || fail "PyInstaller did not produce the FontBlind server executable."
file "$SERVER_EXECUTABLE" | grep -Eq 'ELF 64-bit.*x86-64' || fail "Frozen server is not an x86_64 Linux executable."
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

# Prove the exact frozen server before packaging it.
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

# Assemble one canonical input tree. makepkg receives the same bytes twice.
PACKAGE_INPUT="$BUILD_ROOT/package-input"
mkdir -p "$PACKAGE_INPUT/runtime"
cp -a "$SERVER_DIR" "$PACKAGE_INPUT/runtime/FontBlindServer"
cp "$APP_DIR/linux/fontblind" "$PACKAGE_INPUT/fontblind"
cp "$APP_DIR/web/favicon.svg" "$PACKAGE_INPUT/fontblind.svg"
cp "$APP_DIR/linux/README.txt" "$PACKAGE_INPUT/README.txt"
cp "$APP_DIR/LICENSE.txt" "$PACKAGE_INPUT/LICENSE.txt"
sed -e "s/@VERSION@/$VERSION/g" "$APP_DIR/linux/fontblind.desktop.in" > "$PACKAGE_INPUT/fontblind.desktop"
sed -e "s/@VERSION@/$VERSION/g" "$APP_DIR/linux/PKGBUILD.in" > "$PACKAGE_INPUT/PKGBUILD"
chmod 0755 "$PACKAGE_INPUT/fontblind"

while IFS= read -r -d '' path; do
  touch -h -d "@$SOURCE_DATE_EPOCH" "$path"
done < <(find "$PACKAGE_INPUT" -print0)

# Frozen code and public package inputs must not retain private build paths.
while IFS= read -r -d '' file_path; do
  if strings "$file_path" | grep -F -e "$APP_DIR" -e "$BUILD_ROOT" -e '/home/runner/' -e '/Users/runner/' >/dev/null; then
    fail "Garuda package input retained a source or temporary path: ${file_path#$PACKAGE_INPUT/}"
  fi
done < <(find "$PACKAGE_INPUT" -type f -print0)

build_package() {
  local label="$1"
  # Recreate the same canonical build path for both clean passes. Arch embeds
  # build-directory metadata, so different absolute paths would manufacture a
  # difference unrelated to package content.
  local work="$BUILD_ROOT/makepkg"
  local destination="$BUILD_ROOT/packages-$label"
  rm -rf -- "$work"
  mkdir -p "$work" "$destination"
  cp -a "$PACKAGE_INPUT/." "$work/"
  (
    cd "$work"
    PKGDEST="$destination" makepkg --nodeps --noconfirm --cleanbuild --clean
  )
  find "$destination" -maxdepth 1 -type f \
    -name "fontblind-bin-${VERSION}-1-x86_64.pkg.tar.zst" -print -quit
}

PACKAGE_ONE="$(build_package one)"
PACKAGE_TWO="$(build_package two)"
[[ -n "$PACKAGE_ONE" && -s "$PACKAGE_ONE" ]] || fail "The first makepkg pass produced no FontBlind package."
[[ -n "$PACKAGE_TWO" && -s "$PACKAGE_TWO" ]] || fail "The second makepkg pass produced no FontBlind package."
cmp --silent "$PACKAGE_ONE" "$PACKAGE_TWO" || fail "Two clean makepkg passes produced different package bytes."

pacman -Qp "$PACKAGE_ONE" | grep -qx "fontblind-bin $VERSION-1" || fail "Arch package metadata is incoherent."
for member in \
  usr/bin/fontblind \
  opt/fontblind/FontBlindServer/FontBlindServer \
  usr/share/applications/fontblind.desktop \
  usr/share/icons/hicolor/scalable/apps/fontblind.svg \
  usr/share/doc/fontblind/README.txt \
  usr/share/licenses/fontblind/LICENSE.txt; do
  bsdtar -tf "$PACKAGE_ONE" | grep -qx "$member" || fail "Arch package omitted $member."
done

AUDIT_ROOT="$BUILD_ROOT/package-audit"
mkdir -p "$AUDIT_ROOT"
bsdtar -xf "$PACKAGE_ONE" -C "$AUDIT_ROOT"
[[ -x "$AUDIT_ROOT/usr/bin/fontblind" ]] || fail "Installed launcher mode is not executable."
[[ -x "$AUDIT_ROOT/opt/fontblind/FontBlindServer/FontBlindServer" ]] || fail "Installed server mode is not executable."
if grep -R -I -n -E 'AppImage|aarch64|portable Linux|Ubuntu|Debian|Fedora' "$AUDIT_ROOT" >/dev/null; then
  fail "Garuda package retained an unsupported-platform claim."
fi
while IFS= read -r -d '' file_path; do
  if strings "$file_path" | grep -F -e "$APP_DIR" -e "$BUILD_ROOT" -e '/home/runner/' -e '/Users/runner/' >/dev/null; then
    fail "Garuda package retained a source or temporary build path: ${file_path#$AUDIT_ROOT/}"
  fi
done < <(find "$AUDIT_ROOT" -type f -print0)

ARTIFACT_DIR="$APP_DIR/output/linux"
mkdir -p "$ARTIFACT_DIR"
PACKAGE_NAME="fontblind-bin-${VERSION}-1-x86_64.pkg.tar.zst"
cp "$PACKAGE_ONE" "$ARTIFACT_DIR/$PACKAGE_NAME"
(
  cd "$ARTIFACT_DIR"
  sha256sum "$PACKAGE_NAME" > "$PACKAGE_NAME.sha256"
  sha256sum -c "$PACKAGE_NAME.sha256"
)

printf 'Garuda package gate passed for FontBlind %s (x86_64).\n' "$VERSION"
printf 'Packaged: %s\n' "$ARTIFACT_DIR/$PACKAGE_NAME"
