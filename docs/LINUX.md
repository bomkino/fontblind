# FontBlind on Linux

FontBlind's font engine, worker isolation, loopback server, browser UI, and
verification contracts are platform-neutral. The Linux package deliberately
reuses that runtime instead of creating a second implementation.

## Packages

`bash build-fontblind-linux.sh` produces:

- a self-contained portable `tar.gz`; and
- an AppImage for the current architecture.

Both packages contain the same frozen Python server. Launching either package
starts a private server on `127.0.0.1`, opens the default browser, and remains
attached to the server process. Closing the launcher stops the server and
removes the launch log.

The builder currently recognises `x86_64` and `aarch64`. CI release-clears the
x86_64 package on Ubuntu; aarch64 remains source-supported until an aarch64
package runner is added.

## Run

AppImage:

```sh
chmod +x FontBlind-3.6.0-x86_64.AppImage
./FontBlind-3.6.0-x86_64.AppImage
```

Portable bundle:

```sh
tar -xzf FontBlind-3.6.0-linux-x86_64.tar.gz
cd FontBlind-3.6.0-linux-x86_64
./FontBlind
```

Set `FONTBLIND_NO_OPEN=1` to keep the browser closed and print the loopback URL.
This is useful for automated checks and headless sessions.

## Build requirements

- Python 3.10 or newer
- `curl`
- `sha256sum`
- GNU `tar`
- `gzip`
- `file`

The build creates a private virtual environment and installs the exact
dependencies from `requirements.txt` plus the pinned PyInstaller version used
by the macOS package.

## Package trust

Before packaging, the builder launches the exact frozen server and runs
`release_gauntlet.py`. When a pinned corpus directory is supplied through
`FONTBLIND_CORPUS_DIR`, the multiscript corpus is also processed through that
frozen runtime.

The AppImage builder binary is fetched from an immutable GitHub release asset
ID and verified against a reviewed SHA-256 before execution. The AppImage is
built twice and must be byte-identical. The final AppImage is then launched
with browser opening disabled, and its loopback page must load successfully.

The package does not expose FontBlind to the network, install a background
service, retain source filenames, or create a second font-processing engine.

## Desktop-shell boundary

The first Linux release is a browser-hosted desktop utility, not a GTK or
Electron shell. That keeps the hardened runtime and UI contract identical
across macOS source launches, Linux, and CI. A native Linux shell should only
be added if it can preserve that single-runtime architecture and pass the same
public-seam gauntlet.
