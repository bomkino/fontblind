#!/usr/bin/env python3
"""Local-only browser application for FontBlind."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, Callable, Sequence
from urllib.parse import urlsplit

from fontblind_pipeline import PublicBuildResult
from fontblind_protocol import (
    FONT_SET_MEDIA_TYPE,
    MAX_FONT_BYTES,
    MAX_FONT_SET_BYTES,
    FontSetError,
    FontSetTooLargeError,
    read_font_set_header,
)
from fontblind_policy import BrowserCompatibilityError, ZeroIdPolicyError
from fontblind_stream import StreamInterruptedError, copy_exact
from fontblind_surgical import FontBlindError
from fontblind_web import WebBuildError


APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_ROOT = APP_ROOT / "web"
if not WEB_ROOT.is_dir():
    WEB_ROOT = Path(sys.prefix) / "share" / "fontblind" / "web"
MAX_UPLOAD_BYTES = MAX_FONT_BYTES
MAX_VARIABLE_TOTAL_BYTES = MAX_FONT_SET_BYTES
JOB_TTL_SECONDS = 2 * 60 * 60
JOB_RE = re.compile(r"^[a-f0-9]{32}$")
WORKER_TIMEOUT_SECONDS = 5 * 60
SWEEP_INTERVAL_SECONDS = 60
OWNERSHIP_MARKER = ".fontblind-owned.json"

LAB_FAILURES = {
    "axis_model": "This two-axis set has no real base with independent weight and width extremes. Add the missing row or column masters.",
    "coordinates": "The donors do not expose unique, valid OpenType weight/width coordinates.",
    "structure": "The donors disagree on glyph order, character map, units, or interpolatable outline structure.",
    "upright": "The slant-axis lane needs an upright source at its 0-degree default.",
    "unsupported": "This Lab lane needs standalone static TrueType glyf fonts.",
    "compile": "The local compiler could not prove this Lab build safe and exact.",
}


class LabRequestError(FontBlindError):
    def __init__(self, failure: str) -> None:
        self.failure = failure if failure in LAB_FAILURES else "compile"
        super().__init__(LAB_FAILURES[self.failure])


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _scavenge_stale_roots(base: Path | None = None) -> None:
    """Remove abandoned FontBlind roots, never an active instance's root."""
    temp_root = Path(base or tempfile.gettempdir()).resolve()
    for candidate in temp_root.glob("fontblind-local-*"):
        if candidate.is_symlink() or candidate.parent.resolve() != temp_root or not candidate.is_dir():
            continue
        marker = candidate / OWNERSHIP_MARKER
        try:
            owner = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(owner["pid"])
            version = int(owner["version"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        if version == 1 and not _process_alive(pid):
            shutil.rmtree(candidate, ignore_errors=True)


@dataclass
class Job:
    path: Path
    result: PublicBuildResult
    created: float


class JobStore:
    def __init__(self) -> None:
        _scavenge_stale_roots()
        self._temporary = tempfile.TemporaryDirectory(prefix="fontblind-local-")
        self.root = Path(self._temporary.name)
        (self.root / OWNERSHIP_MARKER).write_text(
            json.dumps({"version": 1, "pid": os.getpid()}, separators=(",", ":")),
            encoding="utf-8",
        )
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._workers: dict[subprocess.Popen[bytes], int] = {}
        self._workers_lock = threading.Lock()
        self._closed = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, name="fontblind-expiry", daemon=True)
        self._sweeper.start()

    def _sweep_loop(self) -> None:
        while not self._closed.wait(SWEEP_INTERVAL_SECONDS):
            self.expire()

    def create(self, payload: bytes) -> tuple[str, Job]:
        return self._create("blind", [payload], {})

    def create_stream(self, source: BinaryIO, length: int) -> tuple[str, Job]:
        return self._create_stream("blind", source, [length], {})

    def create_oblique(self, payload: bytes, angle: float, output: str = "static") -> tuple[str, Job]:
        return self._create("oblique", [payload], {"angle": angle, "output": output})

    def create_oblique_stream(
        self,
        source: BinaryIO,
        length: int,
        angle: float,
        output: str = "static",
    ) -> tuple[str, Job]:
        return self._create_stream("oblique", source, [length], {"angle": angle, "output": output})

    def create_variable(self, payloads: list[bytes]) -> tuple[str, Job]:
        return self._create("variable", payloads, {})

    def create_variable_stream(
        self,
        source: BinaryIO,
        lengths: Sequence[int],
    ) -> tuple[str, Job]:
        return self._create_stream("variable", source, lengths, {})

    def _worker_command(
        self,
        mode: str,
        output_dir: Path,
        result_path: Path,
        parent_read_fd: int,
        options: dict[str, object],
        source_paths: list[Path],
    ) -> list[str]:
        arguments = [
            mode,
            str(output_dir),
            str(result_path),
            str(parent_read_fd),
            json.dumps(options, separators=(",", ":")),
            *(str(path) for path in source_paths),
        ]
        if getattr(sys, "frozen", False):
            return [sys.executable, "--fontblind-worker", *arguments]
        return [sys.executable, str(APP_ROOT / "fontblind_worker.py"), *arguments]

    def _validated_source_lengths(self, mode: str, lengths: Sequence[int]) -> tuple[int, ...]:
        values = tuple(int(length) for length in lengths)
        expected = range(2, 13) if mode == "variable" else range(1, 2)
        if len(values) not in expected:
            raise FontBlindError("Invalid local source count")
        if any(length <= 0 or length > MAX_UPLOAD_BYTES for length in values):
            raise FontBlindError("Invalid local source length")
        if sum(values) > (MAX_VARIABLE_TOTAL_BYTES if mode == "variable" else MAX_UPLOAD_BYTES):
            raise FontBlindError("Local source set is too large")
        return values

    def _create(self, mode: str, payloads: list[bytes], options: dict[str, object]) -> tuple[str, Job]:
        values = tuple(bytes(payload) for payload in payloads)
        lengths = self._validated_source_lengths(mode, [len(payload) for payload in values])

        def populate(streams: list[BinaryIO]) -> None:
            for target, payload, length in zip(streams, values, lengths):
                written = target.write(payload)
                if written != length:
                    raise OSError("Could not write the local source to its anonymous descriptor")

        return self._create_job(mode, len(values), options, populate)

    def _create_stream(
        self,
        mode: str,
        source: BinaryIO,
        lengths: Sequence[int],
        options: dict[str, object],
    ) -> tuple[str, Job]:
        values = self._validated_source_lengths(mode, lengths)

        def populate(streams: list[BinaryIO]) -> None:
            for target, length in zip(streams, values):
                copy_exact(source, target, length)

        return self._create_job(mode, len(values), options, populate)

    def _create_job(
        self,
        mode: str,
        source_count: int,
        options: dict[str, object],
        populate_sources: Callable[[list[BinaryIO]], None],
    ) -> tuple[str, Job]:
        if self._closed.is_set():
            raise FontBlindError("FontBlind is shutting down")
        token = uuid.uuid4().hex
        job_dir = self.root / token
        result_path = job_dir / ".result.json"
        try:
            job_dir.mkdir(mode=0o700)
            # POSIX TemporaryFile has no directory entry. Request bodies are
            # copied into these descriptors in bounded chunks, then workers
            # inherit only descriptors plus a parent-liveness pipe.
            with ExitStack() as stack:
                source_streams = [
                    stack.enter_context(tempfile.TemporaryFile(prefix=".fontblind-source-", dir=job_dir))
                    for _ in range(source_count)
                ]
                populate_sources(source_streams)
                for source_stream in source_streams:
                    source_stream.flush()
                    source_stream.seek(0)
                source_fds = [source_stream.fileno() for source_stream in source_streams]
                source_paths = [Path(f"/dev/fd/{source_fd}") for source_fd in source_fds]
                parent_read_fd, parent_write_fd = os.pipe()
                worker: subprocess.Popen[bytes] | None = None
                try:
                    worker = subprocess.Popen(
                        self._worker_command(
                            mode,
                            job_dir / "output",
                            result_path,
                            parent_read_fd,
                            options,
                            source_paths,
                        ),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        pass_fds=(*source_fds, parent_read_fd),
                    )
                    with self._workers_lock:
                        if self._closed.is_set():
                            worker.terminate()
                        self._workers[worker] = parent_write_fd
                    try:
                        returncode = worker.wait(timeout=WORKER_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired as exc:
                        worker.terminate()
                        try:
                            worker.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            worker.kill()
                            worker.wait(timeout=2)
                        raise FontBlindError("Local worker timed out") from exc
                finally:
                    if worker is not None:
                        with self._workers_lock:
                            self._workers.pop(worker, None)
                    for descriptor in (parent_write_fd, parent_read_fd):
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
            if returncode == 3:
                raise ZeroIdPolicyError("Strict zero-ID policy stopped this font")
            if returncode == 6:
                raise BrowserCompatibilityError("Browser compatibility preflight stopped this font")
            if returncode == 4 and result_path.is_file():
                try:
                    failure = str(json.loads(result_path.read_text(encoding="utf-8"))["failure"])
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    failure = "compile"
                raise LabRequestError(failure)
            if returncode != 0 or not result_path.is_file():
                raise FontBlindError("Local worker failed")
            value = json.loads(result_path.read_text(encoding="utf-8"))
            result = PublicBuildResult.from_internal_dict(value)
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        finally:
            result_path.unlink(missing_ok=True)

        checks = dict(result.checks)
        checks["source_discarded"] = not any(
            path.name == "source.font" or path.name.startswith(".fontblind-source-")
            for path in job_dir.iterdir()
        )
        result = PublicBuildResult(
            native=result.native,
            web=result.web,
            css=result.css,
            bundle=result.bundle,
            flavor=result.flavor,
            variable=result.variable,
            color=result.color,
            checks=checks,
            axes=result.axes,
            masters=result.masters,
        )
        result.require_verified()
        job = Job(path=job_dir, result=result, created=time.monotonic())
        with self._lock:
            self._jobs[token] = job
        return token, job

    def get(self, token: str) -> Job | None:
        if not JOB_RE.fullmatch(token):
            return None
        self.expire()
        with self._lock:
            return self._jobs.get(token)

    def delete(self, token: str) -> bool:
        if not JOB_RE.fullmatch(token):
            return False
        with self._lock:
            job = self._jobs.pop(token, None)
        if job is None:
            return False
        shutil.rmtree(job.path, ignore_errors=True)
        return True

    def expire(self) -> None:
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        with self._lock:
            expired = [token for token, job in self._jobs.items() if job.created < cutoff]
            jobs = [self._jobs.pop(token) for token in expired]
        for job in jobs:
            shutil.rmtree(job.path, ignore_errors=True)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._sweeper.join(timeout=2)
        with self._workers_lock:
            workers = list(self._workers.items())
            for worker, _parent_write_fd in workers:
                # The creator thread is the sole FD owner and closes the
                # liveness pipe in its finally block. Closing it here too can
                # race with descriptor reuse and close an unrelated resource.
                if worker.poll() is None:
                    worker.terminate()
        for worker, _ in workers:
            try:
                worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=2)
        self._temporary.cleanup()


class FontBlindServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        self.jobs: JobStore | None = None
        try:
            super().__init__(address, handler)
            self.jobs = JobStore()
            self.session_secret = secrets.token_urlsafe(32)
            self.worker_gate = threading.BoundedSemaphore(value=1)
        except Exception:
            super().server_close()
            raise

    def server_close(self) -> None:
        if self.jobs is not None:
            self.jobs.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: FontBlindServer
    server_version = "FontBlind"
    sys_version = ""

    def log_message(self, _format: str, *args: Any) -> None:
        # Request paths can contain opaque job tokens. Keep runtime silent.
        return

    def _headers(self, status: int, media_type: str, length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self' blob:; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _json(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _session_ok(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-FontBlind-Session", ""),
            self.server.session_secret,
        )

    def _host_ok(self) -> bool:
        port = self.server.server_port
        host = self.headers.get("Host", "").strip().lower()
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _static(self, path: Path, media_type: str | None = None) -> None:
        try:
            payload = path.read_bytes()
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
            return
        kind = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._headers(HTTPStatus.OK, kind, len(payload))
        self.wfile.write(payload)

    def _body_length(self, maximum: int, empty_error: str) -> int | None:
        raw_value = self.headers.get("Content-Length")
        if raw_value is None:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": empty_error})
            return None
        value = raw_value.strip()
        if not value or not value.isascii() or not value.isdecimal():
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid local upload framing."})
            return None
        length = int(value)
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": empty_error})
            return None
        if length > maximum:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Local input is too large."})
            return None
        return length

    def _public_result(self, token: str, job: Job) -> None:
        public = job.result.to_public_dict()
        for kind in ("native", "web", "css", "bundle"):
            public[kind]["url"] = f"/download/{token}/{kind}"
        self._json(HTTPStatus.OK, {"ok": True, "job": token, **public})

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"ok": False, "error": "Invalid local host."})
            return
        path = urlsplit(self.path).path
        static = {
            "/": (WEB_ROOT / "index.html", "text/html; charset=utf-8"),
            "/index.html": (WEB_ROOT / "index.html", "text/html; charset=utf-8"),
            "/styles.css": (WEB_ROOT / "styles.css", "text/css; charset=utf-8"),
            "/app.js": (WEB_ROOT / "app.js", "text/javascript; charset=utf-8"),
            "/lab-proof.js": (WEB_ROOT / "lab-proof.js", "text/javascript; charset=utf-8"),
            "/favicon.svg": (WEB_ROOT / "favicon.svg", "image/svg+xml"),
            "/lab-map.css": (WEB_ROOT / "lab-map.css", "text/css; charset=utf-8"),
        }
        if path in static:
            self._static(*static[path])
            return

        if path == "/api/session":
            self._json(HTTPStatus.OK, {"ok": True, "session": self.server.session_secret})
            return

        match = re.fullmatch(r"/download/([a-f0-9]{32})/(native|web|css|bundle)", path)
        if match:
            token, kind = match.groups()
            job = self.server.jobs.get(token)
            if job is None:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "This output has expired."})
                return
            item = getattr(job.result, kind)
            target = job.path / "output" / item.filename
            try:
                payload = target.read_bytes()
            except OSError:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Output unavailable."})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", item.media_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Disposition", f'attachment; filename="{item.filename}"')
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)
            return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"ok": False, "error": "Invalid local host."})
            return
        path = urlsplit(self.path).path
        if path not in {"/api/process", "/api/lab/oblique", "/api/lab/variable"}:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
            return
        if not self._session_ok():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Invalid local session."})
            return

        if not self.server.worker_gate.acquire(blocking=False):
            self._json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "error": "Another local build is already running. Finish or reset it before starting another."},
            )
            return
        try:
            try:
                media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
                if path in {"/api/process", "/api/lab/oblique"}:
                    if media_type != "application/octet-stream":
                        self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "Invalid local upload."})
                        return

                    angle = 12.0
                    output = "static"
                    if path == "/api/lab/oblique":
                        try:
                            angle = float(self.headers.get("X-FontBlind-Angle", "12"))
                        except ValueError:
                            angle = 0
                        if not 4 <= angle <= 20:
                            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Oblique angle must be between 4 and 20 degrees."})
                            return
                        output = self.headers.get("X-FontBlind-Output", "static").strip().lower()
                        if output not in {"static", "slnt"}:
                            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Choose a valid Oblique output."})
                            return

                    length = self._body_length(MAX_UPLOAD_BYTES, "Choose a TTF or OTF first.")
                    if length is None:
                        return
                    try:
                        if path == "/api/process":
                            token, job = self.server.jobs.create_stream(self.rfile, length)
                        else:
                            token, job = self.server.jobs.create_oblique_stream(self.rfile, length, angle, output)
                    except StreamInterruptedError:
                        self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "The local upload was interrupted."})
                        return
                else:
                    if media_type != FONT_SET_MEDIA_TYPE:
                        self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "Invalid local Lab request."})
                        return
                    try:
                        lengths = read_font_set_header(self.rfile, self.headers.get("Content-Length"))
                        token, job = self.server.jobs.create_variable_stream(self.rfile, lengths)
                    except FontSetTooLargeError as exc:
                        self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": str(exc)})
                        return
                    except (FontSetError, StreamInterruptedError):
                        self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid local Lab request."})
                        return
            except BrowserCompatibilityError:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "This font is missing structure required by modern browsers. No output was kept."},
                )
                return
            except ZeroIdPolicyError:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "This font contains data FontBlind cannot yet prove zero-ID. No output was kept."},
                )
                return
            except LabRequestError as exc:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": f"{exc} No output was kept."})
                return
            except (FontBlindError, WebBuildError, ValueError, OSError):
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "This font could not satisfy the zero-ID fidelity checks. No output was kept."},
                )
                return
            except Exception:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Processing failed safely. No output was kept."})
                return

            self._public_result(token, job)
        finally:
            self.server.worker_gate.release()

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"ok": False, "error": "Invalid local host."})
            return
        if not self._session_ok():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Invalid local session."})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", urlsplit(self.path).path)
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
            return
        deleted = self.server.jobs.delete(match.group(1))
        self._json(HTTPStatus.OK, {"ok": True, "deleted": deleted})


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run FontBlind on this machine only.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    return parser


def _stop_signal(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    args = make_parser().parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("FontBlind refuses non-loopback hosts.")
    try:
        server = FontBlindServer((args.host, args.port), Handler)
    except OSError:
        print("FontBlind could not open its local port. Close another FontBlind window or choose another port.", file=sys.stderr)
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


if __name__ == "__main__":
    raise SystemExit(main())
