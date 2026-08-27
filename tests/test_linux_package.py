from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GarudaPackageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = (ROOT / "build-fontblind-linux.sh").read_text(encoding="utf-8")
        cls.launcher = (ROOT / "linux" / "fontblind").read_text(encoding="utf-8")
        cls.desktop = (ROOT / "linux" / "fontblind.desktop.in").read_text(encoding="utf-8")
        cls.readme = (ROOT / "linux" / "README.txt").read_text(encoding="utf-8")
        cls.pkgbuild = (ROOT / "linux" / "PKGBUILD.in").read_text(encoding="utf-8")
        cls.verifier = (ROOT / "linux" / "verify-installed-package.sh").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        cls.project_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_builder_has_one_x86_64_pacman_artifact_and_one_runtime(self) -> None:
        for fragment in (
            '"$(uname -m)" != "x86_64"',
            '"$APP_DIR/fontblind_entry.py"',
            'release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT',
            "makepkg --nodeps --noconfirm --cleanbuild --clean",
            "fontblind-bin-${VERSION}-1-x86_64.pkg.tar.zst",
            'cmp --silent "$PACKAGE_ONE" "$PACKAGE_TWO"',
            "pacman -Qp",
            "bsdtar -tf",
            "sha256sum -c",
        ):
            self.assertIn(fragment, self.builder)
        for forbidden in (
            "appimagetool",
            "APPIMAGE_",
            ".AppImage",
            "runtime-aarch64",
            "aarch64|arm64",
            "--portable-only",
            "--arch-package-only",
            ".tar.gz",
        ):
            self.assertNotIn(forbidden, self.builder)

    def test_pkgbuild_is_fixed_to_the_garuda_arch_contract(self) -> None:
        self.assertIn("pkgname=fontblind-bin", self.pkgbuild)
        self.assertIn("arch=('x86_64')", self.pkgbuild)
        self.assertIn("depends=('hicolor-icon-theme' 'xdg-utils' 'kde-cli-tools')", self.pkgbuild)
        self.assertIn('$pkgdir/opt/fontblind', self.pkgbuild)
        self.assertIn('$pkgdir/usr/bin/fontblind', self.pkgbuild)
        self.assertNotIn("@ARCH@", self.pkgbuild)
        self.assertNotIn("AppRun", self.pkgbuild)

    def test_kde_launcher_is_root_refusing_and_uses_the_browser_host(self) -> None:
        self.assertIn('"$(id -u)" -eq 0', self.launcher)
        self.assertIn("/opt/fontblind/FontBlindServer/FontBlindServer", self.launcher)
        self.assertIn("--fontblind-browser-app", self.launcher)
        self.assertNotIn("xdg-open", self.launcher)
        self.assertNotIn("eval ", self.launcher)

    def test_desktop_entry_is_kde_visible_without_terminal_or_file_arguments(self) -> None:
        for fragment in (
            "Type=Application",
            "Exec=fontblind",
            "TryExec=fontblind",
            "Icon=fontblind",
            "Terminal=false",
            "Categories=Graphics;",
        ):
            self.assertIn(fragment, self.desktop)
        self.assertNotIn("Categories=Graphics;Utility;", self.desktop)
        for forbidden in ("%f", "%F", "AppImage", "http://", "https://"):
            self.assertNotIn(forbidden, self.desktop)

    def test_installed_gate_models_plasma_wayland_and_full_product_proof(self) -> None:
        for fragment in (
            "XDG_CURRENT_DESKTOP=KDE",
            "KDE_SESSION_VERSION=6",
            "XDG_SESSION_TYPE=wayland",
            "WAYLAND_DISPLAY=wayland-0",
            "env -u DISPLAY",
            "FONTBLIND_EXISTING",
            'mkdir -m 0700 -p "$ROOT/release-gauntlet"',
            "release_gauntlet.py",
            "/api/shutdown",
            "desktop.url",
        ):
            self.assertIn(fragment, self.verifier)
        for forbidden in ("nvidia-smi", "glxinfo", "vulkaninfo", "DISPLAY=:"):
            self.assertNotIn(forbidden, self.verifier)

    def test_ci_builds_installs_exercises_uninstalls_and_exports_the_arch_package(self) -> None:
        for fragment in (
            "container: archlinux:base-devel",
            "kde-cli-tools",
            "Build reproducible Garuda package",
            "pacman -U --noconfirm",
            "verify-installed-package.sh",
            "desktop-file-validate",
            "pacman -Rns --noconfirm fontblind-bin",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        ):
            self.assertIn(fragment, self.workflow)

    def test_public_claims_are_exactly_bounded(self) -> None:
        for text in (self.readme, self.project_readme, self.changelog):
            self.assertIn("Garuda", text)
            self.assertIn("KDE", text)
            self.assertIn("x86_64", text)
        for forbidden in ("AppImage", "aarch64", "Portable Linux", "Ubuntu package", "Fedora package"):
            self.assertNotIn(forbidden, self.readme)
            self.assertNotIn(forbidden, self.project_readme)
            self.assertNotIn(forbidden, self.changelog.split("## 3.6.0", 1)[0])

    def test_temporary_linux_scaffolding_is_absent(self) -> None:
        workflows = {path.name for path in (ROOT / ".github" / "workflows").glob("*.yml")}
        self.assertEqual(workflows, {"tests.yml"})
        self.assertFalse((ROOT / ".tmp-export-trigger").exists())
        self.assertFalse((ROOT / "tests" / "test_linux_packaging.py").exists())
        self.assertFalse((ROOT / "linux" / "fontblind-launcher.sh").exists())
        self.assertFalse((ROOT / "linux" / "PORTABLE-README.txt").exists())
        self.assertFalse((ROOT / "linux" / "fontblind.desktop").exists())
        self.assertEqual(
            {path.name for path in (ROOT / "docs").glob("LINUX*.md")},
            {"LINUX_ACCEPTANCE.md", "LINUX_ARCHITECTURE.md"},
        )
        self.assertEqual(
            {path.name for path in (ROOT / "tools").glob("*.py")},
            {"fetch_corpus.py"},
        )


if __name__ == "__main__":
    unittest.main()
