"""Bounded clone-only actual ingestor committed replay, never normal main().

ROOT serializes phases after native parity and protected Backup/WAL custody.
Run in the exact final image with target-only ordinary credentials in env.
Expected crash phases terminate74 immediately after DB commit, before ACK.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

SOURCE = "cb21fd3d3295aa8d7183f70b0d5ac82405410b1a"
MODULE_SHA = "b8f3957fa98a3fc963253725fc3cfd328d93234027f40934d700e123ce45182d"
IMAGE = "registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:c6cc8d250ef8dabe3bc3bfa4c5bc759c845508a029eae52ea4b3fadde5cd773f"
CLUSTER = "verdify-cnpg-s2-integration"
SYSTEM_IDENTIFIER = "7693399121498484768"
MODULE_SHAS = {
    "ingestor.py": MODULE_SHA,
    "source_spool.py": "8d31c70d81ca5c68875fdce6b28c96feb0ee36469aa354df6eb0e44dfa6f7983",
    "observation_spool.py": "643a8aa376d32d44a56341a5f9f2573370971f1ac9d2adca4e03975efef7b207",
}
LOGIN = "verdify_ingestor_runtime_login"
HOST = "verdify-cnpg-s2-integration-rw.verdify-db-rehearsal.svc.cluster.local"
CLUSTER_UID = "b087a146-58c5-4d98-8054-09094d22ecfe"
PRIMARY_UID = "45cbb6ef-a246-4448-9111-8d1a2ba4afad"
PHASES = ("unavailable", "crash-climate", "crash-observation", "crash-equipment", "replay", "duplicate", "conflict")


def validate_manifest(manifest):
    assert manifest["schema"] == "s2-committed-spool-v1"
    uuid.UUID(manifest["run_id"])
    assert set(manifest["events"]) == {"climate", "diagnostics", "equipment"}
    identities = []
    for kind, row in manifest["events"].items():
        identities.append(str(uuid.UUID(row["identity"])))
        event = row["event"]
        if kind == "equipment":
            assert event["receipt_id"] == row["identity"] and event["gap_requested"] is True
            assert event["gap_version"] == 1 and event["source_connection_generation"] == 7
            assert event["firmware_revision"] == "s2-qualification" and len(event["events"]) == 1
            uuid.UUID(event["source_runtime_instance_id"])
            at = event["source_observed_through"]
            inner = event["events"][0]
            assert inner["source_observed_at"] == at and inner["equipment"] == "fan1" and inner["state"] is False
            assert inner["source_runtime_instance_id"] == event["source_runtime_instance_id"]
            assert inner["source_connection_generation"] == 7 and inner["firmware_revision"] == "s2-qualification"
        else:
            assert event["greenhouse_id"] == "vallery" and event["connection_generation"] == 7
            uuid.UUID(event["runtime_instance_id"])
            at = event["row"]["ts"] if kind == "climate" else event["observed_at"]
        assert at.startswith("2000-01-01T00:00:00.") and at.endswith("+00:00")
        parsed = datetime.fromisoformat(at)
        assert parsed.utcoffset() == timedelta(0) and parsed.year == 2000
        if kind == "climate":
            assert set(event["row"]) == {"ts", "temp_avg"} and event["row"]["temp_avg"] == 70.0
        elif kind == "diagnostics":
            assert event["payload"] == {"firmware_version": "s2-qualification"}
    assert len(set(identities)) == 3


def prepared_manifest():
    run_id, runtime = str(uuid.uuid4()), str(uuid.uuid4())
    at = "2000-01-01T00:00:00." + f"{uuid.uuid4().int % 1000000:06d}" + "+00:00"
    common = {"greenhouse_id": "vallery", "runtime_instance_id": runtime, "connection_generation": 7}
    result = {
        "schema": "s2-committed-spool-v1",
        "run_id": run_id,
        "events": {
            "climate": {
                "identity": str(uuid.uuid4()),
                "event": {
                    **common,
                    "row": {"ts": at, "temp_avg": 70.0},
                    "sample_provenance": {
                        "temp_avg": {
                            "source": "s2-qualification",
                            "received_at": at,
                            "connection_generation": 7,
                            "runtime_instance_id": runtime,
                        }
                    },
                },
            },
            "diagnostics": {
                "identity": str(uuid.uuid4()),
                "event": {**common, "observed_at": at, "payload": {"firmware_version": "s2-qualification"}},
            },
        },
    }
    receipt_id = str(uuid.uuid4())
    result["events"]["equipment"] = {
        "identity": receipt_id,
        "event": {
            "receipt_id": receipt_id,
            "source_observed_through": at,
            "source_runtime_instance_id": runtime,
            "source_connection_generation": 7,
            "firmware_revision": "s2-qualification",
            "gap_requested": True,
            "gap_version": 1,
            "events": [
                {
                    "event_id": str(uuid.uuid4()),
                    "source_observed_at": at,
                    "equipment": "fan1",
                    "state": False,
                    "source_runtime_instance_id": runtime,
                    "source_connection_generation": 7,
                    "firmware_revision": "s2-qualification",
                }
            ],
        },
    }
    validate_manifest(result)
    return result


def require_authority(binding):
    assert binding["cluster_uid"] == CLUSTER_UID and binding["primary_uid"] == PRIMARY_UID
    assert binding["database_oid"] == 16447 and binding["cluster"] == CLUSTER
    assert binding["system_identifier"] == SYSTEM_IDENTIFIER
    assert binding["consumer_image"] == IMAGE and binding["consumer_source"] == SOURCE
    for key in ("sealed_admission_sha256", "protected_backup_wal_receipt_sha256"):
        value = binding[key]
        assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    assert binding["full_native_count_time_parity"] is True and binding["protected_backup_wal"] is True
    assert os.environ["VERDIFY_GIT_SHA"] == SOURCE  # Baked image; never manifest override.
    assert os.environ["DB_HOST"] == HOST and os.environ["DB_PORT"] == "5432"
    assert os.environ["DB_NAME"] == "verdify_rehearsal" and os.environ["DB_USER"] == LOGIN
    assert os.environ["VERDIFY_DEVICE_WRITE_ENABLED"] == "0"
    assert os.environ["VERDIFY_INGESTOR_RUNTIME_DB_ROLE_REQUIRED"] == "1"
    assert not any(os.environ.get(k) for k in ("ESP32_API_KEY", "HA_TOKEN", "MQTT_PASS", "DATABASE_URL"))


async def run(args):
    import asyncpg

    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest)
    binding = json.loads(os.environ["QUALIFICATION_BINDING"])
    require_authority(binding)
    directory = Path("/qualification-state") / ("s2-db-replay-" + manifest["run_id"])
    path = Path("/app/ingestor/ingestor.py")
    for filename, expected in MODULE_SHAS.items():
        assert hashlib.sha256((path.parent / filename).read_bytes()).hexdigest() == expected
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("qualified_committed_ingestor", path)
    consumer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = consumer
    spec.loader.exec_module(consumer)
    assert consumer._fanout_publisher is None
    assert consumer.policy_device_id("vallery") == "esp32-vallery"
    climate = consumer.SourceSpool(directory / "climate.sqlite", max_rows=20, max_bytes=1048576)
    observations = consumer.ObservationSpool(directory / "observations.sqlite", max_rows=20, max_bytes=1048576)
    equipment = consumer.SourceSpool(directory / "equipment.sqlite", max_rows=20, max_bytes=1048576)
    consumer._climate_event_spool = climate
    consumer._observation_spool = observations
    consumer._source_spool = equipment
    os.environ["VERDIFY_SOURCE_SPOOL_ENABLED"] = "1"
    assert (
        os.environ["VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER"]
        == consumer._EQUIPMENT_SOURCE_COLLECTOR_LOGIN
    )
    pools = []
    result = {
        "phase": args.phase,
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "consumer_image": IMAGE,
        "consumer_source": SOURCE,
        "consumer_module_sha256": MODULE_SHA,
        "consumer_modules_sha256": MODULE_SHAS,
        "target_binding": {k: binding[k] for k in ("cluster_uid", "primary_uid", "database_oid")},
        "normal_main_started": False,
        "fresh_device_confirmation_claimed": False,
    }

    def queues():
        return {"climate": climate.rows(), "diagnostics": observations.queue.rows(), "equipment": equipment.rows()}

    def accept(kind, conflict=False):
        item = manifest["events"][kind]
        event = json.loads(json.dumps(item["event"]))
        if conflict:
            if kind == "climate":
                event["row"]["temp_avg"] = 71.0
            elif kind == "diagnostics":
                event["payload"]["firmware_version"] = "s2-conflict"
            else:
                event["events"][0]["state"] = True
        spool = {"climate": climate, "diagnostics": observations.queue, "equipment": equipment}[kind]
        spool.put("receipt" if kind == "equipment" else kind, item["identity"], event)

    async def drain(kind, pool):
        if kind == "climate":
            await consumer._drain_climate_event_spool(pool)
        elif kind == "diagnostics":
            await consumer._drain_observations(pool)
        else:
            assert consumer.shared.esp32.get("client") is None
            consumer._restore_source_spool()
            await consumer.write_equipment_events(
                pool, consumer._coerce_spooled_ts(manifest["events"][kind]["event"]["source_observed_through"])
            )

    try:
        pool = await asyncpg.create_pool(consumer.DB_DSN, min_size=1, max_size=1, timeout=5, command_timeout=10)
        pools.append(pool)
        await consumer.attest_ordinary_ingestor_runtime_role(pool)
        async with pool.acquire() as conn:
            identity = dict(
                await conn.fetchrow("""SELECT current_user::text AS current_user,
                session_user::text AS session_user, current_database() AS database,
                (SELECT oid::int FROM pg_database WHERE datname=current_database()) AS database_oid,
                current_setting('cluster_name') AS cluster_name, pg_is_in_recovery() AS replica,
                pg_catalog.host(inet_server_addr()) AS backend_address""")
            )
            assert identity["current_user"] == identity["session_user"] == LOGIN
            assert identity["database"] == "verdify_rehearsal" and identity["database_oid"] == 16447
            assert identity["cluster_name"] == CLUSTER and identity["replica"] is False
            assert identity["backend_address"] == binding["primary_address"]
        result["identity"] = identity
        collector = await consumer.create_equipment_source_pool()
        assert collector is not None
        pools.append(collector)
        async with collector.acquire() as conn:
            collector_identity = dict(
                await conn.fetchrow(
                    "SELECT current_user::text AS current_user,session_user::text AS session_user,current_database() AS database,pg_catalog.host(inet_server_addr()) AS backend_address"
                )
            )
            assert (
                collector_identity["current_user"]
                == collector_identity["session_user"]
                == consumer._EQUIPMENT_SOURCE_COLLECTOR_LOGIN
            )
            assert (
                collector_identity["database"] == "verdify_rehearsal"
                and collector_identity["backend_address"] == binding["primary_address"]
            )
        result["collector_identity"] = collector_identity
        if args.phase == "unavailable":
            assert all(not rows for rows in queues().values()), "owned queue already populated"
            for kind in manifest["events"]:
                accept(kind)
            before = queues()
            # Exact target hostname, deliberately unavailable5433; scoped policy
            # permits5432 only. Actual asyncpg acquire fails with bounded timeout.
            unavailable = await asyncpg.create_pool(
                host=HOST,
                port=5433,
                database="verdify_rehearsal",
                user=LOGIN,
                password=os.environ["DB_PASSWORD"],
                min_size=0,
                max_size=1,
                timeout=2,
                command_timeout=5,
            )
            pools.append(unavailable)
            errors = {}
            for kind in manifest["events"]:
                try:
                    await drain(kind, unavailable)
                except (TimeoutError, OSError) as error:
                    errors[kind] = type(error).__name__
                else:
                    raise AssertionError("unavailable target unexpectedly accepted writes")
            assert queues() == before
            result.update(unavailable_transport=errors, durable_queue_unchanged=True)
        elif args.phase.startswith("crash-"):
            kind = {"crash-climate": "climate", "crash-observation": "diagnostics", "crash-equipment": "equipment"}[
                args.phase
            ]
            spool = {"climate": climate, "diagnostics": observations.queue, "equipment": equipment}[kind]
            assert len(spool.rows()) == 1
            # Source drains call ACK only after the actual SQL autocommit returned.
            # os._exit prevents local ACK/finalizers; next Pod reopens exact bytes.
            spool.acknowledge = lambda _: os._exit(74)
            await drain(kind, collector if kind == "equipment" else pool)
            raise AssertionError("expected crash boundary was not reached")
        elif args.phase in {"replay", "duplicate"}:
            if args.phase == "duplicate":
                assert all(not rows for rows in queues().values())
                for kind in manifest["events"]:
                    accept(kind)
            before = queues()
            for kind in manifest["events"]:
                assert len(before[kind]) == 1 and before[kind][0][1] == manifest["events"][kind]["identity"]
                await drain(kind, collector if kind == "equipment" else pool)
            assert all(not rows for rows in queues().values())
            result.update(
                original_uuid_replayed=True, queue_empty_after_ack=True, durable_commit_requires_owner_witness=True
            )
        else:
            assert all(not rows for rows in queues().values())
            for kind in manifest["events"]:
                accept(kind, conflict=True)
                try:
                    await drain(kind, collector if kind == "equipment" else pool)
                except Exception as error:
                    assert getattr(error, "sqlstate", None) == "P0001"
                else:
                    raise AssertionError("immutable UUID conflict accepted")
            assert all(len(rows) == 1 for rows in queues().values())
            result.update(conflicting_uuid_sqlstate="P0001", rejected_local_events_retained=True)
        result["status"] = "passed"
        print(json.dumps(result))
    finally:
        for pool in reversed(pools):
            await pool.close()
        climate.close()
        observations.queue.close()
        equipment.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", *PHASES), required=True)
    args = parser.parse_args()
    if args.phase == "prepare":
        assert not args.manifest.exists(), "never overwrite fixture custody"
        args.manifest.write_text(json.dumps(prepared_manifest(), indent=2) + "\n")
        args.manifest.chmod(0o600)
        print(json.dumps({"prepared_only": True, "manifest": str(args.manifest)}))
        return
    try:
        asyncio.run(run(args))
    except BaseException as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "phase": args.phase,
                    "error_category": type(error).__name__,
                    "sqlstate": getattr(error, "sqlstate", None),
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    # Standalone qualification emits redacted receipts. Importing its offline
    # guards must not change logging for unrelated application callers/tests.
    logging.disable(logging.CRITICAL)
    main()
