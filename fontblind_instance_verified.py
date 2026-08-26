"""Production static-instance entry point with a second independent proof pass."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Mapping

from fontblind_instance import (
    INSTANCE_BUNDLE_NAME,
    INSTANCE_CSS_NAME,
    INSTANCE_NATIVE_NAME,
    INSTANCE_WEB_NAME,
    StaticInstanceError,
    build_static_instance_outputs as _build_static_instance_outputs,
)
from fontblind_instance_proof import StaticInstanceProofError, verify_static_instance_outputs
from fontblind_pipeline import PublicBuildResult


_OUTPUT_NAMES = (
    INSTANCE_NATIVE_NAME,
    INSTANCE_WEB_NAME,
    INSTANCE_CSS_NAME,
    INSTANCE_BUNDLE_NAME,
)


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
    "build_static_instance_outputs",
]
