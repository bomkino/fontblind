"""Hardened local runtime used by the source launcher and native wrapper."""
from __future__ import annotations

import argparse
import secrets
import signal
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from fontblind_app import FontBlindServer, Handler, JobStore, _stop_signal
from fontblind_contract import validate_build_result
from fontblind_instance_http import InstanceHandler


class ContractJobStore(JobStore):
    """Validate worker output again in the parent before a token is exposed."""

    def _create_job(self, mode, source_count, options, populate_sources):  # type: ignore[no-untyped-def]
        token, job = super()._create_job(mode, source_count, options, populate_sources)
        try:
            validate_build_result(job.result)
        except Exception:
            self.delete(token)
            raise
        return token, job


class ContractFontBlindServer(FontBlindServer):
    """Use the strict parent store without changing the stable base server API."""

    def __init__(self, address: tuple[str, int], handler: type[Handler] = InstanceHandler) -> None:
        self.jobs: ContractJobStore | None = None
        try:
            ThreadingHTTPServer.__init__(self, address, handler)
            self.jobs = ContractJobStore()
            self.session_secret = secrets.token_urlsafe(32)
            self.worker_gate = threading.BoundedSemaphore(value=1)
        except Exception:
            ThreadingHTTPServer.server_close(self)
            raise


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run FontBlind on this machine only.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("FontBlind refuses non-loopback hosts.")
    try:
        server = ContractFontBlindServer((args.host, args.port), InstanceHandler)
    except OSError:
        print(
            "FontBlind could not open its local port. Close another FontBlind window or choose another port.",
            file=sys.stderr,
        )
        return 2
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"FontBlind local: {url}", flush=True)
    if not args.no_open:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    signal.signal(signal.SIGTERM, _stop_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _stop_signal)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0
