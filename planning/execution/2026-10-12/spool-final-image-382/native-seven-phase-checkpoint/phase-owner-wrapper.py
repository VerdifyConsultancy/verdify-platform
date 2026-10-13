"""Finite serial phase wrapper; accepts only its immutable owner stage release."""

import asyncio
import hashlib
import json
import os
import resource
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID


async def wait_target_transport(host, *, timeout=10):
    """Bound policy/startup readiness without sending credentials or SQL."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    attempts = []
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 5432), timeout=min(1.0, deadline - loop.time())
            )
        except (OSError, TimeoutError) as error:
            attempts.append(type(error).__name__)
        else:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=min(1.0, max(0.01, deadline - loop.time())))
            attempts.append("connected")
            return attempts
        await asyncio.sleep(min(0.25, max(0.0, deadline - loop.time())))
    raise TimeoutError("bounded target transport readiness expired")


def validate_release(packet, release, phase):
    manifest = json.loads((packet / "fixture-manifest.json").read_text())
    binding = json.loads((packet / "native-binding.json").read_text())

    def sha(name):
        return hashlib.sha256((packet / name).read_bytes()).hexdigest()

    assert release["allowed"] is True and release["phase"] == phase
    assert release["run_id"] == manifest["run_id"]
    assert release["manifest_sha256"] == sha("fixture-manifest.json")
    assert release["binding_sha256"] == sha("native-binding.json")
    assert release["helper_sha256"] == sha("qualify-ingestor-committed-spool.py")
    assert release["wrapper_sha256"] == sha("phase-owner-wrapper.py")
    for key in (
        "cluster_uid",
        "primary_uid",
        "rw_service_uid",
        "database_oid",
        "system_identifier",
    ):
        assert release[key] == binding[key]
    assert release["sole_target_writer"] == "/root/runtime_inventory"
    assert release["fresh_capacity_fit"] is True and release["fresh_isolation_verified"] is True
    assert release["fresh_baseline_exact"] is True and release["previous_phase_accepted"] is True
    assert os.environ["QUALIFICATION_BINDING"] == (packet / "native-binding.json").read_text()
    return manifest


def capture_queues(manifest, state_root=Path("/qualification-state")):
    """Read stopped-child spool evidence without creating, recovering or ACKing it."""
    run_id = str(UUID(manifest["run_id"]))
    if run_id != manifest["run_id"]:
        raise ValueError("noncanonical run UUID")
    directory = state_root / ("s2-db-replay-" + run_id)
    receipts = {}
    for kind, filename in (
        ("climate", "climate.sqlite"),
        ("diagnostics", "observations.sqlite"),
        ("equipment", "equipment.sqlite"),
    ):
        path = directory / filename
        receipt = {"path": str(path), "status": "unavailable"}
        connection = None
        try:
            receipt["database_bytes"] = path.stat().st_size
            try:
                receipt["journal_bytes"] = Path(str(path) + "-journal").stat().st_size
            except FileNotFoundError:
                receipt["journal_bytes"] = 0
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
            connection.execute("PRAGMA query_only=ON")
            receipt["format"] = connection.execute("PRAGMA user_version").fetchone()[0]
            receipt["journal_mode"] = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if receipt["format"] != 1 or receipt["journal_mode"] != "delete":
                raise ValueError("unsupported spool format or journaling")
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            receipt["integrity_check_ok"] = integrity == [("ok",)]
            if not receipt["integrity_check_ok"]:
                raise ValueError("spool integrity refused")
            columns = connection.execute("PRAGMA table_info(source_queue)").fetchall()
            if [(row[1], row[2], row[3], row[5]) for row in columns] != [
                ("seq", "INTEGER", 0, 1),
                ("kind", "TEXT", 1, 0),
                ("identity", "TEXT", 1, 0),
                ("payload", "TEXT", 1, 0),
            ]:
                raise ValueError("unsupported source_queue schema")
            rows = connection.execute(
                "SELECT seq,kind,identity,CAST(payload AS BLOB) FROM source_queue ORDER BY seq LIMIT 21"
            ).fetchall()
            if len(rows) > 20:
                raise ValueError("spool exceeds fixed qualification bound")
            receipt["rows"] = [
                {
                    "seq": seq,
                    "kind": row_kind,
                    "identity": identity,
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                }
                for seq, row_kind, identity, payload in rows
            ]
            receipt["row_count"] = len(rows)
            receipt["status"] = "captured"
        except (OSError, sqlite3.Error, ValueError, TypeError) as error:
            # Exception messages/integrity errors can contain data; emit type only.
            receipt["error_type"] = type(error).__name__
        finally:
            if connection is not None:
                connection.close()
        receipts[kind] = receipt
    return receipts


def capture_spool_filesystem(manifest, state_root=Path("/qualification-state")):
    """Allocation metadata for the one fixed spool directory, without walking it."""
    directory = state_root / ("s2-db-replay-" + str(UUID(manifest["run_id"])))
    receipt = {"path": str(directory), "available": False}
    try:
        stats = os.statvfs(directory)
        receipt.update(
            available=True,
            capacity_bytes=stats.f_blocks * stats.f_frsize,
            available_bytes=stats.f_bavail * stats.f_frsize,
        )
    except OSError as error:
        receipt["reason"] = type(error).__name__
    return receipt


def capture_cgroup(cgroup_root=Path("/sys/fs/cgroup")):
    """Fixed, bounded container resource metadata; absent counters are unknown."""
    receipts = {}
    for name in (
        "memory.peak",
        "memory.current",
        "memory.events",
        "cpu.stat",
        "io.stat",
        "pids.peak",
    ):
        try:
            with (cgroup_root / name).open("r", encoding="ascii") as handle:
                value = handle.read(16385)
            if not value.strip() or len(value) > 16384:
                receipts[name] = {"available": False, "reason": "empty_or_over_bound"}
            else:
                receipts[name] = {"available": True, "value": value.strip()}
        except (OSError, UnicodeError) as error:
            receipts[name] = {"available": False, "reason": type(error).__name__}
    return receipts


def main():
    packet = Path("/qualification/packet")
    phase = sys.argv[1]
    release = json.loads(Path("/qualification/release/owner-stage-release.json").read_text())
    manifest = validate_release(packet, release, phase)
    readiness = {
        "kind": "transport_readiness",
        "phase": phase,
        "run_id": manifest["run_id"],
        "authentication_attempted": False,
        "sql_attempted": False,
        "child_started": False,
        "receipt_is_not_phase_acceptance": True,
    }
    try:
        attempts = asyncio.run(wait_target_transport(os.environ["DB_HOST"]))
    except (OSError, TimeoutError) as error:
        queues = capture_queues(manifest)
        readiness.update(
            status="failed",
            error_category=type(error).__name__,
            attempts=None,
            attempts_unavailable_reason="frozen_wait_raises_without_attempt_list",
            local_queues=queues,
            queue_capture_ok=all(receipt["status"] == "captured" for receipt in queues.values()),
            cgroup_at_readiness_failure=capture_cgroup(),
            spool_filesystem=capture_spool_filesystem(manifest),
        )
        print(json.dumps(readiness), flush=True)
        sys.exit(1)
    readiness.update(status="passed", attempts=attempts)
    print(json.dumps(readiness), flush=True)
    start = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            str(packet / "qualify-ingestor-committed-spool.py"),
            "--manifest",
            str(packet / "fixture-manifest.json"),
            "--phase",
            phase,
        ],
        check=False,
    )
    expected = 74 if phase.startswith("crash-") else 0
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    elapsed = time.monotonic() - start
    queues = capture_queues(manifest)
    queue_capture_ok = all(receipt["status"] == "captured" for receipt in queues.values())
    cgroup = capture_cgroup()
    filesystem = capture_spool_filesystem(manifest)
    # Linux KiB; this wrapper is staged only inside the pinned Linux image.
    print(
        json.dumps(
            {
                "phase": phase,
                "run_id": manifest["run_id"],
                "native_process_exit_code": result.returncode,
                "expected_process_exit_code": expected,
                "elapsed_seconds": elapsed,
                "child_peak_rss_kib": peak,
                "child_exit_expected": result.returncode == expected,
                "local_queues": queues,
                "queue_capture_ok": queue_capture_ok,
                "cgroup_after_child": cgroup,
                "spool_filesystem": filesystem,
                "postgres_commit_requires_owner_native_witness": True,
                "receipt_is_not_phase_acceptance": True,
            }
        ),
        flush=True,
    )
    sys.exit(0 if result.returncode == expected and queue_capture_ok else 1)


if __name__ == "__main__":
    main()
