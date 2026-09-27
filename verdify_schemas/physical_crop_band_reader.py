"""Bounded, fail-closed physical crop-band evidence reader for API and MCP."""

from __future__ import annotations

import json

from pydantic import ValidationError

from .physical_crop_band import PhysicalCropBandEvidence

READER_SQL = "SELECT * FROM public.fn_crop_band_physical_evidence($1::date, $2::text)"


def parse_physical_crop_band_row(row, day, greenhouse_id="vallery"):
    try:
        data = dict(row)
        if data["day"] != day or data["greenhouse_id"] != greenhouse_id:
            raise ValueError("scope mismatch")
        if isinstance(data.get("diagnostic"), str):
            data["diagnostic"] = json.loads(data["diagnostic"])
        data["availability"] = "available" if data["unavailable_reason"] is None else "unavailable"
        return PhysicalCropBandEvidence.model_validate(data)
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        return PhysicalCropBandEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="invalid_evidence")


async def read_physical_crop_band_evidence(conn, day, greenhouse_id="vallery"):
    import asyncpg

    if greenhouse_id != "vallery":
        return PhysicalCropBandEvidence(
            day=day, greenhouse_id=greenhouse_id, unavailable_reason="unsupported_greenhouse"
        )
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '3000ms'")
            row = await conn.fetchrow(READER_SQL, day, greenhouse_id, timeout=3.5)
    except (asyncpg.QueryCanceledError, TimeoutError):
        return PhysicalCropBandEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="db_statement_timeout")
    except (
        asyncpg.UndefinedFunctionError,
        asyncpg.UndefinedTableError,
        asyncpg.UndefinedColumnError,
        asyncpg.InsufficientPrivilegeError,
    ):
        return PhysicalCropBandEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="reader_unavailable")
    return parse_physical_crop_band_row(row, day, greenhouse_id)
