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

- The schemas it validates against (`verdify_schemas/` — shared, coordinator merges)
- The ESP32 side of the connection (that's `firmware`)
- The planner logic (that's `genai`) — even though `iris_planner.py` sits in `ingestor/` for deployment reasons, its content is genai-owned

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `firmware` | New sensor, new override flag, new diagnostic field | Firmware emits → ingestor routes via `entity_map.py` → coordinator adds DB column + schema |
| `genai` | Planner's emitted tunables change | Genai updates `ALL_TUNABLES`; ingestor dispatcher validates through `SetpointChange`; no code coupling |
| `web` | Adding a new table for vault writers / API to read | Ingestor writes, web reads — column additions via coordinator schema PR |
| `coordinator` | Every write path schema change | Every `INSERT INTO climate/diagnostics/equipment_state/...` must validate through a `verdify_schemas` model first |

## Gates

- Every DB write must run through a Pydantic schema at the boundary (Sprint 23 completed this across ingestor.py + tasks.py). New write paths must continue this pattern.
- Restart-then-tail is the live-test gate: `sudo systemctl restart verdify-ingestor && sudo journalctl -u verdify-ingestor -f`. Watch for `ValidationError` or `row failed schema validation` for 5 min before considering a deploy green.
- DB is live production; never run destructive migrations without coordinator sign-off.

## Ask coordinator before

- Adding a new hypertable or renaming a column
- Changing a `verdify_schemas` write model (e.g., tightening a range check) — surface drift first
- Wiring a new external API (Shelly v2, new HA integration) — might need a new `external.py` schema
- Touching the setpoint confirmation loop (`confirmed_at` / setpoint_snapshot cross-check) — dispatcher and confirmation are tightly coupled

## Recent arc (pre-agent-org)

- Sprint 18: Deterministic dispatch
- Sprint 19: Signal quality + test coverage
- Sprint 20: Unified plan schema + feedback loop
- Sprint 21: Full-stack Pydantic coverage (added `verdify_schemas/` as the contract layer)
- Sprint 23 (in flight): Rollout — every ingestor write path validates through a schema

Use GitHub issues on `VerdifyConsultancy/verdify-platform` for next work; the
old `docs/backlog/ingestor.md` file is archived in
`/Users/jason/Orbit/context_dump/verdify-platform/`.
