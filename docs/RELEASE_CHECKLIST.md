# FontBlind 3.6 release checklist

This checklist separates machine-verifiable release evidence from actions that require a repository owner or a human using assistive technology.

## Automated release gate

The exact final commit must pass all of the following in `.github/workflows/tests.yml`:

### Runtime and package contract

- Python 3.10, 3.12, and 3.13 on Ubuntu.
- Python 3.12 on macOS.
- Browser JavaScript syntax and Node contract tests.
- Full Python suite with the pinned corpus available to legacy pipeline tests.
- Dedicated representative-corpus suite on Ubuntu and macOS.
- Ad-hoc-signed macOS package build.
- Frozen-server Blind, Oblique, `slnt`, Variable, static-instance, download, ZIP, cleanup, and parent/child gauntlet.
- Every pinned corpus asset processed through the exact frozen server before signing.
- Bundle signature, release ZIP, checksum, and ZIP integrity checks.

### Corpus integrity

- `tests/corpus/manifest.json` uses immutable upstream commits.
- Every file has a reviewed licence, exact byte size, and SHA-256.
- `python tools/fetch_corpus.py` downloads only reviewed HTTPS hosts.
- `python tools/fetch_corpus.py --verify-only` passes after fetch.
- Corpus bytes remain outside the repository and application bundle.
- Linux and macOS produce the expected accept/refuse outcomes.

### Container and privacy integrity

- Generated SFNT directories are contiguous, aligned, and end at the file extent.
- SFNT padding contains only zero bytes.
- WOFF2 declared length equals the retained file and metadata/private blocks are absent.
- ZIP local records, central directory, and end record are contiguous and exact.
- No trailing bytes, hidden gaps, duplicate records, unsupported flags, or unreferenced payloads survive.
- Native, WOFF2, CSS, and ZIP independently pass the zero-ID and semantic contracts.
- Public JSON, DOM, logs, filenames, paths, and downloads contain no source identity.

### Version and documentation

- `fontblind_version.py`, `pyproject.toml`, and `macos/Info.plist` all report `3.6.0`.
- `CFBundleVersion` is `360`.
- `CHANGELOG.md` describes the shipped behaviour.
- README guarantees and refusals match the code.
- Temporary workflows, staging helpers, corpus cache, build output, and diagnostic files are absent from the final tree.

## Final branch audit

Before merge:

1. Compare the complete PR against `main`.
2. Review every changed executable file and workflow.
3. Confirm one public runtime and one production static-instance path.
4. Confirm all temporary Gate 7 workflows and helpers were deleted.
5. Confirm the exact final head has one fully green workflow run.
6. Keep the PR draft until the repository owner has read the final report.
7. Squash merge only with explicit owner confirmation.

Recommended squash title:

```text
FontBlind 3.6: verified static instances and hardened Lab runtime
```

## Human assistive-technology acceptance

Automated tests can verify semantics, focus targets, live regions, keyboard state, reduced-motion rules, and reflow. They cannot hear VoiceOver timing or judge whether spoken phrasing is comfortable.

The human checklist in `docs/ACCESSIBILITY_ACCEPTANCE.md` is therefore reported separately. A release may be technically complete while this human listening pass remains unperformed, but that limitation must be stated plainly.

## Publication actions

The following are intentionally outside the autonomous engineering gate:

- Squash merging PR #2.
- Creating tag `v3.6.0`.
- Publishing a GitHub release.
- Uploading or replacing public release assets.

Each changes the repository’s published state and requires explicit owner confirmation.
