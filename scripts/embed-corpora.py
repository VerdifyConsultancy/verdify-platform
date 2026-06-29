#!/usr/bin/env python3
"""
embed-corpora.py — Generate OpenAI text-embedding-3-large embeddings for the
Iris knowledge corpora (lesson / plan / site_doc / playbook / observation) and
store in verdify_embeddings (migration 112). The site_doc corpus is populated
from public website Markdown plus operator-facing docs by populate-site-content.py.

Phase 3 of the Iris loop overhaul. Idempotent: each row carries a content_hash
so unchanged rows skip the embedding call. Designed to run as a daily systemd
timer post-SUNRISE; also safe to run ad-hoc.

Usage:
    embed-corpora.py                       # incremental (default)
    embed-corpora.py --all                 # re-embed everything (force)
    embed-corpora.py --source lesson       # restrict to one source_type
    embed-corpora.py --dry-run             # walk + count, no API calls

Requires OPENAI_API_KEY env var.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ingestor"))
from config import DB_DSN  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [embed-corpora] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-large"  # 3072-dim
EMBED_DIM = 3072
BATCH_SIZE = 64  # OpenAI accepts up to 2048; smaller keeps latency low

VALID_SOURCES = ("lesson", "plan", "site_doc", "playbook", "observation")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────────
# Source collectors — each yields (source_id, chunk_idx, content, metadata)
# ──────────────────────────────────────────────────────────────────────────


async def _collect_lessons(conn: asyncpg.Connection):
    rows = await conn.fetch(
        """
        SELECT id, category, condition, lesson, confidence, times_validated,
               is_active, last_validated
          FROM planner_lessons
         WHERE is_active = true
           AND superseded_by IS NULL
           AND lesson IS NOT NULL
           AND lesson != ''
        """
    )
    for r in rows:
        text = f"[{r['category']}|{r['condition']}] {r['lesson']}"
        meta = {
            "confidence": r["confidence"],
            "times_validated": r["times_validated"],
            "is_active": r["is_active"],
            "last_validated": r["last_validated"].isoformat() if r["last_validated"] else None,
        }
        yield ("lesson", str(r["id"]), 0, text, meta)


async def _collect_plans(conn: asyncpg.Connection):
    rows = await conn.fetch(
        """
        SELECT plan_id, hypothesis, actual_outcome, outcome_score, anchor_score,
               created_at
          FROM plan_journal
         WHERE plan_id LIKE 'iris-%'
           AND hypothesis IS NOT NULL
           AND created_at >= '2026-04-01'
        """
    )
    for r in rows:
        parts = [r["hypothesis"]]
        if r["actual_outcome"]:
            parts.append("---")
            parts.append(r["actual_outcome"])
        text = "\n".join(parts)
        meta = {
            "outcome_score": r["outcome_score"],
            "anchor_score": r["anchor_score"],
            "created_at": r["created_at"].isoformat(),
        }
        yield ("plan", r["plan_id"], 0, text, meta)


async def _collect_site_docs(conn: asyncpg.Connection):
    rows = await conn.fetch("SELECT page_path, content, updated_at FROM site_content")
    for r in rows:
        # site_content is single-blob per page; chunk on embed to fit token limits
        chunks = _chunk_text(r["content"])
        for idx, chunk in enumerate(chunks):
            meta = {"updated_at": r["updated_at"].isoformat()}
            yield ("site_doc", r["page_path"], idx, chunk, meta)


async def _collect_playbook(conn: asyncpg.Connection):
    rows = await conn.fetch(
        """SELECT source_path, chunk_idx, heading, content, updated_at
             FROM playbook_content
            ORDER BY source_path, chunk_idx"""
    )
    for r in rows:
        prefix = f"[{r['heading']}]\n" if r["heading"] else ""
        text = prefix + r["content"]
        meta = {
            "heading": r["heading"],
            "updated_at": r["updated_at"].isoformat(),
        }
        yield ("playbook", r["source_path"], r["chunk_idx"], text, meta)


async def _collect_observations(conn: asyncpg.Connection):
    rows = await conn.fetch(
        """
        SELECT o.id, o.ts, o.obs_type, o.zone, o.position, o.severity,
               o.species, o.count, o.affected_pct, o.source, o.notes,
               o.health_score, o.plant_height_cm, o.leaf_count,
               o.canopy_cover_pct, o.flowering_count, o.fruit_count,
               o.root_condition, o.mortality_count, o.stress_tags,
               c.name AS crop_name, c.variety AS crop_variety, c.stage AS crop_stage,
               z.slug AS zone_slug, p.label AS position_label
          FROM observations o
          LEFT JOIN crops c ON c.id = o.crop_id
          LEFT JOIN zones z ON z.id = o.zone_id
          LEFT JOIN positions p ON p.id = o.position_id
         WHERE coalesce(o.notes, '') != ''
            OR o.health_score IS NOT NULL
            OR o.severity IS NOT NULL
            OR o.stress_tags IS NOT NULL
         ORDER BY o.ts DESC
        """
    )
    for r in rows:
        location = r["position_label"] or r["position"] or r["zone_slug"] or r["zone"] or "unknown position"
        crop = " ".join(
            part
            for part in (
                r["crop_name"],
                f"({r['crop_variety']})" if r["crop_variety"] else None,
                f"stage={r['crop_stage']}" if r["crop_stage"] else None,
            )
            if part
        )
        metrics = []
        for key in (
            "health_score",
            "severity",
            "affected_pct",
            "plant_height_cm",
            "leaf_count",
            "canopy_cover_pct",
            "flowering_count",
            "fruit_count",
            "mortality_count",
        ):
            if r[key] is not None:
                metrics.append(f"{key}={r[key]}")
        if r["root_condition"]:
            metrics.append(f"root_condition={r['root_condition']}")
        if r["stress_tags"]:
            metrics.append("stress_tags=" + ",".join(r["stress_tags"]))
        text = "\n".join(
            part
            for part in (
                f"[observation|{r['obs_type']}|{location}|{r['ts'].isoformat()}]",
                f"crop={crop}" if crop else None,
                "metrics=" + "; ".join(metrics) if metrics else None,
                r["notes"],
            )
            if part
        )
        meta = {
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "obs_type": r["obs_type"],
            "zone": r["zone_slug"] or r["zone"],
            "position": r["position_label"] or r["position"],
            "source": r["source"],
            "crop_name": r["crop_name"],
            "crop_stage": r["crop_stage"],
            "health_score": r["health_score"],
            "severity": r["severity"],
            "stress_tags": list(r["stress_tags"]) if r["stress_tags"] else [],
        }
        yield ("observation", str(r["id"]), 0, text, meta)


def _split_oversized_block(text: str, max_bytes: int) -> list[str]:
    """Hard-split a paragraph/table row that is larger than the embedding limit."""
    out: list[str] = []
    buf: list[str] = []
    buf_bytes = 0
    for ch in text:
        cb = len(ch.encode("utf-8"))
        if buf and buf_bytes + cb > max_bytes:
            chunk = "".join(buf).strip()
            if chunk:
                out.append(chunk)
            buf = [ch]
            buf_bytes = cb
        else:
            buf.append(ch)
            buf_bytes += cb
    chunk = "".join(buf).strip()
    if chunk:
        out.append(chunk)
    return out


def _chunk_text(text: str, max_bytes: int = 2048) -> list[str]:
    """Byte-bounded chunker: prefer paragraphs, hard-split oversized blocks."""
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    out: list[str] = []
    buf: list[str] = []
    buf_bytes = 0
    for para in text.split("\n\n"):
        para_chunks = _split_oversized_block(para, max_bytes) if len(para.encode("utf-8")) > max_bytes else [para]
        for chunk in para_chunks:
            cb = len(chunk.encode("utf-8"))
            if buf and buf_bytes + cb + 2 > max_bytes:
                out.append("\n\n".join(buf).strip())
                buf = [chunk]
                buf_bytes = cb
            else:
                buf.append(chunk)
                buf_bytes += cb + 2
    if buf:
        out.append("\n\n".join(buf).strip())
    return out


# ──────────────────────────────────────────────────────────────────────────
# OpenAI batching
# ──────────────────────────────────────────────────────────────────────────


async def _embed_batch(client, texts: list[str]) -> list[list[float]]:
    """Call OpenAI embeddings.create on a batch and return vectors."""
    resp = await asyncio.to_thread(client.embeddings.create, model=EMBED_MODEL, input=texts, dimensions=EMBED_DIM)
    return [d.embedding for d in resp.data]


def _vector_literal(vec: list[float]) -> str:
    """asyncpg-friendly pgvector literal: '[v1,v2,...]' as text."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def _upsert_embedding(
    conn: asyncpg.Connection,
    source_type: str,
    source_id: str,
    chunk_idx: int,
    content: str,
    content_hash: str,
    embedding: list[float],
    metadata: dict,
) -> None:
    await conn.execute(
        """
        INSERT INTO verdify_embeddings
          (source_type, source_id, chunk_idx, content, content_hash, embedding, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::vector, $7::jsonb)
        ON CONFLICT (source_type, source_id, chunk_idx) DO UPDATE
          SET content       = EXCLUDED.content,
              content_hash  = EXCLUDED.content_hash,
              embedding     = EXCLUDED.embedding,
              metadata      = EXCLUDED.metadata,
              embedded_at   = now()
        """,
        source_type,
        source_id,
        chunk_idx,
        content,
        content_hash,
        _vector_literal(embedding),
        json.dumps(metadata),
    )


async def _existing_hash(conn: asyncpg.Connection, source_type: str, source_id: str, chunk_idx: int) -> str | None:
    return await conn.fetchval(
        """SELECT content_hash FROM verdify_embeddings
             WHERE source_type = $1 AND source_id = $2 AND chunk_idx = $3""",
        source_type,
        source_id,
        chunk_idx,
    )


# ──────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────


async def run(sources: tuple[str, ...], force: bool, dry_run: bool) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not dry_run:
        log.error("OPENAI_API_KEY not set; cannot call embeddings API")
        sys.exit(1)

    client = None
    if not dry_run:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

    conn = await asyncpg.connect(DB_DSN)
    try:
        collectors = {
            "lesson": _collect_lessons,
            "plan": _collect_plans,
            "site_doc": _collect_site_docs,
            "playbook": _collect_playbook,
            "observation": _collect_observations,
        }

        # Gather all rows that need (re)embedding
        to_embed: list[tuple[str, str, int, str, str, dict]] = []
        for source_type in sources:
            collector = collectors[source_type]
            async for st, sid, idx, content, meta in collector(conn):
                h = _hash(content)
                if not force:
                    existing = await _existing_hash(conn, st, sid, idx)
                    if existing == h:
                        continue
                to_embed.append((st, sid, idx, content, h, meta))

        log.info("queued %d rows for embedding (dry_run=%s)", len(to_embed), dry_run)
        if dry_run or not to_embed:
            return

        # Batch through the OpenAI API
        for i in range(0, len(to_embed), BATCH_SIZE):
            batch = to_embed[i : i + BATCH_SIZE]
            texts = [row[3] for row in batch]
            vectors = await _embed_batch(client, texts)
            for (st, sid, idx, content, h, meta), vec in zip(batch, vectors, strict=True):
                await _upsert_embedding(conn, st, sid, idx, content, h, vec, meta)
            log.info("embedded %d/%d", min(i + BATCH_SIZE, len(to_embed)), len(to_embed))
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-embed everything (force)")
    ap.add_argument(
        "--source",
        action="append",
        choices=VALID_SOURCES,
        help="restrict to one source_type (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sources = tuple(args.source) if args.source else VALID_SOURCES
    asyncio.run(run(sources=sources, force=args.all, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
