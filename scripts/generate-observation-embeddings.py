#!/usr/bin/env python3
"""
generate-observation-embeddings.py — Generate vector embeddings for image observations
using Gemini Embedding 2. Stores 3072-dim vectors in pgvector for similarity search.

Usage:
    generate-observation-embeddings.py           # embed all unembedded observations
    generate-observation-embeddings.py --all     # re-embed everything
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestor"))
from ai_config import ai

from config import DB_DSN

logging.basicConfig(level=logging.INFO, format="%(asctime)s [embed] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def observation_to_text(row) -> str:
    """Convert an image observation record into embeddable text."""
    crops = json.loads(row["crops_observed"]) if row["crops_observed"] else []
    crop_summaries = []
    for c in crops:
        health = c.get("health_score", "?")
        stress = c.get("stress_indicators", [])
        notes = c.get("notes", "")
        crop_summaries.append(
            f"{c.get('crop', 'unknown')} in {c.get('zone', '?')}: health {health}/10, {', '.join(stress) if stress else 'no stress'}. {notes}"
        )

    env = row.get("environment_notes") or ""
    actions = row.get("recommended_actions") or []
    actions_text = "; ".join(actions) if isinstance(actions, list) else str(actions)

    return f"""Greenhouse observation from camera {row["camera"]} in zone {row["zone"]}.
Confidence: {row.get("confidence", "?")}
Crops observed: {"; ".join(crop_summaries) if crop_summaries else "none identified"}
Environment: {env}
Recommended actions: {actions_text}"""


async def main():
    reembed_all = "--all" in sys.argv
    client = ai.get_client("embedding")
    conn = await asyncpg.connect(DB_DSN)

    try:
        if reembed_all:
            rows = await conn.fetch("SELECT * FROM image_observations ORDER BY ts")
        else:
            rows = await conn.fetch("SELECT * FROM image_observations WHERE embedding IS NULL ORDER BY ts")

        if not rows:
            log.info("No observations to embed")
            return

        log.info("Embedding %d observations", len(rows))
        embedded = 0

        for row in rows:
            text = observation_to_text(row)
            try:
                from google import genai

                embed_cfg = ai.model("embedding")
                response = client.models.embed_content(
                    model=ai.model_name("embedding"),
                    contents=text,
                    config=genai.types.EmbedContentConfig(output_dimensionality=embed_cfg.get("dimensions", 3072)),
                )
                vector = response.embeddings[0].values

                # pgvector expects a string like '[0.1, 0.2, ...]'
                vec_str = "[" + ",".join(str(v) for v in vector) + "]"
                await conn.execute(
                    "UPDATE image_observations SET embedding = $1::vector WHERE id = $2", vec_str, row["id"]
                )
                embedded += 1

            except Exception as e:
                log.error("Failed to embed observation %d: %s", row["id"], e)

        log.info("Done: %d/%d observations embedded", embedded, len(rows))

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
