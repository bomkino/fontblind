from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from fontTools.ttLib import TTFont

from fontblind_instance import INSTANCE_NATIVE_NAME
from fontblind_instance_http import InstanceHandler
from fontblind_protocol import FONT_SET_MEDIA_TYPE, pack_font_set
from fontblind_runtime import ContractFontBlindServer
from tests.test_lab import write_fixture_font


class StaticInstanceHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ContractFontBlindServer(("127.0.0.1", 0), InstanceHandler)
        self.port = int(self.server.server_port)
        self.root = self.server.jobs.root
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.assertFalse(self.root.exists())

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def session(self) -> str:
        status, _, payload = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        return str(json.loads(payload)["session"])

    def variable_parent(self) -> tuple[str, dict[str, object]]:
        with tempfile.TemporaryDirectory(prefix="fontblind-instance-http-source-") as temp_text:
            root = Path(temp_text)
            regular = root / "revealing-regular.ttf"
            bold = root / "revealing-bold.ttf"
            write_fixture_font(regular, weight=400, family="Revealing HTTP Regular")
            write_fixture_font(bold, weight=700, family="Revealing HTTP Bold")
            body = pack_font_set([regular.read_bytes(), bold.read_bytes()])
        session = self.session()
        status, _, payload = self.request(
            "POST",
            "/api/lab/variable",
            body=body,
            headers={"Content-Type": FONT_SET_MEDIA_TYPE, "X-FontBlind-Session": session},
        )
        self.assertEqual(status, 200, payload)
        return session, json.loads(payload)

    def test_workbench_injects_local_static_export_controller(self) -> None:
        status, headers, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
        self.assertIn(b'<script src="/instance-export.js" defer></script>', payload)
        self.assertLess(payload.index(b"/instance-export.js"), payload.index(b"/app.js"))

        status, headers, payload = self.request("GET", "/instance-export.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn(b"FREEZE A STATIC INSTANCE", payload)

    def test_freezes_parent_location_and_exposes_only_static_outputs(self) -> None:
        session, parent = self.variable_parent()
        token = parent["job"]
        status, _, payload = self.request(
            "POST",
            f"/api/jobs/{token}/instance",
            body=json.dumps({"location": {"wght": 550}}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-FontBlind-Session": session},
        )
        self.assertEqual(status, 200, payload)
        result = json.loads(payload)
        self.assertEqual(result["native"]["filename"], INSTANCE_NATIVE_NAME)
        self.assertNotIn("axes", result)
        self.assertNotIn("masters", result)
        self.assertNotIn("revealing", payload.decode("utf-8").casefold())
        self.assertTrue(all(result["checks"].values()))

        status, _, native = self.request("GET", result["native"]["url"])
        self.assertEqual(status, 200)
        with tempfile.NamedTemporaryFile(suffix=".ttf") as target:
            target.write(native)
            target.flush()
            font = TTFont(target.name, lazy=False)
            try:
                self.assertNotIn("fvar", font)
                self.assertNotIn("gvar", font)
                self.assertEqual(int(font["OS/2"].usWeightClass), 550)
            finally:
                font.close()

    def test_rejects_missing_session_bad_locations_and_expired_parents(self) -> None:
        session, parent = self.variable_parent()
        token = parent["job"]
        body = json.dumps({"location": {"wght": 550}}).encode("utf-8")
        status, _, _ = self.request(
            "POST",
            f"/api/jobs/{token}/instance",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)

        for request in (
            {},
            {"location": {}},
            {"location": {"wght": 399}},
            {"location": {"wght": True}},
            {"location": {"wght": 550}, "extra": 1},
        ):
            status, _, _ = self.request(
                "POST",
                f"/api/jobs/{token}/instance",
                body=json.dumps(request).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-FontBlind-Session": session},
            )
            self.assertEqual(status, 400)

        status, _, payload = self.request(
            "DELETE",
            f"/api/jobs/{token}",
            headers={"X-FontBlind-Session": session},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["deleted"])
        status, _, _ = self.request(
            "POST",
            f"/api/jobs/{token}/instance",
            body=body,
            headers={"Content-Type": "application/json", "X-FontBlind-Session": session},
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
