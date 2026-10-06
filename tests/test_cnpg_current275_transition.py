"""Closed275 adapter integrity and native rollback; synthetic digests aren't live proof."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import test_cnpg_target_runtime_transition as original

private_pg = original.private_pg

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("current275", ROOT / "scripts/cnpg-current275-transition.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def migration():
    return (ROOT / m.MIGRATION).read_bytes()


def test_exact_six_computed_guards_only_and_canonical_receipts_retained():
    raw = migration()
    out = m.remap_migration(raw)
    assert m.sha(raw) == m.MIGRATION_SHA
    for value in (*m.NATIVE_BEFORE, *m.NATIVE_AFTER):
        assert value in out
    for value in (*m.CANONICAL_BEFORE, *m.CANONICAL_AFTER):
        assert value in out
    assert out.count("UPDATE public.runtime_ordinary_login_attestation_receipts") == 2
    assert "UPDATE public.mcp_runtime_boundary_receipt" not in out
    with pytest.raises(ValueError, match="exact reviewed"):
        m.remap_migration(raw + b"\n")


@pytest.mark.parametrize("cluster", ("verdify-db", "verdify-cnpg-s2-pitr-a-frozen", "arbitrary"))
def test_unowned_targets_refused(cluster):
    with pytest.raises(ValueError, match="closed275"):
        m.configured(cluster)


def test_new275_admission_is_separate_and_exact_target_bound():
    for target in m.TARGETS:
        m.configured(target)
        ddl, bodies = m.t.ddl(True, physical_target=target)
        assert "CREATE TABLE " + m.TABLE in ddl
        assert "current_setting('cluster_name')<>'" + target + "'" in ddl
        assert "CREATE TABLE public.cnpg_qualified_runtime_receipts" not in ddl
        assert "UPDATE public.cnpg_qualified_runtime_receipts" not in ddl
        assert len(bodies) == 2


def synthetic_witnesses():
    before = {
        "ledger": [["db/migrations", "274.sql", 274, "a" * 64, "runner"]],
        "seals": {
            "ordinary": [[m.t.LOGINS[i], m.CANONICAL_BEFORE[i]] for i in range(2)],
            "mcp": [m.CANONICAL_BEFORE[2]],
        },
        "boundaries": {},
        "portable_catalog": [["function", m.CATALOG_ID, "old"], ["function", "public.other()", "same"]],
        "raw_portable_catalog_v2": [["function", m.CATALOG_ID, "old"], ["function", "public.other()", "same"]],
        "roles": {"10": "postgres"},
        "database": "verdify_rehearsal",
    }
    for login, native in zip(m.t.LOGINS, m.NATIVE_BEFORE, strict=True):
        before["boundaries"][login] = {
            "native": native,
            "installed_shape_verified": True,
            "raw_entries": ["unchanged"] + ([] if login == m.t.LOGINS[2] else [m.ENTRY_PREFIX + "old"]),
            "semantic_entries": ["unchanged"] + ([] if login == m.t.LOGINS[2] else [m.ENTRY_PREFIX + "old"]),
        }
    after = copy.deepcopy(before)
    after["ledger"] = sorted(
        [*copy.deepcopy(before["ledger"]), copy.deepcopy(m.LEDGER_ROW)], key=lambda r: (r[0], r[1])
    )
    after["seals"] = {
        "ordinary": [[m.t.LOGINS[i], m.CANONICAL_AFTER[i]] for i in range(2)],
        "mcp": [m.CANONICAL_AFTER[2]],
    }
    for key in ("portable_catalog", "raw_portable_catalog_v2"):
        after[key][0][2] = "new"
    for login, native in zip(m.t.LOGINS, m.NATIVE_AFTER, strict=True):
        after["boundaries"][login]["native"] = native
        for key in ("raw_entries", "semantic_entries"):
            after["boundaries"][login][key] = ["unchanged"] + (
                [] if login == m.t.LOGINS[2] else [m.ENTRY_PREFIX + "new"]
            )
    return before, after


@pytest.mark.parametrize(
    "tamper", ("none", "role", "ledger", "seal", "otherfunction", "boundary", "implementation", "missing")
)
def test_full_witness_bridge_refuses_unreviewed_drift(monkeypatch, tamper):
    monkeypatch.setattr(m.t.c0, "checked", lambda *a, **k: None)
    before, after = synthetic_witnesses()
    monkeypatch.setattr(m, "NATIVE274_WITNESS_SHA", m.sha((json.dumps(before, indent=2) + "\n").encode()))
    if tamper == "role":
        after["roles"]["10"] = "changed"
    if tamper == "ledger":
        after["ledger"][0][3] = "b" * 64
    if tamper == "seal":
        after["seals"]["mcp"] = ["b" * 64]
    if tamper == "otherfunction":
        after["portable_catalog"][1][2] = "changed"
    if tamper == "boundary":
        after["boundaries"][m.t.LOGINS[0]]["raw_entries"][0] = "changed"
    if tamper == "implementation":
        after["boundaries"][m.t.LOGINS[0]]["installed_shape_verified"] = False
    if tamper == "missing":
        after["raw_portable_catalog_v2"].pop()
    if tamper == "none":
        m.validate_bridge(before, after)
    else:
        with pytest.raises(ValueError):
            m.validate_bridge(before, after)


def test_native_remapped_source_keeps_canonical_custody_and_exact_proc_rolls_back(private_pg):
    q = private_pg
    function = (
        (ROOT / "db/migrations/179-lighting-minutes-policy-planner-over-device.sql")
        .read_text()
        .split("CREATE OR REPLACE FUNCTION", 1)[1]
    )
    q("SET check_function_bodies=off;CREATE OR REPLACE FUNCTION" + function)
    assert (
        q(
            "SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure"
        )
        == "049c12f4e73337b2bfc97eda1faf4db9685b98da64e1184c7a90f1d36520cbdd"
    )
    q(
        "DROP TABLE schema_migrations;CREATE TABLE schema_migrations(source text,seq int,filename text,sha256 text,stamp_method text);INSERT INTO schema_migrations VALUES('db/migrations',274,'db/migrations/274-qualified-physical-crop-band-publication.sql','0b939df1a79e3ce71835e59aedb5d1898805cd43cae72b79fa905f77067a8e4f','runner');"
    )
    for i, login in enumerate(m.t.LOGINS[:2]):
        q(
            f"UPDATE runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('{m.CANONICAL_BEFORE[i]}','hex') WHERE login_name='{login}';"
        )
    q(f"UPDATE mcp_runtime_boundary_receipt SET boundary_sha256=decode('{m.CANONICAL_BEFORE[2]}','hex');")
    q(f"""CREATE OR REPLACE FUNCTION public.fn_runtime_ordinary_boundary_digest(text) RETURNS bytea LANGUAGE sql AS $$ SELECT decode(CASE WHEN $1='verdify_api_runtime_login' THEN CASE WHEN (SELECT proconfig @> ARRAY['jit=off'] FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure) THEN '{m.NATIVE_AFTER[0]}' ELSE '{m.NATIVE_BEFORE[0]}' END ELSE CASE WHEN (SELECT proconfig @> ARRAY['jit=off'] FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure) THEN '{m.NATIVE_AFTER[1]}' ELSE '{m.NATIVE_BEFORE[1]}' END END,'hex') $$;
    CREATE OR REPLACE FUNCTION public.fn_mcp_runtime_boundary_digest() RETURNS bytea LANGUAGE sql AS $$ SELECT decode('{m.NATIVE_BEFORE[2]}','hex') $$;""")
    before = q(
        "SELECT to_jsonb(p) FROM pg_proc p WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure"
    )
    receipts = q("SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM runtime_ordinary_login_attestation_receipts r")
    result = q(
        "BEGIN;"
        + m.remap_migration(migration())
        + "SELECT proconfig FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure;ROLLBACK;"
    )
    assert "{jit=off}" in result
    assert (
        q(
            "SELECT to_jsonb(p) FROM pg_proc p WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure"
        )
        == before
    )
    assert (
        q("SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM runtime_ordinary_login_attestation_receipts r")
        == receipts
    )


def test_atomic_full_bridge_sql_compiles_and_rolls_back_native_custody(private_pg, monkeypatch):
    q = private_pg
    # Build the native source preconditions, then exercise the complete bridge
    # transaction around a small explicit witness stand-in. This is syntax and
    # atomic metadata custody proof, not live full-catalog/data acceptance.
    test_native_remapped_source_keeps_canonical_custody_and_exact_proc_rolls_back(q)
    q(
        "ALTER TABLE schema_migrations ADD COLUMN applied_at timestamptz,ADD COLUMN duration_ms integer,ADD COLUMN applied_by text;"
    )
    before, after = synthetic_witnesses()
    before["ledger"] = [
        [
            "db/migrations",
            "db/migrations/274-qualified-physical-crop-band-publication.sql",
            274,
            "0b939df1a79e3ce71835e59aedb5d1898805cd43cae72b79fa905f77067a8e4f",
            "runner",
        ]
    ]
    after["ledger"] = sorted([*copy.deepcopy(before["ledger"]), m.LEDGER_ROW], key=lambda r: (r[0], r[1]))
    monkeypatch.setattr(m, "NATIVE274_WITNESS_SHA", m.sha((json.dumps(before, indent=2) + "\n").encode()))
    lit = m.t.literal
    projected = f"""SELECT {lit(json.dumps(before))}::jsonb || jsonb_build_object(
      'ledger',(SELECT jsonb_agg(jsonb_build_array(source,filename,seq,sha256,stamp_method) ORDER BY source,filename) FROM public.schema_migrations),
      'seals',jsonb_build_object('ordinary',(SELECT jsonb_agg(jsonb_build_array(login_name,encode(boundary_sha256,'hex')) ORDER BY login_name) FROM public.runtime_ordinary_login_attestation_receipts),'mcp',(SELECT jsonb_agg(encode(boundary_sha256,'hex')) FROM public.mcp_runtime_boundary_receipt)),
      'portable_catalog',CASE WHEN coalesce((SELECT proconfig @> ARRAY['jit=off'] FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure),false) THEN {lit(json.dumps(after["portable_catalog"]))}::jsonb ELSE {lit(json.dumps(before["portable_catalog"]))}::jsonb END,
      'raw_portable_catalog_v2',CASE WHEN coalesce((SELECT proconfig @> ARRAY['jit=off'] FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure),false) THEN {lit(json.dumps(after["raw_portable_catalog_v2"]))}::jsonb ELSE {lit(json.dumps(before["raw_portable_catalog_v2"]))}::jsonb END,
      'boundaries',CASE WHEN coalesce((SELECT proconfig @> ARRAY['jit=off'] FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure),false) THEN {lit(json.dumps(after["boundaries"]))}::jsonb ELSE {lit(json.dumps(before["boundaries"]))}::jsonb END)"""
    monkeypatch.setattr(m.t.c0, "checked", lambda *a, **k: None)
    monkeypatch.setattr(m, "witness_parts", lambda cluster: ("", projected))
    monkeypatch.setattr(m.t, "bootstrap_owner_sql", lambda sql, **kw: sql)
    sql = m.emit_bridge(m.TARGETS[0], before, migration())
    sql = (
        sql.replace(
            "current_setting('cluster_name')<>'verdify-cnpg-s2'",
            "current_setting('cluster_name')<>'verdify-cnpg-rehearsal'",
        )
        .replace("::int<>160013", "::int<>" + q("SHOW server_version_num"))
        .replace("<>16447", "<>" + q("SELECT oid FROM pg_database WHERE datname=current_database()"))
    )
    raw = q(sql)
    record = json.loads(next(x for x in raw.splitlines() if x.startswith("{")))
    assert record["post_witness"] == after
    m.validate_bridge(before, record["post_witness"])
    assert q("SELECT max(seq) FROM schema_migrations") == "274"
    assert (
        q(
            "SELECT proconfig IS NULL FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure"
        )
        == "t"
    )
    assert (
        q(
            "SELECT encode(boundary_sha256,'hex') FROM runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login'"
        )
        == m.CANONICAL_BEFORE[0]
    )


def test_real_mcp_boundary_shape_has_no_semantic_entries(monkeypatch):
    before, after = synthetic_witnesses()
    for witness in (before, after):
        witness["boundaries"][m.t.LOGINS[2]].pop("semantic_entries")
    monkeypatch.setattr(m.t.c0, "checked", lambda *a, **k: None)
    monkeypatch.setattr(m, "NATIVE274_WITNESS_SHA", m.sha((json.dumps(before, indent=2) + "\n").encode()))
    m.validate_bridge(before, after)


def test_closed_complete_predecessor_pin_rejects_unqualified_catalog():
    before, _ = synthetic_witnesses()
    with pytest.raises(ValueError, match="complete sealed native274"):
        m.checked_before(before)
