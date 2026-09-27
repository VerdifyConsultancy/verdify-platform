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


def _run_backup(
    tmp_path: Path,
    fixture: str,
    *,
    succeed_on_attempt: int | None,
    unsafe_role_settings: bool = False,
    role_export_has_password: bool = False,
    role_export_fails: bool = False,
) -> subprocess.CompletedProcess[str]:
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
    _write_executable(
        fake_bin / "psql",
        """#!/bin/sh
case "$*" in
  *pg_db_role_setting*)
    printf '%s\\n' "${UNSAFE_ROLE_SETTINGS:-0}" ;;
  *pg_get_userbyid*)
    printf 'verdify\\n' ;;
  *) exit 98 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "pg_dumpall",
        """#!/bin/sh
set -eu
for required in --roles-only --no-role-passwords --no-comments --no-security-labels; do
  case " $* " in
    *" $required "*) ;;
    *) exit 97 ;;
  esac
done
printf 'CREATE ROLE verdify;\\nGRANT duty TO login;\\n'
if [ "${ROLE_EXPORT_FAILS:-0}" = 1 ]; then
  exit 1
fi
if [ "${ROLE_EXPORT_HAS_PASSWORD:-0}" = 1 ]; then
  printf "ALTER ROLE verdify PASSWORD 'should-never-publish';\\n"
fi
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
            "UNSAFE_ROLE_SETTINGS": "1" if unsafe_role_settings else "0",
            "ROLE_EXPORT_HAS_PASSWORD": "1" if role_export_has_password else "0",
            "ROLE_EXPORT_FAILS": "1" if role_export_fails else "0",
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


def test_temporary_dns_failure_retries_then_publishes_verified_pair(tmp_path: Path):
    script = _backup_script()
    assert "for attempt in $(seq 1 30)" in script

    result = _run_backup(tmp_path, "transient-dns-try-again.stderr", succeed_on_attempt=2)

    assert result.returncode == 0, result.stderr
    assert result.attempts == 2  # type: ignore[attr-defined]
    assert "DB temporarily unreachable" in result.stdout
    dumps = list(result.backup_dir.glob("verdify-*.dump"))  # type: ignore[attr-defined]
    assert len(dumps) == 1
    assert dumps[0].stat().st_size > 0
    roles = dumps[0].with_suffix(".roles.sql")
    manifest = dumps[0].with_suffix(".sha256")
    assert roles.stat().st_size > 0
    assert "password" not in roles.read_text().lower()
    assert manifest.read_text().startswith("# verdify-backup-pair-v1 database=verdify owner=verdify\n")
    verified = subprocess.run(
        ["sha256sum", "-c", manifest.name],
        cwd=manifest.parent,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    pair_check = subprocess.run(
        [str(REPO_ROOT / "scripts/verify-backup-pair.sh"), str(manifest.parent), dumps[0].stem],
        capture_output=True,
        text=True,
    )
    assert pair_check.returncode == 0, pair_check.stderr
    assert pair_check.stdout.strip() == "verdify|verdify"
    roles.write_text(roles.read_text() + "-- changed after publication\n")
    tampered = subprocess.run(
        [str(REPO_ROOT / "scripts/verify-backup-pair.sh"), str(manifest.parent), dumps[0].stem],
        capture_output=True,
        text=True,
    )
    assert tampered.returncode == 1
    assert "checksum mismatch" in tampered.stderr
    assert not list(result.backup_dir.glob("*.partial"))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "fixture",
    [
        "non-transient-auth.stderr",
        "non-transient-permanent-dns.stderr",
        "non-transient-permission.stderr",
        "non-transient-disk-full.stderr",
    ],
)
def test_non_transient_failures_fail_loudly_without_retry(tmp_path: Path, fixture: str):
    result = _run_backup(tmp_path, fixture, succeed_on_attempt=None)

    assert result.returncode == 1
    assert result.attempts == 1  # type: ignore[attr-defined]
    assert "FATAL: pg_dump failed with a non-transient error" in result.stderr
    assert not list(result.backup_dir.glob("verdify-*"))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("unsafe_settings", "password_clause", "expected_error"),
    [
        (True, False, "unapproved role setting"),
        (False, True, "role export contains a password clause"),
    ],
)
def test_secret_bearing_roles_fail_without_publishing_pair(
    tmp_path: Path,
    unsafe_settings: bool,
    password_clause: bool,
    expected_error: str,
):
    result = _run_backup(
        tmp_path,
        "transient-dns-try-again.stderr",
        succeed_on_attempt=1,
        unsafe_role_settings=unsafe_settings,
        role_export_has_password=password_clause,
    )

    assert result.returncode == 1
    assert expected_error in result.stderr
    assert not list(result.backup_dir.glob("verdify-*"))  # type: ignore[attr-defined]


def test_role_export_failure_does_not_publish_the_completed_dump(tmp_path: Path):
    result = _run_backup(
        tmp_path,
        "transient-dns-try-again.stderr",
        succeed_on_attempt=1,
        role_export_fails=True,
    )

    assert result.returncode == 1
    assert "role export failed; pair not published" in result.stderr
    assert not list(result.backup_dir.glob("verdify-*"))  # type: ignore[attr-defined]
