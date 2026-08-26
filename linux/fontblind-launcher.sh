#!/bin/sh
set -eu

umask 077

fail() {
  printf '%s\n' "FontBlind Linux: $*" >&2
  exit 70
}

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ROOT=${APPDIR:-$SELF_DIR}
case "$ROOT" in
  /*) ;;
  *) fail "application root must be absolute" ;;
esac

SERVER=${FONTBLIND_SERVER:-"$ROOT/usr/lib/fontblind/FontBlindServer"}
if [ ! -x "$SERVER" ]; then
  fail "bundled server executable is missing"
fi

RUNTIME_ROOT=${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}
if [ ! -d "$RUNTIME_ROOT" ] || [ ! -w "$RUNTIME_ROOT" ]; then
  RUNTIME_ROOT=/tmp
fi
STATE_DIR=$(mktemp -d "$RUNTIME_ROOT/fontblind-launch.XXXXXX") || fail "could not create private runtime state"
SERVER_LOG="$STATE_DIR/server.log"
SERVER_PID=

cleanup() {
  if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf -- "$STATE_DIR"
}
on_signal() {
  exit 143
}
trap cleanup EXIT
trap on_signal HUP INT TERM

"$SERVER" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

READY_LINE=
attempt=0
while [ "$attempt" -lt 240 ]; do
  READY_LINE=$(grep -m1 '^FONTBLIND_READY ' "$SERVER_LOG" 2>/dev/null || true)
  if [ -n "$READY_LINE" ]; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    cat "$SERVER_LOG" >&2 || true
    fail "bundled server exited before readiness"
  fi
  attempt=$((attempt + 1))
  sleep 0.05
done

if [ -z "$READY_LINE" ]; then
  cat "$SERVER_LOG" >&2 || true
  fail "bundled server did not announce readiness"
fi

# Readiness is a closed three-field protocol emitted by fontblind_entry.py.
set -- $READY_LINE
if [ "$#" -ne 3 ] || [ "$1" != "FONTBLIND_READY" ] || [ "$2" != "127.0.0.1" ]; then
  fail "bundled server emitted malformed readiness data"
fi
case "$3" in
  ''|*[!0-9]*) fail "bundled server emitted an invalid local port" ;;
esac
if [ "$3" -lt 1 ] || [ "$3" -gt 65535 ]; then
  fail "bundled server emitted an invalid local port"
fi

URL="http://127.0.0.1:$3"
printf 'FontBlind local: %s\n' "$URL"

if [ "${FONTBLIND_NO_OPEN:-0}" != "1" ]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
  elif command -v gio >/dev/null 2>&1; then
    gio open "$URL" >/dev/null 2>&1 &
  elif [ -n "${BROWSER:-}" ] && command -v "$BROWSER" >/dev/null 2>&1; then
    "$BROWSER" "$URL" >/dev/null 2>&1 &
  else
    printf 'Open this local address in a browser: %s\n' "$URL"
  fi
fi

set +e
wait "$SERVER_PID"
status=$?
set -e
SERVER_PID=
exit "$status"
