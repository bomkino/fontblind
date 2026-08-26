# Garuda Linux acceptance pass

Reference machine: **Garuda Linux / Arch / KDE, x86_64**.

Record the exact package SHA-256 and test date before starting.

## Pacman package

1. Install the exact `fontblind-bin-<version>-1-x86_64.pkg.tar.zst` with
   `sudo pacman -U`.
2. Confirm FontBlind appears in the KDE application launcher with the correct
   icon and no terminal window.
3. Launch it. Confirm exactly one browser tab opens to `127.0.0.1`.
4. Launch FontBlind again. Confirm it reopens the existing local app rather than
   starting a competing service.
5. Complete Blind with one permitted font and open every download.
6. Complete static Oblique and confirm the UI calls it Oblique, not Italic.
7. Complete one variable build, move an axis, and freeze one interior position.
8. Confirm moving the live axis removes the stale frozen package.
9. Start a build, try another build, and confirm explicit back-pressure.
10. Use **Quit FontBlind**. Confirm the page reports closure only after the local
    service has stopped.
11. Relaunch and confirm no prior jobs or source labels remain.
12. Uninstall with `sudo pacman -Rns fontblind-bin`. Confirm `/opt/fontblind`,
    `/usr/bin/fontblind`, and the desktop entry are removed.

## AppImage fallback

1. Run the exact AppImage normally.
2. Repeat launch, one Blind build, one frozen-instance build, download, and quit.
3. Run it again with `APPIMAGE_EXTRACT_AND_RUN=1` and repeat the shutdown path.
4. Confirm neither mode writes files beside the AppImage except user-requested
   downloads.

## Keyboard and accessibility

1. Complete every workbench without a pointer.
2. Verify tab order through navigation, dropzone, controls, proof, downloads,
   reset, and quit.
3. Confirm processing, refusal, success, stale-result removal, and closure are
   announced by the active Linux screen reader.
4. Test 200% browser zoom and a narrow window without horizontal loss of core
   controls.
5. Enable reduced motion and confirm no functional transition depends on
   animation.
6. Confirm proof rows remain understandable without colour.

## Privacy observation

While processing a font:

- inspect the browser network panel and confirm every request stays on the one
  loopback origin;
- confirm public responses, download names, page text, and errors contain no
  original filename, path, family, foundry, designer, licence text, or hash;
- quit during an active build and confirm the worker exits with the host.

A failed item blocks the Garuda release claim. It does not get converted into a
cosmetic follow-up.
