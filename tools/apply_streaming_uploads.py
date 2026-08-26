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


app = read("fontblind_app.py")
app = replace_once(
    app,
    "from typing import Any\n",
    "from typing import Any, BinaryIO, Callable, Sequence\n",
    label="stream typing imports",
)
app = replace_once(
    app,
    "    read_font_set,\n",
    "    read_font_set_header,\n",
    label="stream protocol import",
)
app = replace_once(
    app,
    "from fontblind_surgical import FontBlindError\n",
    "from fontblind_stream import StreamInterruptedError, copy_exact\n"
    "from fontblind_surgical import FontBlindError\n",
    label="stream helper import",
)
app = replace_once(
    app,
    '''    def create(self, payload: bytes) -> tuple[str, Job]:
        return self._create("blind", [payload], {})

    def create_oblique(self, payload: bytes, angle: float, output: str = "static") -> tuple[str, Job]:
        return self._create("oblique", [payload], {"angle": angle, "output": output})

    def create_variable(self, payloads: list[bytes]) -> tuple[str, Job]:
        return self._create("variable", payloads, {})
''',
    '''    def create(self, payload: bytes) -> tuple[str, Job]:
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
''',
    label="stream job APIs",
)
start = app.index("    def _create(self, mode: str, payloads: list[bytes], options: dict[str, object]) -> tuple[str, Job]:")
end = app.index("\n    def get(self, token: str) -> Job | None:", start)
new_create = '''    def _validated_source_lengths(self, mode: str, lengths: Sequence[int]) -> tuple[int, ...]:
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
'''
app = app[:start] + new_create + app[end:]
old_reader_start = app.index("    def _read_body(self, maximum: int, empty_error: str) -> bytes | None:")
old_reader_end = app.index("\n    def _public_result", old_reader_start)
new_reader = '''    def _body_length(self, maximum: int, empty_error: str) -> int | None:
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
'''
app = app[:old_reader_start] + new_reader + app[old_reader_end:]
old_post = '''                if path in {"/api/process", "/api/lab/oblique"}:
                    if media_type != "application/octet-stream":
                        self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "Invalid local upload."})
                        return
                    payload = self._read_body(MAX_UPLOAD_BYTES, "Choose a TTF or OTF first.")
                    if payload is None:
                        return
                    if path == "/api/process":
                        token, job = self.server.jobs.create(payload)
                    else:
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
                        token, job = self.server.jobs.create_oblique(payload, angle, output)
                else:
                    if media_type != FONT_SET_MEDIA_TYPE:
                        self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "Invalid local Lab request."})
                        return
                    try:
                        payloads = read_font_set(self.rfile, self.headers.get("Content-Length"))
                    except FontSetTooLargeError as exc:
                        self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": str(exc)})
                        return
                    except FontSetError:
                        self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid local Lab request."})
                        return
                    token, job = self.server.jobs.create_variable(payloads)
'''
new_post = '''                if path in {"/api/process", "/api/lab/oblique"}:
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
'''
app = replace_once(app, old_post, new_post, label="streaming endpoints")
write("fontblind_app.py", app)

app_js = read("web/app.js")
app_js = replace_once(
    app_js,
    '''async function fileLooksLikeTrueType(file) {
  const header = await file.slice(0, 4).arrayBuffer();
  try {
    return looksLikeTrueType(header);
  } finally {
    wipe(header);
  }
}
''',
    '''async function fileLooksLikeOpenType(file) {
  const header = await file.slice(0, 4).arrayBuffer();
  try {
    return looksLikeOpenType(header);
  } finally {
    wipe(header);
  }
}

async function fileLooksLikeTrueType(file) {
  const header = await file.slice(0, 4).arrayBuffer();
  try {
    return looksLikeTrueType(header);
  } finally {
    wipe(header);
  }
}
''',
    label="browser header probes",
)
process_start = app_js.index("async function processSingle(name, file, endpoint, extraHeaders = {}) {")
process_end = app_js.index("\nasync function processVariable(files) {", process_start)
new_process = '''async function processSingle(name, file, endpoint, extraHeaders = {}) {
  if (!file || file.size === 0) {
    const message = name === "oblique"
      ? "Choose one non-empty standalone TTF. No output was kept."
      : "Choose one non-empty TTF or OTF. No output was kept.";
    fail(name, message);
    return;
  }
  if (file.size > MAX_FONT_BYTES) {
    fail(name, "This font exceeds the 128 MB local limit. No output was kept.");
    return;
  }

  setView(name, "processing");
  let body = file;
  file = null;
  try {
    const validFont = name === "oblique"
      ? await fileLooksLikeTrueType(body)
      : await fileLooksLikeOpenType(body);
    if (!validFont) {
      const message = name === "oblique"
        ? "Oblique Lab accepts standalone TrueType fonts only. No output was kept."
        : "That is not a standalone TTF or OTF font. No output was kept.";
      throw new SafeMessage(message);
    }
    const data = await postLocal(endpoint, {
      "Content-Type": "application/octet-stream",
      ...extraHeaders
    }, body);
    body = null;
    const isSlantVariable = name === "oblique" && Array.isArray(data.axes) && data.axes.some((axis) => axis.tag === "slnt");
    if (name === "oblique") configureObliqueResult(isSlantVariable);
    const context = name === "oblique"
      ? isSlantVariable
        ? `Built a live 0° to ${extraHeaders["X-FontBlind-Angle"]}° mechanical slant range. Still Oblique, never a designed Italic.`
        : `Built at ${extraHeaders["X-FontBlind-Angle"]}°. This is an Oblique, not a designed Italic.`
      : null;
    await acceptResult(name, data, context);
  } catch (error) {
    body = null;
    fail(name, error instanceof SafeMessage && error.message ? error.message : DEFAULT_ERRORS[name]);
  }
}
'''
app_js = app_js[:process_start] + new_process + app_js[process_end:]
write("web/app.js", app_js)

pyproject = read("pyproject.toml")
pyproject = replace_once(
    pyproject,
    '  "fontblind_surgical",\n',
    '  "fontblind_stream",\n  "fontblind_surgical",\n',
    label="stream package module",
)
write("pyproject.toml", pyproject)

protocol_test = read("tests/test_protocol.py")
protocol_test = replace_once(
    protocol_test,
    '    read_font_set,\n',
    '    read_font_set,\n    read_font_set_header,\n',
    label="protocol header test import",
)
protocol_test = replace_once(
    protocol_test,
    '''    def test_roundtrip_preserves_raw_donor_bytes(self) -> None:
        payloads = [b"\\x00\\x01\\x00\\x00first", b"OTTOsecond"]
        body = pack_font_set(payloads)
        self.assertEqual(read_font_set(io.BytesIO(body), str(len(body))), payloads)
        self.assertLess(len(body), sum(map(len, payloads)) + 32)
''',
    '''    def test_roundtrip_preserves_raw_donor_bytes(self) -> None:
        payloads = [b"\\x00\\x01\\x00\\x00first", b"OTTOsecond"]
        body = pack_font_set(payloads)
        stream = io.BytesIO(body)
        lengths = read_font_set_header(stream, str(len(body)))
        self.assertEqual(lengths, tuple(map(len, payloads)))
        self.assertEqual(stream.read(), b"".join(payloads))
        self.assertEqual(read_font_set(io.BytesIO(body), str(len(body))), payloads)
        self.assertLess(len(body), sum(map(len, payloads)) + 32)
''',
    label="protocol streamed header test",
)
write("tests/test_protocol.py", protocol_test)

test_app = read("tests/test_app.py")
test_app = replace_once(test_app, "import json\n", "import io\nimport json\n", label="app stream test io")
test_app = replace_once(
    test_app,
    "from fontblind_surgical import FontBlindError\n",
    "from fontblind_stream import COPY_CHUNK_BYTES\nfrom fontblind_surgical import FontBlindError\n",
    label="app stream test import",
)
insert_at = test_app.index("    def test_parent_liveness_pipe_stops_worker_when_writer_disappears(self) -> None:")
stream_test = '''    def test_parent_streams_request_into_anonymous_descriptor_in_bounded_chunks(self) -> None:
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

'''
test_app = test_app[:insert_at] + stream_test + test_app[insert_at:]
write("tests/test_app.py", test_app)

readme = read("README.md")
readme = replace_once(
    readme,
    '''A deterministic proof grid renders min, default, max, and necessary midpoint combinations through the generated WOFF2, marks exact masters, and lets every proof tile drive the live axis controls. Variable donors travel in a compact length-prefixed binary envelope rather than base64 JSON, and the app permits one heavy build at a time to keep local memory bounded.''',
    '''A deterministic proof grid renders min, default, max, and necessary midpoint combinations through the generated WOFF2, marks exact masters, and lets every proof tile drive the live axis controls. Browser uploads retain only a four-byte signature probe; complete fonts then stream into anonymous local descriptors in bounded 1 MB chunks. Variable donors travel in a compact length-prefixed binary envelope rather than base64 JSON, and the app permits one heavy build at a time to keep local memory bounded.''',
    label="README streaming contract",
)
write("README.md", readme)

changelog = read("CHANGELOG.md")
changelog = replace_once(
    changelog,
    "- Replace Variable Lab's base64/JSON donor upload with a bounded binary font-set envelope.\n",
    "- Replace Variable Lab's base64/JSON donor upload with a bounded binary font-set envelope.\n"
    "- Stream every browser upload into anonymous descriptors in 1 MB chunks instead of buffering whole fonts in browser and server memory.\n",
    label="changelog streaming",
)
write("CHANGELOG.md", changelog)

docs = read("docs/LAB_HARDENING.md")
docs = replace_once(
    docs,
    '''The server validates the declared body length, count, every donor length, aggregate length, signature, and exact framing before compilation. It rejects malformed, interrupted, oversized, or trailing data. The envelope removes base64 expansion and avoids the second full JSON string copy in the browser.
''',
    '''The server validates the declared body length, count, every donor length, aggregate length, signature, and exact framing before compilation. It rejects malformed, interrupted, oversized, or trailing data. The envelope removes base64 expansion and avoids the second full JSON string copy in the browser.

After the small header is accepted, donor bytes stream directly from the local request into anonymous descriptors in 1 MB chunks. Blind and Oblique uploads use the same bounded path. The browser reads only each file's four-byte SFNT signature before handing the original `File` or composite `Blob` to `fetch`; the Python parent never materializes a complete browser upload as a `bytes` list.
''',
    label="docs streaming section",
)
write("docs/LAB_HARDENING.md", docs)
