"""Verified static-instance export for generated FontBlind variable fonts.

This lane accepts only an already generated, zero-ID TrueType variable font.
It pins every registered axis to one explicit location, removes variation
machinery, repairs static metadata, and proves the resulting native/WOFF2
package against a fresh FontTools instantiation before committing outputs.
"""
from __future__ import annotations

import math
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Mapping

from fontTools.misc.roundTools import otRound
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont

import fontblind_surgical as surgical
from fontblind_lab import _gpos_anchor_coordinates, _hmtx_by_gid
from fontblind_outline import _glyf_signature
from fontblind_pipeline import (
    CSS_FAMILY,
    OutputFile,
    PublicBuildResult,
    _decode_woff2,
    _deterministic_bundle,
    _verify_shaping,
    _verify_woff2_roundtrip,
)
from fontblind_policy import FUNCTIONAL_TABLES, SourceContract, inspect_strict_source
from fontblind_web import WebBuildError, build_full_woff2


INSTANCE_NATIVE_NAME = "fontblind-instance.ttf"
INSTANCE_WEB_NAME = "fontblind-instance.woff2"
INSTANCE_CSS_NAME = "fontblind-instance.css"
INSTANCE_BUNDLE_NAME = "fontblind-instance-package.zip"

_ALLOWED_AXES = frozenset({"wght", "wdth", "slnt"})
_VARIATION_TABLES = frozenset({"avar", "cvar", "fvar", "gvar", "HVAR", "MVAR", "STAT", "VVAR"})
_WIDTH_PERCENT = {
    1: 50.0,
    2: 62.5,
    3: 75.0,
    4: 87.5,
    5: 100.0,
    6: 112.5,
    7: 125.0,
    8: 150.0,
    9: 200.0,
}


class StaticInstanceError(surgical.FontBlindError):
    """A requested frozen location cannot satisfy the static-instance contract."""


def _load(path: Path) -> TTFont:
    try:
        return TTFont(
            str(path),
            lazy=False,
            recalcBBoxes=False,
            recalcTimestamp=False,
            ignoreDecompileErrors=False,
        )
    except Exception as exc:
        raise StaticInstanceError("The generated variable font could not be reopened safely.") from exc


def _axis_specs(font: TTFont) -> dict[str, tuple[float, float, float]]:
    if "glyf" not in font or "fvar" not in font or "gvar" not in font:
        raise StaticInstanceError("Static export needs a generated TrueType variable font.")
    specs: dict[str, tuple[float, float, float]] = {}
    for axis in font["fvar"].axes:
        tag = str(axis.axisTag)
        if tag not in _ALLOWED_AXES or tag in specs:
            raise StaticInstanceError("Static export encountered an unsupported generated axis.")
        minimum = float(axis.minValue)
        default = float(axis.defaultValue)
        maximum = float(axis.maxValue)
        if not all(math.isfinite(value) for value in (minimum, default, maximum)) or not minimum <= default <= maximum:
            raise StaticInstanceError("Static export encountered malformed generated axis bounds.")
        specs[tag] = (minimum, default, maximum)
    if not specs:
        raise StaticInstanceError("Static export found no generated variable axes.")
    return specs


def _validated_location(font: TTFont, location: Mapping[str, object]) -> dict[str, float]:
    if not isinstance(location, Mapping):
        raise StaticInstanceError("Choose one complete generated-axis location.")
    specs = _axis_specs(font)
    if set(location) != set(specs):
        raise StaticInstanceError("Static export needs one value for every generated axis.")
    result: dict[str, float] = {}
    for tag, raw_value in location.items():
        if isinstance(raw_value, bool):
            raise StaticInstanceError("Static export axis values must be finite numbers.")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise StaticInstanceError("Static export axis values must be finite numbers.") from exc
        if not math.isfinite(value):
            raise StaticInstanceError("Static export axis values must be finite numbers.")
        minimum, _default, maximum = specs[tag]
        if value < minimum or value > maximum:
            raise StaticInstanceError("A static export axis value is outside the generated range.")
        result[tag] = value
    return result


def _css_number(value: float) -> str:
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _nearest_width_class(percent: float) -> int:
    return min(_WIDTH_PERCENT, key=lambda value: (abs(_WIDTH_PERCENT[value] - percent), value))


def _selected_values(font: TTFont, location: Mapping[str, float]) -> tuple[float, float, float]:
    os2 = font["OS/2"]
    weight = float(location.get("wght", int(os2.usWeightClass)))
    width = float(location.get("wdth", _WIDTH_PERCENT.get(int(os2.usWidthClass), 100.0)))
    slant = float(location.get("slnt", 0.0))
    return weight, width, slant


def _drop_variation_tables(font: TTFont) -> None:
    for tag in _VARIATION_TABLES:
        if tag in font:
            del font[tag]


def _assert_no_variable_layout(font: TTFont) -> None:
    """Reject unresolved variation stores or feature substitutions."""
    if "GDEF" in font and getattr(font["GDEF"].table, "VarStore", None) is not None:
        raise StaticInstanceError("Static export retained a GDEF variation store.")
    for tag in ("GSUB", "GPOS"):
        if tag in font and getattr(font[tag].table, "FeatureVariations", None) is not None:
            raise StaticInstanceError("Static export retained variable layout substitutions.")


def _set_static_metadata(font: TTFont, location: Mapping[str, float]) -> tuple[float, float, float]:
    weight, width, slant = _selected_values(font, location)
    os2 = font["OS/2"]
    weight_class = max(1, min(1000, int(otRound(weight))))
    width_class = _nearest_width_class(width)
    os2.usWeightClass = weight_class
    os2.usWidthClass = width_class

    # fsSelection: ITALIC=0, BOLD=5, REGULAR=6, OBLIQUE=9.
    selection = int(os2.fsSelection) & ~(0x0001 | 0x0020 | 0x0040 | 0x0200)
    is_bold = weight_class >= 700
    is_oblique = abs(slant) > 1 / 65536
    if is_bold:
        selection |= 0x0020
    if is_oblique:
        if int(os2.version) < 1:
            os2.ulCodePageRange1 = 0
            os2.ulCodePageRange2 = 0
        if int(os2.version) < 2:
            os2.sxHeight = 0
            os2.sCapHeight = 0
            os2.usDefaultChar = 0
            os2.usBreakChar = 32
            os2.usMaxContext = 1
        if int(os2.version) < 4:
            os2.version = 4
        selection |= 0x0200
    if not is_bold and not is_oblique:
        selection |= 0x0040
    os2.fsSelection = selection

    head = font["head"]
    head.macStyle = (int(head.macStyle) & ~0x0003) | (0x0001 if is_bold else 0)
    if "post" in font:
        font["post"].italicAngle = slant if is_oblique else 0.0
    hhea = font["hhea"]
    hhea.caretSlopeRise = 1000
    hhea.caretSlopeRun = int(otRound(1000.0 * math.tan(math.radians(-slant)))) if is_oblique else 0
    hhea.caretOffset = 0
    return weight, width, slant


def _instance_css(weight: float, width: float, slant: float) -> str:
    style = "normal" if abs(slant) <= 1 / 65536 else f"oblique {_css_number(abs(slant))}deg"
    return (
        "@font-face {\n"
        f'  font-family: "{CSS_FAMILY}";\n'
        f'  src: url("{INSTANCE_WEB_NAME}") format("woff2");\n'
        f"  font-weight: {_css_number(weight)};\n"
        f"  font-style: {style};\n"
        f"  font-stretch: {_css_number(width)}%;\n"
        "  font-display: swap;\n"
        "}\n"
    )


def _assert_generated_zero_id(path: Path) -> SourceContract:
    contract = inspect_strict_source(path)
    if contract.native_suffix != ".ttf" or contract.outline_flavor != "TrueType" or not contract.variable:
        raise StaticInstanceError("Static export accepts only FontBlind-generated TrueType variable fonts.")
    report = surgical.audit_font(path)
    if not report.ok:
        raise StaticInstanceError("The generated variable source no longer satisfies the zero-ID contract.")
    return contract


def _assert_static_zero_id(path: Path) -> None:
    report = surgical.audit_font(path)
    if not report.ok:
        raise StaticInstanceError("The frozen instance failed the zero-ID metadata audit.")
    font = _load(path)
    try:
        retained = sorted(set(font.reader.keys()) & set(_VARIATION_TABLES))
        if retained:
            raise StaticInstanceError("The frozen instance retained variable-font machinery.")
        _assert_no_variable_layout(font)
        unexpected = sorted(set(font.reader.keys()) - set(FUNCTIONAL_TABLES))
        if unexpected:
            raise StaticInstanceError("The frozen instance retained an unreviewed font table.")
    finally:
        font.close()


def _assert_matching_static(reference_path: Path, output_path: Path) -> None:
    reference = _load(reference_path)
    output = _load(output_path)
    try:
        if tuple(reference.getGlyphOrder()) != tuple(output.getGlyphOrder()):
            raise StaticInstanceError("Static export changed glyph order.")
        if int(reference["maxp"].numGlyphs) != int(output["maxp"].numGlyphs):
            raise StaticInstanceError("Static export changed glyph count.")
        if int(reference["head"].unitsPerEm) != int(output["head"].unitsPerEm):
            raise StaticInstanceError("Static export changed units-per-em.")
        if surgical._cmap_snapshot(reference) != surgical._cmap_snapshot(output):
            raise StaticInstanceError("Static export changed character mapping.")
        if _hmtx_by_gid(reference) != _hmtx_by_gid(output):
            raise StaticInstanceError("Static export changed selected-location metrics.")
        if _gpos_anchor_coordinates(reference) != _gpos_anchor_coordinates(output):
            raise StaticInstanceError("Static export changed selected-location anchors.")
        glyph_count = int(reference["maxp"].numGlyphs)
        for glyph_id in range(glyph_count):
            if _glyf_signature(reference, glyph_id) != _glyf_signature(output, glyph_id):
                raise StaticInstanceError("Static export changed selected-location geometry.")
        if set(output.reader.keys()) & set(_VARIATION_TABLES):
            raise StaticInstanceError("Static export retained variable-font machinery.")
        _assert_no_variable_layout(output)
    finally:
        reference.close()
        output.close()
    _verify_shaping(reference_path, output_path)


def _verify_selected_metadata(path: Path, location: Mapping[str, float]) -> None:
    font = _load(path)
    try:
        weight, width, slant = _selected_values(font, location)
        expected_weight = max(1, min(1000, int(otRound(weight))))
        expected_width = _nearest_width_class(width)
        os2 = font["OS/2"]
        selection = int(os2.fsSelection)
        is_bold = expected_weight >= 700
        is_oblique = abs(slant) > 1 / 65536
        if int(os2.usWeightClass) != expected_weight or int(os2.usWidthClass) != expected_width:
            raise StaticInstanceError("Static export emitted incorrect weight or width metadata.")
        if bool(selection & 0x0020) != is_bold or bool(selection & 0x0200) != is_oblique:
            raise StaticInstanceError("Static export emitted incoherent style metadata.")
        if selection & 0x0001 or int(font["head"].macStyle) & 0x0002:
            raise StaticInstanceError("A mechanical slant was incorrectly labelled Italic.")
        if bool(int(font["head"].macStyle) & 0x0001) != is_bold:
            raise StaticInstanceError("Static export emitted incoherent bold metadata.")
        if bool(selection & 0x0040) != (not is_bold and not is_oblique):
            raise StaticInstanceError("Static export emitted incoherent Regular metadata.")
        if "post" in font:
            expected_angle = slant if is_oblique else 0.0
            if abs(float(font["post"].italicAngle) - expected_angle) > 1 / 65536:
                raise StaticInstanceError("Static export emitted an incorrect slant angle.")
        expected_caret_run = int(otRound(1000.0 * math.tan(math.radians(-slant)))) if is_oblique else 0
        hhea = font["hhea"]
        if (
            int(hhea.caretSlopeRise) != 1000
            or int(hhea.caretSlopeRun) != expected_caret_run
            or int(hhea.caretOffset) != 0
        ):
            raise StaticInstanceError("Static export emitted an incorrect text-caret slope.")
        styles = {
            record.toUnicode()
            for record in font["name"].names
            if int(record.nameID) in {2, 17}
        }
        if any("Italic" in style for style in styles):
            raise StaticInstanceError("A mechanical slant retained an Italic style label.")
    finally:
        font.close()


def _verify_bundle(bundle: Path, files: list[tuple[Path, str]]) -> None:
    expected = [name for _path, name in files]
    with zipfile.ZipFile(bundle, "r") as archive:
        if archive.namelist() != expected:
            raise StaticInstanceError("The frozen package contains unexpected files.")
        for source, name in files:
            if archive.read(name) != source.read_bytes():
                raise StaticInstanceError("The frozen package changed an output file.")


def build_static_instance_outputs(
    source: Path,
    output_dir: Path,
    *,
    location: Mapping[str, object],
) -> PublicBuildResult:
    """Freeze every generated axis at one verified static location atomically."""
    source = Path(source)
    output_dir = Path(output_dir)
    contract = _assert_generated_zero_id(source)

    source_font = _load(source)
    try:
        selected = _validated_location(source_font, location)
    finally:
        source_font.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    native = output_dir / INSTANCE_NATIVE_NAME
    web = output_dir / INSTANCE_WEB_NAME
    css = output_dir / INSTANCE_CSS_NAME
    bundle = output_dir / INSTANCE_BUNDLE_NAME
    if source.resolve(strict=False) in {path.resolve(strict=False) for path in (native, web, css, bundle)}:
        raise StaticInstanceError("A generated variable source cannot also be a frozen output.")

    with tempfile.TemporaryDirectory(prefix=".fontblind-instance-stage-", dir=str(output_dir)) as stage_text:
        stage = Path(stage_text)
        reference_stage = stage / "reference-instance.ttf"
        native_stage = stage / native.name
        web_stage = stage / web.name
        css_stage = stage / css.name
        decoded_stage = stage / "decoded-instance.ttf"
        bundle_stage = stage / bundle.name

        variable_font = _load(source)
        try:
            reference = instantiateVariableFont(
                variable_font,
                selected,
                inplace=False,
                optimize=True,
                overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
                updateFontNames=False,
                static=True,
            )
        except Exception as exc:
            raise StaticInstanceError("FontTools could not freeze this generated location safely.") from exc
        finally:
            variable_font.close()

        try:
            _drop_variation_tables(reference)
            _assert_no_variable_layout(reference)
            reference.flavor = None
            reference.recalcTimestamp = False
            reference.save(str(reference_stage), reorderTables=True)
            weight, width, slant = _set_static_metadata(reference, selected)
            weight = float(max(1, min(1000, int(otRound(weight)))))
            surgical._sanitize_font(reference)
            reference.flavor = None
            reference.recalcBBoxes = True
            reference.recalcTimestamp = False
            reference.save(str(native_stage), reorderTables=True)
        except StaticInstanceError:
            raise
        except Exception as exc:
            raise StaticInstanceError("The frozen instance could not be compiled deterministically.") from exc
        finally:
            reference.close()

        _assert_static_zero_id(native_stage)
        _assert_matching_static(reference_stage, native_stage)
        _verify_selected_metadata(native_stage, selected)

        build_full_woff2(native_stage, web_stage, overwrite=True, css_family=CSS_FAMILY)
        css_text = _instance_css(weight, width, slant)
        if not css_text or "local(" in css_text.casefold() or INSTANCE_WEB_NAME not in css_text:
            raise WebBuildError("Frozen CSS failed the zero-ID web contract")
        css_stage.write_text(css_text, encoding="utf-8")

        _decode_woff2(web_stage, decoded_stage)
        _assert_static_zero_id(decoded_stage)
        _verify_woff2_roundtrip(native_stage, decoded_stage)
        _verify_selected_metadata(decoded_stage, selected)

        package_files = [(native_stage, native.name), (web_stage, web.name), (css_stage, css.name)]
        _deterministic_bundle(bundle_stage, package_files)
        _verify_bundle(bundle_stage, package_files)

        for staged, destination in (
            (native_stage, native),
            (web_stage, web),
            (css_stage, css),
            (bundle_stage, bundle),
        ):
            os.replace(staged, destination)

    return PublicBuildResult(
        native=OutputFile("native", native.name, "font/ttf"),
        web=OutputFile("web", web.name, "font/woff2"),
        css=OutputFile("css", css.name, "text/css; charset=utf-8"),
        bundle=OutputFile("bundle", bundle.name, "application/zip"),
        flavor="TrueType",
        variable=False,
        color=contract.color,
        checks={
            "source_identity_removed": True,
            "embedding_flags_cleared": True,
            "selected_location_verified": True,
            "static_instance_verified": True,
            "variation_tables_removed": True,
            "axis_metadata_verified": True,
            "harfbuzz_shaping_verified": True,
            "woff2_roundtrip_verified": True,
        },
    )
