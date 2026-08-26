#!/usr/bin/env python3
"""Apply findings from the first real-font Gate 7 corpus run."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor drifted in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_variable_name_precedence() -> None:
    path = ROOT / "fontblind_surgical.py"
    replace_once(
        path,
        '                overrides[name_id] = f"Value {index:02d}"\n',
        '                # A shared fvar/STAT name ID is primarily a named instance;\n'
        '                # keep that more specific neutral label instead of replacing\n'
        '                # it with the generic STAT value label.\n'
        '                overrides.setdefault(name_id, f"Value {index:02d}")\n',
        "shared fvar/STAT name precedence",
    )


def patch_corpus_test() -> None:
    path = ROOT / "tests" / "test_corpus.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from fontblind_lab import build_oblique_outputs, build_variable_outputs\n",
        "from fontblind_lab import FontLabError, build_oblique_outputs, build_variable_outputs\n",
        1,
    )
    old = '''    def test_real_hebrew_variable_font_supplies_compatible_two_axis_donors(self) -> None:
        source = CORPUS_DIR / str(self.by_id["hebrew-variable-ttf"]["filename"])
        axes = _axis_rows(source)
        self.assertEqual(set(axes), {"wdth", "wght"})
        weight_min, weight_default, weight_max = axes["wght"]
        width_min, width_default, width_max = axes["wdth"]
        weight_extreme = weight_max if weight_max != weight_default else weight_min
        width_extreme = width_min if width_min != width_default else width_max

        with tempfile.TemporaryDirectory(prefix="fontblind-corpus-donors-") as temp_text:
            root = Path(temp_text)
            donors = [root / "default.ttf", root / "weight.ttf", root / "width.ttf"]
            _save_instance(source, donors[0], {"wght": weight_default, "wdth": width_default})
            _save_instance(source, donors[1], {"wght": weight_extreme, "wdth": width_default})
            _save_instance(source, donors[2], {"wght": weight_default, "wdth": width_extreme})

            result = build_variable_outputs(donors, root / "variable")
            result.require_verified()
            self.assertEqual([axis["tag"] for axis in result.axes], ["wght", "wdth"])
            generated = root / "variable" / result.native.filename
            location = {
                "wght": weight_default + (weight_extreme - weight_default) * 0.43,
                "wdth": width_default + (width_extreme - width_default) * 0.37,
            }
            frozen = build_static_instance_outputs(generated, root / "static", location=location)
            frozen.require_verified()
            font = TTFont(str(root / "static" / frozen.native.filename), lazy=False)
            try:
                self.assertFalse(VARIATION_TABLES & set(font.keys()))
            finally:
                font.close()
'''
    new = '''    def test_extracted_existing_variable_donors_fail_closed_on_geometry_drift(self) -> None:
        source = CORPUS_DIR / str(self.by_id["hebrew-variable-ttf"]["filename"])
        axes = _axis_rows(source)
        self.assertEqual(set(axes), {"wdth", "wght"})
        weight_min, weight_default, weight_max = axes["wght"]
        width_min, width_default, width_max = axes["wdth"]
        weight_extreme = weight_max if weight_max != weight_default else weight_min
        width_extreme = width_min if width_min != width_default else width_max

        with tempfile.TemporaryDirectory(prefix="fontblind-corpus-donors-") as temp_text:
            root = Path(temp_text)
            donors = [root / "default.ttf", root / "weight.ttf", root / "width.ttf"]
            _save_instance(source, donors[0], {"wght": weight_default, "wdth": width_default})
            _save_instance(source, donors[1], {"wght": weight_extreme, "wdth": width_default})
            _save_instance(source, donors[2], {"wght": weight_default, "wdth": width_extreme})

            output = root / "variable"
            with self.assertRaisesRegex(FontLabError, "does not match donor geometry"):
                build_variable_outputs(donors, output)
            self.assertEqual(list(output.iterdir()) if output.exists() else [], [])
'''
    if text.count(old) != 1:
        raise RuntimeError("real extracted-donor corpus test anchor drifted")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_manifest() -> None:
    path = ROOT / "tests" / "corpus" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    matches = [asset for asset in manifest["assets"] if asset.get("id") == "hebrew-variable-ttf"]
    if len(matches) != 1:
        raise RuntimeError("Hebrew corpus manifest entry drifted")
    matches[0]["role"] = "Hebrew combining marks, right-to-left shaping and extracted-donor geometry-refusal coverage"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_docs() -> None:
    replace_once(
        ROOT / "tests" / "corpus" / "README.md",
        "| Noto Sans Hebrew | RTL shaping, combining marks, real donor extraction and Variable Lab |",
        "| Noto Sans Hebrew | RTL shaping, combining marks, and fail-closed extracted-donor geometry drift |",
        "corpus Hebrew role",
    )
    replace_once(
        ROOT / "tests" / "corpus" / "README.md",
        "The Hebrew variable font supplies real compatible static donors for a donor-built two-axis Variable Lab pass.",
        "The Hebrew variable font supplies extracted static candidates that FontBlind correctly refuses when recompiled donor geometry drifts.",
        "corpus Hebrew outcome",
    )
    replace_once(
        ROOT / "README.md",
        "real compatible donor extraction, Oblique Lab, and fractional static positions.",
        "real extracted-donor refusal on geometry drift, Oblique Lab, and fractional static positions.",
        "README corpus coverage",
    )
    replace_once(
        ROOT / "CHANGELOG.md",
        "real donor extraction, Oblique Lab, and frozen fractional positions.",
        "real extracted-donor refusal on geometry drift, Oblique Lab, and frozen fractional positions.",
        "changelog corpus coverage",
    )


def main() -> int:
    patch_variable_name_precedence()
    patch_corpus_test()
    patch_manifest()
    patch_docs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
