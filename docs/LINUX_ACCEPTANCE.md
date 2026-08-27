# Dell G7 · Garuda KDE acceptance

This is the only remaining human gate after the exact CI package is green.

Use a fully updated Garuda installation and reboot into the normal KDE Plasma 6
Wayland session before testing. System updating is deliberately outside the
FontBlind acceptance script; the script observes the machine but does not
install, remove, or update anything.

Record before testing:

- date;
- Garuda version and kernel;
- KDE Plasma version;
- Wayland or X11 session;
- Dell G7 model identifier;
- package filename;
- package SHA-256;
- configured default browser.

## Install and machine preflight

1. Verify the package receipt from the downloaded artifact directory:

   ```bash
   sha256sum -c fontblind-bin-3.7.0-1-x86_64.pkg.tar.zst.sha256
   ```

2. Install the exact package:

   ```bash
   sudo pacman -U ./fontblind-bin-3.7.0-1-x86_64.pkg.tar.zst
   ```

3. Run the non-destructive machine preflight as the normal KDE user:

   ```bash
   bash ./dell-g7-preflight.sh \
     ./fontblind-bin-3.7.0-1-x86_64.pkg.tar.zst \
     ./fontblind-dell-g7-preflight.txt
   ```

   It must end with `RESULT: PASS`. Preserve the generated receipt. The script
   records Garuda/Arch identity, x86_64, Dell G7 DMI data, Plasma 6, Wayland,
   the configured browser, package ownership and dependencies, ELF linkage,
   loopback startup, authenticated shutdown, and reconnect-state cleanup. It
   does not use `sudo`, invoke pacman mutation commands, or process a font.

Any preflight failure blocks the hardware-specific support claim. Do not work
around a failed check merely to continue the visual journey.

## Install and launch

1. Confirm FontBlind appears in the KDE application launcher with its icon and
   opens no terminal window.
2. Launch it. Confirm one browser tab opens to `127.0.0.1`.
3. Launch it again. Confirm it reopens the same local URL rather than starting a
   second service.
4. Confirm the page exposes **Quit FontBlind** only in this packaged desktop
   mode.
5. Confirm the configured KDE default browser opens. FontBlind must not choose
   or bundle a browser of its own.

## Product journey

1. Complete Blind with one permitted TTF or OTF and open all four downloads.
2. Build static Oblique and confirm the result is called Oblique, never a
   designed Italic.
3. Build one generated variable font, move its axis, and freeze an interior
   position.
4. Move the live axis again and confirm the stale frozen package disappears.
5. Start a build and attempt another; confirm explicit local back-pressure.
6. Quit during an idle state, relaunch, and confirm no prior jobs or labels
   remain.
7. Start a build, use Quit FontBlind, and confirm the worker and local service
   stop.

## KDE, Wayland, and browser behaviour

1. Run the journey in the normal KDE Plasma 6 Wayland session.
2. Confirm file pickers and downloads behave normally under KDE.
3. Confirm no NVIDIA/Intel GPU selection prompt or graphics dependency appears.
4. Repeat launch and quit once after suspending and waking the Dell G7.
5. Repeat one launch after changing the KDE default browser, then restore the
   preferred browser. FontBlind should follow KDE’s choice both times.

## Keyboard and accessibility

1. Complete each workbench without a pointer.
2. Check focus order through navigation, dropzone, controls, proof, downloads,
   reset, and quit.
3. Check processing, refusal, success, stale-result removal, and closure with
   the active Linux screen reader.
4. Test 200% browser zoom and a narrow window.
5. Enable reduced motion and confirm no function depends on animation.
6. Confirm proof rows remain understandable without colour.

## Privacy observation

While processing a font:

- confirm browser traffic remains on one loopback origin;
- confirm responses, page text, errors, and download names reveal no original
  filename, path, family, foundry, designer, licence text, or hash;
- confirm Quit removes the reconnect URL and the previous session cannot be
  reopened.

## Uninstall

```bash
sudo pacman -Rns fontblind-bin
```

Confirm these are gone:

- `/opt/fontblind`;
- `/usr/bin/fontblind`;
- `/usr/share/applications/fontblind.desktop`;
- `/usr/share/icons/hicolor/scalable/apps/fontblind.svg`.

Any failure blocks the Garuda/Dell G7 support claim. Do not convert a failed
functional item into cosmetic follow-up work.
