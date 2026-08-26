from __future__ import annotations

import math
import tempfile
import unittest
import zipfile
from array import array
from pathlib import Path

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.ttProgram import Program
from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont

from fontblind_lab import (
    FontLabError,
    OBLIQUE_BUNDLE_NAME,
    OBLIQUE_CSS_NAME,
    OBLIQUE_NATIVE_NAME,
    OBLIQUE_WEB_NAME,
    SLANT_VARIABLE_BUNDLE_NAME,
    SLANT_VARIABLE_CSS_NAME,
    SLANT_VARIABLE_NATIVE_NAME,
    SLANT_VARIABLE_WEB_NAME,
    VARIABLE_BUNDLE_NAME,
    VARIABLE_CSS_NAME,
    VARIABLE_NATIVE_NAME,
    VARIABLE_WEB_NAME,
    _gpos_anchor_coordinates,
    build_oblique_outputs,
    build_slant_variable_outputs,
    build_variable_outputs,
)
from fontblind_outline import _glyf_signature


def _contour(points: list[tuple[int, int]]) -> object:
    pen = TTGlyphPen(None)
    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()
    return pen.glyph()


def _empty_glyph() -> object:
    return TTGlyphPen(None).glyph()


def _composite_glyph() -> object:
    pen = TTGlyphPen({"A": object(), "acute": object()})
    pen.addComponent("A", (1, 0, 0, 1, 0, 0))
    pen.addComponent("acute", (1, 0, 0, 1, 0, 100))
    return pen.glyph()


def _program(bytecode: bytes = b"\x00") -> Program:
    program = Program()
    program.fromBytecode(bytecode)
    return program


def write_fixture_font(
    path: Path,
    *,
    weight: int,
    family: str,
    width_class: int = 5,
    incompatible: bool = False,
) -> None:
    glyph_order = [".notdef", "space", "A", "acute", "Aacute"]
    spread = max(0, weight - 400) // 3
    width_percent = {1: 50.0, 2: 62.5, 3: 75.0, 4: 87.5, 5: 100.0, 6: 112.5, 7: 125.0, 8: 150.0, 9: 200.0}[width_class]

    def wide(value: int) -> int:
        return otRound(value * width_percent / 100.0)

    a_points = [(wide(100 - spread // 4), 0), (wide(300), 700), (wide(500 + spread // 3), 0)]
    if incompatible:
        a_points.insert(2, (430, 280))
    glyphs = {
        ".notdef": _contour([(wide(40), 0), (wide(40), 700), (wide(500), 700), (wide(500), 0)]),
        "space": _empty_glyph(),
        "A": _contour(a_points),
        "acute": _contour([(wide(160), 620), (wide(220), 750), (wide(310 + spread // 8), 750), (wide(240), 620)]),
        "Aacute": _composite_glyph(),
    }
    glyphs["A"].program = _program()
    glyphs["Aacute"].program = _program()

    advance = wide(620 + spread // 3)
    metrics = {
        ".notdef": (wide(540), wide(40)),
        "space": (wide(280), 0),
        "A": (advance, wide(45)),
        "acute": (wide(380), wide(40)),
        "Aacute": (advance, wide(45)),
    }

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({32: "space", 65: "A", 193: "Aacute", 0x301: "acute"})
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=850, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular" if weight == 400 else "Bold",
            "uniqueFontIdentifier": f"{family} Fixture Build",
            "fullName": f"{family} {'Regular' if weight == 400 else 'Bold'}",
            "psName": f"{family.replace(' ', '')}-{'Regular' if weight == 400 else 'Bold'}",
            "version": "Version 7.125",
        }
    )
    builder.setupOS2(
        version=4,
        sTypoAscender=850,
        sTypoDescender=-200,
        sTypoLineGap=0,
        usWinAscent=850,
        usWinDescent=200,
        usWeightClass=weight,
        usWidthClass=width_class,
        fsSelection=0x0040 if weight == 400 else 0x0020,
        fsType=0x0008,
        achVendID="TST1",
    )
    builder.setupPost()
    builder.setupMaxp()

    font = builder.font
    font["head"].macStyle = 0x0001 if weight >= 700 else 0
    cvt = newTable("cvt ")
    cvt.values = array("h", [20, 40])
    font["cvt "] = cvt
    for tag in ("fpgm", "prep"):
        table = newTable(tag)
        table.program = _program()
        font[tag] = table
    font["maxp"].maxZones = 2
    font["maxp"].maxTwilightPoints = 4
    font["maxp"].maxStorage = 8
    font["maxp"].maxFunctionDefs = 1
    font["maxp"].maxInstructionDefs = 1
    font["maxp"].maxStackElements = 8
    font["maxp"].maxSizeOfInstructions = 1

    addOpenTypeFeaturesFromString(
        font,
        f"""
        languagesystem DFLT dflt;
        markClass acute <anchor {wide(180)} 650> @TOP;
        feature mark {{
            pos base A <anchor {wide(300)} 700> mark @TOP;
        }} mark;
        """,
    )
    font.save(str(path), reorderTables=True)
    font.close()


def _coordinates(path: Path, glyph_name: str) -> tuple[tuple[int, int], ...]:
    font = TTFont(str(path), lazy=False)
    try:
        glyph = font["glyf"][glyph_name]
        glyph.expand(font["glyf"])
        return tuple(glyph.coordinates)
    finally:
        font.close()


class ObliqueLabTests(unittest.TestCase):
    def test_builds_zero_id_oblique_with_sheared_anchors_and_no_hints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            source = root / "fixture-upright.ttf"
            output = root / "output"
            write_fixture_font(source, weight=400, family="Fixture Upright Source")

            result = build_oblique_outputs(source, output, angle=12)
            native = output / result.native.filename
            web = output / result.web.filename
            css = output / result.css.filename
            bundle = output / result.bundle.filename
            self.assertEqual(result.native.filename, OBLIQUE_NATIVE_NAME)
            self.assertEqual(result.web.filename, OBLIQUE_WEB_NAME)
            self.assertEqual(result.css.filename, OBLIQUE_CSS_NAME)
            self.assertEqual(result.bundle.filename, OBLIQUE_BUNDLE_NAME)
            self.assertTrue(all(result.checks.values()))
            public_result = str(result.to_public_dict()).casefold()
            self.assertNotIn("fixture", public_result)
            self.assertNotIn("sha256", public_result)

            tangent = math.tan(math.radians(12))
            source_points = _coordinates(source, "A")
            output_points = _coordinates(native, "A")
            self.assertEqual(
                output_points,
                tuple((otRound(x + tangent * y), y) for x, y in source_points),
            )

            source_font = TTFont(str(source), lazy=False)
            output_font = TTFont(str(native), lazy=False)
            try:
                expected_anchors = tuple((otRound(x + tangent * y), y) for x, y in _gpos_anchor_coordinates(source_font))
                self.assertEqual(_gpos_anchor_coordinates(output_font), expected_anchors)
                self.assertTrue(int(output_font["OS/2"].fsSelection) & 0x0200)
                self.assertFalse(int(output_font["OS/2"].fsSelection) & 0x0001)
                self.assertFalse(int(output_font["head"].macStyle) & 0x0002)
                self.assertAlmostEqual(float(output_font["post"].italicAngle), -12.0, places=4)
                self.assertFalse({"cvar", "cvt ", "fpgm", "prep", "hdmx", "LTSH", "VDMX"} & set(output_font.keys()))
                for glyph_name in output_font.getGlyphOrder():
                    glyph = output_font["glyf"][glyph_name]
                    glyph.expand(output_font["glyf"])
                    self.assertFalse(getattr(glyph, "program", _program(b"")).getBytecode())
                styles = {
                    record.toUnicode()
                    for record in output_font["name"].names
                    if int(record.nameID) in {2, 17}
                }
                self.assertEqual(styles, {"Oblique"})
            finally:
                source_font.close()
                output_font.close()

            css_text = css.read_text(encoding="utf-8")
            self.assertIn("font-style: oblique 12deg", css_text)
            self.assertNotIn("local(", css_text.casefold())
            self.assertNotIn("Fixture", css_text)
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), [OBLIQUE_NATIVE_NAME, OBLIQUE_WEB_NAME, OBLIQUE_CSS_NAME])
            self.assertTrue(web.is_file())

            first = {path.name: path.read_bytes() for path in (native, web, css, bundle)}
            build_oblique_outputs(source, output, angle=12)
            self.assertEqual(first, {path.name: path.read_bytes() for path in (native, web, css, bundle)})

    def test_rejects_angles_outside_the_declared_lane(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            source = root / "fixture-upright.ttf"
            output = root / "output"
            write_fixture_font(source, weight=400, family="Fixture Upright Source")
            for angle in (3.99, 20.01, float("nan")):
                with self.subTest(angle=angle), self.assertRaises(FontLabError):
                    build_oblique_outputs(source, output, angle=angle)
            self.assertFalse((output / OBLIQUE_NATIVE_NAME).exists())

    def test_builds_verified_slant_axis_from_one_upright(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            source = root / "fixture-upright.ttf"
            output = root / "output"
            write_fixture_font(source, weight=400, family="Fixture Slant Source")

            result = build_slant_variable_outputs(source, output, angle=14)
            native = output / result.native.filename
            css = output / result.css.filename
            bundle = output / result.bundle.filename
            self.assertEqual(result.native.filename, SLANT_VARIABLE_NATIVE_NAME)
            self.assertEqual(result.web.filename, SLANT_VARIABLE_WEB_NAME)
            self.assertEqual(result.css.filename, SLANT_VARIABLE_CSS_NAME)
            self.assertEqual(result.bundle.filename, SLANT_VARIABLE_BUNDLE_NAME)
            self.assertTrue(all(result.checks.values()))
            self.assertEqual(
                result.axes,
                ({"tag": "slnt", "name": "Slant", "min": -14.0, "default": 0.0, "max": 0.0},),
            )

            variable_font = TTFont(str(native), lazy=False)
            try:
                axis = variable_font["fvar"].axes[0]
                self.assertEqual(str(axis.axisTag), "slnt")
                self.assertEqual((axis.minValue, axis.defaultValue, axis.maxValue), (-14.0, 0.0, 0.0))
                self.assertEqual(float(variable_font["post"].italicAngle), 0.0)
            finally:
                variable_font.close()
            self.assertIn("font-style: oblique 0deg 14deg", css.read_text(encoding="utf-8"))
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [SLANT_VARIABLE_NATIVE_NAME, SLANT_VARIABLE_WEB_NAME, SLANT_VARIABLE_CSS_NAME],
                )


class VariableLabTests(unittest.TestCase):
    def test_builds_exact_weight_axis_from_compatible_donors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            regular = root / "fixture-regular.ttf"
            bold = root / "fixture-bold.ttf"
            output = root / "output"
            write_fixture_font(regular, weight=400, family="Fixture Regular Origin")
            write_fixture_font(bold, weight=700, family="Fixture Bold Origin")

            result = build_variable_outputs([bold, regular], output)
            native = output / result.native.filename
            web = output / result.web.filename
            css = output / result.css.filename
            bundle = output / result.bundle.filename
            self.assertEqual(result.native.filename, VARIABLE_NATIVE_NAME)
            self.assertEqual(result.web.filename, VARIABLE_WEB_NAME)
            self.assertEqual(result.css.filename, VARIABLE_CSS_NAME)
            self.assertEqual(result.bundle.filename, VARIABLE_BUNDLE_NAME)
            self.assertTrue(all(result.checks.values()))
            public_result = str(result.to_public_dict()).casefold()
            self.assertNotIn("fixture", public_result)
            self.assertNotIn("sha256", public_result)

            variable_font = TTFont(str(native), lazy=False)
            donor_font = TTFont(str(bold), lazy=False)
            try:
                self.assertIn("gvar", variable_font)
                self.assertIn("STAT", variable_font)
                self.assertEqual(len(variable_font["fvar"].axes), 1)
                axis = variable_font["fvar"].axes[0]
                self.assertEqual(str(axis.axisTag), "wght")
                self.assertEqual((axis.minValue, axis.defaultValue, axis.maxValue), (400.0, 400.0, 700.0))
                names = {
                    record.toUnicode()
                    for record in variable_font["name"].names
                    if int(record.nameID) == int(axis.axisNameID)
                }
                self.assertEqual(names, {"Weight"})
                instance = instantiateVariableFont(
                    variable_font,
                    {"wght": 700},
                    inplace=False,
                    overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
                    static=True,
                )
                try:
                    for glyph_id in range(int(donor_font["maxp"].numGlyphs)):
                        self.assertEqual(_glyf_signature(instance, glyph_id), _glyf_signature(donor_font, glyph_id))
                finally:
                    instance.close()
            finally:
                variable_font.close()
                donor_font.close()

            css_text = css.read_text(encoding="utf-8")
            self.assertIn("font-weight: 400 700", css_text)
            self.assertIn('format("woff2-variations")', css_text)
            self.assertNotIn("Fixture", css_text)
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), [VARIABLE_NATIVE_NAME, VARIABLE_WEB_NAME, VARIABLE_CSS_NAME])
            self.assertTrue(web.is_file())

            first = {path.name: path.read_bytes() for path in (native, web, css, bundle)}
            build_variable_outputs([regular, bold], output)
            self.assertEqual(first, {path.name: path.read_bytes() for path in (native, web, css, bundle)})

    def test_rejects_incompatible_donor_topology_without_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            regular = root / "fixture-regular.ttf"
            incompatible = root / "fixture-incompatible.ttf"
            output = root / "output"
            write_fixture_font(regular, weight=400, family="Fixture Regular Origin")
            write_fixture_font(incompatible, weight=700, family="Fixture Incompatible Origin", incompatible=True)

            with self.assertRaisesRegex(FontLabError, "topology"):
                build_variable_outputs([regular, incompatible], output)
            self.assertFalse((output / VARIABLE_NATIVE_NAME).exists())
            self.assertFalse((output / VARIABLE_WEB_NAME).exists())
            self.assertFalse((output / VARIABLE_CSS_NAME).exists())
            self.assertFalse((output / VARIABLE_BUNDLE_NAME).exists())

    def test_builds_independent_weight_and_width_axes_from_real_grid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            donors: list[Path] = []
            for weight, width in ((400, 5), (700, 5), (400, 3), (700, 3)):
                path = root / f"fixture-{weight}-{width}.ttf"
                write_fixture_font(path, weight=weight, width_class=width, family=f"Fixture {weight} {width}")
                donors.append(path)
            output = root / "output"

            result = build_variable_outputs(list(reversed(donors)), output)
            self.assertTrue(all(result.checks.values()))
            self.assertEqual([axis["tag"] for axis in result.axes], ["wght", "wdth"])
            self.assertEqual(result.axes[0], {"tag": "wght", "name": "Weight", "min": 400.0, "default": 400.0, "max": 700.0})
            self.assertEqual(result.axes[1], {"tag": "wdth", "name": "Width", "min": 75.0, "default": 100.0, "max": 100.0})

            native = TTFont(str(output / VARIABLE_NATIVE_NAME), lazy=False)
            try:
                self.assertEqual([str(axis.axisTag) for axis in native["fvar"].axes], ["wght", "wdth"])
                self.assertEqual(int(native["OS/2"].usWeightClass), 400)
                self.assertEqual(int(native["OS/2"].usWidthClass), 5)
            finally:
                native.close()
            css = (output / VARIABLE_CSS_NAME).read_text(encoding="utf-8")
            self.assertIn("font-weight: 400 700", css)
            self.assertIn("font-stretch: 75% 100%", css)

    def test_builds_width_only_axis(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            condensed = root / "fixture-condensed.ttf"
            normal = root / "fixture-normal.ttf"
            output = root / "output"
            write_fixture_font(condensed, weight=400, width_class=3, family="Fixture Condensed")
            write_fixture_font(normal, weight=400, width_class=5, family="Fixture Normal")

            result = build_variable_outputs([condensed, normal], output)
            self.assertEqual([axis["tag"] for axis in result.axes], ["wdth"])
            css = (output / VARIABLE_CSS_NAME).read_text(encoding="utf-8")
            self.assertIn("font-weight: 400", css)
            self.assertIn("font-stretch: 75% 100%", css)

    def test_rejects_coupled_two_axis_diagonal_without_independent_extremes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontlab-test-") as temp_text:
            root = Path(temp_text)
            condensed_regular = root / "fixture-condensed-regular.ttf"
            normal_bold = root / "fixture-normal-bold.ttf"
            output = root / "output"
            write_fixture_font(condensed_regular, weight=400, width_class=3, family="Fixture One")
            write_fixture_font(normal_bold, weight=700, width_class=5, family="Fixture Two")

            with self.assertRaisesRegex(FontLabError, "independent"):
                build_variable_outputs([condensed_regular, normal_bold], output)
            self.assertFalse((output / VARIABLE_NATIVE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
