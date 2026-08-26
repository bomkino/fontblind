from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class BrowserPrivacyAndAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.instance = (WEB / "instance-export.js").read_text(encoding="utf-8")
        cls.app = (WEB / "app.js").read_text(encoding="utf-8")
        cls.index = (WEB / "index.html").read_text(encoding="utf-8")
        cls.styles = (WEB / "styles.css").read_text(encoding="utf-8")
        cls.map_styles = (WEB / "lab-map.css").read_text(encoding="utf-8")

    def test_browser_never_renders_unreviewed_server_error_text(self) -> None:
        self.assertIn("SAFE_SERVER_ERRORS", self.instance)
        self.assertIn("installErrorFirewall(root)", self.instance)
        self.assertNotIn("data && typeof data.error", self.instance)
        self.assertNotIn("source_filename", self.index)
        self.assertNotIn("localStorage", self.instance + self.app)
        self.assertNotIn("sessionStorage", self.instance + self.app)
        self.assertNotIn("console.", self.instance + self.app)

    def test_static_download_contract_rejects_identity_bearing_url_surfaces(self) -> None:
        self.assertIn("parsed.search || parsed.hash", self.instance)
        self.assertIn("fontblind-instance.ttf", self.instance)
        self.assertIn("fontblind-instance.woff2", self.instance)
        self.assertIn("fontblind-instance.css", self.instance)
        self.assertIn("fontblind-instance-package.zip", self.instance)
        self.assertIn("returned unexpected fields", self.instance)

    def test_stale_and_moved_instance_results_are_invalidated(self) -> None:
        self.assertIn("createOperationLedger", self.instance)
        self.assertIn("operationUsable", self.instance)
        self.assertIn("Coordinates changed during verification", self.instance)
        self.assertIn("Live coordinates changed", self.instance)
        self.assertNotIn("await discardChild(parent.token);", self.instance)
        self.assertIn("previousToken", self.instance)
        self.assertIn("previousFace", self.instance)

    def test_accessibility_layer_covers_navigation_focus_and_busy_state(self) -> None:
        for fragment in (
            'setAttribute("role", "tablist")',
            'setAttribute("role", "tab")',
            'setAttribute("role", "tabpanel")',
            'setAttribute("aria-selected"',
            'setAttribute("aria-busy"',
            'setAttribute("role", "status")',
            'setAttribute("aria-live", "polite")',
            'skip.dataset.skipWorkspace = "true"',
            'dropzone.setAttribute("aria-describedby"',
            'bench.toggleAttribute("inert", busy)',
        ):
            self.assertIn(fragment, self.instance)

    def test_existing_visual_layer_retains_focus_reflow_and_reduced_motion_support(self) -> None:
        combined = self.styles + self.map_styles
        self.assertIn(":focus-visible", combined)
        self.assertIn("@media (prefers-reduced-motion: reduce)", combined)
        self.assertIn("@media (max-width: 760px)", self.styles)
        self.assertIn("@media (max-width: 680px)", self.map_styles)

    def test_materialization_left_no_release_debris(self) -> None:
        self.assertFalse((ROOT / ".gate6-materialize").exists())
        self.assertFalse((ROOT / ".github" / "workflows" / "gate6-materialize.yml").exists())


if __name__ == "__main__":
    unittest.main()
