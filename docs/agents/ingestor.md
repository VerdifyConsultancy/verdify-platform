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
  readiness fail closed; the process must not open the ESP32 connection. On
  SIGTERM, stop renewal and close the local push gate first, cancel and await
  the ESP32 disconnect, and only then clear the remote holder identity. Never
  release the Lease while the old client can still be connected.
- HA gap recovery fetches every bounded history leaf before opening the
  per-window DB transaction. Ordinary requests are pre-split to at most 60
  minutes and 25 entities, retry once, then timeout leaves split adaptively
  under 1,200-second, 512-request, 250,000-point, and 32 MiB/response bounds.
  Recorder requests use padded microsecond bounds and suppress synthetic
  initial state after the first temporal leaf so a real boundary transition is
  neither dropped nor replaced. Malformed rows fail the whole window before
  its transaction; a failed leaf writes nothing. Apply mode and every ingestor
  climate insert share a transaction-scoped advisory fence. The ingestor
  rechecks the logical minute bucket under that fence, including for delayed
  spool replay, so a replay after a committed backfill cannot create a duplicate
  climate bucket. The mounted script requires the matching fence-contract module
  and fails closed on an older image. Rollout is deliberately two-phase because
  equal desired digests do not make an ArgoCD multi-resource sync atomic: first
  pin both resources while the CronJob remains suspended, then roll and prove
  the singleton healthy on that exact digest with an explicit
  `ingestor-healthz.py --mode readiness` probe. Writer readiness includes an
  empty current-pod climate spool. Production still mounts this state as
  restart-volatile `emptyDir` under open #382, so the gated Recreate must also
  prove the old pod's spool empty immediately before deletion; a newly empty pod
  is not evidence that old replay was drained. Restoring the retained Longhorn
  PVC is a separate storage gate. Only a separate reviewed change may unsuspend
  the natural schedule after those proofs. The pre-commit fenced phase and each DB
  statement (including COMMIT) have 30-second bounds; timeout or a writer failure
  rolls the whole window back. The live writer never waits behind repair: a busy
  try-lock preserves one fresh or replayed row at normal cadence in the durable
  spool. If repair already filled that bucket, the fenced replay reconciles its
  actual timestamp and present sensor fields into the single existing row before
  removing the spool entry; device telemetry therefore wins over reconstructed
  HA history while the spool survives. It does not cure #382 pod-replacement
  loss. Reconciliation is re-emitted on the retired fan-out path; do not
  reactivate a subscriber until its plain insert is made bucket-idempotent too.
  Adjacent inclusive history windows meet at microsecond-precise,
  non-overlapping boundaries, and every natural run also reconciles a trailing
  240-minute equipment/system event window because sparse events have no sample
  cadence from which a missing transition can be inferred. The tail covers two
  missed hourly starts plus the declared 15-minute late-start allowance. Apply
  ranges end at least 120 seconds behind wall time and require the shared
  60-second climate bucket cadence. Dry runs take no write fence and report
  candidate rows, never committed/backfilled windows.
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
