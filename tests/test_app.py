from __future__ import annotations

import io
import json
import http.client
import inspect
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fontblind_app import (
    OWNERSHIP_MARKER,
    MAX_UPLOAD_BYTES,
    FontBlindServer,
    Handler,
    Job,
    JobStore,
    _scavenge_stale_roots,
)
from fontblind_protocol import FONT_SET_MEDIA_TYPE, pack_font_set
from fontblind_stream import COPY_CHUNK_BYTES
from fontblind_surgical import FontBlindError
from fontblind_worker import main as worker_main


class SourceLifecycleTests(unittest.TestCase):
    def test_worker_deletes_source_after_rejected_font(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.font"
            source.write_bytes(b"not a font")
            parent_read_fd, parent_write_fd = os.pipe()
            try:
                status = worker_main(
                    [
                        "fontblind_worker.py",
                        "blind",
                        str(root / "output"),
                        str(root / ".result.json"),
                        str(parent_read_fd),
                        "{}",
                        str(source),
                    ]
                )
            finally:
                for descriptor in (parent_write_fd, parent_read_fd):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            self.assertEqual(status, 4)
            self.assertFalse(source.exists())
            self.assertFalse((root / ".result.json").exists())

    def test_parent_removes_failed_job_directory(self) -> None:
        store = JobStore()
        try:
            with self.assertRaises(FontBlindError):
                store.create(b"not a font")
            self.assertFalse(any(store.root.glob("*/source.font")))
            self.assertFalse(any(path.is_dir() for path in store.root.iterdir()))
        finally:
            store.close()

    def test_parent_passes_source_without_a_directory_entry(self) -> None:
        store = JobStore()
        payload = b"private font bytes"

        test_case = self

        class RejectedWorker:
            def __init__(self, command: list[str], **_kwargs: object) -> None:
                source = Path(command[-1])
                test_case.assertEqual(source.parent, Path("/dev/fd"))
                test_case.assertEqual(source.read_bytes(), payload)
                test_case.assertEqual(command[2], "blind")
                test_case.assertEqual(len(command), 8)
                test_case.assertFalse(any(store.root.glob("*/source.font")))
                test_case.assertFalse(any(store.root.glob("*/.fontblind-source-*")))
                self.returncode = 4

            def wait(self, timeout: float | None = None) -> int:
                return self.returncode

            def poll(self) -> int:
                return self.returncode

            def terminate(self) -> None:
                self.returncode = -15

            def kill(self) -> None:
                self.returncode = -9

        try:
            with (
                mock.patch("fontblind_app.subprocess.Popen", RejectedWorker),
                self.assertRaises(FontBlindError),
            ):
                store.create(payload)
        finally:
            store.close()

    def test_parent_streams_request_into_anonymous_descriptor_in_bounded_chunks(self) -> None:
        store = JobStore()
        payload = (b"private-streamed-font-bytes" * 100_000) + b"end"
        requests: list[int] = []
        test_case = self

        class BoundedReader(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                requests.append(size)
                test_case.assertGreater(size, 0)
                test_case.assertLessEqual(size, COPY_CHUNK_BYTES)
                return super().read(size)

        class RejectedWorker:
            def __init__(self, command: list[str], **_kwargs: object) -> None:
                source = Path(command[-1])
                test_case.assertEqual(source.parent, Path("/dev/fd"))
                test_case.assertEqual(source.read_bytes(), payload)
                test_case.assertFalse(any(store.root.glob("*/source.font")))
                test_case.assertFalse(any(store.root.glob("*/.fontblind-source-*")))
                self.returncode = 4

            def wait(self, timeout: float | None = None) -> int:
                return self.returncode

            def poll(self) -> int:
                return self.returncode

            def terminate(self) -> None:
                self.returncode = -15

            def kill(self) -> None:
                self.returncode = -9

        try:
            with (
                mock.patch("fontblind_app.subprocess.Popen", RejectedWorker),
                self.assertRaises(FontBlindError),
            ):
                store.create_stream(BoundedReader(payload), len(payload))
            self.assertGreater(len(requests), 1)
        finally:
            store.close()

    def test_parent_liveness_pipe_stops_worker_when_writer_disappears(self) -> None:
        parent_read_fd, parent_write_fd = os.pipe()
        code = (
            "import os,sys,threading,time; "
            "from fontblind_worker import _watch_parent; "
            "done=threading.Event(); "
            "threading.Thread(target=_watch_parent,args=(int(sys.argv[1]),done),daemon=True).start(); "
            "time.sleep(30)"
        )
        worker = subprocess.Popen(
            [str(Path(sys.executable)), "-c", code, str(parent_read_fd)],
            cwd=Path(__file__).resolve().parents[1],
            pass_fds=(parent_read_fd,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(parent_read_fd)
        os.close(parent_write_fd)
        try:
            self.assertEqual(worker.wait(timeout=3), 70)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=2)

    def test_shutdown_terminates_worker_without_double_closing_pipe(self) -> None:
        store = JobStore()
        worker_started = threading.Event()
        worker_released = threading.Event()
        close_thread_ids: list[int] = []
        real_close = os.close
        real_pipe = os.pipe
        liveness_fds: set[int] = set()

        class BlockingWorker:
            def __init__(self, _command: list[str], **_kwargs: object) -> None:
                self.returncode: int | None = None

            def wait(self, timeout: float | None = None) -> int:
                worker_started.set()
                if not worker_released.wait(timeout=timeout):
                    raise subprocess.TimeoutExpired("worker", timeout)
                assert self.returncode is not None
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def terminate(self) -> None:
                self.returncode = -15
                worker_released.set()

            def kill(self) -> None:
                self.returncode = -9
                worker_released.set()

        def tracked_close(descriptor: int) -> None:
            app_path = Path(__file__).resolve().parents[1] / "fontblind_app.py"
            if any(
                Path(frame.filename).resolve() == app_path and frame.function == "close"
                for frame in inspect.stack()
            ):
                close_thread_ids.append(descriptor)
            real_close(descriptor)

        def tracked_pipe() -> tuple[int, int]:
            descriptors = real_pipe()
            liveness_fds.update(descriptors)
            return descriptors

        errors: list[Exception] = []

        def create_job() -> None:
            try:
                store.create(b"private font bytes")
            except Exception as exc:
                errors.append(exc)

        with (
            mock.patch("fontblind_app.subprocess.Popen", BlockingWorker),
            mock.patch("fontblind_app.os.pipe", side_effect=tracked_pipe),
            mock.patch("fontblind_app.os.close", side_effect=tracked_close),
        ):
            creator = threading.Thread(target=create_job)
            creator.start()
            self.assertTrue(worker_started.wait(timeout=2))
            store.close()
            creator.join(timeout=2)

        self.assertFalse(creator.is_alive())
        self.assertTrue(errors)
        self.assertTrue(liveness_fds)
        self.assertEqual(set(close_thread_ids) & liveness_fds, set())

    def test_scavenger_only_removes_stale_owned_roots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            base = Path(temp)
            stale = base / "fontblind-local-stale"
            stale.mkdir()
            (stale / OWNERSHIP_MARKER).write_text(
                json.dumps({"version": 1, "pid": 99999999}), encoding="utf-8"
            )
            (stale / "source.font").write_bytes(b"private")

            unowned = base / "fontblind-local-unowned"
            unowned.mkdir()
            (unowned / "source.font").write_bytes(b"leave this alone")

            with mock.patch("fontblind_app._process_alive", return_value=False):
                _scavenge_stale_roots(base)

            self.assertFalse(stale.exists())
            self.assertTrue(unowned.exists())

    def test_scavenger_preserves_active_owned_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            base = Path(temp)
            active = base / "fontblind-local-active"
            active.mkdir()
            (active / OWNERSHIP_MARKER).write_text(
                json.dumps({"version": 1, "pid": os.getpid()}), encoding="utf-8"
            )
            _scavenge_stale_roots(base)
            self.assertTrue(active.exists())

    def test_idle_sweeper_expires_job_without_another_request(self) -> None:
        with (
            mock.patch("fontblind_app.SWEEP_INTERVAL_SECONDS", 0.01),
            mock.patch("fontblind_app.JOB_TTL_SECONDS", 0.01),
        ):
            store = JobStore()
            try:
                token = "a" * 32
                job_dir = store.root / token
                job_dir.mkdir()
                with store._lock:
                    store._jobs[token] = Job(
                        path=job_dir,
                        result=mock.sentinel.result,
                        created=time.monotonic() - 1,
                    )
                deadline = time.monotonic() + 1
                while job_dir.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(job_dir.exists())
            finally:
                store.close()


class ServerStartupTests(unittest.TestCase):
    def test_bind_failure_is_not_masked_by_cleanup(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            port = int(occupied.getsockname()[1])
            with self.assertRaises(OSError):
                FontBlindServer(("127.0.0.1", port), Handler)


class HttpBoundaryTests(unittest.TestCase):
    sample = Path("/System/Library/Fonts/Apple Braille Outline 6 Dot.ttf")

    def setUp(self) -> None:
        self.server = FontBlindServer(("127.0.0.1", 0), Handler)
        self.assertIsNotNone(self.server.jobs)
        self.port = int(self.server.server_port)
        self.root = self.server.jobs.root
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.assertFalse(self.root.exists())

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def session(self) -> str:
        status, _, payload = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        return str(json.loads(payload)["session"])

    def test_static_response_has_local_privacy_headers(self) -> None:
        status, headers, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"FontBlind", payload)
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(headers["Origin-Agent-Cluster"], "?1")
        self.assertIn("camera=()", headers["Permissions-Policy"])
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_invalid_host_and_missing_session_are_rejected(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest("GET", "/", skip_host=True)
            connection.putheader("Host", "example.invalid")
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 421)
            response.read()
        finally:
            connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest("GET", "/", skip_host=True)
            connection.putheader("Host", f"[::1]:{self.port}")
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 421)
            response.read()
        finally:
            connection.close()

        status, _, _ = self.request(
            "POST",
            "/api/process",
            body=b"x",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 403)

    def test_oversized_upload_is_rejected_before_body_read(self) -> None:
        status, _, _ = self.request(
            "POST",
            "/api/process",
            body=b"",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(MAX_UPLOAD_BYTES + 1),
                "X-FontBlind-Session": self.session(),
            },
        )
        self.assertEqual(status, 413)

    def test_pathological_content_length_is_rejected_without_integer_conversion(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/process",
            body=b"",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": "9" * 100,
                "X-FontBlind-Session": self.session(),
            },
        )
        self.assertEqual(status, 413)
        self.assertIn(b"too large", payload)

    def test_stalled_upload_releases_with_a_safe_error(self) -> None:
        session = self.session()
        with mock.patch("fontblind_app.UPLOAD_READ_TIMEOUT_SECONDS", 0.05):
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
                request = (
                    "POST /api/process HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.port}\r\n"
                    "Content-Type: application/octet-stream\r\n"
                    f"X-FontBlind-Session: {session}\r\n"
                    "Content-Length: 10\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                connection.sendall(request)
                response = b""
                while True:
                    block = connection.recv(4096)
                    if not block:
                        break
                    response += block
        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
        self.assertIn(b"upload was interrupted", response)
        self.assertTrue(self.server.worker_gate.acquire(blocking=False))
        self.server.worker_gate.release()

    def test_invalid_content_type_and_interrupted_upload_are_rejected(self) -> None:
        session = self.session()
        status, _, _ = self.request(
            "POST",
            "/api/process",
            body=b"font",
            headers={"Content-Type": "text/plain", "X-FontBlind-Session": session},
        )
        self.assertEqual(status, 415)

        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
            request = (
                "POST /api/process HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.port}\r\n"
                "Content-Type: application/octet-stream\r\n"
                f"X-FontBlind-Session: {session}\r\n"
                "Content-Length: 10\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii") + b"x"
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            response = b""
            while True:
                block = connection.recv(4096)
                if not block:
                    break
                response += block
        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
        self.assertIn(b"upload was interrupted", response)

    def test_lab_boundaries_reject_invalid_angle_and_binary_framing(self) -> None:
        session = self.session()
        status, _, payload = self.request(
            "POST",
            "/api/lab/oblique",
            body=b"font",
            headers={
                "Content-Type": "application/octet-stream",
                "X-FontBlind-Session": session,
                "X-FontBlind-Angle": "30",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn(b"between 4 and 20", payload)

        status, _, payload = self.request(
            "POST",
            "/api/lab/variable",
            body=b"not-a-font-set",
            headers={
                "Content-Type": FONT_SET_MEDIA_TYPE,
                "X-FontBlind-Session": session,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Invalid local Lab request", payload)

        status, _, payload = self.request(
            "POST",
            "/api/lab/variable",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-FontBlind-Session": session,
            },
        )
        self.assertEqual(status, 415)
        self.assertIn(b"Invalid local Lab request", payload)

    def test_worker_gate_rejects_a_parallel_heavy_build(self) -> None:
        self.assertTrue(self.server.worker_gate.acquire(blocking=False))
        try:
            status, _, payload = self.request(
                "POST",
                "/api/process",
                body=b"font",
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-FontBlind-Session": self.session(),
                },
            )
        finally:
            self.server.worker_gate.release()
        self.assertEqual(status, 429)
        self.assertIn(b"Another local build", payload)

    def test_lab_endpoints_build_anonymous_oblique_and_variable_packages(self) -> None:
        from tests.test_lab import write_fixture_font

        with tempfile.TemporaryDirectory(prefix="fontblind-http-lab-") as temp_text:
            root = Path(temp_text)
            regular = root / "revealing-regular-name.ttf"
            bold = root / "revealing-bold-name.ttf"
            write_fixture_font(regular, weight=400, family="Revealing Regular Origin")
            write_fixture_font(bold, weight=700, family="Revealing Bold Origin")
            session = self.session()

            status, _, payload = self.request(
                "POST",
                "/api/lab/oblique",
                body=regular.read_bytes(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-FontBlind-Session": session,
                    "X-FontBlind-Angle": "12",
                },
            )
            self.assertEqual(status, 200)
            oblique = json.loads(payload)
            self.assertEqual(oblique["native"]["filename"], "fontlab-oblique.ttf")
            self.assertNotIn("revealing", payload.decode("utf-8").casefold())

            status, _, payload = self.request(
                "POST",
                "/api/lab/oblique",
                body=regular.read_bytes(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-FontBlind-Session": session,
                    "X-FontBlind-Angle": "12",
                    "X-FontBlind-Output": "slnt",
                },
            )
            self.assertEqual(status, 200)
            slant = json.loads(payload)
            self.assertEqual(slant["native"]["filename"], "fontlab-slant-variable.ttf")
            self.assertEqual([axis["tag"] for axis in slant["axes"]], ["slnt"])
            self.assertNotIn("revealing", payload.decode("utf-8").casefold())

            request = pack_font_set([regular.read_bytes(), bold.read_bytes()])
            status, _, payload = self.request(
                "POST",
                "/api/lab/variable",
                body=request,
                headers={
                    "Content-Type": FONT_SET_MEDIA_TYPE,
                    "X-FontBlind-Session": session,
                },
            )
            self.assertEqual(status, 200)
            variable = json.loads(payload)
            self.assertEqual(variable["native"]["filename"], "fontlab-variable.ttf")
            self.assertEqual([axis["tag"] for axis in variable["axes"]], ["wght"])
            self.assertEqual(len(variable["masters"]), 2)
            self.assertEqual(sum(bool(master["default"]) for master in variable["masters"]), 1)
            self.assertNotIn("revealing", payload.decode("utf-8").casefold())

    def test_variable_lab_returns_anonymous_actionable_compatibility_diagnostic(self) -> None:
        from tests.test_lab import write_fixture_font

        with tempfile.TemporaryDirectory(prefix="fontblind-http-lab-") as temp_text:
            root = Path(temp_text)
            first = root / "revealing-first-name.ttf"
            second = root / "revealing-second-name.ttf"
            write_fixture_font(first, weight=400, width_class=3, family="Revealing One")
            write_fixture_font(second, weight=700, width_class=5, family="Revealing Two")
            body = pack_font_set([first.read_bytes(), second.read_bytes()])
            status, _, payload = self.request(
                "POST",
                "/api/lab/variable",
                body=body,
                headers={
                    "Content-Type": FONT_SET_MEDIA_TYPE,
                    "X-FontBlind-Session": self.session(),
                },
            )
            self.assertEqual(status, 422)
            diagnostic = payload.decode("utf-8").casefold()
            self.assertIn("independent weight and width extremes", diagnostic)
            self.assertNotIn("revealing", diagnostic)

    @unittest.skipUnless(sample.is_file(), "local strict-compatible TTF sample unavailable")
    def test_process_download_delete_exposes_no_source_identity(self) -> None:
        session = self.session()
        status, _, payload = self.request(
            "POST",
            "/api/process",
            body=self.sample.read_bytes(),
            headers={
                "Content-Type": "application/octet-stream",
                "X-FontBlind-Session": session,
            },
        )
        self.assertEqual(status, 200)
        result = json.loads(payload)
        token = str(result["job"])
        public_text = payload.decode("utf-8")
        self.assertNotIn(self.sample.name, public_text)
        self.assertNotIn(str(self.sample), public_text)
        self.assertNotIn("sha256", public_text.casefold())
        self.assertNotIn('"source":', public_text.casefold())
        self.assertFalse((self.root / token / "source.font").exists())

        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("download buffered whole output")):
            status, headers, native = self.request("GET", result["native"]["url"])
        self.assertEqual(status, 200)
        self.assertGreater(len(native), 100)
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="fontblind-native.ttf"')
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")

        status, _, payload = self.request(
            "DELETE",
            f"/api/jobs/{token}",
            headers={"X-FontBlind-Session": session},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["deleted"])
        status, _, _ = self.request("GET", result["native"]["url"])
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
