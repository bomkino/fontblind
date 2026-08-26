from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from fontblind_contract import (
    LANE_BLIND,
    LANE_INSTANCE,
    LANE_OBLIQUE,
    LANE_SLANT,
    LANE_VARIABLE,
    BuildResultContractError,
    expected_lane_for,
    validate_build_result,
    validate_job_artifacts,
    verify_artifact_seal,
)
from fontblind_lab import build_variable_outputs
from fontblind_pipeline import OutputFile, PublicBuildResult
from tests.test_lab import write_fixture_font


_BASE_FILES = {
    "native": OutputFile("native", "fontblind-native.ttf", "font/ttf"),
    "web": OutputFile("web", "fontblind-web.woff2", "font/woff2"),
    "css": OutputFile("css", "fontblind.css", "text/css; charset=utf-8"),
    "bundle": OutputFile("bundle", "fontblind-package.zip", "application/zip"),
}


def lane_result(lane: str, *, parent: bool = False) -> PublicBuildResult:
    axes: tuple[dict[str, object], ...] = ()
    masters: tuple[dict[str, object], ...] = ()
    variable = False
    checks: set[str]

    if lane == LANE_BLIND:
        checks = {
            "source_identity_removed",
            "embedding_flags_cleared",
            "outline_flavor_retained",
            "functional_clone_verified",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    elif lane == LANE_OBLIQUE:
        checks = {
            "source_identity_removed",
            "embedding_flags_cleared",
            "declared_shear_verified",
            "oblique_not_italic_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    elif lane == LANE_SLANT:
        variable = True
        axes = ({"tag": "slnt", "name": "Slant", "min": -12.0, "default": 0.0, "max": 0.0},)
        masters = (
            {"id": "M01", "location": {"slnt": 0.0}, "default": True},
            {"id": "M02", "location": {"slnt": -12.0}, "default": False},
        )
        checks = {
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
    elif lane == LANE_VARIABLE:
        variable = True
        axes = ({"tag": "wght", "name": "Weight", "min": 400.0, "default": 400.0, "max": 700.0},)
        masters = (
            {"id": "M01", "location": {"wght": 400.0}, "default": True},
            {"id": "M02", "location": {"wght": 700.0}, "default": False},
        )
        checks = {
            "source_identity_removed",
            "embedding_flags_cleared",
            "donor_compatibility_verified",
            "donor_instances_verified",
            "independent_axis_model_verified",
            "axis_metadata_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
            "weight_axis_verified",
        }
    elif lane == LANE_INSTANCE:
        checks = {
            "source_identity_removed",
            "embedding_flags_cleared",
            "selected_location_verified",
            "static_instance_verified",
            "variation_tables_removed",
            "axis_metadata_verified",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
        }
    else:
        raise AssertionError(lane)

    if parent:
        checks.add("source_discarded")
    return PublicBuildResult(
        native=_BASE_FILES["native"],
        web=_BASE_FILES["web"],
        css=_BASE_FILES["css"],
        bundle=_BASE_FILES["bundle"],
        flavor="TrueType",
        variable=variable,
        color=False,
        checks={key: True for key in sorted(checks)},
        axes=axes,
        masters=masters,
    )


class ClosedLaneProofTests(unittest.TestCase):
    def test_every_lane_requires_its_exact_worker_and_parent_contract(self) -> None:
        for lane in (LANE_BLIND, LANE_OBLIQUE, LANE_SLANT, LANE_VARIABLE, LANE_INSTANCE):
            with self.subTest(lane=lane, phase="worker"):
                validate_build_result(lane_result(lane), expected_lane=lane)
            with self.subTest(lane=lane, phase="parent"):
                validate_build_result(
                    lane_result(lane, parent=True),
                    expected_lane=lane,
                    require_source_discarded=True,
                )

    def test_omitted_or_cross_lane_passes_cannot_masquerade_as_success(self) -> None:
        for lane in (LANE_BLIND, LANE_OBLIQUE, LANE_SLANT, LANE_VARIABLE, LANE_INSTANCE):
            base = lane_result(lane)
            omitted = dict(base.checks)
            omitted.pop(next(iter(omitted)))
            with self.subTest(lane=lane, attack="omitted"), self.assertRaises(BuildResultContractError):
                validate_build_result(replace(base, checks=omitted), expected_lane=lane)

            extra = dict(base.checks)
            extra["source_discarded"] = True
            with self.subTest(lane=lane, attack="premature-parent-proof"), self.assertRaises(BuildResultContractError):
                validate_build_result(replace(base, checks=extra), expected_lane=lane)

        variable = lane_result(LANE_VARIABLE)
        extra_known = dict(variable.checks)
        extra_known["slant_axis_verified"] = True
        with self.assertRaises(BuildResultContractError):
            validate_build_result(replace(variable, checks=extra_known), expected_lane=LANE_VARIABLE)

    def test_axis_model_controls_the_required_axis_proof(self) -> None:
        variable = lane_result(LANE_VARIABLE)
        missing = dict(variable.checks)
        missing.pop("weight_axis_verified")
        with self.assertRaises(BuildResultContractError):
            validate_build_result(replace(variable, checks=missing), expected_lane=LANE_VARIABLE)

        wrong_default = (
            {"id": "M01", "location": {"wght": 400.0}, "default": False},
            {"id": "M02", "location": {"wght": 700.0}, "default": True},
        )
        with self.assertRaises(BuildResultContractError):
            validate_build_result(replace(variable, masters=wrong_default), expected_lane=LANE_VARIABLE)

    def test_requested_mode_is_the_authoritative_lane(self) -> None:
        self.assertEqual(expected_lane_for("blind", {}), LANE_BLIND)
        self.assertEqual(expected_lane_for("oblique", {"output": "static"}), LANE_OBLIQUE)
        self.assertEqual(expected_lane_for("oblique", {"output": "slnt"}), LANE_SLANT)
        self.assertEqual(expected_lane_for("variable", {}), LANE_VARIABLE)
        self.assertEqual(expected_lane_for("instance", {}), LANE_INSTANCE)
        with self.assertRaises(BuildResultContractError):
            expected_lane_for("oblique", {"output": "italic"})
        with self.assertRaises(BuildResultContractError):
            validate_build_result(lane_result(LANE_INSTANCE), expected_lane=LANE_BLIND)


class RetainedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="fontblind-artifact-contract-")
        root = Path(cls._temporary.name)
        regular = root / "regular.ttf"
        bold = root / "bold.ttf"
        write_fixture_font(regular, weight=400, family="Artifact Contract Regular")
        write_fixture_font(bold, weight=700, family="Artifact Contract Bold")
        cls.template_job = root / "template-job"
        cls.template_output = cls.template_job / "output"
        cls.result = build_variable_outputs([regular, bold], cls.template_output)
        cls.symlink_target = regular

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def fresh_job(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fontblind-artifact-copy-")
        job = Path(temporary.name) / "job"
        shutil.copytree(self.template_job, job)
        return temporary, job

    def test_accepts_and_seals_the_exact_generated_package(self) -> None:
        temporary, job = self.fresh_job()
        try:
            seals = validate_job_artifacts(job, self.result)
            self.assertEqual(set(seals), {"native", "web", "css", "bundle"})
            for kind, seal in seals.items():
                self.assertTrue(verify_artifact_seal(job, getattr(self.result, kind), seal))
        finally:
            temporary.cleanup()

    def test_rejects_unexpected_files_external_css_and_mutated_packages(self) -> None:
        for attack in ("extra", "css", "bundle"):
            temporary, job = self.fresh_job()
            try:
                output = job / "output"
                if attack == "extra":
                    (output / ".worker-debug").write_text("private", encoding="utf-8")
                elif attack == "css":
                    (output / self.result.css.filename).write_text(
                        '@font-face { font-family: "Untitled"; src: url("https://example.invalid/source.woff2"); }\n',
                        encoding="utf-8",
                    )
                else:
                    with zipfile.ZipFile(output / self.result.bundle.filename, "a") as archive:
                        archive.writestr("unexpected.txt", "private")
                with self.subTest(attack=attack), self.assertRaises(BuildResultContractError):
                    validate_job_artifacts(job, self.result)
            finally:
                temporary.cleanup()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_rejects_a_declared_output_replaced_by_a_symlink(self) -> None:
        temporary, job = self.fresh_job()
        try:
            native = job / "output" / self.result.native.filename
            native.unlink()
            try:
                native.symlink_to(self.symlink_target)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaises(BuildResultContractError):
                validate_job_artifacts(job, self.result)
        finally:
            temporary.cleanup()

    def test_a_post_validation_mutation_breaks_the_download_seal(self) -> None:
        temporary, job = self.fresh_job()
        try:
            seals = validate_job_artifacts(job, self.result)
            css = job / "output" / self.result.css.filename
            css.write_text(css.read_text(encoding="utf-8") + "/* changed */\n", encoding="utf-8")
            self.assertFalse(verify_artifact_seal(job, self.result.css, seals["css"]))
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
