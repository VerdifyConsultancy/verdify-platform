"""Bounded asyncpg adapter shared by API/MCP; imports no service entrypoint."""

import json

from pydantic import ValidationError

from .observed_minutes import ObservedMinuteEvidence

READER_SQL = "SELECT * FROM public.fn_observed_minute_diagnostic($1::date, $2::text)"


def parse_observed_minute_row(row, day, greenhouse_id="vallery"):
    """Fail closed; never send invalid DB values or validation inputs to readers."""
    try:
        data = dict(row)
        if data["day"] != day or data["greenhouse_id"] != greenhouse_id:
            raise ValueError("scope mismatch")
        if isinstance(data.get("diagnostic"), str):
            data["diagnostic"] = json.loads(data["diagnostic"])
        data["availability"] = "available" if data["unavailable_reason"] is None else "unavailable"
        return ObservedMinuteEvidence.model_validate(data)
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        return ObservedMinuteEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="invalid_diagnostic")


async def read_observed_minute_evidence(conn, day, greenhouse_id="vallery"):
    # Keep the pure wire schema/publisher usable without the DB client installed.
    import asyncpg

    if greenhouse_id != "vallery":
        return ObservedMinuteEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="unsupported_greenhouse")
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '3000ms'")
            row = await conn.fetchrow(READER_SQL, day, greenhouse_id, timeout=3.5)
    except (asyncpg.QueryCanceledError, TimeoutError):
        return ObservedMinuteEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="db_statement_timeout")
    except (
        asyncpg.UndefinedFunctionError,
        asyncpg.UndefinedTableError,
        asyncpg.UndefinedColumnError,
        asyncpg.InsufficientPrivilegeError,
    ):
        return ObservedMinuteEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="reader_unavailable")
    return parse_observed_minute_row(row, day, greenhouse_id)
