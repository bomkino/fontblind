# Linux scope

The first Linux package is deliberately narrow:

- x86_64 is the release-cleared target.
- aarch64 is accepted by the build script but remains unclaimed until CI runs the same package gate on that architecture.
- the UI opens in the user's default browser.
- the server binds only to `127.0.0.1` on an ephemeral port.
- no daemon, autostart entry, installer, package manager repository, GTK shell, Electron shell, or network service is introduced.
- the package contains no corpus fonts and downloads no fonts at runtime.
- the AppImage build tool is downloaded only during packaging and is verified by SHA-256 before execution.

This slice is complete when one x86_64 AppImage and one portable tarball pass the exact frozen-runtime, corpus, lifecycle, reproducibility, and launch gates in pull-request CI.
