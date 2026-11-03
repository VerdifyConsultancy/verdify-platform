"""Every role setting a migration adds must be on the backup job's allowlist.

The nightly backup fails closed on any role setting it does not know, because a setting can hold a token. Migration
282 added the dashboard login's timeouts without updating it, and the job failed after a good dump on 2026-11-03.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALTER_ROLE_SET = re.compile(
    r"ALTER ROLE (\w+) SET (\w+)\s*(?:=|TO)\s*'([^']*)'\s*;",
    re.IGNORECASE,
)
ALLOWED_BLOCK = re.compile(r"rolname = '(\w+)'\)\s+AND setdatabase = 0\s+AND setting IN \(([^)]*)\)")


def migration_role_settings() -> set[tuple[str, str]]:
    found = set()
    for path in sorted((ROOT / "db/migrations").glob("*.sql")):
        for role, name, value in ALTER_ROLE_SET.findall(path.read_text()):
            if name.lower() != "search_path":
                found.add((role, f"{name.lower()}={value}"))
    return found


def backup_allowlist() -> set[tuple[str, str]]:
    text = (ROOT / "deploy/k8s/components/db-backup/backup-cronjob.yaml").read_text()
    allowed = set()
    for role, settings in ALLOWED_BLOCK.findall(text):
        allowed.update((role, item) for item in re.findall(r"'([^']*)'", settings))
    return allowed


def test_the_scan_sees_the_known_settings():
    assert ("verdify_dashboard_runtime_login", "statement_timeout=10s") in migration_role_settings()
    assert ("verdify_grafana_runtime_login", "statement_timeout=15s") in backup_allowlist()


def test_every_migrated_role_setting_is_on_the_backup_allowlist():
    missing = migration_role_settings() - backup_allowlist()
    assert not missing, (
        f"add these to backup-cronjob.yaml's unsafe_role_settings check, value-checked: {sorted(missing)}"
    )
