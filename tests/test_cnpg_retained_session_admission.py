"""Local native16.15 fixture; never target16.13/import/admission credit."""

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import test_cnpg_target_runtime_transition as original

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("retained", ROOT / "scripts/cnpg-retained-session-admission.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
private_pg = original.private_pg


def command(q, user="verdify"):
    return [
        str(Path(os.environ["CNPG_TEST_PG_BIN"]) / "psql"),
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        q("SHOW unix_socket_directories"),
        "-p",
        q("SHOW port"),
        "-U",
        user,
        "-d",
        "verdify_rehearsal",
    ]


def test_default_emitter_bytes_unchanged(monkeypatch):
    before = original.witnesses()[1]
    monkeypatch.setattr(original.t.c0, "checked", lambda *a, **k: None)
    # Full byte hashes independently measured against actual e15 source before
    # editing. No Git-history dependency in the native CI's shallow checkout.
    baseline = {
        "logical": "08158dc9259a4c1e5a6e971d946f235164477a54415d4b8d423132abe1df0286",
        "verdify-cnpg-pitr-a": "ba786a79e774a309852ce5ca20b2db69e5185c7a1a9a1067dc05ef9f852fdb7f",
        "verdify-cnpg-pitr-b": "e5d99dfdf631d69ea47c55d1779de4caad038ce61d908a91996393719027eade",
    }
    for profile, expected in baseline.items():
        options = {} if profile == "logical" else {"physical_target": profile, "logical_receipts": []}
        assert hashlib.sha256(original.t.emit_sql(before, **options).encode()).hexdigest() == expected


@pytest.mark.parametrize("tamper", ["version", "mode", "extra", "duplicate"])
def test_closed_record_refuses_unexpected_envelope(tamper):
    value = {
        "version": d.VERSION,
        "mode": "savepoint-rollback-qualification",
        "ddl_sha256": "a" * 64,
        "before_witness": {},
        "post_witness": {},
    }
    if tamper == "version":
        value["version"] = original.t.VERSION
    if tamper == "mode":
        value["mode"] = "rollback-qualification"
    if tamper == "extra":
        value["extra"] = True
    raw = json.dumps(value).encode() + b"\n"
    if tamper == "duplicate":
        raw = raw.replace(b'"version":', b'"version":"duplicate","version":', 1)
    with pytest.raises((ValueError, TypeError)):
        d.record(raw, "savepoint-rollback-qualification")


@pytest.mark.parametrize("private_pg", ["bootstrap_peer"], indirect=True)
def test_actual_native_one_backend_savepoint_complete_guards_and_commit(private_pg, monkeypatch, tmp_path):
    native_q = private_pg

    def q(sql, **kwargs):
        kwargs["user"] = "postgres"
        return native_q("SET SESSION AUTHORIZATION verdify;\n" + sql, **kwargs)

    q, before, selects, historical = original.selected_atomic_fixture(q, monkeypatch)
    monkeypatch.setattr(d, "t", original.t)
    reference = json.loads(q(original.t.emit_sql(before)).splitlines()[-1])
    session = d.Session(command(q, "postgres"), tmp_path)
    refresh = None
    try:
        begin = d.outer_begin().replace("160013", q("SHOW server_version_num"))
        custody = d.checked_custody(json.loads(session.phase("begin", begin)))
        captured = json.loads(session.phase("capture", selects))
        assert captured == before
        refresh = subprocess.Popen(
            command(q, "postgres"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        refresh.stdin.write(b"SET SESSION AUTHORIZATION verdify;REFRESH MATERIALIZED VIEW public.v_relay_stuck;\n")
        refresh.stdin.close()
        deadline = time.monotonic() + 5
        while (
            q(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE relation='public.v_relay_stuck'::regclass AND mode='AccessExclusiveLock' AND NOT granted)"
            )
            != "t"
        ):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        third = q("SET lock_timeout='100ms';SELECT 1 FROM public.v_relay_stuck LIMIT 0", check=False)
        assert third.returncode and "lock timeout" in third.stderr
        raw = session.phase("qualification", original.t.emit_sql(before, retained_session=True))
        rec, raw_record = d.record(raw, "savepoint-rollback-qualification")
        assert rec["before_witness"] == before and rec["ddl_sha256"] == reference["ddl_sha256"]
        current = d.object_output(session.phase("rolledback", d.rollback_proof(before)))[0]
        d.checked_custody(current, custody)
        post = d.review(before, rec, reference)
        assert refresh.poll() is None
        install = original.t.emit_sql(
            before,
            reviewed_post=post,
            qualification_sha256=hashlib.sha256(raw_record).hexdigest(),
            retained_session=True,
        )
        installed, _ = d.record(session.phase("install", d.install_custody_sql(install)), "install")
        original.t.validate_installed_post(before, post, installed["post_witness"])
        session.committed = True
        returned = d.object_output(session.phase("returned", d.bootstrap_return_sql()))[0]
        assert returned["session_user"] == returned["current_user"] == "postgres"
        assert all(returned[k] == custody[k] for k in ("pid", "backend_start", "postmaster_start"))
        refresh.wait(timeout=10)
        assert refresh.returncode == 0 and q("SELECT " + original.t.original_facts_sql()) == historical
        assert q("SELECT count(*) FROM public.cnpg_qualified_runtime_receipts") == "3"
    finally:
        result = session.close()
        if refresh and refresh.poll() is None:
            refresh.kill()
            refresh.wait(timeout=10)
    assert result == {"native_exit": 0, "commit_sent": True, "commit_verified": True, "outcome": "committed"}


@pytest.mark.parametrize("tamper", ["child_xid", "extra_own_xid", "cutoff"])
def test_native_savepoint_creation_guards_fail_closed(private_pg, monkeypatch, tamper):
    q, before, selects, historical = original.selected_atomic_fixture(private_pg, monkeypatch)
    sql = original.t.emit_sql(before, retained_session=True)
    if tamper == "child_xid":
        sql = sql.replace(" v_creation_xid := v_new_child_xids[1]::text::bigint;", " v_creation_xid := 0;")
    elif tamper == "cutoff":
        sql = sql.replace(
            " v_creation_xid := v_new_child_xids[1]::text::bigint;",
            " v_creation_xid := v_new_child_xids[1]::text::bigint; v_creation_frozenxid := 0;",
        )
    else:
        # Keep a real nested subtransaction ACTIVE while the source guard reads
        # its own lock set: both the creating child and this extra child exist.
        sql = sql.replace(
            " SELECT array_agg(transactionid ORDER BY transactionid::text) INTO v_new_child_xids",
            " BEGIN INSERT INTO public.schema_migrations VALUES('fixture','extra-child','extra');\n"
            " SELECT array_agg(transactionid ORDER BY transactionid::text) INTO v_new_child_xids",
        ).replace(
            " v_creation_xid := v_new_child_xids[1]::text::bigint;",
            " v_creation_xid := v_new_child_xids[1]::text::bigint;\n EXCEPTION WHEN division_by_zero THEN NULL; END;",
        )
    # Same explicit outer session/held locks; no substitute numeric-range rule.
    refused = q("BEGIN;" + original.t.refresh_custody_sql() + sql, check=False)
    assert refused.returncode and (
        "new native OID binding drift" in refused.stderr or "ambiguous creating child XID" in refused.stderr
    )
    assert q("SELECT to_regclass('public.cnpg_qualified_runtime_receipts') IS NULL") == "t"
    assert json.loads(q("SET search_path=pg_catalog,pg_temp;" + selects)) == before
    assert original.originals(q) == historical


def test_actual_native_error_and_unknown_commit_cleanup(private_pg, tmp_path):
    session = d.Session(command(private_pg), tmp_path)
    try:
        with pytest.raises(ValueError, match="complete phase"):
            session.phase("install", "BEGIN;CREATE TABLE public.must_rollback(x int);SELECT 1/0;COMMIT;")
    finally:
        result = session.close()
    assert result["outcome"] == "unknown-commit" and not result["commit_verified"]
    assert private_pg("SELECT to_regclass('public.must_rollback') IS NULL") == "t"


def test_same_backend_custody_refuses_epoch_or_lock_change():
    value = {
        "pid": 123,
        "backend_start": "a",
        "postmaster_start": "b",
        "database": "verdify_rehearsal",
        "cluster": "verdify-cnpg-rehearsal",
        "server": "160013",
        "session_user": "verdify",
        "current_user": "verdify",
        "primary": True,
        "locks": [
            ["public.v_relay_stuck", "AccessShareLock", True],
            ["public.v_climate_merged", "AccessShareLock", True],
        ],
    }
    d.checked_custody(value)
    for field, changed in [
        ("pid", 124),
        ("backend_start", "changed"),
        ("postmaster_start", "changed"),
        ("locks", value["locks"][:1]),
    ]:
        other = copy.deepcopy(value)
        other[field] = changed
        with pytest.raises(ValueError):
            d.checked_custody(other, value)


@pytest.mark.parametrize("private_pg", ["bootstrap_peer"], indirect=True)
def test_native_password_free_role_custody_refuses_before_commit(private_pg, monkeypatch, tmp_path):
    native_q = private_pg

    def q(sql, **kwargs):
        kwargs["user"] = "postgres"
        return native_q("SET SESSION AUTHORIZATION verdify;\n" + sql, **kwargs)

    q, before, selects, historical = original.selected_atomic_fixture(q, monkeypatch)
    monkeypatch.setattr(d, "t", original.t)
    session = d.Session(command(q, "postgres"), tmp_path)
    try:
        session.phase("begin", d.outer_begin())
        raw = session.phase("qualification", original.t.emit_sql(before, retained_session=True))
        rec, body = d.record(raw, "savepoint-rollback-qualification")
        post = original.t.checked_qualification(before, rec, retained_session=True)
        sql = original.t.emit_sql(
            before, reviewed_post=post, qualification_sha256=hashlib.sha256(body).hexdigest(), retained_session=True
        )
        sql = d.install_custody_sql(sql).replace(
            "DO $bootstrap_custody$", "ALTER ROLE verdify SET work_mem='8MB';\nDO $bootstrap_custody$", 1
        )
        with pytest.raises(ValueError, match="complete phase"):
            session.phase("install", sql)
    finally:
        result = session.close()
    assert result["outcome"] == "unknown-commit" and not result["commit_verified"]
    assert "password-free role posture" in (tmp_path / "native-private.stderr").read_text()
    assert q("SELECT to_regclass('public.cnpg_qualified_runtime_receipts') IS NULL") == "t"
    assert json.loads(q("SET search_path=pg_catalog,pg_temp;" + selects)) == before
    assert original.originals(q) == historical


def test_native_phase_timeout_rolls_back_and_releases_prior_locks(private_pg, monkeypatch, tmp_path):
    monkeypatch.setattr(d, "PHASE_SECONDS", 0.05)
    session = d.Session(command(private_pg), tmp_path)
    try:
        with pytest.raises(ValueError, match="deadline"):
            session.phase(
                "capture",
                "BEGIN;"
                + original.t.refresh_custody_sql()
                + "CREATE TABLE public.timeout_cleanup(x int);SELECT pg_sleep(.2);",
            )
    finally:
        result = session.close()
    assert result["outcome"] == "not-installed"
    assert private_pg("SELECT to_regclass('public.timeout_cleanup') IS NULL") == "t"
    private_pg("SET lock_timeout='100ms';REFRESH MATERIALIZED VIEW public.v_relay_stuck;")
    assert (tmp_path / "capture.native-stream").is_file()


def test_native_idle_expiry_cleans_abandoned_outer_transaction(private_pg, tmp_path):
    session = d.Session(command(private_pg), tmp_path)
    try:
        session.phase(
            "held",
            "BEGIN;SET LOCAL idle_in_transaction_session_timeout='100ms';"
            + original.t.refresh_custody_sql()
            + "CREATE TABLE public.idle_cleanup(x int);SELECT '{}'::jsonb;",
        )
        time.sleep(0.25)
        assert private_pg("SELECT to_regclass('public.idle_cleanup') IS NULL") == "t"
        private_pg("SET lock_timeout='100ms';REFRESH MATERIALIZED VIEW public.v_relay_stuck;")
    finally:
        result = session.close()
    assert result["outcome"] == "not-installed"
    assert "idle-in-transaction timeout" in (tmp_path / "native-private.stderr").read_text()


def test_mode_bounds_and_physical_refusal(monkeypatch):
    before = original.witnesses()[1]
    monkeypatch.setattr(original.t.c0, "checked", lambda *a, **k: None)
    with pytest.raises(ValueError, match="logical-target-only"):
        original.t.emit_sql(before, retained_session=True, physical_target="verdify-cnpg-pitr-a", logical_receipts=[])
    assert "SET LOCAL statement_timeout='180s'" in d.outer_begin()
    assert "idle_in_transaction_session_timeout='60s'" in d.outer_begin()
    assert d.TOTAL_SECONDS == 900 and d.PHASE_SECONDS == 240
    assert "SET SESSION AUTHORIZATION verdify" in d.outer_begin()
    assert "SET ROLE" not in d.outer_begin()
    assert "RESET SESSION AUTHORIZATION" in d.bootstrap_return_sql()


def test_operator_source_refuses_unreviewed_or_changed_bytes(monkeypatch):
    with pytest.raises(ValueError, match="exact reviewed"):
        d.operator_source("unreviewed")
    monkeypatch.setattr(d.subprocess, "check_output", lambda *a, **k: b"changed code")
    with pytest.raises(ValueError, match="operator bytes differ"):
        d.operator_source("a" * 40)


def test_record_keeps_original_single_and_composite_bounds(monkeypatch):
    monkeypatch.setattr(d.t.c0, "WITNESS_MAX_BYTES", 32)
    value = {
        "version": d.VERSION,
        "mode": "savepoint-rollback-qualification",
        "ddl_sha256": "a" * 64,
        "before_witness": {"extra": "x" * 33},
        "post_witness": {},
    }
    with pytest.raises(ValueError, match="single full witness"):
        d.record(json.dumps(value).encode() + b"\n", "savepoint-rollback-qualification")
    value["before_witness"] = {}
    raw = json.dumps(value).encode()
    with pytest.raises(ValueError, match="two-witness record bound"):
        d.record(b"{" + b" " * 1100 + raw[1:] + b"\n", "savepoint-rollback-qualification")
