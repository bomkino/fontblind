from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "linux" / "fontblind-launcher.sh"
DESKTOP = ROOT / "linux" / "fontblind.desktop"
BUILD = ROOT / "build-fontblind-linux.sh"


class LinuxPackageContractTests(unittest.TestCase):
    def test_launcher_and_desktop_preserve_the_single_loopback_runtime(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        desktop = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("FONTBLIND_READY", launcher)
        self.assertIn("127.0.0.1", launcher)
        self.assertIn("FONTBLIND_NO_OPEN", launcher)
        self.assertNotIn("0.0.0.0", launcher)
        self.assertNotIn("eval ", launcher)
        self.assertIn("Terminal=false", desktop)
        self.assertIn("Exec=FontBlind", desktop)

    def test_build_uses_the_frozen_entrypoint_and_release_gauntlet(self) -> None:
        text = BUILD.read_text(encoding="utf-8")
        self.assertIn('"$APP_DIR/fontblind_entry.py"', text)
        self.assertIn('"$APP_DIR/release_gauntlet.py"', text)
        self.assertIn("APPIMAGE_EXTRACT_AND_RUN=1", text)
        self.assertIn("cmp --silent", text)
        self.assertIn("FONTBLIND_CORPUS_DIR", text)
        self.assertNotIn("build-fontblind-app.command", text)

    def test_launcher_accepts_only_the_closed_ready_protocol_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-linux-launcher-") as temp_text:
            root = Path(temp_text)
            server = root / "server"
            marker = root / "stopped"
            server.write_text(
                "#!/bin/sh\n"
                "trap 'touch \"$FONTBLIND_STOP_MARKER\"; exit 0' TERM INT HUP\n"
                "echo 'FONTBLIND_READY 127.0.0.1 7331'\n"
                "while :; do sleep 1; done\n",
                encoding="utf-8",
            )
            server.chmod(server.stat().st_mode | stat.S_IXUSR)
            environment = {
                **os.environ,
                "FONTBLIND_SERVER": str(server),
                "FONTBLIND_STOP_MARKER": str(marker),
                "FONTBLIND_NO_OPEN": "1",
                "XDG_RUNTIME_DIR": str(root),
            }
            process = subprocess.Popen(
                ["sh", str(LAUNCHER)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            try:
                assert process.stdout is not None
                line = process.stdout.readline().strip()
                self.assertEqual(line, "FontBlind local: http://127.0.0.1:7331")
            finally:
                process.terminate()
                process.wait(timeout=5)
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists())
            self.assertFalse(any(root.glob("fontblind-launch.*")))

    def test_launcher_rejects_malformed_readiness(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-linux-malformed-") as temp_text:
            root = Path(temp_text)
            server = root / "server"
            server.write_text(
                "#!/bin/sh\n"
                "echo 'FONTBLIND_READY 0.0.0.0 7331'\n"
                "sleep 2\n",
                encoding="utf-8",
            )
            server.chmod(server.stat().st_mode | stat.S_IXUSR)
            result = subprocess.run(
                ["sh", str(LAUNCHER)],
                capture_output=True,
                text=True,
                timeout=5,
                env={
                    **os.environ,
                    "FONTBLIND_SERVER": str(server),
                    "FONTBLIND_NO_OPEN": "1",
                    "XDG_RUNTIME_DIR": str(root),
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("malformed readiness", result.stderr)


if __name__ == "__main__":
    unittest.main()
