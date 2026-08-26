#!/usr/bin/env python3
"""WOFF2 packager for FontBlind.

This module deliberately treats WOFF2 as a *delivery container*, not as a
replacement outline technology.  A TrueType-flavoured OpenType font remains
TrueType-flavoured inside WOFF2; a CFF/CFF2 OpenType font remains CFF/CFF2.

The safest web output is a full WOFF2 made from an already metadata-neutral
native FontBlind output.  Repertoire subsetting is optional because it changes
the font's coverage and may require table-specific behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from fontTools import subset
from fontTools.ttLib import TTFont, TTLibError

from fontblind_version import PROGRAM_VERSION


class WebBuildError(RuntimeError):
    pass


@dataclass
class NativeSnapshot:
    sfnt_version: str
    tables: list[str]
    glyphs: int
    units_per_em: int
    cmap_entries: int
    outline_flavor: str
    axes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WebBuildReport:
    mode: str
    source: str
    output: str
    bytes: int
    sha256: str
    source_snapshot: NativeSnapshot
    output_snapshot: NativeSnapshot
    requested_codepoints: int = 0
    available_codepoints: int = 0
    missing_codepoints: list[str] = field(default_factory=list)
    css: str | None = None
    warnings: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_native(path: Path) -> TTFont:
    path = Path(path)
    if not path.is_file():
        raise WebBuildError(f"input does not exist: {path}")
    try:
        font = TTFont(str(path), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    except (OSError, TTLibError) as exc:
        raise WebBuildError(f"cannot open font {path}: {exc}") from exc
    if font.flavor is not None:
        font.close()
        raise WebBuildError("provide a standalone .ttf/.otf, not WOFF/WOFF2")
    return font


def _outline_flavor(font: TTFont) -> str:
    if "glyf" in font:
        return "TrueType-glyf"
    if "CFF2" in font:
        return "OpenType-CFF2"
    if "CFF " in font:
        return "OpenType-CFF1"
    if any(tag in font for tag in ("CBDT", "sbix", "EBDT")):
        return "bitmap-or-color-only"
    return "unknown"


def _snapshot(font: TTFont) -> NativeSnapshot:
    axes: list[dict[str, Any]] = []
    if "fvar" in font:
        for axis in font["fvar"].axes:
            axes.append(
                {
                    "tag": str(axis.axisTag),
                    "min": float(axis.minValue),
                    "default": float(axis.defaultValue),
                    "max": float(axis.maxValue),
                }
            )
    cmap = font.getBestCmap() or {}
    return NativeSnapshot(
        sfnt_version=str(font.sfntVersion),
        tables=sorted(tag for tag in font.keys() if tag != "GlyphOrder"),
        glyphs=int(font["maxp"].numGlyphs),
        units_per_em=int(font["head"].unitsPerEm),
        cmap_entries=len(cmap),
        outline_flavor=_outline_flavor(font),
        axes=axes,
    )


def _atomic_target(output: Path, overwrite: bool) -> tuple[Path, Path]:
    output = Path(output)
    if output.exists() and not overwrite:
        raise WebBuildError(f"output already exists: {output} (use --overwrite)")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent))
    os.close(handle)
    return Path(temp_name), output


def _commit(temp: Path, output: Path) -> None:
    os.replace(temp, output)


def _font_name(font: TTFont, name_id: int, fallback: str) -> str:
    if "name" not in font:
        return fallback
    table = font["name"]
    for platform, encoding, language in ((3, 1, 0x409), (3, 10, 0x409), (1, 0, 0)):
        record = table.getName(name_id, platform, encoding, language)
        if record is not None:
            try:
                value = record.toUnicode().strip()
            except Exception:
                continue
            if value:
                return value
    for record in table.names:
        if int(record.nameID) == int(name_id):
            try:
                value = record.toUnicode().strip()
            except Exception:
                continue
            if value:
                return value
    return fallback


def _css_weight(font: TTFont) -> str:
    if "fvar" in font:
        for axis in font["fvar"].axes:
            if str(axis.axisTag) == "wght":
                low = int(round(float(axis.minValue)))
                high = int(round(float(axis.maxValue)))
                return f"{low} {high}" if low != high else str(low)
    if "OS/2" in font:
        return str(max(1, min(1000, int(font["OS/2"].usWeightClass))))
    return "400"


def _css_style(font: TTFont) -> str:
    if "fvar" in font:
        tags = {str(axis.axisTag) for axis in font["fvar"].axes}
        if "ital" in tags or "slnt" in tags:
            return "oblique"
    if "OS/2" in font and int(font["OS/2"].fsSelection) & 0x01:
        return "italic"
    if "head" in font and int(font["head"].macStyle) & 0x02:
        return "italic"
    return "normal"


def _width_class_to_percent(value: int) -> str:
    mapping = {
        1: 50,
        2: 62.5,
        3: 75,
        4: 87.5,
        5: 100,
        6: 112.5,
        7: 125,
        8: 150,
        9: 200,
    }
    number = mapping.get(max(1, min(9, int(value))), 100)
    return f"{number:g}%"


def _css_stretch(font: TTFont) -> str:
    if "fvar" in font:
        for axis in font["fvar"].axes:
            if str(axis.axisTag) == "wdth":
                low = float(axis.minValue)
                high = float(axis.maxValue)
                if low != high:
                    return f"{low:g}% {high:g}%"
                return f"{low:g}%"
    if "OS/2" in font:
        return _width_class_to_percent(int(font["OS/2"].usWidthClass))
    return "100%"


def _unicode_ranges(codepoints: Iterable[int]) -> str:
    values = sorted({int(cp) for cp in codepoints if 0 <= int(cp) <= 0x10FFFF})
    if not values:
        return ""
    ranges: list[tuple[int, int]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append((start, previous))
        start = previous = value
    ranges.append((start, previous))

    def fmt(value: int) -> str:
        width = 4 if value <= 0xFFFF else 6
        return f"U+{value:0{width}X}"

    return ", ".join(fmt(a) if a == b else f"{fmt(a)}-{b:0{4 if b <= 0xFFFF else 6}X}" for a, b in ranges)


def make_css(
    font: TTFont,
    woff2_filename: str,
    *,
    family: str | None = None,
    codepoints: Iterable[int] | None = None,
    font_display: str = "swap",
) -> str:
    family = family or _font_name(font, 1, "Untitled")
    family = family.replace("\\", "\\\\").replace('"', '\\"')
    lines = [
        "@font-face {",
        f'  font-family: "{family}";',
        f'  src: url("{woff2_filename}") format("woff2");',
        f"  font-weight: {_css_weight(font)};",
        f"  font-style: {_css_style(font)};",
        f"  font-stretch: {_css_stretch(font)};",
        f"  font-display: {font_display};",
    ]
    if codepoints is not None:
        ranges = _unicode_ranges(codepoints)
        if ranges:
            lines.append(f"  unicode-range: {ranges};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _validate_full_contract(source: NativeSnapshot, output: NativeSnapshot) -> None:
    checks = {
        "sfnt version": source.sfnt_version == output.sfnt_version,
        "table set": source.tables == output.tables,
        "glyph count": source.glyphs == output.glyphs,
        "units per em": source.units_per_em == output.units_per_em,
        "cmap entry count": source.cmap_entries == output.cmap_entries,
        "outline flavor": source.outline_flavor == output.outline_flavor,
        "variation axes": source.axes == output.axes,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise WebBuildError("WOFF2 full-package contract failed: " + ", ".join(failed))


def build_full_woff2(
    source: Path,
    output: Path,
    *,
    overwrite: bool = False,
    css_output: Path | None = None,
    css_family: str | None = None,
    font_display: str = "swap",
) -> WebBuildReport:
    source = Path(source)
    font = _open_native(source)
    temp, output = _atomic_target(Path(output), overwrite)
    try:
        before = _snapshot(font)
        css = make_css(font, output.name, family=css_family, font_display=font_display)
        font.flavor = "woff2"
        font.save(str(temp), reorderTables=True)
        font.close()
        decoded = TTFont(str(temp), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
        try:
            after = _snapshot(decoded)
        finally:
            decoded.close()
        _validate_full_contract(before, after)
        _commit(temp, output)
    except Exception:
        temp.unlink(missing_ok=True)
        try:
            font.close()
        except Exception:
            pass
        raise

    if css_output is not None:
        css_path = Path(css_output)
        css_path.parent.mkdir(parents=True, exist_ok=True)
        css_path.write_text(css, encoding="utf-8")
    return WebBuildReport(
        mode="full",
        source=str(source),
        output=str(output),
        bytes=output.stat().st_size,
        sha256=sha256_file(output),
        source_snapshot=before,
        output_snapshot=after,
        css=css,
    )


UNICODE_ITEM = re.compile(r"^(?:U\+)?([0-9A-Fa-f]{1,6})(?:-(?:U\+)?([0-9A-Fa-f]{1,6}))?$")


def parse_unicode_spec(values: Sequence[str]) -> set[int]:
    result: set[int] = set()
    for raw in values:
        for item in re.split(r"[,\s]+", raw.strip()):
            if not item:
                continue
            match = UNICODE_ITEM.match(item)
            if not match:
                raise WebBuildError(f"invalid Unicode item: {item!r}")
            start = int(match.group(1), 16)
            end = int(match.group(2), 16) if match.group(2) else start
            if start > end or end > 0x10FFFF:
                raise WebBuildError(f"invalid Unicode range: {item!r}")
            result.update(range(start, end + 1))
    return result


def build_subset_woff2(
    source: Path,
    output: Path,
    *,
    text: str = "",
    unicodes: Iterable[int] = (),
    overwrite: bool = False,
    keep_hinting: bool = True,
    retain_gids: bool = False,
    css_output: Path | None = None,
    css_family: str | None = None,
    font_display: str = "swap",
) -> WebBuildReport:
    source = Path(source)
    requested = {ord(character) for character in text}
    requested.update(int(cp) for cp in unicodes)
    if not requested:
        raise WebBuildError("subset mode requires --text, --text-file, or --unicodes")

    original = _open_native(source)
    try:
        before = _snapshot(original)
        source_cmap = original.getBestCmap() or {}
        available = requested & set(source_cmap)
        missing = requested - set(source_cmap)
        name_ids = sorted({int(record.nameID) for record in original["name"].names}) if "name" in original else []
        languages = sorted({int(record.langID) for record in original["name"].names}) if "name" in original else []
    finally:
        original.close()
    if not available:
        raise WebBuildError("none of the requested Unicode codepoints are present in the source font")

    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.layout_closure = True
    options.recommended_glyphs = True
    options.notdef_glyph = True
    options.notdef_outline = True
    options.hinting = bool(keep_hinting)
    options.retain_gids = bool(retain_gids)
    options.recalc_bounds = False
    options.recalc_timestamp = False
    options.canonical_order = True
    options.name_legacy = True
    if name_ids:
        options.name_IDs = name_ids
    if languages:
        options.name_languages = languages

    font = subset.load_font(str(source), options, lazy=False)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=available)
    subsetter.subset(font)

    temp, output = _atomic_target(Path(output), overwrite)
    try:
        subset.save_font(font, str(temp), options)
        font.close()
        decoded = TTFont(str(temp), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
        try:
            after = _snapshot(decoded)
            output_cmap = decoded.getBestCmap() or {}
            absent_after = available - set(output_cmap)
            if absent_after:
                raise WebBuildError(
                    "subset output lost requested codepoints: "
                    + ", ".join(f"U+{cp:04X}" for cp in sorted(absent_after)[:64])
                )
            css = make_css(
                decoded,
                output.name,
                family=css_family,
                codepoints=available,
                font_display=font_display,
            )
        finally:
            decoded.close()
        _commit(temp, output)
    except Exception:
        temp.unlink(missing_ok=True)
        try:
            font.close()
        except Exception:
            pass
        raise

    if css_output is not None:
        css_path = Path(css_output)
        css_path.parent.mkdir(parents=True, exist_ok=True)
        css_path.write_text(css, encoding="utf-8")
    warnings: list[str] = []
    if not keep_hinting:
        warnings.append("hinting was removed; raster output can differ at small pixel sizes")
    if missing:
        warnings.append(f"{len(missing)} requested codepoints were not present in the source")
    return WebBuildReport(
        mode="subset",
        source=str(source),
        output=str(output),
        bytes=output.stat().st_size,
        sha256=sha256_file(output),
        source_snapshot=before,
        output_snapshot=after,
        requested_codepoints=len(requested),
        available_codepoints=len(available),
        missing_codepoints=[f"U+{cp:04X}" for cp in sorted(missing)],
        css=css,
        warnings=warnings,
    )


def _print_report(report: WebBuildReport, report_path: str | None) -> None:
    data = asdict(report)
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fontblind-web",
        description="Package an already metadata-neutral native font as full or subset WOFF2.",
    )
    parser.add_argument("--version", action="version", version=f"fontblind-web {PROGRAM_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    full = sub.add_parser("full", help="losslessly package the complete native font as WOFF2")
    full.add_argument("input")
    full.add_argument("output")
    full.add_argument("--overwrite", action="store_true")
    full.add_argument("--css-output")
    full.add_argument("--css-family")
    full.add_argument("--font-display", default="swap", choices=["auto", "block", "swap", "fallback", "optional"])
    full.add_argument("--report")

    reduced = sub.add_parser("subset", help="create a repertoire-subset WOFF2 with layout closure")
    reduced.add_argument("input")
    reduced.add_argument("output")
    reduced.add_argument("--text", action="append", default=[])
    reduced.add_argument("--text-file", action="append", default=[])
    reduced.add_argument("--unicodes", action="append", default=[], metavar="U+0020-007E,...")
    reduced.add_argument("--drop-hinting", action="store_true")
    reduced.add_argument("--retain-gids", action="store_true")
    reduced.add_argument("--overwrite", action="store_true")
    reduced.add_argument("--css-output")
    reduced.add_argument("--css-family")
    reduced.add_argument("--font-display", default="swap", choices=["auto", "block", "swap", "fallback", "optional"])
    reduced.add_argument("--report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "full":
            report = build_full_woff2(
                Path(args.input),
                Path(args.output),
                overwrite=args.overwrite,
                css_output=Path(args.css_output) if args.css_output else None,
                css_family=args.css_family,
                font_display=args.font_display,
            )
        else:
            text_parts = list(args.text)
            for filename in args.text_file:
                text_parts.append(Path(filename).read_text(encoding="utf-8"))
            report = build_subset_woff2(
                Path(args.input),
                Path(args.output),
                text="\n".join(text_parts),
                unicodes=parse_unicode_spec(args.unicodes),
                overwrite=args.overwrite,
                keep_hinting=not args.drop_hinting,
                retain_gids=args.retain_gids,
                css_output=Path(args.css_output) if args.css_output else None,
                css_family=args.css_family,
                font_display=args.font_display,
            )
        _print_report(report, args.report)
        return 0
    except (WebBuildError, OSError, ValueError, TTLibError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
