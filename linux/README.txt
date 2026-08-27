FONTBLIND FOR GARUDA KDE

SUPPORTED TARGET

  Current, fully updated Garuda Linux on x86_64
  KDE Plasma 6
  Wayland-first desktop session
  Dell G7 reference machine

The supported Linux artifact is:

  fontblind-bin-<version>-1-x86_64.pkg.tar.zst

Verify and install it with:

  sha256sum -c fontblind-bin-<version>-1-x86_64.pkg.tar.zst.sha256
  sudo pacman -U ./fontblind-bin-<version>-1-x86_64.pkg.tar.zst

After installation, run the non-destructive machine receipt included beside the
package:

  bash ./dell-g7-preflight.sh \
    ./fontblind-bin-<version>-1-x86_64.pkg.tar.zst \
    ./fontblind-dell-g7-preflight.txt

It must end with RESULT: PASS before the visible journey in
LINUX_ACCEPTANCE.md. The script observes Garuda, KDE, Wayland, Dell G7 DMI,
package ownership, dependencies, loopback startup, and shutdown. It does not
install, remove, update, or process anything.

Launch FontBlind from the KDE application launcher or run:

  fontblind

Remove it with:

  sudo pacman -Rns fontblind-bin

HOW IT WORKS

FontBlind starts one private service on 127.0.0.1 and opens the reviewed
interface through KDE's configured default browser. It does not ship Electron,
GTK, Qt, a cloud client, telemetry, or a second font engine. Source fonts remain
inside the existing anonymous local descriptor and isolated-worker boundary.

A second launch reopens the exact existing loopback session. Use "Quit
FontBlind" in the footer to stop the local server, workers, and retained jobs.
The browser does not claim closure until the service is actually gone.

SUPPORT BOUNDARY

This package is release-gated only for the target above. No support claim is
made for another distribution, desktop, architecture, or machine. FontBlind
has no OpenGL, Vulkan, CUDA, NVIDIA, or discrete-GPU runtime dependency; the
Dell G7 graphics stack is outside its processing path.

Only process fonts you are entitled to modify. FontBlind's MIT licence covers
the software, not font files supplied to it.
