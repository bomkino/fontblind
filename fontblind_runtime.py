"""Hardened local runtime used by the source launcher and native wrapper."""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import shutil
import signal
import stat
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Mapping
from http.server import ThreadingHTTPServer

from fontblind_app import (
    JOB_RE,
    JOB_TTL_SECONDS,
    MAX_UPLOAD_BYTES,
    FontBlindServer,
    Handler,
    Job,
    JobStore,
)
from fontblind_artifacts import retained_artifact_bytes, validate_job_artifacts
from fontblind_contract import (
    LANE_SLANT,
    LANE_VARIABLE,
    ArtifactSeal,
    expected_lane_for,
    validate_build_result,
)
from fontblind_instance_http import InstanceHandler
from fontblind_surgical import FontBlindError


MAX_RETAINED_JOBS = 8
MAX_RETAINED_BYTES = 768 * 1024 * 1024
MAX_CONCURRENT_DOWNLOADS = 2


def _stop_signal(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


class ContractJobStore(JobStore):
    """Own proof validation, artifact seals, lifecycle, and retained-resource limits."""

    def __init__(self) -> None:
        # The base constructor starts the expiry thread, so initialise every
        # structure that an overridden expiry path can touch first.
        self._artifact_seals: dict[str, dict[str, ArtifactSeal]] = {}
        self._retained_bytes_by_token: dict[str, int] = {}
        self._retained_bytes = 0
        self._parent_by_child: dict[str, str] = {}
        self._children_by_parent: dict[str, set[str]] = {}
        self._instance_inflight: set[str] = set()
        self._build_gate = threading.BoundedSemaphore(value=1)
        super().__init__()

    def _create_job(self, mode, source_count, options, populate_sources):  # type: ignore[no-untyped-def]
        self.expire()
        with self._build_gate:
            lane = expected_lane_for(mode, options)
            token, job = super()._create_job(mode, source_count, options, populate_sources)
            try:
                validate_build_result(
                    job.result,
                    expected_lane=lane,
                    require_source_discarded=True,
                )
                seals = validate_job_artifacts(
                    job.path,
                    job.result,
                    mode=mode,
                    options=options,
                )
                artifact_bytes = retained_artifact_bytes(seals)
                with self._lock:
                    if self._jobs.get(token) is not job:
                        raise FontBlindError("Generated output expired before it could be sealed")
                    if (
                        len(self._jobs) > MAX_RETAINED_JOBS
                        or self._retained_bytes + artifact_bytes > MAX_RETAINED_BYTES
                    ):
                        raise FontBlindError(
                            "FontBlind reached its local retained-output limit. "
                            "Reset an existing result before building another."
                        )
                    self._artifact_seals[token] = seals
                    self._retained_bytes_by_token[token] = artifact_bytes
                    self._retained_bytes += artifact_bytes
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

    @staticmethod
    def _sealed_snapshot(job: Job, item: object, seal: ArtifactSeal):  # type: ignore[no-untyped-def]
        """Copy one sealed file into an anonymous snapshot and verify while copying.

        The returned descriptor is independent of the retained path. A local
        mutation after verification therefore cannot alter bytes already being
        used as an instance source or streamed to the browser.
        """
        descriptor: int | None = None
        source = None
        snapshot = None
        try:
            output_root = (job.path / "output").resolve(strict=True)
            target = output_root / item.filename
            if target.parent != output_root:
                return None
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or int(metadata.st_size) != seal.size
                or int(metadata.st_dev) != seal.device
                or int(metadata.st_ino) != seal.inode
                or int(metadata.st_mtime_ns) != seal.modified_ns
            ):
                return None

            source = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = None
            snapshot = tempfile.TemporaryFile(prefix=".fontblind-sealed-", dir=job.path)
            digest = hashlib.sha256()
            remaining = seal.size
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    return None
                digest.update(block)
                written = snapshot.write(block)
                if written != len(block):
                    return None
                remaining -= len(block)
            if source.read(1) or digest.hexdigest() != seal.sha256:
                return None
            after = os.fstat(source.fileno())
            if (
                int(after.st_size) != seal.size
                or int(after.st_dev) != seal.device
                or int(after.st_ino) != seal.inode
                or int(after.st_mtime_ns) != seal.modified_ns
            ):
                return None
            snapshot.flush()
            snapshot.seek(0)
            result = snapshot
            snapshot = None
            return result
        except (OSError, AttributeError, TypeError, ValueError):
            return None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if source is not None:
                source.close()
            if snapshot is not None:
                snapshot.close()

    def open_artifact(self, token: str, kind: str):  # type: ignore[no-untyped-def]
        """Return an anonymous verified snapshot, never the retained path itself."""
        if not JOB_RE.fullmatch(token) or kind not in {"native", "web", "css", "bundle"}:
            return None
        job = self.get(token)
        if job is None:
            return None
        with self._lock:
            if self._jobs.get(token) is not job:
                return None
            seal = self._artifact_seals.get(token, {}).get(kind)
        if seal is None:
            self.delete(token)
            return None
        item = getattr(job.result, kind)
        snapshot = self._sealed_snapshot(job, item, seal)
        if snapshot is None:
            self.delete(token)
            return None
        return job, item, snapshot, seal.size

    def verify_artifact(self, token: str, kind: str) -> bool:
        opened = self.open_artifact(token, kind)
        if opened is None:
            return False
        _job, _item, snapshot, _size = opened
        snapshot.close()
        return True

    def retained_usage(self) -> tuple[int, int]:
        """Return the number and total bytes of currently retained verified jobs."""
        with self._lock:
            return len(self._jobs), int(self._retained_bytes)

    def create_instance(
        self,
        parent_token: str,
        location: Mapping[str, object],
    ) -> tuple[str, Job]:
        """Freeze one parent location and retain at most one child package."""
        parent = self.get(parent_token)
        if parent is None:
            raise FontBlindError("The generated variable source has expired")
        with self._lock:
            if self._jobs.get(parent_token) is not parent:
                raise FontBlindError("The generated variable source has expired")
            if parent_token in self._instance_inflight:
                raise FontBlindError("A static export is already running for this generated source")
            self._instance_inflight.add(parent_token)

        try:
            lane = self._parent_lane(parent)
            validate_build_result(
                parent.result,
                expected_lane=lane,
                require_source_discarded=True,
            )
            if not parent.result.variable or not parent.result.axes or parent.result.native.media_type != "font/ttf":
                raise FontBlindError("This output has no generated axis to freeze")

            opened = self.open_artifact(parent_token, "native")
            if opened is None:
                raise FontBlindError("The generated variable source is no longer verified")
            sealed_parent, _item, source, size = opened
            try:
                token, job = self._create_stream(
                    "instance",
                    source,
                    [size],
                    {"location": dict(location)},
                )
            finally:
                source.close()

            stale_parent = False
            previous_children: tuple[str, ...] = ()
            with self._lock:
                if self._jobs.get(parent_token) is not sealed_parent:
                    stale_parent = True
                else:
                    previous_children = tuple(self._children_by_parent.get(parent_token, set()))
                    self._children_by_parent[parent_token] = {token}
                    self._parent_by_child[token] = parent_token
            if stale_parent:
                self.delete(token)
                raise FontBlindError("The generated variable source expired during static export")

            # Keep the newly verified child, then remove the previous one. A
            # failed replacement never destroys the last valid child package.
            for previous in previous_children:
                if previous != token:
                    self.delete(previous)
            return token, job
        finally:
            with self._lock:
                self._instance_inflight.discard(parent_token)

    def _pop_tokens_locked(self, tokens: set[str]) -> list[Job]:
        jobs: list[Job] = []
        for token in tokens:
            job = self._jobs.pop(token, None)
            if job is not None:
                jobs.append(job)
            self._artifact_seals.pop(token, None)
            released = self._retained_bytes_by_token.pop(token, 0)
            self._retained_bytes = max(0, self._retained_bytes - released)
            self._instance_inflight.discard(token)

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
        with self._lock:
            self._artifact_seals.clear()
            self._retained_bytes_by_token.clear()
            self._retained_bytes = 0
            self._parent_by_child.clear()
            self._children_by_parent.clear()
            self._instance_inflight.clear()


class ContractFontBlindServer(FontBlindServer):
    """Use the strict parent store without changing the stable base server API."""

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[Handler] = InstanceHandler,
        *,
        allow_browser_shutdown: bool = False,
    ) -> None:
        self.jobs: ContractJobStore | None = None
        try:
            ThreadingHTTPServer.__init__(self, address, handler)
            self.jobs = ContractJobStore()
            self.session_secret = secrets.token_urlsafe(32)
            self.worker_gate = threading.BoundedSemaphore(value=1)
            self.download_gate = threading.BoundedSemaphore(value=MAX_CONCURRENT_DOWNLOADS)
            self.allow_browser_shutdown = bool(allow_browser_shutdown)
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
