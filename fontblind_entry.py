#!/usr/bin/env python3
"""Frozen entry point shared by the native and browser-hosted desktop apps.

The same PyInstaller executable serves three roles:

- no arguments: loopback server owned by the macOS wrapper;
- ``--fontblind-browser-app``: Linux/system-browser desktop lifecycle;
- ``--fontblind-worker``: isolated font compiler child.

Keeping all three roles in one executable preserves the existing anonymous-file
worker seam: frozen workers relaunch ``sys.executable`` without introducing a
second runtime or copying source paths into a new process contract.
"""
from __future__ import annotations

import signal
import sys
import threading

import fontblind_instance as _fontblind_instance  # Force frozen builds to include the static export lane.
import fontblind_lab as _fontblind_lab  # Force frozen builds to include dynamically dispatched Lab builders.
from fontblind_desktop import BrowserAppLease, DesktopLifecycleError, open_desktop_url
from fontblind_instance_http import InstanceHandler
from fontblind_runtime import ContractFontBlindServer
from fontblind_worker import _terminate as terminate_worker
from fontblind_worker import main as worker_main


READY_PREFIX = "FONTBLIND_READY"
BROWSER_APP_FLAG = "--fontblind-browser-app"
NO_OPEN_FLAG = "--no-open"


def _is_worker_invocation(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[1] == "--fontblind-worker"


def _run_worker(argv: list[str]) -> int:
    signal.signal(signal.SIGTERM, terminate_worker)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, terminate_worker)
    return int(worker_main(["fontblind_worker.py", *argv[2:]]))


def _stop_server(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def _run_server(*, open_browser: bool = False, allow_browser_shutdown: bool = False) -> int:
    signal.signal(signal.SIGTERM, _stop_server)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _stop_server)

    lease: BrowserAppLease | None = None
    if allow_browser_shutdown:
        try:
            lease = BrowserAppLease.acquire()
        except DesktopLifecycleError as exc:
            print(f"FontBlind desktop lifecycle failed safely: {exc}", file=sys.stderr)
            return 70
        if not lease.owned:
            existing = lease.read_existing_url()
            lease.close()
            if existing:
                print(f"FONTBLIND_EXISTING {existing}", flush=True)
                if open_browser:
                    open_desktop_url(existing)
                return 0
            print("FontBlind is already starting. Try again in a moment.", file=sys.stderr)
            return 75

    server: ContractFontBlindServer | None = None
    try:
        server = ContractFontBlindServer(
            ("127.0.0.1", 0),
            InstanceHandler,
            allow_browser_shutdown=allow_browser_shutdown,
        )
        url = f"http://127.0.0.1:{server.server_port}"
        if lease is not None:
            lease.publish(url)
        print(f"{READY_PREFIX} 127.0.0.1 {server.server_port}", flush=True)
        if open_browser:
            # Readiness and server ownership never depend on a desktop opener.
            timer = threading.Timer(0.35, lambda: open_desktop_url(url))
            timer.daemon = True
            timer.start()
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if lease is not None:
            lease.close()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    if _is_worker_invocation(arguments):
        return _run_worker(arguments)
    if len(arguments) == 1:
        return _run_server()
    if arguments[1:] == [BROWSER_APP_FLAG]:
        return _run_server(open_browser=True, allow_browser_shutdown=True)
    if arguments[1:] == [BROWSER_APP_FLAG, NO_OPEN_FLAG]:
        return _run_server(open_browser=False, allow_browser_shutdown=True)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
