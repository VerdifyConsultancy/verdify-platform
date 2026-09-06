"""Source-bound observation after a real synthetic dump/restore; never approval."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from test_c0_migration_delivery import delivery
from test_c0_migration_delivery import isolated_pg as isolated_pg
from test_c0_migration_delivery import owning as owning
from test_c0_release_rehearsal import snapshot

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-restore-rehearsal"


def scripts():
    job = yaml.safe_load((COMPONENT / "restore-rehearsal-job.yaml").read_text())
    init = job["spec"]["template"]["spec"]["initContainers"][0]["args"][0]
    main = yaml.safe_load((COMPONENT / "restore-rehearsal-script.yaml").read_text())["data"]["rehearse.sh"]
    return init, main


def section(source, name):
    return source.split("# BEGIN " + name, 1)[1].split("# END " + name, 1)[0]


def relocate(source, paths):
    # Relocate only explicit image filesystem roots, never SQL/control flow.
    return re.sub(r"(?<![A-Za-z0-9_/])/(work|scripts|db|tmp)(?=/|[\"\s])", lambda m: str(paths[m[1]]), source)


@pytest.fixture
def prepared(tmp_path):
    work = tmp_path / "work"
    (work / "db/migrations").mkdir(parents=True)
    for name in delivery.transition.MIGRATIONS:
        shutil.copyfile(ROOT / "db/migrations" / name, work / "db/migrations" / name)
    output = tmp_path / "private-output"
    output.mkdir()
    paths = {"work": work, "db": ROOT / "db", "scripts": ROOT / "scripts", "tmp": output}
    init, main = scripts()
    prep = relocate(section(init, "C0_INSPECTION_PREPARE"), paths)
    result = subprocess.run(["sh", "-ec", prep], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return paths, relocate(section(main, "C0_CATALOG_INSPECTION"), paths)


def test_prepared_sql_is_exact_read_only_source_and_checksum_bound(prepared):
    paths, _ = prepared
    directory = paths["work"] / "c0-inspection"
    for login in delivery.transition.boundary.LOGINS:
        sql = (directory / f"{login}.sql").read_text()
        assert sql == delivery.transition.boundary.emit_sql(login)
        assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" in sql
        assert "CREATE OR REPLACE" not in sql and "UPDATE public." not in sql
    result = subprocess.run(["sha256sum", "-c", "sql.sha256"], cwd=directory, capture_output=True, text=True)
    assert result.returncode == 0


def test_source_preparation_and_main_shell_parse():
    for source in scripts():
        result = subprocess.run(["bash", "-n"], input=source, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    main = scripts()[1]
    inspect_start = main.index("# BEGIN C0_CATALOG_INSPECTION")
    assert main.index("timescaledb_post_restore()") < inspect_start < main.index("candidate_object_snapshot()")
    assert "shared_preload_libraries=timescaledb" in main
    assert "timescaledb.telemetry_level=off" in main


def test_changed_sql_refuses_before_database_process(prepared):
    paths, script = prepared
    sql = paths["work"] / "c0-inspection/verdify_api_runtime_login.sql"
    with sql.open("a") as stream:
        stream.write("\nSELECT 'unreviewed';\n")
    result = subprocess.run(["bash", "-ec", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 1 and "missing or changed" in result.stderr
    assert "C0_CATALOG_BEGIN" not in result.stdout


def test_database_error_content_is_withheld(prepared, tmp_path):
    paths, script = prepared
    bindir = tmp_path / "bin"
    bindir.mkdir()
    client = bindir / "psql"
    client.write_text("#!/bin/sh\necho 'sensitive-database-error-canary' >&2\nexit 3\n")
    client.chmod(0o755)
    result = subprocess.run(
        ["bash", "-ec", script],
        env=dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", PGDATABASE="fixture"),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1 and "raw database output withheld" in result.stderr
    assert "sensitive-database-error-canary" not in result.stdout + result.stderr
    assert (paths["tmp"] / "c0-inspection.stderr").read_text().strip() == "sensitive-database-error-canary"


def test_restore_error_sql_is_not_published(tmp_path):
    paths = {name: tmp_path / name for name in ("work", "scripts", "db", "tmp")}
    paths["tmp"].mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    client = bindir / "pg_restore"
    client.write_text("#!/bin/sh\necho 'sensitive-restore-sql-canary' >&2\nexit 1\n")
    client.chmod(0o755)
    script = relocate(section(scripts()[1], "RESTORE_ERROR_REDACTION"), paths)
    result = subprocess.run(
        ["bash", "-ec", script],
        env=dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", PGDATABASE="fixture", dump_path="fixture.dump"),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1 and "pg_restore failed" in result.stderr
    assert "sensitive-restore-sql-canary" not in result.stdout + result.stderr


@pytest.mark.parametrize("resource_only", [False, True])
def test_real_dump_restore_inspection_retains_stale_receipts_without_approval(
    owning, prepared, tmp_path, resource_only
):
    source_query, _, _, env, _ = owning
    paths, inspect_script = prepared
    if resource_only:
        # Narrow only this disposable copied inventory, never the repo sources.
        directory = paths["work"] / "db/migrations"
        for name in delivery.transition.MIGRATIONS:
            (directory / name).unlink()
        name = "248-shelly-source-interval-accounting.sql"
        shutil.copyfile(ROOT / "db/migrations" / name, directory / name)
    pg_bin = Path(os.environ["SCORECARD_TEST_PG_BIN"])
    source_env = dict(env, PGHOST=env["DB_HOST"], PGPORT="55472", PGUSER="scorecard_fixture", PGDATABASE="postgres")
    dump = tmp_path / "synthetic.dump"
    result = subprocess.run(
        [str(pg_bin / "pg_dump"), "-Fc", "-f", str(dump)], env=source_env, text=True, capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    dump_sha = hashlib.sha256(dump.read_bytes()).hexdigest()
    source_query("CREATE DATABASE fixture_rehearsal OWNER scorecard_fixture")
    target_env = dict(source_env, PGDATABASE="fixture_rehearsal")

    def target(sql):
        result = subprocess.run(
            [str(pg_bin / "psql"), "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
            input=sql,
            env=target_env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    target("CREATE EXTENSION timescaledb; SELECT timescaledb_pre_restore();")
    toc = subprocess.run(
        [str(pg_bin / "pg_restore"), "-l", str(dump)], text=True, capture_output=True, check=True
    ).stdout
    filtered = tmp_path / "restore.toc"
    filtered.write_text("\n".join(line for line in toc.splitlines() if "MATERIALIZED VIEW DATA" not in line) + "\n")
    result = subprocess.run(
        [
            str(pg_bin / "pg_restore"),
            "--exit-on-error",
            "--use-list",
            str(filtered),
            "--dbname=fixture_rehearsal",
            str(dump),
        ],
        env=target_env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    target(
        "SELECT timescaledb_post_restore(); SET search_path=pg_catalog,public,pg_temp; REFRESH MATERIALIZED VIEW public.mv_daily_kpi;"
    )
    receipt_sql = (
        "SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM public.runtime_ordinary_login_attestation_receipts r"
    )
    assert target(receipt_sql) == source_query(receipt_sql)
    before = snapshot(target), target(receipt_sql)
    result = subprocess.run(
        ["bash", "-ec", "umask 077\n" + inspect_script + "\necho UNREACHABLE_LEGACY_APPLY"],
        env=target_env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 1 and "HOLD: restored catalog inspection complete" in result.stderr
    assert "UNREACHABLE_LEGACY_APPLY" not in result.stdout
    assert "transition_authorized=false receipt_refresh_allowed=false" in result.stdout
    for login in delivery.transition.boundary.LOGINS:
        data = json.loads((paths["tmp"] / f"{login}.catalog.json").read_text())
        assert data["installed_source_verified"] is True
        assert data["projection_matches_installed_digest"] is True
        assert data["database"] == "fixture_rehearsal"
        assert data["stored_digest"] != data["current_digest"]
        delivery.transition.boundary.validate(data)
    assert (snapshot(target), target(receipt_sql)) == before
    assert hashlib.sha256(dump.read_bytes()).hexdigest() == dump_sha


def test_resource_only_inspection_preparation_does_not_skip_source_checks(tmp_path):
    paths = {name: tmp_path / name for name in ("work", "scripts", "db", "tmp")}
    (paths["db"] / "migrations").mkdir(parents=True)
    name = "248-shelly-source-interval-accounting.sql"
    shutil.copyfile(ROOT / "db/migrations" / name, paths["db"] / "migrations" / name)
    # No packaged transition helper: detecting 248 must try the checked-source
    # path and fail, never report successful preparation with no inspection SQL.
    prep = relocate(section(scripts()[0], "C0_INSPECTION_PREPARE"), paths)
    result = subprocess.run(["sh", "-ec", prep], text=True, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert not (paths["work"] / "c0-inspection").exists()


def test_component_stays_inactive_and_can_render_without_secret_resources(tmp_path):
    prod = yaml.safe_load((ROOT / "deploy/k8s/overlays/prod/kustomization.yaml").read_text())
    assert "../../components/experiment-v2-restore-rehearsal" not in prod["components"]
    pin = next(image for image in prod["images"] if image["name"] == "ghcr.io/verdifyconsultancy/verdify-migrate")
    config = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": "fixture-rehearsal",
        "components": [os.path.relpath(COMPONENT, tmp_path)],
        "images": [pin],
    }
    (tmp_path / "kustomization.yaml").write_text(yaml.safe_dump(config))
    result = subprocess.run(
        ["kustomize", "build", str(tmp_path), "--load-restrictor", "LoadRestrictionsNone"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    assert {doc["kind"] for doc in documents} == {"Job", "ConfigMap", "NetworkPolicy"}
    job = next(doc for doc in documents if doc["kind"] == "Job")
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    candidate_image = job["spec"]["template"]["spec"]["initContainers"][0]["image"]
    assert candidate_image == f"{pin['newName']}@{pin['digest']}"
    assert candidate_image.startswith("registry.vallery.net/")
    policy = next(doc for doc in documents if doc["kind"] == "NetworkPolicy")
    assert policy["spec"]["ingress"] == policy["spec"]["egress"] == []
