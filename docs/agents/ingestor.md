# Agent: `ingestor`

Every write into TimescaleDB, every read from Home Assistant / Shelly / Tempest / Open-Meteo, the setpoint dispatcher, and the alert monitor.

## Owns

- `ingestor/ingestor.py` — ESP32 → DB main loop, all hypertable writes
- `ingestor/tasks.py` — periodic tasks (shelly_sync, tempest_sync, ha_sensor_sync, alert_monitor, forecast_sync, setpoint_dispatcher, grow_light_daily, water_flowing_sync, etc.)
- `ingestor/shared.py`, `ingestor/config.py`, `ingestor/entity_map.py`
- `ingestor/iris_planner.py` — **planner invocation** lives here but is owned by `genai` (see handshake)
- `scripts/forecast-sync.py` (Open-Meteo)
- `scripts/daily-summary-snapshot.py` (if not already absorbed into tasks.py)
- Systemd unit: `verdify-ingestor.service`

## Does not own

- The schemas it validates against (`verdify_schemas/` is shared)
- The ESP32 side of the connection (that's `firmware`)
- The planner logic (that's `genai`) — even though `iris_planner.py` sits in `ingestor/` for deployment reasons, its content is genai-owned

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `firmware` | New sensor, new override flag, new diagnostic field | Firmware emits → ingestor routes via `entity_map.py` → DB column and schema land compatibly |
| `genai` | Planner's emitted tunables change | Genai updates `ALL_TUNABLES`; ingestor dispatcher validates through `SetpointChange`; no code coupling |
| `web` | Adding a new table for vault writers / API to read | Land the table, schema, and write path before the web consumer |
| shared schemas | Every write-path contract change | Every `INSERT INTO climate/diagnostics/equipment_state/...` validates through a `verdify_schemas` model first |

## Required checks

- Every DB write must run through a Pydantic schema at the boundary (Sprint 23 completed this across ingestor.py + tasks.py). New write paths must continue this pattern.
- For a live deploy, restart and inspect the journal. Watch for `ValidationError`
  or `row failed schema validation` for five minutes before declaring it green.
- DB is live production; never run destructive migrations without documented validation.

## Cross-component checks

- Validate new hypertables and column renames with a disposable restored schema.
- Surface existing drift before tightening a `verdify_schemas` write model.
- Add an `external.py` boundary schema when wiring a new external API.
- Test dispatcher and confirmation together when changing the setpoint
  confirmation loop.

## Recent arc (pre-agent-org)

- Sprint 18: Deterministic dispatch
- Sprint 19: Signal quality + test coverage
- Sprint 20: Unified plan schema + feedback loop
- Sprint 21: Full-stack Pydantic coverage (added `verdify_schemas/` as the contract layer)
- Sprint 23 (in flight): Rollout — every ingestor write path validates through a schema

Use GitHub issues on `VerdifyConsultancy/verdify-platform` for next work; the
old `docs/backlog/ingestor.md` file is archived in
`/Users/jason/Orbit/context_dump/verdify-platform/`.
