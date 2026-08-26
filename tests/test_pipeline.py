from __future__ import annotations

import tempfile
import unittest
import unicodedata
import zipfile
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from fontTools.ttLib.tables.ttProgram import Program

from fontblind_outline import _convert_cff_to_single_fd_cid
from fontblind_pipeline import CSS_FAMILY, _verify_woff2_roundtrip, build_browser_outputs
from fontblind_policy import BrowserCompatibilityError, ZeroIdPolicyError, inspect_strict_source
from fontblind_web import WebBuildError


FONT_ROOTS = (Path("/System/Library/Fonts"), Path("/Library/Fonts"))


def find_sample(required_table: str) -> Path | None:
    for root in FONT_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted((*root.rglob("*.ttf"), *root.rglob("*.otf"))):
            try:
                font = TTFont(str(path), lazy=True)
                has_table = required_table in font
                font.close()
                if has_table:
                    inspect_strict_source(path)
                    return path
            except Exception:
                continue
    return None


TTF_SAMPLE = find_sample("glyf")
CFF_SAMPLE = find_sample("CFF ")
VARIABLE_SAMPLE = find_sample("fvar")
SECRET = "ORIGIN_LABEL_7Q9K2M"


def family_strings(path: Path) -> set[str]:
    font = TTFont(str(path), lazy=False)
    try:
        return {
            record.toUnicode().strip()
            for record in font["name"].names
            if int(record.nameID) in {1, 3, 4, 6, 16} and record.toUnicode().strip()
        }
    finally:
        font.close()


def contains_secret(path: Path) -> bool:
    payload = path.read_bytes()
    probes = {
        SECRET.encode("utf-8"),
        SECRET.encode("utf-16-be"),
        SECRET.encode("utf-16-le"),
        SECRET.encode("utf-32-be"),
        SECRET.encode("utf-32-le"),
        SECRET.encode("mac-roman"),
        SECRET.encode("latin-1"),
    }
    return any(probe in payload for probe in probes)


def add_runtime_label(font: TTFont, tag: str, label: bytes) -> None:
    if len(label) > 255:
        raise ValueError("test label is too long for NPUSHB")
    bytecode = bytes((0x40, len(label))) + label + bytes((0x21,)) * len(label)
    table = newTable(tag)
    table.program = Program()
    table.program.fromBytecode(bytecode)
    font[tag] = table
    font["maxp"].maxStackElements = max(int(font["maxp"].maxStackElements), len(label))


def write_tiny_cid_cff(path: Path) -> None:
    glyph_order = [".notdef", "cid00001"]
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({65: "cid00001"})
    charstrings = {}
    for glyph_name in glyph_order:
        pen = T2CharStringPen(500, None)
        if glyph_name != ".notdef":
            pen.moveTo((100, 0))
            pen.lineTo((250, 700))
            pen.lineTo((400, 0))
            pen.closePath()
        charstrings[glyph_name] = pen.getCharString(private=None, globalSubrs=None)
    builder.setupCFF(
        "Source-Regular",
        {"FullName": "Source Regular", "FamilyName": "Source", "Weight": "Regular"},
        charstrings,
        {},
    )
    builder.setupHorizontalMetrics({name: (500, 0) for name in glyph_order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Source",
            "styleName": "Regular",
            "uniqueFontIdentifier": "Source 1.0",
            "fullName": "Source Regular",
            "psName": "Source-Regular",
            "version": "Version 1.0",
        }
    )
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.save(str(path))
    font = TTFont(str(path), lazy=False)
    try:
        _convert_cff_to_single_fd_cid(font)
        font.save(str(path))
    finally:
        font.close()


class StrictSourceTests(unittest.TestCase):
    def test_cid_keyed_cff_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            source = Path(temp) / "source.otf"
            write_tiny_cid_cff(source)
            with self.assertRaises(ZeroIdPolicyError):
                inspect_strict_source(source)


@unittest.skipUnless(TTF_SAMPLE is not None, "strict-compatible TTF sample unavailable")
class PipelineTests(unittest.TestCase):
    def test_ttf_native_web_css_and_bundle(self) -> None:
        assert TTF_SAMPLE is not None
        identities = family_strings(TTF_SAMPLE)
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            result = build_browser_outputs(TTF_SAMPLE, Path(temp), verify_rounds=2)
            native = Path(temp) / result.native.filename
            web = Path(temp) / result.web.filename
            css = Path(temp) / result.css.filename
            bundle = Path(temp) / result.bundle.filename
            self.assertTrue(native.is_file())
            self.assertTrue(web.is_file())
            self.assertTrue(bundle.is_file())
            css_text = css.read_text(encoding="utf-8")
            self.assertIn(f'font-family: "{CSS_FAMILY}"', css_text)
            self.assertIn('src: url("fontblind-web.woff2") format("woff2")', css_text)
            self.assertNotIn("local(", css_text.casefold())
            self.assertIn("font-weight:", css_text)
            self.assertIn("font-style:", css_text)
            self.assertIn("font-stretch:", css_text)
            output_names = family_strings(native)
            self.assertFalse({value.casefold() for value in identities} & {value.casefold() for value in output_names})
            self.assertTrue(all(result.checks.values()))

    def test_unknown_table_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            source = Path(temp) / "source.ttf"
            assert TTF_SAMPLE is not None
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                table = DefaultTable("ZZZZ")
                table.data = b"PrivateIdentityCarrier"
                font["ZZZZ"] = table
                font.save(str(source))
            finally:
                font.close()
            with self.assertRaises(ZeroIdPolicyError):
                inspect_strict_source(source)

    def test_missing_browser_required_os2_table_fails_preflight(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            source = Path(temp) / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                del font["OS/2"]
                font.save(str(source))
            finally:
                font.close()
            with self.assertRaises(BrowserCompatibilityError):
                inspect_strict_source(source)

    def test_identity_label_in_true_type_program_fails_closed(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                font["name"].setName(SECRET, 1, 3, 1, 0x0409)
                add_runtime_label(font, "fpgm", SECRET.encode("ascii"))
                font.save(str(source))
            finally:
                font.close()

            output = root / "output"
            with self.assertRaises(ZeroIdPolicyError):
                build_browser_outputs(source, output, verify_rounds=1)
            self.assertFalse((output / "fontblind-native.ttf").exists())
            self.assertFalse((output / "fontblind-web.woff2").exists())

    def test_utf16_identity_label_in_true_type_program_fails_closed(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                font["name"].setName(SECRET, 1, 3, 1, 0x0409)
                add_runtime_label(font, "prep", SECRET.encode("utf-16-be"))
                font.save(str(source))
            finally:
                font.close()

            with self.assertRaises(ZeroIdPolicyError):
                build_browser_outputs(source, root / "output", verify_rounds=1)

    def test_private_name_id_label_in_runtime_program_fails_closed(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                label = "PRIVATE_AXIS_ORIGIN"
                font["name"].setName(label, 256, 3, 1, 0x0409)
                add_runtime_label(font, "fpgm", label.encode("ascii"))
                font.save(str(source))
            finally:
                font.close()
            with self.assertRaises(ZeroIdPolicyError):
                build_browser_outputs(source, root / "output", verify_rounds=1)

    def test_case_changed_runtime_identity_fails_closed(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                label = "CaseSensitiveOrigin"
                font["name"].setName(label, 1, 3, 1, 0x0409)
                add_runtime_label(font, "fpgm", b"cASEsENSITIVEoRIGIN")
                font.save(str(source))
            finally:
                font.close()
            with self.assertRaises(ZeroIdPolicyError):
                build_browser_outputs(source, root / "output", verify_rounds=1)

    def test_unicode_normalized_runtime_identity_fails_closed(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                label = "Caf\u00e9 Origin"
                font["name"].setName(label, 1, 3, 1, 0x0409)
                decomposed = unicodedata.normalize("NFD", label).encode("utf-8")
                add_runtime_label(font, "fpgm", decomposed)
                font.save(str(source))
            finally:
                font.close()
            with self.assertRaises(ZeroIdPolicyError):
                build_browser_outputs(source, root / "output", verify_rounds=1)

    def test_short_source_identity_is_rejected_before_output(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            source = Path(temp) / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                font["name"].setName("ABC", 1, 3, 1, 0x0409)
                font.save(str(source))
            finally:
                font.close()
            with self.assertRaises(ZeroIdPolicyError):
                inspect_strict_source(source)

    def test_source_name_colliding_with_generic_output_is_rejected(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                font["name"].setName("Untitled", 1, 3, 1, 0x0409)
                font.save(str(source))
            finally:
                font.close()
            with self.assertRaises(ZeroIdPolicyError):
                build_browser_outputs(source, root / "output", verify_rounds=1)

    def test_woff2_proof_detects_changed_glyph_instructions(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            native = root / "native.ttf"
            decoded = root / "decoded.ttf"
            native.write_bytes(TTF_SAMPLE.read_bytes())
            font = TTFont(str(native), lazy=False)
            try:
                glyf = font["glyf"]
                glyph_name = next(
                    name
                    for name in font.getGlyphOrder()
                    if not glyf[name].isComposite() and int(glyf[name].numberOfContours) > 0
                )
                glyf[glyph_name].program = Program()
                glyf[glyph_name].program.fromBytecode(b"\xb0\x00\x21")
                font.save(str(native))
            finally:
                font.close()

            decoded.write_bytes(native.read_bytes())
            font = TTFont(str(decoded), lazy=False)
            try:
                glyph = font["glyf"][glyph_name]
                glyph.program = Program()
                glyph.program.fromBytecode(b"\xb0\x01\x21")
                font.save(str(decoded))
            finally:
                font.close()

            with self.assertRaises(WebBuildError):
                _verify_woff2_roundtrip(native, decoded)

    def test_adversarial_identity_and_embedding_labels_are_absent(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                for name_id in (0, 1, 3, 4, 5, 6, 8, 9, 13, 14, 16, 18, 25, 256):
                    font["name"].setName(f"{SECRET}-{name_id}", name_id, 3, 1, 0x0409)
                font["head"].fontRevision = 9.876
                font["OS/2"].achVendID = "Q9K2"
                font["OS/2"].fsType = 0x0008
                font["OS/2"].sFamilyClass = 0x1234
                for key in vars(font["OS/2"].panose):
                    if key != "tableTag":
                        setattr(font["OS/2"].panose, key, 7)
                for tag in ("meta", "FFTM"):
                    table = DefaultTable(tag)
                    table.data = SECRET.encode("ascii")
                    font[tag] = table
                font.save(str(source))
            finally:
                font.close()

            output = root / "output"
            result = build_browser_outputs(source, output, verify_rounds=2)
            native = output / result.native.filename
            web = output / result.web.filename
            css = output / result.css.filename
            decoded = root / "decoded.ttf"
            decoded_font = TTFont(str(web), lazy=False)
            try:
                decoded_font.flavor = None
                decoded_font.save(str(decoded))
            finally:
                decoded_font.close()

            self.assertFalse(contains_secret(native))
            self.assertFalse(contains_secret(decoded))
            self.assertNotIn(SECRET, css.read_text(encoding="utf-8"))
            blind = TTFont(str(native), lazy=False)
            try:
                self.assertEqual(int(blind["OS/2"].fsType), 0)
                self.assertEqual(str(blind["OS/2"].achVendID), "NONE")
                self.assertNotIn("meta", blind)
                self.assertNotIn("FFTM", blind)
            finally:
                blind.close()
            with zipfile.ZipFile(output / result.bundle.filename) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    sorted([result.native.filename, result.web.filename, result.css.filename]),
                )
                self.assertFalse(any(SECRET.encode("ascii") in archive.read(name) for name in archive.namelist()))

    def test_native_names_preserve_generic_bold_italic_semantics(self) -> None:
        assert TTF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.ttf"
            font = TTFont(str(TTF_SAMPLE), lazy=False)
            try:
                font["name"].setName(SECRET, 1, 3, 1, 0x0409)
                font["OS/2"].usWeightClass = 700
                font["OS/2"].fsSelection = (int(font["OS/2"].fsSelection) & ~0x40) | 0x21
                font["head"].macStyle = int(font["head"].macStyle) | 0x03
                font.save(str(source))
            finally:
                font.close()

            result = build_browser_outputs(source, root / "output", verify_rounds=2)
            native = root / "output" / result.native.filename
            blind = TTFont(str(native), lazy=False)
            try:
                self.assertEqual(blind["name"].getName(2, 3, 1, 0x0409).toUnicode(), "Bold Italic")
                self.assertEqual(blind["name"].getName(6, 3, 1, 0x0409).toUnicode(), "Untitled-BoldItalic")
                self.assertEqual(int(blind["OS/2"].usWeightClass), 700)
                self.assertTrue(int(blind["OS/2"].fsSelection) & 0x01)
                self.assertTrue(int(blind["head"].macStyle) & 0x02)
            finally:
                blind.close()

    @unittest.skipUnless(VARIABLE_SAMPLE is not None, "strict-compatible variable sample unavailable")
    def test_variable_axes_survive_native_and_web_roundtrip(self) -> None:
        assert VARIABLE_SAMPLE is not None
        source = TTFont(str(VARIABLE_SAMPLE), lazy=False)
        try:
            source_axes = [
                (str(axis.axisTag), float(axis.minValue), float(axis.defaultValue), float(axis.maxValue))
                for axis in source["fvar"].axes
            ]
        finally:
            source.close()
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            result = build_browser_outputs(VARIABLE_SAMPLE, Path(temp), verify_rounds=2)
            self.assertTrue(result.variable)
            for filename in (result.native.filename, result.web.filename):
                font = TTFont(str(Path(temp) / filename), lazy=False)
                try:
                    output_axes = [
                        (str(axis.axisTag), float(axis.minValue), float(axis.defaultValue), float(axis.maxValue))
                        for axis in font["fvar"].axes
                    ]
                    self.assertEqual(source_axes, output_axes)
                    expected_axis_names = {
                        "ital": "Italic",
                        "opsz": "Optical Size",
                        "slnt": "Slant",
                        "wdth": "Width",
                        "wght": "Weight",
                    }
                    for axis in font["fvar"].axes:
                        label = font["name"].getName(int(axis.axisNameID), 3, 1, 0x0409).toUnicode()
                        expected = expected_axis_names.get(str(axis.axisTag), f"Axis {str(axis.axisTag)}")
                        self.assertEqual(label, expected)
                    for index, instance in enumerate(font["fvar"].instances, start=1):
                        if int(instance.subfamilyNameID) > 25:
                            label = font["name"].getName(int(instance.subfamilyNameID), 3, 1, 0x0409).toUnicode()
                            self.assertEqual(label, f"Instance {index:02d}")
                finally:
                    font.close()


@unittest.skipUnless(CFF_SAMPLE is not None, "strict-compatible CFF sample unavailable")
class CffPipelineTests(unittest.TestCase):
    def test_cff_output_remains_otf(self) -> None:
        assert CFF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            result = build_browser_outputs(CFF_SAMPLE, Path(temp), verify_rounds=2)
            self.assertEqual(result.native.filename, "fontblind-native.otf")
            font = TTFont(str(Path(temp) / result.native.filename), lazy=False)
            try:
                self.assertIn("CFF ", font)
                self.assertNotIn("glyf", font)
            finally:
                font.close()

    def test_cff_identity_strings_and_unique_id_are_removed(self) -> None:
        assert CFF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.otf"
            font = TTFont(str(CFF_SAMPLE), lazy=False)
            try:
                font["name"].setName(SECRET, 1, 3, 1, 0x0409)
                cff = font["CFF "].cff
                cff.fontNames = [SECRET]
                top = cff.topDictIndex[0]
                for key in ("FullName", "FamilyName", "Notice", "Copyright"):
                    setattr(top, key, SECRET)
                    if key not in top.order:
                        top.order.append(key)
                top.UniqueID = 8675309
                if "UniqueID" not in top.order:
                    top.order.append("UniqueID")
                font.save(str(source))
            finally:
                font.close()

            output = root / "output"
            result = build_browser_outputs(source, output, verify_rounds=2)
            native = output / result.native.filename
            self.assertFalse(contains_secret(native))
            blind = TTFont(str(native), lazy=False)
            try:
                cff = blind["CFF "].cff
                top = cff.topDictIndex[0]
                self.assertNotEqual(list(cff.fontNames), [SECRET])
                self.assertFalse(hasattr(top, "UniqueID"))
            finally:
                blind.close()

    def test_cff_generic_style_names_match_functional_flags(self) -> None:
        assert CFF_SAMPLE is not None
        with tempfile.TemporaryDirectory(prefix="fontblind-test-") as temp:
            root = Path(temp)
            source = root / "source.otf"
            font = TTFont(str(CFF_SAMPLE), lazy=False)
            try:
                font["OS/2"].usWeightClass = 700
                font["OS/2"].fsSelection = (int(font["OS/2"].fsSelection) & ~0x40) | 0x21
                font["head"].macStyle = int(font["head"].macStyle) | 0x03
                font.save(str(source))
            finally:
                font.close()

            result = build_browser_outputs(source, root / "output", verify_rounds=2)
            blind = TTFont(str(root / "output" / result.native.filename), lazy=False)
            try:
                self.assertEqual(blind["name"].getName(2, 3, 1, 0x0409).toUnicode(), "Bold Italic")
                self.assertEqual(list(blind["CFF "].cff.fontNames), ["Untitled-BoldItalic"])
            finally:
                blind.close()


if __name__ == "__main__":
    unittest.main()
