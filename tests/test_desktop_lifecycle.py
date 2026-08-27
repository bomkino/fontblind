from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fontblind_desktop import BrowserAppLease, DesktopLifecycleError, _valid_loopback_url, open_desktop_url


class DesktopLeaseTests(unittest.TestCase):
    def test_one_process_owns_the_runtime_and_a_second_reopens_the_exact_url(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-desktop-runtime-") as temp_text, mock.patch.dict(
            os.environ,
            {"XDG_RUNTIME_DIR": temp_text},
            clear=False,
        ):
            first = BrowserAppLease.acquire()
            self.assertTrue(first.owned)
            first.publish("http://127.0.0.1:7331")

            second = BrowserAppLease.acquire()
            try:
                self.assertFalse(second.owned)
                self.assertEqual(second.read_existing_url(), "http://127.0.0.1:7331")
            finally:
                second.close()

            first.close()
            self.assertFalse((Path(temp_text) / "fontblind" / "desktop.url").exists())

            third = BrowserAppLease.acquire()
            try:
                self.assertTrue(third.owned)
            finally:
                third.close()

    def test_state_file_refuses_external_or_identity_bearing_urls(self) -> None:
        for value in (
            "https://127.0.0.1:7331",
            "http://localhost:7331",
            "http://127.0.0.1:7331/private/font.ttf",
            "http://127.0.0.1:7331/?family=Secret",
            "http://user@127.0.0.1:7331",
            "not a url",
        ):
            with self.subTest(value=value):
                self.assertIsNone(_valid_loopback_url(value))
        self.assertEqual(_valid_loopback_url("http://127.0.0.1:7331/"), "http://127.0.0.1:7331")

    def test_runtime_root_cannot_be_replaced_by_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fontblind-desktop-parent-") as parent_text, tempfile.TemporaryDirectory(
            prefix="fontblind-desktop-target-"
        ) as target_text, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": parent_text}, clear=False):
            (Path(parent_text) / "fontblind").symlink_to(Path(target_text), target_is_directory=True)
            with self.assertRaises(DesktopLifecycleError):
                BrowserAppLease.acquire()

    def test_browser_opener_prefers_the_kde_xdg_default_application_seam(self) -> None:
        with (
            mock.patch("fontblind_desktop.shutil.which", side_effect=lambda command: "/usr/bin/xdg-open" if command == "xdg-open" else None),
            mock.patch("fontblind_desktop.subprocess.Popen") as process,
            mock.patch("fontblind_desktop.webbrowser.open_new_tab") as browser,
        ):
            self.assertTrue(open_desktop_url("http://127.0.0.1:7331"))
            process.assert_called_once()
            self.assertEqual(process.call_args.args[0], ("xdg-open", "http://127.0.0.1:7331"))
            browser.assert_not_called()

    def test_browser_opener_falls_back_without_widening_the_url_contract(self) -> None:
        with (
            mock.patch("fontblind_desktop.shutil.which", return_value=None),
            mock.patch("fontblind_desktop.webbrowser.open_new_tab", return_value=True) as opener,
        ):
            self.assertTrue(open_desktop_url("http://127.0.0.1:7331"))
            opener.assert_called_once_with("http://127.0.0.1:7331")
        with (
            mock.patch("fontblind_desktop.shutil.which") as lookup,
            mock.patch("fontblind_desktop.webbrowser.open_new_tab") as opener,
        ):
            self.assertFalse(open_desktop_url("https://example.com"))
            lookup.assert_not_called()
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
