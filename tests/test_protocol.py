from __future__ import annotations

import io
import struct
import tempfile
import unittest
from pathlib import Path

from fontblind_mastermap import anonymous_slant_masters, anonymous_variable_masters
from fontblind_pipeline import OutputFile, PublicBuildResult
from fontblind_protocol import (
    FONT_SET_MAGIC,
    MAX_FONT_BYTES,
    MAX_FONT_SET_BYTES,
    FontSetError,
    FontSetTooLargeError,
    pack_font_set,
    read_font_set,
)


class FontSetProtocolTests(unittest.TestCase):
    def test_roundtrip_preserves_raw_donor_bytes(self) -> None:
        payloads = [b"\x00\x01\x00\x00first", b"OTTOsecond"]
        body = pack_font_set(payloads)
        self.assertEqual(read_font_set(io.BytesIO(body), str(len(body))), payloads)
        self.assertLess(len(body), sum(map(len, payloads)) + 32)

    def test_rejects_signature_count_length_and_interruption_faults(self) -> None:
        valid = pack_font_set([b"first", b"second"])

        broken = b"BADMAGIC" + valid[len(FONT_SET_MAGIC):]
        with self.assertRaises(FontSetError):
            read_font_set(io.BytesIO(broken), str(len(broken)))

        one = FONT_SET_MAGIC + b"\x01" + struct.pack(">I", 4) + b"font"
        with self.assertRaises(FontSetError):
            read_font_set(io.BytesIO(one), str(len(one)))

        with self.assertRaises(FontSetError):
            read_font_set(io.BytesIO(valid), str(len(valid) + 1))

        with self.assertRaises(FontSetError):
            read_font_set(io.BytesIO(valid[:-1]), str(len(valid)))

    def test_rejects_large_declared_fonts_without_allocating_them(self) -> None:
        lengths = [MAX_FONT_BYTES + 1, 1]
        body = FONT_SET_MAGIC + b"\x02" + b"".join(struct.pack(">I", value) for value in lengths)
        declared = len(body) + sum(lengths)
        with self.assertRaises(FontSetTooLargeError):
            read_font_set(io.BytesIO(body), str(declared))

        lengths = [MAX_FONT_SET_BYTES // 2 + 1, MAX_FONT_SET_BYTES // 2 + 1]
        body = FONT_SET_MAGIC + b"\x02" + b"".join(struct.pack(">I", value) for value in lengths)
        declared = len(body) + sum(lengths)
        with self.assertRaises(FontSetTooLargeError):
            read_font_set(io.BytesIO(body), str(declared))


class PublicResultTests(unittest.TestCase):
    def _verified_result(self) -> PublicBuildResult:
        return PublicBuildResult(
            native=OutputFile("native", "font.ttf", "font/ttf"),
            web=OutputFile("web", "font.woff2", "font/woff2"),
            css=OutputFile("css", "font.css", "text/css"),
            bundle=OutputFile("bundle", "font.zip", "application/zip"),
            flavor="TrueType",
            variable=False,
            color=False,
            checks={"verified": True},
        )

    def test_internal_result_rejects_truthy_non_boolean_proof(self) -> None:
        value = self._verified_result().to_internal_dict()
        value["checks"]["verified"] = "false"
        with self.assertRaises(ValueError):
            PublicBuildResult.from_internal_dict(value)

    def test_internal_result_rejects_truthy_non_boolean_descriptors(self) -> None:
        for field in ("variable", "color"):
            with self.subTest(field=field):
                value = self._verified_result().to_internal_dict()
                value[field] = "false"
                with self.assertRaises(ValueError):
                    PublicBuildResult.from_internal_dict(value)

    def test_result_requires_every_proof_to_pass(self) -> None:
        result = self._verified_result()
        object.__setattr__(result, "checks", {"verified": False})
        with self.assertRaises(ValueError):
            result.require_verified()


class AnonymousMasterMapTests(unittest.TestCase):
    def test_slant_map_exposes_only_functional_locations(self) -> None:
        masters = anonymous_slant_masters(12)
        self.assertEqual([master["id"] for master in masters], ["M01", "M02"])
        self.assertEqual([master["location"]["slnt"] for master in masters], [0.0, -12.0])
        self.assertEqual(sum(bool(master["default"]) for master in masters), 1)

    def test_variable_map_hides_source_names_and_marks_default(self) -> None:
        from tests.test_lab import write_fixture_font

        with tempfile.TemporaryDirectory(prefix="fontblind-master-map-") as temp:
            root = Path(temp)
            regular = root / "revealing-secret-regular.ttf"
            bold = root / "revealing-secret-bold.ttf"
            write_fixture_font(regular, weight=400, family="Secret Regular")
            write_fixture_font(bold, weight=700, family="Secret Bold")
            axes = (
                {"tag": "wght", "name": "Weight", "min": 400.0, "default": 400.0, "max": 700.0},
            )
            masters = anonymous_variable_masters([bold, regular], axes)
            rendered = repr(masters).casefold()
            self.assertNotIn("revealing", rendered)
            self.assertNotIn("secret", rendered)
            self.assertEqual([master["id"] for master in masters], ["M01", "M02"])
            self.assertEqual([master["location"]["wght"] for master in masters], [400.0, 700.0])
            self.assertEqual(sum(bool(master["default"]) for master in masters), 1)


if __name__ == "__main__":
    unittest.main()
