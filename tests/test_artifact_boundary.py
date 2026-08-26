from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from fontTools.ttLib import TTFont

from fontblind_artifacts import validate_job_artifacts, verify_artifact_seal
from fontblind_contract import BuildResultContractError
from fontblind_lab import build_variable_outputs
from tests.test_lab import write_fixture_font


class ArtifactBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="fontblind-artifact-boundary-")
        root = Path(cls._temporary.name)
        regular = root / "regular.ttf"
        bold = root / "bold.ttf"
        write_fixture_font(regular, weight=400, family="Artifact Boundary Regular")
        write_fixture_font(bold, weight=700, family="Artifact Boundary Bold")
        cls.template_job = root / "template-job"
        cls.template_output = cls.template_job / "output"
        cls.result = build_variable_outputs([regular, bold], cls.template_output)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def fresh_job(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fontblind-artifact-boundary-copy-")
        job = Path(temporary.name) / "job"
        shutil.copytree(self.template_job, job)
        return temporary, job

    def package_files(self, job: Path) -> list[tuple[Path, str]]:
        output = job / "output"
        return [
            (output / self.result.native.filename, self.result.native.filename),
            (output / self.result.web.filename, self.result.web.filename),
            (output / self.result.css.filename, self.result.css.filename),
        ]

    def rewrite_bundle(
        self,
        job: Path,
        *,
        timestamp: tuple[int, int, int, int, int, int] = (2020, 1, 1, 0, 0, 0),
        compression: int = zipfile.ZIP_DEFLATED,
        member_extra: bytes = b"",
        member_comment: bytes = b"",
        mode: int = 0o644,
    ) -> None:
        bundle = job / "output" / self.result.bundle.filename
        with zipfile.ZipFile(bundle, "w", compression=compression) as archive:
            for source, name in self.package_files(job):
                info = zipfile.ZipInfo(name, date_time=timestamp)
                info.compress_type = compression
                info.external_attr = mode << 16
                info.extra = member_extra
                info.comment = member_comment
                archive.writestr(info, source.read_bytes())

    def test_accepts_exact_files_and_seals_every_download(self) -> None:
        temporary, job = self.fresh_job()
        try:
            seals = validate_job_artifacts(job, self.result)
            self.assertEqual(set(seals), {"native", "web", "css", "bundle"})
            for kind, seal in seals.items():
                self.assertTrue(verify_artifact_seal(job, getattr(self.result, kind), seal))
        finally:
            temporary.cleanup()

    def test_css_must_match_the_exact_single_font_face_grammar(self) -> None:
        attacks = (
            '@import "private.css";\n',
            '/* hidden source label */\n',
            '  src: local/**/("Private"), url("fontlab-variable.woff2") format("woff2-variations");\n',
            'body { background: red; }\n',
        )
        for attack in attacks:
            temporary, job = self.fresh_job()
            try:
                css = job / "output" / self.result.css.filename
                original = css.read_text(encoding="utf-8")
                if attack.startswith("  src:"):
                    lines = original.splitlines(keepends=True)
                    lines[2] = attack
                    css.write_text("".join(lines), encoding="utf-8")
                else:
                    css.write_text(attack + original, encoding="utf-8")
                with self.subTest(attack=attack.strip()), self.assertRaises(BuildResultContractError):
                    validate_job_artifacts(job, self.result)
            finally:
                temporary.cleanup()

    def test_native_and_woff2_must_be_the_same_verified_font(self) -> None:
        temporary, job = self.fresh_job()
        try:
            web = job / "output" / self.result.web.filename
            replacement = web.with_suffix(".changed.woff2")
            font = TTFont(str(web), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
            try:
                font["head"].fontRevision = float(font["head"].fontRevision) + 0.125
                font.recalcTimestamp = False
                font.save(str(replacement), reorderTables=True)
            finally:
                font.close()
            os.replace(replacement, web)
            with self.assertRaises(BuildResultContractError):
                validate_job_artifacts(job, self.result)
        finally:
            temporary.cleanup()

    def test_public_axis_metadata_must_match_the_actual_font(self) -> None:
        temporary, job = self.fresh_job()
        try:
            wrong = replace(
                self.result,
                axes=(
                    {
                        "tag": "wght",
                        "name": "Weight",
                        "min": 400.0,
                        "default": 400.0,
                        "max": 800.0,
                    },
                ),
            )
            with self.assertRaises(BuildResultContractError):
                validate_job_artifacts(job, wrong)
        finally:
            temporary.cleanup()

    @unittest.skipUnless(hasattr(os, "link"), "hard links unavailable")
    def test_declared_output_cannot_be_a_hard_link(self) -> None:
        temporary, job = self.fresh_job()
        try:
            native = job / "output" / self.result.native.filename
            external = job / "external-copy.ttf"
            shutil.copy2(native, external)
            native.unlink()
            try:
                os.link(external, native)
            except OSError as exc:
                self.skipTest(f"hard-link creation unavailable: {exc}")
            self.assertGreater(os.stat(native).st_nlink, 1)
            with self.assertRaises(BuildResultContractError):
                validate_job_artifacts(job, self.result)
        finally:
            temporary.cleanup()

    def test_zip_members_require_deterministic_safe_metadata(self) -> None:
        attacks = (
            {"timestamp": (2024, 1, 1, 0, 0, 0)},
            {"compression": zipfile.ZIP_STORED},
            {"member_extra": b"\x0a\x00\x04\x00test"},
            {"member_comment": b"private"},
            {"mode": 0o777},
        )
        for values in attacks:
            temporary, job = self.fresh_job()
            try:
                self.rewrite_bundle(job, **values)
                with self.subTest(values=values), self.assertRaises(BuildResultContractError):
                    validate_job_artifacts(job, self.result)
            finally:
                temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
