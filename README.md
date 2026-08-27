# FontBlind

[![Verification](https://github.com/bomkino/fontblind/actions/workflows/tests.yml/badge.svg)](https://github.com/bomkino/fontblind/actions/workflows/tests.yml)

FontBlind is a local font-transformation and packaging workshop. It has three workbenches:

- **Blind** converts one TTF or OTF into a faithful zero-ID native font, full WOFF2, CSS, and ZIP.
- **Oblique Lab** makes a declared mechanical Oblique or a live upright-to-Oblique `slnt` variable font. It never presents mechanical slanting as a designed Italic.
- **Variable Lab** builds verified `wght`, `wdth`, or independent two-axis variable fonts from compatible static TrueType donors. It refuses missing or incompatible designspace evidence.

Generated `slnt`, `wght`, and `wdth` results can freeze any verified position into a static TTF, WOFF2, exact `@font-face` CSS, and deterministic ZIP.

FontBlind does **not** provide typography review, Candidate comparison, design-system planning, or handoff documents. Those belong to the separate Font Previewer product; no application code or product model is shared.

## Current status

- Canonical branch: `main`
- Current source version: **3.7.0 — unreleased**
- Latest published release: **v3.4.0**
- macOS source/package target: macOS 13+, Apple Silicon
- Linux source/package target: current Garuda Linux on its rolling Arch base, KDE Plasma 6, Wayland-first, x86_64 Dell G7
- Linux posture: **experimental and physically unverified** until the documented Dell G7 journey passes

The published v3.4.0 download does not contain the unreleased 3.5–3.7 source work. Build current source yourself or use exact-SHA CI artifacts. See [`docs/maintenance/REPOSITORY_STATE.md`](docs/maintenance/REPOSITORY_STATE.md).

## Install published macOS release

Download the published build from [GitHub Releases](https://github.com/bomkino/fontblind/releases/latest), verify its checksum, unzip it, and move `FontBlind.app` to Applications. The published app is ad-hoc signed, not Apple Developer ID signed or notarized. macOS may require Control-click → **Open**. No notarisation, stapling, or Gatekeeper acceptance is claimed.

## Experimental Garuda package

Current source produces:

```text
fontblind-bin-3.7.0-1-x86_64.pkg.tar.zst
fontblind-bin-3.7.0-1-x86_64.pkg.tar.zst.sha256
```

Verify, install, and remove it with:

```bash
sha256sum -c fontblind-bin-3.7.0-1-x86_64.pkg.tar.zst.sha256
sudo pacman -U ./fontblind-bin-3.7.0-1-x86_64.pkg.tar.zst
sudo pacman -Rns fontblind-bin
```

The package adds one KDE application entry and `/usr/bin/fontblind`. It opens the reviewed interface through KDE’s configured default browser while all font processing remains in FontBlind’s private loopback runtime. CI verifies the package in a current Arch container; it does not prove the real Dell firmware, Garuda installation, KDE session, configured browser, suspend/wake behaviour, file pickers, or attended accessibility quality. Follow [`docs/LINUX_ACCEPTANCE.md`](docs/LINUX_ACCEPTANCE.md) before making a hardware-specific support claim.

## Build and verify

Install the locked source dependencies, then run the public gate:

```bash
python -m pip install .
python -m pip check
python -m compileall -q .
for file in web/*.js; do node --check "$file"; done
node --test tests/*.test.cjs
python -m unittest discover -s tests -v
```

Build the macOS package on macOS 13+ with Apple command-line developer tools:

```bash
./build-fontblind-app.command --no-install
```

Build the experimental Garuda/Arch package as a normal user on current x86_64 Garuda or Arch:

```bash
python3 tools/fetch_corpus.py
FONTBLIND_CORPUS_DIR="$PWD/tests/corpus/cache" ./build-fontblind-linux.sh
```

The representative corpus is fetched separately and never bundled. Every corpus file is open-licensed and pinned by upstream commit, byte size, and SHA-256. See [`tests/corpus/README.md`](tests/corpus/README.md).

## Use

Every successful workbench emits four generic, tool-specific downloads:

- native TTF or OTF;
- full WOFF2;
- generic `@font-face` CSS with no `local(...)` lookup;
- one ZIP containing all three.

For generated variable results, move the live controls to any verified position and choose **Freeze current position**. FontBlind records the exact coordinates, reruns the static exit contract, loads the frozen WOFF2 in the browser, and only then exposes the static package. Moving an axis invalidates the prior frozen result instead of relabelling stale bytes.

The app owns an ephemeral loopback-only worker. Uploaded bytes are held in anonymous file descriptors without source filenames or directory entries. If the app dies, the child worker exits and the kernel closes those descriptors. Jobs disappear on reset, after two hours, or when the app closes.

To keep ordinary machines responsive, FontBlind permits one heavy build, one static export per generated parent, eight retained verified jobs, 768 MiB of retained artifact bytes, and two concurrent sealed download snapshots. New work receives explicit local back-pressure instead of hidden worker or buffer growth.

On Garuda, **Quit FontBlind** uses the private session to stop the local service and remove temporary jobs. The page does not claim closure until the loopback service disappears. A second launch reopens the existing local app rather than starting another server.

## Current zero-ID contract

- Replaces every OpenType name record with generic values while retaining neutral functional labels such as Bold, Italic, Oblique, Weight, Width, and numbered variable instances.
- Clears source vendor, embedding flags, family classification, PANOSE, timestamps, and revision labels.
- Removes PostScript glyph names, CFF identity strings/IDs, known source/editor/licence/debug/signature tables, and known image metadata.
- Rejects unknown/private tables instead of preserving a possible hidden label.
- Rejects embedded SVG/bitmap artwork until those payloads can be exhaustively rewritten without changing rendering.
- Rejects CID-keyed CFF because its functional Registry/Ordering strings cannot yet be rewritten safely.
- Rejects fonts without the `OS/2` structure required by modern browser sanitizers.
- Scans every retained compiled output table for complete original identity strings in OpenType and legacy name encodings.
- Treats custom/private name IDs as possible identity, normalizes Unicode and case, and refuses source labels too short to prove safely.
- Uses generic output paths, downloads, CSS, DOM copy, and silent request logs.
- Never produces or exposes source hashes, mappings, manifests, reports, or original filenames.
- Rejects generated SFNT padding, WOFF2 metadata/private blocks, ZIP gaps, unreferenced records, and bytes outside declared container extents.

“Zero-ID” means no retained original identity or embedding-policy labels. It cannot make unchanged glyph drawings visually unrecognisable: that would contradict the fidelity requirement.

The native output deliberately has no per-artifact identifier. Generic Bold/Italic/Oblique/width styles can coexist as one anonymous family, but two unrelated outputs with the same generic style cannot be installed simultaneously without creating an identifier. Remove one before installing the other.

## Blind fidelity gates

- Same SFNT/outline flavour, glyph count/order, cmap-to-GID mapping, metrics, layout, variations, hints, and retained runtime tables under the clone contract.
- Every non-policy table remains byte-identical.
- Three deterministic reconstruction rounds.
- Source/output HarfBuzz shaping comparison across Latin, Arabic, Devanagari, Hebrew, Thai, marks, ligatures, and numerals.
- Full WOFF2 encode/decode contract check, including per-glyph TrueType instructions.
- Raw WOFF2 `FontFace` load before the browser reveals downloads.
- Parent-side inspection of retained files rather than worker-reported descriptors.
- Atomic package commit; a failed gate keeps no output.

## Lab boundaries

Oblique Lab transforms TrueType outlines, composites, and GPOS anchors; removes invalidated TrueType hints; updates functional metadata; checks browser packaging; and then applies the same zero-ID exit gate. Static and registered `slnt`-axis outputs are useful mechanical slanting—not newly drawn italic letterforms.

Variable Lab infers registered coordinates from `OS/2.usWeightClass` and `OS/2.usWidthClass`, requires unique coordinate tuples, and preflights units-per-em, glyph order, cmap, outline topology, and interpolation compatibility. A two-axis build must contain a real default plus independent weight and width extremes; two diagonal donors are rejected because they only prove a coupled change. Every donor location is reinstanced and checked against its original geometry, metrics, anchors, and shaping before output is committed.

The result screen exposes only neutral functional axis tags, bounds, and an anonymous master map. Each `M01`/`M02` pin is a keyboard-operable preset for one exact donor location; source names, paths, hashes, and family labels never enter the page. A deterministic proof grid renders min, default, max, and necessary midpoint combinations through the generated WOFF2, marks exact masters, and lets every proof tile drive the live axis controls.

Browser uploads retain only a four-byte signature probe; complete fonts then stream into anonymous local descriptors in bounded 1 MB chunks with read and total-time ceilings. Downloads stream back in the same bounded chunks rather than becoming a second full in-memory copy. Variable donors travel in a compact length-prefixed binary envelope rather than base64 JSON.

Optical size, designed italic, synthetic weight/width generation from one drawing, and arbitrary custom axes still require authored masters or a separately reviewed research workflow.

## Static-instance proof

A frozen position is not accepted merely because FontTools emitted a static file. FontBlind:

1. builds the package in an unpublished staging directory;
2. independently re-instances the generated variable font at the requested coordinates with a different optimisation path;
3. compares glyph order, cmap, outlines, horizontal and vertical metrics, anchors, OpenType layout, MVAR-selected values, and HarfBuzz shaping;
4. repairs and checks static weight, width, Bold/Regular/Oblique bits, names, `post.italicAngle`, and caret slope;
5. removes and recursively rejects variation tables, GDEF variation stores, GSUB/GPOS feature variations, and VariationIndex devices;
6. verifies the native font, decoded WOFF2, exact CSS, deterministic ZIP, zero-ID audit, and browser load;
7. publishes only after every check passes.

A failed replacement leaves the last verified static package intact. Deleting or expiring the generated parent invalidates its static child.

## Browser and accessibility boundary

The browser displays only exact reviewed API envelopes. Unknown backend text is replaced with generic workbench copy so future exception messages, filenames, paths, or donor labels cannot enter the DOM.

Workbench navigation exposes tabs and tabpanels, roving keyboard focus, a skip link, described dropzones, live processing and result regions, explicit busy states, focus handoff after success or refusal, non-colour proof announcements, reduced-motion support, and responsive reflow. Automated tests cover semantics and races. They do not establish attended VoiceOver quality; that remains a separate human gate in [`docs/ACCESSIBILITY_ACCEPTANCE.md`](docs/ACCESSIBILITY_ACCEPTANCE.md).

## Release validation

The canonical workflow checks the exact PR head or `main` commit. It runs Python 3.10, 3.12, and 3.13 on Ubuntu; Python 3.12 on macOS; browser tests; the complete Python suite; the pinned corpus on Ubuntu and macOS; a native ad-hoc-signed macOS package; and the experimental Garuda package inside a current Arch container. Artifacts include the exact verified commit SHA.

The frozen-runtime gauntlet lives in `release_gauntlet.py`; package builders orchestrate it rather than hiding product checks in shell. Publication is manual, guarded, and separate from verification. See [`docs/maintenance/RELEASE_POLICY.md`](docs/maintenance/RELEASE_POLICY.md).

## Licence and font files

FontBlind is MIT-licensed. That licence covers this software, not fonts processed with it. Only process font files you are entitled to modify. No proprietary, client, system, or mystery font binary belongs in this repository or its release artifacts.
