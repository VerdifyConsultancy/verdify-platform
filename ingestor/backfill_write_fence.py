"""Shared DB-write arbitration between the ingestor and HA gap backfill.

The climate table has no unique logical-bucket constraint, and the ingestor can
replay old rows from its pod-local spool after a backfill has committed. Both
writers therefore take this transaction-scoped advisory lock and re-check the
minute bucket before inserting. The mounted backfill script imports this module
as a runtime capability marker, so it fails closed on an older ingestor image.
"""

from __future__ import annotations

BACKFILL_WRITE_FENCE_CONTRACT_VERSION = 1
CLIMATE_WRITE_FENCE_NAMESPACE = "verdify-climate-logical-bucket-v1"
CLIMATE_WRITE_BUCKET_SECONDS = 60
CLIMATE_WRITE_FENCE_SQL = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
CLIMATE_WRITE_FENCE_TRY_SQL = "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))"


def climate_write_fence_key(greenhouse_id: str) -> str:
    if not isinstance(greenhouse_id, str) or not greenhouse_id.strip():
        raise ValueError("greenhouse_id must be a non-empty string")
    return f"{CLIMATE_WRITE_FENCE_NAMESPACE}:{greenhouse_id.strip()}"
