#!/usr/bin/env python3
"""Frozen entry point for the native FontBlind macOS wrapper.

The same PyInstaller executable serves two roles. With no arguments it owns the
loopback HTTP server. When the local job store relaunches ``sys.executable``
with ``--fontblind-worker``, this entry point dispatches into the isolated worker
instead of starting a second server.
"""
from __future__ import annotations

import signal
import sys

import fontblind_instance as _fontblind_instance  # Force frozen builds to include the static export lane.
import fontblind_lab as _fontblind_lab  # Force frozen builds to include dynamically dispatched Lab builders.
from fontblind_instance_http import InstanceHandler
from fontblind_runtime import ContractFontBlindServer
from fontblind_worker import _terminate as terminate_worker
from fontblind_worker import main as worker_main


READY_PREFIX = "FONTBLIND_READY"


def _is_worker_invocation(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[1] == "--fontblind-worker"


def _run_worker(argv: list[str]) -> int:
    signal.signal(signal.SIGTERM, terminate_worker)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, terminate_worker)
    return int(worker_main(["fontblind_worker.py", *argv[2:]]))


def _stop_server(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def _run_server() -> int:
    signal.signal(signal.SIGTERM, _stop_server)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _stop_server)

    server = ContractFontBlindServer(("127.0.0.1", 0), InstanceHandler)
    print(f"{READY_PREFIX} 127.0.0.1 {server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    if _is_worker_invocation(arguments):
        return _run_worker(arguments)
    if len(arguments) != 1:
        return 64
    return _run_server()


if __name__ == "__main__":
    raise SystemExit(main())
