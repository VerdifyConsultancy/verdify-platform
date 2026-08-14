"""db/apply-migrations.sh (ledgered migration runner, #583) behavior tests.

No live database: a stub `psql` on PATH answers the runner's control queries
and logs every invocation, so we can verify plan mode, the baseline guard,
sha computation, duplicate-number tolerance, and the edited-in-place fatal.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "db" / "apply-migrations.sh"

STUB_PSQL = r"""#!/bin/sh
LOG="${STUB_PSQL_LOG:?}"
sql=""
apply=""
next=""
for a in "$@"; do
  if [ "$next" = "c" ]; then sql="$a"; next=""; continue; fi
  if [ "$next" = "f" ]; then apply="$a"; next=""; continue; fi
  case "$a" in
    -c) next=c ;;
    -f) next=f ;;
  esac
done
if [ -n "$apply" ]; then
  echo "APPLY $apply" >> "$LOG"
  exit 0
fi
echo "QUERY $sql" >> "$LOG"
case "$sql" in
  *"to_regclass('public.schema_migrations')"*) echo "${STUB_HAVE_LEDGER:-t}" ;;
  *"to_regclass('public.climate')"*) echo "${STUB_POPULATED:-t}" ;;
  *"count(*) FROM schema_migrations"*) echo "${STUB_LEDGER_COUNT:-0}" ;;
  *"COALESCE(sha256"*)
    fn=$(printf '%s' "$sql" | sed -n "s/.*filename = '\([^']*\)'.*/\1/p")
    if [ -n "${STUB_LEDGER_SHA_FILE:-}" ] && [ -f "$STUB_LEDGER_SHA_FILE" ]; then
      awk -v f="$fn" '$1 == f {print $2}' "$STUB_LEDGER_SHA_FILE"
    fi
    ;;
  *"SELECT 1"*) echo 1 ;;
esac
exit 0
"""


@pytest.fixture()
def runner_env(tmp_path):
    """Fixture migrations dir (with historical duplicate numbers) + stub psql."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    # Duplicate leading number, distinct filenames — the historical 070 shape.
    (migrations / "070-plan-accuracy-72h.sql").write_text(
        "-- fixture A\nCREATE TABLE IF NOT EXISTS fixture_a (id int);\n"
    )
    (migrations / "070-plan-accuracy-by-day.sql").write_text(
        "-- fixture B\nCREATE TABLE IF NOT EXISTS fixture_b (id int);\n"
    )
    (migrations / "095a-suffixed.sql").write_text("-- fixture C\nCREATE TABLE IF NOT EXISTS fixture_c (id int);\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "psql"
    stub.write_text(STUB_PSQL)
    stub.chmod(0o755)

    log = tmp_path / "psql.log"
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "DB_HOST": "stub",
            "DB_NAME": "stub",
            "DB_USER": "stub",
            "DB_PASS": "stub",
            "VERDIFY_MIGRATIONS_DIR": str(migrations),
            "STUB_PSQL_LOG": str(log),
        }
    )
    return {"env": env, "migrations": migrations, "log": log, "tmp": tmp_path}


def _run(runner_env, *args, **extra_env):
    env = dict(runner_env["env"])
    env.update({k: str(v) for k, v in extra_env.items()})
    return subprocess.run(
        ["sh", str(RUNNER), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_plan_mode_lists_pending_with_shas_and_applies_nothing(runner_env):
    res = _run(
        runner_env,
        "--plan",
        STUB_HAVE_LEDGER="t",
        STUB_POPULATED="t",
        STUB_LEDGER_COUNT="5",
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout
    # Duplicate-numbered files are both individually pending (identity =
    # filename + sha, never the leading number).
    for name in (
        "070-plan-accuracy-72h.sql",
        "070-plan-accuracy-by-day.sql",
        "095a-suffixed.sql",
    ):
        sha = _sha(runner_env["migrations"] / name)
        assert f"pending: {name} sha256={sha}" in out, name
    assert "plan: 3 pending" in out
    assert "PLAN complete — nothing applied." in out
    # The stub logged zero -f applications.
    assert "APPLY" not in runner_env["log"].read_text()


def test_plan_orders_duplicates_by_filename(runner_env):
    res = _run(
        runner_env,
        "--plan",
        STUB_HAVE_LEDGER="t",
        STUB_POPULATED="t",
        STUB_LEDGER_COUNT="5",
    )
    out = res.stdout
    assert out.index("070-plan-accuracy-72h.sql") < out.index("070-plan-accuracy-by-day.sql")
    assert out.index("070-plan-accuracy-by-day.sql") < out.index("095a-suffixed.sql")


def test_ledgered_files_with_matching_sha_are_skipped(runner_env):
    shafile = runner_env["tmp"] / "ledger-shas.txt"
    known = runner_env["migrations"] / "070-plan-accuracy-72h.sql"
    shafile.write_text(f"db/migrations/070-plan-accuracy-72h.sql {_sha(known)}\n")
    res = _run(
        runner_env,
        "--plan",
        STUB_HAVE_LEDGER="t",
        STUB_POPULATED="t",
        STUB_LEDGER_COUNT="1",
        STUB_LEDGER_SHA_FILE=shafile,
    )
    assert res.returncode == 0, res.stderr
    assert "plan: 2 pending, 1 already ledgered" in res.stdout


def test_edited_in_place_migration_is_fatal(runner_env):
    shafile = runner_env["tmp"] / "ledger-shas.txt"
    shafile.write_text("db/migrations/070-plan-accuracy-72h.sql " + "0" * 64 + "\n")
    res = _run(
        runner_env,
        "--plan",
        STUB_HAVE_LEDGER="t",
        STUB_POPULATED="t",
        STUB_LEDGER_COUNT="1",
        STUB_LEDGER_SHA_FILE=shafile,
    )
    assert res.returncode != 0
    assert "edited after it was applied" in res.stderr


def test_populated_db_with_empty_ledger_refuses_without_baseline_flag(runner_env):
    res = _run(
        runner_env,
        STUB_HAVE_LEDGER="t",
        STUB_POPULATED="t",
        STUB_LEDGER_COUNT="0",
    )
    assert res.returncode != 0
    assert "VERDIFY_MIGRATE_ALLOW_BASELINE=1" in res.stderr
    # Nothing from the migrations dir was applied.
    log = runner_env["log"].read_text()
    assert "070-plan-accuracy" not in log


def test_plan_mode_surfaces_baseline_requirement_without_failing(runner_env):
    res = _run(
        runner_env,
        "--plan",
        STUB_HAVE_LEDGER="t",
        STUB_POPULATED="t",
        STUB_LEDGER_COUNT="0",
    )
    assert res.returncode == 0, res.stderr
    assert "VERDIFY_MIGRATE_ALLOW_BASELINE=1" in res.stdout
    assert "PLAN complete — nothing applied." in res.stdout
    assert "APPLY" not in runner_env["log"].read_text()


def test_fresh_db_plan_treats_everything_pending_without_baseline_gate(runner_env):
    res = _run(
        runner_env,
        "--plan",
        STUB_HAVE_LEDGER="f",
        STUB_POPULATED="f",
        STUB_LEDGER_COUNT="0",
    )
    assert res.returncode == 0, res.stderr
    assert "would bootstrap schema_migrations ledger" in res.stdout
    assert "plan: 3 pending" in res.stdout
    assert "ALLOW_BASELINE" not in res.stdout
