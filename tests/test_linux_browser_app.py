from __future__ import annotations

import http.client
import json
import threading
import unittest
from unittest import mock

import fontblind_entry
from fontblind_instance_http import InstanceHandler
from fontblind_runtime import ContractFontBlindServer


def request(
    server: ContractFontBlindServer,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class BrowserHostedDesktopTests(unittest.TestCase):
    def test_frozen_entry_routes_native_browser_and_worker_modes_explicitly(self) -> None:
        with mock.patch("fontblind_entry._run_server", return_value=17) as run_server:
            self.assertEqual(fontblind_entry.main(["FontBlindServer"]), 17)
            run_server.assert_called_once_with()

        with mock.patch("fontblind_entry._run_server", return_value=18) as run_server:
            self.assertEqual(
                fontblind_entry.main(["FontBlindServer", "--fontblind-browser-app"]),
                18,
            )
            run_server.assert_called_once_with(open_browser=True, allow_browser_shutdown=True)

        with mock.patch("fontblind_entry._run_server", return_value=19) as run_server:
            self.assertEqual(
                fontblind_entry.main(
                    ["FontBlindServer", "--fontblind-browser-app", "--no-open"]
                ),
                19,
            )
            run_server.assert_called_once_with(open_browser=False, allow_browser_shutdown=True)

        with mock.patch("fontblind_entry._run_worker", return_value=20) as run_worker:
            arguments = ["FontBlindServer", "--fontblind-worker", "blind"]
            self.assertEqual(fontblind_entry.main(arguments), 20)
            run_worker.assert_called_once_with(arguments)

        self.assertEqual(fontblind_entry.main(["FontBlindServer", "--unknown"]), 64)

    def test_regular_runtime_does_not_expose_browser_shutdown(self) -> None:
        server = ContractFontBlindServer(("127.0.0.1", 0), InstanceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _, payload = request(server, "GET", "/api/session")
            self.assertEqual(status, 200)
            session = json.loads(payload)
            self.assertEqual(set(session), {"ok", "session", "can_quit"})
            self.assertFalse(session["can_quit"])

            status, _, payload = request(
                server,
                "POST",
                "/api/shutdown",
                headers={"X-FontBlind-Session": session["session"]},
            )
            self.assertEqual(status, 404)
            self.assertEqual(json.loads(payload), {"ok": False, "error": "Not found."})
            self.assertTrue(thread.is_alive())
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_browser_hosted_runtime_requires_session_then_closes_cleanly(self) -> None:
        server = ContractFontBlindServer(
            ("127.0.0.1", 0),
            InstanceHandler,
            allow_browser_shutdown=True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _, payload = request(server, "GET", "/api/session")
            self.assertEqual(status, 200)
            session = json.loads(payload)
            self.assertTrue(session["can_quit"])

            status, _, _ = request(server, "POST", "/api/shutdown")
            self.assertEqual(status, 403)
            self.assertTrue(thread.is_alive())

            status, _, payload = request(
                server,
                "POST",
                "/api/shutdown",
                headers={"X-FontBlind-Session": session["session"]},
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload), {"ok": True, "shutdown": True})
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=2)
            server.server_close()

    def test_shutdown_endpoint_refuses_request_bodies(self) -> None:
        server = ContractFontBlindServer(
            ("127.0.0.1", 0),
            InstanceHandler,
            allow_browser_shutdown=True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _, _, payload = request(server, "GET", "/api/session")
            session = json.loads(payload)["session"]
            status, _, payload = request(
                server,
                "POST",
                "/api/shutdown",
                headers={"X-FontBlind-Session": session},
                body=b"x",
            )
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(payload), {"ok": False, "error": "Not found."})
            self.assertTrue(thread.is_alive())

            status, _, payload = request(
                server,
                "POST",
                "/api/shutdown",
                headers={
                    "X-FontBlind-Session": session,
                    "Content-Length": "9" * 1000,
                },
            )
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(payload), {"ok": False, "error": "Not found."})
            self.assertTrue(thread.is_alive())
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
