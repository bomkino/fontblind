from __future__ import annotations

import io
import unittest

from fontblind_stream import COPY_CHUNK_BYTES, StreamInterruptedError, copy_exact


class RecordingReader(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.requests: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requests.append(size)
        if size < 0 or size > COPY_CHUNK_BYTES:
            raise AssertionError(f"unbounded read requested: {size}")
        return super().read(size)


class ShortWriter(io.BytesIO):
    def write(self, payload: bytes | bytearray | memoryview) -> int:
        view = memoryview(payload)
        maximum = max(1, len(view) // 3)
        return super().write(view[:maximum])


class BoundedStreamTests(unittest.TestCase):
    def test_large_upload_is_copied_in_bounded_chunks(self) -> None:
        payload = (b"FontBlind-stream-proof-" * 100_000) + b"end"
        source = RecordingReader(payload)
        target = io.BytesIO()
        copied = copy_exact(source, target, len(payload))
        self.assertEqual(copied, len(payload))
        self.assertEqual(target.getvalue(), payload)
        self.assertGreater(len(source.requests), 1)
        self.assertLessEqual(max(source.requests), COPY_CHUNK_BYTES)

    def test_short_writes_are_completed_without_losing_bytes(self) -> None:
        payload = b"0123456789" * 100
        target = ShortWriter()
        self.assertEqual(copy_exact(io.BytesIO(payload), target, len(payload)), len(payload))
        self.assertEqual(target.getvalue(), payload)

    def test_interrupted_upload_fails_closed(self) -> None:
        with self.assertRaises(StreamInterruptedError):
            copy_exact(io.BytesIO(b"short"), io.BytesIO(), 20)

    def test_invalid_lengths_and_chunk_sizes_are_rejected(self) -> None:
        for length in (0, -1):
            with self.subTest(length=length), self.assertRaises(ValueError):
                copy_exact(io.BytesIO(b"font"), io.BytesIO(), length)
        for chunk_size in (0, COPY_CHUNK_BYTES + 1):
            with self.subTest(chunk_size=chunk_size), self.assertRaises(ValueError):
                copy_exact(io.BytesIO(b"font"), io.BytesIO(), 4, chunk_size=chunk_size)


if __name__ == "__main__":
    unittest.main()
