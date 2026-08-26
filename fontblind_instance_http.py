"""HTTP boundary for freezing verified Lab positions into static fonts."""
from __future__ import annotations

import io
import json
import math
import re
import shutil
from http import HTTPStatus
from urllib.parse import urlsplit

from fontblind_app import (
    UPLOAD_READ_TIMEOUT_SECONDS,
    WEB_ROOT,
    Handler,
    LabRequestError,
)
from fontblind_stream import COPY_CHUNK_BYTES, StreamInterruptedError, copy_exact
from fontblind_surgical import FontBlindError
from fontblind_web import WebBuildError


INSTANCE_BODY_BYTES = 4 * 1024
INSTANCE_PATH = re.compile(r"^/api/jobs/([a-f0-9]{32})/instance$")
DOWNLOAD_PATH = re.compile(r"^/download/([a-f0-9]{32})/(native|web|css|bundle)$")
_INDEX_MARKER = '<script src="/app.js" defer></script>'
_INDEX_INJECTION = (
    '<script src="/result-contract.js" defer></script>\n    '
    '<script src="/instance-export.js" defer></script>\n    '
    + _INDEX_MARKER
)


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

    def _sealed_download(self, token: str, kind: str) -> None:
        opener = getattr(self.server.jobs, "open_artifact", None)
        opened = opener(token, kind) if callable(opener) else None
        if opened is None:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": "This output expired or failed its retained-file integrity check."},
            )
            return
        _job, item, snapshot, length = opened
        with snapshot:
            self._headers(
                HTTPStatus.OK,
                item.media_type,
                length,
                f'attachment; filename="{item.filename}"',
            )
            try:
                shutil.copyfileobj(snapshot, self.wfile, length=COPY_CHUNK_BYTES)
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        download = DOWNLOAD_PATH.fullmatch(path)
        if download is not None:
            if not self._host_ok():
                self._json(HTTPStatus.MISDIRECTED_REQUEST, {"ok": False, "error": "Invalid local host."})
                return
            self._sealed_download(*download.groups())
            return

        if path not in {"/", "/index.html", "/instance-export.js", "/result-contract.js"}:
            super().do_GET()
            return
        if not self._host_ok():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"ok": False, "error": "Invalid local host."})
            return
        if path == "/instance-export.js":
            self._static(WEB_ROOT / "instance-export.js", "text/javascript; charset=utf-8")
            return
        if path == "/result-contract.js":
            self._static(WEB_ROOT / "result-contract.js", "text/javascript; charset=utf-8")
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

    def _public_instance_result(self, token: str, job: object, location: dict[str, float]) -> None:
        public = job.result.to_public_dict()
        for kind in ("native", "web", "css", "bundle"):
            public[kind]["url"] = f"/download/{token}/{kind}"
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "job": token,
                "location": dict(location),
                **public,
            },
        )

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

        parent_token = match.group(1)
        parent = self.server.jobs.get(parent_token)
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
            creator = getattr(self.server.jobs, "create_instance", None)
            if not callable(creator):
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": "Static export is unavailable in this local runtime."},
                )
                return
            try:
                token, job = creator(parent_token, location)
            except LabRequestError:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "This generated position could not be frozen and verified. No output was kept."},
                )
                return
            except (FontBlindError, WebBuildError, OSError, ValueError):
                if self.server.jobs.get(parent_token) is None:
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "The generated variable source has expired."})
                else:
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

            if self.server.jobs.get(token) is not job:
                self.server.jobs.delete(token)
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "The frozen output expired before it could be exposed."},
                )
                return
            self._public_instance_result(token, job, location)
        finally:
            self.server.worker_gate.release()
