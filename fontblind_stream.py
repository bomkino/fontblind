"""Small, bounded copies from local HTTP bodies into anonymous descriptors."""
from __future__ import annotations

from typing import BinaryIO


COPY_CHUNK_BYTES = 1024 * 1024


class StreamInterruptedError(EOFError):
    """A declared local upload ended before all bytes arrived."""


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
) -> int:
    """Copy exactly ``length`` bytes without allocating the whole upload."""
    size = int(length)
    block_size = int(chunk_size)
    if size <= 0:
        raise ValueError("Upload length must be positive")
    if block_size <= 0 or block_size > COPY_CHUNK_BYTES:
        raise ValueError("Upload chunk size exceeds the local memory boundary")

    copied = 0
    while copied < size:
        request = min(block_size, size - copied)
        block = source.read(request)
        if block is None or not block:
            raise StreamInterruptedError("The local upload was interrupted")
        if len(block) > request:
            raise StreamInterruptedError("The local upload violated bounded framing")
        _write_all(target, block)
        copied += len(block)
    return copied
