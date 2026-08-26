from __future__ import annotations

import textwrap
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
pipeline = read("fontblind_pipeline.py")
pipeline = replace_once(
    pipeline,
    '    axes: tuple[dict[str, object], ...] = ()\n',
    '    axes: tuple[dict[str, object], ...] = ()\n'
    '    masters: tuple[dict[str, object], ...] = ()\n',
    label="pipeline masters field",
)
pipeline = replace_once(
    pipeline,
    '        if self.axes:\n'
    '            result["axes"] = [dict(axis) for axis in self.axes]\n'
    '        return result\n',
    '        if self.axes:\n'
    '            result["axes"] = [dict(axis) for axis in self.axes]\n'
    '        if self.masters:\n'
    '            result["masters"] = [dict(master) for master in self.masters]\n'
    '        return result\n',
    label="pipeline public masters",
)
old_from_internal = '''    def to_internal_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_internal_dict(cls, value: dict[str, object]) -> "PublicBuildResult":
        return cls(
            native=OutputFile(**value["native"]),
            web=OutputFile(**value["web"]),
            css=OutputFile(**value["css"]),
            bundle=OutputFile(**value["bundle"]),
            flavor=str(value["flavor"]),
            variable=bool(value["variable"]),
            color=bool(value["color"]),
            checks={str(key): bool(item) for key, item in dict(value["checks"]).items()},
            axes=tuple(dict(axis) for axis in value.get("axes", ())),
        )
'''
new_from_internal = '''    def to_internal_dict(self) -> dict[str, object]:
        return asdict(self)

    def require_verified(self) -> None:
        if not self.checks:
            raise ValueError("FontBlind worker returned no verification proof")
        for key, passed in self.checks.items():
            if not isinstance(key, str) or not key or type(passed) is not bool:
                raise ValueError("FontBlind worker returned malformed verification proof")
            if passed is not True:
                raise ValueError("FontBlind worker returned a failed verification proof")

    @classmethod
    def from_internal_dict(cls, value: dict[str, object]) -> "PublicBuildResult":
        raw_checks = value["checks"]
        if not isinstance(raw_checks, dict):
            raise ValueError("FontBlind worker returned malformed verification proof")
        checks: dict[str, bool] = {}
        for key, item in raw_checks.items():
            if not isinstance(key, str) or not key or type(item) is not bool:
                raise ValueError("FontBlind worker returned malformed verification proof")
            checks[key] = item

        raw_axes = value.get("axes", ())
        raw_masters = value.get("masters", ())
        if not isinstance(raw_axes, (list, tuple)) or not isinstance(raw_masters, (list, tuple)):
            raise ValueError("FontBlind worker returned malformed Lab inspection data")
        result = cls(
            native=OutputFile(**dict(value["native"])),
            web=OutputFile(**dict(value["web"])),
            css=OutputFile(**dict(value["css"])),
            bundle=OutputFile(**dict(value["bundle"])),
            flavor=str(value["flavor"]),
            variable=bool(value["variable"]),
            color=bool(value["color"]),
            checks=checks,
            axes=tuple(dict(axis) for axis in raw_axes),
            masters=tuple(dict(master) for master in raw_masters),
        )
        result.require_verified()
        return result
'''
pipeline = replace_once(pipeline, old_from_internal, new_from_internal, label="pipeline strict internal result")
write("fontblind_pipeline.py", pipeline)

worker = read("fontblind_worker.py")
worker = replace_once(
    worker,
    'import threading\nfrom pathlib import Path\n',
    'import threading\nfrom dataclasses import replace\nfrom pathlib import Path\n',
    label="worker dataclasses import",
)
worker = replace_once(
    worker,
    'from fontblind_pipeline import build_browser_outputs\n',
    'from fontblind_mastermap import anonymous_slant_masters, anonymous_variable_masters\n'
    'from fontblind_pipeline import build_browser_outputs\n',
    label="worker master map import",
)
worker = replace_once(
    worker,
    '''                if options.get("output") == "slnt":
                    result = build_slant_variable_outputs(sources[0], output_dir, angle=angle)
                else:
                    result = build_oblique_outputs(sources[0], output_dir, angle=angle)
            else:
                from fontblind_lab import build_variable_outputs

                result = build_variable_outputs(sources, output_dir)
''',
    '''                if options.get("output") == "slnt":
                    result = build_slant_variable_outputs(sources[0], output_dir, angle=angle)
                    result = replace(result, masters=anonymous_slant_masters(angle))
                else:
                    result = build_oblique_outputs(sources[0], output_dir, angle=angle)
            else:
                from fontblind_lab import build_variable_outputs

                result = build_variable_outputs(sources, output_dir)
                result = replace(result, masters=anonymous_variable_masters(sources, result.axes))
''',
    label="worker attach master maps",
)
worker = replace_once(
    worker,
    '''        temporary.write_text(
            json.dumps(result.to_internal_dict(), separators=(",", ":"), ensure_ascii=False),
''',
    '''        result.require_verified()
        temporary.write_text(
            json.dumps(result.to_internal_dict(), separators=(",", ":"), ensure_ascii=False),
''',
    label="worker verify result",
)
write("fontblind_worker.py", worker)

app = read("fontblind_app.py")
app = replace_once(
    app,
    'import argparse\nimport base64\nimport binascii\nfrom contextlib import ExitStack\n',
    'import argparse\nfrom contextlib import ExitStack\n',
    label="app remove base64 imports",
)
app = replace_once(
    app,
    'from fontblind_pipeline import PublicBuildResult\n',
    'from fontblind_pipeline import PublicBuildResult\n'
    'from fontblind_protocol import (\n'
    '    FONT_SET_MEDIA_TYPE,\n'
    '    MAX_FONT_BYTES,\n'
    '    MAX_FONT_SET_BYTES,\n'
    '    FontSetError,\n'
    '    FontSetTooLargeError,\n'
    '    read_font_set,\n'
    ')\n',
    label="app protocol imports",
)
app = replace_once(
    app,
    'MAX_UPLOAD_BYTES = 128 * 1024 * 1024\n'
    'MAX_VARIABLE_BODY_BYTES = 350 * 1024 * 1024\n'
    'MAX_VARIABLE_TOTAL_BYTES = 256 * 1024 * 1024\n',
    'MAX_UPLOAD_BYTES = MAX_FONT_BYTES\n'
    'MAX_VARIABLE_TOTAL_BYTES = MAX_FONT_SET_BYTES\n',
    label="app shared limits",
)
app = replace_once(
    app,
    '            self.session_secret = secrets.token_urlsafe(32)\n',
    '            self.session_secret = secrets.token_urlsafe(32)\n'
    '            self.worker_gate = threading.BoundedSemaphore(value=1)\n',
    label="app worker gate",
)
app = replace_once(
    app,
    '            "/favicon.svg": (WEB_ROOT / "favicon.svg", "image/svg+xml"),\n',
    '            "/favicon.svg": (WEB_ROOT / "favicon.svg", "image/svg+xml"),\n'
    '            "/lab-map.css": (WEB_ROOT / "lab-map.css", "text/css; charset=utf-8"),\n',
    label="app lab map asset",
)
old_variable_request = '''            else:
                if media_type != "application/json":
                    self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "Invalid local Lab request."})
                    return
                encoded = self._read_body(MAX_VARIABLE_BODY_BYTES, "Choose between two and twelve compatible font masters.")
                if encoded is None:
                    return
                try:
                    request = json.loads(encoded)
                    fonts = request["fonts"]
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid local Lab request."})
                    return
                if not isinstance(fonts, list) or not 2 <= len(fonts) <= 12 or not all(isinstance(item, str) for item in fonts):
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Choose between two and twelve compatible font masters."})
                    return
                try:
                    payloads = [base64.b64decode(item, validate=True) for item in fonts]
                except (binascii.Error, ValueError, TypeError):
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid local Lab request."})
                    return
                if any(not payload or len(payload) > MAX_UPLOAD_BYTES for payload in payloads):
                    self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "A font exceeds the 128 MB local limit."})
                    return
                if sum(map(len, payloads)) > MAX_VARIABLE_TOTAL_BYTES:
                    self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "The selected masters exceed the 256 MB local limit."})
                    return
                token, job = self.server.jobs.create_variable(payloads)
'''
new_variable_request = '''            else:
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
app = replace_once(app, old_variable_request, new_variable_request, label="app binary variable request")
app = replace_once(
    app,
    '''            axes=result.axes,
        )
        job = Job(path=job_dir, result=result, created=time.monotonic())
''',
    '''            axes=result.axes,
            masters=result.masters,
        )
        result.require_verified()
        job = Job(path=job_dir, result=result, created=time.monotonic())
''',
    label="app retain masters and verify",
)
post_start = app.index("    def do_POST(self) -> None:")
segment_start = app.index(
    '        try:\n            media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()\n',
    post_start,
)
segment_end_marker = '        self._public_result(token, job)\n'
segment_end = app.index(segment_end_marker, segment_start) + len(segment_end_marker)
segment = app[segment_start:segment_end]
wrapped = (
    '        if not self.server.worker_gate.acquire(blocking=False):\n'
    '            self._json(\n'
    '                HTTPStatus.TOO_MANY_REQUESTS,\n'
    '                {"ok": False, "error": "Another local build is already running. Finish or reset it before starting another."},\n'
    '            )\n'
    '            return\n'
    '        try:\n'
    + textwrap.indent(segment, "    ")
    + '        finally:\n'
    '            self.server.worker_gate.release()\n'
)
app = app[:segment_start] + wrapped + app[segment_end:]
write("fontblind_app.py", app)
