#!/usr/bin/env python3
"""fontblind: recreate an OpenType/TrueType font without identity metadata.

The output preserves the source font's glyph IDs, outlines, hinting, metrics,
kerning, OpenType/AAT/Graphite layout, variations, and color data. It does not
rebuild outlines. Instead, it writes a new SFNT file while keeping functional
font tables unchanged and replacing or removing metadata-bearing structures.

Supported inputs: standalone .ttf and .otf files (TrueType, CFF1, or CFF2).
Collections (.ttc/.otc), WOFF/WOFF2 containers, and synthetic CFF FontSets are
not accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from fontTools.cffLib import cffStandardStrings
from fontTools.ttLib import TTFont, TTLibError, newTable

PROGRAM = "fontblind"
PROGRAM_VERSION = "3.1.0"
GENERIC_FAMILY = "Untitled"
GENERIC_STYLE = "Regular"
GENERIC_FULL = f"{GENERIC_FAMILY} {GENERIC_STYLE}"
GENERIC_PS = f"{GENERIC_FAMILY}-{GENERIC_STYLE}"
GENERIC_VERSION = "Version 1.000"
FIXED_SFNT_TIME = 2082844800  # 1970-01-01 in seconds since 1904-01-01.

GENERIC_WEIGHT_STYLES = (
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
GENERIC_WIDTH_STYLES = (
    "",
    "UltraCondensed",
    "ExtraCondensed",
    "Condensed",
    "SemiCondensed",
    "SemiExpanded",
    "Expanded",
    "ExtraExpanded",
    "UltraExpanded",
)
GENERIC_SLOPE_STYLES = ("", "Italic", "Oblique")
GENERIC_STYLES = frozenset(
    " ".join(part for part in (width, "" if weight == "Regular" else weight, slope) if part) or "Regular"
    for width in GENERIC_WIDTH_STYLES
    for weight in GENERIC_WEIGHT_STYLES
    for slope in GENERIC_SLOPE_STYLES
)

# Optional/source/debug/signature tables that do not participate in text
# rendering and may directly contain editor source or identifying metadata.
REMOVE_TABLES = frozenset(
    {
        "DSIG",  # invalid after any byte changes
        "FFTM",  # FontForge timestamps/version
        "meta",  # OpenType metadata table
        "META",  # legacy/SING metadata table
        "EPAR",  # licensing permissions/restrictions metadata
        "PCLT",  # PCL typeface/manufacturer/file-name data
        "BDF ",  # BDF properties
        "PfEd",  # FontForge private editor data
        "Silt",  # SIL TypeTuner/source metadata (not the runtime Graphite tables)
        "Debg",  # debug data
        "TTFA",  # ttfautohint invocation parameters/build provenance
        "SING",  # Adobe glyphlet identity/unique-name metadata
        "fdsc",  # AAT font-family substitution descriptors
        "fond",  # legacy family/resource names and PostScript mappings
        "FOND",  # nonstandard uppercase spelling seen in older tooling
        "xref",  # Apple tool symbolic source names
        "Zapf",  # glyph identifiers, notes, and reference metadata
        "TSIB",  # VTT BASE source text
        "TSI0",
        "TSI1",
        "TSI2",
        "TSI3",
        "TSI5",
        "TSIC",
        "TSID",
        "TSIJ",
        "TSIP",
        "TSIS",
        "TSIV",
    }
)

# Tables allowed to change while preserving rendering. Every other retained
# table is required to remain byte-for-byte identical.
MUTABLE_TABLES = frozenset(
    {
        "head",
        "OS/2",
        "name",
        "post",
        "CFF ",
        "cmap",  # may be reserialized when CFF1 glyph names are neutralized
        "SVG ",
        "sbix",
        "CBDT",
        "CBLC",
    }
)

CFF_METADATA_STRINGS = {
    "version": "1.000",
    "Notice": "Metadata removed",
    "Copyright": "Metadata removed",
    "FullName": GENERIC_FULL,
    "FamilyName": GENERIC_FAMILY,
    "Weight": GENERIC_STYLE,
    "FontName": GENERIC_PS,
}
CFF_ID_FIELDS = ("UniqueID", "XUID", "UIDBase", "CIDFontVersion", "CIDFontRevision", "CIDFontType")
CFF_UNSUPPORTED_SYNTHETIC_FIELDS = ("SyntheticBase", "PostScript", "BaseFontName", "BaseFontBlend")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_METADATA_CHUNKS = frozenset({b"tEXt", b"zTXt", b"iTXt", b"tIME", b"eXIf"})
SVG_METADATA_LOCAL_NAMES = frozenset({"metadata", "title", "desc"})

# Name IDs that can identify the font, its provenance, its version, or its license.
# Style, axis, feature, and palette labels are replaced too, but are excluded
# from raw leakage tokenization because they are usually generic words.
IDENTITY_NAME_IDS = frozenset(
    {0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 21, 25}
)

COMMON_NONIDENTIFYING = frozenset(
    {
        "regular",
        "roman",
        "normal",
        "bold",
        "italic",
        "oblique",
        "medium",
        "light",
        "black",
        "thin",
        "book",
        "semibold",
        "demibold",
        "extrabold",
        "weight",
        "width",
        "slant",
        "optical size",
        "version",
        "copyright",
        "reserved",
        "font",
        "typeface",
        "opentype",
        "truetype",
        "metadata",
        "removed",
        "none",
        "unknown",
    }
)
GENERIC_OUTPUT_STRINGS = frozenset(
    value.casefold()
    for style in GENERIC_STYLES
    for value in (
        style,
        f"{GENERIC_FAMILY} {style}",
        f"{GENERIC_FAMILY}-{style.replace(' ', '')}",
        f"{GENERIC_FAMILY}-{style.replace(' ', '')};1.000;Universal",
    )
) | frozenset(
    {
        GENERIC_FAMILY.casefold(),
        GENERIC_STYLE.casefold(),
        GENERIC_FULL.casefold(),
        GENERIC_PS.casefold(),
        GENERIC_VERSION.casefold(),
        f"{GENERIC_PS};1.000;Universal".casefold(),
        "metadata removed",
        "anonymous",
        "about:blank",
        "aa",
        "light background",
        "dark background",
        "1.000",
    }
)



class FontBlindError(RuntimeError):
    """A user-facing fontblind failure."""


@dataclass(frozen=True)
class GenericNames:
    family: str
    style: str
    full: str
    postscript: str


@dataclass
class ScrubStats:
    removed_tables: list[str] = field(default_factory=list)
    name_ids_replaced: int = 0
    cff_glyph_names_replaced: int = 0
    svg_documents_scrubbed: int = 0
    bitmap_images_scrubbed: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class EquivalenceReport:
    ok: bool
    changed_tables: list[str]
    removed_tables: list[str]
    unexpected_changed_tables: list[str]
    checks: dict[str, bool]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditReport:
    ok: bool
    tables: list[str]
    names: dict[int, list[str]]
    forbidden_tables_found: list[str]
    metadata_state_ok: bool
    source_identity_tokens_checked: int = 0
    source_identity_tokens_found: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _open_font(path: Path, *, lazy: bool = True) -> TTFont:
    try:
        stream = Path(path).open("rb")
        stream.seek(0)
    except OSError as exc:
        raise FontBlindError(f"could not open font: {exc}") from exc
    try:
        font = TTFont(
            stream,
            lazy=lazy,
            recalcBBoxes=False,
            recalcTimestamp=False,
            ignoreDecompileErrors=False,
        )
    except TTLibError as exc:
        stream.close()
        message = str(exc)
        if "collection" in message.lower():
            raise FontBlindError("font collections (.ttc/.otc) are not supported; provide one standalone TTF/OTF") from exc
        raise FontBlindError(f"could not open font: {message}") from exc
    except Exception:
        stream.close()
        raise
    if font.flavor is not None:
        font.close()
        raise FontBlindError("WOFF/WOFF2 containers are not supported; provide a standalone .ttf or .otf")
    if "head" not in font or "maxp" not in font:
        font.close()
        raise FontBlindError("input is not a supported standalone OpenType/TrueType font")
    if not any(tag in font for tag in ("glyf", "CFF ", "CFF2", "CBDT", "sbix")):
        font.close()
        raise FontBlindError("font has no supported outline or bitmap glyph data")
    return font


def _safe_name_ids(font: TTFont) -> set[int]:
    ids = {0, 1, 2, 3, 4, 5, 6}
    if "name" not in font:
        return ids
    try:
        for record in font["name"].names:
            if 0 <= int(record.nameID) <= 0xFFFF:
                ids.add(int(record.nameID))
    except Exception as exc:
        raise FontBlindError(f"could not read the source name table: {exc}") from exc
    return ids


def _axis_default(font: TTFont, tag: str) -> float | None:
    if "fvar" not in font:
        return None
    for axis in font["fvar"].axes:
        if str(axis.axisTag) == tag:
            return float(axis.defaultValue)
    return None


def _generic_names(font: TTFont) -> GenericNames:
    weight_value = _axis_default(font, "wght")
    if weight_value is None and "OS/2" in font:
        weight_value = float(font["OS/2"].usWeightClass)
    weight_value = 400.0 if weight_value is None else weight_value
    weight_index = max(0, min(8, int((weight_value + 50) // 100) - 1))
    weight = GENERIC_WEIGHT_STYLES[weight_index]

    width_class = int(font["OS/2"].usWidthClass) if "OS/2" in font else 5
    width_by_class = {
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
    width = width_by_class.get(max(1, min(9, width_class)), "")

    italic_default = _axis_default(font, "ital")
    slant_default = _axis_default(font, "slnt")
    selection = int(font["OS/2"].fsSelection) if "OS/2" in font else 0
    mac_style = int(font["head"].macStyle) if "head" in font else 0
    if (italic_default is not None and italic_default >= 0.5) or selection & 0x01 or mac_style & 0x02:
        slope = "Italic"
    elif (slant_default is not None and abs(slant_default) > 1e-9) or selection & 0x200:
        slope = "Oblique"
    else:
        slope = ""

    style = " ".join(part for part in (width, "" if weight == "Regular" else weight, slope) if part) or "Regular"
    postscript_style = style.replace(" ", "")
    return GenericNames(
        family=GENERIC_FAMILY,
        style=style,
        full=f"{GENERIC_FAMILY} {style}",
        postscript=f"{GENERIC_FAMILY}-{postscript_style}",
    )


def _generic_axis_label(tag: str) -> str:
    registered = {
        "ital": "Italic",
        "opsz": "Optical Size",
        "slnt": "Slant",
        "wdth": "Width",
        "wght": "Weight",
    }
    if tag in registered:
        return registered[tag]
    safe = "".join(character if character.isalnum() else "_" for character in tag)[:4]
    return f"Axis {safe or 'Custom'}"


def _generic_name_overrides(font: TTFont, names: GenericNames) -> dict[int, str]:
    """Build neutral functional labels for variable-font name references."""
    overrides: dict[int, str] = {}
    if "fvar" in font:
        for axis in font["fvar"].axes:
            name_id = int(axis.axisNameID)
            if name_id > 25:
                overrides[name_id] = _generic_axis_label(str(axis.axisTag))
        for index, instance in enumerate(font["fvar"].instances, start=1):
            subfamily_id = int(instance.subfamilyNameID)
            if subfamily_id > 25:
                overrides[subfamily_id] = f"Instance {index:02d}"
            postscript_id = int(getattr(instance, "postscriptNameID", 0xFFFF))
            if 25 < postscript_id < 0xFFFF:
                overrides[postscript_id] = f"{names.postscript}-Instance{index:02d}"

    if "STAT" in font:
        table = font["STAT"].table
        design_axes = getattr(getattr(table, "DesignAxisRecord", None), "Axis", ()) or ()
        for axis in design_axes:
            name_id = int(axis.AxisNameID)
            if name_id > 25:
                overrides[name_id] = _generic_axis_label(str(axis.AxisTag))
        axis_values = getattr(getattr(table, "AxisValueArray", None), "AxisValue", ()) or ()
        for index, value in enumerate(axis_values, start=1):
            name_id = int(value.ValueNameID)
            if name_id > 25:
                overrides[name_id] = f"Value {index:02d}"
    return overrides


def _generic_name(name_id: int, names: GenericNames | None = None) -> str:
    names = names or GenericNames(GENERIC_FAMILY, GENERIC_STYLE, GENERIC_FULL, GENERIC_PS)
    fixed = {
        0: "Metadata removed",
        1: names.family,
        2: names.style,
        3: f"{names.postscript};1.000;Universal",
        4: names.full,
        5: GENERIC_VERSION,
        6: names.postscript,
        7: "Metadata removed",
        8: "Anonymous",
        9: "Anonymous",
        10: "Metadata removed",
        11: "about:blank",
        12: "about:blank",
        13: "Metadata removed",
        14: "about:blank",
        15: "Metadata removed",
        16: names.family,
        17: names.style,
        18: names.full,
        19: "Aa",
        20: names.postscript,
        21: names.family,
        22: names.style,
        23: "Light background",
        24: "Dark background",
        25: names.family,
    }
    return fixed.get(name_id, f"Label-{name_id:05d}")


def _replace_name_table(font: TTFont, stats: ScrubStats) -> None:
    name_ids = _safe_name_ids(font)
    names = _generic_names(font)
    overrides = _generic_name_overrides(font, names)
    name_ids.update(overrides)
    table = newTable("name")
    table.names = []
    for name_id in sorted(name_ids):
        value = overrides.get(name_id, _generic_name(name_id, names))
        # Windows Unicode BMP, US English. All generated values are ASCII.
        table.setName(value, name_id, 3, 1, 0x0409)
        # Mac Roman records improve compatibility with older consumers.
        if name_id <= 25:
            table.setName(value, name_id, 1, 0, 0)
    font["name"] = table
    stats.name_ids_replaced = len(name_ids)
    if "ltag" in font:
        del font["ltag"]
        stats.removed_tables.append("ltag")


def _normalize_head(font: TTFont) -> None:
    head = font["head"]
    head.fontRevision = 1.0
    head.created = FIXED_SFNT_TIME
    head.modified = FIXED_SFNT_TIME


def _normalize_os2(font: TTFont) -> None:
    if "OS/2" not in font:
        return
    os2 = font["OS/2"]
    os2.achVendID = "NONE"
    # Remove embedding-permission metadata. This does not grant any rights;
    # it only prevents the bit field becoming a training shortcut.
    os2.fsType = 0
    # These are descriptive classification fingerprints, not drawing or
    # layout data. Keep weight/width/selection and typographic metrics intact.
    if hasattr(os2, "sFamilyClass"):
        os2.sFamilyClass = 0
    panose = getattr(os2, "panose", None)
    if panose is not None:
        for field_name in vars(panose):
            if field_name != "tableTag":
                setattr(panose, field_name, 0)


def _normalize_post(font: TTFont) -> None:
    if "post" not in font:
        return
    post = font["post"]
    post.formatType = 3.0
    if hasattr(post, "extraNames"):
        post.extraNames = []
    if hasattr(post, "mapping"):
        post.mapping = {}


def _cff_explicit(obj: Any, attr: str) -> bool:
    raw = getattr(obj, "rawDict", None)
    return attr in vars(obj) or (isinstance(raw, dict) and attr in raw)


def _cff_drop(obj: Any, attr: str) -> None:
    raw = getattr(obj, "rawDict", None)
    if isinstance(raw, dict):
        raw.pop(attr, None)
    if attr in vars(obj):
        delattr(obj, attr)


def _next_generic_glyph_name(gid: int, used: set[str]) -> str:
    base = f"_fb{gid:05d}"
    candidate = base
    serial = 1
    while candidate in used:
        candidate = f"{base}_{serial}"
        serial += 1
    used.add(candidate)
    return candidate


def _sanitize_cff1(font: TTFont, stats: ScrubStats) -> None:
    if "CFF " not in font:
        return
    cff = font["CFF "].cff
    if len(cff.topDictIndex) != 1:
        raise FontBlindError("multi-font CFF FontSets are not supported inside a standalone OTF")
    names = _generic_names(font)
    cff_metadata_strings = {
        "version": "1.000",
        "Notice": "Metadata removed",
        "Copyright": "Metadata removed",
        "FullName": names.full,
        "FamilyName": names.family,
        "Weight": names.style,
        "FontName": names.postscript,
    }
    cff.fontNames = [names.postscript]

    top = cff.topDictIndex[0]
    for attr in CFF_UNSUPPORTED_SYNTHETIC_FIELDS:
        if _cff_explicit(top, attr):
            raise FontBlindError(
                f"CFF field {attr!r} can affect a synthetic/base font relationship; refusing to strip it and change rendering"
            )

    charset = list(top.charset)
    is_cid = _cff_explicit(top, "ROS") or hasattr(top, "ROS")
    if not is_cid:
        encoding = getattr(top, "Encoding", None)
        # Standard CFF names are format-defined and carry no source identity.
        # Custom encoding entries can be renamed because the code-to-GID
        # mapping is rebuilt below.
        protected = set(cffStandardStrings)

        used = {name for name in charset if name in protected}
        mapping: dict[str, str] = {}
        replaced = 0
        for gid, old_name in enumerate(charset):
            if gid == 0:
                new_name = ".notdef"
            elif old_name in protected:
                new_name = old_name
            else:
                new_name = _next_generic_glyph_name(gid, used)
                if new_name != old_name:
                    replaced += 1
            mapping[old_name] = new_name

        char_strings = top.CharStrings
        old_dict = char_strings.charStrings
        char_strings.charStrings = {mapping[name]: old_dict[name] for name in charset}
        top.charset = [mapping[name] for name in charset]
        if isinstance(encoding, list):
            top.Encoding = [mapping.get(name, ".notdef") for name in encoding]
        stats.cff_glyph_names_replaced += replaced

    for attr, value in cff_metadata_strings.items():
        if _cff_explicit(top, attr):
            setattr(top, attr, value)
    for attr in CFF_ID_FIELDS:
        _cff_drop(top, attr)

    if _cff_explicit(top, "FDArray") or hasattr(top, "FDArray"):
        try:
            fd_array = top.FDArray
        except AttributeError:
            fd_array = None
        if fd_array is not None:
            for index, font_dict in enumerate(fd_array):
                if font_dict is None:
                    continue
                if _cff_explicit(font_dict, "FontName"):
                    setattr(font_dict, "FontName", f"{names.postscript}-FD{index:03d}")
                for attr, value in cff_metadata_strings.items():
                    if attr != "FontName" and _cff_explicit(font_dict, attr):
                        setattr(font_dict, attr, value)
                for attr in CFF_ID_FIELDS:
                    _cff_drop(font_dict, attr)


def _strip_png_metadata(data: bytes) -> tuple[bytes, bool]:
    if not data.startswith(PNG_SIGNATURE):
        return data, False
    out = bytearray(PNG_SIGNATURE)
    pos = len(PNG_SIGNATURE)
    changed = False
    saw_iend = False
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        end = pos + 12 + length
        if end > len(data):
            return data, False
        chunk_type = data[pos + 4 : pos + 8]
        chunk = data[pos:end]
        if chunk_type in PNG_METADATA_CHUNKS:
            changed = True
        else:
            out.extend(chunk)
        pos = end
        if chunk_type == b"IEND":
            saw_iend = True
            break
    if not saw_iend or pos != len(data):
        return data, False
    return bytes(out), changed


def _strip_jpeg_metadata(data: bytes) -> tuple[bytes, bool]:
    """Remove non-rendering JPEG identity/provenance segments losslessly.

    The entropy-coded image stream is copied byte-for-byte. The parser also
    handles metadata markers between scans in progressive/multi-scan JPEGs.
    JFIF (APP0), ICC profiles (APP2), and Adobe color-transform data (APP14)
    are retained because removing them can change color interpretation.
    """
    if not data.startswith(b"\xFF\xD8"):
        return data, False

    # APP1: Exif/XMP and other application metadata.
    # APP12: Ducky/other editor metadata. APP13: Photoshop/IPTC metadata.
    # COM: free-form comments.
    removable = {0xE1, 0xEC, 0xED, 0xFE}
    out = bytearray(data[:2])
    pos = 2
    changed = False

    while pos < len(data):
        if data[pos] != 0xFF:
            return data, False
        marker_start = pos
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            return data, False
        marker = data[pos]
        pos += 1

        # Standalone markers carry no length. Drop trailing bytes after EOI;
        # they are outside the rendered JPEG image and can carry metadata.
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            out.extend(data[marker_start:pos])
            if marker == 0xD9:
                return bytes(out), changed or pos != len(data)
            continue

        if pos + 2 > len(data):
            return data, False
        segment_length = struct.unpack(">H", data[pos : pos + 2])[0]
        if segment_length < 2 or pos + segment_length > len(data):
            return data, False
        segment_end = pos + segment_length

        if marker in removable:
            changed = True
        else:
            out.extend(data[marker_start:segment_end])
        pos = segment_end

        if marker != 0xDA:  # Start of scan.
            continue

        # Copy entropy-coded data until an unstuffed, non-restart marker. That
        # marker is parsed by the outer loop, which lets us remove metadata
        # segments legally placed between scans.
        while pos < len(data):
            byte = data[pos]
            if byte != 0xFF:
                out.append(byte)
                pos += 1
                continue

            run_start = pos
            while pos < len(data) and data[pos] == 0xFF:
                pos += 1
            if pos >= len(data):
                return data, False
            following = data[pos]

            if following == 0x00 or 0xD0 <= following <= 0xD7:
                # Byte-stuffed 0xFF or a restart marker: both are part of the
                # entropy stream and must remain byte-identical.
                out.extend(data[run_start : pos + 1])
                pos += 1
                continue

            # Leave the marker for the outer parser. Any fill 0xFF bytes are
            # canonicalized to the marker run already present in the source.
            pos = run_start
            break

    return data, False


def _strip_image_metadata(data: bytes, graphic_type: str | None = None) -> tuple[bytes, bool]:
    kind = (graphic_type or "").strip().lower()
    if data.startswith(PNG_SIGNATURE) or kind == "png":
        return _strip_png_metadata(data)
    if data.startswith(b"\xFF\xD8") or kind in {"jpg", "jpeg"}:
        return _strip_jpeg_metadata(data)
    return data, False


def _local_xml_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _strip_svg_metadata(svg: str) -> tuple[str, bool]:
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        root = ET.fromstring(svg.encode("utf-8"), parser=parser)
    except (ET.ParseError, ValueError):
        return svg, False

    changed = False
    for parent in list(root.iter()):
        for child in list(parent):
            local = _local_xml_name(child.tag)
            if child.tag is ET.Comment or child.tag is ET.ProcessingInstruction or local in SVG_METADATA_LOCAL_NAMES:
                parent.remove(child)
                changed = True
    if not changed:
        return svg, False
    cleaned = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return cleaned, True


def _scrub_embedded_assets(font: TTFont, stats: ScrubStats) -> None:
    if "SVG " in font:
        table = font["SVG "]
        for doc in table.docList:
            cleaned, changed = _strip_svg_metadata(doc.data)
            if changed:
                doc.data = cleaned
                stats.svg_documents_scrubbed += 1

    if "sbix" in font:
        table = font["sbix"]
        for strike in table.strikes.values():
            for glyph in strike.glyphs.values():
                if glyph.is_reference_type() or glyph.imageData is None:
                    continue
                cleaned, changed = _strip_image_metadata(glyph.imageData, glyph.graphicType)
                if changed:
                    glyph.imageData = cleaned
                    stats.bitmap_images_scrubbed += 1

    if "CBDT" in font:
        table = font["CBDT"]
        for strike in table.strikeData:
            for glyph in strike.values():
                try:
                    image = glyph.imageData
                except AttributeError:
                    continue
                cleaned, changed = _strip_image_metadata(image, "png")
                if changed:
                    glyph.imageData = cleaned
                    stats.bitmap_images_scrubbed += 1


def _remove_metadata_tables(font: TTFont, stats: ScrubStats) -> None:
    for tag in sorted(REMOVE_TABLES):
        if tag in font:
            del font[tag]
            stats.removed_tables.append(tag)


def _sanitize_font(font: TTFont) -> ScrubStats:
    stats = ScrubStats()
    _replace_name_table(font, stats)
    _normalize_head(font)
    _normalize_os2(font)
    _normalize_post(font)
    _sanitize_cff1(font, stats)
    _scrub_embedded_assets(font, stats)
    _remove_metadata_tables(font, stats)
    stats.removed_tables = sorted(set(stats.removed_tables))
    return stats


def _table_data(font: TTFont, tag: str) -> bytes:
    if font.reader is None:
        return font.getTableData(tag)
    return font.reader[tag]


def _table_tags(font: TTFont) -> set[str]:
    if font.reader is not None:
        return set(font.reader.keys())
    return {tag for tag in font.keys() if tag != "GlyphOrder"}


def _cmap_snapshot(font: TTFont) -> tuple[Any, ...]:
    if "cmap" not in font:
        return ()
    rows: list[Any] = []
    for subtable in font["cmap"].tables:
        cmap_rows: tuple[tuple[int, int], ...] | None = None
        if hasattr(subtable, "cmap"):
            cmap_rows = tuple(
                sorted((int(cp), int(font.getGlyphID(name))) for cp, name in subtable.cmap.items())
            )
        uvs_rows: tuple[Any, ...] | None = None
        if hasattr(subtable, "uvsDict"):
            uvs_rows = tuple(
                (int(selector), tuple((int(cp), None if name is None else int(font.getGlyphID(name))) for cp, name in entries))
                for selector, entries in sorted(subtable.uvsDict.items())
            )
        rows.append(
            (
                int(subtable.platformID),
                int(subtable.platEncID),
                int(subtable.format),
                int(getattr(subtable, "language", 0) or 0),
                cmap_rows,
                uvs_rows,
            )
        )
    return tuple(rows)


def _head_snapshot(font: TTFont) -> dict[str, Any]:
    head = font["head"]
    excluded = {"tableTag", "checkSumAdjustment", "fontRevision", "created", "modified"}
    return {key: value for key, value in vars(head).items() if key not in excluded}


def _os2_snapshot(font: TTFont) -> dict[str, Any] | None:
    if "OS/2" not in font:
        return None
    os2 = font["OS/2"]
    # FontTools canonically recomputes usFirst/LastCharIndex from cmap when
    # compiling OS/2. They are descriptive coverage metadata, not drawing,
    # metrics, or shaping data, so compare all other functional fields.
    excluded = {
        "tableTag",
        "achVendID",
        "fsType",
        "sFamilyClass",
        "panose",
        "usFirstCharIndex",
        "usLastCharIndex",
    }
    result: dict[str, Any] = {}
    for key, value in vars(os2).items():
        if key in excluded:
            continue
        if hasattr(value, "__dict__"):
            result[key] = tuple(sorted(vars(value).items()))
        else:
            result[key] = value
    return result


def _post_header_snapshot(font: TTFont) -> dict[str, Any] | None:
    if "post" not in font:
        return None
    post = font["post"]
    fields = (
        "italicAngle",
        "underlinePosition",
        "underlineThickness",
        "isFixedPitch",
        "minMemType42",
        "maxMemType42",
        "minMemType1",
        "maxMemType1",
    )
    return {field: getattr(post, field, None) for field in fields}


def _cff_private_snapshot(private: Any) -> tuple[Any, ...] | None:
    if private is None:
        return None
    rows = []
    for name in getattr(private, "order", ()):
        if name == "Subrs":
            continue
        value = getattr(private, name, None)
        if value is not None:
            if isinstance(value, list):
                value = tuple(tuple(v) if isinstance(v, list) else v for v in value)
            rows.append((name, value))
    subrs = getattr(private, "Subrs", None)
    subr_bytes = tuple(cs.bytecode for cs in subrs) if subrs is not None else ()
    return tuple(rows), subr_bytes


def _cff_encoding_snapshot(top: Any) -> Any:
    encoding = getattr(top, "Encoding", None)
    if isinstance(encoding, str):
        return ("predefined", encoding)
    if isinstance(encoding, list):
        gid_by_name = {name: gid for gid, name in enumerate(top.charset)}
        return ("custom", tuple(gid_by_name.get(name, 0) for name in encoding))
    return None


def _cff_snapshot(font: TTFont) -> Any:
    if "CFF " not in font:
        return None
    cff = font["CFF "].cff
    tops = []
    for top in cff.topDictIndex:
        metadata_fields = set(CFF_METADATA_STRINGS) | set(CFF_ID_FIELDS) | set(CFF_UNSUPPORTED_SYNTHETIC_FIELDS)
        structural_fields = {"charset", "Encoding", "CharStrings", "Private", "FDArray", "FDSelect"}
        top_values = []
        for name in getattr(top, "order", ()):
            if name in metadata_fields or name in structural_fields:
                continue
            value = getattr(top, name, None)
            if value is not None:
                if isinstance(value, list):
                    value = tuple(tuple(v) if isinstance(v, list) else v for v in value)
                top_values.append((name, value))

        charstring_bytes = tuple(top.CharStrings[name].bytecode for name in top.charset)
        selector_rows = []
        if hasattr(top, "FDSelect"):
            selector_rows = tuple(int(value) for value in top.FDSelect)

        fd_rows = []
        if hasattr(top, "FDArray"):
            for fd in top.FDArray:
                fd_values = []
                for name in getattr(fd, "order", ()):
                    if name in metadata_fields or name in {"Private"}:
                        continue
                    value = getattr(fd, name, None)
                    if value is not None:
                        if isinstance(value, list):
                            value = tuple(tuple(v) if isinstance(v, list) else v for v in value)
                        fd_values.append((name, value))
                fd_rows.append((tuple(fd_values), _cff_private_snapshot(getattr(fd, "Private", None))))

        is_cid = hasattr(top, "ROS")
        charset_semantics = tuple(top.charset) if is_cid else len(top.charset)
        tops.append(
            (
                tuple(top_values),
                charset_semantics,
                _cff_encoding_snapshot(top),
                charstring_bytes,
                _cff_private_snapshot(getattr(top, "Private", None)),
                selector_rows,
                tuple(fd_rows),
            )
        )
    global_subrs = tuple(cs.bytecode for cs in cff.GlobalSubrs)
    return tuple(tops), global_subrs


def _embedded_asset_snapshot(font: TTFont) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "SVG " in font:
        result["SVG "] = tuple(
            (
                int(doc.startGlyphID),
                int(doc.endGlyphID),
                bool(doc.compressed),
                _strip_svg_metadata(doc.data)[0],
            )
            for doc in font["SVG "].docList
        )
    if "sbix" in font:
        strikes = []
        for ppem, strike in sorted(font["sbix"].strikes.items()):
            glyphs = []
            for name, glyph in sorted(strike.glyphs.items(), key=lambda item: font.getGlyphID(item[0])):
                if glyph.is_reference_type():
                    payload = ("ref", font.getGlyphID(glyph.referenceGlyphName))
                else:
                    payload = ("img", _strip_image_metadata(glyph.imageData or b"", glyph.graphicType)[0])
                glyphs.append(
                    (
                        font.getGlyphID(name),
                        glyph.graphicType,
                        int(glyph.originOffsetX),
                        int(glyph.originOffsetY),
                        payload,
                    )
                )
            strikes.append((int(ppem), int(strike.resolution), tuple(glyphs)))
        result["sbix"] = tuple(strikes)
    if "CBDT" in font:
        strikes = []
        for strike in font["CBDT"].strikeData:
            glyphs = []
            for name, glyph in sorted(strike.items(), key=lambda item: font.getGlyphID(item[0])):
                try:
                    image = glyph.imageData
                except AttributeError:
                    continue
                glyphs.append((font.getGlyphID(name), glyph.getFormat(), _strip_image_metadata(image, "png")[0]))
            strikes.append(tuple(glyphs))
        result["CBDT"] = tuple(strikes)
    return result


def verify_equivalence(source_path: Path, output_path: Path) -> EquivalenceReport:
    source = _open_font(source_path, lazy=True)
    output = _open_font(output_path, lazy=True)
    try:
        source_tags = _table_tags(source)
        output_tags = _table_tags(output)
        expected_removed = sorted(source_tags & (set(REMOVE_TABLES) | {"ltag"}))
        expected_output_tags = (source_tags - set(expected_removed)) | {"name"}
        tag_set_ok = output_tags == expected_output_tags

        changed_tables = sorted(
            tag for tag in source_tags & output_tags if _table_data(source, tag) != _table_data(output, tag)
        )
        unexpected_changed = sorted(set(changed_tables) - set(MUTABLE_TABLES))

        checks = {
            "sfnt_version": source.sfntVersion == output.sfntVersion,
            "table_set": tag_set_ok,
            "untouched_tables_byte_identical": not unexpected_changed,
            "glyph_count": int(source["maxp"].numGlyphs) == int(output["maxp"].numGlyphs),
            "units_per_em": int(source["head"].unitsPerEm) == int(output["head"].unitsPerEm),
            "head_functional_fields": _head_snapshot(source) == _head_snapshot(output),
            "os2_functional_fields": _os2_snapshot(source) == _os2_snapshot(output),
            "post_functional_fields": _post_header_snapshot(source) == _post_header_snapshot(output),
            "cmap_gid_mapping": _cmap_snapshot(source) == _cmap_snapshot(output),
            "cff_programs_and_hints": _cff_snapshot(source) == _cff_snapshot(output),
            "embedded_asset_pixels": _embedded_asset_snapshot(source) == _embedded_asset_snapshot(output),
        }
        details = {
            "source_tables": sorted(source_tags),
            "output_tables": sorted(output_tags),
            "expected_output_tables": sorted(expected_output_tags),
            "source_glyphs": int(source["maxp"].numGlyphs),
            "output_glyphs": int(output["maxp"].numGlyphs),
        }
        return EquivalenceReport(
            ok=all(checks.values()),
            changed_tables=changed_tables,
            removed_tables=expected_removed,
            unexpected_changed_tables=unexpected_changed,
            checks=checks,
            details=details,
        )
    finally:
        source.close()
        output.close()


def _decode_name_records(font: TTFont) -> dict[int, list[str]]:
    result: dict[int, set[str]] = {}
    if "name" not in font:
        return {}
    for record in font["name"].names:
        try:
            value = record.toUnicode()
        except Exception:
            value = "<decode-error>"
        result.setdefault(int(record.nameID), set()).add(value)
    return {key: sorted(values) for key, values in sorted(result.items())}


def _collect_source_identity_strings(font: TTFont, source_path: Path | None = None) -> set[str]:
    values: set[str] = set()
    neutral_axis_labels: dict[int, set[str]] = {}
    if "fvar" in font:
        for axis in font["fvar"].axes:
            tag = str(axis.axisTag)
            neutral_axis_labels.setdefault(int(axis.axisNameID), set()).update(
                {tag.casefold(), _generic_axis_label(tag).casefold()}
            )
    if "STAT" in font:
        axes = getattr(getattr(font["STAT"].table, "DesignAxisRecord", None), "Axis", ()) or ()
        for axis in axes:
            tag = str(axis.AxisTag)
            neutral_axis_labels.setdefault(int(axis.AxisNameID), set()).update(
                {tag.casefold(), _generic_axis_label(tag).casefold()}
            )
    if "name" in font:
        for record in font["name"].names:
            try:
                text = record.toUnicode().strip().strip("\x00")
            except Exception:
                continue
            if not text:
                continue
            name_id = int(record.nameID)
            folded = " ".join(text.split()).casefold()
            # Subfamily, axis, feature, and palette labels are functional when
            # they use the small neutral vocabulary FontBlind writes itself.
            # Any custom value is still a possible identity label, regardless
            # of name ID (including private IDs above 255).
            functional = name_id not in IDENTITY_NAME_IDS and (
                folded in COMMON_NONIDENTIFYING
                or folded in GENERIC_OUTPUT_STRINGS
                or folded in neutral_axis_labels.get(name_id, set())
            )
            # A bare version number is not an identity. A custom version field
            # such as a foundry/build label remains in scope.
            neutral_version = name_id == 5 and re.fullmatch(
                r"(?:version\s*)?\d+(?:\.\d+)*(?:\s*;\s*\w+)*",
                folded,
            )
            if not functional and not neutral_version:
                values.add(text)
    if "OS/2" in font:
        vendor = str(getattr(font["OS/2"], "achVendID", "") or "").strip()
        if vendor and vendor.casefold() not in COMMON_NONIDENTIFYING and vendor.casefold() not in GENERIC_OUTPUT_STRINGS:
            values.add(vendor)
    if "CFF " in font:
        cff = font["CFF "].cff
        values.update(str(name) for name in cff.fontNames if name)
        for top in cff.topDictIndex:
            for attr in (*CFF_METADATA_STRINGS.keys(), "BaseFontName", "PostScript"):
                if _cff_explicit(top, attr):
                    value = getattr(top, attr, None)
                    if not value:
                        continue
                    text = str(value)
                    folded = " ".join(text.split()).casefold()
                    if attr == "Weight" and (
                        folded in COMMON_NONIDENTIFYING or folded in GENERIC_OUTPUT_STRINGS
                    ):
                        continue
                    if attr == "version" and re.fullmatch(r"\d+(?:\.\d+)*", folded):
                        continue
                    values.add(text)
            if hasattr(top, "FDArray"):
                try:
                    fd_array = top.FDArray
                except AttributeError:
                    fd_array = None
                if fd_array is not None:
                    for fd in fd_array:
                        if _cff_explicit(fd, "FontName"):
                            value = getattr(fd, "FontName", None)
                            if value:
                                values.add(str(value))
    if source_path is not None:
        stem = source_path.stem.strip()
        if stem:
            values.add(stem)
    return values


def _identity_tokens(strings: Iterable[str]) -> set[str]:
    """Return high-confidence full identity strings for leakage scanning.

    We intentionally do not split copyright or license prose into words. Full
    field values catch family, PostScript, vendor, designer, URL, version, and
    unique-ID leakage without false positives from standard glyph names or
    generic OpenType labels.
    """
    tokens: set[str] = set()
    generic_compact = {re.sub(r"[\s_-]+", "", value) for value in GENERIC_OUTPUT_STRINGS}
    for text in strings:
        clean = " ".join(text.strip().strip("\x00").split())
        folded = clean.casefold()
        if (
            len(clean) < 4
            or folded in COMMON_NONIDENTIFYING
            or folded in GENERIC_OUTPUT_STRINGS
            or re.fullmatch(r"label[- ]\d{5}", folded)
            or clean.isdigit()
        ):
            continue
        tokens.add(clean)
        # Also catch no-space/hyphen spellings often used by filenames and
        # PostScript names, but never break prose into generic word tokens.
        compact = re.sub(r"[\s_-]+", "", clean)
        compact_folded = compact.casefold()
        if len(compact) >= 5 and compact_folded != folded and compact_folded not in generic_compact:
            tokens.add(compact)
    return tokens


def _printable_contains_token(value: str, token: str) -> bool:
    """Find a token in a textual run without matching inside another word."""
    haystack = value.casefold()
    needle = token.casefold()
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        end = index + len(needle)
        left_ok = index == 0 or not (haystack[index - 1].isalnum() or haystack[index - 1] == "_")
        right_ok = end == len(haystack) or not (haystack[end].isalnum() or haystack[end] == "_")
        if left_ok and right_ok:
            return True
        start = index + 1


def _collect_output_identity_strings(font: TTFont) -> set[str]:
    """Collect text only from structures that are defined to contain text.

    Arbitrary outline/layout bytes are intentionally excluded: valid ``glyf``,
    CFF charstrings, and OpenType Layout data can coincidentally spell printable
    words. Changing those bytes to satisfy a raw string scan would change the
    font rather than remove metadata.
    """
    values: set[str] = set()
    if "name" in font:
        for record in font["name"].names:
            try:
                value = record.toUnicode().strip().strip("\x00")
            except Exception:
                continue
            if value:
                values.add(value)
    if "OS/2" in font:
        vendor = str(getattr(font["OS/2"], "achVendID", "") or "").strip()
        if vendor:
            values.add(vendor)
    if "CFF " in font:
        cff = font["CFF "].cff
        values.update(str(name) for name in cff.fontNames if name)
        indexed_strings = getattr(getattr(cff, "strings", None), "strings", None)
        functional_cid_strings: set[str] = set()
        for top in cff.topDictIndex:
            ros = getattr(top, "ROS", None)
            if ros:
                functional_cid_strings.update(str(value) for value in ros[:2])
        if indexed_strings:
            values.update(
                str(value)
                for value in indexed_strings
                if value and str(value) not in functional_cid_strings
            )
        for top in cff.topDictIndex:
            for attr in (*CFF_METADATA_STRINGS.keys(), *CFF_ID_FIELDS, "BaseFontName", "PostScript"):
                if _cff_explicit(top, attr):
                    value = getattr(top, attr, None)
                    if value is not None:
                        values.add(str(value))
            try:
                fd_array = top.FDArray
            except AttributeError:
                fd_array = None
            if fd_array is not None:
                for fd in fd_array:
                    if fd is None:
                        continue
                    for attr in ("FontName", *CFF_METADATA_STRINGS.keys(), *CFF_ID_FIELDS):
                        if _cff_explicit(fd, attr):
                            value = getattr(fd, attr, None)
                            if value is not None:
                                values.add(str(value))
    if "SVG " in font:
        for doc in font["SVG "].docList:
            if doc.data:
                values.add(str(doc.data))
    return values


def _metadata_state_ok(font: TTFont) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    ok = True
    generic = _generic_names(font)
    overrides = _generic_name_overrides(font, generic)
    if "head" in font:
        head = font["head"]
        if head.fontRevision != 1.0 or head.created != FIXED_SFNT_TIME or head.modified != FIXED_SFNT_TIME:
            ok = False
            warnings.append("head revision/timestamps are not normalized")
    if "OS/2" in font:
        os2 = font["OS/2"]
        panose = getattr(os2, "panose", None)
        panose_values = [value for key, value in vars(panose).items() if key != "tableTag"] if panose is not None else []
        if (
            str(os2.achVendID) != "NONE"
            or int(os2.fsType) != 0
            or int(getattr(os2, "sFamilyClass", 0)) != 0
            or any(int(value) != 0 for value in panose_values)
        ):
            ok = False
            warnings.append("OS/2 vendor, embedding, or classification metadata is not normalized")
    if "post" in font and float(font["post"].formatType) != 3.0:
        ok = False
        warnings.append("post table still exposes glyph names")
    names = _decode_name_records(font)
    for name_id, values in names.items():
        expected = overrides.get(name_id, _generic_name(name_id, generic))
        if any(value != expected for value in values):
            ok = False
            warnings.append(f"name ID {name_id} contains non-generic data")
    if "CFF " in font:
        cff = font["CFF "].cff
        cff_metadata_strings = {
            "version": "1.000",
            "Notice": "Metadata removed",
            "Copyright": "Metadata removed",
            "FullName": generic.full,
            "FamilyName": generic.family,
            "Weight": generic.style,
            "FontName": generic.postscript,
        }
        if list(cff.fontNames) != [generic.postscript]:
            ok = False
            warnings.append("CFF Name INDEX is not generic")
        top = cff.topDictIndex[0]
        for attr, expected in cff_metadata_strings.items():
            if _cff_explicit(top, attr) and getattr(top, attr, None) != expected:
                ok = False
                warnings.append(f"CFF {attr} is not generic")
        for attr in CFF_ID_FIELDS:
            if _cff_explicit(top, attr):
                ok = False
                warnings.append(f"CFF {attr} is still present")
        is_cid = _cff_explicit(top, "ROS") or hasattr(top, "ROS")
        if not is_cid:
            for gid, glyph_name in enumerate(top.charset):
                allowed = (
                    (gid == 0 and glyph_name == ".notdef")
                    or glyph_name in cffStandardStrings
                    or re.fullmatch(r"_fb\d{5}(?:_\d+)?", glyph_name) is not None
                )
                if not allowed:
                    ok = False
                    warnings.append(f"CFF glyph name {glyph_name!r} is not neutralized")
                    break
        try:
            fd_array = top.FDArray
        except AttributeError:
            fd_array = None
        if fd_array is not None:
            for index, fd in enumerate(fd_array):
                if fd is None:
                    continue
                if _cff_explicit(fd, "FontName") and getattr(fd, "FontName", None) != f"{generic.postscript}-FD{index:03d}":
                    ok = False
                    warnings.append(f"CFF FDArray FontName {index} is not generic")
                for attr in CFF_ID_FIELDS:
                    if _cff_explicit(fd, attr):
                        ok = False
                        warnings.append(f"CFF FDArray {index} field {attr} is still present")

        # The CFF String INDEX is an actual textual carrier. After rebuilding,
        # it should contain only generic metadata/glyph names plus the required
        # Registry/Ordering strings of a CID-keyed font.
        allowed_strings = {
            generic.family,
            generic.style,
            generic.full,
            generic.postscript,
            "1.000",
            "Metadata removed",
        }
        if is_cid:
            ros = getattr(top, "ROS", None)
            if ros:
                allowed_strings.update(str(value) for value in ros[:2])
        indexed_strings = getattr(getattr(cff, "strings", None), "strings", ())
        for value in indexed_strings:
            text = str(value)
            allowed = (
                text in allowed_strings
                or re.fullmatch(r"_fb\d{5}(?:_\d+)?", text) is not None
                or re.fullmatch(rf"{re.escape(generic.postscript)}-FD\d{{3}}", text) is not None
            )
            if not allowed:
                ok = False
                warnings.append(f"CFF String INDEX contains non-generic text {text!r}")
                break
    if "SVG " in font:
        for doc in font["SVG "].docList:
            if _strip_svg_metadata(doc.data)[1]:
                ok = False
                warnings.append("SVG metadata remains")
                break
    if "sbix" in font:
        for strike in font["sbix"].strikes.values():
            for glyph in strike.glyphs.values():
                if not glyph.is_reference_type() and glyph.imageData is not None:
                    if _strip_image_metadata(glyph.imageData, glyph.graphicType)[1]:
                        ok = False
                        warnings.append("sbix image metadata remains")
                        break
    if "CBDT" in font:
        for strike in font["CBDT"].strikeData:
            for glyph in strike.values():
                image = getattr(glyph, "imageData", None)
                if image is not None and _strip_image_metadata(image, "png")[1]:
                    ok = False
                    warnings.append("CBDT image metadata remains")
                    break
    return ok, warnings


def audit_font(font_path: Path, source_path: Path | None = None) -> AuditReport:
    font = _open_font(font_path, lazy=True)
    try:
        tags = _table_tags(font)
        forbidden = sorted(tags & (set(REMOVE_TABLES) | {"ltag"}))
        metadata_ok, warnings = _metadata_state_ok(font)
        names = _decode_name_records(font)
        output_strings = _collect_output_identity_strings(font)
    finally:
        font.close()

    checked = 0
    found: list[str] = []
    if source_path is not None:
        source = _open_font(source_path, lazy=True)
        try:
            tokens = _identity_tokens(_collect_source_identity_strings(source, source_path))
        finally:
            source.close()
        checked = len(tokens)
        found = sorted(
            token
            for token in tokens
            if any(_printable_contains_token(value, token) for value in output_strings)
        )

    ok = not forbidden and metadata_ok and not found
    return AuditReport(
        ok=ok,
        tables=sorted(tags),
        names=names,
        forbidden_tables_found=forbidden,
        metadata_state_ok=metadata_ok,
        source_identity_tokens_checked=checked,
        source_identity_tokens_found=found,
        warnings=warnings,
    )


def _recreate_once(
    source_path: Path,
    output_path: Path,
    *,
    overwrite: bool,
    source_leak_check: bool = True,
) -> tuple[ScrubStats, EquivalenceReport, AuditReport]:
    if not source_path.is_file():
        raise FontBlindError(f"input font not found: {source_path}")
    if output_path.exists() and not overwrite:
        raise FontBlindError(f"output already exists: {output_path} (use --overwrite)")
    try:
        if source_path.resolve() == output_path.resolve():
            raise FontBlindError("input and output must be different paths")
    except FileNotFoundError:
        pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        font = _open_font(source_path, lazy=True)
        try:
            stats = _sanitize_font(font)
            font.save(str(temp_path), reorderTables=True)
        finally:
            font.close()

        equivalence = verify_equivalence(source_path, temp_path)
        if not equivalence.ok:
            raise FontBlindError(
                "functional equivalence verification failed: "
                + json.dumps({"checks": equivalence.checks, "unexpected_changed_tables": equivalence.unexpected_changed_tables})
            )
        audit = audit_font(temp_path, source_path if source_leak_check else None)
        if not audit.ok:
            raise FontBlindError(
                "metadata audit failed: "
                + json.dumps(
                    {
                        "forbidden_tables": audit.forbidden_tables_found,
                        "source_identity_tokens_found": audit.source_identity_tokens_found,
                        "warnings": audit.warnings,
                    },
                    ensure_ascii=False,
                )
            )
        os.replace(temp_path, output_path)
        return stats, equivalence, audit
    finally:
        if temp_path.exists():
            temp_path.unlink()


def recreate_font(source_path: Path, output_path: Path, *, overwrite: bool = False, verify_rounds: int = 1) -> dict[str, Any]:
    if verify_rounds < 1 or verify_rounds > 20:
        raise FontBlindError("--verify-rounds must be between 1 and 20")

    stats, equivalence, audit = _recreate_once(source_path, output_path, overwrite=overwrite)
    first_hash = _sha256_file(output_path)
    idempotent = True
    round_hashes = [first_hash]

    if verify_rounds > 1:
        with tempfile.TemporaryDirectory(prefix="fontblind-rounds-") as temp_dir:
            previous = output_path
            for round_index in range(2, verify_rounds + 1):
                current = Path(temp_dir) / f"round-{round_index}{output_path.suffix or '.font'}"
                _recreate_once(previous, current, overwrite=True, source_leak_check=False)
                digest = _sha256_file(current)
                round_hashes.append(digest)
                if digest != first_hash or current.read_bytes() != output_path.read_bytes():
                    idempotent = False
                    raise FontBlindError(f"idempotence failed on verification round {round_index}")
                previous = current

    return {
        "ok": True,
        "program": PROGRAM,
        "version": PROGRAM_VERSION,
        "input": str(source_path),
        "output": str(output_path),
        "sha256": first_hash,
        "bytes": output_path.stat().st_size,
        "verification_rounds": verify_rounds,
        "idempotent": idempotent,
        "round_hashes": round_hashes,
        "scrubbed": {
            "removed_tables": stats.removed_tables,
            "name_ids_replaced": stats.name_ids_replaced,
            "cff_glyph_names_replaced": stats.cff_glyph_names_replaced,
            "svg_documents_scrubbed": stats.svg_documents_scrubbed,
            "bitmap_images_scrubbed": stats.bitmap_images_scrubbed,
            "warnings": stats.warnings,
        },
        "equivalence": {
            "ok": equivalence.ok,
            "checks": equivalence.checks,
            "changed_tables": equivalence.changed_tables,
            "removed_tables": equivalence.removed_tables,
            "unexpected_changed_tables": equivalence.unexpected_changed_tables,
        },
        "audit": {
            "ok": audit.ok,
            "source_identity_tokens_checked": audit.source_identity_tokens_checked,
            "source_identity_tokens_found": audit.source_identity_tokens_found,
            "forbidden_tables_found": audit.forbidden_tables_found,
            "warnings": audit.warnings,
        },
    }


def _sample_gids(font_path: Path, maximum: int) -> list[int]:
    font = _open_font(font_path, lazy=True)
    try:
        count = int(font["maxp"].numGlyphs)
        gids = {0}
        if count <= maximum:
            return list(range(count))
        for index in range(maximum):
            gids.add(round(index * (count - 1) / max(1, maximum - 1)))
        if "cmap" in font:
            for subtable in font["cmap"].tables:
                if hasattr(subtable, "cmap"):
                    for name in list(subtable.cmap.values())[: maximum]:
                        gids.add(font.getGlyphID(name))
        return sorted(gid for gid in gids if 0 <= gid < count)[: maximum]
    finally:
        font.close()


def _freetype_raster_snapshot(path: Path, gids: Sequence[int], ppems: Sequence[int]) -> tuple[dict[tuple[int, int], Any], list[str]]:
    try:
        import freetype
    except ImportError:
        return {}, ["freetype-py not installed; raster comparison skipped"]

    snapshots: dict[tuple[int, int], Any] = {}
    warnings: list[str] = []
    face = freetype.Face(str(path))
    for ppem in ppems:
        size_ready = False
        try:
            face.set_pixel_sizes(0, int(ppem))
            size_ready = True
        except freetype.FT_Exception:
            if getattr(face, "available_sizes", None):
                sizes = list(face.available_sizes)
                index = min(range(len(sizes)), key=lambda i: abs(int(sizes[i].y_ppem // 64) - int(ppem)))
                try:
                    face.select_size(index)
                    size_ready = True
                except freetype.FT_Exception:
                    pass
        if not size_ready:
            warnings.append(f"could not select {ppem} ppem")
            continue

        flags = freetype.FT_LOAD_DEFAULT | freetype.FT_LOAD_RENDER
        if hasattr(freetype, "FT_LOAD_COLOR"):
            flags |= freetype.FT_LOAD_COLOR
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


def gauntlet_font(
    source_path: Path,
    *,
    rounds: int = 5,
    glyph_samples: int = 64,
    ppems: Sequence[int] = (9, 12, 16, 24, 48),
) -> dict[str, Any]:
    if rounds < 2 or rounds > 20:
        raise FontBlindError("--rounds must be between 2 and 20")
    if glyph_samples < 1 or glyph_samples > 4096:
        raise FontBlindError("--glyph-samples must be between 1 and 4096")

    with tempfile.TemporaryDirectory(prefix="fontblind-gauntlet-") as temp_dir:
        temp = Path(temp_dir)
        suffix = source_path.suffix if source_path.suffix.lower() in {".ttf", ".otf"} else ".font"
        first = temp / f"round-1{suffix}"
        result = recreate_font(source_path, first, overwrite=True, verify_rounds=rounds)

        gids = _sample_gids(source_path, glyph_samples)
        source_rasters, source_warnings = _freetype_raster_snapshot(source_path, gids, ppems)
        output_rasters, output_warnings = _freetype_raster_snapshot(first, gids, ppems)
        raster_available = bool(source_rasters or output_rasters)
        raster_equal = source_rasters == output_rasters if raster_available else True
        if not raster_equal:
            mismatches = sorted(set(source_rasters) | set(output_rasters))
            mismatches = [key for key in mismatches if source_rasters.get(key) != output_rasters.get(key)]
            raise FontBlindError(f"FreeType raster equivalence failed for {len(mismatches)} sampled glyph/size pairs")

        return {
            **result,
            "gauntlet": {
                "rounds": rounds,
                "glyph_ids_sampled": len(gids),
                "ppems": [int(value) for value in ppems],
                "raster_comparison_available": raster_available,
                "raster_pairs_compared": len(source_rasters),
                "raster_equal": raster_equal,
                "warnings": source_warnings + output_warnings,
            },
        }


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return vars(value)
    raise TypeError(type(value).__name__)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=_json_default))


def cmd_recreate(args: argparse.Namespace) -> int:
    result = recreate_font(
        Path(args.input),
        Path(args.output),
        overwrite=args.overwrite,
        verify_rounds=args.verify_rounds,
    )
    _print_json(result)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    report = audit_font(Path(args.font), Path(args.source) if args.source else None)
    _print_json(report)
    return 0 if report.ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    report = verify_equivalence(Path(args.source), Path(args.output))
    _print_json(report)
    return 0 if report.ok else 1


def cmd_gauntlet(args: argparse.Namespace) -> int:
    report = gauntlet_font(
        Path(args.font),
        rounds=args.rounds,
        glyph_samples=args.glyph_samples,
        ppems=args.ppem,
    )
    _print_json(report)
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Recreate a TTF/OTF with identical functional font data and generic metadata.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {PROGRAM_VERSION}")
    subparsers = parser.add_subparsers(dest="command")

    recreate_parser = subparsers.add_parser("recreate", help="create a metadata-blind copy")
    recreate_parser.add_argument("input", help="source .ttf or .otf")
    recreate_parser.add_argument("output", help="output .ttf or .otf")
    recreate_parser.add_argument("--overwrite", action="store_true", help="replace an existing output")
    recreate_parser.add_argument(
        "--verify-rounds",
        type=int,
        default=1,
        help="repeat the scrub and require byte-identical idempotence (1-20; default: 1)",
    )
    recreate_parser.set_defaults(func=cmd_recreate)

    audit_parser = subparsers.add_parser("audit", help="audit a recreated font for metadata")
    audit_parser.add_argument("font")
    audit_parser.add_argument("--source", help="original font for source-identity leakage checks")
    audit_parser.set_defaults(func=cmd_audit)

    verify_parser = subparsers.add_parser("verify", help="verify functional equivalence")
    verify_parser.add_argument("source")
    verify_parser.add_argument("output")
    verify_parser.set_defaults(func=cmd_verify)

    gauntlet_parser = subparsers.add_parser("gauntlet", help="run repeated structural and raster equivalence loops")
    gauntlet_parser.add_argument("font")
    gauntlet_parser.add_argument("--rounds", type=int, default=5)
    gauntlet_parser.add_argument("--glyph-samples", type=int, default=64)
    gauntlet_parser.add_argument("--ppem", type=int, nargs="+", default=[9, 12, 16, 24, 48])
    gauntlet_parser.set_defaults(func=cmd_gauntlet)

    return parser


def _normalize_shorthand(argv: Sequence[str]) -> list[str]:
    args = list(argv)
    commands = {"recreate", "audit", "verify", "gauntlet"}
    positional = [item for item in args if not item.startswith("-")]
    if args and args[0] not in commands and args[0] not in {"-h", "--help", "--version"} and len(positional) >= 2:
        return ["recreate", *args]
    return args


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    normalized = _normalize_shorthand(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(normalized)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except (FontBlindError, OSError, ValueError, TTLibError) as exc:
        eprint(f"{PROGRAM}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
