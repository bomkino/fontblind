#!/usr/bin/env python3
"""Apply the reviewed generated-container canonicalization layer."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fontblind_artifacts.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor drifted")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(text, "import stat\n", "import stat\nimport struct\n", "struct import")
    text = replace_once(
        text,
        "_ZIP_MODE = 0o644\n",
        "_ZIP_MODE = 0o644\n"
        "_SFNT_SIGNATURES = frozenset({b\"\\x00\\x01\\x00\\x00\", b\"true\", b\"OTTO\"})\n"
        "_MAX_SFNT_TABLES = 4096\n"
        "_MAX_ZIP_RATIO = 1000\n",
        "container constants",
    )

    functions = r'''

def _read_exact(stream: BinaryIO, size: int, message: str) -> bytes:
    payload = stream.read(size)
    if len(payload) != size:
        raise BuildResultContractError(message)
    return payload


def _validate_sfnt_container(path: Path, file_size: int) -> None:
    """Reject data outside the canonical generated SFNT table layout."""
    try:
        with path.open("rb") as stream:
            signature, table_count, search_range, entry_selector, range_shift = struct.unpack(
                ">4sHHHH",
                _read_exact(stream, 12, "worker returned a truncated SFNT header"),
            )
            if signature not in _SFNT_SIGNATURES or not 0 < table_count <= _MAX_SFNT_TABLES:
                raise BuildResultContractError("worker returned an unsupported SFNT container")
            power = 1 << (table_count.bit_length() - 1)
            if (
                search_range != power * 16
                or entry_selector != power.bit_length() - 1
                or range_shift != table_count * 16 - power * 16
            ):
                raise BuildResultContractError("worker returned a non-canonical SFNT directory header")

            directory_end = 12 + table_count * 16
            if directory_end > file_size:
                raise BuildResultContractError("worker returned a truncated SFNT directory")
            records: list[tuple[bytes, int, int, int]] = []
            tags: set[bytes] = set()
            for _index in range(table_count):
                tag, _checksum, offset, length = struct.unpack(
                    ">4sIII",
                    _read_exact(stream, 16, "worker returned a truncated SFNT directory"),
                )
                if tag in tags or length <= 0 or offset < directory_end or offset % 4:
                    raise BuildResultContractError("worker returned an unsafe or non-canonical SFNT table record")
                tags.add(tag)
                raw_end = offset + length
                padded_end = (raw_end + 3) & ~3
                if raw_end < offset or padded_end > file_size:
                    raise BuildResultContractError("worker returned an out-of-range SFNT table")
                records.append((tag, offset, raw_end, padded_end))

            cursor = directory_end
            for _tag, offset, raw_end, padded_end in sorted(records, key=lambda row: row[1]):
                if offset != cursor:
                    raise BuildResultContractError("worker returned hidden or overlapping SFNT container bytes")
                if padded_end > raw_end:
                    stream.seek(raw_end)
                    padding = _read_exact(
                        stream,
                        padded_end - raw_end,
                        "worker returned truncated SFNT table padding",
                    )
                    if any(padding):
                        raise BuildResultContractError("worker returned non-zero SFNT table padding")
                cursor = padded_end
            if cursor != file_size:
                raise BuildResultContractError("worker returned trailing bytes outside the SFNT table extent")
    except BuildResultContractError:
        raise
    except (OSError, struct.error, OverflowError) as exc:
        raise BuildResultContractError("worker returned an unreadable SFNT container") from exc


def _validate_woff2_container(path: Path, file_size: int) -> None:
    """Bind the retained WOFF2 bytes to their header and forbid side channels."""
    try:
        with path.open("rb") as stream:
            (
                signature,
                flavor,
                declared_length,
                table_count,
                reserved,
                total_sfnt_size,
                total_compressed_size,
                _major_version,
                _minor_version,
                metadata_offset,
                metadata_length,
                metadata_original_length,
                private_offset,
                private_length,
            ) = struct.unpack(
                ">4s4sIHHIIHHIIIII",
                _read_exact(stream, 48, "worker returned a truncated WOFF2 header"),
            )
        if signature != b"wOF2" or flavor not in _SFNT_SIGNATURES:
            raise BuildResultContractError("worker returned an unsupported WOFF2 container")
        if declared_length != file_size:
            raise BuildResultContractError("worker returned bytes outside the declared WOFF2 extent")
        if not 0 < table_count <= _MAX_SFNT_TABLES or reserved != 0:
            raise BuildResultContractError("worker returned malformed WOFF2 table metadata")
        if not 12 < total_sfnt_size <= _OUTPUT_MAX_BYTES["native"]:
            raise BuildResultContractError("worker returned an implausible decoded WOFF2 size")
        if not 0 < total_compressed_size < file_size:
            raise BuildResultContractError("worker returned an implausible WOFF2 compressed stream")
        if any((metadata_offset, metadata_length, metadata_original_length, private_offset, private_length)):
            raise BuildResultContractError("worker returned WOFF2 metadata or private-data side channels")
    except BuildResultContractError:
        raise
    except (OSError, struct.error) as exc:
        raise BuildResultContractError("worker returned an unreadable WOFF2 container") from exc


def _validate_zip_container(path: Path, file_size: int, expected_names: tuple[str, ...]) -> None:
    """Require one contiguous classic ZIP with no hidden records or trailing bytes."""
    if file_size < 22:
        raise BuildResultContractError("worker returned a truncated ZIP package")
    try:
        with path.open("rb") as stream:
            eocd_offset = file_size - 22
            stream.seek(eocd_offset)
            (
                signature,
                disk_number,
                central_disk,
                disk_entries,
                total_entries,
                central_size,
                central_offset,
                comment_length,
            ) = struct.unpack(
                "<4s4H2IH",
                _read_exact(stream, 22, "worker returned a truncated ZIP end record"),
            )
            if (
                signature != b"PK\x05\x06"
                or disk_number != 0
                or central_disk != 0
                or disk_entries != len(expected_names)
                or total_entries != len(expected_names)
                or comment_length != 0
                or central_size in {0, 0xFFFFFFFF}
                or central_offset == 0xFFFFFFFF
                or central_offset + central_size != eocd_offset
            ):
                raise BuildResultContractError("worker returned a non-canonical ZIP end record")

            stream.seek(central_offset)
            entries: list[dict[str, int | bytes]] = []
            for expected_name in expected_names:
                fields = struct.unpack(
                    "<4s6H3I5H2I",
                    _read_exact(stream, 46, "worker returned a truncated ZIP central directory"),
                )
                (
                    central_signature,
                    _version_made,
                    version_needed,
                    flags,
                    compression,
                    modified_time,
                    modified_date,
                    crc32,
                    compressed_size,
                    uncompressed_size,
                    filename_length,
                    extra_length,
                    member_comment_length,
                    member_disk,
                    internal_attributes,
                    _external_attributes,
                    local_offset,
                ) = fields
                if (
                    central_signature != b"PK\x01\x02"
                    or flags != 0
                    or compression != zipfile.ZIP_DEFLATED
                    or filename_length <= 0
                    or extra_length != 0
                    or member_comment_length != 0
                    or member_disk != 0
                    or internal_attributes != 0
                    or local_offset == 0xFFFFFFFF
                    or compressed_size in {0, 0xFFFFFFFF}
                    or uncompressed_size in {0, 0xFFFFFFFF}
                    or uncompressed_size > compressed_size * _MAX_ZIP_RATIO + 1024
                ):
                    raise BuildResultContractError("worker returned an unsafe ZIP central-directory entry")
                filename = _read_exact(stream, filename_length, "worker returned a truncated ZIP filename")
                if filename != expected_name.encode("ascii"):
                    raise BuildResultContractError("worker returned an unexpected ZIP member name")
                entries.append(
                    {
                        "name": filename,
                        "version_needed": version_needed,
                        "flags": flags,
                        "compression": compression,
                        "time": modified_time,
                        "date": modified_date,
                        "crc32": crc32,
                        "compressed_size": compressed_size,
                        "uncompressed_size": uncompressed_size,
                        "local_offset": local_offset,
                    }
                )
            if stream.tell() != eocd_offset:
                raise BuildResultContractError("worker returned hidden ZIP central-directory records")

            cursor = 0
            for entry in sorted(entries, key=lambda row: int(row["local_offset"])):
                local_offset = int(entry["local_offset"])
                if local_offset != cursor:
                    raise BuildResultContractError("worker returned hidden or overlapping ZIP local records")
                stream.seek(local_offset)
                (
                    local_signature,
                    version_needed,
                    flags,
                    compression,
                    modified_time,
                    modified_date,
                    crc32,
                    compressed_size,
                    uncompressed_size,
                    filename_length,
                    extra_length,
                ) = struct.unpack(
                    "<4s5H3I2H",
                    _read_exact(stream, 30, "worker returned a truncated ZIP local header"),
                )
                if (
                    local_signature != b"PK\x03\x04"
                    or version_needed != entry["version_needed"]
                    or flags != entry["flags"]
                    or compression != entry["compression"]
                    or modified_time != entry["time"]
                    or modified_date != entry["date"]
                    or crc32 != entry["crc32"]
                    or compressed_size != entry["compressed_size"]
                    or uncompressed_size != entry["uncompressed_size"]
                    or extra_length != 0
                ):
                    raise BuildResultContractError("worker returned an incoherent ZIP local header")
                filename = _read_exact(stream, filename_length, "worker returned a truncated ZIP local filename")
                if filename != entry["name"]:
                    raise BuildResultContractError("worker returned mismatched ZIP member names")
                cursor = stream.tell() + compressed_size
                if cursor > central_offset:
                    raise BuildResultContractError("worker returned an out-of-range ZIP member")
            if cursor != central_offset:
                raise BuildResultContractError("worker returned hidden bytes before the ZIP central directory")
    except BuildResultContractError:
        raise
    except (OSError, UnicodeEncodeError, struct.error, OverflowError) as exc:
        raise BuildResultContractError("worker returned an unreadable ZIP container") from exc
'''
    text = replace_once(text, "\ndef _axis_rows(font: TTFont)", functions + "\n\ndef _axis_rows(font: TTFont)", "container verifier insertion")

    old_loop = '''        if kind in {"native", "web"}:
            _validate_font(path, result, kind)
        elif kind == "bundle":
            with path.open("rb") as stream:
                if stream.read(4) != b"PK\\x03\\x04":
                    raise BuildResultContractError("worker returned a non-ZIP package")
'''
    new_loop = '''        if kind == "native":
            _validate_sfnt_container(path, int(metadata.st_size))
            _validate_font(path, result, kind)
        elif kind == "web":
            _validate_woff2_container(path, int(metadata.st_size))
            _validate_font(path, result, kind)
        elif kind == "bundle":
            _validate_zip_container(
                path,
                int(metadata.st_size),
                (result.native.filename, result.web.filename, result.css.filename),
            )
'''
    text = replace_once(text, old_loop, new_loop, "artifact validation loop")
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
