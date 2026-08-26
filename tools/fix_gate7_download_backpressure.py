#!/usr/bin/env python3
"""Make the exact-runtime gauntlet respect FontBlind's bounded download gate."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "release_gauntlet.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor drifted")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(text, "    import struct\n", "    import struct\n    import time\n", "time import")
    text = replace_once(
        text,
        '''            status, headers, payload = request("GET", url)
            require(status == 200, f"{label} {kind} download failed")
''',
        '''            # The runtime deliberately permits only two simultaneous
            # sealed snapshots. A fast local test client can receive EOF before
            # the server thread reaches its semaphore release, so honour the
            # authored 429 back-pressure for a bounded one-second window.
            for _attempt in range(50):
                status, headers, payload = request("GET", url)
                if status != 429:
                    break
                time.sleep(0.02)
            require(
                status == 200,
                f"{label} {kind} download failed: {status} {payload[:200]!r}",
            )
''',
        "download back-pressure",
    )
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
