from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont

from fontblind_instance_verified import build_static_instance_outputs
from fontblind_lab import FontLabError, build_oblique_outputs, build_variable_outputs
from fontblind_pipeline import _decode_woff2, _harfbuzz_shape, build_browser_outputs


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "corpus" / "manifest.json"
CORPUS_DIR = Path(os.environ.get("FONTBLIND_CORPUS_DIR", ROOT / "tests" / "corpus" / "cache"))
RUN_FULL_CORPUS = os.environ.get("FONTBLIND_RUN_FULL_CORPUS") == "1"
VARIATION_TABLES = frozenset({"avar", "cvar", "fvar", "gvar", "HVAR", "MVAR", "STAT", "VVAR"})
SCRIPT_PROBES = {
    "Latin": "AVATAR office affinity ffi fl Á V̈ — 0123456789",
    "Arabic": "السَّلَامُ عَلَيْكُمْ العربية",
    "Devanagari": "नमस्ते दुनिया क्षत्रिय प्रज्ञा",
    "Hebrew": "שָׁלוֹם עוֹלָם בְּרָכָה",
    "Thai": "สวัสดีชาวโลก ภาษาไทย",
}


def _manifest_assets() -> tuple[dict[str, object], ...]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return tuple(dict(asset) for asset in value["assets"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _axis_rows(path: Path) -> dict[str, tuple[float, float, float]]:
    font = TTFont(str(path), lazy=False)
    try:
        return {
            str(axis.axisTag): (float(axis.minValue), float(axis.defaultValue), float(axis.maxValue))
            for axis in font["fvar"].axes
        }
    finally:
        font.close()


def _save_instance(source: Path, output: Path, location: dict[str, float]) -> None:
    font = TTFont(str(source), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    try:
        instance = instantiateVariableFont(
            font,
            location,
            inplace=False,
            optimize=True,
            overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
            updateFontNames=False,
            static=True,
        )
    finally:
        font.close()
    try:
        instance.flavor = None
        instance.recalcTimestamp = False
        instance.save(str(output), reorderTables=True)
    finally:
        instance.close()


@unittest.skipUnless(
    CORPUS_DIR.is_dir() and RUN_FULL_CORPUS,
    "full pinned corpus gate disabled; set FONTBLIND_RUN_FULL_CORPUS=1",
)
class RepresentativeCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assets = _manifest_assets()
        cls.by_id = {str(asset["id"]): asset for asset in cls.assets}
        missing = [str(asset["filename"]) for asset in cls.assets if not (CORPUS_DIR / str(asset["filename"])).is_file()]
        if missing:
            raise AssertionError("release corpus is incomplete: " + ", ".join(missing))

    def test_manifest_bytes_are_exact_and_every_asset_passes_the_full_pipeline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-corpus-pipeline-") as temp_text:
            root = Path(temp_text)
            for asset in self.assets:
                asset_id = str(asset["id"])
                source = CORPUS_DIR / str(asset["filename"])
                with self.subTest(asset=asset_id):
                    self.assertEqual(source.stat().st_size, int(asset["size"]))
                    self.assertEqual(_sha256(source), str(asset["sha256"]))

                    output = root / asset_id
                    result = build_browser_outputs(source, output, verify_rounds=1)
                    result.require_verified()
                    native = output / result.native.filename
                    web = output / result.web.filename
                    decoded = output / ("decoded.ttf" if result.native.filename.endswith(".ttf") else "decoded.otf")
                    _decode_woff2(web, decoded)

                    source_font = TTFont(str(source), lazy=False)
                    native_font = TTFont(str(native), lazy=False)
                    try:
                        self.assertEqual("fvar" in source_font, result.variable)
                        self.assertEqual("fvar" in native_font, result.variable)
                        if asset_id == "cff-static-otf":
                            self.assertIn("CFF ", native_font)
                            self.assertNotIn("glyf", native_font)
                    finally:
                        source_font.close()
                        native_font.close()

                    probe = SCRIPT_PROBES[str(asset["script"])]
                    source_shape = _harfbuzz_shape(source, probe)
                    self.assertGreater(len(source_shape), 1)
                    self.assertTrue(any(glyph_id != 0 for glyph_id, *_rest in source_shape))
                    self.assertEqual(source_shape, _harfbuzz_shape(native, probe))
                    self.assertEqual(source_shape, _harfbuzz_shape(decoded, probe))

                    with zipfile.ZipFile(output / result.bundle.filename, "r") as archive:
                        self.assertEqual(
                            archive.namelist(),
                            [result.native.filename, result.web.filename, result.css.filename],
                        )

    def test_real_static_latin_font_survives_oblique_lab(self) -> None:
        source = CORPUS_DIR / str(self.by_id["latin-static-ttf"]["filename"])
        with tempfile.TemporaryDirectory(prefix="fontblind-corpus-oblique-") as temp_text:
            output = Path(temp_text)
            result = build_oblique_outputs(source, output, angle=12.0)
            result.require_verified()
            native = output / result.native.filename
            font = TTFont(str(native), lazy=False)
            try:
                self.assertTrue(int(font["OS/2"].fsSelection) & 0x0200)
                self.assertFalse(int(font["OS/2"].fsSelection) & 0x0001)
                self.assertFalse(int(font["head"].macStyle) & 0x0002)
            finally:
                font.close()
            self.assertEqual(
                _harfbuzz_shape(source, SCRIPT_PROBES["Latin"]),
                _harfbuzz_shape(native, SCRIPT_PROBES["Latin"]),
            )

    def test_real_script_variable_fonts_freeze_at_fractional_interiors(self) -> None:
        for asset_id in ("arabic-variable-ttf", "devanagari-variable-ttf", "thai-variable-ttf"):
            asset = self.by_id[asset_id]
            source = CORPUS_DIR / str(asset["filename"])
            with self.subTest(asset=asset_id), tempfile.TemporaryDirectory(
                prefix=f"fontblind-corpus-freeze-{asset_id}-"
            ) as temp_text:
                root = Path(temp_text)
                variable = build_browser_outputs(source, root / "variable", verify_rounds=1)
                generated = root / "variable" / variable.native.filename
                axes = _axis_rows(generated)
                location = {
                    tag: minimum + (maximum - minimum) * 0.417
                    for tag, (minimum, _default, maximum) in axes.items()
                }
                frozen = build_static_instance_outputs(generated, root / "static", location=location)
                frozen.require_verified()
                native = root / "static" / frozen.native.filename
                font = TTFont(str(native), lazy=False)
                try:
                    self.assertFalse(VARIATION_TABLES & set(font.keys()))
                finally:
                    font.close()

    def test_extracted_existing_variable_donors_fail_closed_on_geometry_drift(self) -> None:
        source = CORPUS_DIR / str(self.by_id["hebrew-variable-ttf"]["filename"])
        axes = _axis_rows(source)
        self.assertEqual(set(axes), {"wdth", "wght"})
        weight_min, weight_default, weight_max = axes["wght"]
        width_min, width_default, width_max = axes["wdth"]
        weight_extreme = weight_max if weight_max != weight_default else weight_min
        width_extreme = width_min if width_min != width_default else width_max

        with tempfile.TemporaryDirectory(prefix="fontblind-corpus-donors-") as temp_text:
            root = Path(temp_text)
            donors = [root / "default.ttf", root / "weight.ttf", root / "width.ttf"]
            _save_instance(source, donors[0], {"wght": weight_default, "wdth": width_default})
            _save_instance(source, donors[1], {"wght": weight_extreme, "wdth": width_default})
            _save_instance(source, donors[2], {"wght": weight_default, "wdth": width_extreme})

            output = root / "variable"
            with self.assertRaisesRegex(FontLabError, "does not match donor geometry"):
                build_variable_outputs(donors, output)
            self.assertEqual(list(output.iterdir()) if output.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
