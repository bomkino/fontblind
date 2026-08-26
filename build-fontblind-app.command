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
# is not enough: every public lane, generated download, parent-child relation,
# and local response boundary must work before the bundle is signed.
SERVER_LOG="$BUILD_ROOT/server.log"
SERVER_HEADERS="$BUILD_ROOT/headers.txt"
SERVER_INDEX="$BUILD_ROOT/index.html"
SERVER_SCRIPT="$BUILD_ROOT/instance-export.js"
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
grep -q '<script src="/instance-export.js" defer></script>' "$SERVER_INDEX"
grep -q 'FREEZE A STATIC INSTANCE' "$SERVER_SCRIPT"
grep -qi '^Cache-Control: no-store, max-age=0' "$SERVER_HEADERS"
grep -qi '^Content-Security-Policy:' "$SERVER_HEADERS"

SMOKE_ROOT="$BUILD_ROOT/frozen-smoke"
mkdir -p "$SMOKE_ROOT"
"$PYTHON" - "$SERVER_URL" "$SMOKE_ROOT" <<'PY'
from __future__ import annotations

import http.client
import json
import pathlib
import re
import struct
import sys
import zipfile
from urllib.parse import urlsplit

from fontTools.ttLib import TTFont
from tests.test_lab import write_fixture_font


server = urlsplit(sys.argv[1])
root = pathlib.Path(sys.argv[2])
host = server.hostname or "127.0.0.1"
port = int(server.port or 80)
secrets = ("FROZEN_SMOKE_REGULAR_7Q9K", "FROZEN_SMOKE_BOLD_4M2X")
secret_probes = tuple(
    encoded
    for value in secrets
    for encoded in (
        value.encode("utf-8"),
        value.encode("utf-16-be"),
        value.encode("utf-16-le"),
    )
)


def request(
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=180)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def no_source_identity(payload: bytes, context: str) -> None:
    require(not any(probe in payload for probe in secret_probes), f"{context} leaked source identity")


status, session_headers, session_payload = request("GET", "/api/session")
require(status == 200, "frozen server session endpoint failed")
require(session_headers.get("Cache-Control") == "no-store, max-age=0", "session response can be cached")
session_value = json.loads(session_payload)
session = session_value.get("session")
require(session_value.get("ok") is True and isinstance(session, str), "invalid frozen session contract")
require(len(session) >= 32 and re.search(r"\s", session) is None, "weak frozen session contract")

regular = root / "secret-regular.ttf"
bold = root / "secret-bold.ttf"
write_fixture_font(regular, weight=400, family=secrets[0])
write_fixture_font(bold, weight=700, family=secrets[1])
regular_bytes = regular.read_bytes()
bold_bytes = bold.read_bytes()


def post_font(path: str, payload: bytes, extra: dict[str, str] | None = None) -> dict[str, object]:
    headers = {
        "Content-Type": "application/octet-stream",
        "X-FontBlind-Session": session,
        **(extra or {}),
    }
    status, _response_headers, response = request("POST", path, payload, headers)
    require(status == 200, f"frozen workbench failed at {path}: {status} {response[:200]!r}")
    no_source_identity(response, f"public result from {path}")
    value = json.loads(response)
    require(value.get("ok") is True, f"frozen workbench returned no success at {path}")
    require(isinstance(value.get("job"), str) and re.fullmatch(r"[a-f0-9]{32}", value["job"]), "invalid job token")
    return value


def exact_checks(value: dict[str, object], expected: set[str]) -> None:
    checks = value.get("checks")
    require(isinstance(checks, dict), "public result omitted proof")
    require(checks == {key: True for key in expected}, "public result returned the wrong proof contract")


def download(value: dict[str, object], label: str) -> dict[str, pathlib.Path]:
    paths: dict[str, pathlib.Path] = {}
    for kind in ("native", "web", "css", "bundle"):
        item = value.get(kind)
        require(isinstance(item, dict), f"{label} omitted {kind}")
        url = item.get("url")
        filename = item.get("filename")
        require(
            isinstance(url, str)
            and re.fullmatch(r"/download/[a-f0-9]{32}/(native|web|css|bundle)", url)
            and isinstance(filename, str),
            f"{label} returned an invalid {kind} descriptor",
        )
        status, headers, payload = request("GET", url)
        require(status == 200, f"{label} {kind} download failed")
        require(headers.get("Cache-Control") == "no-store, max-age=0", f"{label} {kind} can be cached")
        require(headers.get("Content-Disposition") == f'attachment; filename="{filename}"', f"{label} {kind} filename drifted")
        no_source_identity(payload, f"{label} {kind}")
        path = root / f"{label}-{filename}"
        path.write_bytes(payload)
        paths[kind] = path

    css = paths["css"].read_text(encoding="utf-8")
    require(css.count("@font-face") == 1 and "local(" not in css.casefold(), f"{label} CSS is unsafe")
    require(value["web"]["filename"] in css, f"{label} CSS does not reference its WOFF2")
    with zipfile.ZipFile(paths["bundle"], "r") as archive:
        expected_names = [value[kind]["filename"] for kind in ("native", "web", "css")]
        require(archive.namelist() == expected_names, f"{label} package manifest drifted")
        for kind, name in zip(("native", "web", "css"), expected_names):
            require(archive.read(name) == paths[kind].read_bytes(), f"{label} package changed {kind}")
    return paths


def delete_job(value: dict[str, object]) -> None:
    token = value["job"]
    status, _headers, payload = request(
        "DELETE",
        f"/api/jobs/{token}",
        headers={"X-FontBlind-Session": session},
    )
    require(status == 200 and json.loads(payload).get("deleted") is True, "frozen job cleanup failed")


blind = post_font("/api/process", regular_bytes)
exact_checks(
    blind,
    {
        "source_identity_removed",
        "embedding_flags_cleared",
        "outline_flavor_retained",
        "functional_clone_verified",
        "harfbuzz_shaping_verified",
        "woff2_roundtrip_verified",
        "source_discarded",
    },
)
blind_paths = download(blind, "blind")
for kind in ("native", "web"):
    font = TTFont(str(blind_paths[kind]), lazy=False)
    try:
        require("glyf" in font and "fvar" not in font, "Blind changed the fixture font model")
    finally:
        font.close()
delete_job(blind)

oblique = post_font(
    "/api/lab/oblique",
    regular_bytes,
    {"X-FontBlind-Angle": "12", "X-FontBlind-Output": "static"},
)
exact_checks(
    oblique,
    {
        "source_identity_removed",
        "embedding_flags_cleared",
        "declared_shear_verified",
        "oblique_not_italic_verified",
        "hinting_removed",
        "harfbuzz_shaping_verified",
        "woff2_roundtrip_verified",
        "source_discarded",
    },
)
oblique_paths = download(oblique, "oblique")
font = TTFont(str(oblique_paths["native"]), lazy=False)
try:
    require(bool(int(font["OS/2"].fsSelection) & 0x0200), "static Oblique omitted its Oblique bit")
    require(not bool(int(font["OS/2"].fsSelection) & 0x0001), "static Oblique claimed Italic")
finally:
    font.close()
delete_job(oblique)

slant = post_font(
    "/api/lab/oblique",
    regular_bytes,
    {"X-FontBlind-Angle": "12", "X-FontBlind-Output": "slnt"},
)
exact_checks(
    slant,
    {
        "source_identity_removed",
        "embedding_flags_cleared",
        "declared_shear_verified",
        "slant_axis_verified",
        "variable_endpoints_verified",
        "oblique_not_italic_verified",
        "hinting_removed",
        "harfbuzz_shaping_verified",
        "woff2_roundtrip_verified",
        "source_discarded",
    },
)
require([axis["tag"] for axis in slant.get("axes", [])] == ["slnt"], "slant workbench exposed the wrong axis")
require(len(slant.get("masters", [])) == 2, "slant workbench exposed the wrong master map")
slant_paths = download(slant, "slant")
font = TTFont(str(slant_paths["native"]), lazy=False)
try:
    require([str(axis.axisTag) for axis in font["fvar"].axes] == ["slnt"], "frozen slant output lost its axis")
finally:
    font.close()
delete_job(slant)

font_set = bytearray(b"FBLAB1\x00\x00")
font_set.extend(struct.pack(">B", 2))
font_set.extend(struct.pack(">I", len(regular_bytes)))
font_set.extend(struct.pack(">I", len(bold_bytes)))
font_set.extend(regular_bytes)
font_set.extend(bold_bytes)
status, _headers, variable_payload = request(
    "POST",
    "/api/lab/variable",
    bytes(font_set),
    {
        "Content-Type": "application/vnd.fontblind.font-set",
        "X-FontBlind-Session": session,
    },
)
require(status == 200, f"frozen Variable Lab failed: {status} {variable_payload[:200]!r}")
no_source_identity(variable_payload, "Variable Lab public result")
variable = json.loads(variable_payload)
exact_checks(
    variable,
    {
        "source_identity_removed",
        "embedding_flags_cleared",
        "donor_compatibility_verified",
        "donor_instances_verified",
        "independent_axis_model_verified",
        "axis_metadata_verified",
        "hinting_removed",
        "harfbuzz_shaping_verified",
        "woff2_roundtrip_verified",
        "weight_axis_verified",
        "source_discarded",
    },
)
require([axis["tag"] for axis in variable.get("axes", [])] == ["wght"], "Variable Lab exposed the wrong axis")
require(len(variable.get("masters", [])) == 2, "Variable Lab exposed the wrong master map")
variable_paths = download(variable, "variable")
font = TTFont(str(variable_paths["native"]), lazy=False)
try:
    require([str(axis.axisTag) for axis in font["fvar"].axes] == ["wght"], "Variable Lab output lost its weight axis")
finally:
    font.close()

status, _headers, instance_payload = request(
    "POST",
    f"/api/jobs/{variable['job']}/instance",
    json.dumps({"location": {"wght": 550}}).encode("utf-8"),
    {"Content-Type": "application/json", "X-FontBlind-Session": session},
)
require(status == 200, f"frozen static export failed: {status} {instance_payload[:200]!r}")
no_source_identity(instance_payload, "static export public result")
instance = json.loads(instance_payload)
require(instance.get("location") == {"wght": 550.0}, "static export confirmed the wrong location")
require("axes" not in instance and "masters" not in instance, "static export remained variable in public data")
exact_checks(
    instance,
    {
        "source_identity_removed",
        "embedding_flags_cleared",
        "selected_location_verified",
        "static_instance_verified",
        "variation_tables_removed",
        "axis_metadata_verified",
        "harfbuzz_shaping_verified",
        "woff2_roundtrip_verified",
        "source_discarded",
    },
)
instance_paths = download(instance, "instance")
for kind in ("native", "web"):
    font = TTFont(str(instance_paths[kind]), lazy=False)
    try:
        require(not ({"avar", "cvar", "fvar", "gvar", "HVAR", "MVAR", "STAT", "VVAR"} & set(font.keys())), "static export retained variation tables")
        require(int(font["OS/2"].usWeightClass) == 550, "static export emitted the wrong weight")
    finally:
        font.close()

# The parent is authoritative: deleting it must invalidate the child package.
delete_job(variable)
status, _headers, _payload = request("GET", instance["native"]["url"])
require(status == 404, "a frozen child survived deletion of its variable parent")
status, _headers, _payload = request("GET", variable["native"]["url"])
require(status == 404, "a deleted variable parent remained downloadable")
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
echo "Frozen all-lane gauntlet passed. Ad hoc signed for local use. Not notarized."
