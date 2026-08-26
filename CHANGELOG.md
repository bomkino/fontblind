# Changelog

## 3.7.0 — Unreleased

- Add a Garuda Linux / Arch / KDE x86_64 edition with a pacman-native `pkg.tar.zst` package as the primary Linux installation path.
- Add checksum-pinned AppImage and deterministic AppDir `tar.gz` fallbacks, all using the same frozen FontBlind server and exact release gauntlet.
- Add a browser-hosted desktop lifecycle that opens the configured default browser without bundling a second Chromium runtime.
- Add a private per-user single-instance lock: a second launch reopens the existing loopback app instead of starting competing workers.
- Add a session-authenticated shutdown endpoint enabled only for the Linux desktop host; the page waits until the service is actually unreachable before reporting closure.
- Preserve the canonical loopback, anonymous-source, worker-isolation, artifact-seal, resource-limit, and zero-ID contracts on Linux.
- Add Garuda/Arch package installation tests, AppImage execution without FUSE, desktop metadata checks, pinned packaging-tool digests, and Linux acceptance documentation.
- Keep aarch64 build logic available but explicitly unclaimed until an exact native aarch64 runner and package journey exist.

## 3.6.0 — 2026-08-27

- Add verified static-instance export for generated `wght`, `wdth`, and mechanical `slnt` positions, producing a static TTF, WOFF2, exact CSS, and deterministic ZIP without reopening original donors.
- Build every frozen instance in an unpublished stage, independently re-instance the requested coordinates, and compare geometry, horizontal and vertical metrics, anchors, layout, MVAR values, names, style bits, caret slope, shaping, and WOFF2 before publication.
- Reject residual variation tables, GDEF variation stores, GSUB/GPOS feature variations, VariationIndex devices, malformed proof vocabularies, and coordinate drift.
- Validate actual retained files rather than worker descriptors: same-owner regular files, exact outline model, immutable seals, sealed download snapshots, native/WOFF2 equivalence, and closed CSS and ZIP contracts.
- Canonicalize generated SFNT, WOFF2, and ZIP containers so padding, metadata/private blocks, unreferenced records, gaps, and trailing payloads cannot become hidden identity channels.
- Bound retained jobs, aggregate retained bytes, concurrent snapshots, heavy builds, and per-parent static exports; close parent/child deletion, expiry, replacement, and shutdown races.
- Add a browser error firewall, stale-result generation tickets, live-coordinate invalidation, accessible tabs, keyboard navigation, explicit busy/status semantics, focus management, and non-colour proof announcements.
- Replace environment-dependent font discovery with a pinned, open-licensed release corpus covering Latin TrueType, CFF1, Arabic, Devanagari, Hebrew, Thai, real variable fonts, real extracted-donor refusal on geometry drift, Oblique Lab, and frozen fractional positions.
- Extract the exact frozen-runtime product gauntlet from the macOS shell builder into a reusable Python program and run the representative corpus through the packaged executable before signing.
- Pin GitHub Actions by commit and align every public version surface to 3.6.0.

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
