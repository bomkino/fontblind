"""Independent proof for frozen FontBlind variable-font positions.

The static-instance builder already performs a complete build-time verification.
This module adds a second parent-independent path: it re-instantiates the
selected source location with a deliberately different FontTools optimisation
setting, then compares the committed static font against that fresh reference.
The comparison covers outlines, horizontal and vertical metrics, OpenType
layout, selected MVAR values, naming/style metadata, CSS, and the decoded WOFF2.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Mapping

from fontTools.misc.roundTools import otRound
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.otTables import Device
from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont

import fontblind_surgical as surgical
from fontblind_instance import (
    INSTANCE_CSS_NAME,
    INSTANCE_WEB_NAME,
    StaticInstanceError,
)
from fontblind_lab import _gpos_anchor_coordinates, _hmtx_by_gid
from fontblind_outline import _glyf_signature
from fontblind_pipeline import CSS_FAMILY, _decode_woff2, _verify_shaping, _verify_woff2_roundtrip


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
_WIDTH_STYLE = {
    1: "UltraCondensed",
    2: "ExtraCondensed",
    3: "Condensed",
    4: "SemiCondensed",
    5: "",
    6: "SemiExpanded",
    7: "Expanded",
    8: "ExtraExpanded",
    9: "UltraExpanded",
}
_WEIGHT_STYLE = (
    "Thin",
    "ExtraLight",
    "Light",
    "Regular",
    "Medium",
    "SemiBold",
    "Bold",
    "ExtraBold",
    "Black",
)

# These tables are compared semantically below or are intentionally rewritten
# by the static metadata/zero-ID policy. Every other retained table must remain
# byte-identical to a fresh independent instantiation.
_SEMANTIC_OR_MUTABLE_TABLES = frozenset(
    {
        "head",
        "OS/2",
        "name",
        "post",
        "hhea",
        "glyf",
        "loca",
        "hmtx",
        "vmtx",
        "maxp",
        "cmap",
    }
) | _VARIATION_TABLES

_LAYOUT_TABLES = ("BASE", "GDEF", "GPOS", "GSUB", "JSTF", "MATH", "COLR")

_HEAD_FIELDS = (
    "flags",
    "unitsPerEm",
    "xMin",
    "yMin",
    "xMax",
    "yMax",
    "lowestRecPPEM",
    "indexToLocFormat",
    "glyphDataFormat",
)
_HHEA_FIELDS = (
    "ascent",
    "descent",
    "lineGap",
    "advanceWidthMax",
    "minLeftSideBearing",
    "minRightSideBearing",
    "xMaxExtent",
    "metricDataFormat",
    "numberOfHMetrics",
)
_VHEA_FIELDS = (
    "ascent",
    "descent",
    "lineGap",
    "advanceHeightMax",
    "minTopSideBearing",
    "minBottomSideBearing",
    "yMaxExtent",
    "caretSlopeRise",
    "caretSlopeRun",
    "caretOffset",
    "metricDataFormat",
    "numberOfVMetrics",
)
_OS2_METRIC_FIELDS = (
    "xAvgCharWidth",
    "ySubscriptXSize",
    "ySubscriptYSize",
    "ySubscriptXOffset",
    "ySubscriptYOffset",
    "ySuperscriptXSize",
    "ySuperscriptYSize",
    "ySuperscriptXOffset",
    "ySuperscriptYOffset",
    "yStrikeoutSize",
    "yStrikeoutPosition",
    "sTypoAscender",
    "sTypoDescender",
    "sTypoLineGap",
    "usWinAscent",
    "usWinDescent",
    "sxHeight",
    "sCapHeight",
    "usDefaultChar",
    "usBreakChar",
    "usMaxContext",
)
_MAXP_FIELDS = (
    "tableVersion",
    "numGlyphs",
    "maxPoints",
    "maxContours",
    "maxCompositePoints",
    "maxCompositeContours",
    "maxZones",
    "maxTwilightPoints",
    "maxStorage",
    "maxFunctionDefs",
    "maxInstructionDefs",
    "maxStackElements",
    "maxSizeOfInstructions",
    "maxComponentElements",
    "maxComponentDepth",
)
_POST_FIELDS = ("underlinePosition", "underlineThickness", "isFixedPitch")


class StaticInstanceProofError(StaticInstanceError):
    """The built static output disagrees with an independent selected-location proof."""


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
        raise StaticInstanceProofError("A static-instance proof font could not be reopened safely.") from exc


def _validated_location(source: TTFont, raw: Mapping[str, object]) -> dict[str, float]:
    if not isinstance(raw, Mapping) or "fvar" not in source:
        raise StaticInstanceProofError("Static-instance proof received no complete generated location.")
    axes = source["fvar"].axes
    by_tag = {str(axis.axisTag): axis for axis in axes}
    if len(by_tag) != len(axes) or not by_tag or not set(by_tag).issubset(_ALLOWED_AXES) or set(raw) != set(by_tag):
        raise StaticInstanceProofError("Static-instance proof received an incoherent generated-axis model.")
    location: dict[str, float] = {}
    for axis in axes:
        tag = str(axis.axisTag)
        value = raw[tag]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StaticInstanceProofError("Static-instance proof received a non-numeric coordinate.")
        number = float(value)
        minimum = float(axis.minValue)
        maximum = float(axis.maxValue)
        if not math.isfinite(number) or number < minimum or number > maximum:
            raise StaticInstanceProofError("Static-instance proof received an out-of-range coordinate.")
        location[tag] = number
    return location


def _drop_variation_tables(font: TTFont) -> None:
    for tag in _VARIATION_TABLES:
        if tag in font:
            del font[tag]


def _walk_for_variable_payload(value: object, seen: set[int]) -> None:
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if isinstance(value, Device) and int(getattr(value, "DeltaFormat", 0)) == 0x8000:
        raise StaticInstanceProofError("Static output retained a VariationIndex device.")
    fields = getattr(value, "__dict__", None)
    if fields is not None:
        for name, child in fields.items():
            if name in {"VarStore", "FeatureVariations"} and child is not None:
                raise StaticInstanceProofError("Static output retained variable OpenType layout data.")
            if name != "tableTag":
                _walk_for_variable_payload(child, seen)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _walk_for_variable_payload(child, seen)


def _assert_no_variable_payload(font: TTFont) -> None:
    retained = sorted(set(font.reader.keys()) & set(_VARIATION_TABLES))
    if retained:
        raise StaticInstanceProofError("Static output retained variable-font tables.")
    seen: set[int] = set()
    for tag in _LAYOUT_TABLES:
        if tag in font:
            _walk_for_variable_payload(font[tag], seen)


def _save_fresh_reference(source_path: Path, location: Mapping[str, float], destination: Path) -> None:
    source = _load(source_path)
    try:
        selected = _validated_location(source, location)
        reference = instantiateVariableFont(
            source,
            selected,
            inplace=False,
            optimize=False,
            overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
            updateFontNames=False,
            static=True,
        )
    except StaticInstanceProofError:
        raise
    except Exception as exc:
        raise StaticInstanceProofError("The independent FontTools instancer refused this generated location.") from exc
    finally:
        source.close()
    try:
        _drop_variation_tables(reference)
        _assert_no_variable_payload(reference)
        reference.flavor = None
        reference.recalcBBoxes = True
        reference.recalcTimestamp = False
        reference.save(str(destination), reorderTables=True)
    except StaticInstanceProofError:
        raise
    except Exception as exc:
        raise StaticInstanceProofError("The independent selected-location reference could not be compiled.") from exc
    finally:
        reference.close()


def _field_snapshot(font: TTFont, tag: str, fields: tuple[str, ...]) -> tuple[tuple[str, object], ...] | None:
    if tag not in font:
        return None
    table = font[tag]
    return tuple((name, getattr(table, name, None)) for name in fields)


def _metrics_by_gid(font: TTFont, tag: str) -> tuple[tuple[int, int], ...] | None:
    if tag not in font:
        return None
    metrics = font[tag].metrics
    return tuple(tuple(map(int, metrics[name])) for name in font.getGlyphOrder())


def _assert_selected_semantics(reference_path: Path, output_path: Path) -> None:
    reference = _load(reference_path)
    output = _load(output_path)
    try:
        _assert_no_variable_payload(reference)
        _assert_no_variable_payload(output)

        reference_tags = set(reference.reader.keys())
        output_tags = set(output.reader.keys())
        if reference_tags != output_tags:
            raise StaticInstanceProofError("Static output changed the selected location's functional table set.")

        invariant_tags = sorted(reference_tags - set(_SEMANTIC_OR_MUTABLE_TABLES))
        for tag in invariant_tags:
            if reference.reader[tag] != output.reader[tag]:
                raise StaticInstanceProofError(f"Static output changed selected-location table {tag!r}.")

        if tuple(reference.getGlyphOrder()) != tuple(output.getGlyphOrder()):
            raise StaticInstanceProofError("Static output changed selected-location glyph order.")
        if surgical._cmap_snapshot(reference) != surgical._cmap_snapshot(output):
            raise StaticInstanceProofError("Static output changed selected-location character mapping.")
        if _hmtx_by_gid(reference) != _hmtx_by_gid(output):
            raise StaticInstanceProofError("Static output changed selected-location horizontal metrics.")
        if _metrics_by_gid(reference, "vmtx") != _metrics_by_gid(output, "vmtx"):
            raise StaticInstanceProofError("Static output changed selected-location vertical metrics.")
        if _gpos_anchor_coordinates(reference) != _gpos_anchor_coordinates(output):
            raise StaticInstanceProofError("Static output changed selected-location anchor coordinates.")

        for tag, fields in (
            ("head", _HEAD_FIELDS),
            ("hhea", _HHEA_FIELDS),
            ("vhea", _VHEA_FIELDS),
            ("OS/2", _OS2_METRIC_FIELDS),
            ("maxp", _MAXP_FIELDS),
            ("post", _POST_FIELDS),
        ):
            if _field_snapshot(reference, tag, fields) != _field_snapshot(output, tag, fields):
                raise StaticInstanceProofError(f"Static output changed selected-location {tag} metrics.")

        glyph_count = int(reference["maxp"].numGlyphs)
        if int(output["maxp"].numGlyphs) != glyph_count:
            raise StaticInstanceProofError("Static output changed selected-location glyph count.")
        for glyph_id in range(glyph_count):
            if _glyf_signature(reference, glyph_id) != _glyf_signature(output, glyph_id):
                raise StaticInstanceProofError("Static output changed selected-location outline geometry.")
    finally:
        reference.close()
        output.close()
    _verify_shaping(reference_path, output_path)


def _nearest_width_class(percent: float) -> int:
    return min(_WIDTH_PERCENT, key=lambda value: (abs(_WIDTH_PERCENT[value] - percent), value))


def _css_number(value: float) -> str:
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _selected_metadata_values(source_path: Path, location: Mapping[str, float]) -> tuple[int, int, float, float]:
    source = _load(source_path)
    try:
        os2 = source["OS/2"]
        raw_weight = float(location.get("wght", int(os2.usWeightClass)))
        raw_width = float(location.get("wdth", _WIDTH_PERCENT.get(int(os2.usWidthClass), 100.0)))
        slant = float(location.get("slnt", 0.0))
    finally:
        source.close()
    weight_class = max(1, min(1000, int(otRound(raw_weight))))
    width_class = _nearest_width_class(raw_width)
    return weight_class, width_class, raw_width, slant


def _expected_names(weight_class: int, width_class: int, slant: float) -> dict[int, str]:
    weight_index = max(0, min(8, int((weight_class + 50) // 100) - 1))
    weight = _WEIGHT_STYLE[weight_index]
    width = _WIDTH_STYLE[width_class]
    slope = "Oblique" if abs(slant) > 1 / 65536 else ""
    style = " ".join(part for part in (width, "" if weight == "Regular" else weight, slope) if part) or "Regular"
    postscript = f"{CSS_FAMILY}-{style.replace(' ', '')}"
    full = f"{CSS_FAMILY} {style}"
    return {
        1: CSS_FAMILY,
        2: style,
        3: f"{postscript};1.000;Universal",
        4: full,
        5: "Version 1.000",
        6: postscript,
        16: CSS_FAMILY,
        17: style,
        18: full,
        20: postscript,
        21: CSS_FAMILY,
        22: style,
        25: CSS_FAMILY,
    }


def _name_values(font: TTFont, name_id: int) -> set[str]:
    return {
        record.toUnicode()
        for record in font["name"].names
        if int(record.nameID) == name_id
    }


def _verify_selected_metadata(
    source_path: Path,
    output_path: Path,
    css_path: Path | None,
    location: Mapping[str, float],
) -> None:
    weight_class, width_class, width, slant = _selected_metadata_values(source_path, location)
    output = _load(output_path)
    try:
        os2 = output["OS/2"]
        selection = int(os2.fsSelection)
        is_bold = weight_class >= 700
        is_oblique = abs(slant) > 1 / 65536
        if int(os2.usWeightClass) != weight_class or int(os2.usWidthClass) != width_class:
            raise StaticInstanceProofError("Static output published the wrong selected weight or width class.")
        if bool(selection & 0x0020) != is_bold or bool(selection & 0x0200) != is_oblique:
            raise StaticInstanceProofError("Static output published incoherent selected style bits.")
        if bool(selection & 0x0040) != (not is_bold and not is_oblique):
            raise StaticInstanceProofError("Static output published an incoherent Regular bit.")
        if selection & 0x0001 or int(output["head"].macStyle) & 0x0002:
            raise StaticInstanceProofError("A mechanical slant was labelled Italic.")
        if bool(int(output["head"].macStyle) & 0x0001) != is_bold:
            raise StaticInstanceProofError("Static output published an incoherent macOS Bold bit.")
        if is_oblique and int(os2.version) < 4:
            raise StaticInstanceProofError("Static output used the Oblique bit without a compatible OS/2 version.")

        expected_angle = slant if is_oblique else 0.0
        if "post" in output and abs(float(output["post"].italicAngle) - expected_angle) > 1 / 65536:
            raise StaticInstanceProofError("Static output published the wrong selected slant angle.")
        expected_run = int(otRound(1000.0 * math.tan(math.radians(-slant)))) if is_oblique else 0
        hhea = output["hhea"]
        if (int(hhea.caretSlopeRise), int(hhea.caretSlopeRun), int(hhea.caretOffset)) != (1000, expected_run, 0):
            raise StaticInstanceProofError("Static output published the wrong selected caret slope.")

        expected_names = _expected_names(weight_class, width_class, slant)
        for name_id in (1, 2, 3, 4, 5, 6):
            if _name_values(output, name_id) != {expected_names[name_id]}:
                raise StaticInstanceProofError("Static output published incoherent required family/style names.")
        for name_id in (16, 17, 18, 20, 21, 22, 25):
            values = _name_values(output, name_id)
            if values and values != {expected_names[name_id]}:
                raise StaticInstanceProofError("Static output published incoherent optional family/style names.")
        if any("Italic" in value for record in output["name"].names for value in (record.toUnicode(),)):
            raise StaticInstanceProofError("A mechanical slant retained an Italic name.")
    finally:
        output.close()

    if css_path is not None:
        style = "normal" if abs(slant) <= 1 / 65536 else f"oblique {_css_number(abs(slant))}deg"
        expected_css = (
            "@font-face {\n"
            f'  font-family: "{CSS_FAMILY}";\n'
            f'  src: url("{INSTANCE_WEB_NAME}") format("woff2");\n'
            f"  font-weight: {_css_number(float(weight_class))};\n"
            f"  font-style: {style};\n"
            f"  font-stretch: {_css_number(width)}%;\n"
            "  font-display: swap;\n"
            "}\n"
        )
        try:
            actual_css = css_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise StaticInstanceProofError("Static output CSS could not be reopened.") from exc
        if actual_css != expected_css or css_path.name != INSTANCE_CSS_NAME:
            raise StaticInstanceProofError("Static output CSS disagrees with the selected font location.")


def verify_static_instance_outputs(
    source_path: Path,
    native_path: Path,
    web_path: Path,
    css_path: Path,
    *,
    location: Mapping[str, object],
) -> dict[str, float]:
    """Prove one built static package against a fresh independent instantiation."""
    source_path = Path(source_path)
    native_path = Path(native_path)
    web_path = Path(web_path)
    css_path = Path(css_path)
    source = _load(source_path)
    try:
        selected = _validated_location(source, location)
    finally:
        source.close()

    with tempfile.TemporaryDirectory(prefix=".fontblind-independent-instance-", dir=str(native_path.parent)) as temp_text:
        temp = Path(temp_text)
        reference = temp / "fresh-reference.ttf"
        decoded = temp / "decoded-output.ttf"
        _save_fresh_reference(source_path, selected, reference)

        _assert_selected_semantics(reference, native_path)
        _verify_selected_metadata(source_path, native_path, css_path, selected)
        report = surgical.audit_font(native_path)
        if not report.ok:
            raise StaticInstanceProofError("Static output failed the independent zero-ID audit.")

        _decode_woff2(web_path, decoded)
        _verify_woff2_roundtrip(native_path, decoded)
        _assert_selected_semantics(reference, decoded)
        _verify_selected_metadata(source_path, decoded, None, selected)
        decoded_report = surgical.audit_font(decoded)
        if not decoded_report.ok:
            raise StaticInstanceProofError("Decoded static WOFF2 failed the independent zero-ID audit.")
    return selected
