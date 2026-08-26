# Changelog

## 3.5.0 — 2026-08-26

- Centralize the release version so every CLI, engine, package, and report identifies the same build.
- Replace Variable Lab's base64/JSON donor upload with a bounded binary font-set envelope.
- Stream every browser upload into anonymous descriptors in 1 MB chunks instead of buffering whole fonts in browser and server memory.
- Bound stalled or pathological upload framing, and stream completed downloads without whole-file server buffers.
- Serialize heavy local builds and return explicit back-pressure instead of competing workers.
- Require every cross-process and browser-visible proof to be a real, passing Boolean.
- Add keyboard-operable anonymous master maps for `slnt`, `wght`, and `wdth` results.
- Add deterministic browser proof grids for axis endpoints, defaults, midpoints, and two-axis cross-products.
- Add a deterministic Lab corpus gauntlet, multi-version CI, package builds, and dependency updates.

## 3.4.0 — 2026-08-25

- Ship Blind, Oblique Lab, and Variable Lab in one native macOS app.
- Export native OpenType, full WOFF2, generic CSS, and ZIP packages locally.
- Add static mechanical Oblique and registered `slnt` variable outputs.
- Add donor-driven `wght`, `wdth`, and independent two-axis variable builds.
- Enforce the zero-ID exit contract and fail closed on unsupported identity carriers.
