"""Tests for the migration rollback-safety guard (#23).

Codifies the 2026-05-30 live-commit incident: a self-committing migration
chained under an outer ``BEGIN; ... ROLLBACK;`` dry-run defeats the rollback and
commits to live. These tests pin the guard's behavior so a future edit can't
silently re-open that hole.

No database, no network, no device — the guard only reads ``.sql`` files.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_PATH = REPO_ROOT / "scripts" / "check_migration_rollback_safety.py"
MIGRATIONS = REPO_ROOT / "db" / "migrations"
FIXTURES = MIGRATIONS / "fixtures"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_migration_rollback_safety", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve the module namespace.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


def _mig(name: str) -> Path:
    """Resolve a numbered migration by its numeric prefix (e.g. '149')."""
    matches = sorted(MIGRATIONS.glob(f"{name}-*.sql"))
    assert matches, f"no migration matching {name}-*.sql"
    return matches[0]


# ── Bad fixtures: the guard must REFUSE / FAIL them ──────────────────────────


def test_self_committing_fixture_is_flagged():
    c = guard.classify(FIXTURES / "_self_committing_bad.sql")
    assert c.self_committing is True
    assert any("COMMIT" in r for r in c.reasons)


def test_concurrently_fixture_is_flagged():
    c = guard.classify(FIXTURES / "_concurrently_bad.sql")
    assert c.self_committing is True
    assert any("CONCURRENTLY" in r for r in c.reasons)


def test_rollback_wrap_refuses_self_committing_fixture():
    rc = guard.cmd_rollback_wrap(str(FIXTURES / "_self_committing_bad.sql"))
    assert rc == 1


def test_rollback_wrap_refuses_concurrently_fixture():
    rc = guard.cmd_rollback_wrap(str(FIXTURES / "_concurrently_bad.sql"))
    assert rc == 1


def test_check_fails_on_bad_fixture():
    rc = guard.cmd_check([str(FIXTURES / "_self_committing_bad.sql")])
    assert rc == 1


# ── Real non-self-transactional migrations (146/147): SAFE to wrap ───────────


@pytest.mark.parametrize("num", ["146", "147"])
def test_non_self_transactional_migrations_are_safe_to_wrap(num):
    c = guard.classify(_mig(num))
    assert c.self_committing is False, f"{num} wrongly flagged: {c.reasons}"
    assert guard.cmd_rollback_wrap(str(_mig(num))) == 0


def test_146_with_string_literal_concurrently_is_not_flagged():
    # 146 contains 'REFRESH MATERIALIZED VIEW CONCURRENTLY ...' inside a COMMENT
    # ON ... IS '<string literal>'. That is neither a top-level statement nor a
    # commit-forcing one; the guard must not trip on string-literal text.
    c = guard.classify(_mig("146"))
    assert c.self_committing is False


# ── Real self-transactional migrations (149/150): own top-level COMMIT ───────


@pytest.mark.parametrize("num", ["149", "150"])
def test_self_transactional_migrations_are_refused(num):
    c = guard.classify(_mig(num))
    assert c.self_committing is True, f"{num} should be self-committing"
    assert "top-level COMMIT" in c.reasons
    assert guard.cmd_rollback_wrap(str(_mig(num))) == 1


# ── The issue's explicit acceptance: 151-155 must PASS the guard ─────────────


@pytest.mark.parametrize("num", ["151", "152", "153", "154", "155"])
def test_151_to_155_pass_the_guard(num):
    # These mention 'CREATE INDEX CONCURRENTLY' only inside `--` comments and have
    # no top-level COMMIT, so they are safe to wrap. The guard must NOT trip on
    # commit-forcing words that appear only in comments.
    c = guard.classify(_mig(num))
    assert c.self_committing is False, f"{num} wrongly flagged: {c.reasons}"


def test_check_passes_151_to_155_together():
    files = [str(_mig(n)) for n in ("151", "152", "153", "154", "155")]
    assert guard.cmd_check(files) == 0


def test_145_case_end_is_not_a_false_positive():
    # 145 line 95 is a top-level `CASE ... END;` expression terminator, not a
    # transaction-control END. The guard must treat 145 as safe-to-wrap.
    c = guard.classify(_mig("145"))
    assert c.self_committing is False, f"145 wrongly flagged: {c.reasons}"


# ── Comment / dollar-quote / string stripping unit coverage ──────────────────


def test_comment_only_concurrently_not_flagged(tmp_path):
    f = tmp_path / "x.sql"
    f.write_text(
        "-- This migration deliberately avoids CREATE INDEX CONCURRENTLY.\n"
        "/* and VACUUM is mentioned here too */\n"
        "CREATE TABLE t (id int);\n"
    )
    assert guard.classify(f).self_committing is False


def test_dollar_quoted_commit_not_flagged(tmp_path):
    # A COMMIT keyword inside a DO/function body is PL/pgSQL, not txn control.
    f = tmp_path / "x.sql"
    f.write_text("DO $$\nBEGIN\n  -- COMMIT;\n  PERFORM 1;\nEND\n$$;\n")
    assert guard.classify(f).self_committing is False


def test_string_literal_commit_not_flagged(tmp_path):
    f = tmp_path / "x.sql"
    f.write_text("INSERT INTO log (msg) VALUES ('we will COMMIT; later');\n")
    assert guard.classify(f).self_committing is False


def test_real_top_level_commit_is_flagged(tmp_path):
    f = tmp_path / "x.sql"
    f.write_text("BEGIN;\nCREATE TABLE t (id int);\nCOMMIT;\n")
    c = guard.classify(f)
    assert c.self_committing is True
    assert "top-level COMMIT" in c.reasons


def test_vacuum_top_level_is_flagged(tmp_path):
    f = tmp_path / "x.sql"
    f.write_text("VACUUM ANALYZE my_table;\n")
    c = guard.classify(f)
    assert c.self_committing is True
    assert "VACUUM" in c.reasons


def test_alter_system_is_flagged(tmp_path):
    f = tmp_path / "x.sql"
    f.write_text("ALTER SYSTEM SET work_mem = '64MB';\n")
    c = guard.classify(f)
    assert c.self_committing is True
    assert "ALTER SYSTEM" in c.reasons


def test_rollback_wrap_missing_file_returns_2(tmp_path):
    assert guard.cmd_rollback_wrap(str(tmp_path / "nope.sql")) == 2
