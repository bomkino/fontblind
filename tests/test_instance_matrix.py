from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables
from fontTools.varLib import builder as var_builder
from fontTools.varLib.featureVars import addFeatureVariations

from fontblind_instance import build_static_instance_outputs as build_core_instance
from fontblind_instance_proof import StaticInstanceProofError, verify_static_instance_outputs
from fontblind_instance_verified import build_static_instance_outputs
from fontblind_lab import build_slant_variable_outputs, build_variable_outputs
from tests.test_lab import write_fixture_font


_VARIATION_TABLES = {"avar", "cvar", "fvar", "gvar", "HVAR", "MVAR", "STAT", "VVAR"}


def _add_vertical_metrics(path: Path, *, weight: int, width_class: int) -> None:
    font = TTFont(str(path), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    try:
        glyph_order = font.getGlyphOrder()
        advance = 1000 + (weight - 400) // 5 + (width_class - 5) * 18
        metrics = {
            glyph_name: (advance + index * 7, 70 + (weight - 400) // 25 + index)
            for index, glyph_name in enumerate(glyph_order)
        }
        builder = FontBuilder(font=font)
        builder.setupVerticalMetrics(metrics)
        builder.setupVerticalHeader(ascent=900, descent=-300, lineGap=0)
        font.recalcTimestamp = False
        font.save(str(path), reorderTables=True)
    finally:
        font.close()


def _add_avar_mvar_and_feature_variations(path: Path) -> None:
    font = TTFont(str(path), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    try:
        axis_tags = [str(axis.axisTag) for axis in font["fvar"].axes]
        if "wght" not in axis_tags:
            raise AssertionError("Gate 5 fixture requires a weight axis")

        avar = newTable("avar")
        avar.majorVersion = 1
        avar.minorVersion = 0
        avar.segments = {
            tag: (
                {-1.0: -1.0, 0.0: 0.0, 0.5: 0.25, 1.0: 1.0}
                if tag == "wght"
                else {-1.0: -1.0, 0.0: 0.0, 1.0: 1.0}
            )
            for tag in axis_tags
        }
        font["avar"] = avar

        region_list = var_builder.buildVarRegionList(
            [{"wght": (0.0, 1.0, 1.0)}],
            axis_tags,
        )
        var_data = var_builder.buildVarData([0], [[60], [40]], optimize=False)
        store = var_builder.buildVarStore(region_list, [var_data])
        records = []
        for value_tag, var_index in (("hasc", 0), ("hcla", 1)):
            record = otTables.MetricsValueRecord()
            record.ValueTag = value_tag
            record.VarIdx = var_index
            records.append(record)
        mvar_table = newTable("MVAR")
        mvar = mvar_table.table = otTables.MVAR()
        mvar.Version = 0x00010000
        mvar.Reserved = 0
        mvar.VarStore = store
        mvar.ValueRecordSize = 8
        mvar.ValueRecordCount = len(records)
        mvar.ValueRecord = sorted(records, key=lambda record: record.ValueTag)
        font["MVAR"] = mvar_table

        addFeatureVariations(
            font,
            [([{"wght": (0.45, 1.0)}], {"A": "Aacute"})],
            featureTag="rvrn",
        )
        font.recalcTimestamp = False
        font.save(str(path), reorderTables=True)
    finally:
        font.close()


def _assert_static_layout(font: TTFont) -> None:
    if "GDEF" in font:
        if getattr(font["GDEF"].table, "VarStore", None) is not None:
            raise AssertionError("static output retained a GDEF variation store")
    for tag in ("GSUB", "GPOS"):
        if tag in font and getattr(font[tag].table, "FeatureVariations", None) is not None:
            raise AssertionError(f"static output retained {tag} feature variations")


class GateFiveInstanceMatrixTests(unittest.TestCase):
    def _build_rich_two_axis_source(self, root: Path) -> Path:
        donors: list[Path] = []
        for weight, width_class, label in (
            (400, 5, "default"),
            (300, 5, "light"),
            (700, 5, "bold"),
            (400, 3, "condensed"),
            (400, 7, "expanded"),
        ):
            donor = root / f"gate-five-{label}.ttf"
            write_fixture_font(
                donor,
                weight=weight,
                width_class=width_class,
                family=f"Gate Five {label}",
            )
            _add_vertical_metrics(donor, weight=weight, width_class=width_class)
            donors.append(donor)

        variable_dir = root / "variable"
        result = build_variable_outputs(donors, variable_dir)
        source = variable_dir / result.native.filename
        _add_avar_mvar_and_feature_variations(source)

        font = TTFont(str(source), lazy=False)
        try:
            for tag in ("fvar", "gvar", "HVAR", "VVAR", "avar", "MVAR"):
                self.assertIn(tag, font)
            self.assertIn("GDEF", font)
            self.assertIsNotNone(getattr(font["GDEF"].table, "VarStore", None))
            self.assertIn("GSUB", font)
            self.assertIsNotNone(getattr(font["GSUB"].table, "FeatureVariations", None))
        finally:
            font.close()
        return source

    def test_all_two_axis_corners_cross_points_and_fractional_interior(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-gate-five-matrix-") as temp_text:
            root = Path(temp_text)
            source = self._build_rich_two_axis_source(root)
            positions = [
                {"wght": weight, "wdth": width}
                for weight in (300.0, 400.0, 700.0)
                for width in (75.0, 100.0, 125.0)
            ]
            positions.append({"wght": 537.375, "wdth": 93.625})

            for index, location in enumerate(positions, start=1):
                with self.subTest(location=location):
                    output = root / f"instance-{index:02d}"
                    result = build_static_instance_outputs(source, output, location=location)
                    for path in (output / result.native.filename, output / result.web.filename):
                        font = TTFont(str(path), lazy=False)
                        try:
                            self.assertFalse(_VARIATION_TABLES & set(font.keys()))
                            _assert_static_layout(font)
                            self.assertIn("vmtx", font)
                            self.assertIn("vhea", font)
                        finally:
                            font.close()

            maximum = root / "instance-09" / "fontblind-instance.ttf"
            maximum_font = TTFont(str(maximum), lazy=False)
            try:
                self.assertEqual(int(maximum_font["OS/2"].sTypoAscender), 910)
                self.assertEqual(int(maximum_font["OS/2"].usWinAscent), 890)
                self.assertIsNone(getattr(maximum_font["GSUB"].table, "FeatureVariations", None))
            finally:
                maximum_font.close()

            fractional_output = root / "instance-10"
            first = {
                path.name: path.read_bytes()
                for path in fractional_output.iterdir()
                if path.is_file()
            }
            build_static_instance_outputs(
                source,
                fractional_output,
                location={"wght": 537.375, "wdth": 93.625},
            )
            second = {
                path.name: path.read_bytes()
                for path in fractional_output.iterdir()
                if path.is_file()
            }
            self.assertEqual(first, second)

    def test_slant_default_fractional_interior_and_endpoint_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-gate-five-slant-") as temp_text:
            root = Path(temp_text)
            upright = root / "upright.ttf"
            write_fixture_font(upright, weight=700, family="Gate Five Slant")
            variable = build_slant_variable_outputs(upright, root / "variable", angle=14)
            source = root / "variable" / variable.native.filename

            for index, slant in enumerate((0.0, -4.125, -9.5, -14.0), start=1):
                with self.subTest(slant=slant):
                    output = root / f"slant-{index:02d}"
                    result = build_static_instance_outputs(source, output, location={"slnt": slant})
                    font = TTFont(str(output / result.native.filename), lazy=False)
                    try:
                        style_names = {
                            record.toUnicode()
                            for record in font["name"].names
                            if int(record.nameID) == 2
                        }
                        self.assertEqual(style_names, {"Bold" if slant == 0 else "Bold Oblique"})
                        self.assertFalse(_VARIATION_TABLES & set(font.keys()))
                        _assert_static_layout(font)
                    finally:
                        font.close()
                    css = (output / result.css.filename).read_text(encoding="utf-8")
                    if slant == 0:
                        self.assertIn("font-style: normal", css)
                    else:
                        self.assertIn(f"font-style: oblique {abs(slant):g}deg", css)

    def test_independent_proof_rejects_changed_selected_metric(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-gate-five-tamper-") as temp_text:
            root = Path(temp_text)
            regular = root / "regular.ttf"
            bold = root / "bold.ttf"
            write_fixture_font(regular, weight=400, family="Gate Five Regular")
            write_fixture_font(bold, weight=700, family="Gate Five Bold")
            variable = build_variable_outputs([regular, bold], root / "variable")
            source = root / "variable" / variable.native.filename
            output = root / "core-output"
            result = build_core_instance(source, output, location={"wght": 550})

            native = output / result.native.filename
            font = TTFont(str(native), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
            try:
                font["OS/2"].sTypoAscender = int(font["OS/2"].sTypoAscender) + 1
                font.recalcTimestamp = False
                font.save(str(native), reorderTables=True)
            finally:
                font.close()

            with self.assertRaises(StaticInstanceProofError):
                verify_static_instance_outputs(
                    source,
                    native,
                    output / result.web.filename,
                    output / result.css.filename,
                    location={"wght": 550},
                )

    def test_failed_second_proof_preserves_last_verified_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-gate-five-stage-") as temp_text:
            root = Path(temp_text)
            regular = root / "regular.ttf"
            bold = root / "bold.ttf"
            write_fixture_font(regular, weight=400, family="Gate Five Regular")
            write_fixture_font(bold, weight=700, family="Gate Five Bold")
            variable = build_variable_outputs([regular, bold], root / "variable")
            source = root / "variable" / variable.native.filename
            output = root / "instance"

            build_static_instance_outputs(source, output, location={"wght": 500})
            before = {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}
            with mock.patch(
                "fontblind_instance_verified.verify_static_instance_outputs",
                side_effect=StaticInstanceProofError("forced independent-proof failure"),
            ):
                with self.assertRaises(StaticInstanceProofError):
                    build_static_instance_outputs(source, output, location={"wght": 600})
            after = {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
