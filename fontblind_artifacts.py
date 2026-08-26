"""Parent-side validation of generated artifacts before any local token is exposed."""
from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePath
from typing import BinaryIO

from fontTools.ttLib import TTFont, TTLibError

from fontblind_contract import (
    ArtifactSeal,
    BuildResultContractError,
    _OUTPUT_MAX_BYTES,
)
from fontblind_pipeline import (
    OutputFile,
    PublicBuildResult,
    _decode_woff2,
    _verify_woff2_roundtrip,
)
from fontblind_web import WebBuildError


_VARIATION_TABLES = frozenset({"avar", "cvar", "fvar", "gvar", "HVAR", "MVAR", "STAT", "VVAR"})
_CSS_NUMBER = r"(?:0|[1-9]\d{0,3})(?:\.\d{1,4})?"
_CSS_WEIGHT = r"(?:[1-9]\d{0,2}|1000)(?: (?:[1-9]\d{0,2}|1000))?"
_CSS_PERCENT = rf"{_CSS_NUMBER}%(?: {_CSS_NUMBER}%)?"
_CSS_STYLE = rf"(?:normal|italic|oblique(?: {_CSS_NUMBER}deg(?: {_CSS_NUMBER}deg)?)?)"
_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
_ZIP_MODE = 0o644


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


def _owned_by_process(metadata: os.stat_result) -> bool:
    getter = getattr(os, "getuid", None)
    return not callable(getter) or int(metadata.st_uid) == int(getter())


def _artifact_path(output_root: Path, item: OutputFile) -> tuple[Path, os.stat_result]:
    target = output_root / item.filename
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise BuildResultContractError("worker omitted a declared output file") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or not _owned_by_process(metadata)
    ):
        raise BuildResultContractError("worker returned a non-regular, linked, or foreign output file")
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise BuildResultContractError("worker returned an unresolved output file") from exc
    if resolved.parent != output_root:
        raise BuildResultContractError("worker returned an output outside its owned directory")
    if metadata.st_size <= 0 or metadata.st_size > _OUTPUT_MAX_BYTES[item.kind]:
        raise BuildResultContractError("worker returned an implausible output size")
    return target, metadata


def _axis_rows(font: TTFont) -> tuple[tuple[str, float, float, float], ...]:
    if "fvar" not in font:
        return ()
    rows: list[tuple[str, float, float, float]] = []
    seen: set[str] = set()
    for axis in font["fvar"].axes:
        tag = str(axis.axisTag)
        values = (float(axis.minValue), float(axis.defaultValue), float(axis.maxValue))
        if tag in seen or not all(math.isfinite(value) for value in values) or not values[0] <= values[1] <= values[2]:
            raise BuildResultContractError("generated font contains malformed variation axes")
        seen.add(tag)
        rows.append((tag, *values))
    return tuple(rows)


def _expected_axis_rows(result: PublicBuildResult) -> tuple[tuple[str, float, float, float], ...]:
    return tuple(
        (
            str(axis["tag"]),
            float(axis["min"]),
            float(axis["default"]),
            float(axis["max"]),
        )
        for axis in result.axes
    )


def _axis_rows_match(
    actual: tuple[tuple[str, float, float, float], ...],
    expected: tuple[tuple[str, float, float, float], ...],
) -> bool:
    if len(actual) != len(expected):
        return False
    for actual_row, expected_row in zip(actual, expected):
        if actual_row[0] != expected_row[0]:
            return False
        if any(
            not math.isclose(actual_value, expected_value, rel_tol=0.0, abs_tol=1e-6)
            for actual_value, expected_value in zip(actual_row[1:], expected_row[1:])
        ):
            return False
    return True


def _assert_no_variable_layout(font: TTFont) -> None:
    if "GDEF" in font and getattr(font["GDEF"].table, "VarStore", None) is not None:
        raise BuildResultContractError("static output retained a GDEF variation store")
    for tag in ("GSUB", "GPOS"):
        if tag in font and getattr(font[tag].table, "FeatureVariations", None) is not None:
            raise BuildResultContractError("static output retained variable layout substitutions")


def _validate_font(path: Path, result: PublicBuildResult, kind: str) -> None:
    with path.open("rb") as stream:
        signature = stream.read(4)
    if kind == "web":
        if signature != b"wOF2":
            raise BuildResultContractError("worker returned a non-WOFF2 web output")
    elif result.native.media_type == "font/ttf":
        if signature not in {b"\x00\x01\x00\x00", b"true"}:
            raise BuildResultContractError("worker returned a non-TrueType native output")
    elif signature != b"OTTO":
        raise BuildResultContractError("worker returned a non-OpenType native output")

    try:
        font = TTFont(str(path), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    except Exception as exc:
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

        axes = _axis_rows(font)
        if bool(axes) is not result.variable:
            raise BuildResultContractError("worker returned incoherent variable-font metadata")
        if result.variable and result.flavor == "TrueType" and "gvar" not in font:
            raise BuildResultContractError("TrueType variable output omitted its glyph-variation table")
        expected_axes = _expected_axis_rows(result)
        if expected_axes and not _axis_rows_match(axes, expected_axes):
            raise BuildResultContractError("generated font axes disagree with the public result")

        if result.checks.get("variation_tables_removed") is True:
            retained = sorted(set(font.keys()) & set(_VARIATION_TABLES))
            if retained:
                raise BuildResultContractError("frozen output retained variable-font machinery")
            _assert_no_variable_layout(font)
    finally:
        font.close()


def _validate_css(path: Path, web_filename: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildResultContractError("worker returned unreadable CSS") from exc
    if not text.isascii() or "\r" in text or "\x00" in text:
        raise BuildResultContractError("worker returned non-canonical CSS text")
    filename = re.escape(web_filename)
    contract = re.compile(
        rf'@font-face \{{\n'
        rf'  font-family: "Untitled";\n'
        rf'  src: url\("{filename}"\) format\("(?:woff2|woff2-variations)"\);\n'
        rf'  font-weight: {_CSS_WEIGHT};\n'
        rf'  font-style: {_CSS_STYLE};\n'
        rf'  font-stretch: {_CSS_PERCENT};\n'
        rf'  font-display: swap;\n'
        rf'\}}\n'
    )
    if contract.fullmatch(text) is None:
        raise BuildResultContractError("worker returned CSS outside the exact zero-ID package contract")


def _validate_font_pair(native: Path, web: Path, work_root: Path) -> None:
    try:
        with tempfile.TemporaryDirectory(prefix=".fontblind-roundtrip-", dir=str(work_root)) as temp_text:
            decoded = Path(temp_text) / native.name
            _decode_woff2(web, decoded)
            _verify_woff2_roundtrip(native, decoded)
    except Exception as exc:
        raise BuildResultContractError("native and WOFF2 outputs do not describe the same font") from exc


def _validate_bundle(path: Path, output_root: Path, result: PublicBuildResult) -> None:
    expected = [result.native.filename, result.web.filename, result.css.filename]
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if (
                archive.comment
                or [info.filename for info in infos] != expected
                or len({info.filename for info in infos}) != len(infos)
            ):
                raise BuildResultContractError("worker returned an unexpected package manifest")
            for info in infos:
                mode = (info.external_attr >> 16) & 0xFFFF
                if (
                    PurePath(info.filename).name != info.filename
                    or info.is_dir()
                    or info.flag_bits != 0
                    or info.compress_type != zipfile.ZIP_DEFLATED
                    or info.date_time != _ZIP_TIMESTAMP
                    or info.comment
                    or info.extra
                    or mode != _ZIP_MODE
                ):
                    raise BuildResultContractError("worker returned non-canonical or unsafe package metadata")
                source = output_root / info.filename
                source_size = source.stat().st_size
                member_kind = (
                    "css"
                    if info.filename == result.css.filename
                    else "web"
                    if info.filename == result.web.filename
                    else "native"
                )
                if info.file_size != source_size or info.file_size > _OUTPUT_MAX_BYTES[member_kind]:
                    raise BuildResultContractError("worker returned a package member with the wrong size")
                with archive.open(info, "r") as member:
                    member_hash = _sha256_stream(member)
                if member_hash != _sha256_file(source):
                    raise BuildResultContractError("worker returned a package that changed an output file")
    except BuildResultContractError:
        raise
    except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
        raise BuildResultContractError("worker returned an unreadable or unsupported package") from exc


def validate_job_artifacts(job_dir: Path, result: PublicBuildResult) -> dict[str, ArtifactSeal]:
    """Inspect actual files after worker exit and before any public token is exposed."""
    root = Path(job_dir)
    try:
        root_metadata = root.lstat()
        output_dir = root / "output"
        output_metadata = output_dir.lstat()
        root_resolved = root.resolve(strict=True)
        output_root = output_dir.resolve(strict=True)
    except OSError as exc:
        raise BuildResultContractError("worker returned no owned output directory") from exc
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or not _owned_by_process(root_metadata)
    ):
        raise BuildResultContractError("worker job root is not an owned directory")
    if (
        not stat.S_ISDIR(output_metadata.st_mode)
        or stat.S_ISLNK(output_metadata.st_mode)
        or not _owned_by_process(output_metadata)
        or output_root.parent != root_resolved
    ):
        raise BuildResultContractError("worker output directory escaped its owned job")

    expected_names = {getattr(result, kind).filename for kind in ("native", "web", "css", "bundle")}
    try:
        actual = {entry.name for entry in output_dir.iterdir()}
    except OSError as exc:
        raise BuildResultContractError("worker output directory could not be enumerated") from exc
    if actual != expected_names:
        raise BuildResultContractError("worker left missing or unexpected output files")

    seals: dict[str, ArtifactSeal] = {}
    paths: dict[str, Path] = {}
    for kind in ("native", "web", "css", "bundle"):
        item = getattr(result, kind)
        path, metadata = _artifact_path(output_root, item)
        paths[kind] = path
        if kind in {"native", "web"}:
            _validate_font(path, result, kind)
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

    _validate_font_pair(paths["native"], paths["web"], root_resolved)
    _validate_bundle(paths["bundle"], output_root, result)
    return seals


def retained_artifact_bytes(seals: dict[str, ArtifactSeal]) -> int:
    return sum(int(seal.size) for seal in seals.values())


def verify_artifact_seal(job_dir: Path, item: OutputFile, seal: ArtifactSeal) -> bool:
    """Recheck a retained artifact immediately before creating a sealed snapshot."""
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
