"""Docker-socket-less preflight + psql-verdify backend abstraction (#24).

These tests need NO database and NO docker socket. They prove that
scripts/lib/psql-verdify.sh resolves the DB connection prefix without any hard
dependency on `docker`, and that the A1 freeze-critical firmware-deploy preflight
runs on a Docker-socket-less runner (the hard prerequisite for OTA-on-runner and
the DB cutover).

Why this matters: the whole point of the abstraction is that the firmware freeze
gates keep existing when the DB moves in-cluster. If a socket-less runner could
only reach the DB via `docker exec`, the gates would silently no-op there. These
tests are the regression fence around that guarantee.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from conftest import _assert_read_only_sql, require_writable_fixture_dsn

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "scripts" / "lib" / "psql-verdify.sh"
PREFLIGHT = REPO_ROOT / "scripts" / "firmware-deploy-preflight.sh"

# Connection env the lib reads. Other test modules (test_05_ingestor,
# test_12_fidelity) os.environ.setdefault("DB_USER", "test") at import, which
# leaks into this process. Strip the whole set so these tests assert the lib's
# real defaults deterministically regardless of suite ordering.
_DB_ENV_KEYS = (
    "VERDIFY_DB_BACKEND",
    "VERDIFY_PSQL_MODE",
    "VERDIFY_DB_USER",
    "VERDIFY_DB_NAME",
    "VERDIFY_DB_CONTAINER",
    "VERDIFY_DOCKER_STDIN",
    "VERDIFY_DOCKER_TTY",
    "DB_USER",
    "DB_NAME",
    "DB_HOST",
    "DB_PORT",
    "DB_PASS",
    "POSTGRES_PASSWORD",
    "PGUSER",
    "PGDATABASE",
    "PGHOST",
    "PGPORT",
    "PGPASSWORD",
    "PGOPTIONS",
    "PGSERVICE",
    "PGSERVICEFILE",
)


@pytest.fixture(autouse=True)
def _clear_ambient_pg_service(monkeypatch):
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.delenv("PGSERVICEFILE", raising=False)


@pytest.mark.parametrize(
    "sql",
    (
        "DELETE FROM climate",
        "WITH removed AS (DELETE FROM climate RETURNING *) SELECT * FROM removed",
        "SELECT 1; UPDATE climate SET temp_f = 0",
        "SET default_transaction_read_only = off",
    ),
)
def test_live_db_helper_rejects_write_capable_sql(sql):
    with pytest.raises(ValueError):
        _assert_read_only_sql(sql)


@pytest.mark.parametrize("sql", ("SELECT 1", "WITH sample AS (SELECT 1) SELECT * FROM sample", "SHOW timezone"))
def test_live_db_helper_accepts_read_only_sql(sql):
    _assert_read_only_sql(sql)


def test_portable_and_ci_targets_ignore_ambient_pytest_opt_in_flags():
    makefile = (REPO_ROOT / "Makefile").read_text()
    ci = (REPO_ROOT / "scripts" / "ci-local.sh").read_text()

    assert "PORTABLE_TEST_MARKERS := not live and not device_live and not writable_fixture" in makefile
    for target in ("test", "test-fast", "test-v"):
        recipe = makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
        assert "PYTEST_ADDOPTS=" in recipe, f"{target} inherits ambient pytest opt-ins"
        assert '$(PYTEST) -m "$(PORTABLE_TEST_MARKERS)" tests/' in recipe, (
            f"{target} does not select the portable marker partition"
        )
    assert "PYTEST_ADDOPTS= PGOPTIONS= VERDIFY_PSQL_MODE=" in makefile
    assert "VERDIFY_INSECURE_TEST_TLS= VERDIFY_INGRESS_BASE_URL=" in makefile
    assert "VERDIFY_API_BASE_URL=https://api.verdify.ai" in makefile
    assert "VERDIFY_DB_BACKEND=kube" in makefile
    assert "-m live tests/ verdify_schemas/tests/" in makefile
    assert "VERDIFY_DB_NAMESPACE=verdify-prod VERDIFY_DB_POD=verdify-db-0" in makefile
    assert 'export PYTEST_ADDOPTS=""' in ci
    assert 'PORTABLE_MARKERS="not live and not device_live and not writable_fixture' in ci
    assert '$PY -m pytest -q -m "$PORTABLE_MARKERS"' in ci


def test_nested_planner_context_generator_enforces_read_only_connections():
    generator = (REPO_ROOT / "scripts" / "generate-ai-tunables-page.py").read_text()

    assert '"default_transaction_read_only": "on"' in generator
    assert '"statement_timeout": "30000"' in generator
    assert generator.count("asyncpg.connect(DB_DSN, server_settings=READ_ONLY_SERVER_SETTINGS)") == 2
    gather = (REPO_ROOT / "scripts" / "gather-plan-context.sh").read_text()
    assert gather.count("+=(-X -t -A)") == 2


def test_nested_plan_coverage_validator_enforces_read_only_connections():
    validator = (REPO_ROOT / "scripts" / "validate-plan-coverage.sh").read_text()

    assert 'verdify_psql_cmd -e "PGOPTIONS=-c statement_timeout=15000 -c default_transaction_read_only=on"' in validator
    assert "DB+=(-X -t -A -c)" in validator


@pytest.mark.parametrize(
    "host",
    (
        "verdify-db",
        "verdify-db.verdify-prod",
        "verdify-db.verdify-prod.svc.cluster.local",
        "verdify-db-0",
        "verdify-db-0.verdify-db.verdify-prod.svc.cluster.local",
        "verdify-db-cnpg-rw",
        "postgres.verdify-prod.svc.cluster.local",
    ),
)
def test_writable_fixture_dsn_rejects_every_known_production_host_shape(monkeypatch, host):
    monkeypatch.setenv("VERDIFY_WRITABLE_TEST_ACK", "disposable")
    monkeypatch.setenv("WRITABLE_DSN_TEST", f"postgresql://user:password@{host}:5432/verdify_test")

    with pytest.raises(RuntimeError, match="production database host"):
        require_writable_fixture_dsn("WRITABLE_DSN_TEST")


def test_writable_fixture_dsn_accepts_an_acknowledged_external_test_database(monkeypatch):
    dsn = "postgresql://user:password@ephemeral-postgres.test-fixtures.svc:5432/verdify_test"
    monkeypatch.setenv("VERDIFY_WRITABLE_TEST_ACK", "disposable")
    monkeypatch.setenv("WRITABLE_DSN_TEST", dsn)
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.delenv("PGSERVICEFILE", raising=False)

    assert require_writable_fixture_dsn("WRITABLE_DSN_TEST") == dsn


@pytest.mark.parametrize(
    ("dsn", "message"),
    (
        ("postgresql:///verdify_test", "explicit disposable database host"),
        ("postgresql:///verdify_test?host=verdify-db", "override connection routing"),
        ("postgresql://user:password@ephemeral,verdify-db:5432/verdify_test", "multi-host or failover"),
        ("postgresql://user:password@ephemeral:5432,verdify-db:5432/verdify_test", "multi-host or failover"),
        ("postgresql://user:password@ephemeral:5432/verdify_test?service=prod", "override connection routing"),
        ("postgresql://user:password@%2Fvar%2Frun%2Fpostgresql/verdify_test", "Unix-socket route"),
    ),
)
def test_writable_fixture_dsn_rejects_indirect_and_failover_routing(monkeypatch, dsn, message):
    monkeypatch.setenv("VERDIFY_WRITABLE_TEST_ACK", "disposable")
    monkeypatch.setenv("WRITABLE_DSN_TEST", dsn)

    with pytest.raises(RuntimeError, match=message):
        require_writable_fixture_dsn("WRITABLE_DSN_TEST")


@pytest.mark.parametrize(
    "encoded_host",
    (
        "verdify%2Ddb",
        "verdify%2Ddb%2D0",
        "verdify%2Ddb%2Everdify%2Dprod%2Esvc",
    ),
)
def test_writable_fixture_dsn_rejects_encoded_production_hosts(monkeypatch, encoded_host):
    monkeypatch.setenv("VERDIFY_WRITABLE_TEST_ACK", "disposable")
    monkeypatch.setenv(
        "WRITABLE_DSN_TEST",
        f"postgresql://user:password@{encoded_host}:5432/verdify_test",
    )

    with pytest.raises(RuntimeError, match="production database host"):
        require_writable_fixture_dsn("WRITABLE_DSN_TEST")


@pytest.mark.parametrize("service_key", ("PGSERVICE", "PGSERVICEFILE"))
def test_writable_fixture_dsn_rejects_ambient_service_routing(monkeypatch, service_key):
    monkeypatch.setenv("VERDIFY_WRITABLE_TEST_ACK", "disposable")
    monkeypatch.setenv(
        "WRITABLE_DSN_TEST",
        "postgresql://user:password@ephemeral-postgres.test-fixtures.svc:5432/verdify_test",
    )
    monkeypatch.setenv(service_key, "production-route")

    with pytest.raises(RuntimeError, match="ambient PostgreSQL service routing"):
        require_writable_fixture_dsn("WRITABLE_DSN_TEST")


def _clean_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of os.environ with all DB-connection knobs removed, plus overrides."""
    env = {k: v for k, v in os.environ.items() if k not in _DB_ENV_KEYS}
    if overrides:
        env.update(overrides)
    return env


def _source_and_run(snippet: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Source the lib in a fresh bash and run a snippet, returning the result."""
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"; {snippet}'],
        capture_output=True,
        text=True,
        timeout=15,
        env=_clean_env(env),
    )


# ── the lib exists and is sourceable ─────────────────────────────────────────


def test_lib_exists_and_sources_clean():
    assert LIB.exists(), f"missing {LIB}"
    res = _source_and_run("true")
    assert res.returncode == 0, res.stderr


def test_lib_has_no_bash_syntax_errors():
    res = subprocess.run(["bash", "-n", str(LIB)], capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, res.stderr


# ── VERDIFY_DB_BACKEND switch (issue #24 contract) ───────────────────────────


def test_default_backend_is_docker_exec_byte_identical():
    """Unset backend == docker == the exact prior VM argv."""
    res = _source_and_run("verdify_psql_cmd", env={})
    assert res.returncode == 0, res.stderr
    tokens = res.stdout.split()
    assert tokens == ["docker", "exec", "verdify-timescaledb", "psql", "-U", "verdify", "-d", "verdify"]


def test_backend_docker_explicit_matches_default():
    res = _source_and_run("verdify_psql_cmd", env={"VERDIFY_DB_BACKEND": "docker"})
    assert res.stdout.split() == ["docker", "exec", "verdify-timescaledb", "psql", "-U", "verdify", "-d", "verdify"]


def test_backend_dsn_emits_bare_psql_never_docker():
    """dsn backend must resolve to a bare `psql` — zero docker tokens."""
    res = _source_and_run("verdify_psql_cmd", env={"VERDIFY_DB_BACKEND": "dsn", "DB_HOST": "pg", "DB_PORT": "5432"})
    assert res.returncode == 0, res.stderr
    tokens = res.stdout.split()
    assert tokens == ["psql"], tokens
    assert "docker" not in tokens


def test_backend_dsn_exports_routing_before_mapfile_subshell_without_printing_password():
    res = _source_and_run(
        """
        mapfile -t cmd < <(verdify_psql_cmd)
        printf '%s\n' "$PGHOST" "$PGPORT" "$PGDATABASE" "$PGUSER" "${PGPASSWORD:+password-set}" "${cmd[@]}"
        """,
        env={
            "VERDIFY_DB_BACKEND": "dsn",
            "DB_HOST": "ephemeral-postgres.test-fixtures.svc",
            "DB_PORT": "5544",
            "DB_NAME": "verdify_test",
            "DB_USER": "fixture_user",
            "DB_PASS": "test-only-placeholder",
        },
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "ephemeral-postgres.test-fixtures.svc",
        "5544",
        "verdify_test",
        "fixture_user",
        "password-set",
        "psql",
    ]


def test_backend_dsn_pg_dump_emits_bare_program_never_docker():
    res = _source_and_run("verdify_pg_program_cmd pg_dump", env={"VERDIFY_DB_BACKEND": "dsn", "DB_HOST": "pg"})
    assert res.returncode == 0, res.stderr
    tokens = res.stdout.split()
    assert tokens == ["pg_dump"], tokens
    assert "docker" not in tokens


def test_backend_dsn_translates_pgoptions_to_process_env():
    """Direct psql must inherit PGOPTIONS even when argv is read via mapfile."""
    res = _source_and_run(
        'verdify_psql_cmd -e "PGOPTIONS=-c statement_timeout=15000"',
        env={"VERDIFY_DB_BACKEND": "dsn", "DB_HOST": "pg"},
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "env",
        "PGOPTIONS=-c statement_timeout=15000",
        "psql",
    ]


def test_backend_dsn_appends_caller_safety_after_ambient_pgoptions():
    res = _source_and_run(
        'verdify_psql_cmd -e "PGOPTIONS=-c default_transaction_read_only=on"',
        env={
            "VERDIFY_DB_BACKEND": "dsn",
            "DB_HOST": "pg",
            "PGOPTIONS": "-c default_transaction_read_only=off -c application_name=ambient",
        },
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "env",
        (
            "PGOPTIONS=-c default_transaction_read_only=off -c application_name=ambient "
            "-c default_transaction_read_only=on"
        ),
        "psql",
    ]


def test_pg_dump_docker_default_is_byte_identical():
    res = _source_and_run("verdify_pg_program_cmd pg_dump")
    assert res.stdout.split() == ["docker", "exec", "verdify-timescaledb", "pg_dump", "-U", "verdify", "-d", "verdify"]


def test_explicit_psql_mode_overrides_backend():
    """A call site that set VERDIFY_PSQL_MODE before this knob existed still wins."""
    res = _source_and_run(
        "verdify_psql_cmd",
        env={"VERDIFY_DB_BACKEND": "dsn", "VERDIFY_PSQL_MODE": "docker-exec"},
    )
    assert res.stdout.split()[0] == "docker"


def test_unknown_backend_warns_and_falls_back_to_docker():
    res = _source_and_run("verdify_psql_cmd", env={"VERDIFY_DB_BACKEND": "bogus"})
    assert res.stdout.split()[0] == "docker"
    assert "unknown VERDIFY_DB_BACKEND" in res.stderr


# ── kube-exec backend (#254 — re-home the firmware preflight DB handle) ───────


def test_backend_kube_emits_kubectl_exec_never_docker():
    """kube backend -> `kubectl exec -n verdify-prod verdify-db-0 -c postgres -- psql`."""
    res = _source_and_run("verdify_psql_cmd", env={"VERDIFY_DB_BACKEND": "kube"})
    assert res.returncode == 0, res.stderr
    tokens = res.stdout.split()
    assert tokens == [
        "kubectl",
        "exec",
        "-n",
        "verdify-prod",
        "verdify-db-0",
        "-c",
        "postgres",
        "--",
        "psql",
        "-U",
        "verdify",
        "-d",
        "verdify",
    ], tokens
    assert "docker" not in tokens


def test_backend_kube_multitoken_driver_is_word_split():
    """VERDIFY_KUBECTL may be a remote driver; it must be word-split, not one token."""
    res = _source_and_run(
        "verdify_psql_cmd",
        env={"VERDIFY_DB_BACKEND": "kube", "VERDIFY_KUBECTL": "ssh host sudo k3s kubectl"},
    )
    tokens = res.stdout.split()
    assert tokens[:6] == ["ssh", "host", "sudo", "k3s", "kubectl", "exec"], tokens


def test_backend_kube_translates_pgoptions_to_in_pod_env():
    """A `-e PGOPTIONS=...` docker-extra must survive as in-pod `env PGOPTIONS=...`."""
    res = _source_and_run(
        'verdify_psql_cmd -e "PGOPTIONS=-c statement_timeout=5000"',
        env={"VERDIFY_DB_BACKEND": "kube"},
    )
    out = res.stdout
    assert "\nenv\nPGOPTIONS=-c statement_timeout=5000\n" in out, out
    # env must sit immediately before psql (so psql inherits it in-pod).
    lines = out.splitlines()
    assert lines[lines.index("env") + 2] == "psql"


def test_backend_kube_custom_namespace_pod_container():
    res = _source_and_run(
        "verdify_psql_cmd",
        env={
            "VERDIFY_DB_BACKEND": "kube",
            "VERDIFY_DB_NAMESPACE": "verdify-dev",
            "VERDIFY_DB_POD": "verdify-db-9",
            "VERDIFY_DB_PGCONTAINER": "pg",
        },
    )
    tokens = res.stdout.split()
    assert tokens[:8] == ["kubectl", "exec", "-n", "verdify-dev", "verdify-db-9", "-c", "pg", "--"], tokens


def test_backend_kube_pg_dump_emits_kubectl_exec():
    res = _source_and_run("verdify_pg_program_cmd pg_dump", env={"VERDIFY_DB_BACKEND": "kube"})
    tokens = res.stdout.split()
    assert tokens == [
        "kubectl",
        "exec",
        "-n",
        "verdify-prod",
        "verdify-db-0",
        "-c",
        "postgres",
        "--",
        "pg_dump",
        "-U",
        "verdify",
        "-d",
        "verdify",
    ], tokens
    assert "docker" not in tokens


# ── the freeze-critical preflight is itself socket-less ──────────────────────


def test_preflight_sources_lib_and_has_no_literal_docker_exec_psql():
    """The preflight must not hardcode `docker exec ... psql` — it has to go
    through the abstraction so it is backend-switchable."""
    text = PREFLIGHT.read_text()
    assert "lib/psql-verdify.sh" in text
    assert "verdify_psql_cmd" in text
    # No raw docker-exec psql invocation left in the script body.
    assert "docker exec" not in text


def test_preflight_resolves_psql_not_docker_on_socketless_runner(tmp_path):
    """Run firmware-deploy-preflight.sh on a Docker-socket-less runner
    (VERDIFY_DB_BACKEND=dsn, `docker` removed from PATH, no live DB).

    We install a stub `psql` that exits non-zero (no DB to talk to). The preflight
    must FAIL on the psql/DB query — NOT on a missing `docker` binary. That proves
    the resolved command is `psql`, i.e. the freeze gate has no docker-socket
    dependency. A regression that reintroduces `docker exec` would instead fail
    with `docker: command not found`."""
    # Stub bin dir: a `psql` that records it was called and fails (no DB).
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    marker = tmp_path / "psql-was-called"
    psql_stub = stub_bin / "psql"
    psql_stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo called >> "{marker}"
            echo "psql: error: connection refused (socket-less test stub)" >&2
            exit 2
            """
        )
    )
    psql_stub.chmod(0o755)

    # Build a FULLY ISOLATED PATH (no /usr/bin:/bin) so the real `docker` cannot
    # leak in. Symlink only the core tools the preflight + lib actually invoke.
    core = tmp_path / "core"
    core.mkdir()
    needed = ("bash", "sh", "env", "date", "git", "stat", "tr", "awk", "mkdir", "cat", "printf", "dirname")
    for tool in needed:
        src = shutil.which(tool)
        if src and not (core / tool).exists():
            try:
                (core / tool).symlink_to(src)
            except OSError:
                pass
    socketless_path = f"{stub_bin}:{core}"
    assert shutil.which("docker", path=socketless_path) is None, "docker must be absent from the socket-less PATH"
    assert shutil.which("psql", path=socketless_path) == str(psql_stub), "only the stub psql should be on PATH"

    env = _clean_env(
        {
            "PATH": socketless_path,
            "VERDIFY_DB_BACKEND": "dsn",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "ALLOW_FIRMWARE_DEPLOY_GUARD_OVERRIDE": "0",
        }
    )

    res = subprocess.run(
        ["bash", str(PREFLIGHT)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
        env=env,
    )

    combined = res.stdout + res.stderr
    # The script must have tried to reach the DB via our psql stub...
    assert marker.exists(), f"psql stub was never invoked; preflight did not resolve to psql.\n{combined}"
    # ...and it must NOT have died because `docker` was missing.
    assert "docker: command not found" not in combined, combined
    assert "docker: not found" not in combined, combined
    # It does fail (no real DB), but on the DB query path, not on docker.
    assert res.returncode != 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
