# Linux package decisions

1. Reuse the existing frozen server and browser UI.
2. Ship AppImage plus a portable tarball.
3. Release-clear x86_64 first.
4. Keep aarch64 source-supported but unclaimed until equivalent CI exists.
5. Bind only to loopback and keep the launcher attached to the server.
6. Pin and hash the AppImage packaging tool.
7. Run the existing release gauntlet before packaging and launch the final package afterward.
8. Do not publish release assets from the implementation branch.
