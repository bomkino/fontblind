# FontBlind

[![Tests](https://github.com/bomkino/fontblind/actions/workflows/tests.yml/badge.svg)](https://github.com/bomkino/fontblind/actions/workflows/tests.yml)

FontBlind is one local-only macOS app with three tools:

- **Blind** takes one TTF or OTF and makes a faithful zero-ID native font, full WOFF2, clean CSS, and ZIP.
- **Oblique Lab** takes one upright TrueType font and makes either a declared static Oblique or a live upright-to-Oblique `slnt` variable font at 4–20 degrees. Neither output pretends to be a designed Italic.
- **Variable Lab** takes 2–12 compatible static TrueType donors and builds a donor-driven `wght`, `wdth`, or independent two-axis variable font. It refuses incompatible or merely coupled families instead of inventing missing drawings.

Generated `slnt`, `wght`, and `wdth` fonts can also freeze any verified position into a static TTF, WOFF2, exact `@font-face` CSS, and deterministic ZIP. Static export uses only FontBlind’s generated zero-ID variable font; original donors are not reopened.

## Download

Download the current Apple-silicon build from [GitHub Releases](https://github.com/bomkino/fontblind/releases/latest), unzip it, and move `FontBlind.app` to Applications. It requires macOS 13 or newer.

The downloadable app is ad-hoc signed and not notarized. On first launch, macOS may require Control-clicking the app and choosing **Open**. You can also build it from source.

## Use

Open `/Applications/FontBlind.app`. There is no terminal step, account, cloud API, or external network request.

Every successful tool emits four generic, tool-specific downloads:

- native TTF or OTF;
- full WOFF2;
- generic `@font-face` CSS with no `local(...)` lookup;
- one ZIP containing all three.

For generated variable results, move the live axis controls to any verified position and choose **Freeze current position**. FontBlind records the exact coordinates, reruns the static exit contract, loads the frozen WOFF2 in the browser, and then exposes the static package. Moving an axis invalidates the old frozen result rather than silently relabelling stale bytes.

The app owns an ephemeral loopback-only worker. Uploaded bytes are held in anonymous file descriptors without source filenames or directory entries. If the app dies, the child worker exits and the kernel closes those descriptors. Jobs disappear on reset, after two hours, or when the app closes.

To keep ordinary Macs responsive, FontBlind allows one heavy build at a time, one static export per generated parent, eight retained verified jobs, 768 MiB of retained artifact bytes, and two concurrent sealed download snapshots. New work receives explicit local back-pressure rather than accumulating hidden workers or buffers.

## Build the macOS app

```bash
./build-fontblind-app.command
```

The builder creates `output/macos/FontBlind.zip`, ad-hoc signs the build, verifies nested code signatures, launches the exact frozen server, runs every product lane against it, and installs `/Applications/FontBlind.app` through a recoverable staging path. Pass `--no-install` to package without changing the installed app. It requires macOS 13 or newer and Apple command-line developer tools. The build is not Developer ID signed or notarized.

The representative release corpus is fetched separately and is not bundled with the app:

```bash
python3 tools/fetch_corpus.py
FONTBLIND_CORPUS_DIR="$PWD/tests/corpus/cache" ./build-fontblind-app.command --no-install
```

Every corpus file is pinned by immutable upstream commit, byte size, and SHA-256. See `tests/corpus/README.md`.

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
- Parent-side inspection of the actual retained files rather than worker-reported descriptors.
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

Workbench navigation exposes tabs and tabpanels, roving keyboard focus, a skip link, described dropzones, live processing and result regions, explicit busy states, focus handoff after success or refusal, non-colour proof announcements, reduced-motion support, and responsive reflow. Automated tests cover these semantics and race conditions. A final human VoiceOver listening pass remains useful before a public release because automated accessibility trees cannot judge whether spoken timing and phrasing feel coherent.

## Release validation

The permanent release gate runs on Python 3.10, 3.12, and 3.13 on Ubuntu, Python 3.12 on macOS, and a separately built ad-hoc-signed macOS application. It also runs a pinned open-licensed corpus on Ubuntu and macOS, then processes the same corpus through the exact frozen server before the bundle is signed.

Corpus coverage includes static TrueType, non-CID CFF1, Arabic cursive shaping, Devanagari reordering and conjuncts, Hebrew marks and right-to-left shaping, Thai positioning, real two-axis variable fonts, real extracted-donor refusal on geometry drift, Oblique Lab, and fractional static positions.

The exact frozen-runtime product gauntlet lives in `release_gauntlet.py`; `build-fontblind-app.command` orchestrates it rather than hiding test logic inside shell.

## Licence and font files

FontBlind is MIT-licensed. That licence covers this software, not fonts processed with it. Only process font files you are entitled to modify.

The native browser app is the product workflow. Command launchers remain source-level developer fallbacks. For Lab internals and the release corpus, see `docs/LAB_HARDENING.md`, `lab_gauntlet.py`, and `tests/corpus/README.md`.
