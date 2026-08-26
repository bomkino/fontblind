# Linux browser boundary

The browser is a view of a local application, not a remote service. The package opens only the loopback URL emitted by the frozen server. Content Security Policy, session-secret, Host-header, download-seal, and same-origin rules remain enforced by the existing runtime.

The launcher does not inject JavaScript, rewrite HTML, proxy traffic, or weaken headers. Linux therefore receives the same browser contract already exercised by FontBlind's Node, HTTP, corpus, and frozen-runtime tests.
