"""Source-only target transition proofs; private PG fixture is synthetic.

Actual PG16.13/Timescale target qualification remains ROOT execution. This fixture
executes selected 217/259 attester bodies, with a catalog-sensitive digest stand-in;
it does not certify the complete production protected closure or restore data.
"""

import ast
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from test_cnpg_restore_qualification import witnesses

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cnpg_target", ROOT / "scripts/cnpg-target-runtime-transition.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


@pytest.fixture(autouse=True)
def synthetic_frozen_catalog_pin(monkeypatch):
    monkeypatch.setattr(
        t.c0, "FROZEN_SOURCE_V2_CATALOG_SHA256", t.c0.catalog_sha256([["function", "vision.example()", "d" * 64]])
    )


def binding():
    return {
        "cluster_uid": "a" * 8 + "-" + "a" * 4 + "-" + "a" * 4 + "-" + "a" * 4 + "-" + "a" * 12,
        "pod": "verdify-cnpg-rehearsal-1",
        "pod_uid": "b" * 8 + "-" + "b" * 4 + "-" + "b" * 4 + "-" + "b" * 4 + "-" + "b" * 12,
        "operand_digest": t.operator.DIGEST,
    }


def test_fixed_source_ddl_is_transactional_and_does_not_edit_digest_or_history():
    ddl, bodies = t.ddl()
    assert len(bodies) == 2
    assert "CREATE OR REPLACE FUNCTION public.fn_runtime_ordinary_boundary_digest" not in ddl
    assert "CREATE OR REPLACE FUNCTION public.fn_mcp_runtime_boundary_digest" not in ddl
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in ddl
    assert "INSERT INTO public.schema_migrations" not in ddl
    assert not any(s in ddl.upper() for s in ["COMMIT;", "CONCURRENTLY", "VACUUM ", "ALTER SYSTEM", "ALTER ROLE"])
    assert "160013" in ddl and "verdify_rehearsal" in ddl and "verdify-cnpg-rehearsal" in ddl
    source, target = witnesses()
    t.validate_inputs(source, target, binding())
    sql = t.emit_sql(target)
    assert sql.rstrip().endswith("ROLLBACK;") and "\nCOMMIT;" not in sql
    assert f"INSERT INTO {t.TABLE}" not in sql
    assert "IS DISTINCT FROM v_original" in sql
    assert "current_user<>session_user" in sql and "inet_client_addr() IS NOT NULL" in sql


@pytest.mark.parametrize(
    "key,value",
    [("cluster_uid", "wrong"), ("pod_uid", "wrong"), ("pod", "verdify-db-0"), ("operand_digest", "sha256:" + "0" * 64)],
)
def test_exact_target_binding_refuses_wrong_identity(key, value):
    source, target = witnesses()
    bad = binding()
    bad[key] = value
    with pytest.raises(ValueError):
        t.validate_inputs(source, target, bad)


@pytest.mark.parametrize("field", ["ledger", "seals", "roles", "database", "server"])
def test_post_qualification_preserves_original_facts(field):
    _, target = witnesses()
    after = copy.deepcopy(target)
    after[field] = [] if field in ["ledger", "seals"] else "changed"
    with pytest.raises((ValueError, TypeError, AttributeError)):
        t.validate_post(target, after)


def test_install_cannot_be_emitted_without_reviewed_successor_hash():
    _, target = witnesses()
    with pytest.raises(ValueError):
        t.emit_sql(target, reviewed_post=target)


@pytest.mark.parametrize("version", [t.VERSION, "cnpg-physical-runtime-transition-v1"])
@pytest.mark.parametrize("mode", ["rollback-qualification", "install"])
def test_composite_record_accepts_two_bounded_witnesses_without_broadening_single_reader(
    tmp_path, monkeypatch, version, mode
):
    monkeypatch.setattr(t.c0, "WITNESS_MAX_BYTES", 1024)
    record = {
        "version": version,
        "mode": mode,
        "ddl_sha256": "a" * 64,
        "before_witness": {"proof": "x" * 700},
        "post_witness": {"proof": "y" * 700},
    }
    path = tmp_path / "native-record.json"
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    assert len(raw) > t.c0.WITNESS_MAX_BYTES
    with pytest.raises(ValueError, match="witness exceeds bound"):
        t.c0.read_witness(path)
    actual, sha = t.read_transition_record(path, version=version, mode=mode)
    assert actual == record and sha == hashlib.sha256(raw).hexdigest()
    assert t.c0.WITNESS_MAX_BYTES == 1024


@pytest.mark.parametrize(
    "tamper",
    [
        "oversize-before",
        "oversize-post",
        "oversize-envelope",
        "wrong-version",
        "wrong-mode",
        "wrong-ddl-hash",
        "missing-witness",
        "nonobject-witness",
        "extra-key",
        "duplicate-key",
        "symlink",
    ],
)
def test_composite_record_refuses_unbounded_or_ambiguous_custody(tmp_path, monkeypatch, tamper):
    monkeypatch.setattr(t.c0, "WITNESS_MAX_BYTES", 1024)
    record = {
        "version": t.VERSION,
        "mode": "rollback-qualification",
        "ddl_sha256": "a" * 64,
        "before_witness": {"proof": "before"},
        "post_witness": {"proof": "after"},
    }
    if tamper in {"oversize-before", "oversize-post"}:
        record["before_witness" if tamper == "oversize-before" else "post_witness"] = {"proof": "x" * 1024}
    elif tamper == "wrong-version":
        record["version"] = "unknown"
    elif tamper == "wrong-mode":
        record["mode"] = "install"
    elif tamper == "wrong-ddl-hash":
        record["ddl_sha256"] = "invalid"
    elif tamper == "missing-witness":
        record.pop("post_witness")
    elif tamper == "nonobject-witness":
        record["post_witness"] = []
    elif tamper == "extra-key":
        record["bypass"] = True
    raw = json.dumps(record).encode()
    if tamper == "oversize-envelope":
        raw = b" " * (2 * t.c0.WITNESS_MAX_BYTES + 1025)
    elif tamper == "duplicate-key":
        raw = raw[:-1] + b',"post_witness":{}}'
    path = tmp_path / "native-record.json"
    path.write_bytes(raw)
    if tamper == "symlink":
        link = tmp_path / "linked-record.json"
        link.symlink_to(path)
        path = link
    with pytest.raises(ValueError):
        t.read_transition_record(path, version=t.VERSION, mode="rollback-qualification")


@pytest.mark.parametrize("version,mode", [("arbitrary", "install"), (t.VERSION, "unknown")])
def test_composite_record_reader_has_no_generic_version_or_mode_override(tmp_path, version, mode):
    with pytest.raises(ValueError, match="unsupported transition record"):
        t.read_transition_record(tmp_path / "never-read.json", version=version, mode=mode)


def test_complete_witness_literals_are_outside_atomic_compiler_body():
    _, target = witnesses()
    sql = t.emit_sql(target)
    before, block = sql.split("DO $native_transition$", 1)
    assert t.literal(json.dumps(target, separators=(",", ":"))) in before
    assert "cnpg_transition_expected_before" in before and "cnpg_transition_expected_post" in before
    assert t.literal(json.dumps(target, separators=(",", ":"))) not in block
    assert "current_setting('verdify.cnpg_transition_expected_before')::jsonb" in block
    assert "v_before IS DISTINCT FROM v_expected_before" in block
    assert "IS DISTINCT FROM v_original" in block
    assert before.index("BEGIN;") < before.index("cnpg_transition_expected_before")
    assert block.rstrip().endswith("ROLLBACK;")


def test_actual_witness_scale_inputs_preserve_every_byte_and_expire_with_transaction(private_pg):
    # The native target v3 JSON was 35,046,796 bytes. Exercise comparable input
    # size through the actual source-generated SQL, not a reduced fixture cap.
    before = {"raw": ["x" * 35_000_000, {"quoted": "it's \\\"\nΩ"}], "original_seals": ["unaltered"]}
    after = {"raw": before["raw"], "original_seals": before["original_seals"], "post": "reviewed"}
    inputs = t.transaction_witness_inputs_sql(before, after)
    block = """DO $input_test$ DECLARE v jsonb;
    BEGIN
      v := current_setting('verdify.cnpg_transition_expected_before')::jsonb;
      IF length(v->'raw'->>0)<>35000000 OR v->'original_seals'<> '["unaltered"]'::jsonb THEN
        RAISE EXCEPTION 'incomplete before input';
      END IF;
      IF current_setting('verdify.cnpg_transition_expected_post')::jsonb->>'post'<>'reviewed' THEN
        RAISE EXCEPTION 'incomplete successor input';
      END IF;
    END $input_test$;"""
    assert len(block) < 1000 and "x" * 1000 not in block
    result = private_pg(
        "BEGIN;\n"
        + inputs
        + "\n"
        + block
        + "\nSELECT current_setting('verdify.cnpg_transition_expected_before')::jsonb="
        + t.literal(json.dumps(before, separators=(",", ":")))
        + "::jsonb;"
        + "\nSELECT current_setting('verdify.cnpg_transition_expected_post')::jsonb="
        + t.literal(json.dumps(after, separators=(",", ":")))
        + "::jsonb;"
        + "\nROLLBACK;\nSELECT coalesce(current_setting('verdify.cnpg_transition_expected_before',true),'')=''"
        + " AND coalesce(current_setting('verdify.cnpg_transition_expected_post',true),'')='';"
    )
    assert result.splitlines() == ["t"] * 5
    committed = private_pg(
        "BEGIN;\n"
        + t.transaction_witness_inputs_sql({"before": "complete"}, {"after": "reviewed"})
        + "\nCOMMIT;\nSELECT coalesce(current_setting('verdify.cnpg_transition_expected_before',true),'')=''"
        + " AND coalesce(current_setting('verdify.cnpg_transition_expected_post',true),'')='';"
    )
    assert committed.splitlines() == ["t"] * 3


@pytest.fixture
def private_pg(request):
    directory = os.environ.get("CNPG_TEST_PG_BIN")
    if not directory:
        pytest.skip("set CNPG_TEST_PG_BIN for private socket native attester proof")
    peer_profile = getattr(request, "param", None) == "bootstrap_peer"
    bootstrap = "postgres" if peer_profile else "c5_fixture"
    setting_up = True
    pg = Path(directory)
    cluster = Path(tempfile.mkdtemp(prefix="c5-native-pg-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DB_", "POSTGRES_"))}
    env["LC_ALL"] = "C"

    def run(args, **kwargs):
        r = subprocess.run(args, text=True, capture_output=True, env=env, timeout=60, **kwargs)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def query(sql, *, database="verdify_rehearsal", user="verdify", check=True):
        connection_user = user
        if setting_up and peer_profile and user == "verdify":
            connection_user = bootstrap
            sql = "SET SESSION AUTHORIZATION verdify;\n" + sql
        r = subprocess.run(
            [
                str(pg / "psql"),
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                str(cluster),
                "-p",
                "55475",
                "-U",
                connection_user,
                "-d",
                database,
            ],
            input=sql,
            text=True,
            capture_output=True,
            env=env,
            timeout=60,
        )
        if check:
            assert r.returncode == 0, r.stderr
        return r.stdout.strip() if check else r

    started = False
    try:
        run(
            [
                str(pg / "initdb"),
                "-D",
                str(cluster / "data"),
                "-U",
                bootstrap,
                "--auth-local=" + ("peer" if peer_profile else "trust"),
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        if peer_profile:
            import getpass

            # Initial disposable fixture configuration only: never edit estate
            # HBA or add a password. The only admitted peer role is postgres.
            (cluster / "data/pg_ident.conf").write_text("c5_peer " + getpass.getuser() + " postgres\n")
            (cluster / "data/pg_hba.conf").write_text("local all postgres peer map=c5_peer\nlocal all all reject\n")
        run(
            [
                str(pg / "pg_ctl"),
                "-D",
                str(cluster / "data"),
                "-l",
                str(cluster / "server.log"),
                "-o",
                f"-k {cluster} -c listen_addresses='' -c cluster_name=verdify-cnpg-rehearsal -p 55475",
                "-w",
                "start",
            ]
        )
        started = True
        query(
            "CREATE ROLE verdify SUPERUSER LOGIN; CREATE DATABASE verdify_rehearsal OWNER verdify;",
            database="postgres",
            user=bootstrap,
        )
        query(
            "CREATE EXTENSION pgcrypto; CREATE MATERIALIZED VIEW public.v_relay_stuck AS SELECT 1 AS value; CREATE MATERIALIZED VIEW public.v_climate_merged AS SELECT 1 AS value; CREATE TABLE control_experiments(x integer); CREATE TABLE control_assignments(x integer); "
            "CREATE TABLE schema_migrations(source text,filename text,sha256 text); INSERT INTO schema_migrations VALUES('fixture','fixture','immutable');"
        )
        for login in t.LOGINS:
            duty = login.removesuffix("_login")
            query(
                f"CREATE ROLE {duty} NOLOGIN NOINHERIT; CREATE ROLE {login} LOGIN INHERIT; GRANT {duty} TO {login} WITH ADMIN FALSE,INHERIT TRUE,SET TRUE;"
            )
        ordinary = (
            t.c0.boundary.SOURCE.read_text()
            .split("CREATE OR REPLACE FUNCTION public.fn_runtime_attest_ordinary_login()", 1)[1]
            .split("\nDO $attestation_objects$", 1)[0]
        )
        mcp = (
            t.MCP_ATTEST_SOURCE.read_text()
            .split("CREATE FUNCTION public.fn_mcp_runtime_attest_ordinary_login()", 1)[1]
            .split("REVOKE ALL ON FUNCTION public.fn_mcp_runtime_attest_ordinary_login()", 1)[0]
        )
        query(
            """CREATE TABLE runtime_ordinary_login_attestation_receipts(login_name text PRIMARY KEY,boundary_sha256 bytea,captured_at timestamptz);
        CREATE TABLE mcp_runtime_boundary_receipt(singleton boolean PRIMARY KEY,boundary_sha256 bytea);
        INSERT INTO runtime_ordinary_login_attestation_receipts VALUES('verdify_api_runtime_login',decode(repeat('99',32),'hex'),clock_timestamp()),('verdify_ingestor_runtime_login',decode(repeat('99',32),'hex'),clock_timestamp());
        INSERT INTO mcp_runtime_boundary_receipt VALUES(true,decode(repeat('99',32),'hex'));
        REVOKE ALL ON runtime_ordinary_login_attestation_receipts,mcp_runtime_boundary_receipt FROM PUBLIC;
        CREATE FUNCTION fn_runtime_ordinary_boundary_digest(text) RETURNS bytea LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
          SELECT public.digest(pg_get_functiondef(to_regprocedure('public.fn_runtime_attest_ordinary_login()'))||$1||
            (SELECT jsonb_agg(jsonb_build_array(rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls) ORDER BY rolname)::text
             FROM pg_roles WHERE rolname LIKE 'verdify_%'),'sha256') $$;
        CREATE FUNCTION fn_mcp_runtime_boundary_digest() RETURNS bytea LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
          SELECT public.digest(pg_get_functiondef(to_regprocedure('public.fn_mcp_runtime_attest_ordinary_login()'))||coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY roleid,member)::text FROM pg_auth_members m WHERE member=(SELECT oid FROM pg_roles WHERE rolname='verdify_mcp_runtime_login')),''),'sha256') $$;
        """
            + "CREATE FUNCTION public.fn_runtime_attest_ordinary_login()"
            + ordinary
            + "CREATE FUNCTION public.fn_mcp_runtime_attest_ordinary_login()"
            + mcp
        )
        query(
            "REVOKE ALL ON FUNCTION fn_runtime_ordinary_boundary_digest(text),fn_mcp_runtime_boundary_digest(),fn_runtime_attest_ordinary_login(),fn_mcp_runtime_attest_ordinary_login() FROM PUBLIC; "
            "GRANT EXECUTE ON FUNCTION fn_runtime_attest_ordinary_login() TO verdify_api_runtime,verdify_ingestor_runtime; GRANT EXECUTE ON FUNCTION fn_mcp_runtime_attest_ordinary_login() TO verdify_mcp_runtime;"
        )
        setting_up = False
        yield query
    finally:
        if started:
            run([str(pg / "pg_ctl"), "-D", str(cluster / "data"), "-m", "fast", "-w", "stop"])
        shutil.rmtree(cluster)


def originals(query):
    return query("SELECT " + t.original_facts_sql())


def actual_digests(query):
    return json.loads(
        query(
            "SELECT jsonb_build_object('verdify_api_runtime_login',encode(fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex'),"
            "'verdify_ingestor_runtime_login',encode(fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex'),"
            "'verdify_mcp_runtime_login',encode(fn_mcp_runtime_boundary_digest(),'hex'));"
        )
    )


def startups(query):
    result = {}
    for login, path, name in [
        (t.LOGINS[0], "api/main.py", "_ORDINARY_RUNTIME_ROLE_ATTESTATION_SQL"),
        (t.LOGINS[1], "ingestor/ingestor.py", "_ORDINARY_RUNTIME_ROLE_ATTESTATION_SQL"),
        (t.LOGINS[2], "mcp/server.py", "_MCP_RUNTIME_DB_ATTESTATION_SQL"),
    ]:
        tree = ast.parse((ROOT / path).read_text())
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == name for x in n.targets)
        )
        sql = ast.literal_eval(node.value)
        result[login] = query("SET search_path=pg_catalog,public,pg_temp; " + sql + ";", user=login)
    return result


def qualify_install(query, monkeypatch, *, bootstrap_profile=False):
    # Mac16.15 is explicitly a synthetic execution fixture, not PG16.13 credit.
    monkeypatch.setattr(t, "SERVER", int(query("SHOW server_version_num")))
    ddl, _ = t.ddl(bootstrap_profile)
    before = originals(query)
    measured = json.loads(
        query(
            "BEGIN; "
            + ddl
            + " SELECT jsonb_build_object('verdify_api_runtime_login',encode(fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex'),"
            "'verdify_ingestor_runtime_login',encode(fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex'),"
            "'verdify_mcp_runtime_login',encode(fn_mcp_runtime_boundary_digest(),'hex')); ROLLBACK;"
        )
    )
    assert originals(query) == before and query(f"SELECT to_regclass('{t.TABLE}') IS NULL") == "t"
    values = ",".join(f"('{login}',decode('{measured[login]}','hex'),'{('a' * 64)}')" for login in t.LOGINS)
    query("BEGIN; " + ddl + f" INSERT INTO {t.TABLE} VALUES {values}; COMMIT;")
    assert originals(query) == before and actual_digests(query) == measured
    return before


def test_selected_native_attesters_admit_existing_three_startup_paths_and_keep_history(private_pg, monkeypatch):
    q = private_pg
    assert set(startups(q).values()) == {"f"}
    before = qualify_install(q, monkeypatch)
    assert set(startups(q).values()) == {"t"}
    assert originals(q) == before
    for login in t.LOGINS:
        assert q(f"SELECT * FROM {t.TABLE}", user=login, check=False).returncode != 0
        assert (
            q(f"UPDATE {t.TABLE} SET boundary_sha256=decode(repeat('00',32),'hex')", user=login, check=False).returncode
            != 0
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "wrong-digest",
        "extra-membership",
        "role-super",
        "table-grant",
        "table-view",
        "table-constraint",
        "table-extra-column",
        "table-nullable",
        "wrong-version",
    ],
)
def test_native_admission_fails_closed_for_target_catalog_or_receipt_drift(private_pg, monkeypatch, tamper):
    q = private_pg
    qualify_install(q, monkeypatch)
    if tamper == "wrong-digest":
        q(f"UPDATE {t.TABLE} SET boundary_sha256=decode(repeat('00',32),'hex')")
    elif tamper == "extra-membership":
        q("CREATE ROLE unexpected NOLOGIN; GRANT unexpected TO verdify_mcp_runtime_login")
    elif tamper == "role-super":
        q("ALTER ROLE verdify_api_runtime_login SUPERUSER")
    elif tamper == "table-grant":
        q(f"GRANT SELECT ON {t.TABLE} TO PUBLIC")
    elif tamper == "table-constraint":
        q(f"ALTER TABLE {t.TABLE} DROP CONSTRAINT cnpg_qualified_runtime_receipts_qualification_sha256_check")
    elif tamper == "table-extra-column":
        q(f"ALTER TABLE {t.TABLE} ADD COLUMN unexpected text")
    elif tamper == "table-nullable":
        q(f"ALTER TABLE {t.TABLE} ALTER COLUMN qualification_sha256 DROP NOT NULL")
    elif tamper == "table-view":
        q(f"ALTER TABLE {t.TABLE} RENAME TO hidden_original; CREATE VIEW {t.TABLE} AS SELECT * FROM hidden_original")
    else:
        # Reinstall exact production160013 body on a different fixtureversion.
        monkeypatch.setattr(t, "SERVER", 160013)
        _, bodies = t.ddl()
        q(
            "CREATE OR REPLACE FUNCTION fn_runtime_attest_ordinary_login() RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$"
            + bodies["fn_runtime_attest_ordinary_login"]
            + "$body$;"
        )
        # Match the synthetic digest to the changed body: denial must be the
        # exact server guard, not merely a mismatched expected digest.
        q(
            f"UPDATE {t.TABLE} SET boundary_sha256=public.fn_runtime_ordinary_boundary_digest(login_name) WHERE login_name='verdify_api_runtime_login'"
        )
    values = startups(q)
    assert "f" in values.values()
    if tamper not in ["extra-membership", "role-super", "wrong-version"]:
        assert set(values.values()) == {"f"}


def test_actual_generated_ddl_is_valid_inside_outer_rollback_block(private_pg, monkeypatch):
    q = private_pg
    monkeypatch.setattr(t, "SERVER", int(q("SHOW server_version_num")))
    before = originals(q)
    definitions = q(
        "SELECT jsonb_agg(pg_get_functiondef(oid) ORDER BY oid) FROM pg_proc WHERE proname IN('fn_runtime_attest_ordinary_login','fn_mcp_runtime_attest_ordinary_login')"
    )
    payload, _ = t.ddl()
    assert (
        q(
            "BEGIN; DO $native_transition$ BEGIN "
            + payload
            + " PERFORM set_config('verdify.cnpg_transition_result','fixture',true); END $native_transition$; SELECT current_setting('verdify.cnpg_transition_result'); ROLLBACK;"
        )
        == "fixture"
    )
    assert originals(q) == before
    assert (
        q(
            "SELECT jsonb_agg(pg_get_functiondef(oid) ORDER BY oid) FROM pg_proc WHERE proname IN('fn_runtime_attest_ordinary_login','fn_mcp_runtime_attest_ordinary_login')"
        )
        == definitions
    )
    assert q(f"SELECT to_regclass('{t.TABLE}') IS NULL") == "t"


@pytest.mark.parametrize("tamper", ["wrong-mode", "wrong-ddl", "wrong-before", "missing-post", "extra-field"])
def test_native_qualification_record_cannot_be_substituted(tamper):
    _, target = witnesses()
    record = {
        "version": t.VERSION,
        "mode": "rollback-qualification",
        "ddl_sha256": t.digest(t.ddl()[0].encode()),
        "before_witness": target,
        "post_witness": target,
    }
    if tamper == "wrong-mode":
        record["mode"] = "install"
    elif tamper == "wrong-ddl":
        record["ddl_sha256"] = "0" * 64
    elif tamper == "wrong-before":
        record["before_witness"] = {}
    elif tamper == "missing-post":
        record.pop("post_witness")
    else:
        record["override"] = True
    with pytest.raises(ValueError):
        t.checked_qualification(target, record)


def test_complete_atomic_sql_rolls_back_qualification_and_bad_successor_then_admits(private_pg, monkeypatch):
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
    monkeypatch.setattr(t, "witness_select", lambda target=None: selects)
    before = json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects))
    historical = originals(q)
    result = q(t.emit_sql(before)).splitlines()
    record = json.loads(result[-1])
    assert record["mode"] == "rollback-qualification"
    after = t.checked_qualification(before, record)
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before and originals(q) == historical
    bad = copy.deepcopy(after)
    bad["boundaries"][t.LOGINS[0]]["native"] = "0" * 64
    failure = q(t.emit_sql(before, reviewed_post=bad, qualification_sha256="a" * 64), check=False)
    assert failure.returncode != 0 and "unqualified post-DDL catalog" in failure.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before and originals(q) == historical
    stale = copy.deepcopy(before)
    stale["database_acl"] = "stale"
    failure = q(t.emit_sql(stale), check=False)
    assert failure.returncode != 0 and "stale exact target witness" in failure.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
    tampered_input = t.emit_sql(before).replace(
        "DO $native_transition$",
        "SELECT set_config('verdify.cnpg_transition_expected_before','{}',true);\nDO $native_transition$",
        1,
    )
    failure = q(tampered_input, check=False)
    assert failure.returncode != 0 and "stale exact target witness" in failure.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
    assert originals(q) == historical
    # A nested witness/digest function cannot replace a stale reviewed input by
    # mutating its session GUC after the immutable block-entry capture.
    original_capture = " SELECT " + t.original_facts_sql() + " INTO v_original;"
    for input_before, input_after, corrected_guc, corrected_value, message in [
        (stale, None, "before", before, "stale exact target witness"),
        (before, bad, "post", after, "unqualified post-DDL catalog"),
    ]:
        sql = t.emit_sql(
            input_before,
            reviewed_post=input_after,
            qualification_sha256="a" * 64 if input_after is not None else None,
        )
        assert original_capture in sql
        injected = sql.replace(
            original_capture,
            " PERFORM set_config('verdify.cnpg_transition_expected_"
            + corrected_guc
            + "',"
            + t.literal(json.dumps(corrected_value, separators=(",", ":")))
            + ",true);\n"
            + original_capture,
            1,
        )
        failure = q(injected, check=False)
        assert failure.returncode != 0 and message in failure.stderr
        assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
        assert originals(q) == historical
    original_ddl = t.ddl
    for injected in [
        "GRANT SELECT ON public.control_experiments TO PUBLIC;",
        "CREATE TABLE public.unapproved_native_object(x integer);",
        f"ALTER TABLE {t.TABLE} SET UNLOGGED;",
    ]:
        with monkeypatch.context() as scope:

            def modified_ddl(profile=False):
                payload, bodies = original_ddl(profile)
                return payload + "\n" + injected, bodies

            scope.setattr(t, "ddl", modified_ddl)
            refused = q(t.emit_sql(before, reviewed_post=after, qualification_sha256="a" * 64), check=False)
            assert refused.returncode != 0
            assert "CNPG native transition refuses raw" in refused.stderr or "new raw object shape" in refused.stderr
        assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
        assert originals(q) == historical
    installed = json.loads(q(t.emit_sql(before, reviewed_post=after, qualification_sha256="a" * 64)).splitlines()[-1])
    assert installed["mode"] == "install"
    assert {k: v for k, v in installed["post_witness"].items() if k != "portability_native_facts"} == {
        k: v for k, v in after.items() if k != "portability_native_facts"
    }
    t.validate_raw_delta(before, installed["post_witness"])
    assert installed["post_witness"]["portability_native_facts"] != after["portability_native_facts"]
    assert originals(q) == historical and set(startups(q).values()) == {"t"}


@pytest.mark.parametrize(
    "sql",
    [
        "COMMIT;",
        "CREATE INDEX CONCURRENTLY idx ON example(x);",
        "VACUUM example;",
        "ALTER SYSTEM SET work_mem='4MB';",
        "CREATE DATABASE wrong;",
    ],
)
def test_existing_rollback_classifier_refuses_commit_forcing_ddl(sql):
    with pytest.raises(ValueError, match="nontransactional"):
        t.classify_ddl(sql)
    assert t.classify_ddl(t.ddl()[0]) == {"self_committing": False, "reasons": []}


def test_executor_timeout_preserves_unknown_and_never_retries(tmp_path, monkeypatch):
    calls = []
    identities = []
    source = "CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER LOGIN;\n"

    def read_target(args):
        identities.append((args.pod, args.pod_uid))
        return {"fixture": "exact-identity"}

    def run(command, **kwargs):
        calls.append(command)
        assert "uid-guard" in command and binding()["pod_uid"] in command
        assert command[command.index("-U") + 1] == "postgres"
        if "pg_dumpall" in command:
            assert command[command.index("-l") + 1] == "postgres"
            return subprocess.CompletedProcess(command, 0, stdout=source.encode(), stderr=b"")
        if "-c" in command and "BEGIN READ ONLY" in command[-1]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b'{"session_user":"postgres","current_user":"postgres","bootstrap":{"oid":10,"name":"postgres","superuser":true}}',
                stderr=b"",
            )
        raise subprocess.TimeoutExpired(command, 180)

    monkeypatch.setattr(t.operator, "read_target", read_target)
    monkeypatch.setattr(t.subprocess, "run", run)
    path = tmp_path / "new-custody"
    with pytest.raises(ValueError, match="unknown outcome"):
        t.execute("\\set ON_ERROR_STOP on\nBEGIN;\nROLLBACK;\n", binding(), path, source)
    result = json.loads((path / "execution-result.json").read_text())
    assert result["outcome"] == "timeout-unknown" and result["rollback_not_inferred"] and result["no_retry"]
    assert len(calls) == 3 and len(identities) == 1
    assert (path / "custody-before.json").exists()


def test_native_profile_bootstrap_identity_guard_survives_matching_digest(private_pg, monkeypatch):
    q = private_pg
    q("ALTER ROLE c5_fixture RENAME TO postgres")
    qualify_install(q, monkeypatch, bootstrap_profile=True)
    assert set(startups(q).values()) == {"t"}
    q("ALTER ROLE postgres RENAME TO wrong_bootstrap")
    # Refresh measured fixture digests so identity failure cannot be attributed
    # to a stale expected catalog hash. Native OID10 still exists and is super.
    q(
        f"UPDATE {t.TABLE} SET boundary_sha256=CASE WHEN login_name='verdify_mcp_runtime_login' THEN fn_mcp_runtime_boundary_digest() ELSE fn_runtime_ordinary_boundary_digest(login_name) END"
    )
    assert set(startups(q).values()) == {"f"}


@pytest.mark.parametrize("private_pg", ["bootstrap_peer"], indirect=True)
def test_native_postgres_only_peer_bridge_preserves_owner_and_bootstrap(private_pg, monkeypatch):
    q = private_pg
    monkeypatch.setattr(t, "SERVER", int(q("SHOW server_version_num", user="postgres")))
    refused = q("SELECT current_user;", user="verdify", check=False)
    assert refused.returncode != 0 and "rejects connection" in refused.stderr
    before = q("SELECT " + t.bootstrap_facts_sql(), user="postgres")
    catalog_before = q(
        "SELECT jsonb_agg(jsonb_build_array(oid,rolname,rolsuper,rolinherit,rolcreatedb,rolcreaterole,rolcanlogin,rolreplication,rolbypassrls,rolconfig) ORDER BY oid) FROM pg_roles",
        user="postgres",
    )
    owner_sql = """\\set ON_ERROR_STOP on
BEGIN;
DO $identity$ BEGIN
 IF current_user<>'verdify' OR session_user<>'verdify' THEN RAISE EXCEPTION 'wrong owner DDL session'; END IF;
END $identity$;
CREATE TABLE public.peer_owner_probe(x integer);
SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.peer_owner_probe'::regclass;
ROLLBACK;
"""
    result = q(t.bootstrap_owner_sql(owner_sql), user="postgres")
    assert "verdify" in result.splitlines()
    assert q("SELECT to_regclass('public.peer_owner_probe') IS NULL;", user="postgres") == "t"
    assert q("SELECT " + t.bootstrap_facts_sql(), user="postgres") == before
    assert (
        q(
            "SELECT jsonb_agg(jsonb_build_array(oid,rolname,rolsuper,rolinherit,rolcreatedb,rolcreaterole,rolcanlogin,rolreplication,rolbypassrls,rolconfig) ORDER BY oid) FROM pg_roles",
            user="postgres",
        )
        == catalog_before
    )
    # Guard rejects a privileged peer connection to the wrong target context.
    wrong = q(t.bootstrap_owner_sql(owner_sql), user="postgres", database="postgres", check=False)
    assert wrong.returncode != 0 and "refuses privileged peer bootstrap" in wrong.stderr
    changed = owner_sql.replace("CREATE TABLE public.peer_owner_probe(x integer);", "ALTER ROLE postgres NOCREATEDB;")
    changed = changed.replace(
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.peer_owner_probe'::regclass;", ""
    )
    refused = q(t.bootstrap_owner_sql(changed), user="postgres", check=False)
    assert refused.returncode != 0 and "changed bootstrap/owner custody" in refused.stderr
    assert q("SELECT " + t.bootstrap_facts_sql(), user="postgres") == before


@pytest.mark.parametrize("terminal", ["ROLLBACK", "COMMIT"])
def test_native_refresh_custody_blocks_concurrent_refresh_until_terminal(private_pg, terminal):
    q = private_pg
    socket = q("SHOW unix_socket_directories")
    port = q("SHOW port")
    command = [
        str(Path(os.environ["CNPG_TEST_PG_BIN"]) / "psql"),
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        socket,
        "-p",
        port,
        "-U",
        "verdify",
        "-d",
        "verdify_rehearsal",
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DB_", "POSTGRES_"))}
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    try:
        process.stdin.write(
            "BEGIN; SET LOCAL lock_timeout='2s';\n"
            + t.refresh_custody_sql()
            + "\nSELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND relation IN"
            "('public.v_relay_stuck'::regclass,'public.v_climate_merged'::regclass)"
            " AND mode='AccessShareLock' AND granted;\n"
        )
        process.stdin.flush()
        assert process.stdout.readline().strip() == "2"
        for relation in ("v_relay_stuck", "v_climate_merged"):
            refused = q(f"SET lock_timeout='100ms'; REFRESH MATERIALIZED VIEW public.{relation};", check=False)
            assert refused.returncode != 0 and "lock timeout" in refused.stderr
        process.stdin.write(terminal + ";\n")
        process.stdin.flush()
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        for relation in ("v_relay_stuck", "v_climate_merged"):
            q(f"SET lock_timeout='100ms'; REFRESH MATERIALIZED VIEW public.{relation};")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize("replacement", ["missing", "table"])
def test_native_refresh_custody_refuses_wrong_fixed_objects(private_pg, replacement):
    q = private_pg
    q("DROP MATERIALIZED VIEW public.v_relay_stuck")
    if replacement == "table":
        q("CREATE TABLE public.v_relay_stuck(value integer)")
    refused = q("BEGIN; " + t.refresh_custody_sql() + " ROLLBACK;", check=False)
    assert refused.returncode != 0 and "refresh custody object shape" in refused.stderr


def test_refresh_custody_precedes_complete_literal_witness():
    _, target = witnesses()
    sql = t.emit_sql(target)
    assert sql.index("END $identity$;") < sql.index("DO $refresh_custody$") < sql.index("DO $native_transition$")
    assert "SET LOCAL lock_timeout='2s';" in sql
    assert "IF v_before IS DISTINCT FROM v_expected_before" in sql
    assert "CNPG native transition refuses raw existing/addition custody drift" in sql


def test_native_refresh_custody_acquisition_keeps_lock_timeout(private_pg):
    q = private_pg
    command = [
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
        "verdify",
        "-d",
        "verdify_rehearsal",
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DB_", "POSTGRES_"))}
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    try:
        process.stdin.write("BEGIN; REFRESH MATERIALIZED VIEW public.v_relay_stuck; SELECT 'refresh-held';\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "refresh-held"
        refused = q("BEGIN; SET LOCAL lock_timeout='100ms'; " + t.refresh_custody_sql() + " ROLLBACK;", check=False)
        assert refused.returncode != 0 and "lock timeout" in refused.stderr
        process.stdin.write("ROLLBACK;\n")
        process.stdin.flush()
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        q("BEGIN; " + t.refresh_custody_sql() + " ROLLBACK;")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
