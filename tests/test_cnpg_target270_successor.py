"""Source/delta and socket-only PG16 fixture, not target16.13 proof."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import test_cnpg_target_runtime_transition as original

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("target270", ROOT / "scripts/cnpg-target270-successor.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
private_pg = original.private_pg


def test_selected_applied_source_is_exact_closed_transactional_ops_only():
    ddl, old, new = d.selected_source()
    assert d.digest(old.encode()) == d.OLD_BODY_SHA
    assert d.digest(new.encode()) == d.NEW_BODY_SHA
    assert ddl.count("CREATE OR REPLACE FUNCTION") == 1
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in ddl
    assert "fn_runtime_ordinary_boundary_digest" not in ddl
    assert d.t.classify_ddl(ddl) == {"self_committing": False, "reasons": []}


@pytest.mark.parametrize("change", ["missing", "duplicate", "body", "other"])
def test_exact_boundary_replacement_refuses_uncontrolled_delta(change):
    _, old, new = d.selected_source()
    entry = d.ENTRY_PREFIX + "definition=" + old + "|body=" + old
    values = [entry, "role|unchanged"]
    if change == "missing":
        values = ["role|unchanged"]
    elif change == "duplicate":
        values.append(entry)
    elif change == "body":
        values[0] = entry.replace(old, "changed", 1)
    elif change == "other":
        assert d.successor_entries(values, old, new)[-1] == "role|unchanged"
        return
    with pytest.raises(ValueError):
        d.successor_entries(values, old, new)


def setup_native(q, monkeypatch):
    monkeypatch.setattr(d.t, "SERVER", int(q("SHOW server_version_num")))
    monkeypatch.setattr(d.t.c0, "checked", lambda *a, **k: None)
    monkeypatch.setattr(d.t, "target_receipt_shape", lambda: "true")
    marker = "CREATE OR REPLACE FUNCTION public." + d.IDENTITY
    raw = (ROOT / "db/migrations/217-runtime-role-boundary.sql").read_text()
    oldddl = marker + raw.split(marker, 1)[1].split("$body$;", 1)[0] + "$body$;"
    q(oldddl)
    q("""ALTER TABLE public.schema_migrations ADD COLUMN applied_at timestamptz, ADD COLUMN seq integer, ADD COLUMN stamp_method text, ADD COLUMN duration_ms integer, ADD COLUMN applied_by text;
    INSERT INTO public.schema_migrations(filename,source,seq,sha256,stamp_method)
    VALUES('db/migrations/269-lab-crop-zone-topology-projections.sql','db/migrations',269,
    '2540c20810b07fa42e73200d120d3b753b9b36860b029a49b8db1b4a7283475d','runner');
    CREATE TABLE public.cnpg_qualified_runtime_receipts(login_name text PRIMARY KEY,boundary_sha256 bytea,qualification_sha256 text);""")
    entry = "format('function|public.fn_experiment_v2_ops_status()|definition=%s|body=%s',pg_get_functiondef('public.fn_experiment_v2_ops_status()'::regprocedure),(SELECT prosrc FROM pg_proc WHERE oid='public.fn_experiment_v2_ops_status()'::regprocedure))"
    projection = f"jsonb_build_object('raw_entries',jsonb_build_array({entry}),'semantic_entries',jsonb_build_array({entry}),'native',encode(public.digest({entry},'sha256'),'hex'))"
    catalog = (
        "jsonb_build_array(jsonb_build_array('function','public.fn_experiment_v2_ops_status()',encode(public.digest(("
        + d.ops_sql()
        + "->>'catalog_definition_text'),'sha256'),'hex')))"
    )
    select = f"""SELECT jsonb_build_object('database',current_database(),'server',current_setting('server_version_num')::int,
    'roles',(SELECT jsonb_object_agg(oid::text,rolname) FROM pg_roles),
    'ledger',(SELECT jsonb_agg(jsonb_build_array(source,filename,seq,sha256,stamp_method) ORDER BY source,filename) FROM public.schema_migrations),
    'seals','{{"original":true}}'::jsonb,'portability_native_facts','{{"fixture":true}}'::jsonb,
    'portable_catalog',{catalog},'raw_portable_catalog_v2',{catalog},
    'boundaries',jsonb_build_object('verdify_api_runtime_login',{projection},'verdify_ingestor_runtime_login',{projection},
    'verdify_mcp_runtime_login',jsonb_build_object('native',repeat('a',64),'raw_entries','[]'::jsonb)));"""
    monkeypatch.setattr(d.t, "witness_select", lambda *a, **k: select)
    before = json.loads(q("SET search_path=pg_catalog,pg_temp; " + select))
    for login in d.t.LOGINS:
        q(
            f"INSERT INTO {d.t.TABLE} VALUES({d.t.literal(login)},decode({d.t.literal(before['boundaries'][login]['native'])},'hex'),repeat('b',64))"
        )
    rows = [json.loads(q("SELECT " + d.t.original_facts_sql())), json.loads(q("SELECT " + d.receipts_sql()))]
    monkeypatch.setattr(
        d, "validate_prior_rows", lambda rows: None
    )  # smaller synthetic fixture, not 277-row target credit
    return before, select, rows


def test_native_full_rollback_then_reviewed_install_preserves_history(private_pg, monkeypatch):
    q = private_pg
    before, select, rows = setup_native(q, monkeypatch)
    history = q("SELECT " + d.t.original_facts_sql())
    receipts = q("SELECT " + d.receipts_sql())
    result = q(d.emit_sql(before, prior_rows=rows)).splitlines()
    record = json.loads(result[-1])
    post = d.checked_record(before, record)
    assert q("SELECT " + d.t.original_facts_sql()) == history
    assert q("SELECT " + d.receipts_sql()) == receipts
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + select)) == before
    assert post["boundaries"]["verdify_mcp_runtime_login"] == before["boundaries"]["verdify_mcp_runtime_login"]
    installed = json.loads(
        q(d.emit_sql(before, prior_rows=rows, reviewed=record, qualification_sha="c" * 64)).splitlines()[-1]
    )
    assert d.checked_record(before, installed, mode="install") == post
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + select)) == post
    assert q("SELECT bool_and(qualification_sha256=repeat('c',64)) FROM " + d.t.TABLE) == "t"
    assert (
        q(
            "SELECT encode(public.digest(prosrc,'sha256'),'hex') FROM pg_proc WHERE oid='public.fn_experiment_v2_ops_status()'::regprocedure"
        )
        == d.NEW_BODY_SHA
    )
    assert q("SELECT count(*) FROM public.schema_migrations WHERE seq=270") == "1"


@pytest.mark.parametrize(
    "tamper", ["stale", "metadata", "ledger", "seal", "target_receipts", "extra_boundary", "extra_raw"]
)
def test_native_tampering_fails_and_rolls_back(private_pg, monkeypatch, tamper):
    q = private_pg
    before, select, rows = setup_native(q, monkeypatch)
    history = q("SELECT " + d.t.original_facts_sql())
    sql = d.emit_sql(before, prior_rows=rows)
    ddl, _, _ = d.selected_source()
    if tamper == "stale":
        stale = copy.deepcopy(before)
        stale["roles"]["999"] = "foreign"
        sql = d.emit_sql(stale, prior_rows=rows)
    elif tamper == "metadata":
        sql = sql.replace(ddl, ddl + "\nALTER FUNCTION public.fn_experiment_v2_ops_status() SECURITY INVOKER;")
    elif tamper == "ledger":
        sql = sql.replace(ddl, ddl + "\nUPDATE public.schema_migrations SET applied_by='changed' WHERE seq=269;")
    elif tamper == "seal":
        sql = sql.replace(ddl, ddl + "\nDELETE FROM runtime_ordinary_login_attestation_receipts;")
    elif tamper == "target_receipts":
        sql = sql.replace(
            ddl, ddl + "\nUPDATE cnpg_qualified_runtime_receipts SET qualification_sha256=repeat('d',64);"
        )
    elif tamper == "extra_boundary":
        sql = sql.replace(ddl, ddl + "\nALTER ROLE verdify_api_runtime_login NOINHERIT;")
    elif tamper == "extra_raw":
        sql = sql.replace(ddl, ddl + "\nALTER ROLE verdify_api_runtime_login RENAME TO foreign_login;")
    result = q(sql, check=False)
    assert result.returncode != 0
    assert q("SELECT " + d.t.original_facts_sql()) == history
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + select)) == before


@pytest.mark.parametrize("private_pg", ["bootstrap_peer"], indirect=True)
def test_native_retained_session_real_savepoint_rollback_and_owner_commit(private_pg, monkeypatch, tmp_path):
    import os

    q0 = private_pg

    def q(sql, check=True):
        return q0("SET SESSION AUTHORIZATION verdify;\n" + sql, user="postgres", check=check)

    before, select, rows = setup_native(q, monkeypatch)
    cmd = [
        str(Path(os.environ["CNPG_TEST_PG_BIN"]) / "psql"),
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        q0("SHOW unix_socket_directories", user="postgres"),
        "-p",
        q0("SHOW port", user="postgres"),
        "-U",
        "postgres",
        "-d",
        "verdify_rehearsal",
    ]
    session = d.r.Session(cmd, tmp_path)
    try:
        custody = d.r.checked_custody(d.r.object_output(session.phase("begin", d.outer_begin()))[0])
        captured = d.r.object_output(session.phase("capture", select))[0]
        assert captured == before
        trial = d.r.object_output(session.phase("qualification", d.retained_sql(before, prior_rows=rows)))[0]
        post = d.checked_record(before, trial)
        d.r.checked_custody(d.r.object_output(session.phase("rolled-back", d.rollback_proof(before)))[0], custody)
        assert q("SELECT count(*) FROM schema_migrations WHERE seq=270") == "0"
        for view in ("v_relay_stuck", "v_climate_merged"):
            refused = q(f"SET lock_timeout='50ms'; REFRESH MATERIALIZED VIEW public.{view};", check=False)
            assert refused.returncode != 0 and "lock timeout" in refused.stderr
        installed = d.r.object_output(
            session.phase(
                "install", d.retained_sql(before, prior_rows=rows, reviewed=trial, qualification_sha="d" * 64)
            )
        )[0]
        assert installed["mode"] == "install" and installed["post_witness"] == post
        returned = d.r.object_output(session.phase("return", d.r.bootstrap_return_sql()))[0]
        assert returned["session_user"] == returned["current_user"] == "postgres"
        assert returned["pid"] == custody["pid"]
        assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + select)) == post
        assert (
            q("SELECT bool_and(qualification_sha256=repeat('d',64)) FROM public.cnpg_qualified_runtime_receipts") == "t"
        )
    finally:
        session.close()


@pytest.mark.parametrize("tamper", ["owner", "acl", "definition", "catalog_hash", "body", "mode", "extra"])
def test_independent_reader_refuses_changed_ops_evidence(private_pg, monkeypatch, tmp_path, tamper):
    q = private_pg
    before, _, rows = setup_native(q, monkeypatch)
    record = json.loads(q(d.emit_sql(before, prior_rows=rows)).splitlines()[-1])
    if tamper == "owner":
        record["ops_after"]["native"]["proowner"] = 999
    elif tamper == "acl":
        record["ops_after"]["native"]["proacl"] = ["foreign=X/verdify"]
    elif tamper == "definition":
        record["ops_after"]["definition"] += "changed"
    elif tamper == "catalog_hash":
        record["post_witness"]["portable_catalog"][0][2] = "0" * 64
    elif tamper == "body":
        record["ops_after"]["native"]["prosrc"] = "changed"
    elif tamper == "mode":
        record["mode"] = "install"
    elif tamper == "extra":
        record["extra"] = True
    path = tmp_path / "native-record.json"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        d.read_record(path)


def test_cli_emission_never_reuses_original_production_pre_postflight():
    source = Path(d.__file__).read_text()
    assert "672d1afa92e37f243d5893fbd57cd2b84e86ea19c1c79d3975c04dfd39663532" in source
    assert "fe79f986d58ba6de" not in source
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in source
    assert "UPDATE public.mcp_runtime_boundary_receipt" not in source
    assert "execute_qualification" in source
    assert "Session(command, directory)" in source


def test_original_row_reader_retains_exact_native_baseline_and_refuses_extra(tmp_path):
    rows = [
        {
            "ledger": [
                {"source": "db/migrations", "filename": str(i), "seq": i, "applied_at": "2026-10-01T00:00:00Z"}
                for i in range(277)
            ],
            "ordinary": [{}, {}],
            "mcp": [{}],
        },
        [
            {"login_name": login, "boundary_sha256": "\\x" + "a" * 64, "qualification_sha256": "b" * 64}
            for login in d.t.LOGINS
        ],
    ]
    path = tmp_path / "rows.stdout"
    raw = "\n".join(json.dumps(x) for x in rows) + "\n"
    path.write_text(raw)
    read, sha = d.read_prior_rows(path)
    assert read == rows and sha == d.digest(raw.encode())
    path.write_text(raw + "{}\n")
    with pytest.raises(ValueError):
        d.read_prior_rows(path)
    rows[0]["ledger"].pop()
    with pytest.raises(ValueError):
        d.validate_prior_rows(rows)


def test_native_prior_timestamp_tamper_refuses_before_ops_ddl(private_pg, monkeypatch):
    q = private_pg
    before, select, rows = setup_native(q, monkeypatch)
    bad = copy.deepcopy(rows)
    bad[0]["ledger"][0]["applied_at"] = "1970-01-01T00:00:00Z"
    result = q(d.emit_sql(before, prior_rows=bad), check=False)
    assert result.returncode != 0 and "immutable historical row custody changed" in result.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + select)) == before


@pytest.mark.parametrize(
    "field", ["ledger", "seals", "roles", "boundaries", "portable_catalog", "raw_portable_catalog_v2"]
)
def test_prior_admission_lineage_refuses_unreviewed_nonraw_change(monkeypatch, field):
    before = {
        "ledger": [],
        "seals": {},
        "roles": {},
        "boundaries": {},
        "portable_catalog": [],
        "raw_portable_catalog_v2": [],
        "portability_native_facts": {"original": True},
    }
    prior = {
        "version": d.r.VERSION,
        "mode": "install",
        "ddl_sha256": d.digest(b"selected"),
        "before_witness": {},
        "post_witness": copy.deepcopy(before),
    }
    monkeypatch.setattr(d.t, "ddl", lambda flag: ("selected", {}))
    monkeypatch.setattr(d.t.c0, "compare", lambda *a: None)
    monkeypatch.setattr(d.t, "validate_post", lambda *a: None)
    monkeypatch.setattr(d.t.c0, "checked", lambda *a, **k: None)
    before[field] = "unreviewed"
    with pytest.raises(ValueError):
        d.validate_lineage({}, prior, before)


def raw_lineage_fixture():
    relations = []
    for i, name in enumerate(["public.v_climate_merged", "public.v_relay_stuck", "public.equipment_source_events"]):
        relations.append(
            {
                "identity": name,
                "schema": "public",
                "raw_definition_text": "unchanged",
                "native": {
                    "oid": str(100 + i),
                    "relkind": "m" if i < 2 else "r",
                    "relowner": "10",
                    "relfilenode": str(200 + i),
                    "reltoastrelid": "300" if i == 1 else "0",
                    "relfrozenxid": "20",
                    "relpages": 1,
                },
            }
        )
    return {
        "relations": relations,
        "indexes": [{"identity": "public.example_idx", "native": {"relfilenode": "400"}}],
        "constraints": [{"identity": "public.example.constraint", "native": {"oid": "500"}, "definition": "fixed"}],
        "triggers": [
            {"raw_identity": "public.example.trigger", "native": {"oid": "600"}, "function": {"prosrc": "fixed"}}
        ],
    }


def test_raw_lineage_only_source_proven_five_fields_and_preserves_full_inputs():
    before = raw_lineage_fixture()
    current = copy.deepcopy(before)
    for row in current["relations"][:2]:
        row["native"]["relfilenode"] = str(int(row["native"]["relfilenode"]) + 1000)
        row["native"]["relfrozenxid"] = "30"
    current["relations"][1]["native"]["reltoastrelid"] = "1300"
    frozen_before = copy.deepcopy(before)
    frozen_current = copy.deepcopy(current)
    delta = d.validate_raw_lineage(before, current)
    assert len(delta) == 5 and before == frozen_before and current == frozen_current
    assert {x["identity"] for x in delta} == set(d.REFRESH_RAW_FIELDS)
    assert all(set(x) == {"kind", "identity", "field", "before", "current"} for x in delta)


@pytest.mark.parametrize(
    "tamper",
    [
        "unrelated_relation",
        "unapproved_field",
        "climate_toast",
        "owner",
        "oid",
        "kind",
        "definition",
        "index",
        "constraint",
        "trigger",
        "extra_object",
        "missing_object",
        "type",
        "zero",
        "overflow",
    ],
)
def test_closed_raw_lineage_refuses_unrelated_or_untyped_drift(tamper):
    before = raw_lineage_fixture()
    current = copy.deepcopy(before)
    row = current["relations"][1]
    if tamper == "unrelated_relation":
        current["relations"][2]["native"]["relfilenode"] = "999"
    elif tamper == "unapproved_field":
        row["native"]["relpages"] = 2
    elif tamper == "climate_toast":
        current["relations"][0]["native"]["reltoastrelid"] = "999"
    elif tamper == "owner":
        row["native"]["relowner"] = "999"
    elif tamper == "oid":
        row["native"]["oid"] = "999"
    elif tamper == "kind":
        row["native"]["relkind"] = "r"
    elif tamper == "definition":
        row["raw_definition_text"] = "changed"
    elif tamper == "index":
        current["indexes"][0]["native"]["relfilenode"] = "999"
    elif tamper == "constraint":
        current["constraints"][0]["definition"] = "changed"
    elif tamper == "trigger":
        current["triggers"][0]["function"]["prosrc"] = "changed"
    elif tamper == "extra_object":
        current["relations"].append({"identity": "public.foreign"})
    elif tamper == "missing_object":
        current["relations"].pop()
    elif tamper == "type":
        row["native"]["relfilenode"] = 999
    elif tamper == "zero":
        row["native"]["reltoastrelid"] = "0"
    elif tamper == "overflow":
        row["native"]["relfrozenxid"] = str(2**32)
    with pytest.raises(ValueError):
        d.validate_raw_lineage(before, current)


def test_native_pg16_nonconcurrent_refresh_exhibits_closed_delta(private_pg):
    q = private_pg
    q(
        "DROP MATERIALIZED VIEW public.v_relay_stuck; CREATE MATERIALIZED VIEW public.v_relay_stuck AS SELECT repeat(md5('x'),300) AS sample;"
    )
    capture = "SET search_path=pg_catalog,pg_temp; SELECT (" + d.t.c0.portability_native_facts_sql() + ");"
    before = json.loads(q(capture))
    q("REFRESH MATERIALIZED VIEW public.v_relay_stuck; REFRESH MATERIALIZED VIEW public.v_climate_merged;")
    current = json.loads(q(capture))
    delta = d.validate_raw_lineage(before, current)
    assert {x["identity"] for x in delta} == set(d.REFRESH_RAW_FIELDS)
    assert {"native.relfilenode", "native.reltoastrelid", "native.relfrozenxid"} == {x["field"] for x in delta}
    assert len(delta) == 5
