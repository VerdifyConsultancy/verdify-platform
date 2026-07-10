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
)


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
