from __future__ import annotations

import http.client
import threading
import unittest

from fontblind_instance_http import InstanceHandler
from fontblind_runtime import ContractFontBlindServer


class ResultContractHttpTests(unittest.TestCase):
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

    def request(self, path: str) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_contract_guard_precedes_instance_controller_and_application(self) -> None:
        status, headers, payload = self.request("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
        contract = payload.index(b'<script src="/result-contract.js" defer></script>')
        instance = payload.index(b'<script src="/instance-export.js" defer></script>')
        application = payload.index(b'<script src="/app.js" defer></script>')
        self.assertLess(contract, instance)
        self.assertLess(instance, application)

        status, headers, script = self.request("/result-contract.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn(b"installFetchGuard", script)
        self.assertIn(b"wrong verification contract", script)


if __name__ == "__main__":
    unittest.main()
