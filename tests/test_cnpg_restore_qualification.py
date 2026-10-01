"""Logical recovery must reject drift without rewriting original C0 custody."""

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c0 = load("cnpg-c0-restore-qualification")
roles = load("cnpg-restore-role-parity")
acl = load("cnpg-source-database-acl")
operator = load("cnpg-paired-restore")


def witnesses():
    source = {
        "version": c0.VERSION,
        "database": "verdify",
        "server": 160011,
        "roles": {"10": "postgres", "20": "verdify_mcp_runtime"},
        "database_owner": "verdify",
        "database_acl": [["PUBLIC", "verdify", "CONNECT", False], ["verdify", "verdify", "CREATE", False]],
        "portable_catalog": [["function", "vision.example()", "d" * 64]],
        "namespaces": {"2200": "public"},
        "ledger": [
            [
                "db/migrations",
                "db/migrations/268-six-runtime-workload-role-boundaries.sql",
                268,
                c0.MIGRATION_268_SHA,
                "runner",
            ]
        ],
        "boundaries": {},
    }
    for login in c0.boundary.LOGINS:
        body, _ = c0.ordinary_projection(login)
        sha = hashlib.sha256(body.encode()).hexdigest()
        entries = ["role|ordinary|login=t", "relation|public.example|acl=20:SELECT:false"]
        source["boundaries"][login] = {
            "installed_body_sha256": sha,
            "expected_body_sha256": sha,
            "installed_shape_verified": True,
            "raw_entries": entries,
            "semantic_entries": ["role|ordinary|login=t", "relation|public.example|acl=runtime:SELECT:false"],
        }
    body, _ = c0.mcp_projection()
    sha = hashlib.sha256(body.encode()).hexdigest()
    source["boundaries"]["verdify_mcp_runtime_login"] = {
        "installed_body_sha256": sha,
        "expected_body_sha256": sha,
        "installed_shape_verified": True,
        "raw_entries": ["relation|public|example|r|10|f|f||", "function|public.example(integer)|10|SELECT 10 + 20;"],
    }
    for data in source["boundaries"].values():
        data["native"] = hashlib.sha256("\n".join(sorted(data["raw_entries"])).encode()).hexdigest()
    source["seals"] = {
        "ordinary": [[login, source["boundaries"][login]["native"]] for login in c0.boundary.LOGINS],
        "mcp": [source["boundaries"]["verdify_mcp_runtime_login"]["native"]],
    }
    target = copy.deepcopy(source)
    target.update(
        database="verdify_rehearsal",
        server=160013,
        roles={"100": "postgres", "200": "verdify_mcp_runtime"},
        namespaces={"3000": "public"},
    )
    for login, data in target["boundaries"].items():
        if login in c0.boundary.LOGINS:
            data["raw_entries"][1] = "relation|public.example|acl=200:SELECT:false"
        else:
            data["raw_entries"] = [
                "relation|public|example|r|100|f|f||",
                "function|public.example(integer)|100|SELECT 10 + 20;",
            ]
        data["native"] = hashlib.sha256("\n".join(sorted(data["raw_entries"])).encode()).hexdigest()
    return source, target


def test_only_identity_translation_is_accepted_and_original_seals_remain():
    source, target = witnesses()
    before = copy.deepcopy((source, target))
    result = c0.compare(source, target)
    assert result["semantic_boundaries_equal"]
    assert result["runtime_transition_installed"] is False
    assert source["seals"] == target["seals"]
    assert (source, target) == before


@pytest.mark.parametrize("tamper", ["native", "seal", "ledger", "source-body", "posture", "acl", "body", "six-role"])
def test_catalog_receipt_ledger_and_implementation_drift_fail_closed(tamper):
    source, target = witnesses()
    login = c0.boundary.LOGINS[0]
    if tamper == "native":
        target["boundaries"][login]["native"] = "a" * 64
    elif tamper == "seal":
        target["seals"]["mcp"][0] = "b" * 64
    elif tamper == "ledger":
        target["ledger"][0][-1] = "fixture"
    elif tamper == "source-body":
        target["boundaries"][login]["installed_body_sha256"] = "c" * 64
        target["boundaries"][login]["expected_body_sha256"] = "c" * 64
    elif tamper == "posture":
        target["boundaries"][login]["installed_shape_verified"] = False
    elif tamper == "acl":
        target["boundaries"][login]["semantic_entries"][1] += ",PUBLIC:UPDATE:true"
    elif tamper == "six-role":
        target["portable_catalog"][0][2] = "e" * 64
    else:
        data = target["boundaries"]["verdify_mcp_runtime_login"]
        data["raw_entries"][1] = data["raw_entries"][1].replace("20;", "200;")
        data["native"] = hashlib.sha256("\n".join(sorted(data["raw_entries"])).encode()).hexdigest()
    with pytest.raises(ValueError):
        c0.compare(source, target)


def test_oid_mapping_is_field_typed_not_digit_replacement():
    mapped = c0.semantic_mcp("default-acl|20|2200|r|0|SELECT|f", {"20": "runtime"}, {"2200": "public"})
    assert mapped == "default-acl|runtime|public|r|PUBLIC|SELECT|f"
    with pytest.raises(ValueError, match="unmapped"):
        c0.semantic_mcp("function|example(integer)|999|SELECT 20;", {"20": "runtime"}, {})


def test_management_identity_collision_requires_exact_posture_and_memberships():
    common = "CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER LOGIN;\n"
    extra = "CREATE ROLE streaming_replica;\nALTER ROLE streaming_replica WITH REPLICATION LOGIN;\n"
    source = common + "CREATE ROLE ordinary;\nALTER ROLE ordinary WITH NOSUPERUSER LOGIN;\n"
    replay = roles.prepare(source, common + extra)
    assert "postgres" not in replay and "ordinary" in replay
    assert roles.verify(source, source + extra)["separately_enumerated_management_roles"] == ["streaming_replica"]
    with pytest.raises(ValueError):
        roles.prepare(source, common.replace("SUPERUSER", "NOSUPERUSER") + extra)
    with pytest.raises(ValueError):
        roles.prepare(source, common + "CREATE ROLE ordinary;\n")
    with pytest.raises(ValueError):
        roles.verify(source, source + extra + "GRANT ordinary TO streaming_replica;\n")
    with pytest.raises(ValueError):
        roles.prepare(source + "ALTER ROLE ordinary PASSWORD 'secret';\n", common)


def test_emitted_witness_is_read_only_and_not_receipt_authority():
    for target in (False, True):
        sql = c0.emit_sql(target)
        assert "REPEATABLE READ READ ONLY" in sql and "COMMIT;" in sql
        assert not any(word in sql for word in ["UPDATE public.", "INSERT INTO", "CREATE FUNCTION"])
        assert "pg_get_userbyid(acl.grantee)" in sql
        assert "current_setting('cluster_name') <> 'verdify-cnpg-rehearsal'" in sql if target else True


def test_database_acl_restore_is_witness_bound_and_disposable_only():
    source, _ = witnesses()
    source["roles"]["30"] = "verdify"
    sql = acl.emit_sql(source)
    assert "SET LOCAL ROLE verdify;" in sql
    assert "GRANT CONNECT ON DATABASE verdify_rehearsal TO PUBLIC;" in sql
    assert 'GRANT CREATE ON DATABASE verdify_rehearsal TO "verdify";' in sql
    assert "inet_client_addr() IS NOT NULL" in sql
    assert "ON DATABASE verdify TO" not in sql
    assert not any(text in sql for text in ["UPDATE public.", "INSERT INTO", "ALTER ROLE", "pg_authid"])
    for row in [
        ["PUBLIC", "postgres", "CONNECT", False],
        ["PUBLIC", "verdify", "DROP; SELECT 1", False],
        ["unknown", "verdify", "CONNECT", False],
        ["PUBLIC", "verdify", "CONNECT", 1],
    ]:
        bad = copy.deepcopy(source)
        bad["database_acl"] = [row]
        with pytest.raises(ValueError):
            acl.emit_sql(bad)


def test_operator_refuses_prod_namespace_replacement_uid_and_unadopted_image():
    cluster_uid, pod_uid = "a" * 36, "b" * 36
    cluster = {
        "metadata": {"name": operator.CLUSTER, "namespace": operator.NS, "uid": cluster_uid},
        "spec": {"imageName": "registry/operand@" + operator.DIGEST},
    }
    pod = {
        "metadata": {
            "namespace": operator.NS,
            "uid": pod_uid,
            "labels": {"cnpg.io/cluster": operator.CLUSTER},
            "ownerReferences": [{"kind": "Cluster", "uid": cluster_uid}],
        },
        "status": {
            "containerStatuses": [{"name": "postgres", "ready": True, "imageID": "registry/operand@" + operator.DIGEST}]
        },
    }
    operator.target_identity(cluster, pod, cluster_uid=cluster_uid, pod_uid=pod_uid)
    for key, value in [("namespace", "verdify-prod"), ("uid", "c" * 36)]:
        bad = copy.deepcopy(pod)
        bad["metadata"][key] = value
        with pytest.raises(ValueError):
            operator.target_identity(cluster, bad, cluster_uid=cluster_uid, pod_uid=pod_uid)
    bad = copy.deepcopy(pod)
    bad["status"]["containerStatuses"][0]["imageID"] = "registry/operand@sha256:" + "d" * 64
    with pytest.raises(ValueError):
        operator.target_identity(cluster, bad, cluster_uid=cluster_uid, pod_uid=pod_uid)


def test_full_identities_with_same_63_character_prefix_remain_distinct():
    source, target = witnesses()
    prefix = "column." + "x" * 60
    entries = [["column", prefix + suffix, digest * 64] for suffix, digest in [(".first", "a"), (".second", "b")]]
    assert entries[0][1][:63] == entries[1][1][:63]
    source["portable_catalog"] = copy.deepcopy(entries)
    target["portable_catalog"] = copy.deepcopy(entries)
    assert c0.compare(source, target)["semantic_boundaries_equal"]
    target["portable_catalog"][1][2] = "c" * 64
    with pytest.raises(ValueError, match="catalog drift"):
        c0.compare(source, target)


@pytest.mark.parametrize("mutation", ["duplicate", "duplicate-different-hash", "missing", "extra", "changed-identity"])
def test_catalog_multiplicity_and_full_identity_drift_are_never_deduplicated(mutation):
    source, target = witnesses()
    entries = [["column", "public.table.first", "a" * 64], ["column", "public.table.second", "b" * 64]]
    source["portable_catalog"] = copy.deepcopy(entries)
    target["portable_catalog"] = copy.deepcopy(entries)
    if mutation.startswith("duplicate"):
        target["portable_catalog"].append(copy.deepcopy(entries[0]))
        if mutation == "duplicate-different-hash":
            target["portable_catalog"][-1][2] = "c" * 64
    elif mutation == "missing":
        target["portable_catalog"].pop()
    elif mutation == "extra":
        target["portable_catalog"].append(["column", "public.table.third", "c" * 64])
    else:
        target["portable_catalog"][1][1] += "_changed"
    with pytest.raises(ValueError):
        c0.compare(source, target)


@pytest.mark.parametrize(
    "entry",
    [
        None,
        [],
        ["column", "public.x"],
        ["column", 17, "a" * 64],
        ["column", "", "a" * 64],
        ["column", "x\0y", "a" * 64],
        ["unknown", "public.x", "a" * 64],
        ["column", "public.x", "bad"],
    ],
)
def test_malformed_catalog_entries_fail_closed(entry):
    source, target = witnesses()
    target["portable_catalog"] = [entry]
    with pytest.raises(ValueError, match="malformed"):
        c0.compare(source, target)


def test_old_truncated_witness_version_cannot_qualify_even_if_arrays_match():
    source, target = witnesses()
    source["version"] = target["version"] = "cnpg-c0-logical-recovery-witness-v1"
    with pytest.raises(ValueError, match="unsupported witness"):
        c0.compare(source, target)


def test_exact_catalog_sql_retains_full_qualified_identities_in_private_pg():
    """Real UNION type resolution; its private socket never uses estate credentials."""
    import json
    import os
    import shutil
    import subprocess
    import tempfile

    bin_dir = os.environ.get("CNPG_TEST_PG_BIN")
    if not bin_dir:
        pytest.skip("set CNPG_TEST_PG_BIN for disposable PostgreSQL identity proof")
    pg = Path(bin_dir)
    cluster = Path(tempfile.mkdtemp(prefix="c5-pg-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["LC_ALL"] = "C"
    started = False

    def run(args, **kwargs):
        result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=60, **kwargs)
        assert result.returncode == 0, result.stderr  # Synthetic fixture only.
        return result.stdout.strip()

    def query(sql):
        return run(
            [
                str(pg / "psql"),
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                str(cluster),
                "-p",
                "55474",
                "-U",
                "c5_fixture",
                "-d",
                "postgres",
            ],
            input=sql,
        )

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
                f"-k {cluster} -c listen_addresses='' -p 55474",
                "-w",
                "start",
            ]
        )
        started = True
        schema = "s" * 50
        table = "t" * 40
        query(
            f'CREATE EXTENSION pgcrypto; CREATE SCHEMA "{schema}"; '
            f'CREATE TABLE "{schema}"."{table}" (first integer, second text);'
        )
        sql = c0.portable_catalog_sql()
        cte = sql.split(" SELECT jsonb_agg(jsonb_build_array(kind,identity,")[0]
        assert query(cte + " SELECT pg_typeof(identity)::text FROM objects LIMIT 1;") == "text"
        catalog = json.loads(query(sql))
        columns = [row for row in catalog if row[0] == "column" and row[1].startswith(schema + "." + table)]
        assert len(columns) == 2
        assert columns[0][1][:63] == columns[1][1][:63]
        assert columns[0][1] != columns[1][1] and all(len(row[1]) > 63 for row in columns)
        assert len({(row[0], row[1]) for row in catalog}) == len(catalog)
        assert query(sql) == query(sql)  # Deterministic aggregate ordering.
        old = cte.replace("SELECT 'schema',n.nspname::text,", "SELECT 'schema',n.nspname,")
        assert query(old + " SELECT pg_typeof(identity)::text FROM objects LIMIT 1;") == "name"
        assert (
            int(
                query(
                    old + " SELECT count(*) FROM (SELECT kind,identity FROM objects "
                    "GROUP BY kind,identity HAVING count(*)>1) collisions;"
                )
            )
            > 0
        )
    finally:
        if started:
            run([str(pg / "pg_ctl"), "-D", str(cluster / "data"), "-m", "fast", "-w", "stop"])
        shutil.rmtree(cluster)  # Only this fixture's generated cluster.
