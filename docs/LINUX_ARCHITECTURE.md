# FontBlind Linux architecture

## Binding target

FontBlind's first Linux edition targets **Garuda Linux / Arch / KDE on x86_64**.
That machine is the reference environment. Other distributions do not get to
weaken or delay the Garuda path.

The release order is:

1. `pkg.tar.zst` for direct pacman installation.
2. AppImage fallback.
3. AppDir `tar.gz` fallback.

`.deb`, RPM, Flatpak, and Snap are intentionally deferred until the Garuda
edition is reliable.

## Product shape

The Linux edition is a desktop launcher around the existing hardened local
runtime. It opens FontBlind in the user's configured browser instead of
shipping a second Chromium/Electron runtime.

Process model:

1. `AppRun` or `/usr/bin/fontblind` starts the frozen `FontBlindServer`.
2. The process claims a private per-user runtime lock.
3. The server binds an ephemeral `127.0.0.1` port and publishes only that URL in
   a mode-0600 runtime file.
4. The default browser opens the reviewed FontBlind web interface.
5. Heavy font work continues in isolated child processes using anonymous source
   descriptors and the existing parent-liveness pipe.
6. An authenticated footer control requests shutdown.
7. The page waits until the loopback service is genuinely unreachable before
   reporting that FontBlind is closed.

A second launch cannot create a competing server. It reads the existing
loopback URL from the private runtime file and reopens that app.

## Security boundary

The Linux host preserves the 3.6 runtime contract:

- loopback binding only;
- strict `Host` validation;
- random per-process session secret;
- no permissive CORS response;
- session-authenticated mutations;
- one optional session-authenticated shutdown endpoint, enabled only in the
  Linux browser-hosted mode;
- anonymous source file descriptors;
- isolated worker processes;
- canonical artifact validation and immutable download snapshots;
- silent request logging and generic public filenames.

The desktop state file contains only `http://127.0.0.1:<port>`. It never contains
a session secret, source path, filename, family name, hash, or artifact token.
The lock directory and state files must be owned by the current user, non-linked,
and private.

## Why no bundled Electron runtime in this slice

FontBlind already has a complete browser interface and a hardened local server.
Bundling Electron would duplicate a large browser engine, create another update
and sandbox surface, and substantially increase the artifact without improving
font-engineering truth.

The system-browser host is not treated as dogma. A native Linux shell becomes
justified if the Garuda acceptance pass exposes a material failure that cannot
be solved at the current public seam: broken desktop lifecycle, unacceptable
file-picker behaviour, inaccessible focus, unreliable downloads, or visual
parity that the supported browsers cannot deliver.

## Packaging

### Garuda / Arch

`build-fontblind-linux.sh --arch-package-only` creates
`fontblind-bin-<version>-1-<arch>.pkg.tar.zst` through `makepkg`. The package
installs:

- `/opt/fontblind/AppRun`;
- the self-contained frozen server under `/opt/fontblind/usr/bin`;
- `/usr/bin/fontblind` symlink;
- KDE-visible desktop metadata;
- scalable application icon;
- licence and local usage notes.

The package depends only on `xdg-utils` at runtime. FontTools, HarfBuzz, Brotli,
and Python are frozen into the private application directory.

### Portable Linux

`build-fontblind-linux.sh --portable-only` creates:

- an AppImage using a checksum-pinned `appimagetool` and checksum-pinned type-2
  runtime;
- a deterministic AppDir `tar.gz` fallback;
- SHA-256 receipts for both.

Both formats execute the same `AppRun`, the same frozen server, and the same
browser-hosted shutdown journey.

## Release claims

The following must remain separate:

- **Automated:** exact runtime, corpus, package, AppImage, install, launch,
  shutdown, cleanup, checksums, and artifact structure.
- **Human:** Garuda/KDE application-menu behaviour, default-browser opening,
  actual desktop feel, screen-reader speech, and high-zoom usability.

An automated green run does not silently become a claim that the human desktop
experience has been heard or observed.
