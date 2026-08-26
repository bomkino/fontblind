from __future__ import annotations

import unittest
from http import HTTPStatus
from types import SimpleNamespace

from fontblind_instance_http import InstanceHandler


class RecordingGate:
    def __init__(self, available: bool) -> None:
        self.available = available
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self, blocking: bool = True) -> bool:
        self.acquire_calls += 1
        if blocking:
            raise AssertionError("instance admission must not block a request thread")
        return self.available

    def release(self) -> None:
        self.release_calls += 1


class Jobs:
    def __init__(self, parent: object) -> None:
        self.parent = parent

    def get(self, _token: str) -> object:
        return self.parent


class InstanceAdmissionTests(unittest.TestCase):
    def handler(self, gate: RecordingGate) -> tuple[InstanceHandler, list[tuple[int, dict[str, object]]]]:
        parent = SimpleNamespace(
            result=SimpleNamespace(
                variable=True,
                axes=({"tag": "wght", "min": 400.0, "max": 700.0},),
                native=SimpleNamespace(media_type="font/ttf"),
            )
        )
        handler = object.__new__(InstanceHandler)
        handler.path = f"/api/jobs/{'a' * 32}/instance"
        handler.headers = {"Content-Type": "application/json"}
        handler.server = SimpleNamespace(jobs=Jobs(parent), worker_gate=gate)
        handler._host_ok = lambda: True
        handler._session_ok = lambda: True
        responses: list[tuple[int, dict[str, object]]] = []
        handler._json = lambda status, value: responses.append((int(status), value))
        return handler, responses

    def test_busy_instance_endpoint_rejects_before_inspecting_the_body(self) -> None:
        gate = RecordingGate(available=False)
        handler, responses = self.handler(gate)

        def body_was_touched(*_args: object) -> None:
            raise AssertionError("busy instance request inspected its body before admission")

        handler._body_length = body_was_touched
        handler.do_POST()

        self.assertEqual(gate.acquire_calls, 1)
        self.assertEqual(gate.release_calls, 0)
        self.assertEqual(responses[0][0], HTTPStatus.TOO_MANY_REQUESTS)

    def test_admitted_instance_request_releases_slot_when_framing_is_rejected(self) -> None:
        gate = RecordingGate(available=True)
        handler, responses = self.handler(gate)
        handler._body_length = lambda *_args: None

        handler.do_POST()

        self.assertEqual(gate.acquire_calls, 1)
        self.assertEqual(gate.release_calls, 1)
        self.assertEqual(responses, [])


if __name__ == "__main__":
    unittest.main()
