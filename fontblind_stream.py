"""Small, bounded copies from local HTTP bodies into anonymous descriptors."""
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
