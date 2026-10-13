"""Offline tests: synthetic temporary SQLite files only, no qualification helper."""

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).parent
SOURCE = Path("/private/tmp/verdify-spool-final-image-382-20261012/ingestor/source_spool.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wrapper = load("phase_owner_wrapper", ROOT / "phase-owner-wrapper.py")
SourceSpool = load("test_original_source_spool", SOURCE).SourceSpool
RUN_ID = "a62bd3cf-baf3-4f39-a1e3-2fbde547ca93"
IDS = ("ffffffff-ffff-4fff-8fff-ffffffffffff", "00000000-0000-4000-8000-000000000000")


class QueueReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {"run_id": RUN_ID}
        self.directory = self.root / ("s2-db-replay-" + RUN_ID)

    def spools(self, populated=True):
        for kind, name in (
            ("climate", "climate.sqlite"),
            ("diagnostics", "observations.sqlite"),
            ("receipt", "equipment.sqlite"),
        ):
            spool = SourceSpool(self.directory / name, max_rows=20, max_bytes=1048576)
            if populated:
                for identity in IDS:
                    spool.put(
                        kind,
                        identity,
                        {"synthetic_marker": "DO_NOT_EMIT_é", "identity": identity},
                    )
            spool.close()

    def image(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_real_source_schema_exact_payload_hash_source_order_read_only(self):
        self.spools()
        before = self.image()
        result = wrapper.capture_queues(self.manifest, self.root)
        self.assertEqual(before, self.image())
        self.assertEqual(set(result), {"climate", "diagnostics", "equipment"})
        for receipt in result.values():
            self.assertEqual(receipt["status"], "captured")
            self.assertEqual(receipt["format"], 1)
            self.assertEqual(receipt["journal_mode"], "delete")
            self.assertGreater(receipt["database_bytes"], 0)
            self.assertEqual(receipt["database_bytes"], Path(receipt["path"]).stat().st_size)
            self.assertEqual(receipt["journal_bytes"], 0)
            self.assertTrue(receipt["integrity_check_ok"])
            self.assertEqual(receipt["row_count"], 2)
            self.assertEqual([r["identity"] for r in receipt["rows"]], list(IDS))
            self.assertEqual([r["seq"] for r in receipt["rows"]], [1, 2])
            for row in receipt["rows"]:
                payload = json.dumps(
                    {"synthetic_marker": "DO_NOT_EMIT_é", "identity": row["identity"]},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                self.assertEqual(row["payload_sha256"], hashlib.sha256(payload.encode()).hexdigest())
                self.assertEqual(set(row), {"seq", "kind", "identity", "payload_sha256"})
        self.assertNotIn("DO_NOT_EMIT", json.dumps(result))
        filesystem = wrapper.capture_spool_filesystem(self.manifest, self.root)
        stats = wrapper.os.statvfs(self.directory)
        self.assertTrue(filesystem["available"])
        self.assertEqual(filesystem["capacity_bytes"], stats.f_blocks * stats.f_frsize)
        self.assertGreater(filesystem["available_bytes"], 0)
        self.assertLessEqual(filesystem["available_bytes"], filesystem["capacity_bytes"])

    def test_empty_acked_queues_are_captured_without_repopulation(self):
        self.spools(populated=False)
        before = self.image()
        result = wrapper.capture_queues(self.manifest, self.root)
        self.assertEqual(before, self.image())
        self.assertTrue(
            all(r["status"] == "captured" and r["row_count"] == 0 and r["rows"] == [] for r in result.values())
        )

    def test_missing_files_are_not_created(self):
        result = wrapper.capture_queues(self.manifest, self.root)
        self.assertFalse(self.directory.exists())
        filesystem = wrapper.capture_spool_filesystem(self.manifest, self.root)
        self.assertEqual(
            filesystem,
            {
                "path": str(self.directory),
                "available": False,
                "reason": "FileNotFoundError",
            },
        )
        self.assertTrue(
            all(r["status"] == "unavailable" and r["error_type"] == "FileNotFoundError" for r in result.values())
        )

    def test_corrupt_file_is_retained_and_error_contains_no_payload(self):
        self.spools()
        p = self.directory / "climate.sqlite"
        p.write_bytes(b"DO_NOT_EMIT_CORRUPT_PAYLOAD")
        before = self.image()
        result = wrapper.capture_queues(self.manifest, self.root)
        self.assertEqual(before, self.image())
        self.assertEqual(result["climate"]["status"], "unavailable")
        self.assertEqual(result["climate"]["error_type"], "DatabaseError")
        self.assertNotIn("DO_NOT_EMIT", json.dumps(result))
        self.assertEqual(result["equipment"]["status"], "captured")

    def test_unknown_format_refused_without_repair(self):
        self.spools()
        p = self.directory / "climate.sqlite"
        with contextlib.closing(sqlite3.connect(p)) as conn, conn:
            conn.execute("PRAGMA user_version=2")
        before = self.image()
        result = wrapper.capture_queues(self.manifest, self.root)
        self.assertEqual(before, self.image())
        self.assertEqual(result["climate"]["format"], 2)
        self.assertEqual(result["climate"]["status"], "unavailable")
        self.assertNotIn("rows", result["climate"])

    def test_schema_drift_refused(self):
        self.spools()
        p = self.directory / "climate.sqlite"
        with contextlib.closing(sqlite3.connect(p)) as conn, conn:
            conn.execute("ALTER TABLE source_queue ADD COLUMN unexpected TEXT")
        before = self.image()
        result = wrapper.capture_queues(self.manifest, self.root)
        self.assertEqual(before, self.image())
        self.assertEqual(result["climate"]["status"], "unavailable")
        self.assertEqual(result["climate"]["error_type"], "ValueError")

    def test_readonly_uri_and_query_only_prevent_writes(self):
        self.spools()
        original_connect = sqlite3.connect
        seen = []

        class ConnectionProbe:
            def __init__(self, conn):
                self.conn = conn
                self.calls = []

            def execute(self, sql):
                self.calls.append(sql)
                result = self.conn.execute(sql)
                if sql == "PRAGMA query_only=ON":
                    with self_test.assertRaises(sqlite3.OperationalError):
                        self.conn.execute("DELETE FROM source_queue")
                return result

            def close(self):
                self.conn.close()

        self_test = self

        def connect(database, **kwargs):
            self.assertTrue(database.endswith("?mode=ro"))
            self.assertTrue(kwargs["uri"])
            conn = ConnectionProbe(original_connect(database, **kwargs))
            seen.append(conn)
            return conn

        before = self.image()
        with mock.patch.object(wrapper.sqlite3, "connect", side_effect=connect):
            result = wrapper.capture_queues(self.manifest, self.root)
        self.assertEqual(before, self.image())
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(r["status"] == "captured" for r in result.values()))
        self.assertTrue(all(c.calls[0] == "PRAGMA query_only=ON" for c in seen))

    def test_owner_release_binds_exact_wrapper_bytes_and_refuses_tamper(self):
        packet = self.root / "packet"
        packet.mkdir()
        binding = {
            "cluster_uid": "test-cluster",
            "primary_uid": "test-primary",
            "rw_service_uid": "test-rw",
            "database_oid": 123,
            "system_identifier": "test-system",
        }
        files = {
            "fixture-manifest.json": json.dumps(self.manifest),
            "native-binding.json": json.dumps(binding),
            "qualify-ingestor-committed-spool.py": "# synthetic helper, never executed\n",
            "phase-owner-wrapper.py": (ROOT / "phase-owner-wrapper.py").read_text(),
        }
        for name, value in files.items():
            (packet / name).write_text(value)
        release = {
            "allowed": True,
            "phase": "unavailable",
            "run_id": RUN_ID,
            "sole_target_writer": "/root/runtime_inventory",
            "fresh_capacity_fit": True,
            "fresh_isolation_verified": True,
            "fresh_baseline_exact": True,
            "previous_phase_accepted": True,
            **binding,
        }
        for key, name in (
            ("manifest_sha256", "fixture-manifest.json"),
            ("binding_sha256", "native-binding.json"),
            ("helper_sha256", "qualify-ingestor-committed-spool.py"),
            ("wrapper_sha256", "phase-owner-wrapper.py"),
        ):
            release[key] = hashlib.sha256((packet / name).read_bytes()).hexdigest()
        with mock.patch.dict(wrapper.os.environ, {"QUALIFICATION_BINDING": files["native-binding.json"]}):
            self.assertEqual(wrapper.validate_release(packet, release, "unavailable"), self.manifest)
            with (packet / "phase-owner-wrapper.py").open("a") as handle:
                handle.write("# altered wrapper\n")
            with self.assertRaises(AssertionError):
                wrapper.validate_release(packet, release, "unavailable")

    def test_noncanonical_run_path_refused_without_creating_files(self):
        with self.assertRaises(ValueError):
            wrapper.capture_queues({"run_id": "../../elsewhere"}, self.root)
        self.assertEqual(self.image(), {})

    def run_main(
        self,
        child_exit,
        phase,
        populated=True,
        missing=False,
        readiness_error=None,
        readiness_attempts=None,
    ):
        if not missing:
            self.spools(populated)
        self.main_before_image = self.image()
        self.main_events = []
        self.main_records_at_child = None
        capture = wrapper.capture_queues
        capture_filesystem = wrapper.capture_spool_filesystem
        stream = io.StringIO()
        cgroup = {"memory.peak": {"available": False, "reason": "FileNotFoundError"}}
        ticks = iter((10.0, 12.5))

        async def wait_target_transport(host):
            self.assertEqual(host, "synthetic-readiness.invalid")
            self.main_events.append("readiness")
            if readiness_error is not None:
                raise readiness_error
            return ["connected"] if readiness_attempts is None else readiness_attempts

        def child(*args, **kwargs):
            self.main_records_at_child = [json.loads(line) for line in stream.getvalue().splitlines()]
            self.main_events.append("child")
            return SimpleNamespace(returncode=child_exit)

        def monotonic():
            self.main_events.append("child_timer")
            return next(ticks)

        with (
            mock.patch.object(wrapper, "validate_release", return_value=self.manifest),
            mock.patch.object(wrapper.Path, "read_text", return_value="{}"),
            mock.patch.dict(wrapper.os.environ, {"DB_HOST": "synthetic-readiness.invalid"}),
            mock.patch.object(wrapper, "wait_target_transport", new=wait_target_transport),
            mock.patch.object(
                wrapper.subprocess,
                "run",
                side_effect=child,
            ) as child_run,
            mock.patch.object(wrapper, "capture_queues", side_effect=lambda m: capture(m, self.root)),
            mock.patch.object(wrapper, "capture_cgroup", return_value=cgroup),
            mock.patch.object(
                wrapper,
                "capture_spool_filesystem",
                side_effect=lambda m: capture_filesystem(m, self.root),
            ),
            mock.patch.object(
                wrapper.resource,
                "getrusage",
                return_value=SimpleNamespace(ru_maxrss=12345),
            ),
            mock.patch.object(wrapper, "time", SimpleNamespace(monotonic=monotonic)),
            mock.patch.object(wrapper.sys, "argv", ["wrapper", phase]),
            contextlib.redirect_stdout(stream),
            self.assertRaises(SystemExit) as error,
        ):
            wrapper.main()
        self.main_child_calls = child_run.call_count
        self.main_records = [json.loads(line) for line in stream.getvalue().splitlines()]
        return error.exception.code, self.main_records[-1]

    def test_readiness_failure_prevents_child_and_captures_queues_read_only(self):
        for failure in (
            OSError("DO_NOT_EMIT_READINESS_OS_ERROR"),
            TimeoutError("DO_NOT_EMIT_READINESS_TIMEOUT"),
        ):
            with self.subTest(error_category=type(failure).__name__):
                code, receipt = self.run_main(74, "crash-observation", readiness_error=failure)
                self.assertEqual(code, 1)
                self.assertEqual(self.main_child_calls, 0)
                self.assertEqual(self.main_events, ["readiness"])
                self.assertEqual(len(self.main_records), 1)
                self.assertEqual(receipt["kind"], "transport_readiness")
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["phase"], "crash-observation")
                self.assertEqual(receipt["run_id"], RUN_ID)
                self.assertEqual(receipt["error_category"], type(failure).__name__)
                self.assertIsNone(receipt["attempts"])
                self.assertEqual(
                    receipt["attempts_unavailable_reason"],
                    "frozen_wait_raises_without_attempt_list",
                )
                for flag in (
                    "authentication_attempted",
                    "sql_attempted",
                    "child_started",
                ):
                    self.assertIs(receipt[flag], False)
                self.assertTrue(receipt["queue_capture_ok"])
                self.assertTrue(
                    all(
                        queue["status"] == "captured" and queue["row_count"] == 2
                        for queue in receipt["local_queues"].values()
                    )
                )
                self.assertEqual(
                    receipt["cgroup_at_readiness_failure"],
                    {"memory.peak": {"available": False, "reason": "FileNotFoundError"}},
                )
                self.assertTrue(receipt["spool_filesystem"]["available"])
                self.assertEqual(self.main_before_image, self.image())
                self.assertNotIn("DO_NOT_EMIT", json.dumps(receipt))
                self.assertNotIn("native_process_exit_code", receipt)
                self.assertNotIn("elapsed_seconds", receipt)

    def test_readiness_emits_before_one_child_and_child_timing_starts_after_wait(self):
        attempts = ["ConnectionRefusedError", "connected"]
        code, receipt = self.run_main(74, "crash-climate", readiness_attempts=attempts)
        self.assertEqual(code, 0)
        self.assertEqual(self.main_child_calls, 1)
        self.assertEqual(len(self.main_records), 2)
        readiness = self.main_records[0]
        self.assertEqual(readiness["kind"], "transport_readiness")
        self.assertEqual(readiness["status"], "passed")
        self.assertEqual(readiness["phase"], "crash-climate")
        self.assertEqual(readiness["run_id"], RUN_ID)
        self.assertEqual(readiness["attempts"], attempts)
        for flag in ("authentication_attempted", "sql_attempted", "child_started"):
            self.assertIs(readiness[flag], False)
        self.assertEqual(self.main_records_at_child, [readiness])
        self.assertEqual(self.main_events, ["readiness", "child_timer", "child", "child_timer"])
        self.assertEqual(receipt["elapsed_seconds"], 2.5)
        self.assertEqual(receipt["child_peak_rss_kib"], 12345)
        self.assertEqual(receipt["native_process_exit_code"], 74)

    def test_wait_target_transport_ast_matches_frozen_runtime_duties(self):
        frozen = Path("/private/tmp/verdify-contained-recovery-20261012/scripts/qualify-restored-runtime-duties.py")

        def wait_ast(path):
            functions = [
                node
                for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "wait_target_transport"
            ]
            self.assertEqual(len(functions), 1)
            return ast.dump(functions[0], include_attributes=False)

        self.assertEqual(wait_ast(ROOT / "phase-owner-wrapper.py"), wait_ast(frozen))

    def test_expected_crash_exit_emits_queue_receipt_and_preserves_timing(self):
        code, receipt = self.run_main(74, "crash-climate")
        self.assertEqual(code, 0)
        self.assertEqual(receipt["native_process_exit_code"], 74)
        self.assertTrue(receipt["child_exit_expected"])
        self.assertTrue(receipt["queue_capture_ok"])
        self.assertTrue(receipt["spool_filesystem"]["available"])
        self.assertGreater(receipt["spool_filesystem"]["capacity_bytes"], 0)
        self.assertEqual(receipt["elapsed_seconds"], 2.5)
        self.assertEqual(receipt["child_peak_rss_kib"], 12345)
        self.assertTrue(receipt["receipt_is_not_phase_acceptance"])
        self.assertTrue(receipt["postgres_commit_requires_owner_native_witness"])
        self.assertEqual(receipt["local_queues"]["climate"]["rows"][0]["identity"], IDS[0])
        self.assertNotIn("DO_NOT_EMIT", json.dumps(receipt))

    def test_expected_replay_exit_captures_empty_queues(self):
        code, receipt = self.run_main(0, "replay", populated=False)
        self.assertEqual(code, 0)
        self.assertTrue(all(r["row_count"] == 0 for r in receipt["local_queues"].values()))

    def test_expected_exit_with_missing_queue_fails_wrapper(self):
        code, receipt = self.run_main(74, "crash-equipment", missing=True)
        self.assertEqual(code, 1)
        self.assertTrue(receipt["child_exit_expected"])
        self.assertFalse(receipt["queue_capture_ok"])

    def test_unexpected_native_exit_still_captures_queues_and_fails(self):
        code, receipt = self.run_main(1, "crash-observation")
        self.assertEqual(code, 1)
        self.assertFalse(receipt["child_exit_expected"])
        self.assertTrue(receipt["queue_capture_ok"])


class CgroupReceiptTests(unittest.TestCase):
    def test_fixed_metadata_only_paths_missing_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "memory.peak").write_text("123456\n")
            (root / "cpu.stat").write_text("usage_usec 42\nuser_usec 12\n")
            (root / "unrelated").write_text("DO_NOT_EMIT")
            result = wrapper.capture_cgroup(root)
        self.assertEqual(
            set(result),
            {
                "memory.peak",
                "memory.current",
                "memory.events",
                "cpu.stat",
                "io.stat",
                "pids.peak",
            },
        )
        self.assertEqual(result["memory.peak"], {"available": True, "value": "123456"})
        self.assertEqual(result["cpu.stat"]["value"], "usage_usec 42\nuser_usec 12")
        self.assertEqual(result["pids.peak"], {"available": False, "reason": "FileNotFoundError"})
        self.assertNotIn("DO_NOT_EMIT", json.dumps(result))

    def test_empty_or_over_bound_counter_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "memory.peak").write_text("")
            (root / "io.stat").write_text("1" * 16385)
            result = wrapper.capture_cgroup(root)
        self.assertEqual(result["memory.peak"], {"available": False, "reason": "empty_or_over_bound"})
        self.assertEqual(result["io.stat"], {"available": False, "reason": "empty_or_over_bound"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
