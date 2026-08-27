# Repository cleanup receipt — 2026-08-27

## Starting state

- Default branch: `main`
- Starting `main`: `2fe1be30b0c2f669f28b550e749d7a97e3c0efdf`
- Latest published release: `v3.4.0` at its original commit
- Open product PR: #4, experimental Garuda/KDE package

## Branches reviewed

| Branch | Recorded tip | Disposition |
|---|---|---|
| `ci/gate2-runtime-validation` | `487f2e1bb96d6a4fc944e8444e80584590a8af23` | Diagnostic; PR #3 deliberately closed; delete |
| `improve/lab-hardening-3.5` | `aac9ade7353e329d04aad2bdacec723c0c0c3106` | Product patch squash-merged by PR #1; delete |
| `improve/inspect-instance-proof-3.6` | `f9c0f41f22b82640d6adc1d046951d189003f2f5` | Product patch squash-merged by PR #2; delete |
| `improve/static-instance-contracts-3.6` | `6745130e3fd2b341d35f525a22cffdceb6d8d562` | Temporary exact-tree export workflow only; delete |
| `improve/static-instances-contracts-3.6` | `14727bfddae473040a74dd098913583713264349` | Duplicate/typo transfer fragments only; delete |
| `codex/linux-browser-app-foundation` | `c7f59d6837c5e395b00394cdb1b28a0f31088673` before cleanup commits | Legitimate 3.7 product work; integrate through PR #4, then delete |

## Product integration

PR #4 supplies the one narrow Linux target: current Garuda/Arch, KDE Plasma 6, Wayland-first, x86_64 Dell G7. Automated package, reproducibility, pacman, lifecycle, and macOS-regression evidence passed before canonicalisation. Physical Dell/Garuda acceptance remains explicitly open.

## Cleanup changes

- Separated published v3.4.0 from unreleased 3.7.0 source.
- Replaced hard-coded 3.6 release instructions with reusable release policy.
- Added one canonical repository-state document and branch/release policies.
- Added exact-SHA artifact naming and a verified macOS artifact upload.
- Added a manual, dry-run-first release workflow without publishing anything.
- Removed temporary export workflow from the candidate tree.
- Preserved current technical architecture and historical pull-request evidence.

## Final values

The final `main` SHA, merged PR head, workflow run IDs, artifact checksums, issue numbers, and branch-deletion result are recorded in the external execution receipt because a commit cannot embed its own SHA without making that value stale.
