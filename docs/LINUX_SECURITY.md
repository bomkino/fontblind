# Linux launcher security boundary

The launcher accepts one readiness record: `FONTBLIND_READY 127.0.0.1 <port>`. It refuses non-loopback hosts, malformed ports, and surplus fields. It does not evaluate browser commands, parse font data, persist session secrets, or expose the server on a network interface.

A private `mktemp` directory contains the server's launch log and is removed on normal exit or signal handling. The launcher remains the parent of the frozen server and terminates it when the launcher exits.
