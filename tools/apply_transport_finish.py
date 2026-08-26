from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


write(
    "fontblind_stream.py",
    '''"""Small, bounded copies from local HTTP bodies into anonymous descriptors."""
from __future__ import annotations

import math
import time
from typing import BinaryIO


COPY_CHUNK_BYTES = 1024 * 1024
COPY_DEADLINE_SECONDS = 60.0


class StreamInterruptedError(EOFError):
    """A declared local upload ended or stalled before all bytes arrived."""


def _write_all(target: BinaryIO, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = target.write(view)
        if written is None or written <= 0:
            raise OSError("Could not write the local upload to its anonymous descriptor")
        view = view[written:]


def copy_exact(
    source: BinaryIO,
    target: BinaryIO,
    length: int,
    *,
    chunk_size: int = COPY_CHUNK_BYTES,
    deadline_seconds: float = COPY_DEADLINE_SECONDS,
) -> int:
    """Copy exactly ``length`` bytes within fixed memory and wall-clock bounds."""
    size = int(length)
    block_size = int(chunk_size)
    deadline = float(deadline_seconds)
    if size <= 0:
        raise ValueError("Upload length must be positive")
    if block_size <= 0 or block_size > COPY_CHUNK_BYTES:
        raise ValueError("Upload chunk size exceeds the local memory boundary")
    if not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("Upload deadline must be a positive finite number")

    started = time.monotonic()
    copied = 0
    while copied < size:
        if time.monotonic() - started >= deadline:
            raise StreamInterruptedError("The local upload timed out")
        request = min(block_size, size - copied)
        try:
            block = source.read(request)
        except OSError as exc:
            raise StreamInterruptedError("The local upload was interrupted") from exc
        if time.monotonic() - started >= deadline:
            raise StreamInterruptedError("The local upload timed out")
        if block is None or not block:
            raise StreamInterruptedError("The local upload was interrupted")
        if len(block) > request:
            raise StreamInterruptedError("The local upload violated bounded framing")
        _write_all(target, block)
        copied += len(block)
    return copied
''',
)

protocol = read("fontblind_protocol.py")
protocol = replace_once(
    protocol,
    '''    value = raw_value.strip()
    if not value or not value.isascii() or not value.isdecimal():
        raise FontSetError("The local Lab request has an invalid content length.")
    length = int(value)
''',
    '''    value = raw_value.strip()
    if not value or not value.isascii() or not value.isdecimal():
        raise FontSetError("The local Lab request has an invalid content length.")
    if len(value) > 20:
        raise FontSetTooLargeError("The selected masters exceed the 256 MB local limit.")
    length = int(value)
''',
    label="protocol huge decimal guard",
)
write("fontblind_protocol.py", protocol)

app = read("fontblind_app.py")
app = replace_once(
    app,
    "from fontblind_stream import StreamInterruptedError, copy_exact\n",
    "from fontblind_stream import COPY_CHUNK_BYTES, StreamInterruptedError, copy_exact\n",
    label="app copy chunk import",
)
app = replace_once(
    app,
    "WORKER_TIMEOUT_SECONDS = 5 * 60\n",
    "WORKER_TIMEOUT_SECONDS = 5 * 60\nUPLOAD_READ_TIMEOUT_SECONDS = 30.0\n",
    label="app upload timeout constant",
)
app = replace_once(
    app,
    '''    def _headers(self, status: int, media_type: str, length: int | None = None) -> None:
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
''',
    '''    def _headers(
        self,
        status: int,
        media_type: str,
        length: int | None = None,
        disposition: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        if disposition is not None:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Origin-Agent-Cluster", "?1")
        self.send_header(
            "Permissions-Policy",
            "camera=(), display-capture=(), geolocation=(), microphone=(), payment=(), usb=()",
        )
''',
    label="app hardened response headers",
)
app = replace_once(
    app,
    '''    def _body_length(self, maximum: int, empty_error: str) -> int | None:
        raw_value = self.headers.get("Content-Length")
        if raw_value is None:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": empty_error})
            return None
        value = raw_value.strip()
        if not value or not value.isascii() or not value.isdecimal():
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid local upload framing."})
            return None
        length = int(value)
''',
    '''    def _body_length(self, maximum: int, empty_error: str) -> int | None:
        raw_value = self.headers.get("Content-Length")
        if raw_value is None:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": empty_error})
            return None
        value = raw_value.strip()
        if not value or not value.isascii() or not value.isdecimal():
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid local upload framing."})
            return None
        if len(value) > 20:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Local input is too large."})
            return None
        length = int(value)
''',
    label="app huge decimal guard",
)
app = replace_once(
    app,
    '''    def _public_result(self, token: str, job: Job) -> None:
''',
    '''    def _download_file(self, target: Path, media_type: str, filename: str) -> None:
        try:
            stream = target.open("rb")
            length = os.fstat(stream.fileno()).st_size
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Output unavailable."})
            return
        with stream:
            self._headers(
                HTTPStatus.OK,
                media_type,
                length,
                f'attachment; filename="{filename}"',
            )
            try:
                shutil.copyfileobj(stream, self.wfile, length=COPY_CHUNK_BYTES)
            except (BrokenPipeError, ConnectionResetError):
                # The browser cancelled a local download. The stored output
                # remains available until explicit reset or normal expiry.
                return

    def _public_result(self, token: str, job: Job) -> None:
''',
    label="app streamed download helper",
)
app = replace_once(
    app,
    '''            item = getattr(job.result, kind)
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
''',
    '''            item = getattr(job.result, kind)
            target = job.path / "output" / item.filename
            self._download_file(target, item.media_type, item.filename)
            return
''',
    label="app replace buffered download",
)
app = replace_once(
    app,
    '''        try:
            try:
                media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
''',
    '''        try:
            try:
                self.connection.settimeout(UPLOAD_READ_TIMEOUT_SECONDS)
                media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
''',
    label="app stalled upload timeout",
)
write("fontblind_app.py", app)

stream_test = read("tests/test_stream.py")
stream_test = replace_once(
    stream_test,
    '''class ShortWriter(io.BytesIO):
''',
    '''class FailingReader(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        raise TimeoutError("simulated stalled local upload")


class ShortWriter(io.BytesIO):
''',
    label="stream failing reader",
)
stream_test = replace_once(
    stream_test,
    '''    def test_interrupted_upload_fails_closed(self) -> None:
        with self.assertRaises(StreamInterruptedError):
            copy_exact(io.BytesIO(b"short"), io.BytesIO(), 20)
''',
    '''    def test_interrupted_upload_fails_closed(self) -> None:
        with self.assertRaises(StreamInterruptedError):
            copy_exact(io.BytesIO(b"short"), io.BytesIO(), 20)
        with self.assertRaises(StreamInterruptedError):
            copy_exact(FailingReader(b"font"), io.BytesIO(), 4)
        with self.assertRaises(StreamInterruptedError):
            copy_exact(io.BytesIO(b"font"), io.BytesIO(), 4, deadline_seconds=1e-12)
''',
    label="stream timeout tests",
)
stream_test = replace_once(
    stream_test,
    '''        for chunk_size in (0, COPY_CHUNK_BYTES + 1):
            with self.subTest(chunk_size=chunk_size), self.assertRaises(ValueError):
                copy_exact(io.BytesIO(b"font"), io.BytesIO(), 4, chunk_size=chunk_size)
''',
    '''        for chunk_size in (0, COPY_CHUNK_BYTES + 1):
            with self.subTest(chunk_size=chunk_size), self.assertRaises(ValueError):
                copy_exact(io.BytesIO(b"font"), io.BytesIO(), 4, chunk_size=chunk_size)
        for deadline in (0, -1, float("inf"), float("nan")):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                copy_exact(io.BytesIO(b"font"), io.BytesIO(), 4, deadline_seconds=deadline)
''',
    label="stream deadline validation",
)
write("tests/test_stream.py", stream_test)

protocol_test = read("tests/test_protocol.py")
protocol_test = replace_once(
    protocol_test,
    '''    def test_rejects_large_declared_fonts_without_allocating_them(self) -> None:
''',
    '''    def test_rejects_huge_decimal_content_length_without_integer_conversion(self) -> None:
        with self.assertRaises(FontSetTooLargeError):
            read_font_set_header(io.BytesIO(b""), "9" * 100_000)

    def test_rejects_large_declared_fonts_without_allocating_them(self) -> None:
''',
    label="protocol huge decimal test",
)
write("tests/test_protocol.py", protocol_test)

app_test = read("tests/test_app.py")
app_test = replace_once(
    app_test,
    '''        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
''',
    '''        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(headers["Origin-Agent-Cluster"], "?1")
        self.assertIn("camera=()", headers["Permissions-Policy"])
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
''',
    label="app security header tests",
)
app_test = replace_once(
    app_test,
    '''    def test_invalid_content_type_and_interrupted_upload_are_rejected(self) -> None:
''',
    '''    def test_pathological_content_length_is_rejected_without_integer_conversion(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/process",
            body=b"",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": "9" * 100_000,
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
                    "POST /api/process HTTP/1.1\\r\\n"
                    f"Host: 127.0.0.1:{self.port}\\r\\n"
                    "Content-Type: application/octet-stream\\r\\n"
                    f"X-FontBlind-Session: {session}\\r\\n"
                    "Content-Length: 10\\r\\n"
                    "Connection: close\\r\\n\\r\\n"
                ).encode("ascii")
                connection.sendall(request)
                response = b""
                while True:
                    block = connection.recv(4096)
                    if not block:
                        break
                    response += block
        self.assertIn(b" 400 ", response.split(b"\\r\\n", 1)[0])
        self.assertIn(b"upload was interrupted", response)
        self.assertTrue(self.server.worker_gate.acquire(blocking=False))
        self.server.worker_gate.release()

    def test_invalid_content_type_and_interrupted_upload_are_rejected(self) -> None:
''',
    label="app pathological and stall tests",
)
app_test = replace_once(
    app_test,
    '''        status, headers, native = self.request("GET", result["native"]["url"])
        self.assertEqual(status, 200)
''',
    '''        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("download buffered whole output")):
            status, headers, native = self.request("GET", result["native"]["url"])
        self.assertEqual(status, 200)
''',
    label="app streamed download test",
)
app_test = replace_once(
    app_test,
    '''        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="fontblind-native.ttf"')
''',
    '''        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="fontblind-native.ttf"')
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
''',
    label="app download headers test",
)
write("tests/test_app.py", app_test)

readme = read("README.md")
readme = replace_once(
    readme,
    '''Browser uploads retain only a four-byte signature probe; complete fonts then stream into anonymous local descriptors in bounded 1 MB chunks.''',
    '''Browser uploads retain only a four-byte signature probe; complete fonts then stream into anonymous local descriptors in bounded 1 MB chunks with read and total-time ceilings. Downloads stream back in the same bounded chunks rather than becoming a second full in-memory copy.''',
    label="README bidirectional streaming",
)
write("README.md", readme)

changelog = read("CHANGELOG.md")
changelog = replace_once(
    changelog,
    '''- Stream every browser upload into anonymous descriptors in 1 MB chunks instead of buffering whole fonts in browser and server memory.
''',
    '''- Stream every browser upload into anonymous descriptors in 1 MB chunks instead of buffering whole fonts in browser and server memory.
- Bound stalled or pathological upload framing, and stream completed downloads without whole-file server buffers.
''',
    label="changelog transport finish",
)
write("CHANGELOG.md", changelog)

docs = read("docs/LAB_HARDENING.md")
docs = replace_once(
    docs,
    '''After the small header is accepted, donor bytes stream directly from the local request into anonymous descriptors in 1 MB chunks. Blind and Oblique uploads use the same bounded path. The browser reads only each file's four-byte SFNT signature before handing the original `File` or composite `Blob` to `fetch`; the Python parent never materializes a complete browser upload as a `bytes` list.
''',
    '''After the small header is accepted, donor bytes stream directly from the local request into anonymous descriptors in 1 MB chunks. Blind and Oblique uploads use the same bounded path. The browser reads only each file's four-byte SFNT signature before handing the original `File` or composite `Blob` to `fetch`; the Python parent never materializes a complete browser upload as a `bytes` list. Socket reads and total copies have deadlines, pathological decimal lengths fail before integer conversion, and completed files stream back to the browser in bounded chunks.
''',
    label="docs transport finish",
)
write("docs/LAB_HARDENING.md", docs)
