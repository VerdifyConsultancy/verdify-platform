#!/usr/bin/env python3
"""Sprint 23+ Phase 5: auto-generate the Mermaid ERD block in
verdify_schemas/RELATIONSHIPS.md from information_schema.

Reads every FK constraint in the `public` schema and emits a Mermaid
`erDiagram` block. Replaces everything between the sentinel markers
`<!-- BEGIN AUTO-ERD -->` and `<!-- END AUTO-ERD -->` in
RELATIONSHIPS.md. Idempotent — safe to re-run nightly.

Run:
  python3 scripts/generate-erd.py            # writes RELATIONSHIPS.md
  python3 scripts/generate-erd.py --check    # fails if file would change

Intended to run:
  - manually after any FK-changing migration
  - via a GH Actions step once the schemas job has the DB up
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REL_PATH = Path(__file__).resolve().parent.parent / "verdify_schemas" / "RELATIONSHIPS.md"
BEGIN = "<!-- BEGIN AUTO-ERD -->"
END = "<!-- END AUTO-ERD -->"


def _psql(sql: str) -> list[list[str]]:
    host = os.environ.get("POSTGRES_HOST")
    if host:
        env = os.environ.copy()
        env.setdefault("PGHOST", host)
        env.setdefault("PGPORT", env.get("POSTGRES_PORT", "5432"))
        env.setdefault("PGUSER", env.get("POSTGRES_USER", "verdify"))
        env.setdefault("PGPASSWORD", env.get("POSTGRES_PASSWORD", "verdify"))
        env.setdefault("PGDATABASE", env.get("POSTGRES_DB", "verdify"))
        cmd = ["psql", "-t", "-A", "-F", "|", "-c", sql]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True, env=env)
    else:
        r = subprocess.run(
            [
                "docker",
                "exec",
                "verdify-timescaledb",
                "psql",
                "-U",
                "verdify",
                "-d",
                "verdify",
                "-t",
                "-A",
                "-F",
                "|",
                "-c",
                sql,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    return [ln.split("|") for ln in r.stdout.splitlines() if ln.strip()]


def _fetch_fks() -> list[tuple[str, str, str, str]]:
    """(parent_tbl, parent_col, child_tbl, child_col) tuples, sorted stable."""
    rows = _psql(
        """
        SELECT
          kcu_p.table_name,    -- parent
          kcu_p.column_name,
          kcu_c.table_name,    -- child
          kcu_c.column_name
        FROM information_schema.referential_constraints rc
        JOIN information_schema.key_column_usage kcu_c
          ON kcu_c.constraint_name = rc.constraint_name
         AND kcu_c.constraint_schema = rc.constraint_schema
        JOIN information_schema.key_column_usage kcu_p
          ON kcu_p.constraint_name = rc.unique_constraint_name
         AND kcu_p.constraint_schema = rc.unique_constraint_schema
         AND kcu_p.ordinal_position = kcu_c.ordinal_position
        WHERE kcu_c.table_schema = 'public'
        """
    )
    out: list[tuple[str, str, str, str]] = []
    for r in rows:
        if len(r) >= 4:
            out.append((r[0].strip(), r[1].strip(), r[2].strip(), r[3].strip()))
    # Stable order: parent, child, child_col
    return sorted(set(out))


def _render(fks: list[tuple[str, str, str, str]]) -> str:
    """Emit Mermaid erDiagram block + a human-readable FK table."""
    lines: list[str] = []
    lines.append("")
    lines.append("*Auto-generated from `information_schema.referential_constraints` by ")
    lines.append(f"`scripts/generate-erd.py`. Do not hand-edit. {len(fks)} FK(s) found.*")
    lines.append("")
    lines.append("```mermaid")
    lines.append("erDiagram")
    # Mermaid erDiagram wants `PARENT ||--o{ CHILD : "label"` style.
    # Each (parent, child) pair emits one arrow, labeled by the child column.
    seen_pairs: set[tuple[str, str]] = set()
    for p_tbl, _p_col, c_tbl, c_col in fks:
        pair = (p_tbl, c_tbl)
        # Collapse multiple columns between the same tables into one arrow
        # whose label is the child column list (rare, but happens for
        # composite FKs or self-refs).
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        if p_tbl == c_tbl:
            lines.append(f'    {p_tbl} }}o--|| {c_tbl} : "{c_col} (self-ref)"')
        else:
            lines.append(f'    {p_tbl} ||--o{{ {c_tbl} : "{c_col}"')
    lines.append("```")
    lines.append("")
    lines.append("### Full FK inventory")
    lines.append("")
    lines.append("| Parent | Parent col | Child | Child col |")
    lines.append("|---|---|---|---|")
    for p_tbl, p_col, c_tbl, c_col in fks:
        lines.append(f"| `{p_tbl}` | `{p_col}` | `{c_tbl}` | `{c_col}` |")
    lines.append("")
    return "\n".join(lines)


def _replace_block(existing: str, new_block: str) -> str:
    if BEGIN not in existing or END not in existing:
        # Insert the block after the first heading
        hdr_match = re.search(r"^# .+?$", existing, re.MULTILINE)
        insert_at = hdr_match.end() + 1 if hdr_match else 0
        header_prefix = existing[:insert_at]
        body = existing[insert_at:]
        return f"{header_prefix}\n{BEGIN}\n{new_block}\n{END}\n{body}"
    pattern = re.compile(rf"{re.escape(BEGIN)}.*?{re.escape(END)}", re.DOTALL)
    return pattern.sub(f"{BEGIN}\n{new_block}\n{END}", existing)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if RELATIONSHIPS.md would change (CI mode).",
    )
    args = parser.parse_args()

    fks = _fetch_fks()
    block = _render(fks)
    existing = REL_PATH.read_text() if REL_PATH.exists() else "# Schema Relationships\n"
    updated = _replace_block(existing, block)

    if args.check:
        if existing != updated:
            sys.stderr.write(
                f"{REL_PATH} is stale — run `python3 scripts/generate-erd.py` to regenerate the auto-ERD block.\n"
            )
            return 1
        print("ERD up to date.")
        return 0

    if existing == updated:
        print(f"No change — {REL_PATH} already current ({len(fks)} FK(s)).")
        return 0
    REL_PATH.write_text(updated)
    print(f"Wrote {REL_PATH} with {len(fks)} FK(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
