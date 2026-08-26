# FontBlind Linux package — draft notes

FontBlind's hardened local runtime now has a Linux package path.

The x86_64 build produces a portable tarball and an AppImage. Both start the same loopback-only frozen server used by the source application, open the existing browser UI, and preserve the Blind, Oblique Lab, Variable Lab, and verified static-instance contracts.

The package adds no cloud service, account, daemon, Electron layer, or second font engine. Sources remain anonymous local descriptors; generated artifacts retain the same zero-ID, proof, lifecycle, and download boundaries as FontBlind 3.6.

This draft is not a published release. It becomes release copy only after the Linux package CI gate passes and the repository owner approves publication.
