# Repository state

Last verified: **2026-08-27**

## Product

FontBlind transforms and packages fonts locally. Its active workbenches are Blind, Oblique Lab, Variable Lab, and verified static-instance export. It does not contain Font Previewer’s Study, Candidate, comparison, typography-system, or handoff product model.

## Canonical source

- Repository: `bomkino/fontblind`
- Canonical/default branch: `main`
- Current source version: `3.7.0` — unreleased
- Latest published release: `v3.4.0`
- Starting `main` before the 2026-08-27 canonicalisation: `2fe1be30b0c2f669f28b550e749d7a97e3c0efdf`

The exact current `main` SHA must be resolved with `git rev-parse origin/main` or GitHub. A tracked file cannot truthfully contain the SHA of the commit that contains that file: changing the text changes the SHA. The external cleanup receipt records the final immutable value.

## Platforms

| Platform | Current posture | Evidence boundary |
|---|---|---|
| macOS 13+, Apple Silicon | Source and package gate supported | Ad-hoc signed; not Developer ID signed, notarised, stapled, or independently accepted by Gatekeeper |
| Garuda Linux, rolling Arch base, KDE Plasma 6, Wayland-first, x86_64 Dell G7 | Experimental source/package target | Automated Arch/pacman/KDE-Wayland simulation passes; physical Dell/Garuda journey remains open |
| Other Linux distributions, desktops, architectures, and package formats | Unsupported | No claim |

## Automated gate

`.github/workflows/tests.yml` verifies the exact checked-out SHA with:

- Python 3.10, 3.12, and 3.13 on Ubuntu;
- Python 3.12 on macOS;
- browser contract tests and the complete Python suite;
- pinned open-licensed corpus on Ubuntu and macOS;
- native macOS package build and checksum/signature/ZIP checks;
- reproducible x86_64 Arch package build;
- pacman install, ownership, frozen-runtime gauntlet, KDE Plasma 6 Wayland lifecycle simulation, authenticated shutdown, and clean uninstall.

## Remaining gates

- Physical Dell G7 / current Garuda / KDE Plasma 6 / Wayland journey and preflight receipt.
- Attended VoiceOver review before claiming human screen-reader quality.

## Release posture

`main` is ahead of the published `v3.4.0` release. No v3.5, v3.6, or v3.7 public release is implied. Future publication uses `.github/workflows/release.yml`, starts as a dry run, verifies exact-run artifacts, refuses existing tags/releases, and requires explicit confirmation.

## Current documents

- Build/use: [`../../README.md`](../../README.md)
- Architecture: [`../LAB_HARDENING.md`](../LAB_HARDENING.md), [`../LINUX_ARCHITECTURE.md`](../LINUX_ARCHITECTURE.md)
- QA/release: [`../RELEASE_CHECKLIST.md`](../RELEASE_CHECKLIST.md), [`RELEASE_POLICY.md`](RELEASE_POLICY.md)
- Accessibility: [`../ACCESSIBILITY_ACCEPTANCE.md`](../ACCESSIBILITY_ACCEPTANCE.md)
- Linux physical acceptance: [`../LINUX_ACCEPTANCE.md`](../LINUX_ACCEPTANCE.md)
- Branch policy: [`BRANCH_POLICY.md`](BRANCH_POLICY.md)
- Cleanup evidence: [`REPOSITORY_CLEANUP_2026-08-27.md`](REPOSITORY_CLEANUP_2026-08-27.md)
