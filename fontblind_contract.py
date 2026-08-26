"""Strict validation for data crossing the isolated worker boundary."""
from __future__ import annotations

import math
import re
from pathlib import PurePath

from fontblind_pipeline import OutputFile, PublicBuildResult


_OUTPUT_KINDS = {
    "native": ({"font/ttf", "font/otf"}, {".ttf", ".otf"}),
    "web": ({"font/woff2"}, {".woff2"}),
    "css": ({"text/css; charset=utf-8"}, {".css"}),
    "bundle": ({"application/zip"}, {".zip"}),
}
_AXIS_NAMES = {"wght": "Weight", "wdth": "Width", "slnt": "Slant"}
_SAFE_FILENAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MASTER_ID = re.compile(r"^M\d{2}$")
_KNOWN_CHECKS = frozenset(
    {
        "source_identity_removed",
        "embedding_flags_cleared",
        "outline_flavor_retained",
        "functional_clone_verified",
        "harfbuzz_shaping_verified",
        "woff2_roundtrip_verified",
        "source_discarded",
        "declared_shear_verified",
        "oblique_not_italic_verified",
        "hinting_removed",
        "donor_compatibility_verified",
        "donor_instances_verified",
        "independent_axis_model_verified",
        "axis_metadata_verified",
        "weight_axis_verified",
        "width_axis_verified",
        "slant_axis_verified",
        "variable_endpoints_verified",
        "selected_location_verified",
        "static_instance_verified",
        "variation_tables_removed",
    }
)


class BuildResultContractError(ValueError):
    """A worker result is not safe to store or expose."""


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildResultContractError(f"{context} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise BuildResultContractError(f"{context} must be a finite number")
    return number


def _validate_output(item: OutputFile, expected_kind: str) -> None:
    if not isinstance(item, OutputFile) or item.kind != expected_kind:
        raise BuildResultContractError("worker returned an invalid output descriptor")
    if not isinstance(item.filename, str) or not _SAFE_FILENAME.fullmatch(item.filename):
        raise BuildResultContractError("worker returned an unsafe output filename")
    if PurePath(item.filename).name != item.filename or any(character in item.filename for character in ("/", "\\", "\x00")):
        raise BuildResultContractError("worker returned an unsafe output filename")
    media_types, suffixes = _OUTPUT_KINDS[expected_kind]
    if item.media_type not in media_types or PurePath(item.filename).suffix.lower() not in suffixes:
        raise BuildResultContractError("worker returned an incoherent output descriptor")


def _validated_axes(result: PublicBuildResult) -> dict[str, tuple[float, float, float]]:
    if not isinstance(result.axes, tuple) or len(result.axes) > len(_AXIS_NAMES):
        raise BuildResultContractError("worker returned malformed variation controls")
    axes: dict[str, tuple[float, float, float]] = {}
    for raw_axis in result.axes:
        if not isinstance(raw_axis, dict) or set(raw_axis) != {"tag", "name", "min", "default", "max"}:
            raise BuildResultContractError("worker returned malformed variation controls")
        tag = raw_axis["tag"]
        if not isinstance(tag, str) or tag not in _AXIS_NAMES or tag in axes:
            raise BuildResultContractError("worker returned malformed variation controls")
        if raw_axis["name"] != _AXIS_NAMES[tag]:
            raise BuildResultContractError("worker returned a non-neutral axis label")
        minimum = _number(raw_axis["min"], "axis minimum")
        default = _number(raw_axis["default"], "axis default")
        maximum = _number(raw_axis["max"], "axis maximum")
        if not minimum <= default <= maximum or minimum == maximum:
            raise BuildResultContractError("worker returned invalid variation bounds")
        axes[tag] = (minimum, default, maximum)
    if axes and result.variable is not True:
        raise BuildResultContractError("a static result cannot expose variation controls")
    return axes


def _validate_masters(result: PublicBuildResult, axes: dict[str, tuple[float, float, float]]) -> None:
    if not isinstance(result.masters, tuple):
        raise BuildResultContractError("worker returned malformed anonymous masters")
    if not result.masters:
        return
    if not axes or not 2 <= len(result.masters) <= 12:
        raise BuildResultContractError("worker returned malformed anonymous masters")
    identifiers: set[str] = set()
    coordinates: set[tuple[tuple[str, float], ...]] = set()
    defaults = 0
    expected_tags = set(axes)
    for raw_master in result.masters:
        if not isinstance(raw_master, dict) or set(raw_master) != {"id", "location", "default"}:
            raise BuildResultContractError("worker returned malformed anonymous masters")
        identifier = raw_master["id"]
        if not isinstance(identifier, str) or not _MASTER_ID.fullmatch(identifier) or identifier in identifiers:
            raise BuildResultContractError("worker returned malformed anonymous masters")
        identifiers.add(identifier)
        if type(raw_master["default"]) is not bool:
            raise BuildResultContractError("worker returned malformed anonymous masters")
        defaults += int(raw_master["default"])
        location = raw_master["location"]
        if not isinstance(location, dict) or set(location) != expected_tags:
            raise BuildResultContractError("worker returned an incomplete anonymous master location")
        coordinate: list[tuple[str, float]] = []
        for tag in sorted(expected_tags):
            value = _number(location[tag], "master coordinate")
            minimum, _default, maximum = axes[tag]
            if value < minimum or value > maximum:
                raise BuildResultContractError("worker returned an out-of-range anonymous master")
            coordinate.append((tag, value))
        row = tuple(coordinate)
        if row in coordinates:
            raise BuildResultContractError("worker returned duplicate anonymous master coordinates")
        coordinates.add(row)
    if defaults != 1:
        raise BuildResultContractError("worker returned no unique anonymous default master")


def validate_build_result(result: PublicBuildResult) -> None:
    """Require a small, neutral, path-safe, internally coherent worker result."""
    if not isinstance(result, PublicBuildResult):
        raise BuildResultContractError("worker returned an invalid result type")
    if type(result.variable) is not bool or type(result.color) is not bool:
        raise BuildResultContractError("worker returned malformed font descriptors")
    if result.flavor not in {"TrueType", "OpenType CFF", "OpenType CFF2"}:
        raise BuildResultContractError("worker returned an unsupported outline descriptor")

    files = []
    for kind in _OUTPUT_KINDS:
        item = getattr(result, kind)
        _validate_output(item, kind)
        files.append(item.filename)
    if len(set(files)) != len(files):
        raise BuildResultContractError("worker returned colliding output filenames")
    if result.flavor == "TrueType" and result.native.media_type != "font/ttf":
        raise BuildResultContractError("worker returned an incoherent native font type")
    if result.flavor != "TrueType" and result.native.media_type != "font/otf":
        raise BuildResultContractError("worker returned an incoherent native font type")

    if not isinstance(result.checks, dict) or not result.checks:
        raise BuildResultContractError("worker returned no verification proof")
    if not set(result.checks).issubset(_KNOWN_CHECKS):
        raise BuildResultContractError("worker returned an unrecognised verification claim")
    for key, passed in result.checks.items():
        if not isinstance(key, str) or not key or type(passed) is not bool or passed is not True:
            raise BuildResultContractError("worker returned a failed or malformed verification proof")

    axes = _validated_axes(result)
    _validate_masters(result, axes)
