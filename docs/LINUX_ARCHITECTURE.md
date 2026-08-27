# FontBlind 3.7 Garuda architecture

## Supported target

FontBlind’s Linux edition has one binding target:

- current Garuda Linux, tracking Arch;
- KDE Plasma 6;
- Wayland-first session;
- x86_64 Dell G7;
- native pacman package installation.

The supported artifact is:

```text
fontblind-bin-<version>-1-x86_64.pkg.tar.zst
```

No support claim is made for another distribution, desktop, architecture,
package format, or machine.

## Product shape

The package is a small desktop host around the existing hardened FontBlind
runtime. It does not create a Linux-specific font engine or UI.

1. KDE launches `/usr/bin/fontblind`.
2. The launcher refuses root and starts the frozen server in browser-app mode.
3. A private per-user lease permits one server process.
4. The server binds an ephemeral `127.0.0.1` port and stores only that loopback
   URL in a mode-0600 state file.
5. `xdg-open` passes the URL to KDE’s configured default browser.
6. Font work continues through the existing anonymous-source, isolated-worker,
   artifact-seal, and bounded-resource contracts.
7. A session-authenticated footer control requests shutdown.
8. The page reports closure only after the loopback service has disappeared.

A second launch reads the existing loopback URL and reopens it. It cannot create
another local service while the owner lease is alive.

## KDE and graphics boundary

The host is browser-based and uses the XDG default-application seam. It has no
Qt, Electron, GTK, OpenGL, Vulkan, CUDA, NVIDIA, PRIME-offload, or discrete-GPU
runtime dependency. The Dell G7’s hybrid graphics stack remains the browser and
desktop compositor’s concern, not FontBlind’s processing path.

The automated target model sets:

```text
XDG_CURRENT_DESKTOP=KDE
KDE_SESSION_VERSION=6
XDG_SESSION_TYPE=wayland
WAYLAND_DISPLAY=wayland-0
DISPLAY unset
```

A fake `xdg-open` verifies that the exact loopback URL and the Wayland-only KDE
environment cross the desktop boundary intact.

## Security boundary

The Linux host preserves the established contracts:

- loopback binding and strict `Host` validation;
- random per-process session secret;
- no permissive cross-origin response;
- session-authenticated mutations and shutdown;
- anonymous source descriptors and parent-liveness pipes;
- isolated heavy workers;
- exact proof vocabularies;
- canonical SFNT, WOFF2, CSS, and ZIP validation;
- immutable retained-artifact seals and download snapshots;
- bounded workers, jobs, bytes, child exports, and downloads;
- silent request logging and generic public paths.

The desktop state file contains only `http://127.0.0.1:<port>`. It never contains
a session secret, source path, filename, family name, hash, job token, or
artifact URL.

## Package gate

`build-fontblind-linux.sh`:

1. freezes `fontblind_entry.py` for x86_64 Linux;
2. runs the exact release gauntlet against that frozen executable;
3. stages the runtime, launcher, desktop entry, icon, licence, and usage notes;
4. invokes `makepkg` twice from separate clean directories;
5. requires byte-identical packages;
6. inspects metadata, members, modes, architecture, dependencies, and path
   leakage;
7. emits the package and adjacent SHA-256 receipt.

CI then installs the package in a current Arch container, runs the same complete
product gauntlet from `/opt/fontblind`, models the KDE Wayland lifecycle,
verifies second-launch reuse and authenticated shutdown, checks pacman ownership,
and removes the package without residue.

## Claim boundary

Automated proof establishes the package and runtime contract. The physical Dell
G7 pass in `LINUX_ACCEPTANCE.md` remains necessary before publishing a
hardware-specific support claim. CI cannot see the actual KDE launcher, hear the
screen reader, or judge the chosen browser’s desktop behaviour.
