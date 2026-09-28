"""Bounded API/MCP reader for published route-only observations."""

from __future__ import annotations

import json

from pydantic import ValidationError

from .physical_crop_band import RouteOnlyCropBandEvidence

READER_SQL = "SELECT * FROM public.fn_route_only_crop_band_diagnostic($1::date, $2::text)"


def parse_route_only_crop_band_row(row, day, greenhouse_id="vallery") -> RouteOnlyCropBandEvidence:
    """A malformed latest publication must never fall back or echo raw data."""
    try:
        data = dict(row)
        if data["day"] != day or data["greenhouse_id"] != greenhouse_id:
            raise ValueError("route observation scope mismatch")
        if isinstance(data.get("diagnostic"), str):
            data["diagnostic"] = json.loads(data["diagnostic"])
        data["availability"] = "observational" if data["unavailable_reason"] is None else "unavailable"
        return RouteOnlyCropBandEvidence.model_validate(data)
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        return RouteOnlyCropBandEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="invalid_diagnostic")


async def read_route_only_crop_band_evidence(conn, day, greenhouse_id="vallery") -> RouteOnlyCropBandEvidence:
    import asyncpg

    if greenhouse_id != "vallery":
        return RouteOnlyCropBandEvidence(
            day=day, greenhouse_id=greenhouse_id, unavailable_reason="unsupported_greenhouse"
        )
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '3000ms'")
            row = await conn.fetchrow(READER_SQL, day, greenhouse_id, timeout=3.5)
    except (asyncpg.QueryCanceledError, TimeoutError):
        return RouteOnlyCropBandEvidence(
            day=day, greenhouse_id=greenhouse_id, unavailable_reason="db_statement_timeout"
        )
    except (
        asyncpg.UndefinedFunctionError,
        asyncpg.UndefinedTableError,
        asyncpg.UndefinedColumnError,
        asyncpg.InsufficientPrivilegeError,
    ):
        return RouteOnlyCropBandEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="reader_unavailable")
    return parse_route_only_crop_band_row(row, day, greenhouse_id)
