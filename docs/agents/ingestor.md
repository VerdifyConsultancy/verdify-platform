# Agent: `ingestor`

Every write into TimescaleDB, every read from Home Assistant / Shelly / Tempest / Open-Meteo, the setpoint dispatcher, and the alert monitor.

## Owns

- `ingestor/ingestor.py` — ESP32 → DB main loop, all hypertable writes
- `ingestor/tasks.py` — periodic tasks (shelly_sync, tempest_sync, ha_sensor_sync, alert_monitor, forecast_sync, setpoint_dispatcher, grow_light_daily, water_flowing_sync, etc.)
- `ingestor/shared.py`, `ingestor/config.py`, `ingestor/entity_map.py`
- `ingestor/iris_planner.py` — **planner invocation** lives here but is owned by `genai` (see handshake)
- `scripts/forecast-sync.py` (Open-Meteo)
- `scripts/daily-summary-snapshot.py` (if not already absorbed into tasks.py)
- k3s Deployment and resilience overlay: `deploy/k8s/base/ingestor-deployment.yaml`
  and `deploy/k8s/overlays/prod/ingestor-resilience.patch.yaml`

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
- Production replacement is human-gated and ArgoCD-owned. Validate the exact
  image digest and rendered singleton/Recreate manifest before requesting the
  gate; after sync, use the literal pod/readiness/log probes in the approved
  packet and repeat them at the stated durability interval.
- DB is live production; never run destructive migrations without coordinator sign-off.

## k3s runtime health and gap recovery

- The singleton writer publishes only allowlisted runtime state to
  `/tmp/verdify-ingestor-runtime.json` from its owning asyncio loop. Kubelet
  liveness uses the monotonic heartbeat only; it must never depend on the DB,
  ESP32 reachability, telemetry age, or Lease state.
- Readiness is deliberately stricter: a fresh runtime heartbeat, capture mode,
  no writer-fatal state, an enabled+usable+held writer Lease, a connected ESP32
  client, and climate no older than 300 seconds. A device/DB outage must
  leave the retry loop Running but make the pod NotReady.
- The no-argument `ingestor-healthz.py` command retains the legacy climate-only
  check. The mode-specific probe manifest therefore must roll out with the
  image that implements it. In prod this is one reviewed Recreate replacement;
  preserve `replicas: 1`, the Lease fence, and the human device-write gate. The
  source-only #575 recovery change deliberately leaves the legacy prod probe
  and old digest paired; enable the new liveness/readiness modes only in the
  same gated change that pins the exact newly built image digest.
- A disabled Lease remains a development no-op. If the Lease is explicitly
  enabled but ServiceAccount/API/CA capability is unavailable, acquisition and
  readiness fail closed; the process must not open the ESP32 connection.
- HA gap recovery fetches every bounded history leaf before opening the
  per-window DB transaction. Ordinary requests are pre-split to at most 60
  minutes and 25 entities, retry once, then timeout leaves split adaptively
  under 1,200-second, 512-request, 250,000-point, and 32 MiB/response bounds.
  Recorder requests use padded microsecond bounds and suppress synthetic
  initial state after the first temporal leaf so a real boundary transition is
  neither dropped nor replaced. Malformed rows fail the whole window before
  its transaction; a failed leaf writes nothing, and a writer failure rolls all
  six table writes for the window back. Dry runs report candidate rows, never
  committed/backfilled windows.
- Never manually create/rerun the production backfill Job as a diagnostic: the
  CronJob passes `--apply`. Recovery proof requires a successful natural `:23`
  run and the next natural run, with no new duplicate buckets or out-of-window
  rows.

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
