from __future__ import annotations

import unittest
from importlib.metadata import version
from pathlib import Path
import plistlib
import re

import fontblind
import fontblind_outline
import fontblind_surgical
import fontblind_web
from fontblind_version import PROGRAM_VERSION, __version__


class VersionConsistencyTests(unittest.TestCase):
    def test_every_entry_point_uses_the_packaged_release_version(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project_text = (root / "pyproject.toml").read_text(encoding="utf-8")
        project_match = re.search(r'^version = "([^"]+)"$', project_text, flags=re.MULTILINE)
        self.assertIsNotNone(project_match)
        with (root / "macos" / "Info.plist").open("rb") as stream:
            app = plistlib.load(stream)

        packaged = version("fontblind-local")
        self.assertEqual(project_match.group(1), PROGRAM_VERSION)
        self.assertEqual(app["CFBundleShortVersionString"], PROGRAM_VERSION)
        self.assertEqual(app["CFBundleVersion"], "".join(PROGRAM_VERSION.split(".")))
        self.assertEqual(packaged, PROGRAM_VERSION)
        self.assertEqual(__version__, PROGRAM_VERSION)
        self.assertEqual(fontblind.PROGRAM_VERSION, PROGRAM_VERSION)
        self.assertEqual(fontblind_surgical.PROGRAM_VERSION, PROGRAM_VERSION)
        self.assertEqual(fontblind_outline.OUTLINE_PROGRAM_VERSION, PROGRAM_VERSION)
        self.assertEqual(fontblind_web.PROGRAM_VERSION, PROGRAM_VERSION)


if __name__ == "__main__":
    unittest.main()
