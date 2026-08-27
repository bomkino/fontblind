#!/usr/bin/env bash
set -uo pipefail

if (( $# > 2 )); then
  echo "Usage: $(basename "$0") [PACKAGE_FILE] [RECEIPT_FILE]" >&2
  exit 64
fi
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run the Dell G7 preflight from the normal KDE user session, not root." >&2
  exit 77
fi

PACKAGE_FILE="${1:-}"
RECEIPT_FILE="${2:-fontblind-dell-g7-preflight.txt}"
TMP_ROOT="$(mktemp -d /tmp/fontblind-dell-g7-preflight.XXXXXX)" || exit 70
SERVER_PID=""
FAILURES=0

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$TMP_ROOT"
}
trap cleanup EXIT

exec > >(tee "$RECEIPT_FILE") 2>&1

pass() {
  printf 'PASS  %s\n' "$1"
}

fail() {
  printf 'FAIL  %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}

value_from_os_release() {
  local key="$1"
  sed -n "s/^${key}=//p" /etc/os-release 2>/dev/null | head -n1 | sed -e 's/^"//' -e 's/"$//'
}

require_command() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "command available: $1"
  else
    fail "required command missing: $1"
  fi
}

printf 'FontBlind Dell G7 · Garuda KDE preflight\n'
printf 'Date: %s\n' "$(date --iso-8601=seconds 2>/dev/null || date)"
printf 'Kernel: %s\n' "$(uname -sr 2>/dev/null || printf unknown)"
printf 'Architecture: %s\n' "$(uname -m 2>/dev/null || printf unknown)"

for command in pacman file ldd curl python3 xdg-settings plasmashell; do
  require_command "$command"
done

OS_ID="$(value_from_os_release ID)"
OS_LIKE="$(value_from_os_release ID_LIKE)"
printf 'Distribution ID: %s\n' "${OS_ID:-unknown}"
printf 'Distribution family: %s\n' "${OS_LIKE:-unknown}"
if [[ "$OS_ID" == "garuda" && " $OS_LIKE " == *" arch "* ]]; then
  pass "Garuda reports an Arch-family base"
else
  fail "system is not the supported Garuda Arch-family target"
fi

if [[ "$(uname -m 2>/dev/null)" == "x86_64" ]]; then
  pass "machine architecture is x86_64"
else
  fail "machine architecture is not x86_64"
fi

DMI_VENDOR="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)"
DMI_PRODUCT="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"
printf 'System vendor: %s\n' "${DMI_VENDOR:-unavailable}"
printf 'Product name: %s\n' "${DMI_PRODUCT:-unavailable}"
if [[ "${DMI_VENDOR,,}" == *dell* && "${DMI_PRODUCT,,}" == *g7* ]]; then
  pass "DMI identifies a Dell G7"
else
  fail "DMI does not identify the supported Dell G7 reference machine"
fi

PLASMA_VERSION="$(plasmashell --version 2>/dev/null || true)"
printf 'Plasma: %s\n' "${PLASMA_VERSION:-unavailable}"
if [[ "$PLASMA_VERSION" =~ [[:space:]]6([.[:space:]]|$) ]]; then
  pass "KDE Plasma major version is 6"
else
  fail "KDE Plasma 6 was not detected"
fi

printf 'XDG_CURRENT_DESKTOP: %s\n' "${XDG_CURRENT_DESKTOP:-unset}"
printf 'KDE_SESSION_VERSION: %s\n' "${KDE_SESSION_VERSION:-unset}"
printf 'XDG_SESSION_TYPE: %s\n' "${XDG_SESSION_TYPE:-unset}"
printf 'WAYLAND_DISPLAY: %s\n' "${WAYLAND_DISPLAY:-unset}"
printf 'DISPLAY: %s\n' "${DISPLAY:-unset}"
[[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]] && pass "current desktop includes KDE" || fail "current desktop is not KDE"
[[ "${KDE_SESSION_VERSION:-}" == "6" ]] && pass "KDE session version is 6" || fail "KDE_SESSION_VERSION is not 6"
[[ "${XDG_SESSION_TYPE:-}" == "wayland" ]] && pass "session type is Wayland" || fail "session type is not Wayland"
[[ -n "${WAYLAND_DISPLAY:-}" ]] && pass "Wayland display is present" || fail "Wayland display is missing"

DEFAULT_BROWSER="$(xdg-settings get default-web-browser 2>/dev/null || true)"
printf 'Default browser desktop file: %s\n' "${DEFAULT_BROWSER:-unavailable}"
[[ "$DEFAULT_BROWSER" == *.desktop ]] && pass "KDE/XDG has a configured default browser" || fail "no XDG default browser desktop file was detected"

if pacman -Q fontblind-bin >/dev/null 2>&1; then
  INSTALLED_PACKAGE="$(pacman -Q fontblind-bin)"
  printf 'Installed package: %s\n' "$INSTALLED_PACKAGE"
  [[ "$INSTALLED_PACKAGE" == "fontblind-bin 3.7.0-1" ]] \
    && pass "installed FontBlind package version is exact" \
    || fail "installed FontBlind package version is not 3.7.0-1"
else
  fail "fontblind-bin is not installed"
fi

if pacman -T glibc hicolor-icon-theme xdg-utils kde-cli-tools >/dev/null 2>&1; then
  pass "all declared Garuda/KDE runtime dependencies are installed"
else
  fail "one or more Garuda/KDE runtime dependencies are missing"
fi

for path in \
  /usr/bin/fontblind \
  /opt/fontblind/FontBlindServer/FontBlindServer \
  /usr/share/applications/fontblind.desktop \
  /usr/share/icons/hicolor/scalable/apps/fontblind.svg; do
  if pacman -Qo "$path" 2>/dev/null | grep -q '^.* is owned by fontblind-bin 3\.7\.0-1$'; then
    pass "package owns $path"
  else
    fail "package ownership is wrong or missing for $path"
  fi
done

SERVER=/opt/fontblind/FontBlindServer/FontBlindServer
if [[ -x "$SERVER" ]] && file "$SERVER" | grep -Eq 'ELF 64-bit.*x86-64'; then
  pass "installed server is an executable x86_64 ELF"
else
  fail "installed server is not the expected x86_64 ELF"
fi
if [[ -x "$SERVER" ]] && ! ldd "$SERVER" 2>&1 | grep -q 'not found'; then
  pass "installed server has no unresolved dynamic libraries"
else
  fail "installed server has unresolved dynamic libraries"
fi

if [[ -n "$PACKAGE_FILE" ]]; then
  if [[ -f "$PACKAGE_FILE" ]]; then
    printf 'Candidate package: %s\n' "$(basename "$PACKAGE_FILE")"
    printf 'Candidate SHA-256: %s\n' "$(sha256sum "$PACKAGE_FILE" | awk '{print $1}')"
    if [[ -f "$PACKAGE_FILE.sha256" ]] && (
      cd "$(dirname "$PACKAGE_FILE")" && sha256sum -c "$(basename "$PACKAGE_FILE").sha256" >/dev/null
    ); then
      pass "candidate package matches its adjacent SHA-256 receipt"
    else
      fail "candidate package has no valid adjacent SHA-256 receipt"
    fi
  else
    fail "candidate package path does not exist"
  fi
fi

# Exercise the installed frozen runtime without opening a browser. This does
# not replace the visible KDE launch test; it proves the machine can start,
# authenticate, bind only to loopback, shut down, and remove reconnect state.
if [[ -x "$SERVER" && -x "$(command -v curl 2>/dev/null || true)" && -x "$(command -v python3 2>/dev/null || true)" ]]; then
  RUNTIME_DIR="$TMP_ROOT/runtime"
  SERVER_LOG="$TMP_ROOT/server.log"
  mkdir -m 0700 -p "$RUNTIME_DIR"
  env \
    XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    XDG_CURRENT_DESKTOP=KDE \
    KDE_SESSION_VERSION=6 \
    XDG_SESSION_TYPE=wayland \
    WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
    "$SERVER" --fontblind-browser-app --no-open >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!

  READY_LINE=""
  for _attempt in $(seq 1 240); do
    READY_LINE="$(grep -m1 '^FONTBLIND_READY 127\.0\.0\.1 [0-9]\+$' "$SERVER_LOG" 2>/dev/null || true)"
    [[ -n "$READY_LINE" ]] && break
    if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
      break
    fi
    sleep 0.05
  done

  if [[ -n "$READY_LINE" ]]; then
    read -r _READY _HOST PORT <<< "$READY_LINE"
    URL="http://127.0.0.1:$PORT"
    pass "installed browser runtime announced a valid loopback URL"

    SESSION_JSON="$(curl --fail --silent --show-error "$URL/api/session" 2>/dev/null || true)"
    SESSION_DATA="$(printf '%s' "$SESSION_JSON" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    assert set(value) == {"ok", "session", "can_quit"}
    assert value["ok"] is True and value["can_quit"] is True
    assert isinstance(value["session"], str) and len(value["session"]) >= 32
except Exception:
    raise SystemExit(1)
print(value["session"])
' 2>/dev/null || true)"
    if [[ -n "$SESSION_DATA" ]]; then
      pass "installed browser runtime returned the exact authenticated desktop capability"
      SHUTDOWN_JSON="$(curl --fail --silent --show-error -X POST \
        -H "X-FontBlind-Session: $SESSION_DATA" "$URL/api/shutdown" 2>/dev/null || true)"
      if [[ "$SHUTDOWN_JSON" == '{"ok":true,"shutdown":true}' ]]; then
        pass "authenticated browser shutdown was accepted"
      else
        fail "authenticated browser shutdown returned an unexpected response"
      fi
    else
      fail "installed browser runtime returned an invalid desktop session contract"
    fi
  else
    cat "$SERVER_LOG" 2>/dev/null || true
    fail "installed browser runtime did not become ready"
  fi

  for _attempt in $(seq 1 160); do
    if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
      break
    fi
    sleep 0.05
  done
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    fail "installed browser runtime remained alive after shutdown"
  else
    wait "$SERVER_PID" >/dev/null 2>&1 || true
    SERVER_PID=""
    pass "installed browser runtime exited after shutdown"
  fi
  [[ ! -e "$RUNTIME_DIR/fontblind/desktop.url" ]] \
    && pass "private reconnect URL was removed" \
    || fail "private reconnect URL survived shutdown"
fi

if (( FAILURES == 0 )); then
  printf 'RESULT: PASS\n'
  printf 'Receipt: %s\n' "$RECEIPT_FILE"
  exit 0
fi

printf 'RESULT: FAIL (%d checks)\n' "$FAILURES"
printf 'Receipt: %s\n' "$RECEIPT_FILE"
exit 1
