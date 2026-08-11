#!/usr/bin/env python3
"""Migration rollback-safety guard (#23).

Codifies the 2026-05-30 live-commit incident: a *self-committing* migration
chained under an outer ``BEGIN; ... ROLLBACK;`` dry-run defeats the rollback and
commits to the live database. The distinction was documented in prose only
(``docs/runbooks/backlog-closeout-deploy-2026-05-30.md``):

  * 146 / 147 are NON-self-transactional (DO-block ``BEGIN``s only, no top-level
    ``COMMIT``) and are SAFE to rollback-validate by wrapping in an outer
    ``BEGIN; ... ROLLBACK;``.
  * 149 / 150 are SELF-transactional (own top-level ``BEGIN;``/``COMMIT;``). The
    inner ``COMMIT`` commits to live the moment psql reaches it, so they must
    NEVER be wrapped — to rollback-validate them you swap the trailing
    ``COMMIT;`` for ``ROLLBACK;`` instead.

A migration is *self-committing* (i.e. unsafe to silently wrap in an outer
rollback) if, at the TOP LEVEL (outside comments, string literals, and
dollar-quoted function / DO bodies), it contains:

  * a top-level ``COMMIT;`` / ``COMMIT WORK;`` / ``COMMIT TRANSACTION;``, or
  * a statement that cannot run inside a transaction block and therefore
    force-commits the surrounding work:
      - ``CREATE INDEX CONCURRENTLY`` / ``DROP INDEX CONCURRENTLY``
      - ``REINDEX ... CONCURRENTLY``
      - ``VACUUM`` (any form)
      - ``CREATE DATABASE`` / ``DROP DATABASE``
      - ``CREATE TABLESPACE`` / ``DROP TABLESPACE``
      - ``ALTER SYSTEM``

Comments (``--`` and ``/* */``), single/double-quoted string literals, and
dollar-quoted bodies (``$$ ... $$`` or ``$tag$ ... $tag$`` — the DO blocks and
function bodies that legitimately use ``BEGIN``/``COMMIT`` as PL/pgSQL
keywords) are stripped before classification, so a ``CREATE INDEX
CONCURRENTLY`` mentioned only in a ``--`` comment never trips the guard.

Modes
-----
``--list`` (default)
    Classify every ``db/migrations/*.sql`` and print the result. Exit 0.

``--check FILE [FILE ...]``
    The CI gate. Exit 1 if any listed file is self-committing. This is what the
    workflow runs against the migration files touched in a PR: a self-committing
    migration is flagged so the apply/rollback tooling can confirm it is NOT
    blindly wrapping it.

``--rollback-wrap FILE``
    The mode rollback-validation tooling (``make irrigation-migration-check``)
    calls as a preflight. Exit non-zero — refusing — if the target migration is
    self-committing, so the rollback replay never wraps a migration whose own
    top-level COMMIT (or commit-forcing statement) would defeat the outer
    ROLLBACK.

This module is stdlib-only and does NOT touch the database, the network, or any
device. It only reads ``.sql`` files off disk.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

# Commit-forcing statements that cannot run inside a transaction block. Matched
# at the start of a top-level statement (after comment/literal/dollar-quote
# stripping). Each pattern is anchored with a preceding statement boundary.
_COMMIT_FORCING = (
    ("top-level COMMIT", re.compile(r"\bCOMMIT(\s+(WORK|TRANSACTION))?\s*;", re.IGNORECASE)),
    # NOTE: we deliberately do NOT detect a bare `END;` as transaction control.
    # `END` is heavily overloaded in SQL (CASE ... END, labeled-block END, the
    # PL/pgSQL block END) and the transaction-control `END;` synonym for COMMIT
    # is rare and discouraged. After dollar-quote stripping, a surviving `END;`
    # is almost always a top-level `CASE ... END;` expression terminator (e.g.
    # migration 145 line 95), so matching it produces false positives. An
    # explicit top-level `COMMIT;` plus the commit-forcing DDL below are the
    # reliable, low-false-positive signals for the 2026-05-30 incident shape.
    ("CREATE INDEX CONCURRENTLY", re.compile(r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\b", re.IGNORECASE)),
    ("DROP INDEX CONCURRENTLY", re.compile(r"\bDROP\s+INDEX\s+CONCURRENTLY\b", re.IGNORECASE)),
    ("REINDEX CONCURRENTLY", re.compile(r"\bREINDEX\b[^;]*\bCONCURRENTLY\b", re.IGNORECASE)),
    ("VACUUM", re.compile(r"\bVACUUM\b", re.IGNORECASE)),
    ("CREATE DATABASE", re.compile(r"\bCREATE\s+DATABASE\b", re.IGNORECASE)),
    ("DROP DATABASE", re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE)),
    ("CREATE TABLESPACE", re.compile(r"\bCREATE\s+TABLESPACE\b", re.IGNORECASE)),
    ("DROP TABLESPACE", re.compile(r"\bDROP\s+TABLESPACE\b", re.IGNORECASE)),
    ("ALTER SYSTEM", re.compile(r"\bALTER\s+SYSTEM\b", re.IGNORECASE)),
)


@dataclass
class Classification:
    path: Path
    self_committing: bool
    reasons: list[str]

    @property
    def name(self) -> str:
        return self.path.name


def strip_sql_noise(sql: str) -> str:
    """Return ``sql`` with comments, string literals, and dollar-quoted bodies
    replaced by spaces (newlines preserved so line semantics stay stable).

    Dollar-quoted bodies (``$$...$$`` / ``$tag$...$tag$``) are where PL/pgSQL DO
    blocks and function bodies legitimately use BEGIN/COMMIT/END as control
    keywords; they must be removed before top-level classification. We walk the
    string character-by-character because nested quoting rules cannot be done
    safely with a single regex.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    dollar_tag_re = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")
    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]

        # Line comment: -- ... to end of line
        if two == "--":
            j = sql.find("\n", i)
            if j == -1:
                out.append(" " * (n - i))
                break
            out.append(" " * (j - i))
            i = j
            continue

        # Block comment: /* ... */ (Postgres allows nesting)
        if two == "/*":
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if sql[j : j + 2] == "/*":
                    depth += 1
                    j += 2
                elif sql[j : j + 2] == "*/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            segment = sql[i:j]
            # preserve newlines so line counting downstream stays sane
            out.append("".join("\n" if c == "\n" else " " for c in segment))
            i = j
            continue

        # Dollar-quoted body: $$...$$ or $tag$...$tag$
        if ch == "$":
            m = dollar_tag_re.match(sql, i)
            if m:
                tag = m.group(0)
                end = sql.find(tag, m.end())
                if end == -1:
                    # Unterminated — treat the rest as a body (defensive).
                    segment = sql[i:]
                    out.append("".join("\n" if c == "\n" else " " for c in segment))
                    break
                j = end + len(tag)
                segment = sql[i:j]
                out.append("".join("\n" if c == "\n" else " " for c in segment))
                i = j
                continue

        # Single-quoted string literal (with '' escape)
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and sql[j : j + 2] == "''":
                    j += 2
                    continue
                if sql[j] == "'":
                    j += 1
                    break
                j += 1
            segment = sql[i:j]
            out.append("".join("\n" if c == "\n" else " " for c in segment))
            i = j
            continue

        # Double-quoted identifier (with "" escape) — keep as-is structurally
        # (identifiers can't contain our keywords as statements), but blank it to
        # avoid a quoted name like "vacuum_log" matching VACUUM.
        if ch == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"' and sql[j : j + 2] == '""':
                    j += 2
                    continue
                if sql[j] == '"':
                    j += 1
                    break
                j += 1
            segment = sql[i:j]
            out.append("".join("\n" if c == "\n" else " " for c in segment))
            i = j
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def classify(path: Path) -> Classification:
    """Classify a single migration file as self-committing or safe-to-wrap."""
    sql = path.read_text(encoding="utf-8")
    stripped = strip_sql_noise(sql)
    reasons: list[str] = []
    for label, pattern in _COMMIT_FORCING:
        if pattern.search(stripped):
            reasons.append(label)
    return Classification(path=path, self_committing=bool(reasons), reasons=reasons)


def discover_migrations() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def _print(line: str) -> None:
    sys.stdout.write(line + "\n")


def cmd_list() -> int:
    migrations = discover_migrations()
    if not migrations:
        _print(f"No migrations found under {MIGRATIONS_DIR}")
        return 0
    for path in migrations:
        c = classify(path)
        if c.self_committing:
            _print(f"SELF-COMMITTING (do NOT wrap): {c.name}  [{', '.join(c.reasons)}]")
        else:
            _print(f"safe-to-wrap                 : {c.name}")
    return 0


def cmd_check(files: list[str]) -> int:
    """CI gate: exit 1 if any listed file is self-committing."""
    bad: list[Classification] = []
    for f in files:
        path = Path(f)
        if not path.is_absolute():
            path = (REPO_ROOT / f).resolve()
        if not path.is_file():
            _print(f"::warning::skipping non-existent file {f}")
            continue
        c = classify(path)
        if c.self_committing:
            bad.append(c)
            _print(
                f"::error::{c.name} is SELF-COMMITTING ({', '.join(c.reasons)}). "
                "It must NOT be replayed under an outer BEGIN..ROLLBACK dry-run "
                "(the inner COMMIT / commit-forcing statement defeats the rollback "
                "and commits to live — 2026-05-30 incident). Apply it as-is and "
                "rollback-validate by swapping its trailing COMMIT for ROLLBACK."
            )
        else:
            _print(f"ok (safe-to-wrap): {c.name}")
    return 1 if bad else 0


def cmd_rollback_wrap(file: str) -> int:
    """Preflight for rollback-validation tooling: refuse a self-committing file."""
    path = Path(file)
    if not path.is_absolute():
        path = (REPO_ROOT / file).resolve()
    if not path.is_file():
        _print(f"::error::migration not found: {file}")
        return 2
    c = classify(path)
    if c.self_committing:
        _print(
            f"REFUSING to wrap {c.name} in an outer BEGIN..ROLLBACK: it is "
            f"self-committing ({', '.join(c.reasons)}). The inner COMMIT / "
            "commit-forcing statement would defeat the outer ROLLBACK and commit "
            "to the live database (2026-05-30 live-commit incident). "
            "Rollback-validate it by swapping its trailing COMMIT for ROLLBACK "
            "instead."
        )
        return 1
    _print(f"OK to wrap in BEGIN..ROLLBACK (non-self-transactional): {c.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migration rollback-safety guard (#23): reject self-committing "
        "migrations being replayed under an outer BEGIN..ROLLBACK."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--list",
        action="store_true",
        help="Classify every db/migrations/*.sql (default). Exit 0.",
    )
    group.add_argument(
        "--check",
        nargs="+",
        metavar="FILE",
        help="Exit 1 if any listed migration is self-committing (CI gate).",
    )
    group.add_argument(
        "--rollback-wrap",
        metavar="FILE",
        help="Preflight: exit non-zero refusing to wrap a self-committing migration.",
    )
    args = parser.parse_args(argv)

    if args.check:
        return cmd_check(args.check)
    if args.rollback_wrap:
        return cmd_rollback_wrap(args.rollback_wrap)
    return cmd_list()


if __name__ == "__main__":
    raise SystemExit(main())
