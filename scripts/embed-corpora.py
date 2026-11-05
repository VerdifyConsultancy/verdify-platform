#!/usr/bin/env python3
"""
embed-corpora.py — Embed the knowledge corpora (lesson / plan / site_doc / playbook /
observation) with local Cortex embed.main (2560-dim) into public.knowledge_embeddings
(migration 296, #1351). The site_doc corpus is populated from public website Markdown
plus operator-facing docs by populate-site-content.py.

Runs in the cluster as the verdify-knowledge-embed Job (ingestor image, owner DB login,
the embed.main-only key at /vembed/api_key.txt); never on a laptop. Settings come from
config/ai.yaml models.embedding through verdify_schemas.embeddings, which accepts only
Cortex on cortex.vallery.net.

Resumable: a row whose content_hash and embedding_model already match is skipped, and
every batch is written as soon as it returns, so a rerun continues where an interrupted
run stopped. Bounded: --concurrency batches in flight (default 4), --deadline-seconds
stops launching new batches (the Job's activeDeadlineSeconds is the hard stop), and a
progress line is logged after every batch. Exit 0 only when every queued row is embedded.

Usage:
    embed-corpora.py                       # incremental (default)
    embed-corpora.py --all                 # re-embed everything (force)
    embed-corpora.py --source lesson       # restrict to one source_type
    embed-corpora.py --dry-run             # walk + count, no API calls or writes
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))
from config import DB_DSN  # noqa: E402
from verdify_schemas import embeddings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [embed-corpora] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

VALID_SOURCES = embeddings.SOURCE_TYPES
# embed.main is served with an 8,192-token window; site_doc chunks are 2 KiB, and no other
# source row is near this. Anything longer is cut, and the stored content is the cut text.
MAX_INPUT_CHARS = 16000
ATTEMPTS = 3


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
# Cortex embed.main batching
# ──────────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT INTO public.knowledge_embeddings
      (source_type, source_id, chunk_idx, content, content_hash, embedding_model, embedding, metadata)
    VALUES ($1, $2, $3, $4, $5, $6, $7::halfvec, $8::jsonb)
    ON CONFLICT (source_type, source_id, chunk_idx) DO UPDATE
      SET content         = EXCLUDED.content,
          content_hash    = EXCLUDED.content_hash,
          embedding_model = EXCLUDED.embedding_model,
          embedding       = EXCLUDED.embedding,
          metadata        = EXCLUDED.metadata,
          embedded_at     = now()
"""


async def _existing(conn: asyncpg.Connection, sources: tuple[str, ...]) -> dict[tuple[str, str, int], tuple[str, str]]:
    rows = await conn.fetch(
        """SELECT source_type, source_id, chunk_idx, content_hash, embedding_model
             FROM public.knowledge_embeddings WHERE source_type = ANY($1::text[])""",
        list(sources),
    )
    return {(r["source_type"], r["source_id"], r["chunk_idx"]): (r["content_hash"], r["embedding_model"]) for r in rows}


def plan_rows(collected, existing, force: bool, model: str = embeddings.EMBEDDING_MODEL):
    """The rows still to embed: new, changed, or embedded by another model (all of them with --all)."""
    queued = []
    for st, sid, idx, content, meta in collected:
        content = content[:MAX_INPUT_CHARS]
        h = _hash(content)
        if not force and existing.get((st, sid, idx)) == (h, model):
            continue
        queued.append((st, sid, idx, content, h, meta))
    return queued


async def _embed_with_retry(settings, texts, api_key, client) -> list[list[float]]:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return await embeddings.embed_texts(settings, texts, api_key=api_key, client=client)
        except embeddings.EmbeddingError as exc:
            if attempt == ATTEMPTS or exc.reason == "embedding_batch_invalid":
                raise
            log.warning("batch failed (%s), retry %d/%d", exc.reason, attempt, ATTEMPTS - 1)
            await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


async def embed_queue(conn, settings, api_key, queued, *, concurrency: int, deadline: float, client) -> tuple[int, int]:
    """Embed and upsert in batches, at most `concurrency` in flight; return (embedded, failed batches)."""
    batches = [queued[i : i + settings.batch_size] for i in range(0, len(queued), settings.batch_size)]
    gate = asyncio.Semaphore(concurrency)
    write = asyncio.Lock()
    done = 0
    failed = 0
    started = time.monotonic()

    async def one(batch) -> None:
        nonlocal done, failed
        async with gate:
            if time.monotonic() >= deadline:
                return
            try:
                vectors = await _embed_with_retry(settings, [row[3] for row in batch], api_key, client)
            except embeddings.EmbeddingError as exc:
                failed += 1
                log.error("batch of %d failed: %s", len(batch), exc.reason)
                return
            async with write, conn.transaction():
                for (st, sid, idx, content, h, meta), vec in zip(batch, vectors, strict=True):
                    await conn.execute(
                        UPSERT_SQL,
                        st,
                        sid,
                        idx,
                        content,
                        h,
                        settings.model,
                        embeddings.halfvec_literal(vec),
                        json.dumps(meta),
                    )
            done += len(batch)
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed > 0 else 0.0
            eta = (len(queued) - done) / rate if rate > 0 else float("nan")
            log.info("progress %d/%d rows (%.1f rows/s, eta %.0fs)", done, len(queued), rate, eta)

    await asyncio.gather(*(one(batch) for batch in batches))
    return done, failed


async def run(sources: tuple[str, ...], force: bool, dry_run: bool, concurrency: int, deadline_s: float) -> int:
    deadline = time.monotonic() + deadline_s
    settings = embeddings.load_embedding_settings()
    api_key = None if dry_run else embeddings.read_api_key(settings)

    conn = await asyncpg.connect(DB_DSN)
    try:
        collectors = {
            "lesson": _collect_lessons,
            "plan": _collect_plans,
            "site_doc": _collect_site_docs,
            "playbook": _collect_playbook,
            "observation": _collect_observations,
        }
        collected = []
        for source_type in sources:
            async for row in collectors[source_type](conn):
                collected.append(row)
        existing = await _existing(conn, sources)
        queued = plan_rows(collected, existing, force)
        log.info(
            "corpus %d rows, %d already current, %d queued (model=%s dims=%d dry_run=%s)",
            len(collected),
            len(collected) - len(queued),
            len(queued),
            settings.model,
            settings.dimensions,
            dry_run,
        )
        if dry_run or not queued:
            return 0

        import httpx

        async with httpx.AsyncClient(timeout=settings.timeout_s) as client:
            done, failed = await embed_queue(
                conn, settings, api_key, queued, concurrency=concurrency, deadline=deadline, client=client
            )
    finally:
        await conn.close()
    log.info("finished: %d/%d rows embedded, %d failed batches", done, len(queued), failed)
    if done < len(queued):
        log.error("incomplete: %d rows remain; rerun to resume", len(queued) - done)
        return 1
    return 0


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
    ap.add_argument("--concurrency", type=int, default=4, choices=range(1, 9), metavar="1-8")
    ap.add_argument("--deadline-seconds", type=float, default=3000.0)
    args = ap.parse_args()
    sources = tuple(args.source) if args.source else VALID_SOURCES
    return asyncio.run(
        run(
            sources=sources,
            force=args.all,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
            deadline_s=args.deadline_seconds,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
