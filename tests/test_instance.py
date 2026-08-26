from __future__ import annotations

import math
import tempfile
import unittest
import zipfile
from pathlib import Path

from fontTools.ttLib import TTFont

from fontblind_instance import (
    INSTANCE_BUNDLE_NAME,
    INSTANCE_CSS_NAME,
    INSTANCE_NATIVE_NAME,
    INSTANCE_WEB_NAME,
    StaticInstanceError,
    build_static_instance_outputs,
)
from fontblind_lab import build_slant_variable_outputs, build_variable_outputs
from tests.test_lab import write_fixture_font


_VARIATION_TABLES = {"avar", "cvar", "fvar", "gvar", "HVAR", "MVAR", "VVAR"}


class StaticInstanceTests(unittest.TestCase):
    def test_freezes_verified_weight_location_without_source_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-instance-test-") as temp_text:
            root = Path(temp_text)
            regular = root / "revealing-regular.ttf"
            bold = root / "revealing-bold.ttf"
            variable_dir = root / "variable"
            instance_dir = root / "instance"
            write_fixture_font(regular, weight=400, family="Revealing Regular Origin")
            write_fixture_font(bold, weight=700, family="Revealing Bold Origin")

            variable = build_variable_outputs([regular, bold], variable_dir)
            source = variable_dir / variable.native.filename
            result = build_static_instance_outputs(source, instance_dir, location={"wght": 550})

            native = instance_dir / result.native.filename
            web = instance_dir / result.web.filename
            css = instance_dir / result.css.filename
            bundle = instance_dir / result.bundle.filename
            self.assertEqual(result.native.filename, INSTANCE_NATIVE_NAME)
            self.assertEqual(result.web.filename, INSTANCE_WEB_NAME)
            self.assertEqual(result.css.filename, INSTANCE_CSS_NAME)
            self.assertEqual(result.bundle.filename, INSTANCE_BUNDLE_NAME)
            self.assertFalse(result.variable)
            self.assertTrue(all(result.checks.values()))
            self.assertNotIn("revealing", str(result.to_public_dict()).casefold())

            font = TTFont(str(native), lazy=False)
            try:
                self.assertFalse(_VARIATION_TABLES & set(font.keys()))
                self.assertEqual(int(font["OS/2"].usWeightClass), 550)
                self.assertFalse(int(font["OS/2"].fsSelection) & 0x0001)
                self.assertFalse(int(font["OS/2"].fsSelection) & 0x0200)
                names = {record.toUnicode() for record in font["name"].names}
                self.assertTrue(any(name.startswith("Untitled") for name in names))
                self.assertFalse(any("Revealing" in name for name in names))
            finally:
                font.close()

            css_text = css.read_text(encoding="utf-8")
            self.assertIn("font-weight: 550", css_text)
            self.assertIn("font-style: normal", css_text)
            self.assertNotIn("local(", css_text.casefold())
            self.assertTrue(web.is_file())
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), [INSTANCE_NATIVE_NAME, INSTANCE_WEB_NAME, INSTANCE_CSS_NAME])

            first = {path.name: path.read_bytes() for path in (native, web, css, bundle)}
            build_static_instance_outputs(source, instance_dir, location={"wght": 550})
            self.assertEqual(first, {path.name: path.read_bytes() for path in (native, web, css, bundle)})

    def test_freezes_intermediate_mechanical_slant_as_oblique_not_italic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-instance-test-") as temp_text:
            root = Path(temp_text)
            upright = root / "revealing-upright.ttf"
            variable_dir = root / "slant"
            instance_dir = root / "instance"
            write_fixture_font(upright, weight=400, family="Revealing Upright Origin")

            variable = build_slant_variable_outputs(upright, variable_dir, angle=14)
            source = variable_dir / variable.native.filename
            result = build_static_instance_outputs(source, instance_dir, location={"slnt": -10})
            native = instance_dir / result.native.filename

            font = TTFont(str(native), lazy=False)
            try:
                selection = int(font["OS/2"].fsSelection)
                self.assertTrue(selection & 0x0200)
                self.assertFalse(selection & 0x0001)
                self.assertFalse(selection & 0x0040)
                self.assertFalse(int(font["head"].macStyle) & 0x0002)
                self.assertAlmostEqual(float(font["post"].italicAngle), -10.0, places=4)
                self.assertFalse(_VARIATION_TABLES & set(font.keys()))
            finally:
                font.close()

            css_text = (instance_dir / result.css.filename).read_text(encoding="utf-8")
            self.assertIn("font-style: oblique 10deg", css_text)

    def test_freezes_two_axis_interior_location(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-instance-test-") as temp_text:
            root = Path(temp_text)
            donors = []
            for weight, width_class, label in (
                (400, 5, "regular"),
                (700, 5, "bold"),
                (400, 3, "condensed"),
            ):
                path = root / f"revealing-{label}.ttf"
                write_fixture_font(path, weight=weight, width_class=width_class, family=f"Revealing {label}")
                donors.append(path)
            variable_dir = root / "variable"
            instance_dir = root / "instance"
            variable = build_variable_outputs(donors, variable_dir)
            result = build_static_instance_outputs(
                variable_dir / variable.native.filename,
                instance_dir,
                location={"wght": 525, "wdth": 87.5},
            )
            font = TTFont(str(instance_dir / result.native.filename), lazy=False)
            try:
                self.assertEqual(int(font["OS/2"].usWeightClass), 525)
                self.assertEqual(int(font["OS/2"].usWidthClass), 4)
                self.assertFalse(_VARIATION_TABLES & set(font.keys()))
            finally:
                font.close()
            css_text = (instance_dir / result.css.filename).read_text(encoding="utf-8")
            self.assertIn("font-weight: 525", css_text)
            self.assertIn("font-stretch: 87.5%", css_text)

    def test_rejects_incomplete_nonfinite_out_of_range_and_static_sources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-instance-test-") as temp_text:
            root = Path(temp_text)
            regular = root / "regular.ttf"
            bold = root / "bold.ttf"
            variable_dir = root / "variable"
            output = root / "instance"
            write_fixture_font(regular, weight=400, family="Fixture Regular")
            write_fixture_font(bold, weight=700, family="Fixture Bold")
            variable = build_variable_outputs([regular, bold], variable_dir)
            source = variable_dir / variable.native.filename

            for location in ({}, {"wght": math.nan}, {"wght": 399}, {"wght": 701}, {"wght": True}, {"wdth": 100}):
                with self.subTest(location=location), self.assertRaises(StaticInstanceError):
                    build_static_instance_outputs(source, output, location=location)
            with self.assertRaises(StaticInstanceError):
                build_static_instance_outputs(regular, output, location={"wght": 400})
            self.assertFalse((output / INSTANCE_NATIVE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
