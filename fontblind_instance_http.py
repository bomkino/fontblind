"""HTTP boundary for freezing verified Lab positions into static fonts."""
from __future__ import annotations

import io
import json
import math
import os
import re
from http import HTTPStatus
from urllib.parse import urlsplit

from fontblind_app import (
    MAX_UPLOAD_BYTES,
    UPLOAD_READ_TIMEOUT_SECONDS,
    WEB_ROOT,
    Handler,
    LabRequestError,
)
from fontblind_stream import StreamInterruptedError, copy_exact
from fontblind_surgical import FontBlindError
from fontblind_web import WebBuildError


INSTANCE_BODY_BYTES = 4 * 1024
INSTANCE_PATH = re.compile(r"^/api/jobs/([a-f0-9]{32})/instance$")
_INDEX_MARKER = '<script src="/app.js" defer></script>'
_INDEX_INJECTION = '<script src="/instance-export.js" defer></script>\n    ' + _INDEX_MARKER


def _validated_public_location(axes: tuple[dict[str, object], ...], raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("invalid static location")
    by_tag = {str(axis["tag"]): axis for axis in axes}
    if not by_tag or set(raw) != set(by_tag):
        raise ValueError("incomplete static location")
    location: dict[str, float] = {}
    for tag, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid static coordinate")
        number = float(value)
        axis = by_tag[tag]
        minimum = float(axis["min"])
        maximum = float(axis["max"])
        if not math.isfinite(number) or number < minimum or number > maximum:
            raise ValueError("out-of-range static coordinate")
        location[tag] = number
    return location


class InstanceHandler(Handler):
    """Extend the existing local boundary with one parent-scoped export route."""

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path not in {"/", "/index.html", "/instance-export.js"}:
            super().do_GET()
            return
        if not self._host_ok():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"ok": False, "error": "Invalid local host."})
            return
        if path == "/instance-export.js":
            self._static(WEB_ROOT / "instance-export.js", "text/javascript; charset=utf-8")
            return
        try:
            html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
            if html.count(_INDEX_MARKER) != 1:
                raise ValueError("local index integration marker is missing")
            payload = html.replace(_INDEX_MARKER, _INDEX_INJECTION).encode("utf-8")
        except (OSError, ValueError):
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "The local workbench could not be loaded."})
            return
        self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        match = INSTANCE_PATH.fullmatch(urlsplit(self.path).path)
        if match is None:
            super().do_POST()
            return
        if not self._host_ok():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"ok": False, "error": "Invalid local host."})
            return
        if not self._session_ok():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Invalid local session."})
            return

        parent = self.server.jobs.get(match.group(1))
        if parent is None:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "The generated variable source has expired."})
            return
        if not parent.result.variable or not parent.result.axes or parent.result.native.media_type != "font/ttf":
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": "This output has no generated axis to freeze."})
            return
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if media_type != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "Invalid static export request."})
            return
        length = self._body_length(INSTANCE_BODY_BYTES, "Choose one generated-axis location.")
        if length is None:
            return

        try:
            self.connection.settimeout(UPLOAD_READ_TIMEOUT_SECONDS)
            buffer = io.BytesIO()
            copy_exact(self.rfile, buffer, length, chunk_size=INSTANCE_BODY_BYTES)
            request = json.loads(buffer.getvalue().decode("utf-8"))
            if not isinstance(request, dict) or set(request) != {"location"}:
                raise ValueError("invalid static export envelope")
            location = _validated_public_location(parent.result.axes, request["location"])
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, StreamInterruptedError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid static export request."})
            return

        if not self.server.worker_gate.acquire(blocking=False):
            self._json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "error": "Another local build is already running. Finish or reset it before freezing this position."},
            )
            return
        try:
            item = parent.result.native
            source_path = parent.path / "output" / item.filename
            try:
                with source_path.open("rb") as source:
                    size = os.fstat(source.fileno()).st_size
                    if size <= 0 or size > MAX_UPLOAD_BYTES:
                        raise FontBlindError("Invalid generated variable source")
                    token, job = self.server.jobs._create_stream(  # Internal, same trust boundary.
                        "instance",
                        source,
                        [size],
                        {"location": location},
                    )
            except LabRequestError:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "This generated position could not be frozen and verified. No output was kept."},
                )
                return
            except (FontBlindError, WebBuildError, OSError, ValueError):
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "This generated position could not be frozen and verified. No output was kept."},
                )
                return
            except Exception:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": "Static export failed safely. No output was kept."},
                )
                return
            self._public_result(token, job)
        finally:
            self.server.worker_gate.release()
