# Verdify Platform Epics

> **Current audit (2026-08-29):** the active epic/outcome boundaries, six-step
> sprint sequence, and complete issue disposition are in
> [`docs/audits/work-pending-2026-08-29.md`](docs/audits/work-pending-2026-08-29.md).
> The controller sprint proposal below is retained as historical context.

> Optional planning and status reference. Epics and milestones record context;
> they are not execution authority, prerequisites, or a required workflow. Work
> directly from the user's request, current repository state, and live evidence.

Last updated: 2026-08-29

Agent name: `verdify-platform`

These are the current EPIC-level board cards for the `verdify-platform` lane.
The 2026-06-16 controller replan supersedes the older three-env/product-plane
decomposition as the active planning surface. Older cards remain as child
anchors and evidence; do not delete their history.

## Current Sprint Proposal

Start with S4 `controller-architecture-audit`, then pull the highest-risk
schema/firmware/dashboard contract work forward into S5/S6. Firmware OTA,
prod sync, device VLAN, prod-destructive DB, credential, and outward-facing
infra actions remain safety-checked.

2026-06-23 overlay: use
`planning/backlog.yaml` for the current
climate-control replan. ADR-0004 supersedes ADR-0003 target hugging; the next
priority is DB solar phase parity, then the safety-checked `band_track_fraction -> 0`
float trial, then outcome KPIs and moisture-estimator telemetry before the
focused VPD/dehum policy lane (#383).

## Current Epics

### L1 Architecture Audit, Drift Check, and CI/CD

- Canonical issue: #343.
- User/value statement: A future session can say what is actually deployed,
  what is intended, what is stale, and which component owns each write path.
- Scope: firmware, Kubernetes services, ingestor, database, Grafana, planner,
  Hermes, lab notebook, Home Assistant, Tempest, Open-Meteo, S3, Cloudflare,
  CI/CD, release checklists, and failure-mode docs.
- Non-goals: production sync, firmware OTA, device VLAN changes, credential
  rotation, public DNS/edge changes, or broad architecture rewrites in this
  tracking pass.
- Acceptance criteria:
  - Actual-vs-intended architecture map exists.
  - Every deployed/referenced component is classified as authoritative,
    reader, fallback-only, stale, dead, delete, or rewrite candidate.
  - Single-env prod promotion and manual sync gates are documented.
  - Home Assistant fallback/backfill is documented as fallback/backfill, not a
    duplicate source of truth.
- Status: `Done` (2026-06-17 — audit delivered; drift remediated; CI/CD hardened;
  prod deploy executed under Jason authorization). Acceptance criteria all met:
  actual-vs-intended map ✓, component authority classification ✓, single-env
  promotion/sync gates documented ✓, HA fallback documented as fallback-only ✓.
- Priority: P1
- Effort: L
- Milestone: G0 - Controller Architecture Audit
- Sprint: S4 `controller-architecture-audit`
- Related files/issues/PRs: #207, #335, #336, #339, #341, #342; delivered PRs
  include VM-era purge (#353), schema regen plus migration 180 (#354),
  dead dashboards (#355), CI gates (#356), ingestor reliability (#357), and
  prod promote (#358);
  `docs/reviews/lane1-architecture-audit-2026-06-16.md`, `docs/RELEASE-CHECKLIST.md`,
  `docs/handoff/monitoring-writer-absent-alert.md`, `docs/SERVICE_MAP.md`,
  `docs/reviews/lane1-architecture-audit-2026-06-16.md`.
- Dependencies: Jason, `monitoring-stack`, `network-infra`, `storage-infra`.
- Risks: stale docs can look authoritative; old dev/staging language can cause
  operators to reason about environments that no longer exist.
- Evidence: Project #5 cards #343-#352, the merged PRs above, and root tracking docs.
- Residual: **DEPLOYED 2026-06-17** — the ingestor `emptyDir → durable PVC` revert
  was synced to prod (`argocd app sync verdify-prod-dark` → Synced/Healthy), the
  hermes node-local migration realized, and the grafana PDB bug fixed (`36382e9`).
  Still genuinely gated: monitoring-stack writer-absent alert (external cluster);
  DB PITR (attended maintenance — single live DB); `#240` writer-lease arm (gated);
  DSM iSCSI target-cap reclaim (NAS control-plane; pressure reduced now that lab +
  hermes moved off iSCSI to node-local).

### L2 Firmware Core

- Canonical issue: #344.
- User/value statement: Firmware is a deterministic local physics engine that
  keeps the greenhouse safe without cloud dependency.
- Scope: five-second state machine, climate/lighting/irrigation separation,
  relay states, safety rails, disconnected operation, local fallback, relay
  dwell/wear, override precedence, and crop-specific logic removal.
- Non-goals: firmware OTA without the required preflight, AI-controlled safety/target curves, or
  crop strategy embedded in firmware.
- Acceptance criteria:
  - Firmware responsibilities are documented around climate, lighting, and
    irrigation only.
  - Relay transitions and safety override behavior are explicit.
  - 72-hour disconnected behavior is defined and tested.
  - AI tunables cannot override hard rails or core FSM logic.
  - Crop-specific assumptions are removed or isolated above firmware.
- Status: `Done` (2026-06-17 — all 5 acceptance criteria met. The control core
  was already correct (8-mode band-first FSM, offline-first, AI-bounded); this
  lane closed the documentation + test-rail gaps and proved them OFFLINE. The
  authoritative spec is `docs/firmware-fsm-spec.md` (§11 = AC traceability):
  AC1/AC2 responsibilities + relay-transition + safety-override spec ✓;
  AC3 72h-disconnected defined AND tested (`disconnected_72h_*`,
  `no_time_source_fallback_*`, `reboot_persisted_anchors_*` native tests) ✓;
  AC4 rails + the 5-layer AI-can't-override defense, newly pinned by firmware
  invariants #25 (SAFETY_HEAT) / #26 (SENSOR_FAULT) ✓; AC5 crop-agnostic guard
  test ✓.
  Verified: 222/0 native firmware tests, 193,525-row invariant suite green.
  Firmware OTA arming of the new rails stays safety-checked — NOT an acceptance
  gate (OTA without preflight and rollback is an explicit non-goal).)
- Priority: P1
- Effort: XL
- Milestone: G1 - Firmware-First Determinism
- Sprint: S5 `firmware-first-climate-core`
- Related files/issues/PRs: #287, #289, #290, #292, #299, #300, #323, #324,
  plus #327, #340, `firmware/`, `verdify_schemas/`,
  `docs/design/firmware-v2-simplification-2026-06-10.md`; delivered
  `docs/firmware-fsm-spec.md`, `firmware/test/invariants.h` (#25/#26),
  `firmware/test/test_greenhouse_logic.cpp` (72h tests),
  `tests/test_firmware_crop_agnostic_guard.py`; commits d8ed531, 417531e,
  38c6e08, ffc89b9.
- Dependencies: firmware preflight/rollback; schema-first sequencing for emitted fields and
  tunables.
- Risks: production firmware controls live relays; regressions can harm plants
  or hardware.
- Evidence: firmware gates in `AGENTS.md` and latest firmware/control PRs.

### L3 Climate Control

- Canonical issue: #345.
- User/value statement: Climate follows a smooth deterministic diurnal target
  curve and avoids contradictory device behavior unless the state machine has a
  deliberate reason.
- Scope: target temperature and VPD math, sunrise/sunset/solar peak/seasonal
  alignment, hysteresis, heating/ventilation/fogging/misting/cooling
  transitions, outdoor-air-aware strategies, green-band compliance model, and
  mechanical-limit interpretation.
- Non-goals: AI-authored target temperature, plant-stress limits, or emergency
  behavior.
- Acceptance criteria:
  - Diurnal target curve math is formalized and tested.
  - Target bands and hysteresis are documented.
  - Mechanical transition rules avoid energy-waste contradictions by default.
  - Outdoor air use is explicit.
  - Compliance can distinguish controller misses from physical impossibility.
- Status: `Done` (2026-06-17 — all 5 acceptance criteria met, proven offline +
  live-prod confirmation. AC1 diurnal harmonic curve math formalized
  (`docs/firmware-fsm-spec.md` §6) + cross-impl goldens (firmware==solar.py==DB,
  `fn_crop_band_value` verified LIVE in prod); AC2 bands + hysteresis +
  dwell tables (§7); AC3 energy-waste contradictions avoided by default (fog/heat
  + fog/vent exclusivity invariants #1/#11, night-econ-heat suppression) (§8.1);
  AC4 outdoor-air economizer gate explicit + staleness-guarded (§8.2, invariant
  number #9); AC5 graded + feasibility-aware compliance (`fn_zone_band_grade`,
  migration 146) distinguishes controller-miss vs physically-unachievable —
  VERIFIED LIVE in prod (emitting controller/none labels on real data) and pinned
  offline by `tests/test_compliance_feasibility_classifier.py`.)
- 2026-06-23 qualification: the base L3 firmware/control acceptance remains
  closed, but the DB/service solar mirror was later found seasonally wrong
  (`fn_solar_altitude()` hardcodes solar noon at 13:00 local). Treat DB solar
  parity, #377 float follow-through, and VPD outcome telemetry as active
  follow-through under #359/#293/#327/#347/#348/#371/#383, not as evidence that
  the firmware core is incomplete.
- Priority: P1
- Effort: XL
- Milestone: G1 - Firmware-First Determinism
- Sprint: S5 `firmware-first-climate-core`
- Related files/issues/PRs: #13, #17, #20, #287, #291, #292, #293, #323,
  plus #324, #328, #341, #359, #361, #377, #378, #383,
  `docs/design/band-compliance-architecture.md`,
  `docs/GREENHOUSE-CONTROL-TEST-CATALOG.md`; delivered `docs/firmware-fsm-spec.md`
  §6-§10, `tests/test_compliance_feasibility_classifier.py`; commits 38c6e08,
  417531e, ffc89b9.
- Dependencies: L5 schema authority, L6 dashboards, execution safeguards for OTA/live DB.
- Risks: band/content bugs can look like firmware bugs unless readbacks and
  service projections are compared.
- Evidence: data-path review findings F1/F2/F6/F7/F12/F20.

### L4 AI Planner And Tunables

- Canonical issue: #346.
- User/value statement: AI helps tune bounded parameters over 72 hours without
  owning safety, target curves, emergency behavior, or firmware logic.
- Scope: Hermes-vs-direct-GPT-5 decision, planner input/output schema,
  allowed tunables and bounds, write contract, planner decision ledger,
  outcome scoring, sunrise/sunset/solar-maximum/deviation/weekly triggers.
- Non-goals: planner writes to deterministic target temperature, stress
  thresholds, hard rails, emergency override, or FSM code.
- Acceptance criteria:
  - Planner architecture decision is documented. → `docs/adr/0002-planner-hermes-vs-direct-gpt5.md`
  - Input/output schema exists. → `docs/planner/planner-io-schema.md`
  - Allowed tunables and bounds are explicit and firmware-supported. → I/O doc §3 + `tunable_registry.py`
  - Planner writes are durable, auditable, and cannot exceed bounds. → I/O doc §4 + `test_tunable_registry.py::TestPlannerWriteContractLockout`
  - Decision ledger connects planner changes to outcomes. → I/O doc §5 (`plan_journal`/`plan_evaluate`)
  - Weekly deep-review trigger exists. → `PLANNER_TRIGGER_MATRIX["WEEKLY"]` + `test_11_planner_milestones.py`
- Status: `Done` (2026-06-17)
- Priority: P1
- Effort: L
- Milestone: G3 - Planner, Irrigation, Lab, and Research
- Sprint: S6 `data-observability-planner-contracts`
- Related files/issues/PRs: #214, #315, #287, #293, #300, `planner_graph/`,
  `mcp/`, `templates/`, `verdify_schemas/`.
- Dependencies: L2 firmware tunables and readbacks, L5 write contract, model
  credential health without secret exposure.
- Risks: stale prompt language can imply real-time or band authority that does
  not exist.
- Evidence: data-path review F8 and genai subsystem docs.

### L5 Data, Schema, And Source Of Truth

- Canonical issue: #347.
- User/value statement: Every greenhouse value has one known authority, known
  readers, known fallback behavior, and a drift-detection strategy.
- Scope: schema authority matrix, firmware state, Tempest observed weather,
  Open-Meteo forecast, Home Assistant fallback/backfill, planner tunables, DB
  projections, Grafana/lab usage, firmware/service setpoint drift detection,
  and Mountain-time alignment.
- Non-goals: destructive prod DB operations or schema changes without migration
  safety and restart documentation.
- Acceptance criteria:
  - Full read/write authority matrix exists.
  - Firmware is canonical for greenhouse-side state where possible.
  - Home Assistant is fallback/backfill only.
  - Firmware-calculated and service-calculated setpoints are stored and compared.
  - Divergence alerts exist.
  - Tempest/Open-Meteo timezone handling is documented.
- Status: `Ready`
- 2026-06-23 pull-forward: fix DB solar phase parity before seasonal anchor
  tuning. Acceptance should compare DB sunrise/noon/sunset/phase to the
  firmware/Python NOAA contract on March equinox, June solstice, September
  equinox, and December solstice within the existing +/-5 minute tolerance.
  Local implementation exists as migration 186 + schema/tests; production apply
  and dependent surface refresh remain pending.
- Priority: P1
- Effort: XL
- Milestone: G2 - Data Contracts and Observability
- Sprint: S6 `data-observability-planner-contracts`
- Related files/issues/PRs: #13, #14, #31, #207, #293, #324, #327, #341,
  `verdify_schemas/`, `db/`, `ingestor/entity_map.py`, `mcp/server.py`.
- Dependencies: L1 architecture map, L2 firmware surfaces, Jason for live DB
  gates.
- Risks: duplicated defaults and stale schema dumps can silently mislead future
  maintainers.
- Evidence: data-path review findings F1-F20.

### L6 Observability, Dashboards, And KPIs

- Canonical issue: #348.
- User/value statement: Operators can tell whether the greenhouse is doing the
  right thing, not merely whether relays are on.
- Scope: 72-hour historical viewer, forecast-vs-observed drift, relay
  timeline/runtime/flapping, target-vs-actual temp/VPD, green-band compliance,
  normalized score, mechanical-limit-adjusted interpretation, planner outcome,
  firmware/service drift alerts, and data-hole/backfill status.
- Non-goals: owning the shared monitoring stack outside app-local dashboards and
  repo-authored alert manifests.
- Acceptance criteria:
  - 72-hour viewer is repaired or rebuilt.
  - Green-band compliance score exists.
  - Forecast drift, relay runtime, and flapping views exist.
  - Firmware/service drift alerting exists.
  - Physical-limit-aware interpretation is documented.
- Status: `Ready`
- 2026-06-23 pull-forward: add daily pinched-vs-served corridor KPIs,
  nature-alignment rollups, actuator runtime/cycle budgets, and outcome grading
  for time-in-corridor, DLI, DIF, wet/dry completion, energy, water, and cycling.
  This is the score surface for #377/#378, #327 moisture telemetry, and #383 VPD
  policy work.
- 2026-06-23 local progress: MCP `outcome_kpi(target_date)` now computes the
  read-only served/pinched, DIF, solar-phase, resource, and action-effectiveness
  surface from existing telemetry. It also reports VPD policy sequence counters:
  wetting episodes, vent-dehum episodes, heat-dehum episodes, and 30-minute
  wet->dehum / dehum->wet transitions for fog/dehum ping-pong review.
  #327 moisture-estimator telemetry is source-wired without a new migration:
  firmware emits `climate_moisture_exchange`, the ingestor persists it under
  `climate_action_log.source_system_state`, and `outcome_kpi()` summarizes the
  JSON payload when live rows exist. Still needed: the safety-checked OTA, service
  deploy, live-data verification, and any durable DB/dashboard rollups for
  long-term reporting.
- 2026-06-23 #383 source-policy progress: low-wet night rows now have a bounded
  closed-vent heat-assist dehum path in firmware source when `MX_HEAT_ASSIST`
  is the estimator result, VPD is below the corridor, temperature is inside the
  served band, and a 1.5 F heat probe stays below the high edge. This remains
  offline/source-only until the OTA/deploy/live KPI gates are run. Offline
  proof: native firmware tests and invariants passed; the stock replay produced
  only the intended heat1-only divergence and passed with an explicit threshold;
  ESPHome compile passed.
- Priority: P1
- Effort: L
- Milestone: G2 - Data Contracts and Observability
- Sprint: S6 `data-observability-planner-contracts`
- Related files/issues/PRs: #75, #89, #200, #241, #308, #327, #328, #341,
  plus #371, #383,
  `grafana/`, `deploy/k8s/components/grafana/`,
  `docs/grafana-panel-catalog.md`.
- Dependencies: L5 source-of-truth matrix, `monitoring-stack`,
  `network-infra`.
- Risks: DB-derived curves can show green while device readback diverges unless
  both are plotted/alerted.
- Evidence: data-path review F2/F17 and dashboard catalog.

### L7 Lighting And Occupancy

- Canonical issue: #349.
- User/value statement: Occupancy and lighting behavior is low-latency,
  deterministic, and safe for people in the greenhouse.
- Scope: Frigate/Home Assistant/MQTT/direct event path decision, push-enabled
  occupancy to firmware, fogger/mister suppression while occupied, lux-based
  overhead lighting, and Lutron/Home Assistant vs firmware responsibility.
- Non-goals: ambiguous second device writer paths or live device changes without
  Jason.
- Acceptance criteria:
  - Occupancy reaches firmware quickly and reliably.
  - Fogger/misters are suppressed when occupied.
  - Overhead lights respond to occupancy and lux thresholds without jarring
    daytime behavior.
  - Lutron/Home Assistant responsibilities are documented.
- Status: `Ready`
- Priority: P1
- Effort: L
- Milestone: G1 - Firmware-First Determinism
- Sprint: S5 `firmware-first-climate-core`
- Related files/issues/PRs: #118, #294, #295, #300, #341,
  `scripts/setpoint-server.py`, lighting audit targets.
- Dependencies: Home Assistant, Frigate, Lutron, and device-write preflight for
  validation.
- Risks: lighting paths can become accidental duplicate writers if ownership is
  unclear.
- Evidence: Makefile lighting audits and data-path review F4/F5.

### L8 Irrigation, Fertilization, And Orchids

- Canonical issue: #350.
- User/value statement: Climate wetting and plant irrigation/fertigation are
  separate, auditable loops with explicit horticultural choices.
- Scope: fertilizer-tank routing decision, wall driphead irrigation, soil
  moisture/EC sensor strategy, reduced west/south climate misting, orchid
  manual fertilization/inspection routine, fertilizer material decision, and
  three-sensor averaging/offset strategy.
- Non-goals: physical install by the agent or horticultural automation without
  Jason's decision.
- Acceptance criteria:
  - Fertilizer tank routing is decided.
  - Wall irrigation loop is defined from soil moisture/EC.
  - West/south misters are no longer primary climate irrigation.
  - Orchid manual routine or automation is explicit.
  - Sensor averaging/offset logic is documented.
- Status: `Ready`
- Priority: P1
- Effort: L
- Milestone: G3 - Planner, Irrigation, Lab, and Research
- Sprint: S7 `irrigation-lab-testing-hardening`
- Related files/issues/PRs: #16, #37, #45, #296, #297, #298, Makefile
  irrigation targets.
- Dependencies: Jason/horticulture and physical driphead/sensor work.
- Risks: software assumptions can be invalid if physical sensor placement or
  fertilizer routing changes.
- Evidence: Hardware / Seasonal milestone and firmware-v2 req:D issues.

### L9 Lab Notebook, Website, And Publishing

- Canonical issue: #351.
- User/value statement: The public lab notebook reflects the actual greenhouse
  architecture, data, dashboards, releases, and lessons instead of stale
  implementation assumptions.
- Scope: one Quartz generator, S3-backed content/state, the in-cluster publisher,
  the cache-backed nginx runtime, live Grafana/camera embeds, route/content
  parity, and public-output privacy gates. The abandoned alternate generator,
  canary, occurrence runtime, and image pipelines were retired 2026-08-30.
- Non-goals: merging the independent `verdify-www` repo/deployment, CRM,
  DNS/Cloudflare changes, or raw S3 credential handling.
- Acceptance criteria:
  - `lab.verdify.ai` serves the newest successful Quartz publisher generation.
  - Left navigation, search, dark/reader controls, interactive Grafana panels,
    camera media, canonical routes, and public-output guards remain validated.
  - No alternate Lab generator, stage route, candidate workload, build profile,
    or content-bearing serving image remains active.
- Status: `In Progress`
- Priority: P1
- Effort: XL
- Milestone: G3 - Planner, Irrigation, Lab, and Research
- Sprint: S7 `irrigation-lab-testing-hardening`
- Related files/issues/PRs: #351, `site/`, `scripts/lab-publish-k3s.sh`, and
  `deploy/k8s/components/lab-site/`.
- Dependencies: S3 content/state, the publisher CronJob, the Longhorn cache PVC,
  Grafana's public graph origin, Traefik, and the Cloudflare tunnel.
- Risks: a content-bearing image or temporary overlay superseding the cache;
  stale browser caching; invalid public-output content; unavailable graph media.
- Evidence: `docs/reviews/lab-generator-standardization-2026-08-30.md` and the
  web/publishing runbooks.

### L10 Testing And Research

- Canonical issue: #352.
- User/value statement: Firmware releases are proven across all days of the
  year and representative extreme-weather futures, not just historical replay.
- Scope: 365/366-day checkout, sunrise/sunset validation, target curve
  validation, 72-hour forecast scenarios, historical weather replay, extreme
  heat/cold replay, runtime forecast by equipment, compliance score output,
  safety rail tests, and firmware/service setpoint agreement tests.
- Non-goals: replacing firmware replay/invariant gates; OTA execution without
  Jason.
- Acceptance criteria:
  - 365/366-day firmware checkout exists.
  - Every firmware release can run 72-hour forecast scenarios.
  - Extreme cold and heat scenarios exist.
  - Firmware/service target drift tests exist.
  - Runtime forecast and physical-limit model are started.
- Status: `Ready`
- Priority: P1
- Effort: XL
- Milestone: G3 - Planner, Irrigation, Lab, and Research
- Sprint: S7 `irrigation-lab-testing-hardening`
- Related files/issues/PRs: #14, #31, #303, #322, #335, #340,
  `firmware/test/`, `scripts/firmware-replay-diff.sh`.
- Dependencies: L2/L3 deterministic model, CI capacity, firmware preflight evidence.
- Risks: current replay can miss band-curve changes unless
  `make firmware-replay-band` is used.
- Evidence: `AGENTS.md` verification order, Makefile firmware targets, and
  `docs/GREENHOUSE-CONTROL-TEST-CATALOG.md`.

## Rules

- One issue has one primary owning lane.
- Board cards are EPICS. Child issues/tasks may exist, but planning happens at
  the lane-epic level.
- If work depends on another lane or external owner, record the dependency in
  GitHub issues and the issue `## Project Tracking` block.
- Historical work must be `Done` only when linked to closed issues, merged PRs,
  commits, or durable runbook evidence.
- Firmware OTA, prod sync, device VLAN, destructive prod DB, credential
  rotation, and public DNS/edge actions require exact-target preflight and rollback.
