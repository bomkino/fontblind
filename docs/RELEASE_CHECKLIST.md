# FontBlind release checklist

This is a reusable checklist. It does not describe a specific branch, pull request, or historical release. Current repository truth lives in [`maintenance/REPOSITORY_STATE.md`](maintenance/REPOSITORY_STATE.md); release claims are governed by [`maintenance/RELEASE_POLICY.md`](maintenance/RELEASE_POLICY.md).

## Candidate identity

- [ ] `main` is the candidate source.
- [ ] The intended full commit SHA is recorded outside the candidate commit itself.
- [ ] `fontblind_version.py`, `pyproject.toml`, macOS bundle metadata, Linux package metadata, UI labels, artifact names, changelog, and release notes agree.
- [ ] The tag is new and resolves to the exact intended commit.
- [ ] The latest published release remains untouched.

## Automated gate

- [ ] Python compilation and dependency checks pass.
- [ ] Browser syntax and contract tests pass.
- [ ] The complete Python suite passes on supported Python versions.
- [ ] The pinned open-licensed corpus verifies and runs on Ubuntu and macOS.
- [ ] The exact frozen runtime passes `release_gauntlet.py`.
- [ ] macOS package, nested signatures, checksum, architecture, ZIP integrity, package contents, and privacy scans pass.
- [ ] Experimental Garuda package reproducibility, pacman install/ownership, runtime journey, KDE/Wayland simulation, checksum, package contents, privacy scans, and uninstall-without-residue pass.
- [ ] Generated output does not change tracked source.
- [ ] The workflow run head SHA equals the intended release commit.

## Human and physical gates

Record these separately. Automation cannot convert them into passing evidence.

- [ ] Attended VoiceOver journey, when required for the release claim.
- [ ] Physical Dell G7 / Garuda / KDE Plasma 6 / Wayland journey, when claiming that exact Linux target.
- [ ] Human review of warnings, installation text, and release notes.

A prerelease or experimental package may be prepared with an open human gate only when the limitation is explicit and the release does not claim that gate passed.

## Artifact review

- [ ] Every asset belongs to the exact verified workflow run.
- [ ] Every checksum validates after download.
- [ ] Package filenames include the source version and supported architecture.
- [ ] No source fonts, test corpus fonts, local paths, usernames, email addresses, credentials, source maps, internal handovers, or client material are present.
- [ ] Software licence and third-party notices are present; font rights are not misrepresented.

## Publication

Use the manual release workflow only after explicit owner authorisation for that release.

- [ ] Dry-run bundle validation passes.
- [ ] The confirmation input exactly matches the requested tag.
- [ ] No release or tag with the requested name already exists.
- [ ] The release is created at the exact intended commit.
- [ ] Public assets and checksums match the validated bundle.
- [ ] Stable, platform, accessibility, signing, notarisation, and hardware claims remain within verified evidence.
