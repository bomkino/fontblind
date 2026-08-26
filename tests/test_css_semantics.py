from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fontblind_artifacts import validate_job_artifacts
from fontblind_contract import BuildResultContractError
from fontblind_lab import build_variable_outputs
from tests.test_lab import write_fixture_font


class CssSemanticContractTests(unittest.TestCase):
    def test_valid_syntax_cannot_lie_about_variable_weight_or_stretch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-css-semantics-") as temp_text:
            root = Path(temp_text)
            regular = root / "regular.ttf"
            bold = root / "bold.ttf"
            condensed = root / "condensed.ttf"
            write_fixture_font(regular, weight=400, width_class=5, family="CSS Semantics Regular")
            write_fixture_font(bold, weight=700, width_class=5, family="CSS Semantics Bold")
            write_fixture_font(condensed, weight=400, width_class=3, family="CSS Semantics Condensed")
            job = root / "job"
            result = build_variable_outputs([regular, bold, condensed], job / "output")
            css = job / "output" / result.css.filename
            original = css.read_text(encoding="utf-8")

            validate_job_artifacts(job, result, mode="variable", options={})

            for old, new in (
                ("font-weight: 400 700", "font-weight: 1 1000"),
                ("font-stretch: 75% 100%", "font-stretch: 50% 200%"),
                ("font-style: normal", "font-style: italic"),
                ('format("woff2-variations")', 'format("woff2")'),
            ):
                self.assertIn(old, original)
                css.write_text(original.replace(old, new), encoding="utf-8")
                with self.subTest(field=old), self.assertRaises(BuildResultContractError):
                    validate_job_artifacts(job, result, mode="variable", options={})
                css.write_text(original, encoding="utf-8")

    def test_instance_width_and_slant_are_bound_to_the_selected_location(self) -> None:
        from fontblind_instance import build_static_instance_outputs

        with tempfile.TemporaryDirectory(prefix="fontblind-instance-css-semantics-") as temp_text:
            root = Path(temp_text)
            regular = root / "regular.ttf"
            bold = root / "bold.ttf"
            condensed = root / "condensed.ttf"
            write_fixture_font(regular, weight=400, width_class=5, family="CSS Instance Regular")
            write_fixture_font(bold, weight=700, width_class=5, family="CSS Instance Bold")
            write_fixture_font(condensed, weight=400, width_class=3, family="CSS Instance Condensed")
            variable_dir = root / "variable"
            variable = build_variable_outputs([regular, bold, condensed], variable_dir)
            job = root / "job"
            location = {"wght": 525.0, "wdth": 87.5}
            result = build_static_instance_outputs(variable_dir / variable.native.filename, job / "output", location=location)
            css = job / "output" / result.css.filename
            original = css.read_text(encoding="utf-8")

            validate_job_artifacts(job, result, mode="instance", options={"location": location})

            css.write_text(original.replace("font-stretch: 87.5%", "font-stretch: 100%"), encoding="utf-8")
            with self.assertRaises(BuildResultContractError):
                validate_job_artifacts(job, result, mode="instance", options={"location": location})


if __name__ == "__main__":
    unittest.main()
