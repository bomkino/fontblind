"""Hardened local runtime used by the source launcher and native wrapper."""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import sys
import threading
import time
import webbrowser
from collections.abc import Mapping
from http.server import ThreadingHTTPServer
from pathlib import Path

from fontblind_app import (
    JOB_RE,
    JOB_TTL_SECONDS,
    MAX_UPLOAD_BYTES,
    FontBlindServer,
    Handler,
    Job,
    JobStore,
    _stop_signal,
)
from fontblind_contract import (
    LANE_SLANT,
    LANE_VARIABLE,
    ArtifactSeal,
    expected_lane_for,
    validate_build_result,
    validate_job_artifacts,
    verify_artifact_seal,
)
from fontblind_instance_http import InstanceHandler
from fontblind_surgical import FontBlindError


class ContractJobStore(JobStore):
    """Own proof validation, artifact seals, and parent-child lifecycle."""

    def __init__(self) -> None:
        # The base constructor starts the expiry thread, so initialise every
        # structure that the overridden expiry method can touch first.
        self._artifact_seals: dict[str, dict[str, ArtifactSeal]] = {}
        self._parent_by_child: dict[str, str] = {}
        self._children_by_parent: dict[str, set[str]] = {}
        super().__init__()

    def _create_job(self, mode, source_count, options, populate_sources):  # type: ignore[no-untyped-def]
        lane = expected_lane_for(mode, options)
        token, job = super()._create_job(mode, source_count, options, populate_sources)
        try:
            validate_build_result(
                job.result,
                expected_lane=lane,
                require_source_discarded=True,
            )
            seals = validate_job_artifacts(job.path, job.result)
            with self._lock:
                if self._jobs.get(token) is not job:
                    raise FontBlindError("Generated output expired before it could be sealed")
                self._artifact_seals[token] = seals
        except Exception:
            self.delete(token)
            raise
        return token, job

    @staticmethod
    def _parent_lane(job: Job) -> str:
        tags = tuple(str(axis.get("tag")) for axis in job.result.axes)
        if tags == ("slnt",):
            return LANE_SLANT
        if tags in {("wght",), ("wdth",), ("wght", "wdth")}:
            return LANE_VARIABLE
        raise FontBlindError("This output has no generated axis to freeze")

    def verify_artifact(self, token: str, kind: str) -> bool:
        """Fail closed if a retained file changed after the worker exited."""
        if not JOB_RE.fullmatch(token) or kind not in {"native", "web", "css", "bundle"}:
            return False
        job = self.get(token)
        if job is None:
            return False
        with self._lock:
            if self._jobs.get(token) is not job:
                return False
            seal = self._artifact_seals.get(token, {}).get(kind)
        if seal is None:
            self.delete(token)
            return False
        item = getattr(job.result, kind)
        if not verify_artifact_seal(job.path, item, seal):
            self.delete(token)
            return False
        return True

    def create_instance(
        self,
        parent_token: str,
        location: Mapping[str, object],
    ) -> tuple[str, Job]:
        """Freeze one parent location and retain at most one child package."""
        parent = self.get(parent_token)
        if parent is None:
            raise FontBlindError("The generated variable source has expired")
        lane = self._parent_lane(parent)
        validate_build_result(
            parent.result,
            expected_lane=lane,
            require_source_discarded=True,
        )
        if not parent.result.variable or not parent.result.axes or parent.result.native.media_type != "font/ttf":
            raise FontBlindError("This output has no generated axis to freeze")
        if not self.verify_artifact(parent_token, "native"):
            raise FontBlindError("The generated variable source is no longer verified")

        # Re-resolve the parent after the seal check. An explicit reset can run
        # from another local request while verification is in progress.
        parent = self.get(parent_token)
        if parent is None:
            raise FontBlindError("The generated variable source has expired")
        item = parent.result.native
        source_path = parent.path / "output" / item.filename
        try:
            with source_path.open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                if size <= 0 or size > MAX_UPLOAD_BYTES:
                    raise FontBlindError("Invalid generated variable source")
                token, job = self._create_stream(
                    "instance",
                    source,
                    [size],
                    {"location": dict(location)},
                )
        except OSError as exc:
            raise FontBlindError("The generated variable source is unavailable") from exc

        stale_parent = False
        previous_children: tuple[str, ...] = ()
        with self._lock:
            if self._jobs.get(parent_token) is not parent:
                stale_parent = True
            else:
                previous_children = tuple(self._children_by_parent.get(parent_token, set()))
                self._children_by_parent[parent_token] = {token}
                self._parent_by_child[token] = parent_token
        if stale_parent:
            self.delete(token)
            raise FontBlindError("The generated variable source expired during static export")

        # Keep the newly verified child, then remove the previous one. A failed
        # replacement never destroys the last valid child package.
        for previous in previous_children:
            if previous != token:
                self.delete(previous)
        return token, job

    def _pop_tokens_locked(self, tokens: set[str]) -> list[Job]:
        jobs: list[Job] = []
        for token in tokens:
            job = self._jobs.pop(token, None)
            if job is not None:
                jobs.append(job)
            self._artifact_seals.pop(token, None)

            parent = self._parent_by_child.pop(token, None)
            if parent is not None:
                siblings = self._children_by_parent.get(parent)
                if siblings is not None:
                    siblings.discard(token)
                    if not siblings:
                        self._children_by_parent.pop(parent, None)

            children = self._children_by_parent.pop(token, set())
            for child in children:
                self._parent_by_child.pop(child, None)
        return jobs

    def delete(self, token: str) -> bool:
        if not JOB_RE.fullmatch(token):
            return False
        with self._lock:
            existed = token in self._jobs
            tokens = {token, *self._children_by_parent.get(token, set())}
            jobs = self._pop_tokens_locked(tokens)
        for job in jobs:
            shutil.rmtree(job.path, ignore_errors=True)
        return existed

    def expire(self) -> None:
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        with self._lock:
            expired = {token for token, job in self._jobs.items() if job.created < cutoff}
            for token in tuple(expired):
                expired.update(self._children_by_parent.get(token, set()))
            jobs = self._pop_tokens_locked(expired)
        for job in jobs:
            shutil.rmtree(job.path, ignore_errors=True)

    def close(self) -> None:
        super().close()
        self._artifact_seals.clear()
        self._parent_by_child.clear()
        self._children_by_parent.clear()


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
