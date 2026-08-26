"""Strict zero-origin-label policy for the browser product.

The preservation engine keeps every table it does not understand. That is the
right default for fidelity, but the wrong default when the promise is "no
source identity labels." This module makes that trade explicit: registered,
understood functional tables pass; unknown/private carriers fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from fontTools.ttLib import TTFont

import fontblind_surgical as surgical


# Tables whose runtime role is understood and whose textual surfaces are either
# rebuilt by FontBlind or contain no free-form source strings. Keep this list
# deliberately explicit. A new tag must be reviewed before it can pass.
FUNCTIONAL_TABLES = frozenset(
    {
        "BASE",
        "CBDT",
        "CBLC",
        "CFF ",
        "CFF2",
        "COLR",
        "CPAL",
        "EBDT",
        "EBLC",
        "EBSC",
        "GDEF",
        "GPOS",
        "GSUB",
        "HVAR",
        "JSTF",
        "LTSH",
        "MATH",
        "MVAR",
        "OS/2",
        "STAT",
        "SVG ",
        "VDMX",
        "VORG",
        "VVAR",
        "acnt",
        "ankr",
        "avar",
        "bdat",
        "bhed",
        "bloc",
        "bsln",
        "cmap",
        "cvar",
        "cvt ",
        "feat",
        "fpgm",
        "fvar",
        "gasp",
        "glyf",
        "gvar",
        "hdmx",
        "head",
        "hhea",
        "hmtx",
        "just",
        "kern",
        "kerx",
        "lcar",
        "loca",
        "maxp",
        "mort",
        "morx",
        "name",
        "opbd",
        "post",
        "prep",
        "prop",
        "sbix",
        "trak",
        "vhea",
        "vmtx",
        # Graphite runtime tables.
        "Feat",
        "Glat",
        "Gloc",
        "Silf",
        "Sill",
    }
)

# Embedded SVG and raster payloads can contain identity in rendering-critical
# XML attributes, ICC profiles, or opaque codecs. The engine scrubs common
# metadata, but "zero" needs a proof stronger than "common." Reject them until
# there is a format-complete rewriter and adversarial corpus.
OPAQUE_ASSET_TABLES = frozenset(
    {"SVG ", "sbix", "CBDT", "CBLC", "EBDT", "EBLC", "EBSC", "bdat", "bloc"}
)

IDENTITY_ENCODINGS = (
    "utf-8",
    "utf-16-be",
    "utf-16-le",
    "utf-32-be",
    "utf-32-le",
    "mac-roman",
    "latin-1",
)


def _clean_identity_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value.strip().strip("\x00")).split())


def _strict_identity_tokens(strings: set[str]) -> set[str]:
    """Return full source labels without generic/short-name blind spots."""
    tokens: set[str] = set()
    for value in strings:
        raw = unicodedata.normalize("NFKC", value.strip().strip("\x00"))
        clean = _clean_identity_text(value)
        for candidate in (raw, clean):
            if candidate:
                tokens.add(candidate)
        compact = re.sub(r"[\s_-]+", "", clean)
        if compact and compact != clean:
            tokens.add(compact)
    return tokens


def _identity_byte_needles(tokens: set[str]) -> tuple[set[bytes], set[str]]:
    """Build normalized, case-insensitive byte probes for common font encodings."""
    needles: set[bytes] = set()
    unicode_tokens: set[str] = set()
    for token in tokens:
        if any(ord(character) > 127 for character in token):
            unicode_tokens.add(unicodedata.normalize("NFKC", token).casefold())
        forms = {
            token,
            token.lower(),
            token.upper(),
            token.casefold(),
            unicodedata.normalize("NFC", token),
            unicodedata.normalize("NFD", token),
            unicodedata.normalize("NFKC", token),
            unicodedata.normalize("NFKD", token),
        }
        for form in forms:
            for encoding in IDENTITY_ENCODINGS:
                try:
                    encoded = form.encode(encoding)
                except UnicodeEncodeError:
                    continue
                if encoded:
                    needles.add(encoded.lower())
    return needles, unicode_tokens


def _payload_contains_identity(payload: bytes, needles: set[bytes], unicode_tokens: set[str]) -> bool:
    # bytes.lower() catches arbitrary ASCII casing in UTF-8, UTF-16, UTF-32,
    # MacRoman, and Latin-1 without generating exponential case variants.
    folded_payload = payload.lower()
    if any(needle in folded_payload for needle in needles):
        return True
    if not unicode_tokens:
        return False

    # For non-ASCII labels, decode at every possible code-unit alignment so
    # Unicode case folding and canonical/compatibility normalization are exact.
    widths = {
        "utf-8": 1,
        "utf-16-be": 2,
        "utf-16-le": 2,
        "utf-32-be": 4,
        "utf-32-le": 4,
        "mac-roman": 1,
        "latin-1": 1,
    }
    for encoding in IDENTITY_ENCODINGS:
        for offset in range(widths[encoding]):
            decoded = payload[offset:].decode(encoding, errors="ignore")
            folded = unicodedata.normalize("NFKC", decoded).casefold()
            if any(token in folded for token in unicode_tokens):
                return True
    return False


class ZeroIdPolicyError(surgical.FontBlindError):
    """A source cannot satisfy the strict browser contract safely."""


class BrowserCompatibilityError(surgical.FontBlindError):
    """A source lacks structure required by the supported browsers."""


@dataclass(frozen=True)
class SourceContract:
    native_suffix: str
    outline_flavor: str
    variable: bool
    color: bool


def _tags(font: TTFont) -> set[str]:
    if font.reader is not None:
        return set(font.reader.keys())
    return {tag for tag in font.keys() if tag != "GlyphOrder"}


def inspect_strict_source(path: Path) -> SourceContract:
    """Validate a source before any output is emitted.

    Known metadata tables are allowed because the engine removes them. Unknown
    tables and opaque embedded-asset containers are rejected rather than copied
    into a supposedly zero-ID font.
    """
    font = surgical._open_font(Path(path), lazy=True)
    try:
        tags = _tags(font)
        accepted = set(FUNCTIONAL_TABLES) | set(surgical.REMOVE_TABLES) | {"ltag"}
        unknown = sorted(tags - accepted)
        if unknown:
            joined = ", ".join(repr(tag) for tag in unknown)
            raise ZeroIdPolicyError(
                "This font contains unreviewed private table(s): " + joined + ". No output was kept."
            )

        opaque = sorted(tags & set(OPAQUE_ASSET_TABLES))
        if opaque:
            joined = ", ".join(repr(tag) for tag in opaque)
            raise ZeroIdPolicyError(
                "This font contains embedded artwork that cannot yet be proven zero-ID: "
                + joined
                + ". No output was kept."
            )

        if "OS/2" not in font:
            raise BrowserCompatibilityError(
                "This font is missing the OS/2 table required by modern browser font sanitizers. No output was kept."
            )

        if "glyf" in font:
            suffix = ".ttf"
            flavor = "TrueType"
        elif "CFF2" in font:
            suffix = ".otf"
            flavor = "OpenType CFF2"
        elif "CFF " in font:
            suffix = ".otf"
            flavor = "OpenType CFF"
        else:
            raise ZeroIdPolicyError("No supported vector outline was found. No output was kept.")

        if "CFF " in font:
            cff = font["CFF "].cff
            if any(getattr(top, "ROS", None) for top in cff.topDictIndex):
                # Registry and Ordering are functional strings in a CID-keyed
                # CFF. Rewriting them can change CID semantics; retaining them
                # can preserve a foundry/source identifier. Strict mode rejects
                # the format until a reviewed neutral mapping exists.
                raise ZeroIdPolicyError(
                    "CID-keyed CFF cannot yet be proven zero-ID without changing its semantics. No output was kept."
                )

        # Labels shorter than four characters cannot be searched across binary
        # runtime tables without overwhelming false matches. Refuse them rather
        # than claim a proof the scanner cannot make.
        for value in surgical._collect_source_identity_strings(font):
            clean = _clean_identity_text(value)
            if 0 < len(clean) < 4:
                raise ZeroIdPolicyError(
                    "A source identity label is too short to prove absent from runtime bytes. No output was kept."
                )

        return SourceContract(
            native_suffix=suffix,
            outline_flavor=flavor,
            variable="fvar" in font,
            color=bool(tags & {"COLR", "CPAL"}),
        )
    finally:
        font.close()


def assert_strict_output(output: Path, source: Path) -> None:
    """Run the shared audit and require every known identity surface to pass."""
    report = surgical.audit_font(Path(output), Path(source))
    if not report.ok:
        raise ZeroIdPolicyError("The zero-ID audit failed. No output was kept.")

    source_font = surgical._open_font(Path(source), lazy=True)
    font = surgical._open_font(Path(output), lazy=True)
    try:
        unexpected = sorted(_tags(font) - set(FUNCTIONAL_TABLES))
        if unexpected:
            raise ZeroIdPolicyError("The output retained an unreviewed table. No output was kept.")

        identity_strings = surgical._collect_source_identity_strings(source_font)
        source_strings = set(identity_strings)

        # A valid runtime table can still carry an original label in executable
        # or layout bytes. Search every retained compiled table for the complete
        # high-confidence source labels, in the encodings used by OpenType and
        # legacy Macintosh name records. Strict mode fails closed on a match.
        strict_tokens = _strict_identity_tokens(identity_strings)
        needles, unicode_tokens = _identity_byte_needles(strict_tokens)
        for tag in sorted(_tags(font)):
            try:
                payload = font.getTableData(tag)
            except Exception as exc:
                raise ZeroIdPolicyError("An output table could not be checked for source labels. No output was kept.") from exc
            if _payload_contains_identity(payload, needles, unicode_tokens):
                raise ZeroIdPolicyError("A source identity string survived in retained font data. No output was kept.")

        output_strings = surgical._collect_output_identity_strings(font)
        output_folded = {" ".join(value.strip().split()).casefold() for value in output_strings}
        for value in source_strings:
            folded = " ".join(str(value).strip().split()).casefold()
            if not folded:
                continue
            if folded in output_folded:
                raise ZeroIdPolicyError("A source identity string survived the strict audit. No output was kept.")
    finally:
        source_font.close()
        font.close()
