#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "This installed-package gate is for x86_64 Garuda/Arch only." >&2
  exit 69
fi
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run the KDE lifecycle gate as a normal user." >&2
  exit 69
fi
if (( $# != 2 )); then
  echo "Usage: $(basename "$0") REPOSITORY_ROOT CORPUS_DIR" >&2
  exit 64
fi

REPOSITORY_ROOT="$(cd "$1" && pwd)"
CORPUS_DIR="$(cd "$2" && pwd)"
PYTHON="${FONTBLIND_TEST_PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || { echo "Validation Python is unavailable." >&2; exit 69; }
SERVER=/opt/fontblind/FontBlindServer/FontBlindServer
LAUNCHER=/usr/bin/fontblind
DESKTOP=/usr/share/applications/fontblind.desktop
ICON=/usr/share/icons/hicolor/scalable/apps/fontblind.svg

for path in "$SERVER" "$LAUNCHER" "$DESKTOP" "$ICON"; do
  [[ -e "$path" ]] || { echo "Installed package omitted $path" >&2; exit 70; }
done
[[ -x "$SERVER" && -x "$LAUNCHER" ]] || { echo "Installed executables have invalid modes." >&2; exit 70; }
file "$SERVER" | grep -Eq 'ELF 64-bit.*x86-64' || { echo "Installed server is not x86_64." >&2; exit 70; }
if ldd "$SERVER" 2>&1 | grep -q 'not found'; then
  echo "Installed server has unresolved libraries." >&2
  exit 70
fi

ROOT="$(mktemp -d /tmp/fontblind-garuda-installed.XXXXXX)"
RAW_PID=""
DESKTOP_PID=""
cleanup() {
  for pid in "$DESKTOP_PID" "$RAW_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  rm -rf -- "$ROOT"
}
trap cleanup EXIT

wait_for_line() {
  local pattern="$1"
  local file="$2"
  local pid="$3"
  for _attempt in $(seq 1 240); do
    if grep -m1 -E "$pattern" "$file" >/dev/null 2>&1; then
      grep -m1 -E "$pattern" "$file"
      return 0
    fi
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      cat "$file" >&2 || true
      return 1
    fi
    sleep 0.05
  done
  cat "$file" >&2 || true
  return 1
}

# Run the complete product gauntlet through the executable installed by pacman.
RAW_LOG="$ROOT/raw-server.log"
"$SERVER" >"$RAW_LOG" 2>&1 &
RAW_PID=$!
RAW_READY="$(wait_for_line '^FONTBLIND_READY 127\.0\.0\.1 [0-9]+$' "$RAW_LOG" "$RAW_PID")"
read -r _READY _HOST RAW_PORT <<< "$RAW_READY"
mkdir -m 0700 -p "$ROOT/release-gauntlet"
"$PYTHON" "$REPOSITORY_ROOT/release_gauntlet.py" \
  "http://127.0.0.1:$RAW_PORT" \
  "$ROOT/release-gauntlet" \
  "$CORPUS_DIR"
kill "$RAW_PID"
wait "$RAW_PID" || true
RAW_PID=""

# Model the actual target session: KDE Plasma 6, Wayland, no X11 DISPLAY.
RUNTIME_DIR="$ROOT/runtime"
FAKE_BIN="$ROOT/bin"
OPEN_LOG="$ROOT/open.log"
ENV_LOG="$ROOT/opener-environment.log"
mkdir -m 0700 -p "$RUNTIME_DIR" "$FAKE_BIN"
cat > "$FAKE_BIN/xdg-open" <<'OPENER'
#!/bin/sh
set -eu
[ "${XDG_CURRENT_DESKTOP:-}" = "KDE" ]
[ "${KDE_SESSION_VERSION:-}" = "6" ]
[ "${XDG_SESSION_TYPE:-}" = "wayland" ]
[ -n "${WAYLAND_DISPLAY:-}" ]
[ -z "${DISPLAY+x}" ]
printf '%s\n' "$1" >> "$FONTBLIND_OPEN_LOG"
printf 'KDE|6|wayland|%s|DISPLAY=unset\n' "$WAYLAND_DISPLAY" >> "$FONTBLIND_ENV_LOG"
OPENER
chmod 0755 "$FAKE_BIN/xdg-open"

TARGET_ENV=(
  env -u DISPLAY
  PATH="$FAKE_BIN:/usr/bin"
  XDG_RUNTIME_DIR="$RUNTIME_DIR"
  XDG_CURRENT_DESKTOP=KDE
  KDE_SESSION_VERSION=6
  XDG_SESSION_TYPE=wayland
  WAYLAND_DISPLAY=wayland-0
  FONTBLIND_OPEN_LOG="$OPEN_LOG"
  FONTBLIND_ENV_LOG="$ENV_LOG"
  __NV_PRIME_RENDER_OFFLOAD=
  __GLX_VENDOR_LIBRARY_NAME=
  VK_ICD_FILENAMES=
)

DESKTOP_LOG="$ROOT/desktop-owner.log"
"${TARGET_ENV[@]}" "$LAUNCHER" >"$DESKTOP_LOG" 2>&1 &
DESKTOP_PID=$!
DESKTOP_READY="$(wait_for_line '^FONTBLIND_READY 127\.0\.0\.1 [0-9]+$' "$DESKTOP_LOG" "$DESKTOP_PID")"
read -r _READY _HOST DESKTOP_PORT <<< "$DESKTOP_READY"
EXPECTED_URL="http://127.0.0.1:$DESKTOP_PORT"

for _attempt in $(seq 1 120); do
  [[ -s "$OPEN_LOG" ]] && break
  sleep 0.05
done
[[ "$(sed -n '1p' "$OPEN_LOG")" == "$EXPECTED_URL" ]] || { echo "KDE opener did not receive the exact loopback URL." >&2; exit 70; }

SECOND_LOG="$ROOT/desktop-second.log"
"${TARGET_ENV[@]}" "$LAUNCHER" >"$SECOND_LOG" 2>&1
SECOND_LINE="$(grep -m1 '^FONTBLIND_EXISTING ' "$SECOND_LOG" || true)"
[[ "$SECOND_LINE" == "FONTBLIND_EXISTING $EXPECTED_URL" ]] || { cat "$SECOND_LOG" >&2; echo "Second launch did not reuse the existing session." >&2; exit 70; }
for _attempt in $(seq 1 120); do
  [[ "$(wc -l < "$OPEN_LOG")" -ge 2 ]] && break
  sleep 0.05
done
[[ "$(sed -n '2p' "$OPEN_LOG")" == "$EXPECTED_URL" ]] || { echo "Second KDE launch opened a different URL." >&2; exit 70; }
[[ "$(sort -u "$OPEN_LOG" | wc -l)" -eq 1 ]] || { echo "Multiple loopback sessions were exposed." >&2; exit 70; }
[[ "$(sort -u "$ENV_LOG" | tr -d '\r')" == 'KDE|6|wayland|wayland-0|DISPLAY=unset' ]] || { cat "$ENV_LOG" >&2; echo "KDE Wayland opener environment was not preserved." >&2; exit 70; }

"$PYTHON" - "$EXPECTED_URL" <<'PY'
from __future__ import annotations
import http.client
import json
import sys
from urllib.parse import urlsplit

url = urlsplit(sys.argv[1])
connection = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
connection.request("GET", "/api/session")
response = connection.getresponse()
value = json.loads(response.read())
assert response.status == 200
assert set(value) == {"ok", "session", "can_quit"}
assert value["ok"] is True and value["can_quit"] is True and value["session"]
connection.close()

connection = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
connection.request("POST", "/api/shutdown", headers={"X-FontBlind-Session": value["session"]})
response = connection.getresponse()
shutdown = json.loads(response.read())
assert response.status == 200 and shutdown == {"ok": True, "shutdown": True}
connection.close()
PY

for _attempt in $(seq 1 160); do
  if ! kill -0 "$DESKTOP_PID" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
if kill -0 "$DESKTOP_PID" >/dev/null 2>&1; then
  echo "Installed KDE launcher did not exit after authenticated shutdown." >&2
  exit 70
fi
wait "$DESKTOP_PID" || true
DESKTOP_PID=""
[[ ! -e "$RUNTIME_DIR/fontblind/desktop.url" ]] || { echo "Private reconnect URL survived shutdown." >&2; exit 70; }

printf 'Installed Garuda/KDE package gate passed.\n'
