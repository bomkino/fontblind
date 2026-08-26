# FontBlind

[![Tests](https://github.com/bomkino/fontblind/actions/workflows/tests.yml/badge.svg)](https://github.com/bomkino/fontblind/actions/workflows/tests.yml)

FontBlind is one local-only macOS app with three tools:

- **Blind** takes one TTF or OTF and makes a faithful zero-ID native font, full WOFF2, clean CSS, and ZIP.
- **Oblique Lab** takes one upright TrueType font and makes either a declared static Oblique or a live upright-to-Oblique `slnt` variable font at 4–20 degrees. Neither output pretends to be a designed Italic.
- **Variable Lab** takes 2–12 compatible static TrueType donors and builds a donor-driven `wght`, `wdth`, or independent two-axis variable font. It refuses incompatible or merely coupled families instead of inventing missing drawings.

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

The app owns an ephemeral loopback-only worker. Uploaded bytes are held in anonymous file descriptors without source filenames or directory entries. If the app dies, the child worker exits and the kernel closes those descriptors. Jobs disappear on reset, after two hours, or when the app closes.

## Build the macOS app

```bash
./build-fontblind-app.command
```

The builder creates `output/macos/FontBlind.zip`, ad-hoc signs the build, verifies the nested code signatures, and installs `/Applications/FontBlind.app` through a recoverable staging path. Pass `--no-install` to package without changing the installed app. It requires macOS 13 or newer and Apple command-line developer tools. The build is not Developer ID signed or notarized.

## Current zero-ID contract

- Replaces every OpenType name record with generic values while retaining neutral functional labels such as Bold, Italic, Weight, Width, and numbered variable instances.
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

“Zero-ID” means no retained original identity or embedding-policy labels. It cannot make unchanged glyph drawings visually unrecognisable: that would contradict the fidelity requirement.

The native output deliberately has no per-artifact identifier. Generic Bold/Italic/width styles can coexist as one anonymous family, but two unrelated outputs with the same generic style cannot be installed simultaneously without creating an identifier. Remove one before installing the other.

## Blind fidelity gates

- Same SFNT/outline flavour, glyph count/order, cmap-to-GID mapping, metrics, layout, variations, hints, and retained runtime tables under the clone contract.
- Every non-policy table remains byte-identical.
- Three deterministic reconstruction rounds.
- Source/output HarfBuzz shaping comparison across Latin, Arabic, Devanagari, Hebrew, Thai, marks, ligatures, and numerals.
- Full WOFF2 encode/decode contract check, including per-glyph TrueType instructions.
- Raw WOFF2 `FontFace` load before the browser reveals downloads.
- Atomic package commit; a failed gate keeps no output.

## Lab boundaries

Oblique Lab transforms TrueType outlines, composites, and GPOS anchors; removes invalidated TrueType hints; updates functional metadata; checks browser packaging; and then applies the same zero-ID exit gate. Static and registered `slnt`-axis outputs are useful mechanical slanting—not newly drawn italic letterforms.

Variable Lab infers registered coordinates from `OS/2.usWeightClass` and `OS/2.usWidthClass`, requires unique coordinate tuples, and preflights units-per-em, glyph order, cmap, outline topology, and interpolation compatibility. A two-axis build must contain a real default plus independent weight and width extremes; two diagonal donors are rejected because they only prove a coupled change. Every donor location is reinstanced and checked against its original geometry, metrics, anchors, and shaping before output is committed.

The result screen exposes only neutral functional axis tags, bounds, and an anonymous master map. Each `M01`/`M02` pin is a keyboard-operable preset for one exact donor location; source names, paths, hashes, and family labels never enter the page. A deterministic proof grid renders min, default, max, and necessary midpoint combinations through the generated WOFF2, marks exact masters, and lets every proof tile drive the live axis controls. Browser uploads retain only a four-byte signature probe; complete fonts then stream into anonymous local descriptors in bounded 1 MB chunks. Variable donors travel in a compact length-prefixed binary envelope rather than base64 JSON, and the app permits one heavy build at a time to keep local memory bounded. Optical size, designed italic, synthetic weight/width generation from one drawing, and arbitrary custom axes still require authored masters or a separately reviewed research workflow.

## Licence and font files

FontBlind is MIT-licensed. That licence covers this software, not fonts processed with it. Only process font files you are entitled to modify.

The native browser app is the product workflow. The command launchers remain source-level developer fallbacks. For repeated Lab corpus checks, see `docs/LAB_HARDENING.md` and `lab_gauntlet.py`.
