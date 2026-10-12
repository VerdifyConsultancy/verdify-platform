#!/usr/bin/env python3
"""Preparatory native PostgreSQL check: socket-only, empty reduced fixture.

Never accepts a host or target identity. No study/work/exposure/assignment rows
are inserted. This cannot qualify the restored integration target.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

import asyncpg

PACKET = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("v2_readonly_probe_fixture", PACKET / "probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
PG_BIN = Path("/opt/homebrew/opt/postgresql@16/bin")
PORT = 55492


async def native_checks(socket, admin_password, executor_password, lifecycle_password):
    for key, value in probe.OFF_ENV.items():
        probe.os.environ[key] = value
    component, api, _ = probe.load_sources()
    owner = await asyncpg.connect(
        host=str(socket), port=PORT, database="postgres", user="qualification_owner", password=admin_password
    )
    fixture_path = probe.ROOT / "db/migrations/tests/test-264-facility-safe-closure-startup-handoff.sql"
    fixture = fixture_path.read_text()
    # Reuse the existing migration fixture's exact empty table DDL only.
    tables = fixture.split("CREATE TABLE public.control_experiments", 1)[1].split("GRANT SELECT ON ALL TABLES", 1)[0]
    tables = "CREATE TABLE public.control_experiments" + tables
    migration = (probe.ROOT / "db/migrations/264-facility-safe-closure-startup-handoff.sql").read_text()
    function = (
        "CREATE OR REPLACE FUNCTION"
        + migration.split("CREATE OR REPLACE FUNCTION", 1)[1].split("$body$;", 1)[0]
        + "$body$;"
    )
    try:
        await owner.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        await owner.execute("CREATE ROLE verdify_experiment_v2_owner NOLOGIN NOINHERIT")
        for duty, password in (("executor", executor_password), ("lifecycle", lifecycle_password)):
            role = "verdify_experiment_component_executor" if duty == "executor" else "verdify_experiment_lifecycle"
            await owner.execute("CREATE ROLE " + role + " NOLOGIN NOINHERIT")
            # Random private fixture passwords are used only in this consuming
            # process; PostgreSQL statement logging is disabled by the launcher.
            await owner.execute(
                "CREATE ROLE " + probe.LOGINS[duty] + " LOGIN INHERIT PASSWORD '" + password + "' IN ROLE " + role
            )
        await owner.execute(tables)
        await owner.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO verdify_experiment_v2_owner")
        await owner.execute(function)
        signature = "public.fn_experiment_v2_safe_startup_attestation(text,uuid)"
        await owner.execute("ALTER FUNCTION " + signature + " OWNER TO verdify_experiment_v2_owner")
        await owner.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        await owner.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO verdify_experiment_component_executor")
        outcomes = []
        for duty, password in (("executor", executor_password), ("lifecycle", lifecycle_password)):
            try:
                wrong = await asyncpg.connect(
                    host=str(socket),
                    port=PORT,
                    database="postgres",
                    user=probe.LOGINS[duty],
                    password=secrets.token_hex(32),
                )
            except asyncpg.InvalidPasswordError:
                outcomes.append(duty + ":wrong-password-rejected")
            else:
                await wrong.close()
                raise AssertionError("wrong private password accepted")
            connection = await asyncpg.connect(
                host=str(socket),
                port=PORT,
                database="postgres",
                user=probe.LOGINS[duty],
                password=password,
                server_settings={"default_transaction_read_only": "on", "statement_timeout": "10000"},
            )
            try:
                transaction = connection.transaction(readonly=True)
                await transaction.start()
                try:
                    query, checker = (
                        (component._COMPONENT_ROLE_ATTESTATION_SQL, component._component_role_attestation_passes)
                        if duty == "executor"
                        else (
                            api._EXPERIMENT_LIFECYCLE_ROLE_ATTESTATION_SQL,
                            api._experiment_lifecycle_role_attestation_passes,
                        )
                    )
                    row = dict(await connection.fetchrow(query))
                    assert row["current_user_name"] == row["session_user_name"] == probe.LOGINS[duty]
                    assert row["has_required_function_execute"] is False
                    assert checker(row) is False
                    outcomes.append(duty + ":real-attester-rejects-incomplete-function-fixture")
                    savepoint = connection.transaction()
                    await savepoint.start()
                    try:
                        await connection.fetch("SELECT * FROM public.control_experiments LIMIT 0")
                    except asyncpg.InsufficientPrivilegeError:
                        outcomes.append(duty + ":protected-select-42501")
                    else:
                        raise AssertionError("protected SELECT accepted")
                    finally:
                        await savepoint.rollback()
                    if duty == "executor":
                        pinned = probe.PinnedReadOnlyConnection(connection)
                        off = await component.component_experiment_worker(pinned)
                        assert off.disposition == "idle" and off.reason == "component_capability_off"
                        assert off.setter_calls == 0 and pinned.acquisitions == 0
                        outcomes.append("executor:actual-off-worker-zero-sql-zero-setter")
                        store = component.AsyncpgComponentExperimentStore(pinned)
                        startup = await store.safe_startup_attestation(
                            device_id="fixture-empty-device", experiment_id=None
                        )
                        assert startup.attestation_reason == "no_active_v2_authority"
                        assert startup.scope_resolved and not startup.hold_required
                        assert (
                            startup.active_experiment_count
                            == startup.open_exposure_count
                            == startup.recovery_pending_count
                            == 0
                        )
                        outcomes.append("executor:actual-264-readonly-function-and-store-parser")
                finally:
                    await transaction.rollback()
            finally:
                await connection.close()
        # The read-only database engine boundary rejects an attempted owner
        # INSERT before any study row exists. No synthetic row is accepted.
        transaction = owner.transaction(readonly=True)
        await transaction.start()
        try:
            await owner.execute("INSERT INTO public.control_experiments DEFAULT VALUES")
        except asyncpg.ReadOnlySQLTransactionError:
            outcomes.append("native-readonly-owner-insert-25006")
        else:
            raise AssertionError("read-only INSERT accepted")
        finally:
            await transaction.rollback()
        table_names = await owner.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        counts = {
            row["tablename"]: await owner.fetchval("SELECT count(*) FROM public." + row["tablename"])
            for row in table_names
        }
        assert counts and all(count == 0 for count in counts.values())
        sequence = dict(
            await owner.fetchrow(
                "SELECT last_value,is_called FROM public.experiment_v2_runtime_generations_generation_event_id_seq"
            )
        )
        assert sequence == {"last_value": 1, "is_called": False}
        assert await owner.fetchval("SHOW listen_addresses") == ""
        outcomes.append("empty-eight-tables-and-unallocated-sequence-preserved")
        return {
            "schema": "verdify-v2-readonly-preparation-fixture-v1",
            "source": probe.SOURCE,
            "postgres_version": await owner.fetchval("SHOW server_version"),
            "listen_addresses": "",
            "password_authentication": "SCRAM over private UNIX socket",
            "actual_startup_function_sha256": hashlib.sha256(function.encode()).hexdigest(),
            "empty_fixture_table_ddl_sha256": hashlib.sha256(tables.encode()).hexdigest(),
            "startup": asdict(startup),
            "outcomes": outcomes,
            "empty_table_counts": counts,
            "sequence": sequence,
            "study_rows_created": 0,
            "actual_target_connected": False,
            "full_role_allowlists_qualified": False,
            "actual_target_qualified": False,
            "physical_execution_qualified": False,
            "native_treatment_fields": "27/48 UNPROVEN",
        }
    finally:
        await owner.close()


def main():
    probe.source_guard()
    work = Path(tempfile.mkdtemp(prefix="verdify-v2-readonly-fixture-", dir="/private/tmp"))
    socket = work / "socket"
    socket.mkdir()
    data = work / "data"
    password_file = work / "admin-password.private"
    admin_password, executor_password, lifecycle_password = (secrets.token_hex(32) for _ in range(3))
    password_file.touch(mode=0o600)
    password_file.write_text(admin_password + "\n")
    started = False
    try:
        with (work / "initdb.log").open("w") as log:
            subprocess.run(
                [
                    str(PG_BIN / "initdb"),
                    "-D",
                    str(data),
                    "--username=qualification_owner",
                    "--auth-local=scram-sha-256",
                    "--auth-host=reject",
                    "--pwfile=" + str(password_file),
                ],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        subprocess.run(
            [
                str(PG_BIN / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(work / "postgres.log"),
                "-o",
                "-c listen_addresses='' -c unix_socket_directories='"
                + str(socket)
                + "' -p "
                + str(PORT)
                + " -c log_statement=none -c log_min_error_statement=panic",
                "-w",
                "start",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = True
        receipt = asyncio.run(native_checks(socket, admin_password, executor_password, lifecycle_password))
    finally:
        password_file.unlink(missing_ok=True)
        if started:
            subprocess.run(
                [str(PG_BIN / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            shutil.rmtree(data)
    receipt["private_fixture_server_stopped"] = True
    receipt["private_fixture_data_and_verifiers_removed"] = True
    print(json.dumps(receipt, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
