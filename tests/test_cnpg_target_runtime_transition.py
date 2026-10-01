"""Source-only target transition proofs; private PG fixture is synthetic.

Actual PG16.13/Timescale target qualification remains ROOT execution. This fixture
executes selected 217/259 attester bodies, with a catalog-sensitive digest stand-in;
it does not certify the complete production protected closure or restore data.
"""

import ast
import copy
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


@pytest.fixture
def private_pg():
    directory = os.environ.get("CNPG_TEST_PG_BIN")
    if not directory:
        pytest.skip("set CNPG_TEST_PG_BIN for private socket native attester proof")
    pg = Path(directory)
    cluster = Path(tempfile.mkdtemp(prefix="c5-native-pg-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DB_", "POSTGRES_"))}
    env["LC_ALL"] = "C"

    def run(args, **kwargs):
        r = subprocess.run(args, text=True, capture_output=True, env=env, timeout=60, **kwargs)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def query(sql, *, database="verdify_rehearsal", user="verdify", check=True):
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
                user,
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
                "c5_fixture",
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
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
            user="c5_fixture",
        )
        query(
            "CREATE EXTENSION pgcrypto; CREATE TABLE control_experiments(x integer); CREATE TABLE control_assignments(x integer); "
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
    installed = json.loads(q(t.emit_sql(before, reviewed_post=after, qualification_sha256="a" * 64)).splitlines()[-1])
    assert installed["mode"] == "install" and installed["post_witness"] == after
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
        if "pg_dumpall" in command:
            return subprocess.CompletedProcess(command, 0, stdout=source.encode(), stderr=b"")
        raise subprocess.TimeoutExpired(command, 180)

    monkeypatch.setattr(t.operator, "read_target", read_target)
    monkeypatch.setattr(t.subprocess, "run", run)
    path = tmp_path / "new-custody"
    with pytest.raises(ValueError, match="unknown outcome"):
        t.execute("generated-fixture-sql", binding(), path, source)
    result = json.loads((path / "execution-result.json").read_text())
    assert result["outcome"] == "timeout-unknown" and result["rollback_not_inferred"] and result["no_retry"]
    assert len(calls) == 2 and len(identities) == 1
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
