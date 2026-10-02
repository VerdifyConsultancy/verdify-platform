"""Closed physical profile tests; fabricated witnesses are not recovery proof."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cnpg_restore_qualification import witnesses
from test_cnpg_target_runtime_transition import originals, private_pg  # noqa: F401 — reuse isolated socket fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("physical", ROOT / "scripts/cnpg-physical-runtime-transition.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
t = p.t


def test_physical_inputs_dispatch_only_the_two_logical_records_to_composite_reader(monkeypatch):
    calls = []
    args = SimpleNamespace(captures=Path("capture"))
    for key in p.INPUT_KEYS:
        setattr(args, key, Path(key))
        setattr(args, key + "_sha256", "a" * 64)

    def single(path):
        calls.append((path.name, "single"))
        return {}, "a" * 64

    def composite(path, *, version, mode):
        calls.append((path.name, version, mode))
        return {}, "a" * 64

    class AfterInputDispatch(Exception):
        pass

    def stop_before_independent_capture_qualification(*args):
        raise AfterInputDispatch

    monkeypatch.setattr(p.t.c0, "read_witness", single)
    monkeypatch.setattr(p.t, "read_transition_record", composite)
    monkeypatch.setattr(p.pitr, "validate_captures", stop_before_independent_capture_qualification)
    with pytest.raises(AfterInputDispatch):
        p.qualified_inputs(args)
    assert {row for row in calls if len(row) == 3} == {
        ("logical_rollback", t.VERSION, "rollback-qualification"),
        ("logical_install", t.VERSION, "install"),
    }
    assert {row[0] for row in calls if len(row) == 2} == set(p.INPUT_KEYS) - {"logical_rollback", "logical_install"}


@pytest.mark.parametrize("profile", t.PHYSICAL_TARGETS)
def test_rollback_ddl_retains_original_history_and_native_digest_bodies(profile):
    _, before = witnesses()
    ddl, bodies = t.ddl(physical_target=profile)
    assert f"CREATE TABLE {t.PHYSICAL_TABLE}" in ddl
    assert f"CREATE TABLE {t.TABLE}" not in ddl
    assert profile in ddl and "cluster_name" in ddl
    assert "FUNCTION public.fn_runtime_ordinary_boundary_digest" not in ddl
    assert "FUNCTION public.fn_mcp_runtime_boundary_digest" not in ddl
    assert "ALTER ROLE" not in ddl and "schema_migrations" not in ddl
    assert set(bodies) == {"fn_runtime_attest_ordinary_login", "fn_mcp_runtime_attest_ordinary_login"}
    rows = [[login, "a" * 64, "b" * 64] for login in sorted(t.LOGINS)]
    sql = t.emit_sql(before, physical_target=profile, logical_receipts=rows)
    assert sql.rstrip().endswith("ROLLBACK;") and "\nCOMMIT;" not in sql
    assert f"LOCK TABLE {t.TABLE} IN SHARE MODE;" in sql
    assert sql.count("physical admission refuses copied logical receipt drift") == 2
    assert "IS DISTINCT FROM v_original" in sql
    assert "inet_client_addr() IS NOT NULL" in sql and "current_user<>session_user" in sql
    assert f"INSERT INTO {t.PHYSICAL_TABLE}" not in sql
    assert "cnpg-physical-runtime-transition-v1" in sql


@pytest.mark.parametrize("profile", ("verdify-prod", "verdify-db", "other", "verdify-cnpg-rehearsal"))
def test_only_two_new_source_owned_names_allowed(profile):
    with pytest.raises(ValueError, match="unsupported physical"):
        t.ddl(physical_target=profile)


def test_physical_profile_cannot_omit_inherited_history_or_touch_logical_public_mode():
    _, before = witnesses()
    with pytest.raises(ValueError, match="history custody"):
        t.emit_sql(before, physical_target=t.PHYSICAL_TARGETS[0])
    with pytest.raises(ValueError, match="history custody"):
        t.emit_sql(before, logical_receipts=[])
    old, _ = t.ddl()
    assert t.PHYSICAL_TABLE not in old and f"CREATE TABLE {t.TABLE}" in old
    assert "verdify-cnpg-pitr-" not in old


def test_dataset_parity_refuses_missing_time_count_or_compressed_ownership():
    data = {
        "schema": "cnpg-physical-data-parity-v1",
        "database": t.DATABASE,
        "relations": [{"relation": "public.example", "count": 3, "time_ranges": {"ts": ["start", "end"]}}],
        "timescale_owners": [
            {
                "parent": "public.example",
                "parent_owner": "verdify",
                "chunk": "internal.chunk",
                "chunk_owner": "verdify",
                "compressed": "internal.compressed",
                "compressed_owner": "verdify",
            }
        ],
    }
    p.validate_dataset(data, copy.deepcopy(data))
    wrong = copy.deepcopy(data)
    wrong["timescale_owners"][0]["compressed_owner"] = "postgres"
    with pytest.raises(ValueError, match="dataset/count/time/owner drift"):
        p.validate_dataset(data, wrong)
    wrong = copy.deepcopy(data)
    wrong["relations"][0]["time_ranges"] = {"ts": [None, "end"]}
    with pytest.raises(ValueError, match="timestamp endpoints"):
        p.validate_dataset(wrong, wrong)


def test_install_result_or_raw_history_is_not_rollback_qualification():
    _, before = witnesses()
    profile = t.PHYSICAL_TARGETS[0]
    record = {
        "version": p.VERSION,
        "mode": "install",
        "ddl_sha256": "a" * 64,
        "before_witness": before,
        "post_witness": before,
    }
    with pytest.raises(ValueError, match="rollback qualification"):
        p.checked_physical_qualification(before, record, profile)


@pytest.mark.parametrize(
    "bootstrap,sha",
    [
        (False, "a03bba9b6a2add17fc1602c535a8233b63cbfd823e52e552689a02d2c7b96004"),
        (True, "685b63cafa536e85953900f783475ff43a8d53ac44283194eef78bc3d7ba8fa7"),
    ],
)
def test_original_logical_ddl_remains_exact_882_source_bytes(bootstrap, sha):
    assert t.digest(t.ddl(bootstrap)[0].encode()) == sha


def test_native_dataset_full_public_inventory_cannot_be_sampled():
    data = {
        "schema": "cnpg-physical-data-parity-v1",
        "database": t.DATABASE,
        "relations": [{"relation": "public.example", "count": 3, "time_ranges": {}}],
        "timescale_owners": [
            {
                "parent": "public.example",
                "parent_owner": "verdify",
                "chunk": "internal.chunk",
                "chunk_owner": "verdify",
                "compressed": None,
                "compressed_owner": None,
            }
        ],
    }
    catalog = [["relation", "public.example", "a" * 64], ["relation", "public.omitted", "b" * 64]]
    with pytest.raises(ValueError, match="inventory omits"):
        p.validate_dataset(data, data, catalog)


def test_recovered_marker_claim_without_native_content_and_original_payload_is_refused():
    markers = {
        "markers": {
            key: {"marker_id": "marker-" + key.lower(), "payload_sha256": key.lower() * 64} for key in ("A", "B", "C")
        }
    }
    binding = {"cluster": t.PHYSICAL_TARGETS[0]}
    recovery = {"binding": binding, "marker_ids": ["marker-a"]}
    capture = {
        "binding": binding,
        "database": "rehearsal_bootstrap",
        "server_version_num": 160013,
        "cluster_name": t.PHYSICAL_TARGETS[0],
        "pg_is_in_recovery": False,
        "markers": [{"marker_id": "marker-a", "payload_sha256": "a" * 64}],
    }
    p.validate_sentinel_capture(capture, recovery, markers)
    capture["markers"].append({"marker_id": "marker-b", "payload_sha256": "b" * 64})
    with pytest.raises(ValueError, match="row or payload mismatch"):
        p.validate_sentinel_capture(capture, recovery, markers)


@pytest.mark.parametrize("profile", (p.pitr.SOURCE, *t.PHYSICAL_TARGETS))
def test_native_dataset_sql_is_read_only_full_inventory_and_fixed_identity(profile):
    sql = p.dataset_sql(profile)
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" in sql
    assert "SET LOCAL search_path=pg_catalog,public,pg_temp;" in sql
    assert f"current_setting('cluster_name')<>'{profile}'" in sql
    assert "current_user<>session_user" in sql and "inet_client_addr() IS NOT NULL" in sql
    assert "n.nspname='public'" in sql and "c.relkind IN ('r','p','v','m','S')" in sql
    assert "c.compressed_chunk_id" in sql and "WHERE h.schema_name='public' AND NOT c.dropped" in sql
    assert not any(
        word in sql for word in ("INSERT INTO", "UPDATE public", "ALTER TABLE", "CREATE TABLE", "DELETE FROM")
    )
    assert "min(%I)::text,max(%I)::text" in sql


def test_native_private_fixture_dataset_collector_handles_uncompressed_and_compressed(private_pg):  # noqa: F811
    # This PG16 fixture has no Timescale extension. Only the collector joins,
    # counts, ranges and READ ONLY behavior are tested; no actual recovery proof.
    q = private_pg
    q("""CREATE SCHEMA _timescaledb_catalog;
      CREATE TABLE _timescaledb_catalog.hypertable(id int,schema_name text,table_name text);
      CREATE TABLE _timescaledb_catalog.chunk(id int,hypertable_id int,schema_name text,table_name text,
        compressed_chunk_id int,dropped boolean);
      CREATE TABLE public.fixture_data(ts timestamptz);
      INSERT INTO public.fixture_data VALUES('2026-10-01T10:00:00Z'),('2026-10-01T10:01:00Z');
      CREATE TABLE _timescaledb_catalog.fixture_chunk(ts timestamptz);
      CREATE TABLE _timescaledb_catalog.fixture_compressed(ts timestamptz);
      INSERT INTO _timescaledb_catalog.hypertable VALUES(1,'public','fixture_data');
      INSERT INTO _timescaledb_catalog.chunk VALUES
        (1,1,'_timescaledb_catalog','fixture_chunk',NULL,false),
        (2,1,'_timescaledb_catalog','fixture_data_second',3,false),
        (3,9,'_timescaledb_catalog','fixture_compressed',NULL,false);
      CREATE TABLE _timescaledb_catalog.fixture_data_second(ts timestamptz);""")
    # Real restored v_band_device_divergence calls a source-owned public
    # PL/pgSQL resolver whose nested public helper has no SET search_path.
    # Relation qualification alone cannot resolve that helper/default args.
    q("""CREATE FUNCTION public.fixture_nested_value(value int DEFAULT 7)
          RETURNS int LANGUAGE sql AS 'SELECT value';
      CREATE FUNCTION public.fixture_nested_resolver() RETURNS int LANGUAGE plpgsql AS
          'BEGIN RETURN fixture_nested_value(); END';
      CREATE VIEW public.fixture_nested_view AS SELECT public.fixture_nested_resolver() AS value;""")
    emitted = p.dataset_sql(p.pitr.SOURCE)
    native_version = q("SHOW server_version_num")
    fixture_sql = emitted.replace("::int<>160013", "::int<>" + native_version)
    fixture_sql = fixture_sql.replace(
        "OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') IS DISTINCT FROM '2.25.2'", "OR false"
    )
    old_path = fixture_sql.replace(
        "SET LOCAL search_path=pg_catalog,public,pg_temp;", "SET LOCAL search_path=pg_catalog,pg_temp;"
    )
    old_failure = q(old_path, check=False)
    assert old_failure.returncode != 0 and "fixture_nested_value() does not exist" in old_failure.stderr
    result = json.loads(q(fixture_sql))
    assert next(row for row in result["relations"] if row["relation"] == "public.fixture_nested_view")["count"] == 1
    data = next(row for row in result["relations"] if row["relation"] == "public.fixture_data")
    assert data["count"] == 2 and data["time_ranges"]["ts"] == ["2026-10-01 10:00:00+00", "2026-10-01 10:01:00+00"]
    assert len(result["timescale_owners"]) == 2
    assert result["timescale_owners"][0]["compressed"] is None
    assert result["timescale_owners"][1]["compressed_owner"] == "verdify"
    bad = q(emitted, check=False)
    assert bad.returncode != 0 and "refuses target/session" in bad.stderr
    # Adding public resolution must never permit a view's function to write.
    q("""CREATE FUNCTION public.fixture_forbidden_write() RETURNS int LANGUAGE plpgsql AS
          'BEGIN INSERT INTO public.fixture_data VALUES(now()); RETURN 1; END';
      CREATE VIEW public.fixture_writing_view AS SELECT public.fixture_forbidden_write() AS value;""")
    writing_failure = q(fixture_sql, check=False)
    assert writing_failure.returncode != 0 and "read-only transaction" in writing_failure.stderr
    assert q("SELECT count(*) FROM public.fixture_data") == "2"


@pytest.mark.parametrize("profile", t.PHYSICAL_TARGETS)
def test_physical_atomic_raw_guards_and_copied_history(private_pg, monkeypatch, profile):  # noqa: F811
    q = private_pg
    monkeypatch.setattr(t, "SERVER", int(q("SHOW server_version_num")))
    # Synthetic digest implementation/witness stand-ins only. The default
    # emitter's fixed PG160013 and source-native digest guards are not relaxed.
    # Exercise its actual atomic SQL/CAS/historical retention with real DDL and
    # the selected217/259 attesters, then leave real target proof to ROOT.
    monkeypatch.setattr(t.c0, "checked", lambda *args, **kwargs: None)
    native_members = """(SELECT coalesce(jsonb_agg(format('member|%s|%s|%s|%s|%s|%s',roleid,member,grantor,admin_option,inherit_option,set_option) ORDER BY roleid,member,grantor),'[]'::jsonb) FROM pg_auth_members)"""
    selects = (
        """SELECT jsonb_build_object(
      'database',current_database(),'server',current_setting('server_version_num')::int,
      'roles',(SELECT jsonb_object_agg(oid::text,rolname) FROM pg_roles),
      'namespaces',(SELECT jsonb_object_agg(oid::text,nspname) FROM pg_namespace),
      'database_owner','verdify','database_acl','fixture',
      'ledger',(SELECT jsonb_agg(to_jsonb(r)) FROM public.schema_migrations r),
      'seals',jsonb_build_object('ordinary',(SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM public.runtime_ordinary_login_attestation_receipts r),'mcp',(SELECT jsonb_agg(to_jsonb(r)) FROM public.mcp_runtime_boundary_receipt r)),
      'boundaries',jsonb_build_object('verdify_api_runtime_login',jsonb_build_object('raw_entries',__NATIVE_MEMBERS__,'native',encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex')),
          'verdify_ingestor_runtime_login',jsonb_build_object('raw_entries',__NATIVE_MEMBERS__,'native',encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex')),
          'verdify_mcp_runtime_login',jsonb_build_object('raw_entries',__NATIVE_MEMBERS__,'native',encode(public.fn_mcp_runtime_boundary_digest(),'hex'))),
      'portable_catalog',("""
        + t.c0.portable_catalog_sql()
        + """),'raw_portable_catalog_v2',("""
        + t.c0.raw_portable_catalog_sql()
        + """),'portability_native_facts',("""
        + t.c0.portability_native_facts_sql()
        + """));"""
    )
    selects = selects.replace("__NATIVE_MEMBERS__", native_members)
    monkeypatch.setattr(t, "witness_select", lambda target=None, **kwargs: selects)
    q("""CREATE TABLE physical_when(value integer);
      CREATE FUNCTION physical_when_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
      CREATE TRIGGER compare_old_new BEFORE UPDATE ON physical_when FOR EACH ROW
        WHEN (OLD.value IS DISTINCT FROM NEW.value) EXECUTE FUNCTION physical_when_fn();""")
    before = json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects))
    logical_ddl, _ = t.ddl()
    q(logical_ddl)
    rows = [[login, "a" * 64, "b" * 64] for login in sorted(t.LOGINS)]
    q(
        "INSERT INTO "
        + t.TABLE
        + " VALUES "
        + ",".join(
            "(" + t.literal(login) + ",decode('" + boundary + "','hex'),'" + qualification + "')"
            for login, boundary, qualification in rows
        )
    )
    before = json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects))

    def emit(value, **kwargs):
        native_sql = t.emit_sql(value, physical_target=profile, logical_receipts=rows, **kwargs)
        # Fixture identity substitution only: source emits the fixed physical name,
        # while this reused socket fixture runs with original cluster_name.
        return native_sql.replace(profile, t.operator.CLUSTER)

    refused_identity = q(t.emit_sql(before, physical_target=profile, logical_receipts=rows), check=False)
    assert refused_identity.returncode != 0 and "refuses target/session" in refused_identity.stderr
    historical = originals(q)
    result = q(emit(before)).splitlines()
    record = json.loads(result[-1])
    assert record["mode"] == "rollback-qualification"
    after = p.checked_physical_qualification(before, record, profile)
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before and originals(q) == historical
    bad = copy.deepcopy(after)
    bad["boundaries"][t.LOGINS[0]]["native"] = "0" * 64
    failure = q(emit(before, reviewed_post=bad, qualification_sha256="a" * 64), check=False)
    assert failure.returncode != 0 and "unqualified post-DDL catalog" in failure.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before and originals(q) == historical
    stale = copy.deepcopy(before)
    stale["database_acl"] = "stale"
    failure = q(emit(stale), check=False)
    assert failure.returncode != 0 and "stale exact target witness" in failure.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
    original_ddl = t.ddl
    for injected in [
        "GRANT SELECT ON public.control_experiments TO PUBLIC;",
        "CREATE TABLE public.unapproved_native_object(x integer);",
        f"ALTER TABLE {t.PHYSICAL_TABLE} SET UNLOGGED;",
        f"UPDATE {t.TABLE} SET qualification_sha256=repeat('c',64);",
        "ALTER TABLE public.physical_when DISABLE TRIGGER compare_old_new;",
    ]:
        with monkeypatch.context() as scope:

            def modified_ddl(bootstrap=False, **kwargs):
                payload, bodies = original_ddl(bootstrap, **kwargs)
                return payload + "\n" + injected, bodies

            scope.setattr(t, "ddl", modified_ddl)
            refused = q(emit(before, reviewed_post=after, qualification_sha256="a" * 64), check=False)
            assert refused.returncode != 0
            assert any(
                reason in refused.stderr
                for reason in (
                    "CNPG native transition refuses raw",
                    "new raw object shape",
                    "copied logical receipt drift",
                )
            )
        assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
        assert originals(q) == historical
    installed = json.loads(q(emit(before, reviewed_post=after, qualification_sha256="a" * 64)).splitlines()[-1])
    assert installed["mode"] == "install"
    assert {k: v for k, v in installed["post_witness"].items() if k != "portability_native_facts"} == {
        k: v for k, v in after.items() if k != "portability_native_facts"
    }
    t.validate_raw_delta(before, installed["post_witness"], physical_target=profile)
    t.validate_installed_post(before, after, installed["post_witness"], physical_target=profile)
    for field, key, changed in (
        ("relations", "relnamespace", 999999),
        ("relations", "oid", 999998),
        ("constraints", "conrelid", 999997),
    ):
        altered = copy.deepcopy(installed["post_witness"])
        allowed = t.raw_additions(physical_target=profile)[field]
        fact = next(f for f in altered["portability_native_facts"][field] if f["identity"] in allowed)
        fact["native"][key] = changed
        with pytest.raises(ValueError):
            t.validate_installed_post(before, after, altered, physical_target=profile)
    assert installed["post_witness"]["portability_native_facts"] != after["portability_native_facts"]
    assert originals(q) == historical


@pytest.mark.parametrize("profile", (p.pitr.SOURCE, *t.PHYSICAL_TARGETS))
def test_dataset_peer_bridge_preserves_exact_readonly_snapshot_and_profile(profile):
    emitted = p.dataset_peer_sql(profile)
    assert emitted.count("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;") == 1
    assert "\nBEGIN;\n" not in emitted
    assert "current_user<>'postgres' OR session_user<>'postgres'" in emitted
    assert "SET SESSION AUTHORIZATION verdify;" in emitted and "RESET SESSION AUTHORIZATION;" in emitted
    assert f"current_setting('cluster_name')<>'{profile}'" in emitted
    assert "bootstrap/owner custody" in emitted
    assert not any(
        word in emitted for word in ("INSERT INTO", "UPDATE public", "ALTER TABLE", "CREATE TABLE", "DELETE FROM")
    )


def post270_contract(monkeypatch):
    """Small orchestration fixture; existing native suites own DDL proof."""
    d = p.successor_module()
    monkeypatch.setattr(p, "successor_module", lambda: d)
    before = {"namespaces": {}, "portability_native_facts": {"fixture": "before"}}
    admitted = copy.deepcopy(before)
    post = copy.deepcopy(before)
    post["ledger"] = "270"
    post["boundaries"] = {login: {"native": str(i) * 64} for i, login in enumerate(sorted(t.LOGINS), 1)}
    original = {
        "ledger": [{"source": "fixture", "seq": i} for i in range(277)],
        "ordinary": [{"id": 1}, {"id": 2}],
        "mcp": [{"id": 3}],
    }
    prior = [[login, "a" * 64, "b" * 64] for login in sorted(t.LOGINS)]
    old_rows = [
        {
            "login_name": login,
            "boundary_sha256": "\\x" + h,
            "qualification_sha256": q,
            "qualified_at": "2026-10-01T00:00:00+00:00",
        }
        for login, h, q in prior
    ]
    new_rows = copy.deepcopy(old_rows)
    for row in new_rows:
        row["boundary_sha256"] = "\\x" + post["boundaries"][row["login_name"]]["native"]
        row["qualification_sha256"] = p.POST270_ROLLBACK_SHA
    new_original = copy.deepcopy(original)
    new_original["ledger"].append(copy.deepcopy(p.POST270_NATIVE_LEDGER_ROW))
    original_rollback = {
        "version": d.r.VERSION,
        "mode": "savepoint-rollback-qualification",
        "ddl_sha256": "d" * 64,
        "before_witness": before,
        "post_witness": admitted,
    }
    original_install = dict(original_rollback, mode="install")
    successor_rollback = {
        "version": d.VERSION,
        "mode": "rollback-qualification",
        "before_witness": admitted,
        "post_witness": post,
    }
    successor_install = dict(successor_rollback, mode="install")
    successor = {
        "rollback": successor_rollback,
        "install": successor_install,
        "prior_rows": [original, old_rows],
        "installed_rows": {"original": new_original, "qualified": new_rows},
        "namespace_custody": {},
        "source_roles": "source",
        "installed_roles": "installed",
    }
    calls = []
    monkeypatch.setattr(t.c0, "compare", lambda *a: calls.append("source-compare"))
    monkeypatch.setattr(
        t,
        "checked_qualification",
        lambda *a, **k: (
            calls.append(("original-qualification", k)),
            {"boundaries": {login: {"native": "a" * 64} for login in t.LOGINS}},
        )[1],
    )
    monkeypatch.setattr(t, "validate_installed_post", lambda *a: admitted)
    monkeypatch.setattr(d, "validate_lineage", lambda *a, **k: calls.append("full-original-lineage"))
    monkeypatch.setattr(d, "checked_record", lambda *a, **k: calls.append(("genuine-successor", k)))
    monkeypatch.setattr(d, "installation_projection", lambda *a, **k: (post, []))
    monkeypatch.setattr(t.c0, "bootstrap_profile", lambda *a: {})
    monkeypatch.setattr(t.role_parity, "verify", lambda *a, **k: calls.append("password-free-role-parity"))
    monkeypatch.setattr(t.c0, "checked", lambda *a, **k: calls.append("complete-inherited-witness"))
    rows = [[login, post["boundaries"][login]["native"], p.POST270_ROLLBACK_SHA] for login in sorted(t.LOGINS)]
    args = ({}, before, original_rollback, original_install, post, rows, "b" * 64, successor)
    return args, calls


def test_post270_explicit_chain_retains_native_validators_and_real_modes(monkeypatch):
    args, calls = post270_contract(monkeypatch)
    p.validate_post270_logical(*args)
    assert ("original-qualification", {"retained_session": True}) in calls
    assert ("genuine-successor", {"mode": "rollback-qualification"}) in calls
    assert ("genuine-successor", {"mode": "install"}) in calls
    assert calls.count("full-original-lineage") == 2
    assert "password-free-role-parity" in calls and "complete-inherited-witness" in calls


@pytest.mark.parametrize(
    "tamper",
    [
        "original_mode",
        "original_before",
        "old_row",
        "old_seal",
        "receipt_time",
        "qualification_sha",
        "stamp",
        "catalog",
        "roles",
        "namespace",
        "copied_receipt",
        "270_applied_at",
        "270_duration",
        "270_extra_field",
    ],
)
def test_post270_chain_refuses_truthful_custody_and_inherited_drift(monkeypatch, tamper):
    args, _ = post270_contract(monkeypatch)
    args = list(copy.deepcopy(args))
    successor = args[-1]
    if tamper == "original_mode":
        args[3]["mode"] = "rollback-qualification"
    elif tamper == "original_before":
        args[3]["before_witness"] = {}
    elif tamper == "old_row":
        successor["installed_rows"]["original"]["ledger"][0]["seq"] = 999
    elif tamper == "old_seal":
        successor["installed_rows"]["original"]["mcp"][0]["id"] = 999
    elif tamper == "receipt_time":
        successor["installed_rows"]["qualified"][0]["qualified_at"] = "changed"
    elif tamper == "qualification_sha":
        successor["installed_rows"]["qualified"][0]["qualification_sha256"] = "f" * 64
    elif tamper == "stamp":
        successor["installed_rows"]["original"]["ledger"][-1]["sha256"] = "f" * 64
    elif tamper == "270_applied_at":
        successor["installed_rows"]["original"]["ledger"][-1]["applied_at"] = "2026-10-02T07:00:00+00:00"
    elif tamper == "270_duration":
        successor["installed_rows"]["original"]["ledger"][-1]["duration_ms"] += 1
    elif tamper == "270_extra_field":
        successor["installed_rows"]["original"]["ledger"][-1]["extra"] = "invented"
    elif tamper in {"catalog", "roles"}:
        args[4][tamper] = "unexpected"
    elif tamper == "namespace":
        args[4]["namespaces"]["9"] = "unexpected"
    else:
        args[5][0][2] = "f" * 64
    with pytest.raises(ValueError):
        p.validate_post270_logical(*args)


def test_post270_manifest_hash_and_incomplete_bindings_fail_closed(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": p.POST270_VERSION, "inputs": {}}))
    import hashlib

    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="manifest custody"):
        p.read_post270_lineage(path, "f" * 64)
    with pytest.raises(ValueError, match="complete post270"):
        p.read_post270_lineage(path, sha)
