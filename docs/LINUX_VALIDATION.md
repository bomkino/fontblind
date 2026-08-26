# Linux validation receipt

This branch is intentionally separate from FontBlind 3.6's merged macOS release work.

The Linux package is accepted only when the pull-request workflow proves all of the following:

- the same `fontblind_entry.py` frozen runtime starts on Linux;
- the exact frozen runtime passes `release_gauntlet.py` with the pinned multiscript corpus;
- the launcher accepts only `FONTBLIND_READY 127.0.0.1 <port>`;
- malformed or non-loopback readiness is refused;
- launcher termination stops the child server and removes private runtime state;
- the portable tarball and AppImage contain the same frozen server;
- the AppImage builder binary matches its pinned SHA-256;
- two AppImage builds from one clean tree are byte-identical;
- the final AppImage launches without opening a browser and serves the local UI;
- artifact checksums and archive integrity pass.

The first release-cleared Linux target is x86_64 on Ubuntu CI. The build script recognises aarch64, but that architecture is not claimed until an aarch64 package runner executes the same package gate.

No GitHub release asset is published from this branch. Packaging evidence remains a temporary CI artifact until merge and explicit release approval.
