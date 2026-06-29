#!/usr/bin/env python3
"""
populate-site-content.py — Materialize website/docs Markdown plus planner
playbook and skills into the site_content + playbook_content tables.

Phase 3 of the Iris loop overhaul: until now the site_content table existed
but was empty, and the planner playbook lived only on disk (so embed-corpora.py
had nothing to chunk). This script:

  - Walks docs/**/*.md (excluding agent-internal/meta docs) → site_content
  - Walks /mnt/iris/verdify-vault/website/**/*.md → site_content
  - Walks docs/planner/*.md and the agent-host skills mirror → playbook_content
  - Chunks long files by markdown headings, then ~512-token blocks
  - Idempotent: content_hash skips unchanged rows

Run as:   python3 scripts/populate-site-content.py
Or:       python3 scripts/populate-site-content.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE_ROOT = Path("/mnt/iris/verdify-vault/website")
sys.path.insert(0, str(REPO_ROOT / "ingestor"))
from config import DB_DSN  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [populate-site] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Docs that aren't useful to Iris as semantic retrieval targets.
SITE_DOC_EXCLUDE_PATTERNS = (
    "FOLDER-HIERARCHY",
    "BACKLOG",
    "site-image-audit",
    "site-content-map",
    "grafana-website-visual-audit",
    "grafana-panel-catalog",
    "site-publishing-pipeline",
    "site-simplification-proposal",
    "cleanup-",
    "showcase-",
)

SITE_DOC_ROOTS = [
    (REPO_ROOT / "docs", REPO_ROOT),
    (WEBSITE_ROOT, WEBSITE_ROOT.parent),
]
PLAYBOOK_ROOTS = [
    REPO_ROOT / "docs" / "planner",
]
# Agent-host skills mirror — may or may not be present at runtime
SKILLS_PATHS = [
    Path("/mnt/agents/iris/skills/greenhouse-planner.md"),
]

# Soft target: ~512 tokens ≈ 2KB of English text. We chunk by heading first;
# any individual section that's still too big gets sliced.
CHUNK_TARGET_BYTES = 2048

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_excluded(path: Path) -> bool:
    name = path.name.lower()
    return any(pat.lower() in name for pat in SITE_DOC_EXCLUDE_PATTERNS)


def _site_doc_relative_path(md_path: Path, rel_root: Path) -> str:
    """Stable source_id for site_content/embeddings."""
    return md_path.relative_to(rel_root).as_posix()


def _chunk_markdown(body: str) -> list[tuple[str | None, str]]:
    """Split a markdown doc into (heading, chunk) tuples.

    Heading boundary first; oversized sections get sliced into ~CHUNK_TARGET_BYTES
    pieces while preserving paragraph boundaries where possible.
    """
    chunks: list[tuple[str | None, str]] = []
    # Find headings + their byte positions
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        # No headings — chunk the whole body
        return _slice(None, body)
    # Prefix before the first heading is its own chunk (no heading attribution)
    first_start = matches[0].start()
    if first_start > 0:
        prefix = body[:first_start].strip()
        if prefix:
            chunks.extend(_slice(None, prefix))
    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[m.end() : end].strip()
        if not section:
            continue
        chunks.extend(_slice(heading, section))
    return chunks


def _slice(heading: str | None, text: str) -> list[tuple[str | None, str]]:
    if len(text.encode("utf-8")) <= CHUNK_TARGET_BYTES:
        return [(heading, text)]
    out: list[tuple[str | None, str]] = []
    paragraphs = text.split("\n\n")
    buf: list[str] = []
    buf_bytes = 0
    for para in paragraphs:
        pb = len(para.encode("utf-8"))
        if buf and buf_bytes + pb > CHUNK_TARGET_BYTES:
            out.append((heading, "\n\n".join(buf).strip()))
            buf = [para]
            buf_bytes = pb
        else:
            buf.append(para)
            buf_bytes += pb + 2
    if buf:
        out.append((heading, "\n\n".join(buf).strip()))
    return out


async def _upsert_site_doc(conn: asyncpg.Connection, page_path: str, content: str) -> None:
    """site_content stores a single row per page (no chunking at this layer).
    Chunking happens at the embedding step; site_content is the snapshot.
    """
    existing = await conn.fetchval("SELECT 1 FROM site_content WHERE page_path = $1", page_path)
    if existing:
        await conn.execute(
            "UPDATE site_content SET content = $2, updated_at = now() WHERE page_path = $1",
            page_path,
            content,
        )
    else:
        await conn.execute(
            "INSERT INTO site_content (page_path, content) VALUES ($1, $2)",
            page_path,
            content,
        )


async def _upsert_playbook_chunks(
    conn: asyncpg.Connection, source_path: str, chunks: list[tuple[str | None, str]]
) -> int:
    n_written = 0
    for idx, (heading, text) in enumerate(chunks):
        h = _hash(text)
        existing = await conn.fetchrow(
            "SELECT content_hash FROM playbook_content WHERE source_path = $1 AND chunk_idx = $2",
            source_path,
            idx,
        )
        if existing and existing["content_hash"] == h:
            continue
        if existing:
            await conn.execute(
                """UPDATE playbook_content
                      SET heading = $3, content = $4, content_hash = $5, updated_at = now()
                    WHERE source_path = $1 AND chunk_idx = $2""",
                source_path,
                idx,
                heading,
                text,
                h,
            )
        else:
            await conn.execute(
                """INSERT INTO playbook_content
                     (source_path, chunk_idx, heading, content, content_hash)
                   VALUES ($1, $2, $3, $4, $5)""",
                source_path,
                idx,
                heading,
                text,
                h,
            )
        n_written += 1
    return n_written


async def run(dry_run: bool) -> None:
    conn = await asyncpg.connect(DB_DSN)
    try:
        site_written = 0
        playbook_written = 0

        # Site docs
        for root, rel_root in SITE_DOC_ROOTS:
            if not root.is_dir():
                continue
            for md_path in sorted(root.rglob("*.md")):
                if _is_excluded(md_path):
                    continue
                # Don't ingest the repo playbook into site_content — it has its own table.
                if root == REPO_ROOT / "docs" and md_path.parent.name == "planner":
                    continue
                rel = _site_doc_relative_path(md_path, rel_root)
                content = md_path.read_text(encoding="utf-8")
                if not content.strip():
                    continue
                log.info("site_doc: %s (%d bytes)", rel, len(content))
                if not dry_run:
                    await _upsert_site_doc(conn, rel, content)
                site_written += 1

        # Playbook + skills
        for root in PLAYBOOK_ROOTS:
            if not root.is_dir():
                continue
            for md_path in sorted(root.rglob("*.md")):
                rel = md_path.relative_to(REPO_ROOT).as_posix()
                body = md_path.read_text(encoding="utf-8")
                chunks = _chunk_markdown(body)
                log.info("playbook: %s → %d chunks", rel, len(chunks))
                if not dry_run:
                    playbook_written += await _upsert_playbook_chunks(conn, rel, chunks)

        for skill_path in SKILLS_PATHS:
            if not skill_path.is_file():
                log.info("skills mirror not present: %s (skipping)", skill_path)
                continue
            body = skill_path.read_text(encoding="utf-8")
            chunks = _chunk_markdown(body)
            log.info("skills: %s → %d chunks", skill_path, len(chunks))
            if not dry_run:
                playbook_written += await _upsert_playbook_chunks(conn, str(skill_path), chunks)

        log.info(
            "done — site_doc rows=%d playbook chunks written=%d dry_run=%s",
            site_written,
            playbook_written,
            dry_run,
        )
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
