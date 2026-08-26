from __future__ import annotations

import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import fontblind_runtime
from fontblind_runtime import ContractFontBlindServer, ContractJobStore
from fontblind_surgical import FontBlindError
from tests.test_lab import write_fixture_font


class RuntimeLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="fontblind-runtime-limits-")
        root = Path(cls._temporary.name)
        cls.regular = root / "regular.ttf"
        cls.bold = root / "bold.ttf"
        write_fixture_font(cls.regular, weight=400, family="Runtime Limits Regular")
        write_fixture_font(cls.bold, weight=700, family="Runtime Limits Bold")
        cls.regular_bytes = cls.regular.read_bytes()
        cls.bold_bytes = cls.bold.read_bytes()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def variable_parent(self, store: ContractJobStore):
        return store.create_variable([self.regular_bytes, self.bold_bytes])

    def test_job_count_limit_rejects_new_output_without_destroying_old_output(self) -> None:
        with mock.patch.object(fontblind_runtime, "MAX_RETAINED_JOBS", 1):
            store = ContractJobStore()
            store_root = store.root
            try:
                first_token, first = store.create(self.regular_bytes)
                self.assertEqual(store.retained_usage()[0], 1)
                with self.assertRaises(FontBlindError):
                    store.create(self.regular_bytes)
                self.assertIs(store.get(first_token), first)
                self.assertTrue(first.path.exists())
                self.assertEqual(store.retained_usage()[0], 1)
                self.assertEqual(len([path for path in store_root.iterdir() if path.is_dir()]), 1)
            finally:
                store.close()
                self.assertFalse(store_root.exists())

    def test_byte_limit_rejects_unexposed_output_and_leaves_no_job(self) -> None:
        with mock.patch.object(fontblind_runtime, "MAX_RETAINED_BYTES", 1):
            store = ContractJobStore()
            store_root = store.root
            try:
                with self.assertRaises(FontBlindError):
                    store.create(self.regular_bytes)
                self.assertEqual(store.retained_usage(), (0, 0))
                self.assertFalse(any(path.is_dir() for path in store_root.iterdir()))
            finally:
                store.close()
                self.assertFalse(store_root.exists())

    def test_replacement_child_keeps_usage_bounded_and_parent_delete_releases_every_byte(self) -> None:
        store = ContractJobStore()
        store_root = store.root
        try:
            parent_token, _parent = self.variable_parent(store)
            parent_count, parent_bytes = store.retained_usage()
            self.assertEqual(parent_count, 1)
            self.assertGreater(parent_bytes, 0)

            first_token, _first = store.create_instance(parent_token, {"wght": 500})
            first_count, first_bytes = store.retained_usage()
            self.assertEqual(first_count, 2)
            self.assertGreater(first_bytes, parent_bytes)

            second_token, _second = store.create_instance(parent_token, {"wght": 600})
            second_count, second_bytes = store.retained_usage()
            self.assertEqual(second_count, 2)
            self.assertNotEqual(first_token, second_token)
            self.assertIsNone(store.get(first_token))
            self.assertGreater(second_bytes, parent_bytes)

            self.assertTrue(store.delete(parent_token))
            self.assertEqual(store.retained_usage(), (0, 0))
            self.assertIsNone(store.get(second_token))
        finally:
            store.close()
            self.assertFalse(store_root.exists())

    def test_concurrent_freeze_for_one_parent_is_rejected_before_second_build(self) -> None:
        store = ContractJobStore()
        store_root = store.root
        release = threading.Event()
        started = threading.Event()
        results: list[tuple[str, object]] = []
        errors: list[BaseException] = []
        try:
            parent_token, _parent = self.variable_parent(store)
            real_create_stream = store._create_stream

            def slow_create_stream(*args, **kwargs):  # type: ignore[no-untyped-def]
                started.set()
                if not release.wait(timeout=10):
                    raise RuntimeError("test did not release the first instance build")
                return real_create_stream(*args, **kwargs)

            def freeze_first() -> None:
                try:
                    results.append(store.create_instance(parent_token, {"wght": 525}))
                except BaseException as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            with mock.patch.object(store, "_create_stream", side_effect=slow_create_stream):
                thread = threading.Thread(target=freeze_first)
                thread.start()
                self.assertTrue(started.wait(timeout=5))
                with self.assertRaisesRegex(FontBlindError, "already running"):
                    store.create_instance(parent_token, {"wght": 575})
                release.set()
                thread.join(timeout=30)

            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertEqual(len(results), 1)
            self.assertEqual(store.retained_usage()[0], 2)
        finally:
            release.set()
            store.close()
            self.assertFalse(store_root.exists())

    def test_parent_delete_during_freeze_discards_the_unexposed_child(self) -> None:
        store = ContractJobStore()
        store_root = store.root
        release = threading.Event()
        started = threading.Event()
        errors: list[BaseException] = []
        try:
            parent_token, _parent = self.variable_parent(store)
            real_create_stream = store._create_stream

            def slow_create_stream(*args, **kwargs):  # type: ignore[no-untyped-def]
                started.set()
                if not release.wait(timeout=10):
                    raise RuntimeError("test did not release the instance build")
                return real_create_stream(*args, **kwargs)

            def freeze() -> None:
                try:
                    store.create_instance(parent_token, {"wght": 550})
                except BaseException as exc:
                    errors.append(exc)

            with mock.patch.object(store, "_create_stream", side_effect=slow_create_stream):
                thread = threading.Thread(target=freeze)
                thread.start()
                self.assertTrue(started.wait(timeout=5))
                self.assertTrue(store.delete(parent_token))
                release.set()
                thread.join(timeout=30)

            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], FontBlindError)
            self.assertEqual(store.retained_usage(), (0, 0))
            self.assertFalse(any(path.is_dir() for path in store_root.iterdir()))
        finally:
            release.set()
            store.close()
            self.assertFalse(store_root.exists())

    def test_download_snapshot_gate_applies_backpressure(self) -> None:
        server = ContractFontBlindServer(("127.0.0.1", 0))
        root = server.jobs.root
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        acquired = 0
        try:
            token, job = server.jobs.create(self.regular_bytes)
            for _ in range(fontblind_runtime.MAX_CONCURRENT_DOWNLOADS):
                self.assertTrue(server.download_gate.acquire(blocking=False))
                acquired += 1

            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                connection.request("GET", f"/download/{token}/native")
                response = connection.getresponse()
                self.assertEqual(response.status, 429)
                response.read()
            finally:
                connection.close()

            for _ in range(acquired):
                server.download_gate.release()
            acquired = 0

            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
            try:
                connection.request("GET", f"/download/{token}/native")
                response = connection.getresponse()
                payload = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(int(response.getheader("Content-Length")), len(payload))
                self.assertEqual(
                    response.getheader("Content-Disposition"),
                    f'attachment; filename="{job.result.native.filename}"',
                )
            finally:
                connection.close()
        finally:
            for _ in range(acquired):
                server.download_gate.release()
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
