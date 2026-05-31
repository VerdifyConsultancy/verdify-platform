"""#23 migration-safety guard.

Forbids NEW migrations in ``db/migrations/`` from *self-committing* under the
rollback harness.

Context
-------
Verdify validates a migration by replaying it inside an *outer* transaction that
is rolled back, e.g. ``make irrigation-migration-check``:

    BEGIN;
      <migration body>
    ROLLBACK;

If a migration contains its OWN top-level ``COMMIT;`` (or ``START TRANSACTION``,
or ``COMMIT`` via ``END;`` as a transaction-control alias), that statement closes
the outer transaction and **persists** the migration's effects on a database the
harness believed it could throw away. The subsequent ``ROLLBACK`` then has
nothing to undo. That is a "self-committing migration under the rollback
harness" and it makes the safety check a lie: a destructive or wrong migration
can leave permanent state even though the harness reported "replays cleanly in a
rollback transaction."

This is a pure static-text guard (no DB), so it runs in CI on every PR that
touches ``db/migrations/``.

Detection
---------
We strip comments, single-quoted string literals, and dollar-quoted bodies
(``$$ ... $$`` / ``$tag$ ... $tag$`` — where PL/pgSQL ``BEGIN``/``END``/``COMMIT``
keywords legitimately live and are NOT transaction control). What remains is
top-level SQL. A top-level ``COMMIT;`` or ``START TRANSACTION`` there is the
unambiguous self-commit signal we forbid.

We deliberately do NOT flag bare top-level ``END;`` outside dollar-quotes,
because ``END`` there is overwhelmingly a ``CASE ... END`` expression terminator
rather than a transaction-control ``END`` (a COMMIT alias). Every offending
migration in this repo uses an explicit ``COMMIT;``, so keying on ``COMMIT`` /
``START TRANSACTION`` catches the real risk without false positives.

Grandfathering
--------------
39 migrations authored before this guard already self-wrap in
``BEGIN; ... COMMIT;``. They have already been applied to production and are
frozen history — we cannot rewrite applied migrations. They are listed in
``GRANDFATHERED`` below. The guard fails if:

  * a NON-grandfathered migration self-commits (the forward-looking rule), or
  * a grandfathered entry no longer exists or no longer self-commits — in which
    case it must be REMOVED from the allowlist (keeps the list honest and
    shrinking, never silently stale).

New migrations must rely on the harness's outer transaction for atomicity and
must NOT include their own ``BEGIN``/``COMMIT``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

# Migrations authored before the #23 guard that already self-commit under the
# rollback harness. Frozen history — already applied to prod, cannot be
# rewritten. Do NOT add to this list; new self-committing migrations must fail.
GRANDFATHERED: frozenset[str] = frozenset(
    {
        "035-setpoint-rename.sql",
        "071-forecast-accuracy.sql",
        "079-heating-observability.sql",
        "080-observability-override-events-and-clamps.sql",
        "081-active-probe-count.sql",
        "082-obs3-relief-cycle-state.sql",
        "083-fw2-oscillation-metrics.sql",
        "084-unified-schema.sql",
        "085-topology-tables.sql",
        "086-topology-fks.sql",
        "087-topology-views.sql",
        "088-crop-history.sql",
        "089-crop-history-views.sql",
        "090-saas-greenhouse-id-gaps.sql",
        "091-fairness-counter.sql",
        "092-plan-delivery-log.sql",
        "093-planner-instance-audit.sql",
        "103-greenhouse-id-default-audit.sql",
        "104-daily-summary-measured-energy-refresh.sql",
        "106-public-health-ledger-calibration.sql",
        "107-public-contact-submissions.sql",
        "108-public-contact-notification-status.sql",
        "109-planner-trigger-ledger.sql",
        "110-planner-health-recovery-calibration.sql",
        "111-plan-execution-intervals.sql",
        "112-embeddings-unified.sql",
        "113-event-taxonomy-update.sql",
        "114-plan-delivery-hermes-run-id.sql",
        "115-shadow-tables.sql",
        "116-hermes-audit-horizon-embeddings.sql",
        "117-gpu-power-telemetry.sql",
        "118-inference-infra-telemetry.sql",
        "120-plan-transition-guardrail-audit.sql",
        "132-runtime-electric-cost.sql",
        "133-runtime-power-bucket-function.sql",
        "140-retire-shadow-controller-tables.sql",
        "148-plan-accuracy-repoint-plan-journal.sql",
        "149-compress-snapshot-open-alerts-zone-kpis.sql",
        "150-vanda-nutrient-recipe.sql",
    }
)

# Top-level transaction control that breaks an outer BEGIN..ROLLBACK harness.
_SELF_COMMIT_RE = re.compile(r"(?im)^\s*(COMMIT|START\s+TRANSACTION)\s*;")


def _strip_noise(sql: str) -> str:
    """Remove comments, string literals, and dollar-quoted bodies so only
    top-level (transaction-context) SQL keywords remain.

    Dollar-quoted bodies are where PL/pgSQL BEGIN/END/COMMIT keywords live; they
    are function/block syntax, not transaction control, and must be excluded.
    """
    # Line comments.
    sql = re.sub(r"--[^\n]*", "", sql)
    # Block comments.
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Dollar-quoted bodies: $$...$$ or $tag$...$tag$ (matched tag).
    sql = re.sub(r"\$(\w*)\$.*?\$\1\$", " ", sql, flags=re.DOTALL)
    # Single-quoted string literals ('' escapes a quote).
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def _self_commits(path: Path) -> bool:
    """True if the migration contains top-level COMMIT / START TRANSACTION
    outside dollar-quoted bodies — i.e. it self-commits under the harness."""
    return bool(_SELF_COMMIT_RE.search(_strip_noise(path.read_text())))


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def test_migrations_dir_exists() -> None:
    assert MIGRATIONS_DIR.is_dir(), f"missing migrations dir: {MIGRATIONS_DIR}"
    assert _migration_files(), "no migrations found — guard would be a no-op"


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_migration_does_not_self_commit_under_rollback_harness(path: Path) -> None:
    """A NEW migration must not self-commit under the BEGIN..ROLLBACK harness.

    Grandfathered (already-applied) migrations are exempt; everything else must
    rely on the harness's outer transaction for atomicity.
    """
    if path.name in GRANDFATHERED:
        pytest.skip(f"{path.name} is grandfathered (pre-#23, already applied)")
    assert not _self_commits(path), (
        f"{path.name} self-commits under the rollback harness: it contains a "
        f"top-level COMMIT; or START TRANSACTION outside a dollar-quoted body. "
        f"Such a migration closes the outer BEGIN..ROLLBACK transaction (e.g. "
        f"`make irrigation-migration-check`), persisting its effects on a DB the "
        f"harness expected to discard, so the safety check becomes a lie. Remove "
        f"the migration's own BEGIN/COMMIT and let the harness's outer "
        f"transaction provide atomicity."
    )


def test_grandfather_list_is_honest() -> None:
    """Every grandfathered entry must still exist and still self-commit.

    Keeps the allowlist shrinking and prevents it from masking a future
    regression. If an entry is renamed/removed or rewritten to no longer
    self-commit, drop it from GRANDFATHERED.
    """
    present = {p.name for p in _migration_files()}
    stale_missing = sorted(name for name in GRANDFATHERED if name not in present)
    assert not stale_missing, f"GRANDFATHERED lists migrations that no longer exist; remove them: {stale_missing}"
    no_longer_self_commits = sorted(name for name in GRANDFATHERED if not _self_commits(MIGRATIONS_DIR / name))
    assert not no_longer_self_commits, (
        "GRANDFATHERED lists migrations that no longer self-commit; remove them "
        f"so the allowlist stays honest: {no_longer_self_commits}"
    )
