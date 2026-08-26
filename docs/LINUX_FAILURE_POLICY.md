# Linux package failure policy

The build fails rather than publishing partial evidence when:

- the frozen server does not announce exact loopback readiness;
- the release gauntlet or pinned corpus fails;
- the AppImage tool digest differs;
- two AppImage builds differ;
- the final package does not launch;
- any checksum or archive integrity check fails.

An unsuccessful build leaves no release claim and no automatic publication.
