"""Exact-hash release transition on synthetic PostgreSQL 16; no production approval."""

import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import asyncpg
import pytest
from test_c0_release_rehearsal import (
    assert_applied,
    attestation_probe,
    install_actual_attestation_probe,
    snapshot,
)
from test_c0_release_rehearsal import rehearsal as rehearsal
from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("c0_boundary_transition", ROOT / "scripts/c0-boundary-transition.py")
transition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transition)
LOGINS = transition.boundary.LOGINS
PATHS = tuple(ROOT / "db/migrations" / name for name in transition.MIGRATIONS)
DIGESTS = """SELECT jsonb_object_agg(login, encode(public.fn_runtime_ordinary_boundary_digest(login), 'hex'))
    FROM (VALUES ('verdify_api_runtime_login'), ('verdify_ingestor_runtime_login')) names(login);"""


def fixture_contract():
    return {
        "version": transition.VERSION,
        "database": "postgres",
        "server_version_num": 160013,
        "predecessor_ledger_sha256": "1" * 64,
        "before": dict.fromkeys(LOGINS, "2" * 64),
        "after": dict.fromkeys(LOGINS, "3" * 64),
    }


@pytest.fixture
def qualified(rehearsal):
    query, _, _ = rehearsal
    if int(query("SHOW server_version_num")) // 10000 != 16:
        pytest.skip("exact 217 role catalog transition requires PostgreSQL 16")
    query("REVOKE CREATE ON SCHEMA public FROM PUBLIC;")
    install_actual_attestation_probe(query)
    contract = fixture_contract()
    contract["server_version_num"] = int(query("SHOW server_version_num"))
    contract["predecessor_ledger_sha256"] = query(transition.ledger_digest_sql())
    contract["before"] = json.loads(query(DIGESTS))
    # Expected successor is obtained ONLY in this disposable synthetic fixture.
    # This is NOT a tool feature or permission to sample/approve production state.
    before = snapshot(query)
    rehearsal_sql = "BEGIN;\n" + "\n".join(path.read_text() for path in PATHS) + DIGESTS + "ROLLBACK;"
    contract["after"] = json.loads(query(rehearsal_sql).splitlines()[-1])
    assert snapshot(query) == before
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    socket = query("SHOW unix_socket_directories")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PG", "DB_", "POSTGRES_"))}

    def execute(sql, user="scorecard_fixture"):
        return subprocess.run(
            [
                str(Path(os.environ["SCORECARD_TEST_PG_BIN"]) / "psql"),
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                socket,
                "-p",
                "55472",
                "-U",
                user,
                "-d",
                "postgres",
                "-qAt",
            ],
            input=sql,
            text=True,
            capture_output=True,
            timeout=60,
            env=env,
        )

    return query, contract, execute


def emit(contract):
    return transition.emit_sql(contract, hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest())


def receipts(query):
    return query("SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM runtime_ordinary_login_attestation_receipts r")


def test_exact_transition_both_startups_ledger_and_noop(qualified):
    query, contract, execute = qualified
    sql = emit(contract)
    result = execute(sql)
    assert result.returncode == 0, result.stderr
    assert_applied(query, PATHS)
    assert json.loads(query(DIGESTS)) == contract["after"]
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    stable = snapshot(query), receipts(query)
    repeated = execute(sql)
    assert repeated.returncode == 0, repeated.stderr
    assert "Exact successor already applied; no writes." in repeated.stdout
    assert (snapshot(query), receipts(query)) == stable
    count = int(query("SELECT count(*) FROM daily_climate_metric_revisions"))
    query("""SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
        UPDATE public.daily_summary SET compliance_pct=coalesce(compliance_pct,0)+1 WHERE date='2026-09-04';
        RESET SESSION AUTHORIZATION;""")
    assert int(query("SELECT count(*) FROM daily_climate_metric_revisions")) == count + 2
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}


@pytest.mark.parametrize("login", LOGINS)
def test_wrong_successor_rolls_back_every_migration_and_both_receipts(qualified, login):
    query, contract, execute = qualified
    contract["after"][login] = "0" * 64
    before = snapshot(query), receipts(query)
    result = execute(emit(contract))
    assert result.returncode != 0 and "unreviewed catalog boundary" in result.stderr
    assert (snapshot(query), receipts(query)) == before
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}


@pytest.mark.parametrize(
    "drift",
    [
        "GRANT SELECT ON public.v_planner_performance TO verdify_api_runtime WITH GRANT OPTION",
        "ALTER ROLE verdify_ingestor_runtime_login CREATEDB",
        "GRANT CREATE ON SCHEMA public TO verdify_api_runtime",
        "UPDATE runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode(repeat('00',32),'hex') WHERE login_name='verdify_api_runtime_login'",
        "DELETE FROM runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login'",
        "UPDATE schema_migrations SET sha256=repeat('0',64)",
        "ALTER FUNCTION public.fn_runtime_attest_ordinary_login() SECURITY INVOKER",
        "CREATE OR REPLACE FUNCTION public.fn_runtime_attest_ordinary_login() RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN RETURN true; END; $$",
    ],
)
def test_untrusted_predecessor_refused_without_normalization(qualified, drift):
    query, contract, execute = qualified
    query(drift)
    before = snapshot(query), receipts(query)
    result = execute(emit(contract))
    assert result.returncode != 0
    assert "C0 transition" in result.stderr
    assert (snapshot(query), receipts(query)) == before


@pytest.mark.parametrize("position", [*range(7), "receipt"])
def test_failure_at_each_migration_and_after_receipt_write_restores_all(qualified, position):
    query, contract, execute = qualified
    sql = emit(contract)
    marker = (
        "    GET DIAGNOSTICS v_rows = ROW_COUNT;"
        if position == "receipt"
        else f"-- END EXACT SOURCE {PATHS[position].name}"
    )
    fault = (
        "RAISE EXCEPTION 'synthetic transition fault';"
        if position == "receipt"
        else "DO $$ BEGIN RAISE EXCEPTION 'synthetic transition fault'; END $$;"
    )
    assert sql.count(marker) == 1
    before = snapshot(query), receipts(query)
    result = execute(sql.replace(marker, marker + "\n" + fault))
    assert result.returncode != 0 and "synthetic transition fault" in result.stderr
    assert (snapshot(query), receipts(query)) == before
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    result = execute(sql)
    assert result.returncode == 0, result.stderr
    assert_applied(query, PATHS)
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}


def test_hostile_successor_same_object_key_refused(qualified):
    query, contract, execute = qualified
    sql = emit(contract)
    marker = "SET LOCAL search_path = pg_catalog, pg_temp;\nDO $c0_successor$"
    assert sql.count(marker) == 1
    sql = sql.replace(
        marker, "GRANT SELECT ON public.v_planner_performance TO verdify_api_runtime WITH GRANT OPTION;\n" + marker
    )
    before = snapshot(query), receipts(query)
    result = execute(sql)
    assert result.returncode != 0 and "unreviewed catalog boundary" in result.stderr
    assert (snapshot(query), receipts(query)) == before


def test_concurrent_callers_serialize_to_one_apply_one_noop(qualified):
    query, contract, execute = qualified
    sql = emit(contract)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [sql, sql]))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert sum("Exact successor already applied; no writes." in result.stdout for result in results) == 1
    assert_applied(query, PATHS)
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}


def test_catalog_drift_while_waiting_for_migration_lock_is_not_blessed(qualified):
    query, contract, execute = qualified
    sql = emit(contract)
    socket = query("SHOW unix_socket_directories")
    before_receipts = receipts(query)

    async def race():
        owner = await asyncpg.connect(host=socket, port=55472, user="scorecard_fixture", database="postgres")
        await owner.execute("SELECT pg_advisory_lock(hashtext('verdify-schema-migrations'))")
        pending = asyncio.create_task(asyncio.to_thread(execute, sql))
        try:
            async with asyncio.timeout(10):
                while not await owner.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                    WHERE pid<>pg_backend_pid() AND wait_event='advisory'
                      AND query LIKE '%verdify-schema-migrations%')"""):
                    await asyncio.sleep(0.025)
            await owner.execute("GRANT SELECT ON public.v_planner_performance TO verdify_api_runtime WITH GRANT OPTION")
        finally:
            await owner.execute("SELECT pg_advisory_unlock(hashtext('verdify-schema-migrations'))")
            await owner.close()
        return await pending

    result = asyncio.run(race())
    assert result.returncode != 0 and "unreviewed catalog boundary" in result.stderr
    assert query("SELECT count(*) FROM schema_migrations WHERE seq BETWEEN 241 AND 247") == "0"
    assert receipts(query) == before_receipts
    assert (
        query(
            "SELECT is_grantable FROM information_schema.role_table_grants WHERE table_name='v_planner_performance' AND grantee='verdify_api_runtime' AND privilege_type='SELECT'"
        )
        == "YES"
    )


@pytest.mark.parametrize("login", LOGINS)
def test_ordinary_login_cannot_execute_transition(qualified, login):
    query, contract, execute = qualified
    before = snapshot(query), receipts(query)
    result = execute(emit(contract), user=login)
    assert result.returncode != 0 and "permission denied" in result.stderr
    assert (snapshot(query), receipts(query)) == before


def test_partial_release_refused(qualified):
    query, contract, execute = qualified
    path = PATHS[0]
    query(path.read_text())
    query(f"""INSERT INTO schema_migrations(filename,source,seq,sha256,stamp_method)
        VALUES ('db/migrations/{path.name}','db/migrations',241,'{transition.MIGRATIONS[path.name]}','runner')""")
    before = snapshot(query), receipts(query)
    result = execute(emit(contract))
    assert result.returncode != 0 and "partial or changed release ledger" in result.stderr
    assert (snapshot(query), receipts(query)) == before


@pytest.mark.parametrize("field", ["database", "server_version_num", "before"])
def test_live_target_or_predecessor_mismatch_refused(qualified, field):
    query, contract, execute = qualified
    if field == "database":
        contract[field] = "wrong_target"
    elif field == "server_version_num":
        contract[field] += 1
    else:
        contract[field][LOGINS[0]] = "0" * 64
    before = snapshot(query), receipts(query)
    result = execute(emit(contract))
    assert result.returncode != 0 and "C0 transition" in result.stderr
    assert (snapshot(query), receipts(query)) == before


@pytest.mark.parametrize(
    "drift",
    [
        "UPDATE schema_migrations SET sha256=repeat('0',64) WHERE seq=245",
        "UPDATE runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode(repeat('00',32),'hex')",
        "GRANT SELECT ON public.v_planner_performance TO verdify_api_runtime WITH GRANT OPTION",
    ],
)
def test_successful_retry_refuses_new_drift(qualified, drift):
    query, contract, execute = qualified
    sql = emit(contract)
    applied = execute(sql)
    assert applied.returncode == 0, applied.stderr
    query(drift)
    before = snapshot(query), receipts(query)
    result = execute(sql)
    assert result.returncode != 0 and "C0 transition" in result.stderr
    assert (snapshot(query), receipts(query)) == before


@pytest.mark.parametrize(
    "hook",
    [
        "CREATE TRIGGER fixture_hostile BEFORE INSERT ON public.schema_migrations FOR EACH ROW EXECUTE FUNCTION public.fixture_untracked_hook()",
        "CREATE INDEX fixture_ledger_index ON public.schema_migrations(seq)",
        "ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY",
        "GRANT INSERT ON public.schema_migrations TO verdify_api_runtime",
        "GRANT UPDATE(sha256) ON public.schema_migrations TO PUBLIC",
    ],
)
def test_untracked_ledger_hooks_or_write_authority_refused(qualified, hook):
    query, contract, execute = qualified
    query("""CREATE FUNCTION public.fixture_untracked_hook() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'untracked code was executed'; END; $$;
        REVOKE ALL ON FUNCTION public.fixture_untracked_hook() FROM PUBLIC;""")
    query(hook)
    before = snapshot(query), receipts(query)
    result = execute(emit(contract))
    assert result.returncode != 0 and "unexpected ledger shape or executable hooks" in result.stderr
    assert "untracked code was executed" not in result.stderr
    assert (snapshot(query), receipts(query)) == before


def test_changed_extension_primitive_refused_before_call(qualified):
    query, contract, execute = qualified
    query("""CREATE OR REPLACE FUNCTION public.digest(text,text) RETURNS bytea LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'untrusted digest was executed'; END; $$;""")
    before = snapshot(query), receipts(query)
    result = execute(emit(contract))
    assert result.returncode != 0 and "changed digest primitive" in result.stderr
    assert "untrusted digest was executed" not in result.stderr
    assert (snapshot(query), receipts(query)) == before


@pytest.mark.parametrize(
    "change",
    [
        {"version": "unreviewed"},
        {"database": "postgres'; SELECT 'sensitive-canary'; --"},
        {"server_version_num": 150018},
        {"server_version_num": True},
        {"predecessor_ledger_sha256": "wrong"},
        {"before": {LOGINS[0]: "2" * 64}},
        {"after": dict.fromkeys(LOGINS, "2" * 64)},
        {"receipt_refresh_allowed": True},
    ],
)
def test_contract_strict_fields_and_types(change):
    contract = fixture_contract()
    contract.update(change)
    with pytest.raises(ValueError):
        emit(contract)


def test_cli_hash_binding_no_overwrite_and_no_value_disclosure(tmp_path, capsys):
    contract = tmp_path / "synthetic.json"
    contract.write_text(json.dumps(fixture_contract()))
    output = tmp_path / "transition.sql"
    args = [
        "--contract",
        str(contract),
        "--contract-sha256",
        hashlib.sha256(contract.read_bytes()).hexdigest(),
        "--output",
        str(output),
    ]
    assert transition.main(args) == 0
    original = output.read_bytes()
    assert transition.main(args) == 2
    assert output.read_bytes() == original
    contract.write_text('{"version":"sensitive-canary"}')
    assert transition.main(args) == 2
    assert "sensitive-canary" not in capsys.readouterr().err
    assert output.read_bytes() == original


def test_source_drift_refused_before_output(tmp_path, monkeypatch):
    altered = copy.deepcopy(transition.MIGRATIONS)
    altered[next(iter(altered))] = "0" * 64
    monkeypatch.setattr(transition, "MIGRATIONS", altered)
    with pytest.raises(ValueError, match="migration source drift"):
        emit(fixture_contract())


def test_emitted_sql_is_bound_and_does_not_compute_receipt_values():
    sql = emit(fixture_contract())
    assert sql.count("BEGIN;\n") == 1 and sql.count("\nCOMMIT;\n") == 1
    assert "SET boundary_sha256 = CASE login_name" in sql
    assert "SET boundary_sha256 = public.fn_runtime" not in sql
    for name, sha in transition.MIGRATIONS.items():
        assert f"-- BEGIN EXACT SOURCE {name} SHA256 {sha}" in sql
        assert (ROOT / "db/migrations" / name).read_text() in sql
    assert "pg_advisory_xact_lock(hashtext('verdify-schema-migrations'))" in sql
    assert "ACCESS EXCLUSIVE MODE" in sql
