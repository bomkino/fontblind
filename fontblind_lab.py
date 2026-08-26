"""Deterministic, local-only Oblique Lab and Variable Lab builders.

The labs deliberately make two narrow promises:

* Oblique Lab applies a declared affine shear.  It does not claim to draw an
  italic.
* Variable Lab interpolates compatible static donor masters.  It does not
  infer missing masters or invent additional design axes.

Both builders stage every artifact, run FontBlind's strict zero-ID audit, and
only then replace the public outputs.
"""
from __future__ import annotations

import math
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, InstanceDescriptor, SourceDescriptor
from fontTools.misc.fixedTools import floatToFixedToFloat
from fontTools.misc.roundTools import otRound
from fontTools.otlLib.builder import buildStatTable
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import (
    NON_OVERLAPPING,
    OVERLAP_COMPOUND,
    ROUND_XY_TO_GRID,
    SCALED_COMPONENT_OFFSET,
    UNSCALED_COMPONENT_OFFSET,
    USE_MY_METRICS,
)
from fontTools.ttLib.tables.otTables import Anchor
from fontTools.varLib import build as build_variable_font
from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont

import fontblind_surgical as surgical
from fontblind_outline import _glyf_signature
from fontblind_pipeline import (
    CSS_FAMILY,
    OutputFile,
    PublicBuildResult,
    SHAPING_PROBES,
    _decode_woff2,
    _deterministic_bundle,
    _harfbuzz_shape,
    _verify_shaping,
    _verify_woff2_roundtrip,
)
from fontblind_policy import SourceContract, assert_strict_output, inspect_strict_source
from fontblind_web import WebBuildError, build_full_woff2


OBLIQUE_NATIVE_NAME = "fontlab-oblique.ttf"
OBLIQUE_WEB_NAME = "fontlab-oblique.woff2"
OBLIQUE_CSS_NAME = "fontlab-oblique.css"
OBLIQUE_BUNDLE_NAME = "fontlab-oblique-package.zip"

VARIABLE_NATIVE_NAME = "fontlab-variable.ttf"
VARIABLE_WEB_NAME = "fontlab-variable.woff2"
VARIABLE_CSS_NAME = "fontlab-variable.css"
VARIABLE_BUNDLE_NAME = "fontlab-variable-package.zip"

SLANT_VARIABLE_NATIVE_NAME = "fontlab-slant-variable.ttf"
SLANT_VARIABLE_WEB_NAME = "fontlab-slant-variable.woff2"
SLANT_VARIABLE_CSS_NAME = "fontlab-slant-variable.css"
SLANT_VARIABLE_BUNDLE_NAME = "fontlab-slant-variable-package.zip"

_HINT_TABLES = frozenset({"cvar", "cvt ", "fpgm", "prep", "hdmx", "LTSH", "VDMX"})
_COMPONENT_SEMANTIC_FLAGS = (
    ROUND_XY_TO_GRID
    | USE_MY_METRICS
    | SCALED_COMPONENT_OFFSET
    | UNSCALED_COMPONENT_OFFSET
    | NON_OVERLAPPING
    | OVERLAP_COMPOUND
)
_SIMPLE_SEMANTIC_FLAGS = 0x81  # on-curve plus overlap; other bits are coordinate encoding
_FWORD_MIN = -32768
_FWORD_MAX = 32767
_F2DOT14_MIN = -2.0
_F2DOT14_MAX = 32767 / 16384
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


class FontLabError(surgical.FontBlindError):
    """A lab input cannot safely satisfy the requested build contract."""


def _load_font(path: Path) -> TTFont:
    return TTFont(
        str(path),
        lazy=False,
        recalcBBoxes=False,
        recalcTimestamp=False,
        ignoreDecompileErrors=False,
    )


@dataclass(frozen=True)
class _Donor:
    path: Path
    weight: int
    width_class: int
    width: float
    color: bool


@dataclass(frozen=True)
class _Axis:
    tag: str
    name: str
    minimum: float
    default: float
    maximum: float

    def public(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "name": self.name,
            "min": self.minimum,
            "default": self.default,
            "max": self.maximum,
        }


def _axis_value(donor: _Donor, tag: str) -> float:
    if tag == "wght":
        return float(donor.weight)
    if tag == "wdth":
        return float(donor.width)
    raise FontLabError("Variable Lab encountered an unsupported inferred axis. No output was kept.")


def _require_true_type_source(source: Path, *, allow_variable: bool = False) -> SourceContract:
    contract = inspect_strict_source(source)
    if contract.native_suffix != ".ttf" or contract.outline_flavor != "TrueType":
        raise FontLabError("Font Lab currently requires a standalone TrueType glyf font. No output was kept.")
    if contract.variable and not allow_variable:
        raise FontLabError("Font Lab currently requires static donor fonts. No output was kept.")
    return contract


def _require_angle(angle: float) -> float:
    if isinstance(angle, bool):
        raise FontLabError("The oblique angle must be a number from 4 through 20 degrees.")
    try:
        value = float(angle)
    except (TypeError, ValueError) as exc:
        raise FontLabError("The oblique angle must be a number from 4 through 20 degrees.") from exc
    if not math.isfinite(value) or not 4.0 <= value <= 20.0:
        raise FontLabError("The oblique angle must be from 4 through 20 degrees. No output was kept.")
    return value


def _reject_source_output_collision(sources: list[Path], destinations: list[Path]) -> None:
    source_paths = {path.resolve(strict=False) for path in sources}
    for destination in destinations:
        if destination.resolve(strict=False) in source_paths:
            raise FontLabError("A source font cannot also be a Font Lab output. No output was kept.")


def _remove_true_type_hinting(font: TTFont) -> None:
    if "glyf" not in font:
        raise FontLabError("Font Lab cannot remove hinting from a font without glyf outlines.")
    font["glyf"].removeHinting()
    for tag in _HINT_TABLES:
        if tag in font:
            del font[tag]

    maxp = font["maxp"]
    for field in (
        "maxTwilightPoints",
        "maxStorage",
        "maxFunctionDefs",
        "maxInstructionDefs",
        "maxStackElements",
        "maxSizeOfInstructions",
    ):
        if hasattr(maxp, field):
            setattr(maxp, field, 0)
    if hasattr(maxp, "maxZones"):
        maxp.maxZones = 1


def _assert_hinting_absent(font: TTFont) -> None:
    if any(tag in font for tag in _HINT_TABLES):
        raise FontLabError("Outline-dependent hinting data survived the lab build. No output was kept.")
    glyf = font["glyf"]
    for glyph_name in font.getGlyphOrder():
        glyph = glyf[glyph_name]
        glyph.expand(glyf)
        program = getattr(glyph, "program", None)
        if program is not None and program.getBytecode():
            raise FontLabError("A glyph instruction survived the lab build. No output was kept.")


def _fword(value: float, *, context: str) -> int:
    rounded = int(otRound(value))
    if not _FWORD_MIN <= rounded <= _FWORD_MAX:
        raise FontLabError(f"The {context} exceeds TrueType's coordinate range. No output was kept.")
    return rounded


def _matrix(component: object) -> tuple[float, float, float, float]:
    if not hasattr(component, "transform"):
        return 1.0, 0.0, 0.0, 1.0
    transform = component.transform
    return float(transform[0][0]), float(transform[0][1]), float(transform[1][0]), float(transform[1][1])


def _sheared_component_matrix(
    matrix: tuple[float, float, float, float], tangent: float
) -> tuple[float, float, float, float]:
    xx, xy, yx, yy = matrix
    values = (
        xx + tangent * xy,
        xy,
        yx + tangent * yy - tangent * xx - tangent * tangent * xy,
        yy - tangent * xy,
    )
    if any(not _F2DOT14_MIN <= value <= _F2DOT14_MAX for value in values):
        raise FontLabError("A composite transform cannot represent this oblique angle safely. No output was kept.")
    return tuple(floatToFixedToFloat(value, 14) for value in values)


def _set_component_matrix(component: object, matrix: tuple[float, float, float, float]) -> None:
    xx, xy, yx, yy = matrix
    identity = (1.0, 0.0, 0.0, 1.0)
    if matrix == identity:
        if hasattr(component, "transform"):
            del component.transform
        return
    component.transform = [[xx, xy], [yx, yy]]


def _recalculate_bounds_and_hhea(font: TTFont) -> None:
    glyf = font["glyf"]
    glyph_order = font.getGlyphOrder()
    bounds: list[tuple[int, int, int, int]] = []
    hmetrics: list[tuple[int, int, int, int]] = []

    for glyph_name in glyph_order:
        glyph = glyf[glyph_name]
        glyph.expand(glyf)
        glyph.recalcBounds(glyf, boundsDone=set())
        advance, lsb = font["hmtx"].metrics[glyph_name]
        if int(glyph.numberOfContours) != 0 and all(hasattr(glyph, field) for field in ("xMin", "yMin", "xMax", "yMax")):
            x_min = _fword(glyph.xMin, context="oblique outline")
            y_min = _fword(glyph.yMin, context="oblique outline")
            x_max = _fword(glyph.xMax, context="oblique outline")
            y_max = _fword(glyph.yMax, context="oblique outline")
            bounds.append((x_min, y_min, x_max, y_max))
            hmetrics.append((int(advance), int(lsb), x_min, x_max))

    head = font["head"]
    if bounds:
        head.xMin = min(item[0] for item in bounds)
        head.yMin = min(item[1] for item in bounds)
        head.xMax = max(item[2] for item in bounds)
        head.yMax = max(item[3] for item in bounds)
    else:
        head.xMin = head.yMin = head.xMax = head.yMax = 0

    all_metrics = [tuple(map(int, font["hmtx"].metrics[name])) for name in glyph_order]
    hhea = font["hhea"]
    hhea.advanceWidthMax = max(advance for advance, _ in all_metrics)
    if hmetrics:
        hhea.minLeftSideBearing = min(lsb for _, lsb, _, _ in hmetrics)
        hhea.minRightSideBearing = min(advance - lsb - (x_max - x_min) for advance, lsb, x_min, x_max in hmetrics)
        hhea.xMaxExtent = max(lsb + (x_max - x_min) for _, lsb, x_min, x_max in hmetrics)
        for field in ("minLeftSideBearing", "minRightSideBearing", "xMaxExtent"):
            _fword(getattr(hhea, field), context="horizontal metric")


def _walk_open_type_anchors(root: object) -> list[Anchor]:
    """Return every OpenType Anchor reachable from a decompiled table."""
    anchors: list[Anchor] = []
    seen: set[int] = set()

    def walk(value: object) -> None:
        if value is None or isinstance(value, (str, bytes, int, float, bool)):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if isinstance(value, Anchor):
            anchors.append(value)
        if isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
            return
        fields = getattr(value, "__dict__", None)
        if fields is not None:
            for key in sorted(fields):
                if key != "tableTag":
                    walk(fields[key])

    walk(root)
    return anchors


def _gpos_anchor_coordinates(font: TTFont) -> tuple[tuple[int, int], ...]:
    if "GPOS" not in font:
        return ()
    return tuple(
        (int(anchor.XCoordinate), int(anchor.YCoordinate))
        for anchor in _walk_open_type_anchors(font["GPOS"].table)
    )


def _shear_gpos_anchors(font: TTFont, tangent: float) -> None:
    if "GPOS" not in font:
        return
    for anchor in _walk_open_type_anchors(font["GPOS"].table):
        anchor.XCoordinate = _fword(
            float(anchor.XCoordinate) + tangent * float(anchor.YCoordinate),
            context="OpenType anchor",
        )


def _apply_oblique(font: TTFont, angle: float) -> None:
    tangent = math.tan(math.radians(angle))
    glyf = font["glyf"]
    glyph_order = font.getGlyphOrder()
    for glyph_name in glyph_order:
        glyph = glyf[glyph_name]
        glyph.expand(glyf)
        if glyph.isComposite():
            for component in glyph.components:
                _set_component_matrix(component, _sheared_component_matrix(_matrix(component), tangent))
                if hasattr(component, "x"):
                    component.x = _fword(float(component.x) + tangent * float(component.y), context="component offset")
                    component.y = _fword(component.y, context="component offset")
        elif int(glyph.numberOfContours) > 0:
            coordinates = glyph.coordinates
            for index, (x, y) in enumerate(tuple(coordinates)):
                coordinates[index] = (_fword(float(x) + tangent * float(y), context="outline"), _fword(y, context="outline"))

    _remove_true_type_hinting(font)
    _recalculate_bounds_and_hhea(font)
    _shear_gpos_anchors(font, tangent)

    os2 = font["OS/2"]
    # The Oblique bit is defined from OS/2 v4 onward. Versions 2–4 share the
    # same binary shape; older tables need the intervening fields populated
    # before a truthful v4 flag can be emitted.
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
    os2.fsSelection = (int(os2.fsSelection) & ~0x0041) | 0x0200
    font["head"].macStyle = int(font["head"].macStyle) & ~0x0002
    if "post" in font:
        font["post"].italicAngle = -float(angle)
    hhea = font["hhea"]
    hhea.caretSlopeRise = 1000
    hhea.caretSlopeRun = _fword(1000.0 * tangent, context="caret slope")
    hhea.caretOffset = 0


def _hmtx_by_gid(font: TTFont) -> tuple[tuple[int, int], ...]:
    return tuple(tuple(map(int, font["hmtx"].metrics[name])) for name in font.getGlyphOrder())


def _component_signature(font: TTFont, component: object) -> tuple[object, ...]:
    flags = int(getattr(component, "flags", 0)) & _COMPONENT_SEMANTIC_FLAGS
    glyph_id = int(font.getGlyphID(component.glyphName))
    if hasattr(component, "firstPt"):
        placement: tuple[object, ...] = ("points", int(component.firstPt), int(component.secondPt))
    else:
        placement = ("xy", int(component.x), int(component.y))
    return glyph_id, _matrix(component), flags, placement


def _verify_oblique_native(source: Path, output: Path, angle: float) -> None:
    source_font = _load_font(source)
    output_font = _load_font(output)
    tangent = math.tan(math.radians(angle))
    try:
        if int(source_font["maxp"].numGlyphs) != int(output_font["maxp"].numGlyphs):
            raise FontLabError("Oblique Lab changed the glyph count. No output was kept.")
        if int(source_font["head"].unitsPerEm) != int(output_font["head"].unitsPerEm):
            raise FontLabError("Oblique Lab changed units-per-em. No output was kept.")
        if surgical._cmap_snapshot(source_font) != surgical._cmap_snapshot(output_font):
            raise FontLabError("Oblique Lab changed character mapping. No output was kept.")
        if _hmtx_by_gid(source_font) != _hmtx_by_gid(output_font):
            raise FontLabError("Oblique Lab changed horizontal advances or spacing. No output was kept.")
        source_anchors = _gpos_anchor_coordinates(source_font)
        expected_anchors = tuple(
            (_fword(x + tangent * y, context="OpenType anchor"), y)
            for x, y in source_anchors
        )
        if _gpos_anchor_coordinates(output_font) != expected_anchors:
            raise FontLabError("Oblique Lab did not preserve GPOS anchors under the declared shear. No output was kept.")

        source_glyf = source_font["glyf"]
        output_glyf = output_font["glyf"]
        for glyph_id in range(int(source_font["maxp"].numGlyphs)):
            source_glyph = source_glyf[source_font.getGlyphName(glyph_id)]
            output_glyph = output_glyf[output_font.getGlyphName(glyph_id)]
            source_glyph.expand(source_glyf)
            output_glyph.expand(output_glyf)
            if int(source_glyph.numberOfContours) != int(output_glyph.numberOfContours):
                raise FontLabError("Oblique Lab changed outline topology. No output was kept.")
            if source_glyph.isComposite():
                if len(source_glyph.components) != len(output_glyph.components):
                    raise FontLabError("Oblique Lab changed composite topology. No output was kept.")
                for source_component, output_component in zip(source_glyph.components, output_glyph.components):
                    source_sig = _component_signature(source_font, source_component)
                    output_sig = _component_signature(output_font, output_component)
                    expected_matrix = _sheared_component_matrix(source_sig[1], tangent)
                    if source_sig[0] != output_sig[0] or source_sig[2] != output_sig[2] or output_sig[1] != expected_matrix:
                        raise FontLabError("Oblique Lab changed a composite incorrectly. No output was kept.")
                    source_placement = source_sig[3]
                    output_placement = output_sig[3]
                    if source_placement[0] == "points":
                        expected_placement = source_placement
                    else:
                        expected_placement = (
                            "xy",
                            _fword(source_placement[1] + tangent * source_placement[2], context="component offset"),
                            source_placement[2],
                        )
                    if output_placement != expected_placement:
                        raise FontLabError("Oblique Lab changed a composite offset incorrectly. No output was kept.")
            elif int(source_glyph.numberOfContours) > 0:
                if tuple(source_glyph.endPtsOfContours) != tuple(output_glyph.endPtsOfContours):
                    raise FontLabError("Oblique Lab changed contour topology. No output was kept.")
                if tuple(source_glyph.flags) != tuple(output_glyph.flags):
                    raise FontLabError("Oblique Lab changed point types. No output was kept.")
                expected = tuple(
                    (_fword(float(x) + tangent * float(y), context="outline"), _fword(y, context="outline"))
                    for x, y in source_glyph.coordinates
                )
                if tuple(output_glyph.coordinates) != expected:
                    raise FontLabError("Oblique Lab did not preserve the declared shear. No output was kept.")

        _assert_hinting_absent(output_font)
        selection = int(output_font["OS/2"].fsSelection)
        if not selection & 0x0200 or selection & 0x0001 or selection & 0x0040:
            raise FontLabError("Oblique Lab emitted incoherent OS/2 slope metadata. No output was kept.")
        if int(output_font["head"].macStyle) & 0x0002:
            raise FontLabError("Oblique Lab incorrectly labelled the face Italic. No output was kept.")
        if abs(float(output_font["post"].italicAngle) + angle) > 1 / 65536:
            raise FontLabError("Oblique Lab emitted an incorrect slant angle. No output was kept.")
        styles = {
            record.toUnicode()
            for record in output_font["name"].names
            if int(record.nameID) in {2, 17}
        }
        if not styles or any("Italic" in style or not style.endswith("Oblique") for style in styles):
            raise FontLabError("Oblique Lab did not emit neutral Oblique names. No output was kept.")
    finally:
        source_font.close()
        output_font.close()


def _verify_oblique_shaping(source: Path, output: Path) -> None:
    """Preserve selection, clusters, advances, and vertical placement.

    A sheared mark/cursive attachment correctly changes horizontal placement,
    so exact x-offset equality would reject the repaired GPOS anchors.
    """
    for text in SHAPING_PROBES:
        source_rows = _harfbuzz_shape(source, text)
        output_rows = _harfbuzz_shape(output, text)
        if len(source_rows) != len(output_rows):
            raise WebBuildError("Shaping changed during the oblique transformation")
        for source_row, output_row in zip(source_rows, output_rows):
            if source_row[:4] != output_row[:4] or source_row[5] != output_row[5]:
                raise WebBuildError("Shaping changed during the oblique transformation")


def _css_number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _width_percent(width_class: int) -> str:
    return _css_number(_WIDTH_PERCENT.get(max(1, min(9, int(width_class))), 100.0)) + "%"


def _oblique_css(weight: int, width_class: int, angle: float) -> str:
    return (
        "@font-face {\n"
        f'  font-family: "{CSS_FAMILY}";\n'
        f'  src: url("{OBLIQUE_WEB_NAME}") format("woff2");\n'
        f"  font-weight: {weight};\n"
        f"  font-style: oblique {_css_number(angle)}deg;\n"
        f"  font-stretch: {_width_percent(width_class)};\n"
        "  font-display: swap;\n"
        "}\n"
    )


def _slant_variable_css(weight: int, width_class: int, angle: float) -> str:
    return (
        "@font-face {\n"
        f'  font-family: "{CSS_FAMILY}";\n'
        f'  src: url("{SLANT_VARIABLE_WEB_NAME}") format("woff2-variations");\n'
        f"  font-weight: {weight};\n"
        f"  font-style: oblique 0deg {_css_number(angle)}deg;\n"
        f"  font-stretch: {_width_percent(width_class)};\n"
        "  font-display: swap;\n"
        "}\n"
    )


def _variable_css(axes: tuple[_Axis, ...], default_donor: _Donor) -> str:
    by_tag = {axis.tag: axis for axis in axes}
    weight = by_tag.get("wght")
    width = by_tag.get("wdth")
    weight_value = (
        f"{_css_number(weight.minimum)} {_css_number(weight.maximum)}"
        if weight is not None
        else str(default_donor.weight)
    )
    width_value = (
        f"{_css_number(width.minimum)}% {_css_number(width.maximum)}%"
        if width is not None
        else _width_percent(default_donor.width_class)
    )
    return (
        "@font-face {\n"
        f'  font-family: "{CSS_FAMILY}";\n'
        f'  src: url("{VARIABLE_WEB_NAME}") format("woff2-variations");\n'
        f"  font-weight: {weight_value};\n"
        "  font-style: normal;\n"
        f"  font-stretch: {width_value};\n"
        "  font-display: swap;\n"
        "}\n"
    )


def _assert_css(css: str, *, web_name: str) -> None:
    folded = css.casefold()
    if not css or "local(" in folded or CSS_FAMILY not in css or web_name not in css:
        raise WebBuildError("Generated CSS failed the zero-ID web contract")


def _verify_bundle(bundle: Path, files: list[tuple[Path, str]]) -> None:
    expected_names = [name for _, name in files]
    with zipfile.ZipFile(bundle, "r") as archive:
        if archive.namelist() != expected_names:
            raise FontLabError("The Font Lab package contains unexpected files. No output was kept.")
        for source, name in files:
            if archive.read(name) != source.read_bytes():
                raise FontLabError("The Font Lab package changed an output file. No output was kept.")


def _commit(staged: list[tuple[Path, Path]]) -> None:
    for staged_path, destination in staged:
        os.replace(staged_path, destination)


def build_oblique_outputs(source: Path, output_dir: Path, *, angle: float) -> PublicBuildResult:
    """Build a zero-ID static Oblique TTF, WOFF2, CSS, and ZIP atomically."""
    source = Path(source)
    output_dir = Path(output_dir)
    angle = _require_angle(angle)
    contract = _require_true_type_source(source)
    output_dir.mkdir(parents=True, exist_ok=True)

    native = output_dir / OBLIQUE_NATIVE_NAME
    web = output_dir / OBLIQUE_WEB_NAME
    css = output_dir / OBLIQUE_CSS_NAME
    bundle = output_dir / OBLIQUE_BUNDLE_NAME
    _reject_source_output_collision([source], [native, web, css, bundle])

    with tempfile.TemporaryDirectory(prefix=".fontlab-oblique-stage-", dir=str(output_dir)) as stage_text:
        stage = Path(stage_text)
        native_stage = stage / native.name
        web_stage = stage / web.name
        css_stage = stage / css.name
        decoded_stage = stage / "decoded-oblique.ttf"
        bundle_stage = stage / bundle.name

        font = _load_font(source)
        try:
            weight = int(font["OS/2"].usWeightClass)
            width_class = int(font["OS/2"].usWidthClass)
            _apply_oblique(font, angle)
            surgical._sanitize_font(font)
            font.flavor = None
            font.recalcBBoxes = True
            font.recalcTimestamp = False
            font.save(str(native_stage), reorderTables=True)
        except FontLabError:
            raise
        except Exception as exc:
            raise FontLabError("Oblique Lab could not transform this font safely. No output was kept.") from exc
        finally:
            font.close()

        assert_strict_output(native_stage, source)
        _verify_oblique_native(source, native_stage, angle)
        _verify_oblique_shaping(source, native_stage)

        build_full_woff2(native_stage, web_stage, overwrite=True, css_family=CSS_FAMILY)
        css_text = _oblique_css(weight, width_class, angle)
        _assert_css(css_text, web_name=web.name)
        css_stage.write_text(css_text, encoding="utf-8")

        _decode_woff2(web_stage, decoded_stage)
        assert_strict_output(decoded_stage, source)
        _verify_woff2_roundtrip(native_stage, decoded_stage)
        _verify_oblique_native(source, decoded_stage, angle)

        package_files = [(native_stage, native.name), (web_stage, web.name), (css_stage, css.name)]
        _deterministic_bundle(bundle_stage, package_files)
        _verify_bundle(bundle_stage, package_files)
        _commit(((native_stage, native), (web_stage, web), (css_stage, css), (bundle_stage, bundle)))

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
            "declared_shear_verified": True,
            "oblique_not_italic_verified": True,
            "hinting_removed": True,
            "harfbuzz_shaping_verified": True,
            "woff2_roundtrip_verified": True,
        },
    )


def _write_slant_master(source: Path, output: Path, *, angle: float | None) -> None:
    font = _load_font(source)
    try:
        if angle is None:
            _remove_true_type_hinting(font)
        else:
            _apply_oblique(font, angle)
        surgical._sanitize_font(font)
        font.flavor = None
        font.recalcBBoxes = True
        font.recalcTimestamp = False
        font.save(str(output), reorderTables=True)
    finally:
        font.close()
    assert_strict_output(output, source)


def _assert_matching_instance(reference_path: Path, instance: TTFont, instance_path: Path) -> None:
    reference = _load_font(reference_path)
    try:
        if surgical._cmap_snapshot(instance) != surgical._cmap_snapshot(reference):
            raise FontLabError("A variable master location changed character mapping. No output was kept.")
        if _hmtx_by_gid(instance) != _hmtx_by_gid(reference):
            raise FontLabError("A variable master location does not match donor metrics. No output was kept.")
        glyph_count = int(reference["maxp"].numGlyphs)
        if int(instance["maxp"].numGlyphs) != glyph_count:
            raise FontLabError("A variable master location changed the glyph count. No output was kept.")
        for glyph_id in range(glyph_count):
            if _glyf_signature(instance, glyph_id) != _glyf_signature(reference, glyph_id):
                raise FontLabError("A variable master location does not match donor geometry. No output was kept.")
        if _gpos_anchor_coordinates(instance) != _gpos_anchor_coordinates(reference):
            raise FontLabError("A variable master location does not match donor anchors. No output was kept.")
        instance.recalcTimestamp = False
        instance.save(str(instance_path), reorderTables=True)
        _verify_shaping(reference_path, instance_path)
    finally:
        reference.close()


def _build_slant_designspace(upright: Path, oblique: Path, output: Path, angle: float) -> None:
    document = DesignSpaceDocument()
    axis = AxisDescriptor()
    axis.name = "Slant"
    axis.tag = "slnt"
    axis.minimum = -float(angle)
    axis.default = 0.0
    axis.maximum = 0.0
    document.addAxis(axis)

    for index, (master, value) in enumerate(((upright, 0.0), (oblique, -float(angle))), start=1):
        source = SourceDescriptor()
        source.path = str(master.resolve())
        source.name = f"slant-master.{index:02d}"
        source.location = {"Slant": value}
        document.addSource(source)

        instance = InstanceDescriptor()
        instance.name = f"slant-instance.{index:02d}"
        instance.familyName = CSS_FAMILY
        instance.styleName = f"Instance {index:02d}"
        instance.postScriptFontName = f"{CSS_FAMILY}-Instance{index:02d}"
        instance.location = {"Slant": value}
        document.addInstance(instance)
    document.write(output)


def _build_slant_variable_native(
    source: Path,
    stage: Path,
    output: Path,
    angle: float,
) -> tuple[Path, int, int, _Axis]:
    upright = stage / "slant-master-upright.ttf"
    oblique = stage / "slant-master-oblique.ttf"
    _write_slant_master(source, upright, angle=None)
    _write_slant_master(source, oblique, angle=angle)
    _verify_oblique_native(source, oblique, angle)
    _verify_oblique_shaping(source, oblique)

    designspace = stage / "fontlab-slant.designspace"
    _build_slant_designspace(upright, oblique, designspace, angle)
    try:
        variable_font, _, _ = build_variable_font(designspace)
    except Exception as exc:
        raise FontLabError("FontTools could not compile the mechanical slant axis safely. No output was kept.") from exc
    try:
        _remove_true_type_hinting(variable_font)
        buildStatTable(
            variable_font,
            [
                {
                    "tag": "slnt",
                    "name": "Slant",
                    "ordering": 0,
                    "values": [
                        {"value": -float(angle), "name": "Slant End"},
                        {"value": 0.0, "name": "Upright", "flags": 0x2},
                    ],
                }
            ],
            elidedFallbackName="Upright",
        )
        if "post" in variable_font:
            variable_font["post"].italicAngle = 0.0
        surgical._sanitize_font(variable_font)
        variable_font.flavor = None
        variable_font.recalcTimestamp = False
        variable_font.save(str(output), reorderTables=True)
        weight = int(variable_font["OS/2"].usWeightClass)
        width_class = int(variable_font["OS/2"].usWidthClass)
    except Exception as exc:
        raise FontLabError("Oblique Lab could not compile a deterministic slant-axis font. No output was kept.") from exc
    finally:
        variable_font.close()
    return oblique, weight, width_class, _Axis("slnt", "Slant", -float(angle), 0.0, 0.0)


def _verify_slant_variable_native(
    output: Path,
    source: Path,
    oblique: Path,
    angle: float,
    stage: Path,
) -> None:
    variable_font = _load_font(output)
    try:
        if "fvar" not in variable_font or "gvar" not in variable_font or "STAT" not in variable_font:
            raise FontLabError("Oblique Lab did not emit required slant variation tables. No output was kept.")
        axes = variable_font["fvar"].axes
        if len(axes) != 1 or str(axes[0].axisTag) != "slnt":
            raise FontLabError("Oblique Lab emitted an unexpected variable axis. No output was kept.")
        axis = axes[0]
        if (
            float(axis.minValue) != -float(angle)
            or float(axis.defaultValue) != 0.0
            or float(axis.maxValue) != 0.0
        ):
            raise FontLabError("Oblique Lab emitted incorrect slant-axis bounds. No output was kept.")
        if "post" in variable_font and float(variable_font["post"].italicAngle) != 0.0:
            raise FontLabError("The variable slant default is not upright. No output was kept.")
        axis_names = {
            record.toUnicode()
            for record in variable_font["name"].names
            if int(record.nameID) == int(axis.axisNameID)
        }
        if axis_names != {"Slant"}:
            raise FontLabError("Oblique Lab emitted a non-neutral slant label. No output was kept.")
        _assert_hinting_absent(variable_font)

        for index, (value, reference) in enumerate(((0.0, source), (-float(angle), oblique)), start=1):
            instance = instantiateVariableFont(
                variable_font,
                {"slnt": value},
                inplace=False,
                optimize=True,
                overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
                updateFontNames=False,
                static=True,
            )
            try:
                _assert_matching_instance(reference, instance, stage / f"verified-slant-{index:02d}.ttf")
            finally:
                instance.close()
    finally:
        variable_font.close()


def build_slant_variable_outputs(source: Path, output_dir: Path, *, angle: float) -> PublicBuildResult:
    """Build a zero-ID upright-to-Oblique ``slnt`` variable font atomically."""
    source = Path(source)
    output_dir = Path(output_dir)
    angle = _require_angle(angle)
    contract = _require_true_type_source(source)
    source_font = _load_font(source)
    try:
        selection = int(source_font["OS/2"].fsSelection)
        mac_style = int(source_font["head"].macStyle)
        italic_angle = float(source_font["post"].italicAngle) if "post" in source_font else 0.0
        if selection & 0x0201 or mac_style & 0x0002 or abs(italic_angle) > 1 / 65536:
            raise FontLabError("The slant-axis lane requires an upright source font. No output was kept.")
    finally:
        source_font.close()
    output_dir.mkdir(parents=True, exist_ok=True)

    native = output_dir / SLANT_VARIABLE_NATIVE_NAME
    web = output_dir / SLANT_VARIABLE_WEB_NAME
    css = output_dir / SLANT_VARIABLE_CSS_NAME
    bundle = output_dir / SLANT_VARIABLE_BUNDLE_NAME
    _reject_source_output_collision([source], [native, web, css, bundle])

    with tempfile.TemporaryDirectory(prefix=".fontlab-slant-stage-", dir=str(output_dir)) as stage_text:
        stage = Path(stage_text)
        native_stage = stage / native.name
        web_stage = stage / web.name
        css_stage = stage / css.name
        decoded_stage = stage / "decoded-slant-variable.ttf"
        bundle_stage = stage / bundle.name

        oblique, weight, width_class, axis = _build_slant_variable_native(source, stage, native_stage, angle)
        assert_strict_output(native_stage, source)
        _verify_slant_variable_native(native_stage, source, oblique, angle, stage)

        build_full_woff2(native_stage, web_stage, overwrite=True, css_family=CSS_FAMILY)
        css_text = _slant_variable_css(weight, width_class, angle)
        _assert_css(css_text, web_name=web.name)
        css_stage.write_text(css_text, encoding="utf-8")

        _decode_woff2(web_stage, decoded_stage)
        assert_strict_output(decoded_stage, source)
        _verify_woff2_roundtrip(native_stage, decoded_stage)
        _verify_slant_variable_native(decoded_stage, source, oblique, angle, stage)

        package_files = [(native_stage, native.name), (web_stage, web.name), (css_stage, css.name)]
        _deterministic_bundle(bundle_stage, package_files)
        _verify_bundle(bundle_stage, package_files)
        _commit(((native_stage, native), (web_stage, web), (css_stage, css), (bundle_stage, bundle)))

    return PublicBuildResult(
        native=OutputFile("native", native.name, "font/ttf"),
        web=OutputFile("web", web.name, "font/woff2"),
        css=OutputFile("css", css.name, "text/css; charset=utf-8"),
        bundle=OutputFile("bundle", bundle.name, "application/zip"),
        flavor="TrueType",
        variable=True,
        color=contract.color,
        checks={
            "source_identity_removed": True,
            "embedding_flags_cleared": True,
            "declared_shear_verified": True,
            "slant_axis_verified": True,
            "variable_endpoints_verified": True,
            "oblique_not_italic_verified": True,
            "hinting_removed": True,
            "harfbuzz_shaping_verified": True,
            "woff2_roundtrip_verified": True,
        },
        axes=(axis.public(),),
    )


def _glyph_topology(font: TTFont, glyph_id: int) -> tuple[object, ...]:
    glyf = font["glyf"]
    glyph = glyf[font.getGlyphName(glyph_id)]
    glyph.expand(glyf)
    if glyph.isComposite():
        components: list[tuple[object, ...]] = []
        for component in glyph.components:
            glyph_ref = int(font.getGlyphID(component.glyphName))
            flags = int(getattr(component, "flags", 0)) & _COMPONENT_SEMANTIC_FLAGS
            if hasattr(component, "firstPt"):
                placement: tuple[object, ...] = ("points", int(component.firstPt), int(component.secondPt))
            else:
                placement = ("xy",)
            components.append((glyph_ref, _matrix(component), flags, placement))
        return "composite", tuple(components)
    if int(glyph.numberOfContours) > 0:
        return (
            "simple",
            int(glyph.numberOfContours),
            tuple(map(int, glyph.endPtsOfContours)),
            tuple(int(flag) & _SIMPLE_SEMANTIC_FLAGS for flag in glyph.flags),
        )
    return ("empty",)


def _preflight_donors(sources: list[Path]) -> list[_Donor]:
    if not 2 <= len(sources) <= 12:
        raise FontLabError("Variable Lab requires from 2 through 12 static donor fonts. No output was kept.")
    if len({path.resolve(strict=False) for path in sources}) != len(sources):
        raise FontLabError("Variable Lab donor paths must be distinct. No output was kept.")

    contracts = [_require_true_type_source(path) for path in sources]
    fonts = [_load_font(path) for path in sources]
    try:
        weights = [int(font["OS/2"].usWeightClass) for font in fonts]
        width_classes = [int(font["OS/2"].usWidthClass) for font in fonts]
        if any(not 1 <= weight <= 1000 for weight in weights):
            raise FontLabError("Variable Lab needs OS/2 weights from 1 through 1000. No output was kept.")
        if any(not 1 <= width_class <= 9 for width_class in width_classes):
            raise FontLabError("Variable Lab needs OS/2 width classes from 1 through 9. No output was kept.")
        active_weight = len(set(weights)) > 1
        active_width = len(set(width_classes)) > 1
        if not active_weight and not active_width:
            raise FontLabError("Variable Lab donors do not expose distinct weight or width coordinates. No output was kept.")
        coordinates = [
            tuple(
                value
                for enabled, value in ((active_weight, weight), (active_width, width_class))
                if enabled
            )
            for weight, width_class in zip(weights, width_classes)
        ]
        if len(set(coordinates)) != len(coordinates):
            raise FontLabError("Variable Lab donor weight/width coordinates must be unique. No output was kept.")
        if len({contract.color for contract in contracts}) != 1:
            raise FontLabError("Variable Lab donors disagree about color-font structure. No output was kept.")

        baseline = fonts[0]
        glyph_count = int(baseline["maxp"].numGlyphs)
        baseline_upm = int(baseline["head"].unitsPerEm)
        baseline_order = tuple(baseline.getGlyphOrder())
        baseline_cmap = surgical._cmap_snapshot(baseline)
        baseline_topology = tuple(_glyph_topology(baseline, glyph_id) for glyph_id in range(glyph_count))
        for font in fonts[1:]:
            if int(font["head"].unitsPerEm) != baseline_upm:
                raise FontLabError("Variable Lab donors use different units-per-em. No output was kept.")
            if tuple(font.getGlyphOrder()) != baseline_order:
                raise FontLabError("Variable Lab donors use different glyph orders. No output was kept.")
            if int(font["maxp"].numGlyphs) != glyph_count:
                raise FontLabError("Variable Lab donors use different glyph counts. No output was kept.")
            if surgical._cmap_snapshot(font) != baseline_cmap:
                raise FontLabError("Variable Lab donors use different character maps. No output was kept.")
            topology = tuple(_glyph_topology(font, glyph_id) for glyph_id in range(glyph_count))
            if topology != baseline_topology:
                raise FontLabError("Variable Lab donors do not have interpolatable glyf topology. No output was kept.")
    finally:
        for font in fonts:
            font.close()

    donors = [
        _Donor(
            path=path,
            weight=weight,
            width_class=width_class,
            width=_WIDTH_PERCENT[width_class],
            color=contract.color,
        )
        for path, weight, width_class, contract in zip(sources, weights, width_classes, contracts)
    ]
    return sorted(donors, key=lambda donor: (donor.weight, donor.width, str(donor.path)))


def _plan_axes(donors: list[_Donor]) -> tuple[tuple[_Axis, ...], _Donor]:
    tags: list[str] = []
    if len({donor.weight for donor in donors}) > 1:
        tags.append("wght")
    if len({donor.width for donor in donors}) > 1:
        tags.append("wdth")
    if not tags:
        raise FontLabError("Variable Lab found no usable donor axis. No output was kept.")

    candidates: list[_Donor] = []
    for candidate in donors:
        default_values = {tag: _axis_value(candidate, tag) for tag in tags}
        supported = True
        for tag in tags:
            values = [_axis_value(donor, tag) for donor in donors]
            for extreme in {min(values), max(values)}:
                if extreme == default_values[tag]:
                    continue
                if not any(
                    _axis_value(donor, tag) == extreme
                    and all(
                        other == tag or _axis_value(donor, other) == default_values[other]
                        for other in tags
                    )
                    for donor in donors
                ):
                    supported = False
                    break
            if not supported:
                break
        if supported:
            candidates.append(candidate)

    if not candidates:
        raise FontLabError(
            "A two-axis donor set needs a real base plus independent weight and width extremes. No output was kept."
        )

    def regular_distance(donor: _Donor) -> tuple[float, int, float]:
        distance = 0.0
        if "wght" in tags:
            distance += abs(donor.weight - 400) / 100.0
        if "wdth" in tags:
            distance += abs(donor.width - 100.0) / 12.5
        return distance, donor.weight, donor.width

    default_donor = min(candidates, key=regular_distance)
    axes: list[_Axis] = []
    if "wght" in tags:
        values = [float(donor.weight) for donor in donors]
        axes.append(_Axis("wght", "Weight", min(values), float(default_donor.weight), max(values)))
    if "wdth" in tags:
        values = [float(donor.width) for donor in donors]
        axes.append(_Axis("wdth", "Width", min(values), float(default_donor.width), max(values)))
    return tuple(axes), default_donor


def _write_anonymous_master(donor: _Donor, output: Path) -> None:
    font = _load_font(donor.path)
    try:
        _remove_true_type_hinting(font)
        surgical._sanitize_font(font)
        font.flavor = None
        font.recalcTimestamp = False
        font.save(str(output), reorderTables=True)
    finally:
        font.close()
    assert_strict_output(output, donor.path)


def _build_designspace(
    donors: list[_Donor],
    masters: list[Path],
    output: Path,
    axes: tuple[_Axis, ...],
) -> None:
    document = DesignSpaceDocument()
    for spec in axes:
        axis = AxisDescriptor()
        axis.name = spec.name
        axis.tag = spec.tag
        axis.minimum = spec.minimum
        axis.default = spec.default
        axis.maximum = spec.maximum
        document.addAxis(axis)

    for index, (donor, master) in enumerate(zip(donors, masters), start=1):
        source = SourceDescriptor()
        source.path = str(master.resolve())
        source.name = f"master.{index:02d}"
        source.location = {axis.name: _axis_value(donor, axis.tag) for axis in axes}
        document.addSource(source)

        instance = InstanceDescriptor()
        instance.name = f"instance.{index:02d}"
        instance.familyName = CSS_FAMILY
        instance.styleName = f"Instance {index:02d}"
        instance.postScriptFontName = f"{CSS_FAMILY}-Instance{index:02d}"
        instance.location = {axis.name: _axis_value(donor, axis.tag) for axis in axes}
        document.addInstance(instance)

    document.write(output)


def _build_variable_native(donors: list[_Donor], stage: Path, output: Path) -> tuple[tuple[_Axis, ...], _Donor]:
    axes, default_donor = _plan_axes(donors)
    masters: list[Path] = []
    for index, donor in enumerate(donors, start=1):
        master = stage / f"master-{index:02d}.ttf"
        _write_anonymous_master(donor, master)
        masters.append(master)

    designspace = stage / "fontlab.designspace"
    _build_designspace(donors, masters, designspace, axes)
    try:
        variable_font, _, _ = build_variable_font(designspace)
    except Exception as exc:
        raise FontLabError("FontTools could not interpolate these donor fonts safely. No output was kept.") from exc
    try:
        _remove_true_type_hinting(variable_font)
        stat_axes: list[dict[str, object]] = []
        for ordering, axis in enumerate(axes):
            values = sorted({_axis_value(donor, axis.tag) for donor in donors})
            stat_axes.append(
                {
                    "tag": axis.tag,
                    "name": axis.name,
                    "ordering": ordering,
                    "values": [
                        {
                            "value": value,
                            "name": f"{axis.name} Value {index:02d}",
                            "flags": 0x2 if value == axis.default else 0,
                        }
                        for index, value in enumerate(values, start=1)
                    ],
                }
            )
        buildStatTable(
            variable_font,
            stat_axes,
            elidedFallbackName="Instance Default",
        )
        variable_font["OS/2"].usWeightClass = int(default_donor.weight)
        variable_font["OS/2"].usWidthClass = int(default_donor.width_class)
        surgical._sanitize_font(variable_font)
        variable_font.flavor = None
        variable_font.recalcTimestamp = False
        variable_font.save(str(output), reorderTables=True)
    except Exception as exc:
        raise FontLabError("Variable Lab could not compile a deterministic variable font. No output was kept.") from exc
    finally:
        variable_font.close()
    return axes, default_donor


def _verify_variable_native(
    output: Path,
    donors: list[_Donor],
    axes: tuple[_Axis, ...],
    default_donor: _Donor,
    stage: Path,
) -> None:
    variable_font = _load_font(output)
    try:
        if "fvar" not in variable_font or "gvar" not in variable_font:
            raise FontLabError("Variable Lab did not emit required variation tables. No output was kept.")
        emitted_axes = variable_font["fvar"].axes
        if [str(axis.axisTag) for axis in emitted_axes] != [axis.tag for axis in axes]:
            raise FontLabError("Variable Lab emitted an unexpected axis. No output was kept.")
        for emitted, expected in zip(emitted_axes, axes):
            if (
                float(emitted.minValue) != expected.minimum
                or float(emitted.defaultValue) != expected.default
                or float(emitted.maxValue) != expected.maximum
            ):
                raise FontLabError("Variable Lab emitted incorrect axis bounds. No output was kept.")
        _assert_hinting_absent(variable_font)

        for emitted, expected in zip(emitted_axes, axes):
            axis_names = {
                record.toUnicode()
                for record in variable_font["name"].names
                if int(record.nameID) == int(emitted.axisNameID)
            }
            if axis_names != {expected.name}:
                raise FontLabError("Variable Lab emitted a non-neutral axis label. No output was kept.")
        if int(variable_font["OS/2"].usWeightClass) != default_donor.weight:
            raise FontLabError("Variable Lab emitted an incorrect default weight class. No output was kept.")
        if int(variable_font["OS/2"].usWidthClass) != default_donor.width_class:
            raise FontLabError("Variable Lab emitted an incorrect default width class. No output was kept.")

        for index, donor in enumerate(donors, start=1):
            instance = instantiateVariableFont(
                variable_font,
                {axis.tag: _axis_value(donor, axis.tag) for axis in axes},
                inplace=False,
                optimize=True,
                overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
                updateFontNames=False,
                static=True,
            )
            try:
                _assert_matching_instance(donor.path, instance, stage / f"verified-instance-{index:02d}.ttf")
            finally:
                instance.close()
    finally:
        variable_font.close()


def build_variable_outputs(sources: list[Path], output_dir: Path) -> PublicBuildResult:
    """Build a zero-ID ``wght``/``wdth`` variable font from compatible donors."""
    source_paths = [Path(source) for source in sources]
    output_dir = Path(output_dir)
    donors = _preflight_donors(source_paths)
    output_dir.mkdir(parents=True, exist_ok=True)

    native = output_dir / VARIABLE_NATIVE_NAME
    web = output_dir / VARIABLE_WEB_NAME
    css = output_dir / VARIABLE_CSS_NAME
    bundle = output_dir / VARIABLE_BUNDLE_NAME
    _reject_source_output_collision(source_paths, [native, web, css, bundle])

    with tempfile.TemporaryDirectory(prefix=".fontlab-variable-stage-", dir=str(output_dir)) as stage_text:
        stage = Path(stage_text)
        native_stage = stage / native.name
        web_stage = stage / web.name
        css_stage = stage / css.name
        decoded_stage = stage / "decoded-variable.ttf"
        bundle_stage = stage / bundle.name

        axes, default_donor = _build_variable_native(donors, stage, native_stage)
        for donor in donors:
            assert_strict_output(native_stage, donor.path)
        _verify_variable_native(native_stage, donors, axes, default_donor, stage)

        build_full_woff2(native_stage, web_stage, overwrite=True, css_family=CSS_FAMILY)
        css_text = _variable_css(axes, default_donor)
        _assert_css(css_text, web_name=web.name)
        css_stage.write_text(css_text, encoding="utf-8")

        _decode_woff2(web_stage, decoded_stage)
        for donor in donors:
            assert_strict_output(decoded_stage, donor.path)
        _verify_woff2_roundtrip(native_stage, decoded_stage)
        _verify_variable_native(decoded_stage, donors, axes, default_donor, stage)

        package_files = [(native_stage, native.name), (web_stage, web.name), (css_stage, css.name)]
        _deterministic_bundle(bundle_stage, package_files)
        _verify_bundle(bundle_stage, package_files)
        _commit(((native_stage, native), (web_stage, web), (css_stage, css), (bundle_stage, bundle)))

    checks = {
        "source_identity_removed": True,
        "embedding_flags_cleared": True,
        "donor_compatibility_verified": True,
        "donor_instances_verified": True,
        "independent_axis_model_verified": True,
        "axis_metadata_verified": True,
        "hinting_removed": True,
        "harfbuzz_shaping_verified": True,
        "woff2_roundtrip_verified": True,
    }
    if any(axis.tag == "wght" for axis in axes):
        checks["weight_axis_verified"] = True
    if any(axis.tag == "wdth" for axis in axes):
        checks["width_axis_verified"] = True

    return PublicBuildResult(
        native=OutputFile("native", native.name, "font/ttf"),
        web=OutputFile("web", web.name, "font/woff2"),
        css=OutputFile("css", css.name, "text/css; charset=utf-8"),
        bundle=OutputFile("bundle", bundle.name, "application/zip"),
        flavor="TrueType",
        variable=True,
        color=donors[0].color,
        checks=checks,
        axes=tuple(axis.public() for axis in axes),
    )
