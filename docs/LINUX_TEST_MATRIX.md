# Linux test matrix

| Surface | Automated proof |
| --- | --- |
| Launcher | closed readiness protocol, malformed-host refusal, child termination, private-state cleanup |
| Frozen server | exact release gauntlet with pinned corpus |
| Portable bundle | deterministic archive, checksum, member listing |
| AppImage builder | immutable asset ID and SHA-256 |
| AppImage | two-build byte comparison and final launch smoke |
| Architecture | x86_64 release-cleared; aarch64 unclaimed pending runner |
