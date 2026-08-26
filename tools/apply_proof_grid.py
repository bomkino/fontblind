from __future__ import annotations

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


app_js = read("web/app.js")
app_js = replace_once(
    app_js,
    '''  const controls = new Map();
  const pins = new Map();
  axisValues.set(name, values);
''',
    '''  const controls = new Map();
  const pins = new Map();
  let proofController = null;
  axisValues.set(name, values);
''',
    label="proof controller state",
)
app_js = replace_once(
    app_js,
    '''      if (active) entry.button.dataset.activeMaster = id;
      else delete entry.button.dataset.activeMaster;
    }
  }

  function selectMaster(master) {
    for (const [tag, value] of Object.entries(master.location)) {
''',
    '''      if (active) entry.button.dataset.activeMaster = id;
      else delete entry.button.dataset.activeMaster;
    }
    if (proofController) proofController.sync(values);
  }

  function selectLocation(location) {
    for (const [tag, value] of Object.entries(location)) {
''',
    label="shared proof location selector",
)
app_js = replace_once(
    app_js,
    '      pin.addEventListener("click", () => selectMaster(master));\n',
    '      pin.addEventListener("click", () => selectLocation(master.location));\n',
    label="master pin selector",
)
app_js = replace_once(
    app_js,
    '''    panel.append(row);
  }
  panel.hidden = false;
  applyAxisValues(name);
  syncPins();
}
''',
    '''    panel.append(row);
  }

  if (window.FontBlindProof && axes.length <= 2) {
    proofController = window.FontBlindProof.render(panel, axes, masters, {
      fontFamily: tools.get(name).specimen.style.fontFamily,
      onSelect: selectLocation
    });
  }
  panel.hidden = false;
  applyAxisValues(name);
  syncPins();
}
''',
    label="proof grid render hook",
)
write("web/app.js", app_js)

index = read("web/index.html")
index = replace_once(
    index,
    '    <script src="/app.js" defer></script>\n',
    '    <script src="/lab-proof.js" defer></script>\n    <script src="/app.js" defer></script>\n',
    label="proof script order",
)
write("web/index.html", index)

app = read("fontblind_app.py")
app = replace_once(
    app,
    '            "/app.js": (WEB_ROOT / "app.js", "text/javascript; charset=utf-8"),\n',
    '            "/app.js": (WEB_ROOT / "app.js", "text/javascript; charset=utf-8"),\n'
    '            "/lab-proof.js": (WEB_ROOT / "lab-proof.js", "text/javascript; charset=utf-8"),\n',
    label="proof static asset",
)
write("fontblind_app.py", app)

pyproject = read("pyproject.toml")
pyproject = replace_once(
    pyproject,
    '"share/fontblind/web" = ["web/index.html", "web/styles.css", "web/lab-map.css", "web/app.js", "web/favicon.svg"]\n',
    '"share/fontblind/web" = ["web/index.html", "web/styles.css", "web/lab-map.css", "web/lab-proof.js", "web/app.js", "web/favicon.svg"]\n',
    label="proof package data",
)
write("pyproject.toml", pyproject)

css = read("web/lab-map.css")
css += '''

.designspace-proof-shell {
  margin: 1.7rem 0 .35rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--ink);
}

.designspace-proof-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: .85rem;
}

.designspace-proof-heading strong {
  font-size: .69rem;
  font-weight: 900;
  letter-spacing: .13em;
  text-transform: uppercase;
}

.designspace-proof-heading span {
  color: var(--quiet);
  font-family: var(--serif);
  font-size: .82rem;
  line-height: 1.25;
  text-align: right;
}

.designspace-proof-grid {
  display: grid;
  grid-template-columns: repeat(var(--proof-columns, 3), minmax(0, 1fr));
  gap: .6rem;
}

.proof-point-card {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 10.5rem;
  flex-direction: column;
  gap: .65rem;
  padding: .8rem;
  overflow: hidden;
  border: 1px solid var(--rule);
  color: var(--ink);
  background: var(--paper-deep);
  text-align: left;
  cursor: pointer;
  transition: transform 120ms ease, box-shadow 120ms ease, border-color 120ms ease;
}

.proof-point-card:hover {
  z-index: 2;
  border-color: var(--ink);
  transform: translateY(-2px);
  box-shadow: 3px 3px 0 var(--ink);
}

.proof-point-card:focus-visible {
  z-index: 3;
  outline: 3px solid var(--active-accent);
  outline-offset: 2px;
}

.proof-point-card.is-master {
  border-color: var(--ink);
}

.proof-point-card.is-active {
  background: var(--active-accent);
  box-shadow: inset 0 0 0 2px var(--ink);
}

.proof-point-card.is-active::after {
  content: "LIVE";
  position: absolute;
  right: .6rem;
  bottom: .55rem;
  font-size: .5rem;
  font-weight: 900;
  letter-spacing: .12em;
}

.proof-point-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .5rem;
  font-size: .55rem;
  font-weight: 900;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.proof-point-meta code {
  font-size: .62rem;
}

.proof-point-meta small {
  overflow: hidden;
  color: var(--quiet);
  font-size: .5rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.proof-point-card.is-master .proof-point-meta small {
  color: var(--ink);
}

.proof-point-specimen {
  display: block;
  margin: auto 0;
  overflow: hidden;
  font-size: clamp(1.05rem, 2.2vw, 1.75rem);
  font-kerning: normal;
  font-optical-sizing: auto;
  line-height: .98;
  white-space: pre;
}

.proof-point-coordinates {
  padding-top: .55rem;
  border-top: 1px solid var(--rule);
  font-family: var(--mono);
  font-size: .62rem;
  font-weight: 800;
  line-height: 1.2;
}

.proof-point-roles {
  padding-right: 2.6rem;
  color: var(--quiet);
  font-size: .48rem;
  font-weight: 900;
  letter-spacing: .08em;
  line-height: 1.25;
}

@media (max-width: 860px) {
  .designspace-proof-grid.is-2d {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 680px) {
  .designspace-proof-heading {
    display: block;
  }

  .designspace-proof-heading span {
    display: block;
    margin-top: .35rem;
    text-align: left;
  }

  .designspace-proof-grid,
  .designspace-proof-grid.is-2d {
    grid-template-columns: 1fr;
  }

  .proof-point-card {
    min-height: 8.8rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  .proof-point-card {
    transition: none;
  }
}
'''
write("web/lab-map.css", css)

readme = read("README.md")
readme = replace_once(
    readme,
    '''The result screen exposes only neutral functional axis tags, bounds, and an anonymous master map. Each `M01`/`M02` pin is a keyboard-operable preset for one exact donor location; source names, paths, hashes, and family labels never enter the page. Variable donors travel in a compact length-prefixed binary envelope rather than base64 JSON, and the app permits one heavy build at a time to keep local memory bounded. Optical size, designed italic, synthetic weight/width generation from one drawing, and arbitrary custom axes still require authored masters or a separately reviewed research workflow.
''',
    '''The result screen exposes only neutral functional axis tags, bounds, and an anonymous master map. Each `M01`/`M02` pin is a keyboard-operable preset for one exact donor location; source names, paths, hashes, and family labels never enter the page. A deterministic proof grid renders min, default, max, and necessary midpoint combinations through the generated WOFF2, marks exact masters, and lets every proof tile drive the live axis controls. Variable donors travel in a compact length-prefixed binary envelope rather than base64 JSON, and the app permits one heavy build at a time to keep local memory bounded. Optical size, designed italic, synthetic weight/width generation from one drawing, and arbitrary custom axes still require authored masters or a separately reviewed research workflow.
''',
    label="README proof grid",
)
write("README.md", readme)

changelog = read("CHANGELOG.md")
changelog = replace_once(
    changelog,
    '- Add keyboard-operable anonymous master maps for `slnt`, `wght`, and `wdth` results.\n',
    '- Add keyboard-operable anonymous master maps for `slnt`, `wght`, and `wdth` results.\n'
    '- Add deterministic browser proof grids for axis endpoints, defaults, midpoints, and two-axis cross-products.\n',
    label="changelog proof grid",
)
write("CHANGELOG.md", changelog)

docs = read("docs/LAB_HARDENING.md")
docs = replace_once(
    docs,
    '''No filename, family name, path, hash, or source label crosses into the page. Pins are keyboard-operable presets: selecting one moves every registered axis to that exact donor location. Sliders still inspect the continuum between masters.

## Gauntlet
''',
    '''No filename, family name, path, hash, or source label crosses into the page. Pins are keyboard-operable presets: selecting one moves every registered axis to that exact donor location. Sliders still inspect the continuum between masters.

## Deterministic designspace proof grid

The browser derives a small proof matrix from public axis bounds only. A one-axis build renders three distinct locations when possible: min, default, max, or a midpoint when default and one endpoint coincide. A two-axis build renders the Cartesian product of those samples, capped at nine tiles. Each tile uses the generated WOFF2, identifies exact anonymous masters, and is a keyboard-operable preset for the live controls.

The proof module is separate from the application controller and exports pure coordinate-generation functions. Node tests cover ordering, merged endpoint roles, master recognition, source-identity absence, and malformed designspaces. It does not invent new masters or claim that a visual sample replaces outline and shaping verification.

## Gauntlet
''',
    label="docs proof section",
)
docs = replace_once(
    docs,
    '''## Next, in order

1. Sample axis corners and deterministic interior points, then produce a local visual proof sheet.
2. Add optional Fontspector profiles and Diffenator-compatible proof export without making either a runtime dependency.
3. Add glyph-level incompatibility heatmaps and repair bundles.
4. Add static-instance export with explicit selected coordinates.
5. Keep designed Italic, optical-size authoring, and model proposals behind reviewable research lanes.
''',
    '''## Next, in order

1. Add static-instance export with explicit selected coordinates and the same zero-ID exit checks.
2. Add optional Fontspector profiles and Diffenator-compatible proof export without making either a runtime dependency.
3. Add glyph-level incompatibility heatmaps and repair bundles.
4. Add downloadable local proof sheets after their privacy and font-embedding contract is explicit.
5. Keep designed Italic, optical-size authoring, and model proposals behind reviewable research lanes.
''',
    label="docs next steps",
)
write("docs/LAB_HARDENING.md", docs)

contributing = read("CONTRIBUTING.md")
contributing = replace_once(
    contributing,
    '''node --check web/app.js
python -m unittest discover -s tests -v
''',
    '''node --check web/app.js
node --check web/lab-proof.js
node --test tests/lab-proof.test.cjs
python -m unittest discover -s tests -v
''',
    label="contributing proof checks",
)
write("CONTRIBUTING.md", contributing)
