"""Deterministic executable coverage for the production DB-backup retry loop."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parents[1]
PROD_OVERLAY = REPO_ROOT / "deploy/k8s/overlays/prod"
FIXTURES = REPO_ROOT / "tests/fixtures/db_backup_retry"


def _backup_script() -> str:
    rendered = subprocess.run(
        ["kustomize", "build", str(PROD_OVERLAY)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [document for document in yaml.safe_load_all(rendered.stdout) if document]
    cronjob = next(
        document
        for document in documents
        if document.get("kind") == "CronJob" and document["metadata"]["name"] == "verdify-db-backup"
    )
    container = next(
        item
        for item in cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        if item["name"] == "pg-dump"
    )
    assert container["command"][:2] == ["/bin/bash", "-c"]
    return container["command"][2]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_backup(tmp_path: Path, fixture: str, *, succeed_on_attempt: int | None) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    attempts = tmp_path / "attempts"

    _write_executable(
        fake_bin / "pg_isready",
        "#!/bin/sh\nexit 1\n",
    )
    _write_executable(
        fake_bin / "sleep",
        "#!/bin/sh\nexit 0\n",
    )
    _write_executable(
        fake_bin / "pg_dump",
        """#!/bin/sh
set -eu
attempt=0
[ ! -f "$ATTEMPT_FILE" ] || attempt=$(cat "$ATTEMPT_FILE")
attempt=$((attempt + 1))
printf '%s' "$attempt" > "$ATTEMPT_FILE"
out=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-f" ]; then
    out=$2
    break
  fi
  shift
done
[ -n "$out" ] || exit 98
: > "$out"
if [ -n "${SUCCEED_ON_ATTEMPT:-}" ] && [ "$attempt" -eq "$SUCCEED_ON_ATTEMPT" ]; then
  printf 'deterministic-restorable-dump-fixture\n' > "$out"
  exit 0
fi
cat "$PG_DUMP_FIXTURE" >&2
exit 1
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ATTEMPT_FILE": str(attempts),
            "PG_DUMP_FIXTURE": str(FIXTURES / fixture),
            "BACKUP_DIR": str(backup_dir),
            "DB_HOST": "verdify-db",
            "DB_PORT": "5432",
            "DB_USER": "verdify",
            "DB_NAME": "verdify",
            "RETENTION_DAYS": "14",
        }
    )
    if succeed_on_attempt is not None:
        env["SUCCEED_ON_ATTEMPT"] = str(succeed_on_attempt)

    result = subprocess.run(
        ["/bin/bash", "-c", _backup_script()],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    result.attempts = int(attempts.read_text())  # type: ignore[attr-defined]
    result.backup_dir = backup_dir  # type: ignore[attr-defined]
    return result


def test_temporary_dns_failure_retries_then_atomically_publishes_dump(tmp_path: Path):
    script = _backup_script()
    assert "for attempt in $(seq 1 30)" in script

    result = _run_backup(tmp_path, "transient-dns-try-again.stderr", succeed_on_attempt=2)

    assert result.returncode == 0, result.stderr
    assert result.attempts == 2  # type: ignore[attr-defined]
    assert "DB temporarily unreachable" in result.stdout
    dumps = list(result.backup_dir.glob("verdify-*.dump"))  # type: ignore[attr-defined]
    assert len(dumps) == 1
    assert dumps[0].stat().st_size > 0
    assert not list(result.backup_dir.glob("*.partial"))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "fixture",
    [
        "non-transient-auth.stderr",
        "non-transient-permanent-dns.stderr",
    ],
)
def test_non_transient_failures_fail_loudly_without_retry(tmp_path: Path, fixture: str):
    result = _run_backup(tmp_path, fixture, succeed_on_attempt=None)

    assert result.returncode == 1
    assert result.attempts == 1  # type: ignore[attr-defined]
    assert "FATAL: pg_dump failed with a non-transient error" in result.stderr
