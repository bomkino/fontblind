"""Compact binary transport for anonymous Variable Lab donor sets."""
from __future__ import annotations

import struct
from typing import BinaryIO, Sequence


FONT_SET_MAGIC = b"FBLAB1\0\0"
FONT_SET_MEDIA_TYPE = "application/vnd.fontblind.font-set"
MIN_FONT_COUNT = 2
MAX_FONT_COUNT = 12
MAX_FONT_BYTES = 128 * 1024 * 1024
MAX_FONT_SET_BYTES = 256 * 1024 * 1024

_COUNT = struct.Struct(">B")
_LENGTH = struct.Struct(">I")
_MAX_BODY_BYTES = len(FONT_SET_MAGIC) + _COUNT.size + (MAX_FONT_COUNT * _LENGTH.size) + MAX_FONT_SET_BYTES


class FontSetError(ValueError):
    """The request is not a valid FontBlind font-set envelope."""


class FontSetTooLargeError(FontSetError):
    """The request exceeds a declared local memory boundary."""


def _validated_lengths(lengths: Sequence[int]) -> tuple[int, ...]:
    values = tuple(int(length) for length in lengths)
    if not MIN_FONT_COUNT <= len(values) <= MAX_FONT_COUNT:
        raise FontSetError("Choose between two and twelve compatible font masters.")
    if any(length <= 0 for length in values):
        raise FontSetError("Every donor must be non-empty.")
    if any(length > MAX_FONT_BYTES for length in values):
        raise FontSetTooLargeError("A font exceeds the 128 MB local limit.")
    if sum(values) > MAX_FONT_SET_BYTES:
        raise FontSetTooLargeError("The selected masters exceed the 256 MB local limit.")
    return values


def envelope_size(lengths: Sequence[int]) -> int:
    values = _validated_lengths(lengths)
    return len(FONT_SET_MAGIC) + _COUNT.size + (len(values) * _LENGTH.size) + sum(values)


def pack_font_set(payloads: Sequence[bytes]) -> bytes:
    """Build a font-set envelope for tests and non-browser callers."""
    values = tuple(bytes(payload) for payload in payloads)
    lengths = _validated_lengths([len(payload) for payload in values])
    header = bytearray(FONT_SET_MAGIC)
    header.extend(_COUNT.pack(len(values)))
    for length in lengths:
        header.extend(_LENGTH.pack(length))
    return bytes(header) + b"".join(values)


def _content_length(raw_value: str | None) -> int:
    if raw_value is None:
        raise FontSetError("The local Lab request has no content length.")
    value = raw_value.strip()
    if not value or not value.isascii() or not value.isdecimal():
        raise FontSetError("The local Lab request has an invalid content length.")
    length = int(value)
    if length <= 0:
        raise FontSetError("The local Lab request is empty.")
    if length > _MAX_BODY_BYTES:
        raise FontSetTooLargeError("The selected masters exceed the 256 MB local limit.")
    return length


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    remaining = int(size)
    chunks: list[bytes] = []
    while remaining:
        block = stream.read(remaining)
        if not block:
            raise FontSetError("The local Lab upload was interrupted.")
        if len(block) > remaining:
            raise FontSetError("The local Lab upload violated bounded framing.")
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)


def read_font_set_header(stream: BinaryIO, raw_content_length: str | None) -> tuple[int, ...]:
    """Validate the compact envelope and leave the stream at the first donor byte."""
    declared = _content_length(raw_content_length)
    minimum = len(FONT_SET_MAGIC) + _COUNT.size + (MIN_FONT_COUNT * _LENGTH.size)
    if declared < minimum:
        raise FontSetError("The local Lab request is incomplete.")

    if _read_exact(stream, len(FONT_SET_MAGIC)) != FONT_SET_MAGIC:
        raise FontSetError("The local Lab request has an invalid signature.")

    count = _COUNT.unpack(_read_exact(stream, _COUNT.size))[0]
    if not MIN_FONT_COUNT <= count <= MAX_FONT_COUNT:
        raise FontSetError("Choose between two and twelve compatible font masters.")

    lengths = _validated_lengths(
        [_LENGTH.unpack(_read_exact(stream, _LENGTH.size))[0] for _ in range(count)]
    )
    if envelope_size(lengths) != declared:
        raise FontSetError("The local Lab request has inconsistent framing.")
    return lengths


def read_font_set(stream: BinaryIO, raw_content_length: str | None) -> list[bytes]:
    """Read a complete donor set for tests and non-streaming callers."""
    lengths = read_font_set_header(stream, raw_content_length)
    return [_read_exact(stream, length) for length in lengths]
