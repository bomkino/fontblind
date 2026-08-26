from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from fontblind_artifacts import validate_job_artifacts
from fontblind_contract import BuildResultContractError
from fontblind_lab import build_variable_outputs
from tests.test_lab import write_fixture_font


class BinaryCanonicalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="fontblind-binary-canonical-")
        root = Path(cls._temporary.name)
        regular = root / "regular.ttf"
        bold = root / "bold.ttf"
        write_fixture_font(regular, weight=400, family="Binary Canonical Regular")
        write_fixture_font(bold, weight=700, family="Binary Canonical Bold")
        cls.template_job = root / "template-job"
        cls.result = build_variable_outputs([regular, bold], cls.template_job / "output")
        validate_job_artifacts(cls.template_job, cls.result)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def fresh_job(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fontblind-binary-canonical-copy-")
        job = Path(temporary.name) / "job"
        shutil.copytree(self.template_job, job)
        return temporary, job

    def assert_rejected(self, mutate: object) -> None:
        temporary, job = self.fresh_job()
        try:
            mutate(job)
            with self.assertRaises(BuildResultContractError):
                validate_job_artifacts(job, self.result)
        finally:
            temporary.cleanup()

    def test_native_woff2_and_zip_reject_appended_payloads(self) -> None:
        cases = (
            (self.result.native.filename, b"NATIVE_PRIVATE_TRAILER"),
            (self.result.web.filename, b"WOFF2_PRIVATE_TRAILER"),
            (self.result.bundle.filename, b"ZIP_PRIVATE_TRAILER"),
        )
        for filename, trailer in cases:
            with self.subTest(filename=filename):
                self.assert_rejected(
                    lambda job, filename=filename, trailer=trailer: (
                        job / "output" / filename
                    ).write_bytes((job / "output" / filename).read_bytes() + trailer)
                )

    def test_woff2_declared_length_must_equal_the_retained_file(self) -> None:
        def mutate(job: Path) -> None:
            path = job / "output" / self.result.web.filename
            payload = bytearray(path.read_bytes())
            declared = struct.unpack_from(">I", payload, 8)[0]
            struct.pack_into(">I", payload, 8, declared - 1)
            path.write_bytes(payload)

        self.assert_rejected(mutate)

    def test_woff2_metadata_and_private_blocks_are_forbidden(self) -> None:
        for offset, value in ((28, 48), (32, 1), (36, 1), (40, 48), (44, 1)):
            with self.subTest(offset=offset):
                def mutate(job: Path, offset: int = offset, value: int = value) -> None:
                    path = job / "output" / self.result.web.filename
                    payload = bytearray(path.read_bytes())
                    struct.pack_into(">I", payload, offset, value)
                    path.write_bytes(payload)

                self.assert_rejected(mutate)

    def test_sfnt_padding_cannot_carry_hidden_bytes(self) -> None:
        def mutate(job: Path) -> None:
            path = job / "output" / self.result.native.filename
            payload = bytearray(path.read_bytes())
            _signature, table_count, _search_range, _selector, _shift = struct.unpack_from(">4sHHHH", payload, 0)
            for index in range(table_count):
                _tag, _checksum, table_offset, table_length = struct.unpack_from(">4sIII", payload, 12 + index * 16)
                raw_end = table_offset + table_length
                padded_end = (raw_end + 3) & ~3
                if padded_end > raw_end:
                    payload[raw_end] = 0x41
                    path.write_bytes(payload)
                    return
            self.skipTest("generated fixture contained no SFNT padding byte")

        self.assert_rejected(mutate)

    def test_zip_central_directory_cannot_hide_an_unreferenced_gap(self) -> None:
        def mutate(job: Path) -> None:
            path = job / "output" / self.result.bundle.filename
            payload = bytearray(path.read_bytes())
            eocd = len(payload) - 22
            self.assertEqual(payload[eocd : eocd + 4], b"PK\x05\x06")
            central_offset = struct.unpack_from("<I", payload, eocd + 16)[0]
            payload[central_offset:central_offset] = b"HIDDEN"
            eocd += 6
            struct.pack_into("<I", payload, eocd + 16, central_offset + 6)
            path.write_bytes(payload)

        self.assert_rejected(mutate)

    def test_zip_eocd_must_be_the_final_record(self) -> None:
        def mutate(job: Path) -> None:
            path = job / "output" / self.result.bundle.filename
            payload = bytearray(path.read_bytes())
            eocd = len(payload) - 22
            payload[eocd + 20 : eocd + 22] = struct.pack("<H", 4)
            payload.extend(b"NOTE")
            path.write_bytes(payload)

        self.assert_rejected(mutate)


if __name__ == "__main__":
    unittest.main()
