# FontBlind release corpus

This directory contains a manifest, not committed font binaries.

`manifest.json` pins six open-licensed upstream fonts by repository commit, path, byte size, and SHA-256. The corpus is fetched into the ignored `tests/corpus/cache/` directory:

```sh
python tools/fetch_corpus.py
python tools/fetch_corpus.py --verify-only
```

The fetcher accepts only reviewed `raw.githubusercontent.com` URLs, streams into an anonymous temporary file, enforces the pinned maximum size, verifies SHA-256, and atomically replaces the cache entry. A changed upstream file therefore fails rather than silently becoming new test input.

## Coverage

| Asset | Purpose |
| --- | --- |
| ABeeZee Regular | Static TrueType, composites, hinting, Latin shaping, Oblique Lab |
| Source Code Pro Regular | Static non-CID CFF1 and PostScript metadata rewriting |
| Noto Sans Arabic | Arabic cursive shaping, marks, GDEF/GPOS/GSUB, `wdth` + `wght` |
| Noto Sans Devanagari | Reordering, conjuncts, marks, `wdth` + `wght` |
| Noto Sans Hebrew | RTL shaping, combining marks, real donor extraction and Variable Lab |
| Noto Sans Thai | Thai clusters, positioning, `wdth` + `wght` |

The tests exercise FontBlind's strict input policy and full native/WOFF2/CSS/ZIP pipeline for every asset. Selected variable fonts are also frozen at real interior positions. The Hebrew variable font supplies real compatible static donors for a donor-built two-axis Variable Lab pass.

## Licensing

Every corpus asset is licensed under the SIL Open Font License 1.1. The exact upstream licence URL is recorded next to each font in `manifest.json`. The files are downloaded only for tests and are not embedded in the FontBlind application or release ZIP.

## Updating the corpus

Do not replace a hash merely because upstream changed. An update requires:

1. A reviewed immutable upstream commit.
2. A licence check.
3. A fresh strict-policy and full-pipeline probe.
4. Updated size and SHA-256.
5. A documented reason for the replacement.
6. A green release corpus job on Linux and macOS.
