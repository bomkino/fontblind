from __future__ import annotations

import math
import unittest
from dataclasses import replace

from fontblind_contract import BuildResultContractError, validate_build_result
from fontblind_pipeline import OutputFile, PublicBuildResult


def valid_result(*, variable: bool = False) -> PublicBuildResult:
    axes = (
        {"tag": "wght", "name": "Weight", "min": 400.0, "default": 400.0, "max": 700.0},
    ) if variable else ()
    masters = (
        {"id": "M01", "location": {"wght": 400.0}, "default": True},
        {"id": "M02", "location": {"wght": 700.0}, "default": False},
    ) if variable else ()
    return PublicBuildResult(
        native=OutputFile("native", "fontblind-native.ttf", "font/ttf"),
        web=OutputFile("web", "fontblind-web.woff2", "font/woff2"),
        css=OutputFile("css", "fontblind.css", "text/css; charset=utf-8"),
        bundle=OutputFile("bundle", "fontblind-package.zip", "application/zip"),
        flavor="TrueType",
        variable=variable,
        color=False,
        checks={"source_identity_removed": True, "woff2_roundtrip_verified": True},
        axes=axes,
        masters=masters,
    )


class BuildResultContractTests(unittest.TestCase):
    def test_accepts_static_and_anonymous_variable_results(self) -> None:
        validate_build_result(valid_result())
        validate_build_result(valid_result(variable=True))

    def test_rejects_path_traversal_and_descriptor_spoofing(self) -> None:
        base = valid_result()
        cases = (
            replace(base, native=OutputFile("native", "../escape.ttf", "font/ttf")),
            replace(base, native=OutputFile("native", "fontblind-native.ttf", "application/octet-stream")),
            replace(base, web=OutputFile("native", "fontblind-web.woff2", "font/woff2")),
            replace(base, css=OutputFile("css", "fontblind.css/escape.css", "text/css; charset=utf-8")),
            replace(base, bundle=OutputFile("bundle", "fontblind-package.zip", "font/woff2")),
        )
        for result in cases:
            with self.subTest(result=result), self.assertRaises(BuildResultContractError):
                validate_build_result(result)

    def test_rejects_unknown_or_nonliteral_proof_claims(self) -> None:
        base = valid_result()
        for checks in (
            {"unreviewed_claim": True},
            {"source_identity_removed": 1},
            {"source_identity_removed": False},
            {},
        ):
            with self.subTest(checks=checks), self.assertRaises(BuildResultContractError):
                validate_build_result(replace(base, checks=checks))

    def test_rejects_malformed_axes(self) -> None:
        base = valid_result(variable=True)
        cases = (
            ({"tag": "wght", "name": "Weight", "min": True, "default": 400.0, "max": 700.0},),
            ({"tag": "wght", "name": "Source Weight", "min": 400.0, "default": 400.0, "max": 700.0},),
            ({"tag": "opsz", "name": "Optical Size", "min": 8.0, "default": 12.0, "max": 72.0},),
            ({"tag": "wght", "name": "Weight", "min": 400.0, "default": math.nan, "max": 700.0},),
            ({"tag": "wght", "name": "Weight", "min": 700.0, "default": 400.0, "max": 300.0},),
        )
        for axes in cases:
            with self.subTest(axes=axes), self.assertRaises(BuildResultContractError):
                validate_build_result(replace(base, axes=axes))

    def test_rejects_duplicate_or_ambiguous_master_maps(self) -> None:
        base = valid_result(variable=True)
        cases = (
            (
                {"id": "M01", "location": {"wght": 400.0}, "default": True},
                {"id": "M02", "location": {"wght": 400.0}, "default": False},
            ),
            (
                {"id": "M01", "location": {"wght": 400.0}, "default": True},
                {"id": "M02", "location": {"wght": 700.0}, "default": True},
            ),
            (
                {"id": "source.ttf", "location": {"wght": 400.0}, "default": True},
                {"id": "M02", "location": {"wght": 700.0}, "default": False},
            ),
            (
                {"id": "M01", "location": {}, "default": True},
                {"id": "M02", "location": {"wght": 700.0}, "default": False},
            ),
        )
        for masters in cases:
            with self.subTest(masters=masters), self.assertRaises(BuildResultContractError):
                validate_build_result(replace(base, masters=masters))


if __name__ == "__main__":
    unittest.main()
