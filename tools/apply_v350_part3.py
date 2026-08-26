from __future__ import annotations

import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)
test_app = read("tests/test_app.py")
test_app = test_app.replace("import base64\n", "", 1)
test_app = replace_once(
    test_app,
    'from fontblind_surgical import FontBlindError\n',
    'from fontblind_protocol import FONT_SET_MEDIA_TYPE, pack_font_set\n'
    'from fontblind_surgical import FontBlindError\n',
    label="test app protocol imports",
)
boundary_start = test_app.index("    def test_lab_boundaries_reject_invalid_angle_and_master_count(self) -> None:")
boundary_end = test_app.index(
    "\n    def test_lab_endpoints_build_anonymous_oblique_and_variable_packages",
    boundary_start,
)
new_boundary_test = r'''    def test_lab_boundaries_reject_invalid_angle_and_binary_framing(self) -> None:
        session = self.session()
        status, _, payload = self.request(
            "POST",
            "/api/lab/oblique",
            body=b"font",
            headers={
                "Content-Type": "application/octet-stream",
                "X-FontBlind-Session": session,
                "X-FontBlind-Angle": "30",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn(b"between 4 and 20", payload)

        status, _, payload = self.request(
            "POST",
            "/api/lab/variable",
            body=b"not-a-font-set",
            headers={
                "Content-Type": FONT_SET_MEDIA_TYPE,
                "X-FontBlind-Session": session,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Invalid local Lab request", payload)

        status, _, payload = self.request(
            "POST",
            "/api/lab/variable",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-FontBlind-Session": session,
            },
        )
        self.assertEqual(status, 415)
        self.assertIn(b"Invalid local Lab request", payload)

    def test_worker_gate_rejects_a_parallel_heavy_build(self) -> None:
        self.assertTrue(self.server.worker_gate.acquire(blocking=False))
        try:
            status, _, payload = self.request(
                "POST",
                "/api/process",
                body=b"font",
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-FontBlind-Session": self.session(),
                },
            )
        finally:
            self.server.worker_gate.release()
        self.assertEqual(status, 429)
        self.assertIn(b"Another local build", payload)
'''
test_app = test_app[:boundary_start] + new_boundary_test + test_app[boundary_end:]
test_app = replace_once(
    test_app,
    '''            request = json.dumps(
                {
                    "fonts": [
                        base64.b64encode(regular.read_bytes()).decode("ascii"),
                        base64.b64encode(bold.read_bytes()).decode("ascii"),
                    ]
                }
            ).encode("utf-8")
''',
    '''            request = pack_font_set([regular.read_bytes(), bold.read_bytes()])
''',
    label="test app valid variable envelope",
)
test_app = replace_once(
    test_app,
    '                    "Content-Type": "application/json",\n',
    '                    "Content-Type": FONT_SET_MEDIA_TYPE,\n',
    label="test app first variable media type",
)
test_app = replace_once(
    test_app,
    '''            body = json.dumps(
                {
                    "fonts": [
                        base64.b64encode(first.read_bytes()).decode("ascii"),
                        base64.b64encode(second.read_bytes()).decode("ascii"),
                    ]
                }
            ).encode("utf-8")
''',
    '''            body = pack_font_set([first.read_bytes(), second.read_bytes()])
''',
    label="test app diagnostic variable envelope",
)
test_app = replace_once(
    test_app,
    '                    "Content-Type": "application/json",\n',
    '                    "Content-Type": FONT_SET_MEDIA_TYPE,\n',
    label="test app second variable media type",
)
test_app = replace_once(
    test_app,
    '''            self.assertEqual([axis["tag"] for axis in variable["axes"]], ["wght"])
            self.assertNotIn("revealing", payload.decode("utf-8").casefold())
''',
    '''            self.assertEqual([axis["tag"] for axis in variable["axes"]], ["wght"])
            self.assertEqual(len(variable["masters"]), 2)
            self.assertEqual(sum(bool(master["default"]) for master in variable["masters"]), 1)
            self.assertNotIn("revealing", payload.decode("utf-8").casefold())
''',
    label="test app anonymous masters",
)
write("tests/test_app.py", test_app)

pyproject = read("pyproject.toml")
pyproject = replace_once(
    pyproject,
    '  "fontblind_lab",\n',
    '  "fontblind_lab",\n'
    '  "fontblind_mastermap",\n'
    '  "fontblind_protocol",\n',
    label="pyproject modules",
)
pyproject = replace_once(
    pyproject,
    '  "gauntlet",\n',
    '  "gauntlet",\n'
    '  "lab_gauntlet",\n',
    label="pyproject gauntlet module",
)
pyproject = replace_once(
    pyproject,
    '"share/fontblind/web" = ["web/index.html", "web/styles.css", "web/app.js", "web/favicon.svg"]\n',
    '"share/fontblind/web" = ["web/index.html", "web/styles.css", "web/lab-map.css", "web/app.js", "web/favicon.svg"]\n',
    label="pyproject web data",
)
write("pyproject.toml", pyproject)

write(
    ".github/workflows/tests.yml",
    r'''name: Tests

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    name: Python ${{ matrix.python }} · ${{ matrix.os }}
    runs-on: ${{ matrix.os }}
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-latest
            python: "3.10"
          - os: ubuntu-latest
            python: "3.12"
          - os: ubuntu-latest
            python: "3.13"
          - os: macos-latest
            python: "3.12"
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: ${{ matrix.python }}
          cache: pip
      - name: Install package
        run: python -m pip install .
      - name: Verify installed dependencies
        run: python -m pip check
      - name: Compile Python sources
        run: python -m compileall -q .
      - name: Check browser JavaScript syntax
        run: node --check web/app.js
      - name: Run tests
        run: python -m unittest discover -s tests -v

  package:
    name: Native macOS package
    runs-on: macos-latest
    timeout-minutes: 35
    needs: test
    steps:
      - uses: actions/checkout@v7
      - name: Build ad-hoc signed app
        run: ./build-fontblind-app.command --no-install
      - name: Verify package artifacts
        run: |
          test -s output/macos/FontBlind.zip
          test -s output/macos/FontBlind.zip.sha256
          unzip -tq output/macos/FontBlind.zip
''',
)

readme = read("README.md")
readme = replace_once(
    readme,
    '''The result screen exposes only neutral functional axis tags and bounds. Its sliders drive the generated WOFF2 directly with `font-variation-settings`; source names and paths never enter the page. Optical size, designed italic, synthetic weight/width generation from one drawing, and arbitrary custom axes still require authored masters or a separately reviewed research workflow.
''',
    '''The result screen exposes only neutral functional axis tags, bounds, and an anonymous master map. Each `M01`/`M02` pin is a keyboard-operable preset for one exact donor location; source names, paths, hashes, and family labels never enter the page. Variable donors travel in a compact length-prefixed binary envelope rather than base64 JSON, and the app permits one heavy build at a time to keep local memory bounded. Optical size, designed italic, synthetic weight/width generation from one drawing, and arbitrary custom axes still require authored masters or a separately reviewed research workflow.
''',
    label="README lab hardening",
)
readme = replace_once(
    readme,
    '''The native browser app is the product workflow. The command launchers remain source-level developer fallbacks.
''',
    '''The native browser app is the product workflow. The command launchers remain source-level developer fallbacks. For repeated Lab corpus checks, see `docs/LAB_HARDENING.md` and `lab_gauntlet.py`.
''',
    label="README gauntlet",
)
write("README.md", readme)

changelog = read("CHANGELOG.md")
changelog = replace_once(
    changelog,
    "# Changelog\n\n",
    "# Changelog\n\n"
    "## Unreleased\n\n"
    "- Replace Variable Lab's base64/JSON donor upload with a bounded binary font-set envelope.\n"
    "- Serialize heavy local builds and return explicit back-pressure instead of competing workers.\n"
    "- Require every cross-process and browser-visible proof to be a real, passing Boolean.\n"
    "- Add keyboard-operable anonymous master maps for `slnt`, `wght`, and `wdth` results.\n"
    "- Add a deterministic Lab corpus gauntlet, multi-version CI, package builds, and dependency updates.\n\n",
    label="changelog unreleased",
)
write("CHANGELOG.md", changelog)

lab_research = read("docs/VARIABLE_AND_ITALICS_LABS.md")
lab_research = replace_once(
    lab_research,
    '''- master map and axis sliders;
''',
    '''- anonymous master map and axis sliders;
''',
    label="lab research master map wording",
)
lab_research = replace_once(
    lab_research,
    '''2. Add Codex repair bundles, a glyph-level failure heatmap, and optional static-instance export.
''',
    '''2. Add axis-corner proof sheets, Codex repair bundles, a glyph-level failure heatmap, and optional static-instance export.
''',
    label="lab research proof roadmap",
)
write("docs/VARIABLE_AND_ITALICS_LABS.md", lab_research)

contributing = read("CONTRIBUTING.md")
contributing = replace_once(
    contributing,
    '''```bash
python -m unittest discover -s tests -v
```
''',
    '''```bash
python -m pip install .
python -m pip check
node --check web/app.js
python -m unittest discover -s tests -v
```

For repeatability checks against fonts you are entitled to test:

```bash
python lab_gauntlet.py oblique /path/to/corpus --loops 3
python lab_gauntlet.py variable Regular.ttf Bold.ttf --loops 3
```
''',
    label="contributing checks",
)
write("CONTRIBUTING.md", contributing)


for helper in ("tools/apply_v350_part1.py", "tools/apply_v350_part2.py", "tools/apply_v350_part3.py"):
    (ROOT / helper).unlink(missing_ok=True)
(ROOT / ".github/workflows/branch-autofix.yml").unlink(missing_ok=True)
