FONTBLIND FOR LINUX

PRIMARY TARGET

The release gate is Garuda Linux / Arch / KDE on x86_64. The preferred package
is:

  fontblind-bin-<version>-1-x86_64.pkg.tar.zst

Install it with:

  sudo pacman -U ./fontblind-bin-<version>-1-x86_64.pkg.tar.zst

Launch FontBlind from the application menu or run:

  fontblind

Remove it with:

  sudo pacman -Rns fontblind-bin

PORTABLE FALLBACKS

AppImage:

  chmod +x FontBlind-<version>-x86_64.AppImage
  ./FontBlind-<version>-x86_64.AppImage

If FUSE is unavailable:

  APPIMAGE_EXTRACT_AND_RUN=1 ./FontBlind-<version>-x86_64.AppImage

AppDir archive:

  tar -xzf FontBlind-<version>-linux-x86_64.tar.gz
  ./FontBlind.AppDir/AppRun

HOW THE LINUX EDITION WORKS

FontBlind starts one private loopback-only service on 127.0.0.1 and opens the
same reviewed interface in your default browser. It does not ship a second
Chromium/Electron runtime, contact a cloud service, or require an account.
Source fonts still travel only through anonymous local file descriptors.

Use "Quit FontBlind" in the footer when finished. The browser verifies that the
local service has actually stopped before saying the app is closed. A second
launch reopens the already-running local app instead of starting another worker
service.

The Garuda package declares xdg-utils so KDE can open the configured default
browser. The AppImage and tar.gz fallbacks require a graphical browser and an
available xdg-open or gio desktop opener.

SUPPORT BOUNDARY

The x86_64 Garuda/Arch package and x86_64 portable artifacts are release-gated.
The builder contains an aarch64 path, but aarch64 is not claimed until an exact
native runner and packaged-artifact journey are added.

FontBlind remains local, telemetry-free, and fail-closed. Only process fonts you
are entitled to modify. FontBlind's MIT licence covers the software, not the
font files supplied to it.
