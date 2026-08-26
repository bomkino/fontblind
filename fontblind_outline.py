#!/usr/bin/env python3
"""Fresh outline reconstruction engine for FontBlind.

This module emits a new static outline program instead of copying the source
``glyf``/CFF program. Glyph IDs, cmap mappings, metrics, shaping/layout tables,
kerning, vertical behavior, Graphite/AAT behavior, OpenType MATH, and supported
vector-color tables are retained by GID. Hint programs, variation machinery,
bitmap/SVG renderers, editor data, signatures, and identity/provenance metadata
are not retained.

TrueType sources are reconstructed as fresh ``glyf`` fonts with instructions
removed. CFF/CFF2 sources are redrawn and recompiled as fresh static CFF1 fonts.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from fontTools import fontBuilder as _font_builder_module
from fontTools.fontBuilder import FontBuilder
from fontTools.cffLib import FDArrayIndex, FDSelect, FontDict
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from fontTools.ttLib.tables.O_S_2f_2 import Panose
from fontTools.ttLib.tables._g_l_y_f import (
    ARGS_ARE_XY_VALUES,
    NON_OVERLAPPING,
    OVERLAP_COMPOUND,
    ROUND_XY_TO_GRID,
    SCALED_COMPONENT_OFFSET,
    UNSCALED_COMPONENT_OFFSET,
    USE_MY_METRICS,
    WE_HAVE_INSTRUCTIONS,
    flagCubic,
    flagOnCurve,
    flagOverlapSimple,
)
from fontTools.ttLib.tables.ttProgram import Program
from fontTools.misc.fixedTools import otRound
from fontTools.varLib.instancer import (
    AxisLimits,
    OverlapMode,
    instantiateCFF2,
    instantiateVariableFont,
)

from fontblind_surgical import (
    FIXED_SFNT_TIME,
    GENERIC_FAMILY,
    GENERIC_FULL,
    GENERIC_PS,
    GENERIC_STYLE,
    FontBlindError,
    _generic_name,
    _cmap_snapshot,
    _open_font,
    _safe_name_ids,
    _sha256_file,
    audit_font,
)

OUTLINE_PROGRAM_VERSION = "3.1.0"

# A fresh outline build intentionally contains only these core tables.
_BASE_TABLES = frozenset({"head", "hhea", "maxp", "OS/2", "hmtx", "cmap", "name", "post"})
_TTF_TABLES = frozenset({"glyf", "loca"})
_CFF_TABLES = frozenset({"CFF "})
_VERTICAL_TABLES = frozenset({"vhea", "vmtx"})
_OPTIONAL_CORE_TABLES = frozenset({"VORG"})

# Functional tables that refer to glyphs by GID. The outline engine preserves
# glyph count and GID order, so these compiled tables can be retained without
# carrying source glyph names. Any name IDs they reference resolve to generic
# records in the rebuilt name table.
_RUNTIME_TABLES = frozenset(
    {
        "BASE",
        "GDEF",
        "GPOS",
        "GSUB",
        "JSTF",
        "MATH",
        "kern",
        # AAT.
        "ankr",
        "bsln",
        "feat",
        "just",
        "kerx",
        "lcar",
        "mort",
        "morx",
        "opbd",
        "prop",
        "trak",
        # Graphite.
        "Feat",
        "Glat",
        "Gloc",
        "Silf",
        "Sill",
        # Vector color and rasterizer policy.
        "COLR",
        "CPAL",
        "gasp",
    }
)


# Bound fresh CFF1 reconstruction below the practical 16-bit SID/CID ceiling.
# Clone mode has no such limit because it keeps the original CFF program.
_MAX_CFF1_REBUILD_GLYPHS = 65_000


_GEOMETRIC_POINT_FLAGS = flagOnCurve | flagOverlapSimple | flagCubic
_COMPONENT_FLAGS = (
    ARGS_ARE_XY_VALUES
    | ROUND_XY_TO_GRID
    | USE_MY_METRICS
    | OVERLAP_COMPOUND
    | SCALED_COMPONENT_OFFSET
    | UNSCALED_COMPONENT_OFFSET
    | NON_OVERLAPPING
)


@dataclass
class OutlineEquivalenceReport:
    ok: bool
    checks: dict[str, bool]
    mismatched_glyph_ids: list[int] = field(default_factory=list)
    source_tables: list[str] = field(default_factory=list)
    output_tables: list[str] = field(default_factory=list)
    expected_output_tables: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutlineBuildReport:
    mode: str
    source: str
    output: str
    source_sha256: str
    output_sha256: str
    outline_format: str
    glyphs: int
    units_per_em: int
    variable_source: bool
    instance_location: dict[str, float]
    runtime_tables_retained: list[str]
    verification_rounds: int
    equivalence: OutlineEquivalenceReport
    audit_ok: bool
    audit_source_identity_tokens_checked: int
    audit_source_identity_tokens_found: list[str]
    audit_warnings: list[str]


def parse_location(items: Sequence[str] | None) -> dict[str, float]:
    """Parse repeated ``TAG=VALUE`` command-line coordinates."""
    result: dict[str, float] = {}
    for item in items or ():
        if "=" not in item:
            raise FontBlindError(f"invalid location {item!r}; expected AXIS=VALUE")
        tag, raw_value = item.split("=", 1)
        tag = tag.strip()
        if len(tag) != 4 or not tag.isascii():
            raise FontBlindError(f"invalid axis tag {tag!r}; OpenType axis tags are four ASCII characters")
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise FontBlindError(f"invalid coordinate for {tag}: {raw_value!r}") from exc
        if not math.isfinite(value):
            raise FontBlindError(f"coordinate for {tag} must be finite")
        result[tag] = value
    return result


def _font_table_tags(font: TTFont) -> set[str]:
    if font.reader is not None:
        return set(font.reader.keys())
    return {tag for tag in font.keys() if tag != "GlyphOrder"}


def _outline_kind(font: TTFont) -> str:
    if "glyf" in font:
        return "TrueType glyf"
    if "CFF " in font:
        return "CFF1"
    if "CFF2" in font:
        return "CFF2 default/static instance"
    raise FontBlindError("outline reconstruction requires a glyf, CFF, or CFF2 source font")


def _generic_glyph_order(
    source_order: Sequence[str],
    *,
    cid_keyed: bool = False,
) -> tuple[list[str], dict[str, str]]:
    if not source_order:
        raise FontBlindError("source font has no glyph order")
    # OpenType fonts are limited to 65,535 glyphs, so five digits are enough.
    # Fresh CFF output is CID-keyed: this avoids carrying source glyph names and
    # greatly reduces String INDEX pressure. Fresh TrueType output uses a neutral
    # private namespace. Extremely large CFF inputs are rejected separately to
    # keep reconstruction bounded and deterministic.
    if cid_keyed:
        generic = [".notdef"] + [f"cid{gid:05d}" for gid in range(1, len(source_order))]
    else:
        generic = [".notdef"] + [f"_fb{gid:05d}" for gid in range(1, len(source_order))]
    return generic, dict(zip(source_order, generic))


def _static_working_font(
    source_path: Path,
    location: Mapping[str, float] | None,
) -> tuple[TTFont, bool, dict[str, float]]:
    source = _open_font(source_path, lazy=False)
    requested = dict(location or {})
    if "fvar" not in source:
        if requested:
            source.close()
            raise FontBlindError("--location was supplied for a non-variable font")
        return source, False, {}

    axes = {axis.axisTag: axis for axis in source["fvar"].axes}
    unknown = sorted(set(requested) - set(axes))
    if unknown:
        source.close()
        raise FontBlindError("unknown variation axis tag(s): " + ", ".join(unknown))

    resolved: dict[str, float] = {}
    limits: dict[str, float] = {}
    for tag, axis in axes.items():
        value = float(requested.get(tag, axis.defaultValue))
        if not (float(axis.minValue) <= value <= float(axis.maxValue)):
            source.close()
            raise FontBlindError(
                f"axis {tag} coordinate {value:g} is outside [{axis.minValue:g}, {axis.maxValue:g}]"
            )
        resolved[tag] = value
        limits[tag] = value

    # fontTools' general CFF2 instancer rounds each fully-instantiated blend
    # result to an integer. CFF charstrings can represent fractional values, and
    # retaining those values is required to match a live CFF2 variable font at a
    # non-default design coordinate. Build that table separately without the
    # integer-rounding callback, then merge it into the otherwise normal static
    # instance (whose metrics/layout tables are instantiated by the public API).
    unrounded_cff2 = None
    if "CFF2" in source:
        try:
            cff_source = copy.deepcopy(source)
            normalized_limits = (
                AxisLimits(dict(limits))
                .limitAxesAndPopulateDefaults(cff_source)
                .normalize(cff_source)
            )
            instantiateCFF2(
                cff_source,
                normalized_limits,
                round=lambda value: value,
                specialize=True,
                generalize=False,
                downgrade=False,
            )
            unrounded_cff2 = cff_source["CFF2"]
        except Exception as exc:
            source.close()
            raise FontBlindError(f"could not instantiate CFF2 outlines without rounding: {exc}") from exc

    try:
        instance = instantiateVariableFont(
            source,
            limits,
            inplace=False,
            optimize=True,
            overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
            updateFontNames=False,
            downgradeCFF2=False,
            static=True,
        )
    except Exception as exc:
        source.close()
        raise FontBlindError(f"could not instantiate variable source: {exc}") from exc
    if unrounded_cff2 is not None:
        instance["CFF2"] = unrounded_cff2

    # A static TrueType glyph program can store only integer point coordinates
    # and component offsets. Full gvar instancing can leave fractional values in
    # memory even though the glyf compiler subsequently applies OpenType's
    # integer rounding. Canonicalize the working copy now so exhaustive geometry
    # verification compares the representation that can actually be serialized.
    if "glyf" in instance:
        glyf = instance["glyf"]
        for glyph_name in instance.getGlyphOrder():
            glyph = glyf[glyph_name]
            glyph.expand(glyf)
            if glyph.isComposite():
                for component in glyph.components:
                    if hasattr(component, "x"):
                        component.x = otRound(component.x)
                        component.y = otRound(component.y)
            elif int(glyph.numberOfContours) != 0:
                glyph.coordinates.toInt(round=otRound)
            glyph.recalcBounds(glyf, boundsDone=set())

    source.close()
    return instance, True, resolved


def _rebuild_cmap(source: TTFont, output: TTFont, name_map: Mapping[str, str]) -> None:
    if "cmap" not in source:
        raise FontBlindError("source font has no cmap table")
    src = source["cmap"]
    dst = newTable("cmap")
    dst.tableVersion = int(getattr(src, "tableVersion", 0))
    dst.tables = []
    for subtable in src.tables:
        rebuilt = copy.deepcopy(subtable)
        if hasattr(rebuilt, "cmap"):
            remapped: dict[int, str] = {}
            for codepoint, old_name in rebuilt.cmap.items():
                try:
                    remapped[int(codepoint)] = name_map[old_name]
                except KeyError as exc:
                    raise FontBlindError(
                        f"cmap references glyph {old_name!r}, which is not present in the glyph order"
                    ) from exc
            rebuilt.cmap = remapped
        if hasattr(rebuilt, "uvsDict"):
            rebuilt.uvsDict = {
                int(selector): [
                    (int(codepoint), None if old_name is None else name_map[old_name])
                    for codepoint, old_name in entries
                ]
                for selector, entries in rebuilt.uvsDict.items()
            }
        # A decompiled subtable should compile from semantic fields, not stale bytes.
        # cmap subtables expect a ``data`` attribute even after decompilation.
        # Setting it to None forces semantic recompilation without stale bytes.
        rebuilt.data = None
        dst.tables.append(rebuilt)
    output["cmap"] = dst


def _rebuild_true_type_glyphs(
    source: TTFont,
    source_order: Sequence[str],
    generic_order: Sequence[str],
    name_map: Mapping[str, str],
) -> dict[str, Any]:
    glyf = source["glyf"]
    rebuilt: dict[str, Any] = {}
    for gid, (old_name, new_name) in enumerate(zip(source_order, generic_order)):
        try:
            original = glyf[old_name]
            original.expand(glyf)
            glyph = copy.deepcopy(original)
        except Exception as exc:
            raise FontBlindError(f"could not decompile TrueType glyph GID {gid} ({old_name!r}): {exc}") from exc

        if glyph.isComposite():
            for component in glyph.components:
                try:
                    component.glyphName = name_map[component.glyphName]
                except KeyError as exc:
                    raise FontBlindError(
                        f"glyph GID {gid} references missing component {component.glyphName!r}"
                    ) from exc
                component.flags = int(getattr(component, "flags", 0)) & ~WE_HAVE_INSTRUCTIONS
        if hasattr(glyph, "program"):
            glyph.program = Program()
            glyph.program.fromBytecode(b"")
        rebuilt[new_name] = glyph
    return rebuilt


def _rebuild_cff_charstrings(
    source: TTFont,
    source_order: Sequence[str],
    generic_order: Sequence[str],
    source_metrics: Mapping[str, tuple[int, int]],
) -> dict[str, Any]:
    glyph_set = source.getGlyphSet()
    charstrings: dict[str, Any] = {}
    for gid, (old_name, new_name) in enumerate(zip(source_order, generic_order)):
        try:
            width = source_metrics[old_name][0]
            pen = T2CharStringPen(width, glyph_set, roundTolerance=0.0, CFF2=False)
            glyph_set[old_name].draw(pen)
            charstrings[new_name] = pen.getCharString(optimize=False)
        except Exception as exc:
            raise FontBlindError(f"could not reconstruct CFF glyph GID {gid} ({old_name!r}): {exc}") from exc
    return charstrings


def _header_values(table: Any, defaults: Mapping[str, Any], excluded: Iterable[str]) -> dict[str, Any]:
    excluded_set = set(excluded)
    return {
        key: copy.deepcopy(getattr(table, key))
        for key in defaults
        if key not in excluded_set and hasattr(table, key)
    }


def _setup_horizontal_tables(builder: FontBuilder, source: TTFont, name_map: Mapping[str, str]) -> None:
    if "hmtx" not in source or "hhea" not in source:
        raise FontBlindError("source font is missing required horizontal metrics")
    source_metrics = source["hmtx"].metrics
    metrics = {name_map[name]: tuple(source_metrics[name]) for name in source.getGlyphOrder()}
    builder.setupHorizontalMetrics(metrics)
    values = _header_values(source["hhea"], _font_builder_module._hheaDefaults, {"numberOfHMetrics"})
    builder.setupHorizontalHeader(**values)


def _setup_vertical_tables(builder: FontBuilder, source: TTFont, name_map: Mapping[str, str]) -> None:
    if "vmtx" not in source and "vhea" not in source:
        return
    if "vmtx" not in source or "vhea" not in source:
        raise FontBlindError("source has only one of vhea/vmtx; refusing an incomplete vertical-metrics rebuild")
    source_metrics = source["vmtx"].metrics
    metrics = {name_map[name]: tuple(source_metrics[name]) for name in source.getGlyphOrder()}
    builder.setupVerticalMetrics(metrics)
    values = _header_values(source["vhea"], _font_builder_module._vheaDefaults, {"numberOfVMetrics"})
    builder.setupVerticalHeader(**values)


def _setup_vorg(output: TTFont, source: TTFont, name_map: Mapping[str, str]) -> None:
    if "VORG" not in source:
        return
    src = source["VORG"]
    dst = newTable("VORG")
    dst.majorVersion = int(src.majorVersion)
    dst.minorVersion = int(src.minorVersion)
    dst.defaultVertOriginY = int(src.defaultVertOriginY)
    dst.VOriginRecords = {name_map[name]: int(value) for name, value in src.VOriginRecords.items()}
    output["VORG"] = dst


def _setup_generic_name_table(builder: FontBuilder, source: TTFont) -> None:
    # Preserve the *set of numeric name IDs* because retained runtime tables can
    # refer to them, while replacing every value with deterministic generic text.
    ids = _safe_name_ids(source)
    ids.update({0, 1, 2, 3, 4, 5, 6, 16, 17})
    table = newTable("name")
    table.names = []
    for name_id in sorted(ids):
        value = _generic_name(name_id)
        table.setName(value, name_id, 3, 1, 0x0409)
        if name_id <= 25:
            table.setName(value, name_id, 1, 0, 0)
    builder.font["name"] = table


def _setup_generic_os2(builder: FontBuilder, source: TTFont) -> None:
    values: dict[str, Any] = {
        "version": 4,
        "fsType": 0,
        "sFamilyClass": 0,
        "panose": Panose(),
        "achVendID": "NONE",
    }
    src = source["OS/2"] if "OS/2" in source else None
    if src is not None:
        # Preserve functional style-selection and line/decorative metrics, while
        # neutralizing vendor, licensing, family-class, and PANOSE fingerprints.
        for key in (
            "version",
            "usWeightClass",
            "usWidthClass",
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
            "fsSelection",
            "ulCodePageRange1",
            "ulCodePageRange2",
            "sxHeight",
            "sCapHeight",
            "usDefaultChar",
            "usBreakChar",
            "usMaxContext",
            "usLowerOpticalPointSize",
            "usUpperOpticalPointSize",
        ):
            if hasattr(src, key):
                values[key] = copy.deepcopy(getattr(src, key))
    else:
        hhea = source["hhea"]
        values.update(
            {
                "sTypoAscender": int(hhea.ascent),
                "sTypoDescender": int(hhea.descent),
                "sTypoLineGap": int(hhea.lineGap),
                "usWinAscent": max(0, int(source["head"].yMax)),
                "usWinDescent": max(0, -int(source["head"].yMin)),
                "usWeightClass": 400,
                "usWidthClass": 5,
                "fsSelection": 0x0040,
            }
        )
    values["version"] = max(0, min(5, int(values.get("version", 4))))
    builder.setupOS2(**values)


def _setup_generic_post(builder: FontBuilder, source: TTFont) -> None:
    values: dict[str, Any] = {
        "italicAngle": 0,
        "underlinePosition": 0,
        "underlineThickness": 0,
        "isFixedPitch": 0,
        "minMemType42": 0,
        "maxMemType42": 0,
        "minMemType1": 0,
        "maxMemType1": 0,
    }
    # These fields affect rendering/layout conventions but do not carry identity.
    if "post" in source:
        post = source["post"]
        for key in values:
            if hasattr(post, key):
                values[key] = copy.deepcopy(getattr(post, key))
    builder.setupPost(keepGlyphNames=False, **values)


def _compiled_table_data(font: TTFont, tag: str) -> bytes:
    try:
        return bytes(font.getTableData(tag))
    except Exception as exc:
        raise FontBlindError(f"could not compile retained runtime table {tag!r}: {exc}") from exc


def _raw_table(tag: str, data: bytes) -> DefaultTable:
    table = DefaultTable(tag)
    table.data = bytes(data)
    return table


def _retain_runtime_tables(output: TTFont, source: TTFont) -> list[str]:
    retained: list[str] = []
    for tag in sorted(_font_table_tags(source) & set(_RUNTIME_TABLES)):
        output[tag] = _raw_table(tag, _compiled_table_data(source, tag))
        retained.append(tag)
    return retained


def _convert_cff_to_single_fd_cid(output: TTFont) -> None:
    """Convert a freshly built name-keyed CFF1 table to neutral CID-keyed CFF1.

    CFF SIDs are 16-bit. A name-keyed font near the OpenType glyph-count limit
    cannot allocate a distinct custom String INDEX entry for every generated
    glyph name. CID charsets encode numeric CIDs directly, avoid that leakage
    surface, and remain valid through GID 65,534.
    """
    cff = output["CFF "].cff
    top = cff.topDictIndex[0]
    glyph_order = output.getGlyphOrder()
    if not glyph_order or glyph_order[0] != ".notdef":
        raise FontBlindError("fresh CFF glyph order must begin with .notdef")
    if any(
        name != (".notdef" if gid == 0 else f"cid{gid:05d}")
        for gid, name in enumerate(glyph_order)
    ):
        raise FontBlindError("fresh CFF glyph order is not the expected neutral CID sequence")

    private = top.Private
    font_dict = FontDict()
    font_dict.FontName = f"{GENERIC_PS}-FD000"
    font_dict.Private = private

    fd_array = FDArrayIndex()
    fd_array.append(font_dict)
    fd_select = FDSelect(format=3)
    fd_select.gidArray = [0] * len(glyph_order)

    top.ROS = ("Adobe", "Identity", 0)
    top.CIDCount = len(glyph_order)
    top.FDArray = fd_array
    top.FDSelect = fd_select
    delattr(top, "Private")

    char_strings = top.CharStrings
    char_strings.fdArray = fd_array
    char_strings.fdSelect = fd_select
    if hasattr(char_strings, "private"):
        delattr(char_strings, "private")
    for name in glyph_order:
        char_string = char_strings[name]
        char_string.private = private
        char_string.fdSelectIndex = 0


def _expected_outline_tables(source: TTFont, output_is_ttf: bool) -> set[str]:
    expected = set(_BASE_TABLES)
    expected.update(_TTF_TABLES if output_is_ttf else _CFF_TABLES)
    if "vhea" in source and "vmtx" in source:
        expected.update(_VERTICAL_TABLES)
    if "VORG" in source and not output_is_ttf:
        expected.add("VORG")
    expected.update(_font_table_tags(source) & set(_RUNTIME_TABLES))
    return expected


def _build_outline_font_in_memory(
    source: TTFont,
) -> tuple[TTFont, str, dict[str, str]]:
    source_order = source.getGlyphOrder()
    output_is_ttf = "glyf" in source
    if not output_is_ttf and len(source_order) > _MAX_CFF1_REBUILD_GLYPHS:
        raise FontBlindError(
            "outline mode does not rebuild CFF fonts above "
            f"{_MAX_CFF1_REBUILD_GLYPHS:,} glyphs; use clone mode for this font"
        )
    generic_order, name_map = _generic_glyph_order(source_order, cid_keyed=not output_is_ttf)
    units_per_em = int(source["head"].unitsPerEm)
    builder = FontBuilder(units_per_em, isTTF=output_is_ttf)
    builder.setupGlyphOrder(generic_order)

    # Rebuild mappings before OS/2 so FontBuilder can derive coverage ranges.
    _rebuild_cmap(source, builder.font, name_map)

    source_metrics = source["hmtx"].metrics if "hmtx" in source else None
    if source_metrics is None:
        raise FontBlindError("source font has no hmtx table")

    if output_is_ttf:
        glyphs = _rebuild_true_type_glyphs(source, source_order, generic_order, name_map)
        builder.setupGlyf(glyphs, calcGlyphBounds=True, validateGlyphFormat=True)
        outline_format = "TrueType glyf (fresh, unhinted)"
    else:
        charstrings = _rebuild_cff_charstrings(source, source_order, generic_order, source_metrics)
        builder.setupCFF(
            GENERIC_PS,
            {
                "version": "1.000",
                "Notice": "Metadata removed",
                "Copyright": "Metadata removed",
                "FullName": GENERIC_FULL,
                "FamilyName": GENERIC_FAMILY,
                "Weight": GENERIC_STYLE,
            },
            charstrings,
            {},
        )
        _convert_cff_to_single_fd_cid(builder.font)
        outline_format = "CFF1 CID (fresh static charstrings, one neutral FD, unhinted)"

    _setup_horizontal_tables(builder, source, name_map)
    _setup_vertical_tables(builder, source, name_map)
    _setup_vorg(builder.font, source, name_map)
    _setup_generic_name_table(builder, source)
    _setup_generic_os2(builder, source)
    _setup_generic_post(builder, source)
    _retain_runtime_tables(builder.font, source)

    head = builder.font["head"]
    source_head = source["head"]
    head.fontRevision = 1.0
    head.created = FIXED_SFNT_TIME
    head.modified = FIXED_SFNT_TIME
    for field_name in ("macStyle", "flags", "lowestRecPPEM", "fontDirectionHint"):
        if hasattr(source_head, field_name):
            setattr(head, field_name, copy.deepcopy(getattr(source_head, field_name)))

    builder.font.recalcTimestamp = False
    builder.font.recalcBBoxes = True
    return builder.font, outline_format, name_map


def _metrics_by_gid(font: TTFont, tag: str) -> tuple[tuple[int, int], ...] | None:
    if tag not in font:
        return None
    metrics = font[tag].metrics
    return tuple(tuple(metrics[name]) for name in font.getGlyphOrder())


def _line_metric_snapshot(font: TTFont, tag: str) -> tuple[Any, ...] | None:
    if tag not in font:
        return None
    table = font[tag]
    fields = (
        "ascent",
        "descent",
        "lineGap",
        "caretSlopeRise",
        "caretSlopeRun",
        "caretOffset",
    )
    return tuple(getattr(table, field, None) for field in fields)


def _vorg_snapshot(font: TTFont) -> tuple[Any, ...] | None:
    if "VORG" not in font:
        return None
    table = font["VORG"]
    rows = tuple(
        sorted(
            (int(font.getGlyphID(name)), int(value))
            for name, value in table.VOriginRecords.items()
        )
    )
    return (
        int(table.majorVersion),
        int(table.minorVersion),
        int(table.defaultVertOriginY),
        rows,
    )


def _runtime_tables_equal(source: TTFont, output: TTFont) -> bool:
    tags = _font_table_tags(source) & set(_RUNTIME_TABLES)
    return all(
        tag in output and _compiled_table_data(source, tag) == _compiled_table_data(output, tag)
        for tag in tags
    )


def _component_signature(font: TTFont, component: Any) -> tuple[Any, ...]:
    base_name, transform = component.getComponentInfo()
    return (
        int(font.getGlyphID(base_name)),
        tuple(_normalize_number(value) for value in transform),
        int(getattr(component, "flags", 0)) & _COMPONENT_FLAGS,
    )


def _glyf_signature(font: TTFont, gid: int) -> tuple[Any, ...]:
    order = font.getGlyphOrder()
    glyf = font["glyf"]
    glyph = glyf[order[gid]]
    glyph.expand(glyf)
    if glyph.isComposite():
        return ("composite", tuple(_component_signature(font, component) for component in glyph.components))
    if int(glyph.numberOfContours) == 0:
        return ("empty",)
    coordinates = tuple((int(x), int(y)) for x, y in glyph.coordinates)
    flags = tuple(int(flag) & _GEOMETRIC_POINT_FLAGS for flag in glyph.flags)
    ends = tuple(int(value) for value in glyph.endPtsOfContours)
    return ("simple", int(glyph.numberOfContours), coordinates, flags, ends)


def _glyf_instruction_signature(font: TTFont, gid: int) -> bytes:
    order = font.getGlyphOrder()
    glyf = font["glyf"]
    glyph = glyf[order[gid]]
    glyph.expand(glyf)
    program = getattr(glyph, "program", None)
    if program is None:
        return b""
    return bytes(program.getBytecode())


def _normalize_number(value: Any) -> int | float:
    number = float(value)
    nearest = round(number)
    if abs(number - nearest) <= 1e-9:
        return int(nearest)
    return round(number, 8)


def _recording_signature(glyph_set: Any, glyph_name: str) -> tuple[Any, ...]:
    pen = DecomposingRecordingPen(glyph_set)
    glyph_set[glyph_name].draw(pen)
    rows = []
    for operator, operands in pen.value:
        normalized_operands = []
        for operand in operands:
            if isinstance(operand, tuple) and len(operand) == 2:
                normalized_operands.append((_normalize_number(operand[0]), _normalize_number(operand[1])))
            else:
                normalized_operands.append(operand)
        rows.append((operator, tuple(normalized_operands)))
    return tuple(rows)


def _geometry_mismatches(source: TTFont, output: TTFont, maximum_reported: int = 32) -> list[int]:
    count = int(source["maxp"].numGlyphs)
    mismatches: list[int] = []
    if "glyf" in source and "glyf" in output:
        for gid in range(count):
            if _glyf_signature(source, gid) != _glyf_signature(output, gid):
                mismatches.append(gid)
                if len(mismatches) >= maximum_reported:
                    break
        return mismatches

    source_set = source.getGlyphSet()
    output_set = output.getGlyphSet()
    source_order = source.getGlyphOrder()
    output_order = output.getGlyphOrder()
    for gid in range(count):
        if _recording_signature(source_set, source_order[gid]) != _recording_signature(output_set, output_order[gid]):
            mismatches.append(gid)
            if len(mismatches) >= maximum_reported:
                break
    return mismatches


def _generic_glyph_names_ok(font: TTFont) -> bool:
    # TrueType post format 3 stores no glyph names at all; FontTools may invent
    # friendly names from cmap when reopening, which is not source-name leakage.
    if "glyf" in font:
        return "post" in font and float(font["post"].formatType) == 3.0
    order = font.getGlyphOrder()
    if not order or order[0] != ".notdef":
        return False
    return all(
        (name.startswith("_fb") and name[3:].isdigit())
        or (name.startswith("cid") and name[3:].isdigit())
        for name in order[1:]
    )


def verify_outline_equivalence(
    source_path: Path,
    output_path: Path,
    *,
    location: Mapping[str, float] | None = None,
) -> OutlineEquivalenceReport:
    source, variable_source, resolved_location = _static_working_font(source_path, location)
    output = _open_font(output_path, lazy=False)
    try:
        source_is_ttf = "glyf" in source
        output_is_ttf = "glyf" in output
        expected_tables = _expected_outline_tables(source, source_is_ttf)
        output_tables = _font_table_tags(output)
        mismatches: list[int] = []
        basic_geometry_prereqs = (
            int(source["maxp"].numGlyphs) == int(output["maxp"].numGlyphs)
            and source_is_ttf == output_is_ttf
        )
        if basic_geometry_prereqs:
            mismatches = _geometry_mismatches(source, output)

        checks = {
            "outline_format": source_is_ttf == output_is_ttf and ("CFF " in output if not source_is_ttf else True),
            "minimal_table_set": output_tables == expected_tables,
            "glyph_count": int(source["maxp"].numGlyphs) == int(output["maxp"].numGlyphs),
            "units_per_em": int(source["head"].unitsPerEm) == int(output["head"].unitsPerEm),
            "cmap_gid_mapping": _cmap_snapshot(source) == _cmap_snapshot(output),
            "horizontal_metrics": _metrics_by_gid(source, "hmtx") == _metrics_by_gid(output, "hmtx"),
            "vertical_metrics": _metrics_by_gid(source, "vmtx") == _metrics_by_gid(output, "vmtx"),
            "horizontal_line_metrics": _line_metric_snapshot(source, "hhea") == _line_metric_snapshot(output, "hhea"),
            "vertical_line_metrics": _line_metric_snapshot(source, "vhea") == _line_metric_snapshot(output, "vhea"),
            "vertical_origin": _vorg_snapshot(source) == _vorg_snapshot(output),
            "outline_geometry": basic_geometry_prereqs and not mismatches,
            "runtime_table_bytes": _runtime_tables_equal(source, output),
            "generic_glyph_names": _generic_glyph_names_ok(output),
            "static_output": "fvar" not in output and "gvar" not in output and "CFF2" not in output,
        }
        return OutlineEquivalenceReport(
            ok=all(checks.values()),
            checks=checks,
            mismatched_glyph_ids=mismatches,
            source_tables=sorted(_font_table_tags(source)),
            output_tables=sorted(output_tables),
            expected_output_tables=sorted(expected_tables),
            details={
                "source_outline": _outline_kind(source),
                "source_variable": variable_source,
                "instance_location": resolved_location,
                "source_glyphs": int(source["maxp"].numGlyphs),
                "output_glyphs": int(output["maxp"].numGlyphs),
                "runtime_tables_retained": sorted(_font_table_tags(source) & set(_RUNTIME_TABLES)),
            },
        )
    finally:
        source.close()
        output.close()


def _write_outline_once(
    source_path: Path,
    output_path: Path,
    *,
    overwrite: bool,
    location: Mapping[str, float] | None,
    source_leak_check: bool,
) -> tuple[str, bool, dict[str, float], OutlineEquivalenceReport, Any]:
    if source_path.resolve() == output_path.resolve():
        raise FontBlindError("source and output paths must be different")
    if output_path.exists() and not overwrite:
        raise FontBlindError(f"output already exists: {output_path}; use --overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source, variable_source, resolved_location = _static_working_font(source_path, location)
    try:
        output_font, outline_format, _ = _build_outline_font_in_memory(source)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent)
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        try:
            output_font.save(str(temporary_path), reorderTables=True)
            output_font.close()
            equivalence = verify_outline_equivalence(
                source_path,
                temporary_path,
                location=resolved_location if variable_source else None,
            )
            if not equivalence.ok:
                raise FontBlindError(
                    "outline output failed geometry/metrics verification: "
                    + json.dumps(
                        {
                            "checks": equivalence.checks,
                            "mismatched_glyph_ids": equivalence.mismatched_glyph_ids,
                            "output_tables": equivalence.output_tables,
                            "expected_output_tables": equivalence.expected_output_tables,
                        },
                        sort_keys=True,
                    )
                )
            audit = audit_font(temporary_path, source_path if source_leak_check else None)
            if not audit.ok:
                raise FontBlindError(
                    "outline output failed metadata audit: "
                    + json.dumps(
                        {
                            "forbidden_tables": audit.forbidden_tables_found,
                            "source_identity_tokens_found": audit.source_identity_tokens_found,
                            "warnings": audit.warnings,
                        },
                        sort_keys=True,
                    )
                )
            os.replace(temporary_path, output_path)
            return outline_format, variable_source, resolved_location, equivalence, audit
        finally:
            try:
                output_font.close()
            except Exception:
                pass
            if temporary_path.exists():
                temporary_path.unlink()
    finally:
        source.close()


def rebuild_outline_font(
    source_path: Path,
    output_path: Path,
    *,
    overwrite: bool = False,
    verify_rounds: int = 1,
    location: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    if verify_rounds < 1 or verify_rounds > 20:
        raise FontBlindError("--verify-rounds must be between 1 and 20")
    source_path = Path(source_path)
    output_path = Path(output_path)

    outline_format, variable_source, resolved_location, equivalence, audit = _write_outline_once(
        source_path,
        output_path,
        overwrite=overwrite,
        location=location,
        source_leak_check=True,
    )
    first_hash = _sha256_file(output_path)

    if verify_rounds > 1:
        with tempfile.TemporaryDirectory(prefix="fontblind-outline-idempotence-") as temp_dir:
            previous = output_path
            suffix = output_path.suffix or ".font"
            for round_index in range(2, verify_rounds + 1):
                current = Path(temp_dir) / f"round-{round_index}{suffix}"
                _write_outline_once(
                    previous,
                    current,
                    overwrite=True,
                    location=None,
                    source_leak_check=False,
                )
                current_hash = _sha256_file(current)
                if current_hash != first_hash:
                    raise FontBlindError(
                        f"outline reconstruction is not byte-idempotent: round {round_index} differs from round 1"
                    )
                previous = current

    built = _open_font(output_path, lazy=True)
    try:
        glyphs = int(built["maxp"].numGlyphs)
        units_per_em = int(built["head"].unitsPerEm)
    finally:
        built.close()

    report = OutlineBuildReport(
        mode="outline",
        source=str(source_path),
        output=str(output_path),
        source_sha256=_sha256_file(source_path),
        output_sha256=first_hash,
        outline_format=outline_format,
        glyphs=glyphs,
        units_per_em=units_per_em,
        variable_source=variable_source,
        instance_location=resolved_location,
        runtime_tables_retained=list(equivalence.details.get("runtime_tables_retained", [])),
        verification_rounds=verify_rounds,
        equivalence=equivalence,
        audit_ok=audit.ok,
        audit_source_identity_tokens_checked=audit.source_identity_tokens_checked,
        audit_source_identity_tokens_found=audit.source_identity_tokens_found,
        audit_warnings=(
            list(audit.warnings)
            + (
                [
                    "A non-default TrueType variable instance was quantized to integer glyf coordinates, "
                    "as required by the static TrueType format. Clone mode preserves continuous variation."
                ]
                if variable_source and bool(location) and outline_format.startswith("TrueType glyf")
                else []
            )
        ),
    )
    return _jsonable(report)


def _sample_gids(font_path: Path, maximum: int) -> list[int]:
    font = _open_font(font_path, lazy=True)
    try:
        count = int(font["maxp"].numGlyphs)
        if count <= maximum:
            return list(range(count))
        gids = {0, count - 1}
        for index in range(maximum):
            gids.add(round(index * (count - 1) / max(1, maximum - 1)))
        if "cmap" in font:
            for subtable in font["cmap"].tables:
                if hasattr(subtable, "cmap"):
                    for name in list(subtable.cmap.values())[:maximum]:
                        gids.add(font.getGlyphID(name))
        return sorted(gid for gid in gids if 0 <= gid < count)[:maximum]
    finally:
        font.close()


def _unhinted_freetype_snapshot(
    path: Path,
    gids: Sequence[int],
    ppems: Sequence[int],
) -> tuple[dict[tuple[int, int], Any], list[str]]:
    try:
        import freetype
    except ImportError:
        return {}, ["freetype-py not installed; raster comparison skipped"]

    snapshots: dict[tuple[int, int], Any] = {}
    warnings: list[str] = []
    face = freetype.Face(str(path))
    flags = (
        freetype.FT_LOAD_RENDER
        | freetype.FT_LOAD_NO_HINTING
        | freetype.FT_LOAD_NO_AUTOHINT
        | freetype.FT_LOAD_NO_BITMAP
    )
    for ppem in ppems:
        try:
            face.set_pixel_sizes(0, int(ppem))
        except freetype.FT_Exception:
            warnings.append(f"could not select {ppem} ppem")
            continue
        for gid in gids:
            try:
                face.load_glyph(int(gid), flags)
            except freetype.FT_Exception:
                continue
            slot = face.glyph
            bitmap = slot.bitmap
            metrics = slot.metrics
            snapshots[(int(ppem), int(gid))] = (
                int(bitmap.width),
                int(bitmap.rows),
                int(bitmap.pitch),
                int(bitmap.pixel_mode),
                int(bitmap.num_grays),
                bytes(bitmap.buffer),
                int(slot.bitmap_left),
                int(slot.bitmap_top),
                int(slot.advance.x),
                int(slot.advance.y),
                int(metrics.width),
                int(metrics.height),
                int(metrics.horiBearingX),
                int(metrics.horiBearingY),
                int(metrics.horiAdvance),
                int(metrics.vertBearingX),
                int(metrics.vertBearingY),
                int(metrics.vertAdvance),
            )
    return snapshots, warnings


def gauntlet_outline_font(
    source_path: Path,
    *,
    rounds: int = 5,
    glyph_samples: int = 64,
    ppems: Sequence[int] = (9, 12, 16, 24, 48),
    location: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    if rounds < 2 or rounds > 20:
        raise FontBlindError("--rounds must be between 2 and 20")
    if glyph_samples < 1 or glyph_samples > 4096:
        raise FontBlindError("--glyph-samples must be between 1 and 4096")

    with tempfile.TemporaryDirectory(prefix="fontblind-outline-gauntlet-") as temp_dir:
        temp = Path(temp_dir)
        suffix = ".ttf"
        font = _open_font(source_path, lazy=True)
        try:
            if "glyf" not in font:
                suffix = ".otf"
        finally:
            font.close()
        output = temp / f"round-1{suffix}"
        result = rebuild_outline_font(
            source_path,
            output,
            overwrite=True,
            verify_rounds=rounds,
            location=location,
        )

        warnings: list[str] = []
        raster_available = False
        raster_equal = True
        raster_pairs = 0
        mismatch_count = 0
        if location:
            warnings.append("custom variable location: FreeType raster comparison skipped; geometry verification was still exhaustive")
        else:
            gids = _sample_gids(source_path, glyph_samples)
            source_rasters, source_warnings = _unhinted_freetype_snapshot(source_path, gids, ppems)
            output_rasters, output_warnings = _unhinted_freetype_snapshot(output, gids, ppems)
            warnings.extend(source_warnings)
            warnings.extend(output_warnings)
            raster_available = bool(source_rasters or output_rasters)
            raster_equal = source_rasters == output_rasters if raster_available else True
            raster_pairs = len(source_rasters)
            if not raster_equal:
                keys = sorted(set(source_rasters) | set(output_rasters))
                mismatch_count = sum(source_rasters.get(key) != output_rasters.get(key) for key in keys)
                raise FontBlindError(
                    f"unhinted FreeType raster equivalence failed for {mismatch_count} sampled glyph/size pairs"
                )

        result["gauntlet"] = {
            "mode": "outline",
            "rounds": rounds,
            "glyph_ids_sampled": len(gids) if not location else 0,
            "ppems": [int(value) for value in ppems],
            "raster_comparison_available": raster_available,
            "raster_pairs_compared": raster_pairs,
            "raster_equal": raster_equal,
            "raster_mismatches": mismatch_count,
            "warnings": warnings,
        }
        return result


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value
