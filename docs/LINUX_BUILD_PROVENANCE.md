# Linux build provenance

The Linux package builder records trust through inputs rather than mutable service state:

- FontBlind source comes from the checked-out commit.
- Python dependencies come from `requirements.txt`.
- PyInstaller is pinned to the same version used by the macOS build.
- The AppImage tool is addressed by immutable GitHub release asset ID and checked against a fixed SHA-256.
- `SOURCE_DATE_EPOCH`, timezone, locale, and Python hash seed are fixed.
- Generated package checksums are emitted next to each artifact.

No release asset is uploaded by the package builder itself.
