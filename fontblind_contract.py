"""Strict validation for data crossing the isolated worker boundary."""
from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import BinaryIO, Mapping

from fontTools.ttLib import TTFont, TTLibError

from fontblind_pipeline import OutputFile, PublicBuildResult


LANE_BLIND = "blind"
LANE_OBLIQUE = "oblique-static"
LANE_SLANT = "oblique-slnt"
LANE_VARIABLE = "variable"
LANE_INSTANCE = "instance"
LANES = frozenset({LANE_BLIND, LANE_OBLIQUE, LANE_SLANT, LANE_VARIABLE, LANE_INSTANCE})

_OUTPUT_KINDS = {
    "native": ({"font/ttf", "font/otf"}, {".ttf", ".otf"}),
    "web": ({"font/woff2"}, {".woff2"}),
    "css": ({"text/css; charset=utf-8"}, {".css"}),
    "bundle": ({"application/zip"}, {".zip"}),
}
_OUTPUT_MAX_BYTES = {
    "native": 160 * 1024 * 1024,
    "web": 160 * 1024 * 1024,
    "css": 1024 * 1024,
    "bundle": 320 * 1024 * 1024,
}
_AXIS_NAMES = {"wght": "Weight", "wdth": "Width", "slnt": "Slant"}
_SAFE_FILENAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MASTER_ID = re.compile(r"^M\d{2}$")
_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\"\s)]+)['\"]?\s*\)", re.IGNORECASE)
_PARENT_CHECK = "source_discarded"

_LANE_REQUIRED_CHECKS = {
    LANE_BLIND: frozenset(
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "outline_flavor_retained",
            "functional_clone_verified",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    ),
    LANE_OBLIQUE: frozenset(
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "declared_shear_verified",
            "oblique_not_italic_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    ),
    LANE_SLANT: frozenset(
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "declared_shear_verified",
            "slant_axis_verified",
            "variable_endpoints_verified",
            "oblique_not_italic_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    ),
    LANE_VARIABLE: frozenset(
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "donor_compatibility_verified",
            "donor_instances_verified",
            "independent_axis_model_verified",
            "axis_metadata_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    ),
    LANE_INSTANCE: frozenset(
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "selected_location_verified",
            "static_instance_verified",
            "variation_tables_removed",
            "axis_metadata_verified",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    ),
}
_KNOWN_CHECKS = frozenset().union(*_LANE_REQUIRED_CHECKS.values(), {_PARENT_CHECK, "weight_axis_verified", "width_axis_verified"})


class BuildResultContractError(ValueError):
    """A worker result or generated artifact is not safe to store or expose."""


@dataclass(frozen=True)
class ArtifactSeal:
    """Immutable file identity recorded after the worker has exited."""

    kind: str
    filename: str
    size: int
    sha256: str
    device: int
    inode: int
    modified_ns: int


def expected_lane_for(mode: str, options: Mapping[str, object] | None = None) -> str:
    """Resolve the only result lane a requested worker invocation may return."""
    values = dict(options or {})
    if mode == "blind":
        return LANE_BLIND
    if mode == "oblique":
        output = values.get("output", "static")
        if output == "static":
            return LANE_OBLIQUE
        if output == "slnt":
            return LANE_SLANT
        raise BuildResultContractError("worker invocation requested an invalid Oblique output")
    if mode == "variable":
        return LANE_VARIABLE
    if mode == "instance":
        return LANE_INSTANCE
    raise BuildResultContractError("worker invocation requested an unknown result lane")


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


def _validated_axes(result: PublicBuildResult) -> tuple[tuple[str, ...], dict[str, tuple[float, float, float]]]:
    if not isinstance(result.axes, tuple) or len(result.axes) > len(_AXIS_NAMES):
        raise BuildResultContractError("worker returned malformed variation controls")
    axes: dict[str, tuple[float, float, float]] = {}
    ordered: list[str] = []
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
        ordered.append(tag)
    if axes and result.variable is not True:
        raise BuildResultContractError("a static result cannot expose variation controls")
    return tuple(ordered), axes


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
    default_location: dict[str, float] | None = None
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
        normalized: dict[str, float] = {}
        for tag in sorted(expected_tags):
            value = _number(location[tag], "master coordinate")
            minimum, _default, maximum = axes[tag]
            if value < minimum or value > maximum:
                raise BuildResultContractError("worker returned an out-of-range anonymous master")
            coordinate.append((tag, value))
            normalized[tag] = value
        row = tuple(coordinate)
        if row in coordinates:
            raise BuildResultContractError("worker returned duplicate anonymous master coordinates")
        coordinates.add(row)
        if raw_master["default"]:
            default_location = normalized
    if defaults != 1 or default_location is None:
        raise BuildResultContractError("worker returned no unique anonymous default master")
    if any(not math.isclose(default_location[tag], axes[tag][1], rel_tol=0.0, abs_tol=1e-6) for tag in axes):
        raise BuildResultContractError("worker returned an anonymous default at the wrong coordinates")


def _lane_checks(lane: str, axis_tags: tuple[str, ...], require_source_discarded: bool) -> frozenset[str]:
    if lane not in LANES:
        raise BuildResultContractError("parent requested an unknown result lane")
    required = set(_LANE_REQUIRED_CHECKS[lane])
    if lane == LANE_VARIABLE:
        if "wght" in axis_tags:
            required.add("weight_axis_verified")
        if "wdth" in axis_tags:
            required.add("width_axis_verified")
    if require_source_discarded:
        required.add(_PARENT_CHECK)
    return frozenset(required)


def _validate_lane_structure(
    result: PublicBuildResult,
    lane: str,
    axis_tags: tuple[str, ...],
) -> None:
    if lane == LANE_BLIND:
        if axis_tags or result.masters:
            raise BuildResultContractError("Blind returned Lab inspection data")
        return
    if result.flavor != "TrueType":
        raise BuildResultContractError("Font Lab returned a non-TrueType result")
    if lane in {LANE_OBLIQUE, LANE_INSTANCE}:
        if result.variable or axis_tags or result.masters:
            raise BuildResultContractError("a static Lab lane returned variable controls")
        return
    if lane == LANE_SLANT:
        if not result.variable or axis_tags != ("slnt",) or len(result.masters) != 2:
            raise BuildResultContractError("Oblique Lab returned an incoherent slant model")
        return
    if lane == LANE_VARIABLE:
        if not result.variable or axis_tags not in {("wght",), ("wdth",), ("wght", "wdth")}:
            raise BuildResultContractError("Variable Lab returned an incoherent axis model")
        if not 2 <= len(result.masters) <= 12:
            raise BuildResultContractError("Variable Lab returned an incoherent donor map")
        return
    raise BuildResultContractError("parent requested an unknown result lane")


def validate_build_result(
    result: PublicBuildResult,
    *,
    expected_lane: str | None = None,
    require_source_discarded: bool = False,
) -> None:
    """Require a neutral, path-safe, internally coherent worker result.

    Supplying ``expected_lane`` closes the proof vocabulary for that exact
    worker invocation. The parent must additionally require ``source_discarded``.
    """
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

    axis_tags, axes = _validated_axes(result)
    _validate_masters(result, axes)
    if expected_lane is not None:
        _validate_lane_structure(result, expected_lane, axis_tags)
        expected_checks = _lane_checks(expected_lane, axis_tags, require_source_discarded)
        if frozenset(result.checks) != expected_checks:
            missing = sorted(expected_checks - set(result.checks))
            extra = sorted(set(result.checks) - expected_checks)
            detail = ""
            if missing:
                detail += " missing=" + ",".join(missing)
            if extra:
                detail += " extra=" + ",".join(extra)
            raise BuildResultContractError("worker returned the wrong proof contract" + detail)
    elif require_source_discarded and result.checks.get(_PARENT_CHECK) is not True:
        raise BuildResultContractError("parent result omitted source-discard proof")


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


def _artifact_path(output_root: Path, item: OutputFile) -> tuple[Path, os.stat_result]:
    target = output_root / item.filename
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise BuildResultContractError("worker omitted a declared output file") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
        raise BuildResultContractError("worker returned a non-regular or linked output file")
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise BuildResultContractError("worker returned an unresolved output file") from exc
    if resolved.parent != output_root:
        raise BuildResultContractError("worker returned an output outside its owned directory")
    if metadata.st_size <= 0 or metadata.st_size > _OUTPUT_MAX_BYTES[item.kind]:
        raise BuildResultContractError("worker returned an implausible output size")
    return target, metadata


def _font_signature(path: Path, result: PublicBuildResult, kind: str) -> None:
    with path.open("rb") as stream:
        signature = stream.read(4)
    if kind == "web":
        if signature != b"wOF2":
            raise BuildResultContractError("worker returned a non-WOFF2 web output")
    elif result.native.media_type == "font/ttf":
        if signature not in {b"\x00\x01\x00\x00", b"true", b"typ1"}:
            raise BuildResultContractError("worker returned a non-TrueType native output")
    elif signature != b"OTTO":
        raise BuildResultContractError("worker returned a non-OpenType native output")

    try:
        font = TTFont(str(path), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    except (OSError, TTLibError, Exception) as exc:
        raise BuildResultContractError("worker returned an unreadable font output") from exc
    try:
        if kind == "native" and font.flavor is not None:
            raise BuildResultContractError("worker returned a wrapped native output")
        if kind == "web" and font.flavor != "woff2":
            raise BuildResultContractError("worker returned an incoherent WOFF2 output")
        if result.flavor == "TrueType" and "glyf" not in font:
            raise BuildResultContractError("worker returned the wrong outline flavour")
        if result.flavor == "OpenType CFF" and "CFF " not in font:
            raise BuildResultContractError("worker returned the wrong outline flavour")
        if result.flavor == "OpenType CFF2" and "CFF2" not in font:
            raise BuildResultContractError("worker returned the wrong outline flavour")
        if ("fvar" in font) is not result.variable:
            raise BuildResultContractError("worker returned incoherent variable-font metadata")
    finally:
        font.close()


def _validate_css(path: Path, web_filename: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildResultContractError("worker returned unreadable CSS") from exc
    folded = text.casefold()
    if text.count("@font-face") != 1 or "local(" in folded or "untitled" not in folded or "\x00" in text:
        raise BuildResultContractError("worker returned CSS outside the zero-ID contract")
    urls = _CSS_URL.findall(text)
    if urls != [web_filename]:
        raise BuildResultContractError("worker returned CSS pointing outside its generated package")
    if any(scheme in folded for scheme in ("http:", "https:", "data:", "file:")):
        raise BuildResultContractError("worker returned CSS with an external source")


def _validate_bundle(path: Path, output_root: Path, result: PublicBuildResult) -> None:
    expected = [result.native.filename, result.web.filename, result.css.filename]
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildResultContractError("worker returned an unreadable package") from exc
    with archive:
        infos = archive.infolist()
        if archive.comment or [info.filename for info in infos] != expected or len({info.filename for info in infos}) != len(infos):
            raise BuildResultContractError("worker returned an unexpected package manifest")
        for info in infos:
            if PurePath(info.filename).name != info.filename or info.is_dir() or info.flag_bits & 0x1:
                raise BuildResultContractError("worker returned an unsafe package member")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise BuildResultContractError("worker returned a symlink inside its package")
            source = output_root / info.filename
            if info.file_size != source.stat().st_size:
                raise BuildResultContractError("worker returned a package member with the wrong size")
            with archive.open(info, "r") as member:
                member_hash = _sha256_stream(member)
            if member_hash != _sha256_file(source):
                raise BuildResultContractError("worker returned a package that changed an output file")


def validate_job_artifacts(job_dir: Path, result: PublicBuildResult) -> dict[str, ArtifactSeal]:
    """Inspect the actual files after the worker exits and before a token is exposed."""
    root = Path(job_dir)
    try:
        root_metadata = root.lstat()
        output_dir = root / "output"
        output_metadata = output_dir.lstat()
        root_resolved = root.resolve(strict=True)
        output_root = output_dir.resolve(strict=True)
    except OSError as exc:
        raise BuildResultContractError("worker returned no owned output directory") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise BuildResultContractError("worker job root is not an owned directory")
    if not stat.S_ISDIR(output_metadata.st_mode) or stat.S_ISLNK(output_metadata.st_mode) or output_root.parent != root_resolved:
        raise BuildResultContractError("worker output directory escaped its owned job")

    expected_names = {getattr(result, kind).filename for kind in _OUTPUT_KINDS}
    try:
        actual = {entry.name for entry in output_dir.iterdir()}
    except OSError as exc:
        raise BuildResultContractError("worker output directory could not be enumerated") from exc
    if actual != expected_names:
        raise BuildResultContractError("worker left missing or unexpected output files")

    seals: dict[str, ArtifactSeal] = {}
    paths: dict[str, Path] = {}
    for kind in _OUTPUT_KINDS:
        item = getattr(result, kind)
        path, metadata = _artifact_path(output_root, item)
        paths[kind] = path
        if kind in {"native", "web"}:
            _font_signature(path, result, kind)
        elif kind == "css":
            _validate_css(path, result.web.filename)
        else:
            with path.open("rb") as stream:
                if stream.read(4) != b"PK\x03\x04":
                    raise BuildResultContractError("worker returned a non-ZIP package")
        seals[kind] = ArtifactSeal(
            kind=kind,
            filename=item.filename,
            size=int(metadata.st_size),
            sha256=_sha256_file(path),
            device=int(metadata.st_dev),
            inode=int(metadata.st_ino),
            modified_ns=int(metadata.st_mtime_ns),
        )

    _validate_bundle(paths["bundle"], output_root, result)
    return seals


def verify_artifact_seal(job_dir: Path, item: OutputFile, seal: ArtifactSeal) -> bool:
    """Recheck a stored artifact immediately before a local download."""
    if item.kind != seal.kind or item.filename != seal.filename:
        return False
    try:
        output_dir = (Path(job_dir) / "output").resolve(strict=True)
        path, metadata = _artifact_path(output_dir, item)
    except (OSError, BuildResultContractError):
        return False
    if (
        int(metadata.st_size) != seal.size
        or int(metadata.st_dev) != seal.device
        or int(metadata.st_ino) != seal.inode
        or int(metadata.st_mtime_ns) != seal.modified_ns
    ):
        return False
    try:
        return _sha256_file(path) == seal.sha256
    except OSError:
        return False
