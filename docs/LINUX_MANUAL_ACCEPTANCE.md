# Linux manual acceptance

After CI passes, test the downloaded x86_64 package on one Ubuntu-family desktop and one Arch-family desktop:

1. Launch the AppImage from a file manager and from a terminal.
2. Confirm the default browser opens one `127.0.0.1` address.
3. Complete Blind, static Oblique, Variable Lab, and one frozen instance.
4. Download and open the native font, WOFF2, CSS, and ZIP outputs.
5. Close the launcher and confirm the local page stops responding.
6. Relaunch and confirm no prior output remains.
7. Repeat with `FONTBLIND_NO_OPEN=1` and open the printed address manually.
8. Extract and launch the portable tarball.
9. Confirm no desktop service, startup item, or file outside the extracted package is created.

CI establishes the package contract. This pass establishes desktop integration on real distributions.
