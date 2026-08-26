"""Atomic native + WOFF2 pipeline used by the local browser server."""
from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from fontTools.ttLib import TTFont
import uharfbuzz as hb

import fontblind_surgical as surgical
from fontblind_outline import _glyf_instruction_signature, _glyf_signature
from fontblind_policy import SourceContract, assert_strict_output, inspect_strict_source
from fontblind_web import WebBuildError, build_full_woff2


CSS_FAMILY = "Untitled"

SHAPING_PROBES = (
    "AVATAR office affinity ffi fl",
    "السَّلَامُ عَلَيْكُمْ",
    "नमस्ते दुनिया क्षत्रिय",
    "שָׁלוֹם עוֹלָם",
    "สวัสดีชาวโลก",
    "Á V̈ fi — 0123456789",
)


@dataclass(frozen=True)
class OutputFile:
    kind: str
    filename: str
    media_type: str


@dataclass(frozen=True)
class PublicBuildResult:
    native: OutputFile
    web: OutputFile
    css: OutputFile
    bundle: OutputFile
    flavor: str
    variable: bool
    color: bool
    checks: dict[str, bool]
    axes: tuple[dict[str, object], ...] = ()
    masters: tuple[dict[str, object], ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        # Format/color descriptors stay private. Neutral generated-axis bounds
        # are exposed only when the browser needs them for live controls.
        result: dict[str, object] = {
            "native": asdict(self.native),
            "web": asdict(self.web),
            "css": asdict(self.css),
            "bundle": asdict(self.bundle),
            "checks": dict(self.checks),
        }
        if self.axes:
            result["axes"] = [dict(axis) for axis in self.axes]
        if self.masters:
            result["masters"] = [dict(master) for master in self.masters]
        return result

    def to_internal_dict(self) -> dict[str, object]:
        return asdict(self)

    def require_verified(self) -> None:
        if not self.checks:
            raise ValueError("FontBlind worker returned no verification proof")
        for key, passed in self.checks.items():
            if not isinstance(key, str) or not key or type(passed) is not bool:
                raise ValueError("FontBlind worker returned malformed verification proof")
            if passed is not True:
                raise ValueError("FontBlind worker returned a failed verification proof")

    @classmethod
    def from_internal_dict(cls, value: dict[str, object]) -> "PublicBuildResult":
        raw_checks = value["checks"]
        if not isinstance(raw_checks, dict):
            raise ValueError("FontBlind worker returned malformed verification proof")
        checks: dict[str, bool] = {}
        for key, item in raw_checks.items():
            if not isinstance(key, str) or not key or type(item) is not bool:
                raise ValueError("FontBlind worker returned malformed verification proof")
            checks[key] = item

        raw_axes = value.get("axes", ())
        raw_masters = value.get("masters", ())
        if not isinstance(raw_axes, (list, tuple)) or not isinstance(raw_masters, (list, tuple)):
            raise ValueError("FontBlind worker returned malformed Lab inspection data")
        if type(value.get("variable")) is not bool or type(value.get("color")) is not bool:
            raise ValueError("FontBlind worker returned malformed font descriptors")
        result = cls(
            native=OutputFile(**dict(value["native"])),
            web=OutputFile(**dict(value["web"])),
            css=OutputFile(**dict(value["css"])),
            bundle=OutputFile(**dict(value["bundle"])),
            flavor=str(value["flavor"]),
            variable=value["variable"],
            color=value["color"],
            checks=checks,
            axes=tuple(dict(axis) for axis in raw_axes),
            masters=tuple(dict(master) for master in raw_masters),
        )
        result.require_verified()
        return result


def _decode_woff2(source: Path, output: Path) -> None:
    font = TTFont(str(source), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    try:
        font.flavor = None
        font.save(str(output), reorderTables=True)
    finally:
        font.close()


def _verify_woff2_roundtrip(native: Path, decoded: Path) -> None:
    """Require byte identity except for WOFF2's defined glyf/loca transform.

    The decoder marks a transformed font in head.flags bit 11 and recomputes
    checkSumAdjustment. Those two values are container bookkeeping, not a font
    behavior change. Transformed TrueType outlines are compared exhaustively by
    GID after decoding.
    """
    source_font = TTFont(str(native), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    decoded_font = TTFont(str(decoded), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    try:
        source_tags = set(source_font.reader.keys())
        decoded_tags = set(decoded_font.reader.keys())
        if source_tags != decoded_tags or source_font.sfntVersion != decoded_font.sfntVersion:
            raise WebBuildError("WOFF2 changed the native table contract")

        transformed = {"head"}
        if "glyf" in source_tags:
            transformed.update({"glyf", "loca"})
        for tag in sorted(source_tags - transformed):
            if source_font.getTableData(tag) != decoded_font.getTableData(tag):
                raise WebBuildError("WOFF2 changed a native font table")

        source_head = source_font["head"]
        decoded_head = decoded_font["head"]
        excluded = {"checkSumAdjustment", "flags", "tableTag"}
        if "glyf" in source_tags:
            # WOFF2 may choose long rather than short loca offsets when it
            # reconstructs glyf/loca. Exhaustive per-GID geometry and program
            # checks below prove the semantic content independently.
            excluded.add("indexToLocFormat")
        source_values = {key: value for key, value in vars(source_head).items() if key not in excluded}
        decoded_values = {key: value for key, value in vars(decoded_head).items() if key not in excluded}
        if source_values != decoded_values:
            raise WebBuildError("WOFF2 changed a native header field")
        if int(source_head.flags) & ~0x0800 != int(decoded_head.flags) & ~0x0800:
            raise WebBuildError("WOFF2 changed functional header flags")

        if "glyf" in source_tags:
            glyphs = int(source_font["maxp"].numGlyphs)
            if glyphs != int(decoded_font["maxp"].numGlyphs):
                raise WebBuildError("WOFF2 changed the glyph count")
            for gid in range(glyphs):
                if _glyf_signature(source_font, gid) != _glyf_signature(decoded_font, gid):
                    raise WebBuildError("WOFF2 changed TrueType outline geometry")
                if _glyf_instruction_signature(source_font, gid) != _glyf_instruction_signature(decoded_font, gid):
                    raise WebBuildError("WOFF2 changed TrueType glyph instructions")
    finally:
        source_font.close()
        decoded_font.close()


def _harfbuzz_shape(path: Path, text: str) -> tuple[tuple[int, int, int, int, int, int], ...]:
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0)
            payload = stream.read()
        face = hb.Face(payload)
        font = hb.Font(face)
        font.scale = (face.upem, face.upem)
        buffer = hb.Buffer()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        hb.shape(font, buffer)
        return tuple(
            (
                int(info.codepoint),
                int(info.cluster),
                int(position.x_advance),
                int(position.y_advance),
                int(position.x_offset),
                int(position.y_offset),
            )
            for info, position in zip(buffer.glyph_infos, buffer.glyph_positions)
        )
    except Exception as exc:
        raise WebBuildError("The local HarfBuzz validator rejected a font") from exc


def _verify_shaping(source: Path, output: Path) -> None:
    for text in SHAPING_PROBES:
        if _harfbuzz_shape(source, text) != _harfbuzz_shape(output, text):
            raise WebBuildError("Shaping changed during identity removal")


def _deterministic_bundle(output: Path, files: list[tuple[Path, str]]) -> None:
    # ZIP's default timestamp is wall-clock time. Fix it so repeated builds of
    # the same inputs produce the same package bytes.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, name in files:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())


def build_browser_outputs(source: Path, output_dir: Path, *, verify_rounds: int = 3) -> PublicBuildResult:
    """Build all browser-product outputs or leave no partial destination."""
    source = Path(source)
    output_dir = Path(output_dir)
    contract: SourceContract = inspect_strict_source(source)
    output_dir.mkdir(parents=True, exist_ok=True)

    native_name = f"fontblind-native{contract.native_suffix}"
    native = output_dir / native_name
    web = output_dir / "fontblind-web.woff2"
    css = output_dir / "fontblind.css"
    bundle = output_dir / "fontblind-package.zip"

    with tempfile.TemporaryDirectory(prefix=".fontblind-stage-", dir=str(output_dir)) as stage_text:
        stage = Path(stage_text)
        native_stage = stage / native_name
        web_stage = stage / web.name
        css_stage = stage / css.name
        decoded_stage = stage / f"decoded{contract.native_suffix}"
        bundle_stage = stage / bundle.name

        surgical.recreate_font(source, native_stage, overwrite=True, verify_rounds=verify_rounds)
        assert_strict_output(native_stage, source)
        _verify_shaping(source, native_stage)

        web_report = build_full_woff2(
            native_stage,
            web_stage,
            overwrite=True,
            css_family=CSS_FAMILY,
        )
        css_text = web_report.css or ""
        if not css_text or "local(" in css_text.casefold() or CSS_FAMILY not in css_text:
            raise WebBuildError("Generated CSS failed the zero-ID web contract")
        css_stage.write_text(css_text, encoding="utf-8")

        _decode_woff2(web_stage, decoded_stage)
        assert_strict_output(decoded_stage, source)
        _verify_woff2_roundtrip(native_stage, decoded_stage)

        _deterministic_bundle(
            bundle_stage,
            [(native_stage, native_name), (web_stage, web.name), (css_stage, css.name)],
        )

        # Commit only after every check passes.
        for staged, destination in (
            (native_stage, native),
            (web_stage, web),
            (css_stage, css),
            (bundle_stage, bundle),
        ):
            os.replace(staged, destination)

    return PublicBuildResult(
        native=OutputFile("native", native.name, "font/ttf" if contract.native_suffix == ".ttf" else "font/otf"),
        web=OutputFile("web", web.name, "font/woff2"),
        css=OutputFile("css", css.name, "text/css; charset=utf-8"),
        bundle=OutputFile("bundle", bundle.name, "application/zip"),
        flavor=contract.outline_flavor,
        variable=contract.variable,
        color=contract.color,
        checks={
            "source_identity_removed": True,
            "embedding_flags_cleared": True,
            "outline_flavor_retained": True,
            "functional_clone_verified": True,
            "harfbuzz_shaping_verified": True,
            "woff2_roundtrip_verified": True,
        },
    )
