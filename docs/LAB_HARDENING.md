# Lab hardening and inspection

This document describes the post-3.4 Lab architecture. It is a product contract, not a claim that mechanically generated type is authored type design.

## Why this pass exists

The first public Lab proved that deterministic Oblique and donor-built variable outputs were possible. It still had three weak points:

1. Variable donors travelled through the browser as base64 inside JSON, multiplying peak memory use.
2. The threaded local server could start several CPU- and memory-heavy workers at once.
3. A successful variable build exposed sliders, but not the actual anonymous master locations that justified the designspace.

The hardening pass fixes those foundations before adding speculative generation.

## Variable donor envelope

Variable Lab now sends one binary `application/vnd.fontblind.font-set` body:

- eight-byte `FBLAB1` signature;
- one unsigned byte containing a donor count from 2 through 12;
- one big-endian unsigned 32-bit length per donor;
- raw donor bytes in the same order.

The server validates the declared body length, count, every donor length, aggregate length, signature, and exact framing before compilation. It rejects malformed, interrupted, oversized, or trailing data. The envelope removes base64 expansion and avoids the second full JSON string copy in the browser.

## One heavy worker at a time

Static files and completed downloads remain concurrent. Font processing is serialized with a non-blocking local worker gate. A second build receives HTTP 429 instead of quietly competing for hundreds of megabytes of memory. This is deliberate back-pressure, not a queue whose stale work can surprise the user.

## Proof must be true

Worker results cross a process boundary. Every proof value must now be an actual Boolean and every proof must be `true` before the parent stores or exposes a job. The browser repeats that validation before it loads a preview or reveals a download. A malformed or failed proof cannot sit underneath a green “PASS” heading.

## Anonymous master map

Successful `slnt`, `wght`, and `wdth` builds expose functional master records only:

```json
{
  "id": "M02",
  "location": {"wght": 700.0, "wdth": 100.0},
  "default": false
}
```

No filename, family name, path, hash, or source label crosses into the page. Pins are keyboard-operable presets: selecting one moves every registered axis to that exact donor location. Sliders still inspect the continuum between masters.

## Gauntlet

`lab_gauntlet.py` repeats complete Lab builds and compares package digests.

```bash
python lab_gauntlet.py oblique /path/to/corpus --angles 4 12 20 --loops 3 --workers 4
python lab_gauntlet.py variable Regular.ttf Bold.ttf --loops 3
```

The tool is intentionally explicit about variable donor sets. Guessing families from filenames would reproduce the exact source-identity coupling the product avoids.

## Next, in order

1. Sample axis corners and deterministic interior points, then produce a local visual proof sheet.
2. Add optional Fontspector profiles and Diffenator-compatible proof export without making either a runtime dependency.
3. Add glyph-level incompatibility heatmaps and repair bundles.
4. Add static-instance export with explicit selected coordinates.
5. Keep designed Italic, optical-size authoring, and model proposals behind reviewable research lanes.
