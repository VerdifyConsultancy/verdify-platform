# Verdify — Floating-Corridor Replan: Lanes, Waves & Worktree Kickoff

> **Current audit (2026-08-29):** current GitHub lane ownership, sprint
> sequencing, and all open-work handoffs are recorded in
> [`docs/audits/work-pending-2026-08-29.md`](docs/audits/work-pending-2026-08-29.md).
> This file remains the detailed historical floating-corridor decomposition.

> Optional ownership and planning map. Lanes describe likely domain ownership;
> they are not dispatch, permission, approval, or routing boundaries. Work
> directly from the user's request, current repository state, and live evidence.
> **Lab-lane staleness notice (2026-08-30):** This file is a generated
> 2026-06-22 planning snapshot. Its L8/#351 migration entries are historical.
> The Lab is standardized on the Quartz source/publisher/cache path documented
> in `docs/site-publishing-pipeline.md`.

_Generated 2026-06-22 from the ADR-0004 edge-case replan. Source of truth: `planning/backlog.yaml` (validated by `planning/schema.py`, `make planning-validate`). Control objective: ADR-0004 floating-corridor (epic #359). User priority #1: **reduce device cycling / minimize runtime**._

## How to read this
The backlog is carved into **9 worktree-ready lanes** (L0 schema + L1-L8) sequenced across **4 waves**. Each lane = one `git worktree` off `origin/main`, one PR. Each work item states what / why / how / acceptance / dependencies and is tracked as a GitHub issue.

## Waves (sequence)

### W0 — Source-only foundations (the next full CI/CD deploy)
- **Goal:** Land everything that is source-only and no-device: split PR #385 to merge the safe bundle, land L0-SCHEMA tunable_registry/cfg_* rows, make the cycling KPI scoreboard TRUSTWORTHY (reconcile cycles_* against raw equipment_state, exclude partial days, backfill cycles_grow_light), and harden the CI guards that are the wire protocol. This is the next full container promotion to prod via ArgoCD. No firmware OTA, no live tunable push, no prod migration apply in this wave.
- **Gate:** none — source-only, CI-green, no device or prod-migration action; #384 is the review/deploy-gate issue that gates the bundle before any promotion
- **Lanes:** L0-SCHEMA, L2-OBS, L4-DATA, L5-DEPLOY, L3-PLANNER (L3-S1 schema-only)
- **Exit:** PR #385 split-merged with heat_dehum + live float-flip CARVED OUT; migration 186 present and rollback-wrap-proven but NOT yet applied to prod; L0-SCHEMA tunable_registry rows + cfg_* readback bindings for all W2 firmware tunables landed with passing drift guards; outcome_kpi cycles_* reconciled against v_equipment_runtime_daily TRUE rising edges with partial/future days excluded and cycles_grow_light NULL gap backfilled (the scoreboard is now trustworthy); service-restart-drift-guard now requires a structured 'Post-merge restart:' marker (false-pass closed); generated-CM==dashboard-source CI gate added; solar SSoT drift guard binds lat/lon/zenith across firmware/ingestor/186; full CI/CD container deploy promoted to prod via gated ArgoCD sync. make lint + make test green (1 tolerated flaky).

### W1 — Solar parity apply + float trial (gated, reversible)
- **Goal:** Apply migration 186 NOAA solar parity to prod and refresh the band-curve matviews, then run the band_track_fraction 0.25→0 float-flip as a reversible, observable, predeclared-threshold trial. This is the single highest-leverage/lowest-effort cycling lever (~7-9% of samples reclaimed to IDLE, corridor widens temp 7.5F→10F / VPD 0.41→0.55kPa).
- **Safeguard:** runtime preflight — prod migration apply (#293, rollback-wrap proof per CLAUDE.md, non-self-transactional, then REFRESH MATERIALIZED VIEW CONCURRENTLY mv_band_curve) AND the live device tunable push (#377, preflight no-critical-alerts, cfg_band_track_fraction=0 readback as confirm, predeclared rollback value 0.25)
- **Lanes:** L4-DATA (186 apply, safety-checked), L1-CLIMATE (#377 float-flip, safety-checked), L2-OBS (trial read), L6-HA (writer-absent alert live as safety net)
- **Exit:** Migration 186 applied to prod with apply-timestamp recorded and band-grade history treated as a regime break; band-curve matviews refreshed so future-dated rows serve the corrected band; DB-derived solar phase matches firmware/ingestor NOAA contract within minutes year-round. band_track_fraction=0 pushed live with cfg readback confirming 0; 48-72h observation window completed against the now-trustworthy KPIs; SUCCESS = served-temp compliance ≥95% AND mister_center/fog on-events/day not up vs the 0.25 baseline AND night VPD not worse by >5pp; ROLLBACK to 0.25 on served-temp <95% or mister_center on-events/day up >15% or any safety alert. #361/#378/#379 remain deferred — outcome data from this trial feeds the corridor-width decision.

### W2 — Quiet-the-greenhouse OTA(s)
- **Goal:** Land the REAL chatter fixes the user asked for, honoring the firmware freeze (≤1 OTA/week + 48h bake + replay-diff/invariants/test artifacts). OTA-1: mister dwell-guard (#299) CO-OTA'd with the grow-light solar-window shoulder min-on fix (#349/NEW). OTA-2 (separate week, after a clean float baseline + live moisture telemetry): heat_dehum as opt-in default-OFF + fog/mister anti-ping-pong hold (#383).
- **Safeguard:** runtime preflight — firmware OTA (both OTA-1 and OTA-2); each carries replay-diff (THRESHOLD_PCT override documented for the intentional divergence) + firmware-invariants + test-firmware artifacts in the PR; OTA-2 (heat_dehum) requires its replay heat1-only divergence + an outcome_kpi overnight-heat1 before/after
- **Lanes:** L1-CLIMATE (firmware OTAs, safety-checked), L0-SCHEMA (cfg_* rows already landed in W0), L2-OBS (before/after cycling gate), L5-DEPLOY (cycling deploy gate NEW-D), L8-PRODUCT (#118 2nd-writer channel-separation assertion before grow-light OTA)
- **Exit:** OTA-1: misters in a dwell-guard (mister_min_on_s=120/min_off_s=60 + max-cycles/hr governor, cfg_* readbacks) via the mister FSM path (NOT R[]); grow-light dawn/dusk lux hysteresis widened + window decision held when exterior_lux_fresh flaps + 120s min-on verified to span the !in_window path; lighting-audit-static + lighting replay re-run; measured ~40-55% fewer mister on-events and ~40-50% reduction of the post-#295 grow-light increment, judged on the L2 trustworthy cycles_* before/after gate; 48h bake passed before OTA-2. OTA-2: heat_dehum opt-in behind closed_heat_dehum_enabled defaulting OFF, shipped only after the clean float baseline and live #327 moisture telemetry, with overnight-heat1 on-events before/after proving it adds no net cycling in the idle-inside-corridor regime; fog/mister anti-ping-pong post-wet hold landed (ONE anti-chatter mechanism per BC-8, not two).

### W3 — Parallel reliability + irrigation + product/decommission
- **Goal:** Run the non-climate-critical-path lanes in parallel: HA/reliability hardening (writer fence, backup freshness, CNPG manifests), operator-scoped irrigation + hardware, planner outcome-objective wiring, and product-plane/decommission/auth cleanup. None of these gate the quiet-the-greenhouse spine; several are safety-checked for infra/hardware.
- **Gate:** infra-preflight (HA: #245 CNPG cutover, #382 NAS fix, #235 DNS SPOF), hardware-preflight (irrigation: #298 probe moves, #45 bring-up, #37/#51 calibration, #16), device-write-preflight for any device-channel write (#118/#177); planner #365 wiring is source-only/no-device
- **Lanes:** L6-HA, L7-IRRIG, L3-PLANNER (#365 planner wiring), L8-PRODUCT, L2-OBS (residual KPI/alert-lifecycle)
- **Exit:** L3: ADR-0004 composite outcome objective wired into planner reward + homepage, target-distance reward retired (migration 147 NOT applied as-is), validated offline against the bounded-write contract. L6: VerdifyESP32NoWriter/SplitBrain PrometheusRule live in overlays/prod; verdify-db-backup CronJob proven green + VerdifyBackupStale alert; CNPG prod manifests authored (#244/#245 unblocked, but cutover stays gated and interlocked on #382). L7: day-mask trim runbook applied (reversible overwatering mitigation), soil_moisture_targets/zones.yaml re-baselined to current crops, saturation alert + dispatcher drip-skip first closed loop landed (no OTA); hardware items remain operator-scoped. L8: site_content RAG corpus repointed off the dead /mnt/iris mount with self-alerting staleness; setpoint-server single-writer channel-separation assertion + docs/SERVICE_MAP.md entry; auth-rehome + decommission cleanup progressed. Obsolete-dev issues (#316 twin CrashLoop, #321 staging overlay) closed as the dev env is decommissioned.

## Dependency DAG

```
PR #385 split (W0)  ->  everything downstream
      (PR #385 is the keystone: it lands migration 186 (solar parity), the outcome_kpi scoreboard, the moisture telemetry, and the tunable_registry rows. Every solar/KPI/float/OTA decision downstream consumes one of those surfaces. It must merge (source-only, no device) before W1/W2 can proceed. It is also the next full CI/CD container deploy.)
L0-SCHEMA  ->  L1-CLIMATE
      (Schema-lands-first (CLAUDE.md): every new firmware tunable on L1 (mister_min_on_s/min_off_s/max_cycles_per_hr #299, closed_heat_dehum_enabled/post_wet_antipingpong_hold_s #383, grow_light_shoulder_hysteresis NEW, zone_priority_* #323, sensor_max_rate_of_change/flatline_samples #368) needs a pydantic TunableDef + esp_object_id + cfg_readback_object_id + drift-guard row BEFORE the firmware OTA, or the no-new-fire-and-forget CI gate blocks the OTA.)
L0-SCHEMA  ->  L2-OBS
      (L2's outcome composite-score columns and KPI views bind to schema/contract names; schema registry rows land first so the drift guards stay meaningful.)
L0-SCHEMA  ->  L3-PLANNER
      (L3-S1 is a schema-only PR adding outcome_score_composite to ScorecardResponse + planner-io contract with a drift guard binding planner-reader/MCP-emitter/DB-column names — it lands before the planner consumes the composite in #365 (schema-first + drift-guards-are-the-wire-protocol).)
L0-SCHEMA  ->  L4-DATA
      (L4's DB migrations (solar-186 SSoT note, cycle-count reconcile view) are serialized one-at-a-time through L0-SCHEMA; migration order and rollback-safety classification are owned there.)
L0-SCHEMA  ->  L7-IRRIG
      (L7-S2 data-truth hygiene migration (re-baseline stale soil_moisture_targets + zones.yaml seed rows) is schema-first, serialized through L0-SCHEMA so it does not collide with other in-flight migrations.)
L4-DATA (migration 186 NOAA solar parity)  ->  L2-OBS (KPI day/night split)
      (outcome_kpi day/night buckets ride solar_phase; until 186 corrects the 13:00-hardcoded solar noon, the day/night split is built on a contaminated phase (PR #385 finding P2-correctness). Solar parity must apply before the KPI scoreboard is trusted for the float trial.)
L4-DATA (migration 186 NOAA solar parity)  ->  L1-CLIMATE (anchor reassessment #361)
      (#361 explicitly: do not retune diurnal anchors before DB solar parity — anchor changes made against the seasonally-wrong solar phase tune against a contaminated measurement surface. Solar-186 apply gates any anchor work.)
L4-DATA (cycle-count reconcile, L4-S3)  ->  L2-OBS (trustworthy KPI scoreboard #371)
      (daily_summary.cycles_* are inflated 30-140x on partial/future days; L4 supplies the v_equipment_runtime_daily TRUE-rising-edge reconciliation view and backfills the cycles_grow_light NULL gap. The MCP outcome_kpi() reader (mcp/server.py) consumes that trustworthy view — L2 wires it into the before/after gate and homepage.)
L2-OBS (trustworthy KPI scoreboard #371)  ->  L1-CLIMATE float-flip (#377)
      (The cycling/outcome scoreboard MUST be trustworthy before the float push, or #377 gets a FALSE rollback/success on the cycling axis (the #295 attribution-risk class). Predeclared SUCCESS/ROLLBACK thresholds (served-temp ≥95%, mister_center on-events/day not up >15%) are unmeasurable until cycles_* is reconciled.)
L2-OBS (trustworthy KPI scoreboard #371)  ->  L1-CLIMATE quiet-OTA (#299 mister dwell + #349/grow-light shoulder)
      (Every firmware OTA must be judged on outcome_kpi cycles_* before/after (the L5 NEW-D cycling deploy gate) so an OTA can never silently double cycling again. The trustworthy scoreboard gates the W2 OTAs' attribution.)
L2-OBS (KPI composite outcome surface #371)  ->  L3-PLANNER (outcome objective #365)
      (The planner's ADR-0004 reward = corridor OUTCOMES + actuator cost, computed from the L2 outcome composite. The planner cannot optimize on a composite that does not yet exist; #365 follows the KPI/composite surface.)
L1-CLIMATE float-flip (#377, observed 48-72h)  ->  L1-CLIMATE mister-dwell #299
      (Run the float trial FIRST and clean, observe overnight VPD and wet-equipment on-events, THEN land the mister dwell so the mister on-event reduction is attributable and not confounded with the float-flip's wet-actuation reduction.)
L1-CLIMATE mister-dwell #299  ->  L1-CLIMATE grow-light shoulder (#349/NEW)
      (Co-OTA: #299 and the grow-light shoulder fix bundle into ONE OTA to honor the ≤1-OTA/week firmware freeze rule (both are W2 cycling fixes, both source-ready behind L0-SCHEMA cfg_* rows).)
L1-CLIMATE float-flip (#377, clean baseline)  ->  L1-CLIMATE heat_dehum opt-in OTA (#383)
      (heat_dehum must ship as its OWN post-float OTA so the float benefit is measured uncontaminated; its before/after overnight-heat1 outcome check needs the clean float baseline AND live moisture telemetry (#327) — the climate_moisture_exchange block returns 0 rows until the gated OTA+ingestor/MCP deploy lands.)
L4-DATA / L1 moisture telemetry (#327, live via OTA+deploy)  ->  L1-CLIMATE heat_dehum opt-in (#383)
      (#383 VPD/dehum policy tuning is blocked until the moisture-exchange estimator is observable; the moisture KPI is INERT (0 rows) pre-deploy, so #383 cannot be validated until #327 telemetry is actually live in prod.)
L5-DEPLOY (service-restart guard fix NEW-A / L2 dup)  ->  L0-SCHEMA + all schema-touching PRs
      (The service-restart-drift-guard false-passes today (ci.yml:597 greps bare word 'service'); fixing it to require a structured 'Post-merge restart:' marker is the wire protocol for schema→runtime restart hygiene — must land before schema PRs rely on it to enforce verdify-mcp/verdify-ingestor bounce documentation.)
L5-DEPLOY (cycling deploy gate NEW-D)  ->  L1-CLIMATE all W2 OTAs
      (NEW-D adds a firmware-OTA deploy gate that judges every OTA on outcome_kpi cycles_* before/after (consumes L2 #371). It must exist before the W2 mister/grow-light/heat_dehum OTAs so the #295 silent-cycling-doubling regression class is structurally prevented.)
L6-HA (#382 iSCSI writer-wedge)  ->  L6-HA CNPG cutover (#245) AND any writer restart in W1/W2
      (#382 left the writer down ~10min on restart (iSCSI target-cap); it blocks #245 and means no ingestor/writer restart is scheduled lightly. The single-writer fence + any DB_HOST flip interlocks with #382 being resolved (safety-checked NAS fix).)
L6-HA (L6-S1 writer-absent PrometheusRule)  ->  L1-CLIMATE float-flip + W2 OTAs
      (The out-of-band writer-absent/split-brain alert (VerdifyESP32NoWriter / VerdifyESP32SplitBrain) is the safety net that catches a dead or doubled device writer during any device-affecting W1/W2 action; wiring it live into overlays/prod precedes risky device pushes.)
L8-PRODUCT (#118 setpoint-server 2nd-writer assertion)  ->  L1-CLIMATE grow-light OTA (#349/NEW)
      (verdify-setpoint-server is a confirmed SECOND device writer (grow-lights via HA at 192.168.30.107:8123). The channel-separation assertion (HA grow-light relays vs ESP32 climate relays) must be proven before the grow-light OTA so the OTA does not create a same-actuator double-writer.)
```

## Deploy & OTA sequence

CONTAINER (ArgoCD) DEPLOY — W0 is the next full CI/CD container promotion to prod. Order: (1) Merge the split PR #385 source bundle to main (heat_dehum + live float-flip CARVED OUT). The promotable container set per CLAUDE.md = api/mcp/ingestor/migrate/planner (setpoint-server + lab hand-pinned). What actually moves in W0: verdify-mcp (outcome_kpi() scoreboard + moisture-exchange reader), verdify-ingestor (moisture telemetry parse + entity_map), verdify-migrate (migration 186 baked into the image but NOT yet applied), and any planner/api image touched by the L0-SCHEMA tunable_registry rows. (2) Every push to main publishes digest-pinned images to GHCR (sha-<sha> + branch-main). (3) Run the prod-promote workflow (dispatch) → resolves :branch-main digests via imagetools, surgically bumps overlays/prod, opens a prod-promote PR (promote-diff-guard enforces digests-only); the change merges after required checks pass. (4) Operator runs the gated `argocd app sync verdify-prod-dark`. (5) Post-merge restart documentation (now CI-enforced by the fixed service-restart guard): bounce verdify-mcp and verdify-ingestor so they pick up the new schema/reader. CAUTION: do NOT restart the ingestor writer carelessly while #382 (iSCSI target-cap wedge) is open — the writer state PVC may not remount (~10min outage); the ingestor is currently on an emptyDir TEMP patch, and the single-writer Lease fence (#240, ARMED) plus the writer-absent alert (L6-S1) must be live to catch a wedge. PROD MIGRATION APPLY is W1, NOT W0: apply migration 186 to prod (safety-checked, rollback-wrap proof — non-self-transactional, safe under BEGIN..ROLLBACK; NO CONCURRENTLY/self-commit shape), record apply timestamp, then REFRESH MATERIALIZED VIEW CONCURRENTLY mv_band_curve so future-dated rows serve the corrected band.

FIRMWARE OTA — honors ≤1 OTA/week + 48h bake + execution safeguard + single-writer interlock. Current baseline on the device = b7a531b (#295 solar-phasing + dead-fixture). NO OTA in W0. W1 carries ONE live device action but it is a TUNABLE PUSH not an OTA: band_track_fraction 0.25→0 via set_tunable (safety-checked, preflight no-critical-alerts, cfg_band_track_fraction=0 readback confirm, predeclared rollback 0.25, 48-72h observation) — this consumes no OTA budget. W2 fires the OTAs, sequenced across weeks: OTA-1 (Week N) = #299 mister dwell-guard CO-OTA'd with #349/NEW grow-light solar-window shoulder fix (one OTA, two cycling fixes, replay-diff with documented THRESHOLD_PCT override for the intentional divergence + firmware-invariants + test-firmware artifacts; judged on the L2 trustworthy cycles_* before/after gate). 48h bake = the new binary runs 48h without the sensor-health sweep flagging a critical alert. OTA-2 (Week N+1, after bake) = #383 heat_dehum opt-in default-OFF + fog/mister anti-ping-pong hold — fired ONLY after the float baseline is clean AND #327 moisture telemetry is live in prod (the moisture KPI is inert/0-rows until the OTA+deploy lands), with an overnight-heat1 on-events before/after proof. Each OTA preflight queries the alerts table (no OTA while any severity='critical' open) and checks the 48h last-good.ota.bin mtime. Stress-window (outdoor_temp>85F/24h) is operator context, not a block. Single-writer interlock: confirm verdify-setpoint-server (2nd writer, grow-lights via HA) channel-separation (#118) before the grow-light OTA so the OTA never co-writes the same actuators.

## Lanes

### L1-CLIMATE — Floating-Corridor Control (firmware)
- **Worktree:** `lane/climate-floating-corridor` · **Safeguard:** firmware preflight and rollback · **Milestone:** Greenhouse Control Optimization
- **Depends on:** L0-SCHEMA · **Owns:** firmware/**
- **Covers:** 377, 299, 383, 367, 368, 369, 370, 361, 323, 324, 326, 300, 291, 349, 14, 31

L1-CLIMATE owns the ESP32 control lane and carries the user's #1 priority: QUIET THE GREENHOUSE (reduce device cycling/runtime) under ADR-0004's floating-corridor regime (float inside the crop corridor, act only at edges with the cheapest minimal dose, treat cost/cycling as first-class). Verified ground truth: band_track_fraction=0.25 is LIVE (pinch armed; cfg readback confirmed in prod), so the float-flip to 0 (#377) is a real safety-checked lever, not a no-op. The three misters (mister_center/south/west) are NOT in the firmware R[] dwell array (controls.yaml:92-99); their relays are toggled directly by the pulse-timer FSM (controls.yaml:1336-1338) with only a per-zone VPD hysteresis floor and no min-on/min-off dwell — confirming the empirical chatter (mister_center 505 on-events/7d, 202 sub-60s, 79 sub-30s incl 0s pulses) while vent/fan/heat (which ARE in R[]) show 0-1 sub-60s. heat_dehum is hardcoded-active at greenhouse_logic.h:1580 with no enable tunable, adding overnight IDLE->heat1 actuation in the exact wet-night regime ADR-0004 wants idle — it must be carved out, gated OFF behind a cfg-backed tunable, and shipped as its own post-float OTA so the float trial stays clean. Grow-light cycling roughly doubled post-#295 (grow_light_main 360/grow_light_grow 485 on-events/7d) from dawn/dusk lux-shoulder + solar-window-boundary flapping that the 120s min-on only partly catches — a genuine NEW gap (the #349 epic is occupancy/Lutron-scoped, too broad). The lane sequences strictly to the firmware freeze budget (<=1 OTA/week + 48h bake + replay-diff/invariants/test artifacts), batching the quiet-the-greenhouse OTA (misters + grow-light shoulder) into one window and the heat_dehum opt-in into a separate post-float window. Source-only/no-OTA cleanups (#367/#369/#370/#300-firmware/#326-firmware/#368-firmware) ride behind the functional control sequence, touched only when replay/invariant coverage proves behavior unchanged. Depends on L0-SCHEMA for: migration 186 NOAA solar parity applied to prod (#293, prerequisite for trustworthy solar phase before float/anchor work), the per-zone band schema (#324 columns already landed in prod — verified zone/band_role/target_value populated), and registry/tunable rows + cfg readbacks for every new firmware tunable this lane introduces.

#### S1-QUIET — _As a greenhouse operator watching relay wear and electricity, I want the controller to stop chattering its misters, grow-lights, and wet-side actuators with sub-minute pulse-trains, so that device runtime and start counts drop materially (the user's stated #1 priority) without losing climate compliance or moisture delivery._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #377 | W1 | device-tunable-preflight | S | Float-flip: band_track_fraction source default -> 0 (corridor-keep) + live safety-checked trial | #293, #371, #324 |
| #299 | W2 | firmware-preflight | M | Mister dwell-guard: mister_min_on_s/mister_min_off_s + per-actuator max-cycles/hr governor | #377, #300 |
| #386 | W2 | firmware-preflight | M | Grow-light solar-window shoulder min-on bypass fix (post-#295 cycling regression) | #293, #300 |
| #367 | W3 | firmware-preflight | M | BC-8: settle ONE anti-chatter mechanism (arm the dwell gate OR consolidate to hysteresis-only) | #299, #377 |

#### S2-DEHUM — _As a control system protecting the orchids on wet nights without re-introducing overnight heat cycling, I want the overnight heat-assist dehum path to be opt-in (default OFF), anti-ping-pong, and proven on its own OTA AFTER the float trial is read clean, so that the float-flip benefit is not contaminated by a bundled new night actuator and any added overnight runtime is an explicit, measured ADR-0004 cost decision._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #383 | W3 | firmware-preflight | L | Carve out heat_dehum: gate it behind a cfg-backed opt-in tunable (default OFF) + fog/mister anti-ping-pong hold + own post-float OTA | #377, #327, #371, #293 |

#### S3-INTEGRITY — _As a control system that must not act on a bad reading once it floats more aggressively at corridor edges, I want tight sensor-input integrity (impossible-jump/rate-of-change rejection + flatline/stuck-in-range fault) and a consolidated, invariant-covered anti-fight equipment resolver, so that floating decisions are not corrupted by a spiking or stuck sensor, and the strongest safety interlock (heat<->air) is provably covered rather than living only in an untested lambda._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #368 | W3 | firmware-preflight | M | BC-9: sensor input integrity — jump/rate-of-change rejection + flatline (stuck-in-range) -> SENSOR_FAULT | #377 |
| #370 | W3 | firmware-preflight | M | BC-11: move heat<->air interlock into pure resolve_equipment + invariant; collapse THERMAL_RELIEF / unify fog-assist if replay-diff=0 | #367 |
| #14 | W3 | firmware-preflight | M | Firmware digital-twin coverage: keep replay_emit/twin source byte-identical across this lane's firmware changes (epic anchor) | #31 |
| #31 | W0 | none | M | TWIN-3 (P0): close the setpoint-coverage gap so the twin divergence metric is trustworthy | — |

#### S4-CLEANUP — _As a future control engineer reading the firmware, I want the registry/doc drift, dead tunables, and stale solar/anchor comments cleaned up AFTER solar parity and float evidence land, so that the drift guards stay the wire protocol, the planner stops pushing no-op params, and anchor decisions are made against a trustworthy (solar-corrected, floated) measurement surface rather than a contaminated one._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #300 | W0 | none | M | Firmware-side registry/doc-drift cleanup: dusk/night cfg readbacks, vent-bypass clamp, stale solar/anchor comments | #324 |
| #291 | W0 | none | S | Verify firmware consumes dispatcher-pushed dusk/night/fog-window + dawn anchor (firmware-side acceptance) | #300, #293 |
| #326 | W0 | none | S | Firmware-side confirmation: the 5 consolidated stress-wet params have no live firmware entities (purge is registry/planner side) | — |
| #369 | W3 | firmware-preflight | S | BC-10: firmware-side dead-code purge (stripped wet-stress entities / benign setpoint_unconfirmed tail / residual suppression knobs) | #326, #361 |
| #361 | W3 | firmware-preflight | M | Reassess diurnal anchors ONLY after solar parity (#293) + float evidence (#377); delete inert bias_heat/cool, do not reintroduce target-distance | #293, #377, #371 |
| #323 | W3 | firmware-preflight | L | Settable zone-priority ranking + mister-router arbiter (Vanda>Cannabis>Lime>Pepper) | #324, #291, #299 |
| #324 | W0 | none | S | Per-zone deterministic band emitter -> setpoint_snapshot/setpoint_changes (schema landed; firmware/emitter consumption remains) | #300 |

### L2-OBS — Observability & KPIs
- **Worktree:** `lane/observability-kpis` · **Gate:** source-only (no device) · **Milestone:** Greenhouse Control Optimization
- **Depends on:** L0-SCHEMA, L4-DATA · **Owns:** mcp/server.py, grafana/**, deploy/k8s/components/grafana/**, verdify_schemas/mcp_responses.py
- **Covers:** 371, 348, 89, 75, 49, 215

L2-OBS owns the SCOREBOARD that gates every device change in W1/W2: the read-only outcome_kpi() MCP tool (mcp/server.py:449), its pydantic contract (verdify_schemas/mcp_responses.py OutcomeKpiResponse/Coverage/ActionRow), the Grafana dashboard surfaces (grafana/dashboards/*.json, deploy/k8s/components/grafana/**), plus the supporting health/monitoring and alert/ingestor-hygiene issues. The lane is overwhelmingly W0 source-only (no device gate): it must produce a TRUSTWORTHY cycling/outcome scoreboard BEFORE the float-flip (#377, L1) and any quiet-OTA (#299/#349/#383) so an OTA cannot silently double cycling the way #295 did (grow_light 4-6/day -> 30-44/day, ungated). VERIFIED ON PROD: outcome_kpi's actuator_cycles dict is sourced from daily_summary firmware counters (db comment: 'Firmware daily_mister_center_cycles counter, reset at local midnight'), which AGREE with v_equipment_runtime_daily edge counts on COMPLETED days but diverge sharply on the partial current day (2026-06-22 fw_fog=299 vs edge_fog=86, ~3.5x) and on FUTURE-DATED daily_summary rows (2026-06-23 fw_fog=275 with ZERO equipment_state edges) — this is the '30-140x inflated' scoreboard the lead flagged. There is already a canonical edge-counting view, v_equipment_runtime_daily (db/schema.sql:27418, 'cycles count TRUE rising edges only'), so the #371 reconciliation is well-defined: source cycles from edges, exclude partial/future days, expose a cycles_source discriminator. Secondary verified findings: outcome_kpi runs 7 SEQUENTIAL awaits with no asyncio.gather and pinched_row(556)/phase_rows(743) are duplicate ~6-LATERAL CTE blocks (cost/cold-cache risk before LLM exposure); the service-restart-drift-guard (ci.yml:597) is a keyword false-match that passes on any PR body containing 'service'; #49's 61 suppressed orphan sensor_offline rows are still live; #215 dead-host polls (.150:9100 retired iris node, .108:9400 immich) are still in ingestor/tasks/_common.py. Lane stays gate:none — it never touches the device, prod migration, or ArgoCD sync; it depends on L0-SCHEMA for any migration/view it adds and L4-DATA for the #327 moisture-estimator telemetry that fills the currently-'pending' moisture coverage axis.

#### S1-TRUSTWORTHY-SCOREBOARD — _As a control engineer about to run the band_track_fraction=0 float trial and a quiet-OTA campaign, I want a cycle/runtime/outcome scoreboard whose cycling numbers are reconciled against raw equipment_state edges and never inflated by partial or future-dated daily_summary rows, so that I can attribute every device-policy change to a real reduction in actuations and avoid a FALSE rollback or FALSE success on the cycling axis (as #295 silently doubled grow_light cycling with nobody gating on it)._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #371 | W0 | none | M | Reconcile outcome_kpi actuator_cycles against v_equipment_runtime_daily edges; exclude partial/future days; add cycles_source discriminator | #293, L0-SCHEMA |
| #371 | W0 | none | M | Wire cycles/corridor/outcome KPIs into a before/after deploy gate and a homepage served-corridor/float view; deprecate the target-distance tent | #371, #293, #327, L4-DATA |
| #387 | W0 | none | S | outcome_kpi() performance hardening: parallelize the 7 sequential awaits and de-duplicate the pinched_row/phase_rows LATERAL CTEs before LLM exposure | #371, S1-TRUSTWORTHY-SCOREBOARD |

#### S2-MOISTURE-DLI-COVERAGE — _As a control engineer deciding whether overnight heat_dehum and VPD/dehum policy changes are justified, I want the outcome scoreboard's moisture-estimator and DLI axes to read real telemetry with explicit coverage/validation, not silently-empty or physically-implausible values, so that I never grade a VPD/dehum or heat_dehum change against an inert or contaminated measurement surface._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #371 | W0 | none | M | (consume) Expose moisture-estimator telemetry through outcome_kpi/MCP/dashboard and bind a drift guard to the firmware moisture JSON keys | #327, L0-SCHEMA, L4-DATA |
| #371 | W0 | none | S | Validate/clamp dli_final and the day/night solar_phase split so outcome_kpi cannot emit physically-implausible DLI | #293, #371, L0-SCHEMA |

#### S3-HEALTH-DRIFT-GATES — _As a operator deploying MCP/ingestor containers and the single-writer device route, I want post-deploy smoke + device-route monitoring and a restart-hygiene guard that actually works, so that a deployed image is provably image==source, exactly one writer is connected to the ESP32, and schema changes can't ship with stale runtime because nobody bounced the service._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #348 | W0 | none | S | L6 observability epic: track the KPI/dashboard/health deliverables and close the action checklist | #371, #89, #75 |
| #89 | W3 | none | M | Post-deploy smoke gate + device-route ESTAB==1 monitor wired into the prod-promote/cutover runbook | #75 |
| #75 | W3 | none | S | Observability & health epic: smoke/metrics/device-route monitor umbrella | #89, #348 |

#### S4-ALERT-INGESTOR-HYGIENE — _As a operator reading raw alert/ingestor queries and dashboards, I want the alert lifecycle reconciled and the ingestor's dead-host polls removed, so that resolved_at IS NULL agrees with the canonical open-alert view and the ingestor logs/metrics are clean of decommissioned-host noise that masks real failures._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #49 | W3 | none | S | Backfill resolved_at on the 61 historical suppressed orphan sensor_offline rows | L0-SCHEMA |
| #215 | W3 | none | S | Repoint ingestor dead-host polls (.150:9100 retired iris node, .108 immich GPU) to in-cluster k3s targets | L4-DATA |

### L3-PLANNER — Planner / GenAI — outcome objective (stop chasing the target, optimize corridor outcomes minus cost)
- **Worktree:** `lane/planner-outcome-objective` · **Gate:** source-only (no device) · **Milestone:** Greenhouse Control Optimization
- **Depends on:** L0-SCHEMA, L2-OBS · **Owns:** ingestor/iris_planner.py, mcp/server.py, prompts/**, planner_graph/**, scripts/gather-plan-context.sh, scripts/forecast-action-engine.py
- **Covers:** 365, 214, 210, 379

L3-PLANNER pivots the AI planner (Hermes/iris_planner) from ADR-0003 target-distance optimization to the ADR-0004 floating-corridor outcome objective: grade and reward OUTCOMES inside the served corridor (time-in-band x DLI x DIF x wet/dry-completion) minus a cost/cycling penalty, and stop spending water/energy/wear to make in-corridor air hug the target line. The lane is almost entirely WAVE-0/W1 source-only (no execution safeguard) because the safety boundary is MCP+registry, not Hermes (ADR-0002): the planner can only ever WRITE band_track_fraction=0 and bounded ClimateIntent, so its reward function is observability-grade, not control-grade.

Verified ground truth from owns_paths: (1) The planner reward (planner_score 80%-compliance half) reads compliance_v2_attributable_pct via fn_planner_scorecard, surfaced through gather-plan-context.sh — that is graded target-compliance, NOT the ADR-0004 outcome composite. The composite outcome score does NOT exist yet: daily_summary has cycles_*/dli_final/DIF columns but no composite, no wet/dry-completion axis, no cost penalty. (2) The MCP outcome_kpi() tool emits the raw ADR-0004 components but has NO consumer — the planner reads SQL via gather-plan-context.sh, never this tool (PR #385 finding: measurement surface shipped ahead of #365). (3) The prompt already carries ADR-0004 language partially (iris_planner.py:189-219, 422-423) and band_track_fraction is registry-constrained to planner-write-0 with cfg___band_track_fraction readback — so the prompt/registry copy is half-done, the reward swap is the real gap. (4) #214 is LIVE: iris_planner.py:987 _run_alert_sql STILL shells `docker exec -i verdify-timescaledb psql`, which does not exist in k3s, so every plan_context_failed alert silently fails (caught + warning-logged only) — exactly the #214 silent-failure bug; #211 (its gather_context VM-path blocker) is now CLOSED so the deviation->set_plan path is unblocked. (5) #210 forecast_actions subprocess path is ALREADY FIXED (forecast.py:33-39 B4 comment + _FORECAST_ACTION_ENGINE = REPO_ROOT/scripts, no VM venv); residual is the heartbeat MCP auto-restart subprocess (heartbeat.py:1097) + live verification that forecast_action_log is writing again. (6) #379 MPC is correctly DEFERRED — it must consume #293 solar parity + #371 outcome history + #327 moisture telemetry + #377 float-trial evidence first, or it re-optimizes the rejected target-hugging proxy.

Sequencing discipline: the outcome reward swap (#365) DEPENDS on L2-OBS landing the trustworthy outcome surface (#371: composite score DB fn + cycle-count reconciliation against raw equipment_state — daily_summary cycles_* are demonstrably inflated 30-140x on partial days and would give a false reward). Schema-first holds: any new planner-visible KPI/score property lands as a schema-only change with pydantic + drift-guard before the planner consumes it. No firmware, no OTA, no prod-migration in this lane (those are L0/L1/firmware lanes); the planner reads the surfaces those lanes produce.

#### L3-S1 — _As a control system whose only authority is bounded tunables and ClimateIntent (ADR-0002), I want my optimization objective to reward crop OUTCOMES inside the served corridor minus actuator cost/cycling, not distance to the target line, so that the AI stops spending water, energy, and relay wear to make in-corridor air hug the target curve — the single largest source of the cycling the operator is trying to eliminate._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #365 | W1 | none | L | Swap the planner reward + prompt + MCP/registry guidance from target-compliance to the ADR-0004 outcome composite | #371, L3-S2 |
| #388 | W0 | none | M | Schema-only: add the ADR-0004 composite-outcome-score property to ScorecardResponse + planner-io contract with pydantic + drift guard | L0-SCHEMA, #371 |

#### L3-S2 — _As a operator who needs the AI to act on real off-schedule deviations instead of failing silently, I want the crop-deviation dynamic-planning path to detect, plan, deliver, AND make its failures visible in alert_log, so that a genuine VPD/wind/heat forecast deviation produces a delivered off-schedule set_plan rather than ending acked or dying invisibly at gather-context._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #214 | W0 | none | M | Fix the silent plan_context_failed alert path (drop the docker-shell) and prove a deviation yields a delivered set_plan | #211 |
| #210 | W0 | none | M | Verify/close the forecast_actions + Tempest-sync cutover regression and clean up the residual MCP auto-restart subprocess | — |

#### L3-S3 — _As a control engineer who wants forecast-anticipatory MPC eventually, but only on a trustworthy outcome substrate, I want the actuator-aware grey-box ID -> MPC work explicitly deferred behind solar parity, outcome history, moisture telemetry, and clean float-trial evidence, so that the model is trained on actuator-aware outcome labels and does not re-optimize the same target-hugging proxy ADR-0004 rejected._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #379 | W3 | none | L | Hold FLOAT-3 actuator-aware grey-box ID -> MPC until its evidence prerequisites land (DEFER, scoped) | #293, #371, #327, #377 |

### L4-DATA — Data / DB / Solar parity
- **Worktree:** `lane/data-db-solar` · **Safeguard:** migration preflight and rollback · **Milestone:** Greenhouse Control Optimization
- **Depends on:** L0-SCHEMA · **Owns:** db/**, ingestor/**, verdify_schemas/**
- **Covers:** 293, 327, 347

L4-DATA owns the DB/schema/ingestor source-of-truth so that every downstream control surface (band phase, lighting windows, outcome KPIs, VPD/dehum policy) reads correct, year-round solar phase and an observable moisture-estimator context. The headline deliverable is the GATED apply of migration 186 (NOAA solar-phase parity) to prod, which fixes the verified fn_solar_altitude() bug that hardcoded solar noon at 13:00 local (~2 min off in June MDT, but ~62 min late at winter solstice) and re-baselines band-grade/compliance KPIs. Verified on disk: the source-side of #293 is COMPLETE in PR #385 (db/migrations/186-noaa-solar-phase-parity.sql + db/schema.sql mirror already NOAA + tests/test_db_solar_sql_contract.py + docs/reviews/diurnal-solar-cycle-math-review-2026-06-23.md); the migration classifies "ok (safe-to-wrap)" via scripts/check_migration_rollback_safety.py (non-self-transactional, CREATE OR REPLACE FUNCTION only, no CONCURRENTLY). What remains is the W1 gated prod apply + REFRESH of mv_band_curve/mv_zone_band_grade + recording the apply timestamp as a KPI regime break. The moisture-estimator telemetry (#327) is also code-complete in PR #385: firmware (b7a531b, OTA-live) already publishes climate_moisture_exchange JSON with keys action/reason/vent_vpd_gain_kpa/heat_vpd_gain_kpa/outdoor_fresh/vent_overcools/heat_assist_corun/heat_assist_active/heat_assist_timer_s (controls.yaml:1687-1700), the ingestor parses+persists it into climate_action_log.source_system_state JSONB (existing column, no new migration), and MCP reads it via ->> extractions (mcp/server.py:889-995) — but the read path is INERT in prod (0 rows) until the ingestor image is promoted, AND there is no drift guard binding the firmware JSON keys to the MCP reader's hardcoded extractions (the verified P2 gap that lets a key rename silently null the moisture KPIs). The lane closes three real bugs the lead verified beyond the seeds: (1) outcome_kpi's cycle counts read daily_summary.cycles_* — firmware per-day counters reset at local midnight — which are inflated 30-140x on partial/recent days and would cause a FALSE rollback/success on the cycling axis of the #377 float trial; the trustworthy source is v_equipment_runtime_daily (TRUE rising edges from raw equipment_state). (2) the 3-way lat/lon/zenith constant duplication (firmware/lib/greenhouse_solar.h, ingestor/solar.py, db migration 186) has no single-source-of-truth record. (3) #43 site_content RAG refresh is ALREADY implemented+scheduled (ingestor/ingestor.py:1997, 86400s) — it is verify-and-close, not new build. The lane is the schema-FIRST foundation other lanes depend on: it must land its source changes and apply migration 186 before the float-flip (#377) and VPD/dehum policy (#383) lanes can trust their measurement surface.

#### L4-S1 — _As a control system reasoning about the diurnal crop band, I want the DB's solar phase/sunrise/noon/sunset helpers to match the firmware/ingestor NOAA contract year-round, applied to prod with the cached band-curve surfaces refreshed, so that band tracking, lighting windows, and every compliance/outcome KPI grade against the correct solar geometry instead of a 13:00-hardcoded noon that is ~1h late at winter solstice._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #293 | W1 | prod-migration-preflight | M | Apply migration 186 (NOAA solar phase parity) to prod + REFRESH band-curve matviews + record KPI regime break | #347 |

#### L4-S2 — _As a operator and AI planner deciding VPD/dehum policy at the corridor edges, I want the firmware moisture-exchange estimator context (action, reason, projected vent/heat VPD gains, outdoor freshness, overcool risk, heat-assist state) persisted, drift-guarded, and queryable in prod, so that overnight low-wet idle cases, fog/dehum ping-pong, and dry-side venting side-effects can be explained by estimator reason instead of looking like policy guesses, and the #383 VPD/dehum tuning has a real measurement surface._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #327 | W3 | prod-sync-preflight | L | Land moisture-estimator telemetry source + drift guard; activate it in prod via gated ingestor promotion | #293, #347 |

#### L4-S3 — _As a control system whose #1 directive is to reduce cycling and minimize device runtime, I want a trustworthy cycle-count source that counts real on-edges from raw equipment_state instead of partial-day firmware counters, so that the float-flip trial (#377) and every future OTA can be judged on actual cycle reduction without a false rollback or false success on the cycling axis._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #389 | W0 | none | M | Reconcile outcome_kpi cycle counts against raw equipment_state on-edges; exclude partial/future-dated days | #293 |

#### L4-S4 — _As a data/schema owner and a future agent session, I want a complete read/write authority matrix for every greenhouse value plus closure of the already-shipped RAG refresh, with the L5 epic kept as the umbrella, so that there is no ambiguity over who owns each value, drift detection is meaningful, and stale-RAG/duplicate-source-of-truth findings cannot silently reopen._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #347 | W0 | none | L | EPIC L5: read/write authority matrix + firmware-canonical drift detection (umbrella for this lane) | — |

### L5-DEPLOY — Deploy Enablement / CI-CD
- **Worktree:** `lane/deploy-cicd` · **Gate:** source-only (no device) · **Milestone:** Deploy Enablement (agent access + firmware CI/OTA)
- **Depends on:** none · **Owns:** .github/workflows/**, deploy/**, Makefile, scripts/**
- **Covers:** 288, 303, 304, 319, 320, 335, 336, 317, 318, 338, 322, 339, 330, 332

This lane builds the CI/CD + firmware-OTA pipeline that the cycling/float control program (lanes L1-L4, ADR-0004) must ride on, and closes the guard gaps PR #385's independent validation surfaced. It is pure infra/CI/manifests/tooling — gate=none, no device action — so it can run W0 in parallel with every other lane, with two exceptions explicitly safety-checked (the ESP32 OTA-password SealedSecret #301 and any prod ArgoCD operation). The headline insight from the replan evidence: #295 proved an OTA can silently DOUBLE cycling (grow_light 4-6 -> 30-44 toggles/day) because NO CI gate judged cycle count, and three existing guards FALSE-PASS — service-restart-drift-guard (ci.yml:597 greps the bare word 'service', present in ~every PR body), the firmware 'compile' job (only runs esphome config validation, never a real compile, and floats the esphome version via unpinned pip install), and there is NO gate binding the generated Grafana dashboard ConfigMaps to their JSON source (the manual-regen invariant #318 depends on). This lane hardens all three, stands up a REAL pinned firmware-compile lane (#303) and the in-pod tooling image (#304) so L2/L3 firmware OTAs (#299/#349/#383) have a reproducible build->bake->OTA path, fixes the prod-promote migrate-rebuild race (#319) and the Actions-PR-creation org blocker (#320), adds a single-source-of-truth guard for the now-triplicated solar site constants (lat 40.167 / lon -105.102 / zenith 90.833 across ingestor/solar.py, firmware/greenhouse_solar.h, db/migrations/186), modernizes the test suite off the destroyed iris-VM stack (#322/#339) so `make test` is meaningful again, brings Actions to Node 24 (#338), and triages the ArgoCD/GitOps cleanup (#336/#317/#318 + verdify-prod-dark rename). Cross-lane: the cycling-KPI deploy gate this lane builds (NEW) consumes the L1 outcome_kpi cycles_* surface (#371) so future firmware OTAs are regression-gated on runtime/cycling, not just band-compliance. Two seed items are downgraded as non-actionable: #332 (Fable) is a close-as-not-planned clarification, #330 is an archived-branch review chore. Covers all 16 lane-scoped issues; #382/#245 storage-infra explicitly NOT taken (separate safety-checked infra lane per the replan deferred list).

#### S1 — _As a greenhouse control engineer shipping firmware that changes how often relays fire, I want a reproducible, pinned firmware build->config->replay->compile->bake->OTA pipeline in CI and in the agent pod, plus a cycling-KPI gate that judges every OTA on runtime/cycle-count before it can be promoted, so that the L2/L3 anti-chatter OTAs (mister dwell #299, grow-light shoulder #349, opt-in heat_dehum #383) can be built and behaviorally proven the same way every time, and no future OTA can silently double cycling the way #295 did (grow_light 4-6 -> 30-44 toggles/day with nobody gating on cycle count)._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #303 | W0 | none | M | Real pinned firmware-compile CI lane + portable esphome paths | — |
| #304 | W0 | none | M | Agent-pod tooling image: pinned esphome + kubectl + psql client | #303, #301, #302 |
| #390 | W1 | none | M | Cycling/runtime deploy gate — judge every firmware OTA on outcome_kpi cycles_* before/after | #371, #322 |

#### S2 — _As a operator who relies on green CI gates to mean something before a merge or promotion, I want the three false-passing CI guards fixed (service-restart, firmware-compile-vs-validate already in S1, generated-CM regen) and a single-source guard for the now-triplicated solar site constants, so that a passing PR genuinely proves schema-restart documentation exists, the rendered Grafana ConfigMaps match their JSON source, and the lat/lon/zenith constants can never drift apart across firmware/ingestor/DB and silently re-baseline the band curve._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #391 | W0 | none | S | Fix service-restart-drift-guard false-pass (greps bare word 'service') | — |
| #392 | W0 | none | S | Generated-CM == dashboard-source CI gate | — |
| #393 | W0 | none | M | Single-source-of-truth + drift guard for solar site constants | — |
| #338 | W0 | none | S | Bring GitHub Actions to Node 24 runtime default | — |

#### S3 — _As a operator who promotes images to the single prod environment behind the device-write gate, I want the prod-promote pipeline race fixed, the Actions-PR-creation org blocker resolved (or explicitly safety-checked), and the ArgoCD/GitOps cleanup (selective-sync bugs, SSA, the verdify-prod-dark rename) triaged with evidence and a safe plan, so that a quiet pipeline reliably opens a digests-only prod-promote PR that merging can proceed after required checks pass, and the prod ArgoCD app is reviewable, drift-triaged, and renamed without orphaning the live greenhouse writer._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #319 | W0 | none | S | Fix prod-promote migrate rebuild-on-every-publish race | — |
| #320 | W0 | infra-preflight | S | Resolve Actions-cannot-create-PR org blocker for prod-promote | — |
| #317 | W2 | prod-sync-preflight | M | ArgoCD: plain full syncs on verdify-prod-dark get rewritten to a stale 2-resource selective scope | — |
| #318 | W2 | prod-sync-preflight | M | Make per-resource ServerSideApply durable for the large grafana dashboard CMs | #339 |
| #336 | W2 | prod-sync-preflight | L | ArgoCD/GitOps cleanup epic: app manifests, staging/live-branch retirement, verdify-prod-dark rename | #317, #318 |
| #335 | W0 | none | L | EPIC: CI/CD and Promotion Hardening (umbrella) | #303, #319, #320, #322, #338 |
| #288 | W2 | firmware-preflight | L | EPIC: Deploy Enablement — agent k3s/DB access + firmware CI/OTA + secret sealing (umbrella) | #303, #304 |

#### S4 — _As a agent (or human) running `make test` on a clean checkout to know whether the repo is actually healthy, I want the test suite and local validation modernized off the destroyed iris-VM stack and the archived-branch / Fable-ownership chores closed out, so that a red `make test` means a real regression (not ~150 environmental failures asserting dead Docker/systemd/localhost:5432), and the lane's housekeeping items are resolved or explicitly closed._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #322 | W0 | none | M | Overhaul test suite off the destroyed iris-VM stack (Docker/systemd smoke tests) | — |
| #339 | W2 | prod-sync-preflight | M | Clean up retired-VM assumptions in local validation + triage prod ArgoCD drift | #322 |
| #330 | W3 | infra-preflight | S | Review archived branches from the 2026-06-12 repo cleanup | — |
| #332 | W3 | none | S | Clarify (and likely close) Fable workstream ownership in verdify-platform | — |

### L6-HA — HA / Reliability / Infra
- **Worktree:** `lane/ha-reliability` · **Safeguard:** exact-target infra preflight · **Milestone:** M7 — HA: first-principles resilience
- **Depends on:** none · **Owns:** deploy/k8s/**
- **Covers:** 225, 218, 245, 238, 235, 382, 207, 114

L6-HA is the Track-A reliability lane: keep the single ESP32-writer datapath and the verdify-db system-of-record durable and survivable, in parallel with (never blocking, never lightly restarting against) the climate-control sprint. Verified repo state: ha-1 (4-tier PriorityClasses, PDBs, serving CPU-limits, ingestor liveness/anti-affinity) and ha-3 (Lease-fence + fast-failover patch + split-brain alarm, all STAGED/gated) source has LANDED; the nightly pg_dump CronJob now has netpol-race-retry and is wired into both prod overlays but is UNVERIFIED-green; the descheduler (#234) is authored but wired into NO live overlay/ArgoCD app; WAL archiving is OFF and there is no standby (#218 RED); the prod CNPG cluster (#244) does not yet exist in repo and the cutover (#245) is hard-blocked by the live #382 iSCSI target-cap wedge that today keeps the writer's state on a committed emptyDir TEMP patch. The single highest-leverage unblock is the storage-infra/safety-checked iSCSI cap fix (#382) — until it lands, every writer restart is a 5-10 min outage and #245 cannot proceed. This lane sequences: (Story A) close the active #382 storage wedge + restore durable ingestor state + add the missing out-of-band writer-absent/split-brain alerting and the never-verified backup proof; (Story B) get verdify-db to a real recoverable posture short of the risky cutover — interim WAL archiving + a practiced restore drill; (Story C) the gated CNPG HA program (prod cluster build + the riskiest atomic cutover), strictly downstream of #382; (Story D) ship the authored-but-dormant resilience surfaces (descheduler arm, edge HA hooks that DO live in deploy/k8s) and close platform drift. Cross-lane: #238 (traefik/metallb) and #235 (pihole) per their own bodies file in network-infra/cluster-infra, OUTSIDE deploy/k8s — flagged for the cluster-infra owner, not owned here. #114 is STALE (single-env decommissioned dev/stage) and is recommended for closure.

#### L6-S1 — _As a operator of the single-writer greenhouse datapath, I want the live iSCSI storage wedge closed so that any ingestor/DB restart remounts durable state in seconds, with out-of-band alerting that tells me the instant the writer is absent or doubled, so that a routine pod restart or node flap can no longer silently strand the sole ESP32 writer for 5-10 minutes on ephemeral state, and a backup I never verified can no longer be a false safety net._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #382 | W3 | infra-preflight | M | Close the iSCSI target-cap wedge: confirm DSM fix, revert ingestor-state emptyDir TEMP patch back to the durable PVC, verify remount | — |
| #394 | W3 | infra-preflight | M | Out-of-band writer-absent + split-brain alert: ship the VerdifyESP32SplitBrain/NoWriter PrometheusRule live (HA-3.3 #241 wiring) | — |
| #395 | W3 | infra-preflight | S | Prove the nightly pg_dump backup actually runs green + add a backup-freshness alert (HA-1.8 #233 verification gap) | — |

#### L6-S2 — _As a operator who needs the greenhouse system-of-record to be recoverable without a 7-day-data-loss window, I want a real point-in-time-recovery posture for verdify-db short of the riskiest live cutover — interim WAL archiving to existing NFS plus a practiced restore drill, so that I have a tested sub-restore-window RPO and a proven RTO before committing to the gated CNPG flip, instead of an unbounded-RPO single replica with only nightly dumps._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #218 | W3 | prod-migration-preflight | L | Interim DB durability: enable WAL archiving to the existing verdify-db-dumps NFS + run a practiced PITR restore drill (no new iSCSI, no cutover) | L6-S1 |

#### L6-S3 — _As a operator targeting true DB high availability (kill-primary < 30s RTO, RPO=0) for the greenhouse system-of-record, I want the CloudNativePG HA program staged and the gated atomic live-DB cutover executed only after the storage wedge is fixed and parity is proven, so that the single-replica TimescaleDB stops being a single point of total data loss, while the riskiest flip in the program stays strictly behind its storage prerequisite and a bounded, drilled rollback._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #396 | W3 | prod-migration-preflight | L | HA-4.2 (#244): author the PROD CNPG cluster (1+2 sync + Barman WAL/PITR) alongside live verdify-db | #382, L6-S1 |
| #245 | W3 | prod-migration-preflight | L | HA-4.3: gated atomic live-DB cutover to CNPG (quiesce-writer → flip DB_HOST → bake → decom) [GATED, riskiest] | #382, #244, #218, L6-S3 |

#### L6-S4 — _As a operator who wants the authored-but-dormant resilience surfaces actually live and the platform-drift backlog scoped to a real owner, I want the descheduler armed safely, the in-deploy/k8s edge-HA hooks shipped, and the cross-lane edge/DNS SPOFs + stale dev/stage tickets correctly routed or closed, so that the resilience work that already exists in the repo is operating rather than sitting STAGED, and the board reflects what this lane actually owns versus what belongs to cluster-infra._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #225 | W3 | infra-preflight | M | Arm the descheduler safely (HA-1.9 #234) + wire it into a live ArgoCD-managed surface; track the HA epic to closure | — |
| #238 | W3 | infra-preflight | S | traefik-apps PDB/priority + metallb-controller 1→2 — ROUTE to cluster-infra/network-infra (out of deploy/k8s ownership); ship only the in-namespace verdify-traefik PDB half here | — |
| #235 | W3 | infra-preflight | S | pihole DNS SPOF (HA-2.1) — ROUTE to network-infra/nexus (pihole is a cluster-infra workload, not in deploy/k8s) | — |
| #207 | W3 | infra-preflight | S | Close the infra slice of the SOTU platform-drift checklist (deploy/k8s ArgoCD/observability rows) | L6-S1 |
| #114 | W0 | none | S | CLOSE as stale: dev/stage MQTT read-only ingest — moot under single-env-prod (dev/stage decommissioned) | — |

### L7-IRRIG — Irrigation & Hardware (operator-scoped)
- **Worktree:** `lane/irrigation-hardware` · **Safeguard:** physical-target verification · **Milestone:** Hardware / Seasonal (operator-scoped)
- **Depends on:** L0-SCHEMA · **Owns:** firmware/**, ingestor/**, db/**
- **Covers:** 350, 296, 297, 298, 37, 45, 51, 52, 16

L7-IRRIG owns the plant-irrigation/fertigation control path (separate from climate wetting), the soil-feedback closed loops, probe/topology re-baselining, and the operator-scoped hardware/seasonal installs. Grounding-verified against prod source: the irrigation drip FSM (controls.yaml IRR-3/IRR-4 region) is pure clock/day-mask/air-VPD with the soil-feedback hooks explicitly stubbed (TODO "DEFERRED, NOT IMPLEMENTED"); soil_moisture_targets (mig 064) seeds saturation_pct and v_soil_status already computes a 'saturated' status, but NOTHING evaluates it — the only soil alert is soil_dryout in ingestor/tasks/alerts.py, so the saturation/overwatering alert (#297) is a genuine, no-OTA, ship-now gap that directly serves the user's "everything is very wet" pain. The soil_moisture_targets and config/zones.yaml topology still carry stale 'Canna Lily'/'Unknown pots' seed rows (#298 re-baseline confirmed valid). irrig_center_start_hour baked default is still 10 (globals.yaml:1452) with a 10-fallback boot-clamp (greenhouse.yaml:91) despite the dispatcher pushing 06:30 (#37). The cfg_irrig_center_start_hour readback already exists (TunableDef present), so #37 changes only the default — no new property. The bulk of this lane is blocked on physical probe/sensor/relay installs; the software consumers (finalizer, acceptance loop, sub-FSM scaffolding) are designed and can land source-first. CRITICAL ordering: this lane must NOT compete with the cycling/float critical path (L6/firmware-control). #296's firmware sub-FSM and #37's default change are OTA-budget consumers (<=1 OTA/week + 48h bake) and MUST be co-bundled or sequenced AFTER the higher-priority mister-dwell/grow-light/float OTAs land. The mister chatter fix (#299) is climate-mister-path, NOT this lane's irrigation-drip path — explicitly out of scope here. Schema changes (saturation alert threshold, topology re-baseline migration, dormancy column) land first via L0-SCHEMA serialization; next free migration number is 187 (186 is the latest). Wave assignment: the no-OTA observability/dispatcher loops and topology schema are W3 (parallel-reliability+irrigation+product); the firmware sub-FSM and the irrig default change are also W3 but explicitly gated behind the W1/W2 climate OTAs.

#### S1-ANTI-OVERWATER — _As a greenhouse operator whose dominant irrigation failure mode is over-watering ('everything is very wet'), I want a saturation/overwatering alert and a dispatcher-side drip-skip closed loop that act on the soil-moisture data we already collect, with no firmware OTA, so that saturated zones stop getting their next scheduled drench today, before any hardware or firmware change, and I get paged when a pot sits above its saturation threshold._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #297 | W3 | none | M | Saturation/overwatering alert + dispatcher drip-skip first closed loop (no OTA) | #298, S1-ANTI-OVERWATER |
| #397 | W3 | none | S | Day-mask trim runbook + cfg_irrig_*_days_mask readback audit (immediate non-code overwatering mitigation) | #297 |

#### S2-PROBE-TOPOLOGY-TRUTH — _As a control system that grades soil moisture against per-zone thresholds, I want the soil_moisture_targets, sensor_registry, zones topology, and config/zones.yaml to reflect the actual current physical map (lime tree, cannabis, hydro pot, homeless SEN0600) instead of stale Canna-Lily/Unknown-pots seed rows, so that every saturation/dryout/feedback evaluation grades against the crop that is actually in the pot, the stuck-zero south_1 is re-diagnosed against live data, and the irrigation-feedback bring-up can complete._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #398 | W3 | prod-migration-preflight | M | Data-truth hygiene: re-baseline stale soil_moisture_targets + zones.yaml seed rows ahead of the hardware probe moves | L0-SCHEMA |
| #298 | W3 | hardware-preflight | L | Probe/topology re-baseline: lime tree, cannabis, hydro pot, homeless SEN0600 (hardware moves + per-position import) | #45 |
| #45 | W3 | hardware-preflight | M | Run irrigation-feedback bring-up after south-1 probe repair + center feedback install | — |

#### S3-IRRIG-FSM-AND-DEFAULT — _As a ESP32 firmware controller that must water plants safely even on a fresh boot before the dispatcher pushes, I want the baked irrigation default to feed in the AM and a conservative slow-hysteresis soil-feedback irrigation sub-FSM that suppresses a scheduled drip when a zone is already saturated, so that a reset before the dispatcher push still feeds at 06:00 not mid-morning, and once probes are live the firmware itself stops over-watering a saturated zone — both sequenced to respect the <=1-OTA/week + 48h-bake budget and AFTER the climate cycling/float OTAs._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #37 | W3 | firmware-preflight | S | Change firmware default irrig_center_start_hour 10 -> 6 (+ boot-clamp fallback) | — |
| #296 | W3 | firmware-preflight | L | Soil-feedback irrigation sub-FSM (slow hysteresis; over-watering is the failure mode) | #298, #45, #297, #37 |

#### S4-FERTIGATION-DECISION-EPIC — _As a operator deciding how wall plants and orchids are watered and fertilized, I want the L8 horticulture/control decision surface resolved — fertilizer routing, wall-drip vs climate-mister split, and orchid fertilization cadence — so the irrigation lane has an authoritative target end-state, so that wall plants move to driphead-driven fertigation, center misters stay climate-only, and orchid care is explicitly manual-or-automated, not ambiguous._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #350 | W3 | hardware-preflight | M | EPIC L8: Irrigation, Fertilization, and Orchids — horticulture/control decision surface | #296, #297, #298, #45, #37 |
| #16 | W3 | hardware-preflight | S | EPIC: Hardware backlog (operator-scoped sensing/equipment installs) | — |
| #51 | W3 | hardware-preflight | S | CO2 two-point field calibration against a reference NDIR | — |
| #52 | W3 | hardware-preflight | M | SEA1: orchid dormancy phase + dormant lighting clamp + dry-down governance (fall/winter) | — |

### L8-PRODUCT — Product-Plane / Decommission / Auth
- **Worktree:** `lane/product-decommission-auth` · **Safeguard:** exact-target infra preflight · **Milestone:** Enablement: Decommission & Auth
- **Depends on:** none · **Owns:** deploy/**, site/**, scripts/populate-site-content.py, scripts/publish-site-content.sh, ingestor/tasks/ha.py, docs/SERVICE_MAP.md, docs/verdify-migration.md
- **Covers:** 337, 118, 43, 174, 175, 177, 351, 352

L8-PRODUCT owns the residual product-plane: confirm/close the second device writer (verdify-setpoint-server, #118 — already LIVE 1/1 for 14d and correctly excluded from prod-promote + prod-dark, so this is a single-writer-safety VERIFY-and-close, not a build), unblock the broken RAG/site_content snapshot (#43 — the daily ingestor refresh task IS wired but site_content is 30 days stale because its primary corpus root /mnt/iris/verdify-vault/website is the decommissioned VM mount and the docs/** root alone is not advancing the watermark on the live pod), drive the auth/edge decommission decisions to closure (#174 admin auth-rehome to global auth.vallery.net SSO — gated to Nexus/Root edge owners; #175 botauth.verdify.ai retire — decision already RECORDED as "retire both" in docs/verdify-migration.md J20), keep the deferred items explicitly deferred with their gates intact (#177 internet device channel — deferred pending a hardware/topology ADR; the twin epic #14 and its P0 setpoint-coverage gate #31 — twin component is BUILT but the divergence metric is untrustworthy until #31 closes), and steward the two G3 product epics (#351 lab notebook, #352 testing harness) as cross-lane umbrellas. This is a LOW-PRIORITY lane behind the cycling/float critical path; nearly all device/edge actions are cross-system and root-credentialed, so most W0 work is source/docs/verification with no device or edge mutation. The lane explicitly does NOT touch DNS/Auth/Cloudflare/edge config itself (#337 non-goal); it converts those to coordination requests with owner+evidence.

#### L8-S1 — _As a greenhouse operator responsible for device-write safety, I want the confirmed second device writer (verdify-setpoint-server) to be provably single-writer-safe, correctly excluded from automated promotion and the device-dark shape, and documented as a distinct channel from the ESP32 climate writer, so that the grow-light HA writer can never collide with the ingestor's ESP32 control loop, and #118 closes on evidence rather than staying open as an unresolved 'second writer found' inventory item._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #118 | W0 | none | S | Verify + close verdify-setpoint-server single-writer safety (already live 1/1) | — |
| #399 | W0 | none | S | Add single-writer-safety assertion + SERVICE_MAP entry for the HA grow-light writer | #118 |

#### L8-S2 — _As a AI planner (Iris) doing retrieval-augmented reasoning over the greenhouse knowledge base, I want the site_content RAG snapshot to refresh on its declared daily cadence from a corpus source that actually exists post-VM-decommission, so that Iris retrieval stops silently drifting on a 30-day-stale knowledge base and the lab notebook + planner share one authoritative, fresh corpus._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #43 | W0 | none | M | Repoint site_content RAG corpus off the dead VM vault mount; make the refresh self-alert | — |
| #400 | W0 | none | M | RAG corpus source-of-truth fix backing #43 (repoint + alert) | #43 |

#### L8-S3 — _As a platform operator decommissioning the retired VM/edge product plane, I want the residual auth/edge surfaces (admin SSO rehome, dead botauth backend) resolved to closure as coordination requests with owner + evidence, without this lane touching DNS/Auth/Cloudflare itself, so that analytics/logs admin access is consolidated under the global SSO and dead backends are removed or recovered with no orphaned routes, honoring the #337 non-goal that this lane makes no edge changes unilaterally._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #174 | W3 | infra-preflight | S | Admin auth-rehome to global auth.vallery.net SSO — convert to coordination request | — |
| #175 | W3 | infra-preflight | S | Retire botauth.verdify.ai dead backend — confirm no consumers, remove route | #174 |
| #337 | W3 | infra-preflight | S | EPIC steward: Decommission/Auth/residual product-plane — drive residuals to closure | #118, #43, #174, #175, #177 |

#### L8-S4 — _As a control-system steward planning post-decommission product-plane work, I want the deferred and umbrella product items (internet device channel, firmware twins + their P0 setpoint-coverage gate, lab-notebook and testing epics) kept explicitly deferred with their gates and dependencies intact, so that low-priority/gated work is not started prematurely, the twin divergence metric is never trusted before its coverage gate closes, and the G3 product epics stay accurate umbrellas instead of drifting into parallel stale architecture._
| Issue | Wave | Gate | Eff | Title | Depends |
|---|---|---|---|---|---|
| #177 | W3 | hardware-preflight | S | Confirm DEFERRED: internet-friendly device channel (MQTT/edge-gateway) — gate intact | — |
| #351 | W3 | none | M | EPIC steward: L9 lab notebook / website / publishing — keep accurate, not parallel-stale | #43 |
| #352 | W3 | none | S | EPIC steward: L10 testing/research harness — all-year/all-weather deterministic firmware checkout | #31 |

## Worktree kickoff

SHARED-CLONE DISCIPLINE (CLAUDE.md): ~/repos/verdify-platform is worked by multiple lanes via git worktrees — one isolated worktree per lane, each branch cut off origin/main, land via PR, never switch the shared clone's branch and never edit another lane's worktree; push to preserve before cleaning. Fetch origin/main before each cut. NOTE: the current shared clone is on codex/adr0004-solar-kpi-deploy-gate (PR #385) — do NOT switch it; cut all lane worktrees off origin/main.

PLAN (run from any kubectl host; absolute paths):
  git -C /Users/jason/repos/verdify-platform fetch origin
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/schema-registry      ../wt-L0-schema      origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/climate-floating-corridor ../wt-L1-climate origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/observability-kpis    ../wt-L2-obs        origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/planner-outcome-objective ../wt-L3-planner origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/data-db-solar         ../wt-L4-data       origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/deploy-cicd           ../wt-L5-deploy     origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/ha-reliability        ../wt-L6-ha         origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/irrigation-hardware   ../wt-L7-irrig      origin/main
  git -C /Users/jason/repos/verdify-platform worktree add -b lane/product-decommission-auth ../wt-L8-product origin/main

START IMMEDIATELY (no deps, W0, source-only — can run in parallel today): L0-SCHEMA (registry/cfg_* rows are the dependency root — start FIRST so L1/L2/L3/L4/L7 unblock), L5-DEPLOY (no deps; the CI-guard fixes — service-restart marker, generated-CM gate, solar SSoT guard, cycling deploy gate — are pure source and unblock the wire-protocol guards), L6-HA (no deps; gate:infra-preflight applies only at the apply step, the manifests/alerts are authored source-first), L8-PRODUCT (no deps for the source fixes; #118 assertion + #43 RAG repoint are source). SERIALIZE the PR #385 split itself in the existing branch (do not re-cut it). GATE-START (depend on L0-SCHEMA landing): L1-CLIMATE, L2-OBS, L3-PLANNER, L4-DATA, L7-IRRIG begin their schema-consuming work once L0-SCHEMA's registry/contract PR is merged; they may start their non-schema scaffolding immediately in their worktrees. MIGRATION SERIALIZATION: L4-DATA and L7-IRRIG both add migrations — coordinate through L0-SCHEMA so only one migration change is in flight at a time (one-at-a-time per CLAUDE.md); L4's solar-SSoT note + cycle-reconcile view and L7's data-truth re-baseline must not race the same migration number. Cleanup: git -C /Users/jason/repos/verdify-platform worktree remove ../wt-LN-... only after the lane's PR is merged and pushed.

## Cross-lane risks

- OTA BUDGET (highest scheduling risk): W2 stacks THREE firmware changes — mister dwell #299, grow-light shoulder #349/NEW, heat_dehum #383 — against ≤1-OTA/week + 48h bake. They MUST be sequenced as OTA-1 (#299 co-#349) then OTA-2 (#383) across two weeks. Rushing them re-creates the sprint-15.1 fix-it-forward regression spiral the freeze rules exist to prevent. Do not co-bundle heat_dehum into OTA-1 — it would confound the cycling-fix attribution.
- KPI-SCOREBOARD-BEFORE-FLOAT: daily_summary.cycles_* are inflated 30-140x on partial/future-dated days. If the float trial (#377) reads cycles_* before L4-S3 reconciles them against raw equipment_state TRUE rising edges and excludes partial days, it gets a FALSE rollback or false success on the cycling axis. #295 already proved an OTA can silently double cycling with nobody gating on cycle count. The trustworthy scoreboard (W0) is a HARD prerequisite for the W1 float push and the W2 OTA gate — do not push the float-flip until cycles_* is reconciled.
- HEAT_DEHUM CARVE-OUT (regime contradiction): heat_dehum is VERIFIED hardcoded-active at greenhouse_logic.h:1580 (fires when mode==IDLE && closed_heat_dehum_wanted, no enable tunable), adding overnight IDLE→heat1 actuation in the exact wet-night idle-inside-corridor regime ADR-0004 wants quiet. If it ships with the float-flip the float benefit is contaminated and overnight runtime rises silently (the 2026-06-15 orchid overnight RH 84% regression is the cautionary precedent). It MUST be carved out of PR #385, made opt-in default-OFF (closed_heat_dehum_enabled), and proven on a clean float baseline + live moisture telemetry before its own OTA. It fires 0x in 7d of current summer live data — all replay divergence is March cold-spring nights — so its near-term value is low and the carve-out cost is near-zero.
- MIGRATION SERIALIZATION: L0-SCHEMA, L4-DATA, and L7-IRRIG all introduce migrations/registry rows. CLAUDE.md mandates one migration change at a time, classified by the rollback-safety tooling, schema-lands-first. Two lanes racing the same migration number or landing consumer rows before the schema row breaks the drift guards (the wire protocol). Route every migration through L0-SCHEMA and run make migration-rollback-safety + the targeted rollback proof. Migration 186 specifically is a band-grade regime break — apply in June (delta ~2-3 min), record the timestamp, treat band-grade history as discontinuous, and REFRESH the band-curve matviews immediately so now+4d future-dated rows do not serve the stale band.
- #382 WRITER-WEDGE DURING RESTARTS: the iSCSI target-cap wedge left the ingestor writer down ~10min on restart and is safety-checked on the NAS fix; the ingestor currently runs on an emptyDir TEMP patch. Any W0 container promotion, the W1 prod migration apply, or the W2 OTA deploys that bounce the writer risk a remount wedge. Do NOT schedule writer restarts lightly while #382 is open; ensure the single-writer Lease fence (#240, ARMED) + the new out-of-band writer-absent/split-brain alert (L6-S1) are live FIRST so a wedge or a second writer is caught, and interlock the #245 CNPG cutover behind #382's resolution.
- SECOND-WRITER SAFETY (#118): verdify-setpoint-server is a confirmed second device writer (grow-lights via HA at 192.168.30.107:8123). The grow-light OTA (#349/NEW) and any device-channel change risk creating a same-actuator double-writer. The channel-separation assertion (HA grow-light relays vs ESP32 climate relays) + docs/SERVICE_MAP.md entry must be proven before the grow-light OTA, and the writer-absent/split-brain alert must distinguish the two writers.
- KPI COST LATENT (LLM-loop exposure): warm-cache single-day outcome_kpi blocks are sub-second (the 27-30s figure did not reproduce), but 6 sequential awaits with no asyncio.gather + duplicated 6-LATERAL fan-out across partitioned tables (climate 47 children, setpoint_snapshot 14) will degrade on cold cache / multi-day / planner-LLM-loop calls. Do NOT expose outcome_kpi to a looping LLM uncached — gate behind the L2 single-day/cached path before #365 wires it into the planner reward.
- COVERAGE/DUP DISCIPLINE: the lane digest's covers[] lists left 8 issues uncovered and 3 duplicated (resolved in coverage_check). If the lanes are kicked off from the raw digest without applying those assignments (#14/#31→L1, #43→L8, the 8 gaps placed), issues #220/#287/#316/#321/#359/#378/#381/#384 fall through the cracks and #14/#31/#43 get double-worked across two worktrees — a shared-clone collision. Apply the coverage_check assignments before kickoff.
