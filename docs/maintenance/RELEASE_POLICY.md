# Release policy

## Separation of states

- **Current source** means the code on `main`.
- **Latest published release** means the newest immutable GitHub Release and its original tag/commit.
- Source may be newer than the latest published release. Documentation must say so plainly.

## Claims

A release may claim only what its exact commit and evidence prove. Hosted CI does not prove attended VoiceOver quality, physical Dell G7 behaviour, a real Garuda/KDE user session, independent clean-machine behaviour, Developer ID signing, notarisation, stapling, or Gatekeeper acceptance.

Experimental packages may be distributed only with their narrow target and open gates stated. No generic Linux claim may be inferred from the Garuda package.

## Publication mechanism

`.github/workflows/release.yml` is manual-only. Its default path is validation without publication. It:

1. checks out the explicit target SHA;
2. verifies source version and requested tag;
3. verifies the named workflow run completed successfully at that exact SHA;
4. downloads exact-SHA macOS and Garuda artifacts;
5. validates checksums, package contents, and release notes;
6. assembles and uploads a dry-run bundle;
7. refuses an existing tag or release;
8. publishes only when `publish=true` and the confirmation input exactly matches the requested tag.

Publishing a new release remains a separate owner action. Existing public tags, releases, and assets are immutable.
