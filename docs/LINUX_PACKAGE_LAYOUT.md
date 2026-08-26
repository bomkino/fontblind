# Linux package layout

```text
FontBlind.AppDir/
├─ AppRun
├─ fontblind.desktop
├─ fontblind.svg
├─ .DirIcon -> fontblind.svg
└─ usr/lib/fontblind/
   ├─ FontBlindServer
   └─ frozen runtime files
```

The portable archive uses the same layout but names the root launcher `FontBlind`. This keeps the package surfaces aligned and lets both formats exercise the same lifecycle script.
