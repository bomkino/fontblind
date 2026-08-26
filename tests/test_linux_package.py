from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LinuxPackageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = (ROOT / "build-fontblind-linux.sh").read_text(encoding="utf-8")
        cls.desktop = (ROOT / "linux" / "fontblind.desktop.in").read_text(encoding="utf-8")
        cls.readme = (ROOT / "linux" / "README.txt").read_text(encoding="utf-8")
        cls.pkgbuild = (ROOT / "linux" / "PKGBUILD.in").read_text(encoding="utf-8")
        cls.architecture = (ROOT / "docs" / "LINUX_ARCHITECTURE.md").read_text(encoding="utf-8")

    def test_builder_uses_one_frozen_runtime_and_the_exact_release_gauntlet(self) -> None:
        for fragment in (
            '"$APP_DIR/fontblind_entry.py"',
            'release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT',
            "--fontblind-browser-app",
            "/api/shutdown",
            "FontBlind.AppDir",
            "sha256sum -c",
            "pyinstaller==6.22.2",
            "--runtime-file",
        ):
            self.assertIn(fragment, self.builder)
        self.assertNotIn("curl http:", self.builder)
        self.assertNotIn("latest/download", self.builder)

    def test_supply_chain_inputs_are_checksum_pinned(self) -> None:
        self.assertIn("APPIMAGETOOL_SHA256=", self.builder)
        self.assertIn("APPIMAGE_RUNTIME_SHA256=", self.builder)
        self.assertIn("curl --proto '=https' --tlsv1.2", self.builder)
        self.assertIn("printf '%s  %s\\n' \"$APPIMAGETOOL_SHA256\"", self.builder)
        self.assertIn("printf '%s  %s\\n' \"$APPIMAGE_RUNTIME_SHA256\"", self.builder)

    def test_garuda_package_is_primary_and_portable_fallbacks_are_real(self) -> None:
        self.assertIn("linux/PKGBUILD.in", self.builder)
        self.assertIn("fontblind-bin", self.pkgbuild)
        self.assertIn("makepkg", self.builder)
        self.assertIn("pkg.tar.zst", self.builder)
        self.assertIn(".AppImage", self.builder)
        self.assertIn(".tar.gz", self.builder)
        self.assertIn("Garuda Linux / Arch / KDE", self.architecture)
        self.assertIn("pkg.tar.zst", self.readme)
        self.assertIn("APPIMAGE_EXTRACT_AND_RUN=1", self.readme)
        self.assertIn("'hicolor-icon-theme' 'xdg-utils'", self.pkgbuild)
        self.assertIn("/usr/bin/fontblind", self.pkgbuild)

    def test_desktop_entry_never_opens_a_terminal_or_accepts_font_arguments(self) -> None:
        self.assertIn("Type=Application", self.desktop)
        self.assertIn("Exec=@EXEC@", self.desktop)
        self.assertIn("TryExec=@TRY_EXEC@", self.desktop)
        self.assertIn("Icon=fontblind", self.desktop)
        self.assertIn("Terminal=false", self.desktop)
        self.assertNotIn("%f", self.desktop)
        self.assertNotIn("%F", self.desktop)
        self.assertNotIn("http://", self.desktop)
        self.assertNotIn("https://", self.desktop)

    def test_linux_claims_remain_bounded(self) -> None:
        self.assertIn("x86_64 Garuda/Arch package", self.readme)
        self.assertIn("aarch64 is not claimed", self.readme)
        self.assertIn("Chromium/Electron runtime", self.readme)
        self.assertIn(".deb`, RPM, Flatpak, and Snap are intentionally deferred", self.architecture)


if __name__ == "__main__":
    unittest.main()
