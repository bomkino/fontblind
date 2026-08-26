from __future__ import annotations

from contextlib import contextmanager
import http.client
import inspect
from pathlib import Path
import re
import threading
import unittest
from unittest import mock

import fontblind_app
from fontblind_app import WEB_ROOT
from fontblind_contract import (
    LANE_BLIND,
    LANE_INSTANCE,
    LANE_OBLIQUE,
    LANE_SLANT,
    LANE_VARIABLE,
    _LANE_REQUIRED_CHECKS,
    _PARENT_CHECK,
)
from fontblind_instance_http import InstanceHandler
from fontblind_runtime import ContractFontBlindServer


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running_server():
    server = ContractFontBlindServer(("127.0.0.1", 0), InstanceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def request(server: ContractFontBlindServer, path: str) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def quoted_values(block: str) -> frozenset[str]:
    return frozenset(re.findall(r'"([a-z][a-z0-9_]*)"', block))


def browser_base_checks(source: str) -> dict[str, frozenset[str]]:
    block = re.search(
        r"const BASE_CHECKS = Object\.freeze\(\{(?P<body>.*?)\n  \}\);",
        source,
        flags=re.DOTALL,
    )
    if block is None:
        raise AssertionError("browser proof table is missing")
    result: dict[str, frozenset[str]] = {}
    for lane in ("blind", "oblique", "slant", "variable"):
        match = re.search(
            rf"\b{lane}:\s*Object\.freeze\(\[(?P<items>.*?)\]\)",
            block.group("body"),
            flags=re.DOTALL,
        )
        if match is None:
            raise AssertionError(f"browser proof table omitted {lane}")
        result[lane] = quoted_values(match.group("items"))
    return result


def browser_instance_checks(source: str) -> frozenset[str]:
    match = re.search(
        r"const STATIC_CHECKS = new Set\(\[(?P<items>.*?)\n  \]\);",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("browser static-instance proof table is missing")
    return quoted_values(match.group("items"))


class CanonicalRuntimeTests(unittest.TestCase):
    def test_source_launcher_delegates_to_the_hardened_runtime(self) -> None:
        source = inspect.getsource(fontblind_app.main)
        self.assertIn("fontblind_runtime", source)
        self.assertNotIn("FontBlindServer(", source)
        with mock.patch("fontblind_runtime.main", return_value=73) as runtime_main:
            self.assertEqual(fontblind_app.main(), 73)
        runtime_main.assert_called_once_with()

    def test_checked_in_shell_is_served_byte_for_byte(self) -> None:
        expected = (WEB_ROOT / "index.html").read_bytes()
        scripts = (
            b'<script src="/lab-proof.js" defer></script>',
            b'<script src="/result-contract.js" defer></script>',
            b'<script src="/instance-export.js" defer></script>',
            b'<script src="/desktop-runtime.js" defer></script>',
            b'<script src="/app.js" defer></script>',
        )
        for script in scripts:
            self.assertEqual(expected.count(script), 1)
        positions = [expected.index(script) for script in scripts]
        self.assertEqual(positions, sorted(positions))

        with running_server() as server:
            status, headers, payload = request(server, "/")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(payload, expected)
            for asset in ("/result-contract.js", "/instance-export.js", "/desktop-runtime.js"):
                status, asset_headers, asset_payload = request(server, asset)
                self.assertEqual(status, 200)
                self.assertIn("text/javascript", asset_headers["Content-Type"])
                self.assertGreater(len(asset_payload), 100)


class ProofContractParityTests(unittest.TestCase):
    def test_browser_tables_match_the_python_lane_contract_exactly(self) -> None:
        browser = (ROOT / "web" / "result-contract.js").read_text(encoding="utf-8")
        instance = (ROOT / "web" / "instance-export.js").read_text(encoding="utf-8")
        expected = {
            "blind": frozenset(_LANE_REQUIRED_CHECKS[LANE_BLIND] | {_PARENT_CHECK}),
            "oblique": frozenset(_LANE_REQUIRED_CHECKS[LANE_OBLIQUE] | {_PARENT_CHECK}),
            "slant": frozenset(_LANE_REQUIRED_CHECKS[LANE_SLANT] | {_PARENT_CHECK}),
            "variable": frozenset(_LANE_REQUIRED_CHECKS[LANE_VARIABLE] | {_PARENT_CHECK}),
        }
        self.assertEqual(browser_base_checks(browser), expected)
        self.assertEqual(
            browser_instance_checks(instance),
            frozenset(_LANE_REQUIRED_CHECKS[LANE_INSTANCE] | {_PARENT_CHECK}),
        )

        axis_pairs = dict(
            re.findall(
                r'if \(tags\.has\("([a-z0-9]+)"\)\) checks\.add\("([a-z0-9_]+)"\);',
                browser,
            )
        )
        self.assertEqual(
            axis_pairs,
            {"wght": "weight_axis_verified", "wdth": "width_axis_verified"},
        )


if __name__ == "__main__":
    unittest.main()
