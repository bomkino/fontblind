"""Production static-instance entry point with a second independent proof pass."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Mapping

import fontblind_surgical as surgical
from fontblind_instance import (
    INSTANCE_BUNDLE_NAME,
    INSTANCE_CSS_NAME,
    INSTANCE_NATIVE_NAME,
    INSTANCE_WEB_NAME,
    StaticInstanceError,
    build_static_instance_outputs as _build_static_instance_outputs,
)
from fontblind_instance_proof import (
    StaticInstanceProofError,
    _assert_no_variable_payload,
    _assert_selected_semantics,
    _load,
    _save_fresh_reference,
    _validated_location,
    _verify_selected_metadata,
)
from fontblind_pipeline import PublicBuildResult, _decode_woff2, _verify_woff2_roundtrip


_OUTPUT_NAMES = (
    INSTANCE_NATIVE_NAME,
    INSTANCE_WEB_NAME,
    INSTANCE_CSS_NAME,
    INSTANCE_BUNDLE_NAME,
)


def verify_static_instance_outputs(
    source: Path,
    native: Path,
    web: Path,
    css: Path,
    *,
    location: Mapping[str, object],
) -> None:
    """Prove native semantics, then prove WOFF2 as a lossless container.

    WOFF2 decoding may legitimately canonicalise a few SFNT representation
    details such as ``head.flags``. The established round-trip verifier proves
    that container reconstruction has not changed font behaviour; the stricter
    independent table and metric comparison therefore applies to the native
    static font, while the decoded WOFF2 is separately checked for residual
    variation payload, selected metadata, and the zero-ID contract.
    """
    source_font = _load(source)
    try:
        selected = _validated_location(source_font, location)
    finally:
        source_font.close()

    with tempfile.TemporaryDirectory(prefix=".fontblind-independent-instance-", dir=str(native.parent)) as temp_text:
        temp = Path(temp_text)
        reference = temp / "fresh-reference.ttf"
        decoded = temp / "decoded-output.ttf"
        _save_fresh_reference(source, selected, reference)

        _assert_selected_semantics(reference, native)
        _verify_selected_metadata(source, native, css, selected)
        if not surgical.audit_font(native).ok:
            raise StaticInstanceProofError("Static output failed the independent zero-ID audit.")

        _decode_woff2(web, decoded)
        _verify_woff2_roundtrip(native, decoded)
        decoded_font = _load(decoded)
        try:
            _assert_no_variable_payload(decoded_font)
        finally:
            decoded_font.close()
        _verify_selected_metadata(source, decoded, None, selected)
        if not surgical.audit_font(decoded).ok:
            raise StaticInstanceProofError("Decoded static WOFF2 failed the independent zero-ID audit.")


def build_static_instance_outputs(
    source: Path,
    output_dir: Path,
    *,
    location: Mapping[str, object],
) -> PublicBuildResult:
    """Build into an unpublished stage, independently prove it, then commit it."""
    source = Path(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destinations = tuple(output_dir / name for name in _OUTPUT_NAMES)
    if source.resolve(strict=False) in {path.resolve(strict=False) for path in destinations}:
        raise StaticInstanceError("A generated variable source cannot also be a frozen output.")

    with tempfile.TemporaryDirectory(prefix=".fontblind-instance-proof-stage-", dir=str(output_dir.parent)) as temp_text:
        stage = Path(temp_text) / "output"
        result = _build_static_instance_outputs(source, stage, location=location)
        verify_static_instance_outputs(
            source,
            stage / result.native.filename,
            stage / result.web.filename,
            stage / result.css.filename,
            location=location,
        )
        for kind, destination in zip(("native", "web", "css", "bundle"), destinations):
            staged = stage / getattr(result, kind).filename
            if not staged.is_file():
                raise StaticInstanceProofError("Independent proof lost a staged static output before commit.")
            os.replace(staged, destination)
    return result


__all__ = [
    "StaticInstanceError",
    "StaticInstanceProofError",
    "verify_static_instance_outputs",
    "build_static_instance_outputs",
]
