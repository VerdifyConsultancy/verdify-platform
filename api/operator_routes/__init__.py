"""Route modules of the operator API (architecture §8, execution plan §2.1).

Rules for every module here (``reads``, ``series``, ``writes``, ``irrigation_reads``,
``irrigation_writes``):

- One ``router = APIRouter()`` per module. Paths are written without the ``/v1`` prefix:
  ``api/operator_api.py`` includes each router under ``/v1`` with the bearer, session and role
  dependencies, so a module carries no auth of its own.
- Every route sets ``operation_id`` to its design id (API-01 to API-21, API-IRR-01 to API-IRR-13).
- No SQL: a route parses its request and calls ``verdify_services``; a ``ServiceError`` becomes
  ``application/problem+json`` in the operator API's handler.
- A route takes its database connection from ``connection``, which acquires one from the
  operator API's asyncpg pool (``app.state.pool``) for the length of the request.
- Whichever PR lands first creates a module's stub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
from fastapi import Request


async def connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """FastAPI dependency: one pooled connection for the request."""
    async with request.app.state.pool.acquire() as conn:
        yield conn
