# Controlled planner experiment — accuracy review and implementation program

- **Date:** 2026-08-14
- **Author:** claude (outer-loop controller), on direction from Jason
- **Reviews:** [`docs/research/planner-efficacy-current-firmware-2026-08-14.md`](../research/planner-efficacy-current-firmware-2026-08-14.md)
  (the codex second-pass audit, commit `efa85343`)
- **Program goal:** implement and run a controlled experiment that can prove or
  refute that the AI planner usefully optimizes the greenhouse relative to the
  deterministic controller, across planner, database, dispatcher, ingestor,
  firmware, and GitOps surfaces.

## 1. Accuracy review of the audit

Six independent verification passes were run against the codebase (commit
`efa85343`), the live production database, the deploy manifests, and the
audit's own reproduction package. Verdict: **the audit is accurate and its
readiness verdict stands.** Every load-bearing claim was confirmed; the
corrections below are detail-level and none weakens the report's conclusions.

### 1.1 Confirmed (load-bearing)

| Claim | Evidence |
|---|---|
| `v_active_plan` resolves each parameter independently; a tick can execute a hybrid vector | `db/migrations/196-planner-terminal-lifecycle.sql:398-430` — `DISTINCT ON (parameter)` over three disjoint source branches |
| Numbered migrations never reach a populated DB via the migrate image | `db/Dockerfile.migrate:16-18` copies only `schema.sql` + `000`; `db/migrate.sh:24-34` exits 0 when `public.climate` exists; real path is manual `kubectl exec psql` (`docs/runbooks/k3s-operations.md:48`) |
| `schema_migrations` ledger is an unused design artifact | `db/ledger/schema_migrations.sql:3-12` self-describes as not-in-production |
| Single shared `DB_USER=verdify`, also schema owner (4,883 objects) | `deploy/k8s/base/configmap.yaml:23`, `db-statefulset.yaml:69-70` |
| No `btree_gist`; only timescaledb/pgcrypto/vector | `db/schema.sql:25,39,53`; advisory-lock overlap guards are required, as the report says |
| Dispatcher delivers per-parameter `setpoint_changes`; firmware setters mutate live globals per value; no transaction barrier | `ingestor/tasks/dispatcher.py:1236-1244`; `firmware/greenhouse/tunables.yaml` `set_action` lambdas |
| `climate_action_log` plan lineage is a server-side heuristic (one latest active `setpoint_plan` row stamped on the whole tick) | `ingestor/ingestor.py:745-756` |
| `esp32_push` fair queue can interleave two logical vectors; per-parameter supersede; no vector concept | `ingestor/esp32_push.py:494-535,567` (`_FAIR_QUANTUM=2`) |
| Forecast engine is a live parallel writer | runs every 900 s as an ingestor subprocess (`ingestor/tasks/forecast.py:157`, registered `ingestor/ingestor.py:2128`); writes `setpoint_plan`/`setpoint_changes` with `source='preemptive'` — and its `setpoint_changes` rows carry **no** `delivery_status`/`trigger_id`, bypassing the dispatcher lifecycle entirely (stronger than the report states) |
| MCP `set_plan`/`set_tunable` are actuation-eligible writers; waypoints are all materialized from one creation-time band/state snapshot | `mcp/server.py:2057,2603,2669-2676` |
| Registry: `PLANNER_PUSHABLE_REG` = 49, Tier-1 = 40, difference is exactly the nine named fields; `TunableDef` has no wire metadata | `verdify_schemas/tunable_registry.py` (computed) |
| Intent semantics one-sided/inert as described (negative VPD/temp bias discarded; `thermal_lead_time_min` has `materialized_knobs=()`) | `verdify_schemas/climate_intent.py:457,577,120-127` |
| `planner_graph/verdify_contract.py` stale copy: 39 fields, the exact 4 missing and 3 obsolete names | `planner_graph/verdify_contract.py:20-60` |
| All eight firmware compiled defaults in the Section 5 table | `firmware/greenhouse/globals.yaml` `initial_value`s (not `tunables.yaml`) |
| Volatile water/runtime crash state (`restore_value: no`) | `globals.yaml:1214-1217,1291-1339` |
| No MCP server-side authorization at all — the Hermes tool scope is client-side only; the Bearer token is sent but never validated | `mcp/server.py:257-276` (no auth code); `hermes-config.yaml:430` |
| `verdify-prod-dark` Argo app is real prod and sources `overlays/prod`; no app targets `overlays/prod-dark`; twin manifest is an offline corpus loop absent from prod, with runtime gcc/pip and open 443 egress | `deploy/k8s/argocd/apps/verdify-prod-dark.yaml:39,59`; `components/firmware-twin/twin-shadow-deployment.yaml` |
| `verdify-config` consumed via `envFrom` with no rollout trigger | plain ConfigMap, `base/kustomization.yaml:29` |

### 1.2 Methodology integrity

The reproduction package was reviewed line-by-line against the report:
matching spec (384/2,496 bins, nearest-then-caliper, 93 pairs, raw-value
guard), both sensitivity pools, all 12 PID configs and model gates, the
noncentral-t power solver (computed via `scipy.brentq`, not transcribed), all
six forecast-response correlations, and the effective-readback posture table
all match the committed JSONs exactly; 14/14 package tests pass. The code
implements the parts that weaken the headline as faithfully as the parts that
strengthen it (`counterfactual_eligible: false`, 24.2% common support,
max-over-pair-origin screening). One inherent caveat: raw CSV inputs are not
committed, so reproduction depends on DB extraction anchored by the SHA-256
input manifests.

### 1.3 Data spot-checks (live prod DB)

All exact: firmware epoch first-readback `2026-07-10 21:03:12.991915Z`; 84
journal plans; zero journal plans local Aug 6–10 with resumption 2026-08-11
12:13/12:16Z; `climate_action_log` 53,777 total / 28,418 journal-joined /
22,703 `preemptive-` / 2,641 orphan one-shots; 554 preemptive `setpoint_plan`
rows across 230 plan ids; 3,264/3,264 15-minute climate bins; 9,029 gal
quality-ok water deltas. Reproduction tests: 14/14 pass.

### 1.4 Corrections (none change the conclusions)

1. **planner_graph is deployed, not absent** — `components/planner` runs one
   idle replica in prod with zero recorded runs ever. "Deployed but never
   processed a run" is the defensible statement.
2. **The writer lease is a Kubernetes Lease**, not a database lease
   (`ingestor/writer_lease.py`), feature-flagged and no-op off-cluster.
3. **Worker registration** lives in the `TASKS` list at
   `ingestor/ingestor.py:2111-2145` (22 workers); `ingestor/tasks/__init__.py`
   is an import shim.
4. **The replay-override sentence in §3.3 is wrong in detail**:
   `firmware/test/replay_overrides.cpp` wires 17 columns (including
   `fog_escalation_kpa` but **not** `cool_stage2_over_high_f`), while
   `firmware/test/replay_emit.cpp` — the binary the twin ships — wires 56
   `sp_*` columns including both. The Frozen-FSM-replay blocker still stands on
   its other legs (no effective-vector assembly, incomplete Python comparator,
   mister/water-budget coverage), but the quoted two-field claim is inaccurate.
5. **Gather-script byte-sync CI already exists**
   (`tests/test_dli_availability.py:138`); the P0 item reduces to keeping it.
6. **`night_vpd_bias_kpa` is inert but carried forward** from `v_active_plan`,
   not hard-coded to zero.
7. **Argo `prune:false` is by absence** — neither app has an `automated` block;
   a manual `--prune` sync would still prune. Protection is operator
   discipline, not manifest.

### 1.5 New defects found during verification (not in the report)

- `materialize_climate_intent_tier1` writes three parameters that are absent
  from `REGISTRY` (`fog_stress_min_dew_margin_f`,
  `fog_stress_window_latest_hour`, `sw_fog_stress_window_extend_enabled`);
  they are silently dropped and the guardrail clause reading one of them
  (`climate_intent.py:514`) is permanently dead.
- `direct_wet_stress_latest_hour` is a live tier-1 planner-pushable registry
  field with **zero firmware presence** (no global, no entity, no read site).
  The canonical 49-field wire schema must resolve this (retire or implement)
  before the vector contract freezes.
- `db/migrate.sh` claims the snapshot covers "through migration 156" but the
  checked-in `db/schema.sql` contains migration-196 artifacts and lacks
  200/203 — prod's applied state past 196 is not provable from the repo, which
  is exactly the gap the ledger closes.
- The migration Job's `wait-for-db` initContainer uses a mutable tag
  (`timescale/timescaledb:2.25.2-pg16`), violating the stated digest policy.
- `globals.yaml:1219` comment says "persisted" over a block of
  `restore_value: no` globals.

### 1.6 On the hypothesis framing (PID vs Frozen-FSM)

Jason's stated hypothesis is "AI vs a standard PID-based controller." The
audit demonstrates (and this review confirms) that a *physical* PID comparison
is not currently estimable: all 12 prespecified PID variants request 64–138%
more open-loop duty, but both plant models fail their declared gates, so no
physical outcome can be attributed. The deployed deterministic ESP32 FSM with
one reviewed, immutable, device-confirmed policy vector (**Frozen-FSM**) is the
honest "standard controller" arm: same firmware, safety, hardware, and
delivery, differing only in whether fresh AI adaptation is admitted. The
program therefore implements the AI-vs-Frozen-FSM switchback as the causal
test, and retains the PID decision-replay as a secondary, clearly-labeled
non-physical comparison. If a literal PID arm is required, it needs new
firmware control logic and its own safety qualification — out of scope for
version 1 and scientifically weaker (it changes two things at once).

## 2. The study being implemented (locked design, from §8)

- **Question:** does assignment to AI hot/dry template selection reduce
  aggregate nine-actuator command duty while preserving VPD and temperature
  corridor control, versus a frozen baseline vector, all else identical?
- **Design:** 30 consecutive Denver days, 15 adjacent-day pairs, blinded
  `XY|YX` randomization via committed beacon + mapping secret; ITT primary;
  three co-primary gates (VPD noninferiority +0.05 kPa, temperature
  noninferiority +0.50 °F, nine-device duty superiority < 0), each one-sided
  α=0.025; intersection-union decision; leave-one-pair-out influence rule.
- **Arms:** A = Frozen-FSM baseline vector (time-weighted medians of
  device-confirmed readbacks Jul 12–Aug 4, excl. Jul 25); B = planner
  selecting between exactly two pre-qualified 49-field AI templates
  (moderate/aggressive hot-dry), differing from baseline only in the 11-field
  allowlist; deterministic forecast engine runs shadow-only.
- **Prerequisite gates:** byte-identical codec goldens → shadow mode → live
  twin 7–14 d agreement → step-response qualification (96 transitions, 24
  cells, ≤45-day window) → 7-day A/A → protocol lock/beacon/secret → 30-day
  run → frozen analysis → one-way unblind.
- **Power reality (§8.5):** the run is a large-effect screen; a null is
  inconclusive by design and feeds a separately preregistered follow-up.

## 3. Implementation lanes

Dependencies: A → {B, C, E, G}; B → C → D; {A–F} → rollout; G in parallel
after A.

| Lane | Surface | Core deliverables |
|---|---|---|
| **A — wire schema + codecs** | `verdify_schemas`, `firmware/lib` | `TunableDef` wire metadata (wire_id/kind/unit/quantum/width); generated `policy_vector.py` + `policy_vector_generated.h`; `content_sha256`/`activation_sha256` with §8.9 treatment octets; cross-language golden fixtures; drift tests vs ESPHome entities; resolve `direct_wet_stress_latest_hour` and the three unregistered materializer params |
| **B — experiment schema + migration delivery + roles** | `db/` | Migration `207-controlled-policy-experiment.sql` (§8.7 tables + SECURITY DEFINER transition functions + advisory-lock overlap guards); ledgered populated-DB migration path (ledger table, audited baseline, migrate image carries numbered migrations, `psql -X -v ON_ERROR_STOP=1` runner, post-assertions); DB role split with per-workload Secrets |
| **C — assignment/arbiter/delivery workers** | `ingestor/`, `mcp/` | `experiment_assignments.py`, `policy_arbiter.py`, `policy_delivery.py` (feature-off inert); whole-vector transactions in `esp32_push.py`; action logger consumes device-confirmed vector identity; `set_plan`/`set_tunable` and forecast engine demoted to proposal producers; legacy direct writes rejected while armed |
| **D — planner treatment firewall** | `ingestor/iris_planner.py`, `scripts/gather-plan-context.sh`, `hermes-iris`, `mcp/server.py` | Fail-closed experiment gather mode + frozen context snapshots; **new MCP server-side authorization** (none exists today) with audience-scoped tool sets; template-selection proposal tool; quarantined lessons/evaluation; regenerate `planner_graph` contract from registry |
| **E — firmware atomic policy engine** | `firmware/` | `policy_vector.h`; active/boundary/tactical `ControlPolicy` slots + ROM baseline; begin/chunk/validate/commit/abort + manifest native API services (heap-budgeted — the repo deliberately runs ONE service today); per-tick policy snapshot for all 49 consumers + CI consumer manifest; two-copy NVS journal; conservative reboot semantics (water budget marked consumed, relays off, full min-off dwell); recovery image; native tests + power-loss fixtures |
| **F — GitOps, observability, twin** | `deploy/`, `grafana/`, `api/`, `twin/` | `VERDIFY_POLICY_VECTOR_MODE`/`VERDIFY_ACTIVE_EXPERIMENT_ID`/`VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED` flags + config-revision hashes on pod templates; blinded ops board; twin productionized from `twin/Dockerfile` (in-cluster build → zot, digest pin, no runtime pip/gcc, no 443 egress, live as-of adapter + `v_policy_twin_asof_input`); experiment lifecycle API; §8.10 acceptance suite |
| **G — protocol artifacts + analysis** | `research/planner-efficacy/` | `protocols/planner-switchback-v1.yaml` + qualification spec; baseline extraction (locked query/hashes); two AI templates; randomization generator + HMAC commitment tooling; frozen analyzer + power artifact; A/A gate checklist |

## 4. Sequencing and calendar constraints

Software (lanes A–G) is implementable now, in this order: A → B → C/E in
parallel → D/F → shadow deploy. The calendar-bound phases cannot be
compressed by engineering effort:

| Phase | Wall clock | Gate |
|---|---|---|
| Build + shadow deploy | engineering time | CI + A/A-parity acceptance tests |
| Firmware OTA (staged-vector build + recovery image) | ≥1 week cadence + 48 h bake (repo OTA freeze) | replay-diff, invariants, HIL |
| Live twin shadow | 7–14 days | byte-identical policy + action agreement |
| Step-response qualification | up to 45 days (weather-eligible cells) | 96/96 transitions, max settling ≤2 h |
| A/A | 7 days | six gates (§8.6) |
| Randomized run | 30 days (no DST crossing) | integrity/safety monitoring only |
| Frozen analysis + unblind | days | committed analyzer |

Realistic total: **≥10–13 weeks** after the platform build lands, dominated by
qualification weather-eligibility and the fixed windows. A 30-day window
avoiding the Nov 1 2026 DST transition means starting the randomized phase by
late September or after early November.

## 5. Human-gated decisions (gate:jason)

1. Noninferiority margins approval (horticultural + safety owner, §8.4).
2. Frozen baseline vector + both AI template approvals after compiled replay/HIL.
3. Beacon-round naming + witnessed mapping-secret ceremony (the agent cannot
   witness its own CSPRNG draw; a human must attest the commitment ordering).
4. OTA scheduling within the firmware freeze cadence.
5. Protocol lock sign-off before day 1.

## 6. Known constraints and risks

- **Prod delivery reliability is itself a prerequisite fix**: 24.7% of trigger
  cycles failed/missed in the epoch; issue #575 (ingestor CrashLoop history,
  1,128 restarts, stable ~4 days at review time) remains open. The A/A gate
  will fail until delivery is reliable — that is working as intended.
- **Heap**: chronic ESP32 heap exhaustion (#428) constrains the firmware
  service surface; the policy transport must be chunked and heap-budgeted, and
  the repo's one-service pattern (`set_band_anchor`) is the template.
- **DB**: single-instance TimescaleDB; ledgered migration path requires a
  verified restorable snapshot before first production run. Historical
  migration numbering has duplicates/gaps → ledger keys on filename + SHA.
- **Role split** requires owner-level DB work through the new migration path.
- **Images** build in-cluster via Kaniko → zot origin (ghcr banned, ADR-0021);
  the firmware-builder currently uses a ghcr esphome image — needs mirroring.
- **Argo prune safety is discipline-only**; experiment rollout steps must
  never use `--prune`.
