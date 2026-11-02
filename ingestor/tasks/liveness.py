"""tasks.liveness — the ingestor heartbeat (VD-54, decisions Q-01).

Every 60 s the task loop writes one ``system_state`` row, entity
``ingestor_heartbeat``, value ``alive``, whether or not the ESP32 session is
up. The dashboard's Now read (``verdify_services.reads``) calls the ingestor
stale once the newest row is older than 180 s, so the banner can tell a dead
ingestor from a controller that stopped reporting. The row goes through the
ingestor login's existing ``v_runtime_system_state_write`` view: no grant and
no migration. The module is not named after the task, because the ``tasks``
package re-exports each task function by name and a submodule called
``ingestor_heartbeat`` would be shadowed by its own function.
"""

from verdify_schemas.telemetry import SystemStateRow

from ._common import UTC, asyncpg, datetime

HEARTBEAT_ENTITY = "ingestor_heartbeat"
HEARTBEAT_VALUE = "alive"


async def ingestor_heartbeat(pool: asyncpg.Pool) -> None:
    """Write one validated heartbeat row."""
    row = SystemStateRow(ts=datetime.now(UTC), entity=HEARTBEAT_ENTITY, value=HEARTBEAT_VALUE)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO v_runtime_system_state_write (ts, entity, value) VALUES ($1, 'ingestor_heartbeat', 'alive')",
            row.ts,
        )
