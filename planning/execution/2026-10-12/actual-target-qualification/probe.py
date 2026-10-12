#!/usr/bin/env python3
"""Frozen-source, read-only seam probe; preparation is the default operation.

Actual execution is only for the recovery owner's admitted integration target
and serialized stage. This packet is not an executor/worker entrypoint.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

SOURCE = "3d9f31e8274d131e1bb2178bd9edb24cf66eb347"
PACKET = Path(__file__).resolve().parent
ROOT = PACKET.parents[3] if len(PACKET.parents) > 3 else Path("/app")
CLUSTER = "verdify-cnpg-s2-integration"
HOST = CLUSTER + "-rw.verdify-db-rehearsal.svc.cluster.local"
DATABASE = "verdify_rehearsal"
LOGINS = {
    "executor": "verdify_experiment_v2_component_executor_login",
    "lifecycle": "verdify_experiment_v2_lifecycle_login",
}
OFF_ENV = {
    "VERDIFY_DEVICE_WRITE_ENABLED": "0",
    "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "off",
    "VERDIFY_ACTIVE_EXPERIMENT_ID": "",
    "VERDIFY_POLICY_VECTOR_MODE": "off",
}
IDENTITY_SQL = """SELECT current_user, session_user, current_database(),
    pg_catalog.host(inet_server_addr()) AS server_addr,
    (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
    current_setting('cluster_name') AS cluster_name,
    current_setting('server_version_num') AS server_version_num,
    current_setting('transaction_read_only') AS transaction_read_only,
    pg_is_in_recovery() AS replica"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_path(name, layout):
    if layout == "checkout":
        return ROOT / name
    if name == "scripts/qualify-restored-runtime-duties.py":
        return PACKET / "frozen-target-binding.py"
    if layout == "api-image" and name == "api/main.py":
        return Path("/app/main.py")
    return Path("/app") / name


def source_guard(*, layout="checkout", duty="executor"):
    manifest = json.loads((PACKET / "source-hashes.json").read_text())
    require(manifest["source"] == SOURCE, "source pin mismatch")
    if layout != "checkout":
        require(
            (layout, duty) in (("api-image", "lifecycle"), ("ingestor-image", "executor")),
            "consumer image/duty mismatch",
        )
    for name, expected in manifest["files"].items():
        if layout != "checkout" and not (
            name in ("verdify_schemas/experiment_config.py", "scripts/qualify-restored-runtime-duties.py")
            or (duty == "executor" and name.startswith("ingestor/"))
            or (duty == "lifecycle" and name == "api/main.py")
        ):
            continue
        require(
            hashlib.sha256(source_path(name, layout).read_bytes()).hexdigest() == expected,
            "frozen source hash mismatch: " + name,
        )
    return manifest


def load_sources(*, duty=None, layout="checkout"):
    runtime_root = ROOT if layout == "checkout" else Path("/app")
    sys.path[:0] = [str(runtime_root), str(runtime_root / "ingestor")]
    component = api = None
    if duty in (None, "executor"):
        from tasks import component_experiment as component
    if duty in (None, "lifecycle"):
        spec = importlib.util.spec_from_file_location("frozen_v2_api_probe", source_path("api/main.py", layout))
        api = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = api
        spec.loader.exec_module(api)

    spec = importlib.util.spec_from_file_location(
        "qualification_target_private", source_path("scripts/qualify-restored-runtime-duties.py", layout)
    )
    binding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(binding)
    binding.TARGET_CLUSTER = CLUSTER
    return component, api, binding


class PinnedReadOnlyConnection:
    """Forward genuine consumer SQL into the same read-only transaction."""

    def __init__(self, connection):
        self.connection = connection
        self.acquisitions = 0
        self.component_executor_role_attested = False
        self.lifecycle_role_attested = False

    @asynccontextmanager
    async def acquire(self):
        self.acquisitions += 1
        yield self.connection


async def consumer_read(connection, duty, *, device_id, experiment_id, component, api):
    """Called only after the actual LOGIN's source attestation has passed."""
    pinned = PinnedReadOnlyConnection(connection)
    if duty == "executor":
        pinned.component_executor_role_attested = True
        before = pinned.acquisitions
        off = await component.component_experiment_worker(pinned)
        require(pinned.acquisitions == before, "OFF worker accessed SQL")
        require(
            off.disposition == "idle" and off.reason == "component_capability_off", "OFF worker did not stop before SQL"
        )
        store = component.AsyncpgComponentExperimentStore(pinned)
        startup = await store.safe_startup_attestation(device_id=device_id, experiment_id=None)
        return {
            "off_worker": asdict(off),
            "off_worker_sql_acquisitions": 0,
            "startup_attestation": asdict(startup),
            "consumer_acquisitions": pinned.acquisitions,
        }
    require(experiment_id is not None, "lifecycle needs an existing owner-read experiment UUID")
    pinned.lifecycle_role_attested = True
    # Call the existing endpoint's consumer/parser, without starting ASGI or
    # claiming HTTP-token/route qualification. The real LOGIN is already bound.
    previous = api.experiment_lifecycle_pool
    try:
        api.experiment_lifecycle_pool = pinned
        status = await api.get_component_experiment_status(experiment_id, _operator=None)
    finally:
        api.experiment_lifecycle_pool = previous
    require(
        status.environment_admissible is False and status.environment_gate_reason == "component_capability_off",
        "OFF status mismatch",
    )
    return {
        "status_parser": "passed",
        "experiment_id": status.experiment_id,
        "component_capability_mode": status.component_capability_mode,
        "environment_admissible": status.environment_admissible,
        "environment_gate_reason": status.environment_gate_reason,
        "execution_phase": status.execution_phase,
        "admission_state": status.admission_state,
        "open_exposures": status.open_exposures,
        "lease_generation": status.lease_generation,
        "consumer_acquisitions": pinned.acquisitions,
        "http_authentication_qualified": False,
    }


async def actual_probe(args):
    require(__debug__, "Python optimization disables the reused native-binding assertions")
    require(
        all(os.environ.get(key) == value for key, value in OFF_ENV.items()),
        "device/feature/active-ID/vector boundary mismatch",
    )
    require(
        args.native_binding
        and args.cluster_uid
        and args.database_oid
        and args.system_identifier
        and args.admission_sha256
        and args.stage_release,
        "owner admission, actual identity and serialized release are required",
    )
    require(
        len(args.admission_sha256) == 64 and all(c in "0123456789abcdef" for c in args.admission_sha256),
        "bad admission hash",
    )
    require(args.system_identifier.isdigit(), "bad owner-read system identifier")
    uuid.UUID(args.cluster_uid)
    if args.experiment_id:
        args.experiment_id = str(uuid.UUID(args.experiment_id))
    require(args.database_oid > 0 and args.device_id, "actual OID and device identity required")
    require(args.duty != "lifecycle" or args.experiment_id, "existing experiment UUID required")
    # This file is external owner custody, not an automated authorization
    # decision. Check readability before importing consumers or using secrets.
    release = args.stage_release.read_bytes()
    require(bool(release.strip()), "empty owner stage release")
    release_sha = hashlib.sha256(release).hexdigest()
    component, api, binding = load_sources(duty=args.duty, layout=getattr(args, "layout", "checkout"))
    address, binding_sha = binding.target_binding(args.native_binding.read_bytes(), args.cluster_uid)
    # Only now read the consumed Secret value. No credential argument, DSN,
    # Secret API call, owner proxy, SET ROLE or SET SESSION AUTHORIZATION.
    password = os.environ["QUALIFICATION_DB_PASSWORD"]
    import asyncpg

    connection = await asyncpg.connect(
        host=HOST,
        port=5432,
        database=DATABASE,
        user=LOGINS[args.duty],
        password=password,
        timeout=10,
        command_timeout=10,
        server_settings={
            "application_name": "verdify-v2-readonly-qualification",
            "default_transaction_read_only": "on",
            "statement_timeout": "10000",
            "lock_timeout": "3000",
        },
    )
    transaction = connection.transaction(readonly=True)
    transaction_started = False
    try:
        await transaction.start()
        transaction_started = True
        identity = dict(await connection.fetchrow(IDENTITY_SQL))
        require(
            identity["current_user"] == identity["session_user"] == LOGINS[args.duty], "authenticated LOGIN mismatch"
        )
        require(
            identity["current_database"] == DATABASE and identity["transaction_read_only"] == "on",
            "database/read-only mismatch",
        )
        binding.check_target_identity(identity, address, args.database_oid)
        query, check = (
            (component._COMPONENT_ROLE_ATTESTATION_SQL, component._component_role_attestation_passes)
            if args.duty == "executor"
            else (api._EXPERIMENT_LIFECYCLE_ROLE_ATTESTATION_SQL, api._experiment_lifecycle_role_attestation_passes)
        )
        attestation = dict(await connection.fetchrow(query))
        require(check(attestation), "actual LOGIN function-only attestation rejected")
        # LIMIT 0 requests no protected rows; PostgreSQL must still deny SELECT.
        for table in ("control_experiments", "experiment_v2_work", "experiment_v2_exposures"):
            savepoint = connection.transaction()
            await savepoint.start()
            try:
                await connection.fetch("SELECT * FROM public." + table + " LIMIT 0")
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise ValueError("protected relation SELECT was allowed")
            finally:
                await savepoint.rollback()
        consumer = await consumer_read(
            connection,
            args.duty,
            device_id=args.device_id,
            experiment_id=args.experiment_id,
            component=component,
            api=api,
        )
        return {
            "schema": "verdify-v2-readonly-seams-v1",
            "source": SOURCE,
            "duty": args.duty,
            "identity": identity,
            "cluster_uid": args.cluster_uid,
            "native_binding_sha256": binding_sha,
            "admission_sha256": args.admission_sha256,
            "stage_release_sha256": release_sha,
            "owner_system_identifier": args.system_identifier,
            "system_identifier_independently_read_by_login": False,
            "role_attestation": attestation,
            "protected_select_denials": 3,
            "consumer": consumer,
            "sql_boundary": "READ ONLY, outer ROLLBACK, no sequence allocation",
            "actual_target_behavioral_qualification_complete": False,
            "physical_execution_qualified": False,
            "native_treatment_fields": "27/48 UNPROVEN",
        }
    finally:
        try:
            if transaction_started:
                await transaction.rollback()
        finally:
            await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--duty", choices=LOGINS, default="executor")
    parser.add_argument("--layout", choices=("checkout", "ingestor-image", "api-image"), default="checkout")
    parser.add_argument("--native-binding", type=Path)
    parser.add_argument("--cluster-uid")
    parser.add_argument("--database-oid", type=int)
    parser.add_argument("--system-identifier")
    parser.add_argument("--admission-sha256")
    parser.add_argument("--stage-release", type=Path)
    parser.add_argument("--device-id")
    parser.add_argument("--experiment-id")
    args = parser.parse_args()
    source_guard(layout=args.layout, duty=args.duty)
    if not args.execute:
        print(
            json.dumps(
                {
                    "source": SOURCE,
                    "source_hashes": "match",
                    "mode": "prepare-only",
                    "connection_attempted": False,
                    "actual_target_qualified": False,
                }
            )
        )
        return
    try:
        receipt = asyncio.run(actual_probe(args))
    except Exception as error:
        # Error details/DSNs/SQL/data are deliberately omitted from receipts.
        print(json.dumps({"passed": False, "error_type": type(error).__name__, "actual_target_qualified": False}))
        raise SystemExit(1) from None
    print(json.dumps(receipt, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
