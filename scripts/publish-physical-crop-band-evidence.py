#!/usr/bin/env python3
"""Publish operator-authenticated evidence, never qualify route diagnostics.

The existing trusted database owner must independently authenticate historical
crop targets, fixed hardware placement and fresh per-probe observation times.
Hashes and model validation bind reviewed artifacts; they cannot establish
historical authenticity. This command cannot derive or manufacture that proof.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verdify_schemas.physical_crop_band import PhysicalCropBandDiagnostic  # noqa: E402


def prepare(diagnostic_path, evidence_paths, authenticity_statement):
    diagnostic_bytes = Path(diagnostic_path).read_bytes()
    diagnostic = PhysicalCropBandDiagnostic.model_validate_json(diagnostic_bytes)
    payload = diagnostic.model_dump(mode="json")
    if len(authenticity_statement) < 40 or len(authenticity_statement) > 4096:
        raise ValueError("a bounded explicit owner authenticity attestation is required")
    hashes = {key: hashlib.sha256(Path(path).read_bytes()).hexdigest() for key, path in evidence_paths.items()}
    if set(hashes) != {"target_manifest_sha256", "panel_manifest_sha256", "input_sha256", "calculation_source_sha256"}:
        raise ValueError("all four reviewed evidence inputs are required")
    if any(payload[key] != digest for key, digest in hashes.items()):
        raise ValueError("reviewed evidence bytes differ from diagnostic hashes")
    qualification = {
        "contract": "trusted-owner-physical-qualification-v1",
        "attested_by": "verdify",
        "authenticity_statement": authenticity_statement,
        "artifact_sha256": hashlib.sha256(diagnostic_bytes).hexdigest(),
        "input_hashes": hashes,
    }
    return payload, qualification


async def publish(payload, qualification):
    import asyncpg

    conn = await asyncpg.connect(os.environ["DB_DSN"])
    try:
        if not await conn.fetchval("SELECT current_user='verdify' AND session_user='verdify'"):
            raise ValueError("existing trusted owner connection required")
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout='5000ms'; SET LOCAL lock_timeout='3000ms'")
            revision = await conn.fetchval(
                "SELECT public.fn_publish_physical_crop_band_evidence($1::jsonb,$2::jsonb)",
                json.dumps(payload),
                json.dumps(qualification),
            )
        print(
            json.dumps(
                {
                    "revision_id": revision,
                    "day": payload["day"],
                    "greenhouse_id": payload["greenhouse_id"],
                    "artifact_sha256": qualification["artifact_sha256"],
                }
            )
        )
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", required=True)
    parser.add_argument("--authenticity-statement", required=True)
    fields = {
        "target_manifest_sha256": "target_manifest",
        "panel_manifest_sha256": "panel_manifest",
        "input_sha256": "input",
        "calculation_source_sha256": "calculation_source",
    }
    for option in fields.values():
        parser.add_argument("--" + option.replace("_", "-"), required=True)
    args = parser.parse_args()
    payload, qualification = prepare(
        args.diagnostic, {key: getattr(args, option) for key, option in fields.items()}, args.authenticity_statement
    )
    asyncio.run(publish(payload, qualification))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Physical publication refused; credentials/database details withheld.") from None
