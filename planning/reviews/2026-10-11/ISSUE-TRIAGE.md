# Complete issue acceptance review — October11,2026

All64 initially open repository issues, reopened#949, two new gaps and the closed campaign history are included. Each literal checkbox or retained non-checkbox Verify condition is listed. Not fully proven means acceptance evidence is incomplete, not that code is absent. Historical accepted receipts retain their original scope.

## #14 — OPEN / future_owned

[C8][P2] Twin program: qualify declared oracle coverage before using divergence as a gate

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Future device-denied twin remains deferred. Current source/build/output/coverage and negative oracle receipts are not a full current physical-parity proof.

- **not_fully_proven**: Coverage and limitations are explicit; unsupported/default-only fields cannot produce a full-parity PASS.
- **not_fully_proven**: Source/build/runtime and actual read-only output receipts exist, with no startup compiler/pip downloads.
- **not_fully_proven**: The current pilot remains independent; a future twin gate is adopted only after useful positive and negative evidence.
- **not_fully_proven**: #31 — Close twin setpoint and actuator coverage gaps with generated consumer truth
- **not_fully_proven**: #606 — Register the twin build profile only through its owning fleet contract

Source touchpoints: `docs/design/firmware-digital-twin.md`, `twin`, `firmware/test`, `deploy/k8s/components/firmware-twin`.

## #16 — OPEN / physical_or_seasonal

[C7][P2] Hardware program: commission only the sensing and equipment justified by evidence

Actual crop/substrate/probe/plumbing/calibration and seasonal observations remain unavailable where uncommissioned. No guessed calibration, automatic hardware purchase or physical acceptance. Retained October6 assessment, not new execution: Physical inventory/commissioning remains deferred. Missing south, hydro, interior-light and chemistry channels remain unavailable; no installation/physical acceptance was performed.

- **not_fully_proven**: Each physical item has a named measurement/control need, commissioning worksheet and explicit enabled/disabled state.
- **not_fully_proven**: No center-fertilizer path contradicts #434; absent sensors are not synthesized as real observations.
- **not_fully_proven**: Operator windows and seasonal triggers are tracked without guessed dates or blanket launch blockers.
- **not_fully_proven**: #751 — Replace the failed south climate probe and diagnose hydro telemetry in a bounded window
- **not_fully_proven**: #298 — Rebaseline actual crop, pot and probe topology before feedback control
- **not_fully_proven**: #398 — Replace stale soil threshold seeds with commissioned crop/substrate truth
- **not_fully_proven**: #45 — Commission soil and runoff feedback before enabling irrigation decisions
- **not_fully_proven**: #51 — Calibrate CO2 against a real reference and publish measurement uncertainty
- **not_fully_proven**: #52 — Define species-appropriate seasonal dormancy care from operator observations
- **not_fully_proven**: #412 — Close the seasonal door screen only when measured night conditions justify it

Source touchpoints: `config`, `firmware`, `docs`, `planning`.

## #31 — OPEN / future_owned

[C8][P2] Close twin setpoint and actuator coverage gaps with generated consumer truth

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Generated consumer/oracle source does not establish live input coverage, meaningful nondefault divergence or current read-only twin output. Do not reuse the old absent-table claim as current evidence.

- **not_fully_proven**: Every active consumer is covered or explicitly excluded from the oracle claim with a reason.
- **not_fully_proven**: Missing required input and a known nondefault divergence fail loudly; identical-source self-diff passes.
- **not_fully_proven**: No twin result can qualify full current 48-field physical exposure or new transport by default.

Source touchpoints: `firmware/test/replay_emit.cpp`, `scripts/export-replay-overrides.sh`, `twin`, `tests`.

## #45 — OPEN / physical_or_seasonal

[C7][P2] Commission soil and runoff feedback before enabling irrigation decisions

Actual crop/substrate/probe/plumbing/calibration and seasonal observations remain unavailable where uncommissioned. No guessed calibration, automatic hardware purchase or physical acceptance. Retained October6 assessment, not new execution: Seven-day audit: no input/runoff pH/EC; south soil channels are entirely zero. Calibration and topology remain uncommissioned. Nonzero west moisture does not establish validity.

- **not_fully_proven**: Required channels produce credible calibrated data, not merely nonzero rows; current gap counts are measured rather than assumed four.
- **not_fully_proven**: Feedback check passes and requirements/alerts close from actual accepted measurements.
- **not_fully_proven**: Unknown chemistry, sensor or plumbing state remains visibly unavailable and cannot enable fertilizer or soil feedback.

Source touchpoints: `docs/runbooks/irrigation-feedback-bringup.md`, `scripts`, `db`, `config`, `Makefile`.

## #49 — CLOSED / accepted

[C5][P1] Audit and close historical suppressed sensor alerts without false count-parity assumptions

ACCEPTED bounded cleanup: all 61 reviewed IDs remain suppressed sensor_offline, resolved by s2_49_historical_suppressed; resolution hashes unchanged. Published apply/second-run/reversal and active-membership receipts satisfy all three clauses. No runtime deployment belongs to this maintenance-only completion. Unrelated Argo failure is #964.

- **accepted**: Only the reviewed historical suppressed sensor_offline IDs are updated; a second run changes zero rows.
- **accepted**: Before/after receipt lists non-secret IDs/counts and verifies no active alert was hidden.
- **accepted**: Forward lifecycle behavior remains correct and reversal data is retained for the exact touched fields.

Source touchpoints: `db/migrations`, `scripts`, `tests`, `docs`.

## #51 — OPEN / physical_or_seasonal

[C7][P2] Calibrate CO2 against a real reference and publish measurement uncertainty

Actual crop/substrate/probe/plumbing/calibration and seasonal observations remain unavailable where uncommissioned. No guessed calibration, automatic hardware purchase or physical acceptance. Retained October6 assessment, not new execution: Current CO2 is about 3067 ppm. Reference-pair calibration and tolerance evidence are absent; preserve uncalibrated status.

- **not_fully_proven**: New coefficients derive from recorded field pairs and pass the predeclared independent tolerance.
- **not_fully_proven**: Plausibility guards and units remain correct, with uncertainty visible to consumers.
- **not_fully_proven**: If the reference/window is unavailable, retain uncalibrated status without guessing anchors or buying equipment implicitly.

Source touchpoints: `firmware/greenhouse/sensors.yaml`, `firmware/greenhouse/globals.yaml`, `docs`.

## #52 — OPEN / physical_or_seasonal

[C7][P2] Define species-appropriate seasonal dormancy care from operator observations

Actual crop/substrate/probe/plumbing/calibration and seasonal observations remain unavailable where uncommissioned. No guessed calibration, automatic hardware purchase or physical acceptance. Retained October6 assessment, not new execution: Species/crop observations and a supported seasonal care decision are unavailable to this remote review. No change is made; keep seasonal decision deferred.

- **not_fully_proven**: A documented no-change or specific seasonal-care decision is supported by actual crop observations.
- **not_fully_proven**: Any new dormancy/light/dry-down setting is versioned, reversible and cannot silently alter a locked study.
- **not_fully_proven**: No species-wide agronomic or yield claim is made from uncalibrated DLI or a house-average proxy.

Source touchpoints: `db/migrations`, `config`, `firmware`, `docs`.

## #75 — OPEN / incomplete

[C4][P1] Observability program: prove actionable health from source through device

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit. Retained October6 assessment, not new execution: S1 closed eight bounded children. Both authenticated Grafana probes are now up; source-to-live and one-writer receipts pass. Six missing Iris skill lookups remain. Monitoring umbrella still requires its broader fault/delivery acceptance.

- **not_fully_proven**: Each child has a failing and recovering real or isolated fault receipt and a clear owner/runbook.
- **not_fully_proven**: No inactive PrometheusRule or static-green worker is accepted as live monitoring.
- **not_fully_proven**: Historical closed children remain history; current coverage and omissions are documented.
- **not_fully_proven**: #563 — Enforce the actual ConfigMap-based alert delivery contract
- **not_fully_proven**: #394 — Deliver and fault-test live split-brain and no-writer alerts
- **not_fully_proven**: #89 — Verify exact-source post-deploy smoke and device-route health
- **not_fully_proven**: #671 — Make Lab publishing failures and cache contention observable
- **not_fully_proven**: #49 — Audit and close historical suppressed sensor alerts without false count-parity assumptions
- **not_fully_proven**: #950 — Restore the ingestor Slack credential-file contract and operator delivery
- **not_fully_proven**: #951 — Restore authenticated greenhouse camera snapshots after connection refusals
- **not_fully_proven**: #952 — Preserve Verdify Grafana monitoring identity across pod replacement
- **not_fully_proven**: #953 — Reconcile Iris planning skill lookup and required reference inventory
- **not_fully_proven**: #835 — Fix reconnect equipment snapshot provenance and stale relay alerts
- **not_fully_proven**: #862 — Lab publisher: warm-cache init rescans the public tree for ~5 min, pushing runs past the 10-min schedule and the 20-min freshness window

Source touchpoints: `scripts`, `deploy/k8s`, `grafana`, `docs`.

## #89 — CLOSED / historical_closed

[C4][P1] Verify exact-source post-deploy smoke and device-route health

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Smoke fails on wrong image SHA, stale data, missing tools or invalid writer count; staging proves zero device writes.
- **retained_historical_acceptance**: Production receipt ties reviewed source, desired/running digest, Argo Synced + Healthy and current data/action evidence.
- **retained_historical_acceptance**: No DB parity or device confirmation is inferred from mere network reachability.

Source touchpoints: `scripts`, `api`, `mcp`, `tests`, `docs/runbooks`.

## #174 — CLOSED / historical_closed

[C8][P2] Rehome only current Verdify admin surfaces to global SSO, fail closed

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review.

- **retained_historical_acceptance**: Only intended admin apps are exposed behind global SSO; no temporary unauthenticated bypass exists.
- **retained_historical_acceptance**: Current source/route identity and end-to-end allowed/denied probes are recorded.
- **retained_historical_acceptance**: Unused legacy auth branding is redirected or retired only after exact target/use verification; no new identity provider is deployed.

Source touchpoints: `deploy/k8s`, `docs`, `config`.

## #214 — CLOSED / historical_closed

[C6][P1] Prove crop-deviation trigger → valid plan on the sole operational planner path

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller.

- **retained_historical_acceptance**: One real eligible deviation produces a valid expiring set_plan or explicit neutral fallback with terminal lineage.
- **retained_historical_acceptance**: Gather/tool failures enter the canonical alert lifecycle and recover without false success.
- **retained_historical_acceptance**: No second writer or planner_graph routing change is introduced to satisfy acceptance.

Source touchpoints: `ingestor/tasks/forecast.py`, `scripts/gather-plan-context.sh`, `hermes`, `mcp`, `tests`.

## #218 — OPEN / incomplete

[C5][P1] Durability program: recoverable backups, measured RPO/RTO and safe HA option

Current backups and healthy CNPG replicas do not establish full restore/PITR/failover/parity, buffer survival or role rollback. Production remains original StatefulSet; no cutover occurred. Retained October6 assessment, not new execution: Latest paired backup succeeded, but current rehearsal ledgers stop at 270 versus production 274. Source/A/B whole-policy parity remains unqualified after the retained 120.554741-second timeout. S2 targets recovery, durable ingest and runtime-role proof; no production database cutover.

- **not_fully_proven**: Backup, restore, ingest-loss and HA children provide measured evidence rather than deployment counts.
- **not_fully_proven**: RPO/RTO and residual failure domains are explicitly reported, with PITR claims only after a point-in-time restore.
- **not_fully_proven**: No broad live DB mutation is authorized by closing the planning or design tasks.
- **not_fully_proven**: #670 — Make backup artifacts sufficient to restore roles, ownership and memberships safely
- **not_fully_proven**: #672 — Make compressed-chunk ownership restoration a supported blocking rehearsal test
- **not_fully_proven**: #382 — Persist ingestor queue and spool across restart and node loss
- **not_fully_proven**: #396 — Design and rehearse CNPG alongside the live database on current storage
- **not_fully_proven**: #245 — Execute a separately bounded database cutover only after parity and rollback proof
- **not_fully_proven**: #643 — Extend least-privilege database roles beyond the already-required experiment boundary

Source touchpoints: `deploy/k8s`, `db`, `docs/runbooks`, `scripts`.

## #245 — OPEN / incomplete

[C5][P1] Execute a separately bounded database cutover only after parity and rollback proof

Current backups and healthy CNPG replicas do not establish full restore/PITR/failover/parity, buffer survival or role rollback. Production remains original StatefulSet; no cutover occurred. Retained October6 assessment, not new execution: Production remains verdify-db-0. All three rehearsal clusters are healthy but at migration 270, four behind production. Complete parity, writer fencing, cutover packet and post-cutover bake are unproved. S2 does not authorize a cutover.

- **not_fully_proven**: A rehearsal proves all writers are quiescent and measures downtime, parity, RPO/RTO and both rollback boundaries.
- **not_fully_proven**: Production source/running DB authority and health are verified through at least the declared 60-minute initial observation and seven-day durability bake.
- **not_fully_proven**: No old database/PVC removal before separate verified retention/decommission criteria; no unqualified sub-minute zero-loss promise.

Source touchpoints: `deploy/k8s`, `db`, `docs/runbooks`.

## #296 — OPEN / future_owned

[C6][P1] Consider slow-hysteresis firmware irrigation feedback only after dispatcher evidence

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Feedback inputs are uncommissioned and south channels zero. Existing source/preparation does not justify firmware feedback enablement; a measured no-build or independently qualified implementation decision remains open.

- **not_fully_proven**: Either a documented no-build decision or a qualified implementation is delivered; no automatic expansion occurs.
- **not_fully_proven**: With commissioned probes a saturated zone suppresses eligible drip conservatively, without duplicate dispatcher/firmware control.
- **not_fully_proven**: Missing/failed sensors, reboot, manual rescue and maximum limits remain safe; physical enablement waits for commissioning.

Source touchpoints: `firmware`, `tests`, `docs`.

## #297 — OPEN / future_owned

[C6][P1] Add saturation alerts and a commissioned dispatcher-side drip-skip loop

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Fresh soil target rows remain Canna Lily/Canna Lily/Unknown with saturation80/80/75. Alert-only preparation is separate from commissioned persistence/hysteresis and actual eligible skipped drip. No threshold/actuation change.

- **not_fully_proven**: Sustained saturation triggers and recovers with explicit sensor validity; no stale seed is treated as crop truth.
- **not_fully_proven**: One eligible saturated zone’s scheduled drip is truthfully skipped/shrunk and recorded, with safe fallback for invalid evidence.
- **not_fully_proven**: No OTA, EC closed-loop dosing or guessed day-mask mitigation is required; alert-only implementation can proceed before physical enablement.

Source touchpoints: `scripts/alert-monitor.py`, `ingestor`, `db/migrations`, `tests`.

## #298 — OPEN / physical_or_seasonal

[C7][P2] Rebaseline actual crop, pot and probe topology before feedback control

Actual crop/substrate/probe/plumbing/calibration and seasonal observations remain unavailable where uncommissioned. No guessed calibration, automatic hardware purchase or physical acceptance. Retained October6 assessment, not new execution: Live soil seeds still retain Canna Lily/Unknown names and remote telemetry cannot establish physical crop/substrate/probe positions. Actual operator inventory remains the predecessor for threshold commissioning.

- **not_fully_proven**: Repo and DB map agree with observed physical positions and crop/substrate identities; no Canna-Lily residue is carried as current fact.
- **not_fully_proven**: Each sensor has source ID, location, validity/calibration state and explicit unassigned/disabled status when appropriate.
- **not_fully_proven**: No automatic irrigation is enabled by a topology import alone; #398/#45 own threshold and feedback commissioning.

Source touchpoints: `config`, `scripts`, `db/migrations`, `docs`.

## #299 — OPEN / future_owned

[C6][P1] Preserve center-mister re-fire protection through topology and reset recovery

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Existing center re-fire protection is preserved; current routing/reset/re-fire preservation and release receipt are not proved by this telemetry scan. Historical live protection is not a current topology release qualification.

- **not_fully_proven**: No origin or restart can re-fire before the declared protection permits; hard safety remains dominant.
- **not_fully_proven**: Eligible re-fire resumes correctly rather than staying latched without reason.
- **not_fully_proven**: Source/firmware/cycle evidence shows protection survived #434-related changes with a rollback canary.

Source touchpoints: `firmware`, `tests`, `docs`.

## #303 — OPEN / incomplete

[C4][P1] Run real portable ESPHome compilation for declared firmware targets

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit. Retained October6 assessment, not new execution: Declared fleet compile/negative-fixture steps exist, and firmware input tree is unchanged from the previously reviewed source. A declaration and green native C++ tests are not executed ESPHome evidence: retained CI output lacks the full secretless compile receipt. Keep portable positive/negative acceptance open.

- **not_fully_proven**: Each declared target actually compiles from a clean checkout; a deliberately broken generated service declaration fails the lane.
- **not_fully_proven**: No network device, secret-bearing configuration or OTA permission is required for compilation.
- **not_fully_proven**: Artifacts bind exact source and toolchain and feed #390 without being labeled physically qualified.

Source touchpoints: `firmware`, `Makefile`, `tests`, `.agent-fleet/ci.yaml`.

## #304 — OPEN / incomplete

[C4][P1] Provide reproducible least-authority firmware and database diagnostic tooling

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit. Retained October6 assessment, not new execution: Tools and generated CI contract exist; current portable toolchain, clean real compile and least-authority delivery matrix remain unqualified. No generated fleet contract was hand-edited.

- **not_fully_proven**: A clean environment reports pinned tool versions and runs the declared compile fixture reproducibly.
- **not_fully_proven**: No tool install grants Secret, exec, device or cross-namespace access; absence of credentials fails safely.
- **not_fully_proven**: Owning fleet delivery and generated repo references agree; no redundant image is introduced solely for convenience.

Source touchpoints: `.agent-fleet/ci.yaml`, `firmware`, `Makefile`, `docs`.

## #317 — OPEN / incomplete

ArgoCD: unscoped sync operations on verdify-prod-dark get rewritten to a stale 2-resource selective scope

OPEN: stale selective-scope hazard has a documented atomic reset workaround; no upstream/fleet regression qualification proves issue closure. agents#4666 is open. Current failed full operation is not a selective-scope pass.

- **not_fully_proven**: Plain full sync does not inherit previous resource selectors or cached results.
- **not_fully_proven**: Exact hooks and running digests independently prove the intended full operation.

Source touchpoints: .

## #322 — CLOSED / historical_closed

[C4][P1] Replace remaining VM-era tests with current k3s delivery contracts

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: No active CI gate requires the destroyed VM stack; relevant current .agent-fleet/ci.yaml checks pass.
- **retained_historical_acceptance**: Negative tests fail for the intended invariant rather than a missing binary/path, and historical docs are not blindly rewritten.
- **retained_historical_acceptance**: The resulting suite is device-denied and reproducible from a clean checkout.

Source touchpoints: `tests`, `scripts`, `Makefile`, `.agent-fleet/ci.yaml`.

## #324 — OPEN / future_owned

[C6][P1] Extend zonal band lineage compatibly for future deterministic control

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Compatible zonal/source fields are preparation. Complete per-zone/legacy round-trip, drift guards and separate future consumer decision remain unverified; absent center sensing stays unavailable.

- **not_fully_proven**: Per-zone and legacy fixtures round-trip through schema, ingest and read APIs without ambiguous field meaning.
- **not_fully_proven**: No new property lacks a model/readback/drift guard, and unavailable center measurements remain unavailable.
- **not_fully_proven**: Only a separately reviewed future control design consumes new zonal authority.

Source touchpoints: `db/migrations`, `verdify_schemas`, `ingestor`, `api`, `firmware`.

## #350 — OPEN / future_owned

[C6][P1] Irrigation program: topology truth, overwatering protection and commissioned wall feed

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Irrigation physical/chemistry feedback remains uncommissioned. Center-only climate/wall-only fertilizer/source stop safety are distinct from physical delivery and accepted crop/substrate protection.

- **not_fully_proven**: Software allows only the approved plumbing/origin combinations and fails closed without required calibration.
- **not_fully_proven**: Overwatering-safe feedback is commissioned on actual crop/substrate sensors before actuation.
- **not_fully_proven**: Physical feed, crop safety/yield and irrigation water claims require their own observed evidence.
- **not_fully_proven**: #434 — Enforce center-only climate mist and commissioned wall-only fertigation
- **not_fully_proven**: #299 — Preserve center-mister re-fire protection through topology and reset recovery
- **not_fully_proven**: #297 — Add saturation alerts and a commissioned dispatcher-side drip-skip loop
- **not_fully_proven**: #296 — Consider slow-hysteresis firmware irrigation feedback only after dispatcher evidence

Source touchpoints: `firmware`, `ingestor`, `config`, `db`, `docs`.

## #359 — OPEN / future_owned

[C6][P1] Control program: truthful crop corridors before evidence-led tuning

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Current API separates legacy binary diagnostics from credit but physically qualified target evidence is unavailable. No tuning/AI safety delegation is justified by this audit.

- **not_fully_proven**: Measurement and localized safety are trustworthy independently of AI credit.
- **not_fully_proven**: Every tuning proposal cites fixed-panel realized climate/resource evidence and explicit guardrails/replay/rollback.
- **not_fully_proven**: No PID arm, unvalidated response model, unmeasured efficiency claim or firmware safety delegation to AI enters the current pilot.
- **not_fully_proven**: #371 — Repair climate score semantics and publish physical outcomes separately from credit
- **not_fully_proven**: #424 — Resolve served, consumed and raw-readback band lineage without fabricated device truth
- **not_fully_proven**: #386 — Prove grow-light minimum-on behavior at the solar-window boundary
- **not_fully_proven**: #430 — Simplify firmware only while preserving autonomous control and observable truth
- **not_fully_proven**: #368 — Define shared sensor jump, flatline and contributor-integrity semantics
- **not_fully_proven**: #324 — Extend zonal band lineage compatibly for future deterministic control
- **not_fully_proven**: #410 — Measure realized solar-night dry-out before changing the controller
- **not_fully_proven**: #378 — Decide corridor widths from fixed-target crop and resource evidence
- **not_fully_proven**: #361 — Reassess diurnal anchors only after solar parity and measured corridor evidence
- **not_fully_proven**: #379 — Gate grey-box forecasting and MPC on validated response models and simpler baselines

Source touchpoints: `docs/adr/0004-floating-corridor-control.md`, `firmware`, `research/planner-efficacy`, `planning`.

## #361 — OPEN / future_owned

[C6][P1] Reassess diurnal anchors only after solar parity and measured corridor evidence

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: No anchor retune performed. Band/solar lineage and measured corridor/readout predecessors remain open; future bounded safety/readback/rollback acceptance remains deferred.

- **not_fully_proven**: No anchor retune precedes source/solar/band truth or changes a locked study.
- **not_fully_proven**: Candidate changes have explicit temperature/VPD safety limits and observed evidence, not target-hugging intent.
- **not_fully_proven**: Any accepted implementation is versioned with current source/readback and rollback values.

Source touchpoints: `firmware`, `db/migrations`, `research/planner-efficacy`, `docs`.

## #367 — OPEN / future_owned

[C6][P1] Consolidate anti-chatter into one explicit dwell contract

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: No unified dwell release, observed parity/chatter/starvation receipt or current OTA acceptance exists. Prior comments deferred this broad consolidation; preserve existing safety floors.

- **not_fully_proven**: Exactly one documented dwell authority governs ordinary transitions and all safety exceptions remain explicit.
- **not_fully_proven**: Boundary/reboot/override tests and #299 center-mister re-fire invariants pass.
- **not_fully_proven**: Measured starts/runtime and behavior parity show no new chatter or unsafe starvation; #390 release evidence is present.

Source touchpoints: `firmware/lib`, `firmware/test`, `docs`.

## #368 — OPEN / future_owned

[C6][P1] Define shared sensor jump, flatline and contributor-integrity semantics

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Fresh raw north/east/west have values while south is missing; diagnostics report4/OK. This contradiction remains real. Shared validity/jump/flatline semantics and firmware-qualified consumer behavior are incomplete.

- **not_fully_proven**: Synthetic impossible jump/stuck cases are detected without suppressing legitimate steady values.
- **not_fully_proven**: Control consumes explicitly valid inputs; degraded quorum and true SENSOR_FAULT behavior are documented and tested, not conflated.
- **not_fully_proven**: No silent sensor substitution changes trial endpoints; any firmware portion passes #390 before OTA.

Source touchpoints: `firmware`, `ingestor`, `verdify_schemas`, `tests`.

## #369 — OPEN / future_owned

[C6][P1] Remove dead tunables and code from a verified consumer inventory

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: No verified complete live-consumer removal inventory or measured code/entity reduction is established. Defer removals until exact consumer and release evidence.

- **not_fully_proven**: Each removed property has zero required live consumer or an explicit tested migration; no dangling generated IDs or docs remain.
- **not_fully_proven**: Net-negative unused code/entity counts are measured and behavior/safety replay is unchanged.
- **not_fully_proven**: No active trial identity changes silently, no safety control is made AI-writable, and #390 release checks pass for firmware changes.

Source touchpoints: `firmware`, `verdify_schemas`, `ingestor`, `scripts`, `tests`.

## #370 — OPEN / future_owned

[C6][P1] Consolidate equipment conflict resolution into one pure arbitration function

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Pure conflict/origin arbitration preparation is not exhaustive real relay/reset/partial-delivery and current firmware-release proof. No control rewrite was made.

- **not_fully_proven**: Exhaustive conflict/origin fixtures produce one deterministic safe result and an auditable block reason.
- **not_fully_proven**: Reverse conflicts, manual rescue, restart and partial delivery cannot energize incompatible relays.
- **not_fully_proven**: Native/invariant/replay/compile and #390 physical release checks pass without unapproved control-policy changes.

Source touchpoints: `firmware/lib`, `firmware/test`, `firmware/greenhouse`, `docs`.

## #371 — CLOSED / historical_closed

[C0][P0] Repair climate score semantics and publish physical outcomes separately from credit

Current telemetry/ledger and historical source repairs distinguish physical crop truth from controller credit. Scientific contract and root campaign remain incomplete.

- **retained_historical_acceptance**: A fixture with high attributable credit and low physical compliance stays distinct through SQL→API→public/planner rendering; no COALESCE renames one metric into the other.
- **retained_historical_acceptance**: Hand-calculated band/distance and missing-sensor cases pass; true crop targets versus historical dispatched bands are identified, not assumed equal.
- **retained_historical_acceptance**: Current-day and historical sample probes agree across consumers with versioned definitions; historical score changes are published as revisions, not silent rewrites.

Source touchpoints: `ingestor/tasks/daily.py`, `db/migrations/203-materialized-daily-kpi-scorecard.sql`, `db/migrations/206-water-est-completed-day-guard.sql`, `api/main.py`, `scripts`, `verdify_schemas`.

## #378 — OPEN / future_owned

[C6][P1] Decide corridor widths from fixed-target crop and resource evidence

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: No completed pilot/readout exists and physical target evidence is unqualified. Keep corridor-width decisions dependent on fixed-target realized evidence, with no retrospective endpoint retune.

- **not_fully_proven**: The decision distinguishes temperature versus VPD and cannot use attributable credit as physical compliance.
- **not_fully_proven**: No retrospective redefinition rewrites the completed study endpoint.
- **not_fully_proven**: Any live band change has bounded scope, exact rollback values and required operator/release authority.

Source touchpoints: `docs/adr/0004-floating-corridor-control.md`, `research/planner-efficacy`, `verdify_schemas`, `firmware`.

## #379 — OPEN / future_owned

[C8][P2] Gate grey-box forecasting and MPC on validated response models and simpler baselines

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Forecast/MPC model coverage and held-out response/calibration gates are not established. No new PID/MPC arm or unmeasured efficacy claim is admitted.

- **not_fully_proven**: Held-out calibration/response gates pass before any counterfactual efficacy or MPC proposal.
- **not_fully_proven**: No PID treatment arm or new outer-loop control enters the current experiment.
- **not_fully_proven**: A separately reviewed bounded implementation/release plan exists for any justified controller change.

Source touchpoints: `research/planner-efficacy`, `firmware`, `docs/research`.

## #382 — OPEN / sprint

[C5][P1] Persist ingestor queue and spool across restart and node loss

PARTIAL: durable spool source, historical3000-event restart/node-loss witness, format/capacity tests and actual warning/clear receipts are published. Final-image committed seven-phase replay/crash/conflict, exact inverse and full data/sequence/study reseal remain unaccepted. Running ingestor differs from desired pin; qualify exact adopted source without fabricated confirmation.

- **not_fully_proven**: Accepted buffered events survive the declared restart/node-loss boundary with zero unexplained loss and bounded duplicate handling.
- **not_fully_proven**: Replayed data cannot invent fresh device confirmation or reorder writer generations.
- **not_fully_proven**: Storage capacity, backlog alerting, recovery time and rollback of the format are documented and measured.

Source touchpoints: `ingestor`, `deploy/k8s`, `tests`, `docs/runbooks`.

## #386 — OPEN / incomplete

[C4][P1] Prove grow-light minimum-on behavior at the solar-window boundary

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit. Retained October6 assessment, not new execution: Current lights reportOFF in latest equipment events. Historical exact outside-window source receipt remains partial; no eligible solar transition/minimum-on timing is forced or proved. Interior PAR/DLI remains absent.

- **not_fully_proven**: Tests fail for both premature cycling and unintended overrun, with exact transition times and stop reasons.
- **not_fully_proven**: Source and actual grow-light state agree across the boundary; safety/manual override remains dominant.
- **not_fully_proven**: No DLI or lighting-efficiency claim is inferred from runtime while interior light measurement is unavailable.

Source touchpoints: `firmware`, `tests`, `docs`, `Makefile`.

## #390 — OPEN / incomplete

[C4][P1] Gate firmware releases on topology, cycling, runtime safety and rollback evidence

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit. Retained October6 assessment, not new execution: Controller is2026.10.2.0637.620a218a-wifi-bound with fresh diagnostic/runtime data. Exact accepted binary floor and the whole negative release/runtime/recovery matrix are not proved by this scan or prior compile; no last-good promotion/OTA.

- **not_fully_proven**: Negative cases for wrong topology, impossible cycling, stale outdoor inputs and missing compile/runtime receipts refuse release.
- **not_fully_proven**: Exact source/binary/version is tied to observed relay/cfg outcomes and a demonstrated recovery path.
- **not_fully_proven**: No unreviewed firmware or failed watchdog candidate is promoted to last-good; no OTA is performed by ordinary CI.
- **not_fully_proven**: #419 — Populate real outdoor freshness in replay and enforce branch coverage

Source touchpoints: `firmware`, `scripts`, `Makefile`, `tests`, `docs/runbooks`.

## #394 — CLOSED / historical_closed

[C4][P1] Deliver and fault-test live split-brain and no-writer alerts

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: The current singleton state does not alert; zero/multiple/unknown conditions follow the declared policy.
- **retained_historical_acceptance**: A delivered test alert and resolved state are evidenced through the actual receiver path without sending unrelated messages.
- **retained_historical_acceptance**: Exact source/config revision and rule reload are recorded; no inert CR or live split-brain experiment is used.

Source touchpoints: `deploy/k8s/overlays/prod/writer-watchdog.yaml`, `scripts`, `tests`, `docs`.

## #396 — OPEN / sprint

[C5][P1] Design and rehearse CNPG alongside the live database on current storage

PARTIAL: production migration275 is applied. Eight rehearsal CNPG clusters currently report healthy3/3. Later October6 full952-rowset/77-sequence/42-study-table frozen-A/B reseals supersede the old270-only assessment, but do not prove final ordinary-client reversal, post-fixture inverse/reseal and cutover packet. Reuse valid reseals and rejected-clone negative evidence; no DB cutover.

- **not_fully_proven**: Candidate uses digest-pinned compatible images and declared storage/anti-affinity; no production writer or device route is acquired.
- **not_fully_proven**: Restore, failover and PITR are proven with data checks, not only Ready replicas.
- **not_fully_proven**: #245 receives an exact cutover/rollback packet and measured downtime/data risks; live DB remains unchanged.

Source touchpoints: `deploy/k8s`, `db`, `docs/runbooks`.

## #398 — OPEN / physical_or_seasonal

[C7][P2] Replace stale soil threshold seeds with commissioned crop/substrate truth

Actual crop/substrate/probe/plumbing/calibration and seasonal observations remain unavailable where uncommissioned. No guessed calibration, automatic hardware purchase or physical acceptance. Retained October6 assessment, not new execution: Fresh database retains original three target seeds80/80/75 and Canna Lily/Canna Lily/Unknown. No commissioned crop/substrate replacement, before/after/reversal receipt exists; never activate seeds as calibration truth.

- **not_fully_proven**: Current map, seed and DB rows agree without overwriting valid operator-specific values.
- **not_fully_proven**: No saturation/drip-skip consumer uses an uncommissioned or mismatched target as valid.
- **not_fully_proven**: Before/after row receipt and exact rollback data exist; #45 can validate the resulting feedback contract.

Source touchpoints: `config`, `db/migrations`, `scripts`, `tests`.

## #399 — CLOSED / historical_closed

[C4][P1] Prove the HA grow-light writer remains within the single-writer contract

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Every live grow-light command origin is attributable to the declared owner; no ambient second climate writer is permitted.
- **retained_historical_acceptance**: Negative competing-writer fixture fails and the legitimate lighting path passes.
- **retained_historical_acceptance**: The exact source/route ownership is documented without changing active scheduling or auth.

Source touchpoints: `docs`, `scripts`, `tests`, `deploy/k8s`.

## #410 — OPEN / future_owned

[C6][P1] Measure realized solar-night dry-out before changing the controller

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Latest24h actions includeHEAT,IDLE,DEHUM_VENT and wet assists. This aggregation does not prove solar-night episodes, response and zero solar-day held-temperature admission; retain realized-episode acceptance rather than fixed-clock/night PASS.

- **not_fully_proven**: Episodes use device solar semantics, never fixed 02:00–06:00, and incomplete/confounded episodes cannot be PASS.
- **not_fully_proven**: Solar-day held-temperature admission is zero while ordinary daytime dehumidification remains correctly distinguished.
- **not_fully_proven**: Planner/MCP sees realized validity-bearing outcomes and explicit disposition, not a universal VPD target or a crop-safety claim without sensing.

Source touchpoints: `db/migrations`, `firmware`, `research/planner-efficacy`, `mcp`, `scripts`.

## #412 — OPEN / physical_or_seasonal

[C7][P2] Close the seasonal door screen only when measured night conditions justify it

Actual crop/substrate/probe/plumbing/calibration and seasonal observations remain unavailable where uncommissioned. No guessed calibration, automatic hardware purchase or physical acceptance. Retained October6 assessment, not new execution: No measured seasonal door-screen trigger or physical closure observation is available. Reminder/calendar prose is not completion; keep operator seasonal decision deferred.

- **not_fully_proven**: The trigger and physical completion are recorded from observation, not a reminder date alone.
- **not_fully_proven**: Temperature/moisture/ventilation remain within the declared safety envelope after closure.
- **not_fully_proven**: Any study receives a visible intervention/deviation record rather than an unexplained seasonal covariate change.

Source touchpoints: `docs`, `config`, `planning`.

## #419 — OPEN / incomplete

[C4][P1] Populate real outdoor freshness in replay and enforce branch coverage

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit. Retained October6 assessment, not new execution: Retained handoff reports real outdoor replay corpus still lacksHUMIDIFY coverage. Live outdoor/forecast values are fresh but do not prove corpus fresh/stale branches, no future leakage and known-good replay qualification.

- **not_fully_proven**: Corpus contains measured finite outdoor age with provenance and both fresh/stale branches; a zero-coverage corpus fails loudly.
- **not_fully_proven**: No future outdoor sample leaks into replay; missing values preserve safe stale behavior.
- **not_fully_proven**: Native/invariant/band and targeted outdoor tests pass against the candidate and known-good self-diff.

Source touchpoints: `scripts/export-replay-overrides.sh`, `db/migrations/192-replay-outdoor-freshness-provenance.sql`, `firmware/test`, `Makefile`.

## #424 — CLOSED / historical_closed

[C0][P0] Resolve served, consumed and raw-readback band lineage without fabricated device truth

Current telemetry/ledger and historical source repairs distinguish physical crop truth from controller credit. Scientific contract and root campaign remain incomplete.

- **retained_historical_acceptance**: All relevant low/high/target series have documented source, unit, grid, timestamps and consumed/readback relationship.
- **retained_historical_acceptance**: The historical discrepancy is explicitly resolved, still present or unobservable; a desired row/cache is never called device confirmation.
- **retained_historical_acceptance**: #749 can distinguish reporting-only mismatch from a real control contradiction and fails closed on the latter without an OTA workaround.

Source touchpoints: `db/migrations/119-band-traceability.sql`, `verdify_schemas`, `firmware/lib`, `ingestor/tasks`, `scripts`.

## #427 — CLOSED / historical_closed

[C4][P1] Prove active Hermes/MCP recovery and truthful non-authoritative worker health

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Controlled required-cycle failures end in a valid full plan or explicit typed fallback and resolve alerts only from actual terminal evidence.
- **retained_historical_acceptance**: Exactly one effective expiring plan and one device writer remain; synthetic planner_graph runs produce zero device writes and no production trigger credit.
- **retained_historical_acceptance**: Tool-level readiness, recovery and live source/digest evidence pass without claiming that Ready pods alone establish planner delivery.

Source touchpoints: `hermes`, `mcp`, `planner_graph`, `ingestor/tasks`, `tests`, `deploy/k8s/components/planner`.

## #428 — OPEN / future_owned

[C6][P1] Diagnose heap/watchdog risk and qualify the actual last-good firmware floor

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Current uptime is 307929 seconds and heap is about 61.21 KiB (published heap_bytes column contains KiB). Retained minimum 2.336 KiB is historical. Full worst-load, fragmentation and accepted rollback-floor qualification remains incomplete; no reset cause inferred.

- **not_fully_proven**: Cause is reproduced or uncertainty explicitly bounded with current load/reset evidence; software-reset versus watchdog counts are distinct.
- **not_fully_proven**: Candidate meets predeclared heap/fragmentation/timing and clean-runtime criteria under representative worst-case load.
- **not_fully_proven**: No last-good promotion occurs solely on compile/replay success, a reset count omission or a projected heap improvement.

Source touchpoints: `firmware`, `tests`, `docs/reviews`, `scripts`.

## #430 — OPEN / future_owned

[C6][P1] Simplify firmware only while preserving autonomous control and observable truth

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: Closed #433 and #949 keep their scoped acceptance. Current bounded ingestor log has zero broad-restore holds; this does not prove broad firmware simplification savings or all tier reductions.

- **not_fully_proven**: Each tier has measured before/after heap/traffic and zero unintended native/replay behavior divergence.
- **not_fully_proven**: Network-isolation autonomy, reset/water-budget conservatism and current full-state evidence survive.
- **not_fully_proven**: #428/#369/#367/#370 receipts define the achieved scope; theoretical entity or heap savings are labeled projections.
- **not_fully_proven**: #433 — Finish truthful single-writer command lifecycle and no-reconcile-storm acceptance
- **not_fully_proven**: #428 — Diagnose heap/watchdog risk and qualify the actual last-good firmware floor
- **not_fully_proven**: #367 — Consolidate anti-chatter into one explicit dwell contract
- **not_fully_proven**: #370 — Consolidate equipment conflict resolution into one pure arbitration function
- **not_fully_proven**: #369 — Remove dead tunables and code from a verified consumer inventory
- **not_fully_proven**: #949 — Bound planner-policy drift holds and prove autonomous safe convergence

Source touchpoints: `docs/reviews/firmware-simplification-proposal-2026-07-06.md`, `firmware`, `ingestor`, `verdify_schemas`.

## #433 — CLOSED / historical_closed

[C4][P1] Finish truthful single-writer command lifecycle and no-reconcile-storm acceptance

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Requested/queued/sent/failed/cancelled/superseded/confirmed states match physical completion, never cache intent.
- **retained_historical_acceptance**: Periodic work is not later than twice its intended cadence during writes; no repeated broad restore storm occurs.
- **retained_historical_acceptance**: Experiment qualification consumes the narrow #639/#749 evidence without waiting for unrelated firmware simplification; full closure requires this issue’s broader receipt.

Source touchpoints: `ingestor`, `verdify_schemas`, `tests`, `docs/reviews/firmware-simplification-proposal-2026-07-06.md`.

## #434 — OPEN / future_owned

[C6][P1] Enforce center-only climate mist and commissioned wall-only fertigation

No control tuning, irrigation actuation or OTA was performed. Source/native/replay/compile and physical topology/cycle/crop evidence named by each clause remain required; do not infer safety/resource benefit from a connected controller. Retained October6 assessment, not new execution: No current all-origin reverse-conflict/plumbing/calibration/duplicate-dose physical release acceptance exists. Missing chemistry/soil calibration blocks feed; no center non-climate or non-wall fertilization is authorized.

- **not_fully_proven**: All command origins and reverse conflicts prove zero south/west climate demand, zero center non-climate mist and zero non-wall fertilizer actuation.
- **not_fully_proven**: Missing/stale/nonfinite/zero/out-of-bounds calibration prevents feed; restart/duplicate/missed-window cases cannot double-dose.
- **not_fully_proven**: Native/invariant/replay/band/compile and make irrigation-stack-software-check pass; #390 release ties actual routing/readbacks/sequence to exact binary with rollback.

Source touchpoints: `firmware`, `verdify_schemas`, `ingestor`, `tests`, `docs`.

## #563 — CLOSED / historical_closed

[C4][P1] Enforce the actual ConfigMap-based alert delivery contract

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: A fixture PrometheusRule fails with the expected first diagnostic; valid ConfigMap contract passes and missing path is explicit.
- **retained_historical_acceptance**: Documentation tells #89/#394 where the real rule source lives; no cross-namespace imperative patch is used.
- **retained_historical_acceptance**: If the monitoring architecture changed, revise the contract with observed selector evidence before changing the guard.

Source touchpoints: `deploy/k8s`, `tests`, `scripts`, `docs`.

## #581 — OPEN / incomplete

[C2][P0] Experiment program: qualify, run and read out the bounded AI-admission pilot

Zero random draws/assigned outcomes; prospective scientific choice, qualified proof, immutable lock and exactly one draw/launch remain incomplete. No launch authority follows from this review. Retained October6 assessment, not new execution: Live experiment counts:0 draws,0 assignment outcomes,0 freezes,0 exports,0 proof receipts,0 open exposures. Full physical qualification/design/readout program remains unfinished.

- **not_fully_proven**: No launch before exact physical proof, tested outcome path, frozen design/no-redraw and separate launch authority.
- **not_fully_proven**: All assigned days, including failed delivery, fallback, rescue and null outcomes, survive into ITT.
- **not_fully_proven**: Final report states whether the result is feasible, promising, adverse or inconclusive and distinguishes climate noninferiority from improvement and runtime from resource savings.
- **not_fully_proven**: #750 — Recovery/proof campaign: rebaseline current state and seal one safe physical receipt
- **not_fully_proven**: #639 — Qualify the existing component executor and full-state recovery before launch
- **not_fully_proven**: #587 — Qualify fail-closed lifecycle, kill switch and blinded operational visibility
- **not_fully_proven**: #782 — Choose a season-appropriate exploratory pilot and freeze the scientific contract
- **not_fully_proven**: #783 — Prove restore → selector → setter schema → receipt → frozen outcome end to end
- **not_fully_proven**: #588 — Lock the qualified pilot identity and finalize exactly one internal random draw
- **not_fully_proven**: #642 — Authorize and verify one randomized day-1 activation from the locked pilot
- **not_fully_proven**: #640 — Freeze the first assigned-day ITT outcome and blinded reproducible export
- **not_fully_proven**: #784 — Operate the complete blinded pilot and preserve every assigned day
- **not_fully_proven**: #785 — Reveal once, reproduce the frozen analysis and publish the campaign decision

Source touchpoints: `research/planner-efficacy`, `experiment_orchestrator`, `docs/runbooks/experiment-rollout.md`, `planning`.

## #586 — OPEN / future_owned

[C8][P2] Qualify a future atomic firmware policy engine with crash-safe recovery

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Future atomic firmware policy engine remains device-denied/deferred. No complete per-tick/recovery/worst-load heap proof or separate current OTA/protocol acceptance.

- **not_fully_proven**: Every consumer reads one atomic per-tick policy and invalid/corrupt/expired state returns to approved safe baseline.
- **not_fully_proven**: Worst-case heap/fragmentation/watchdog margins and independent recovery flashing are measured, not projected.
- **not_fully_proven**: Separate OTA authority and future protocol decision exist before vector mode can enable; current pilot remains unchanged.

Source touchpoints: `firmware`, `verdify_schemas`, `db/migrations`, `tests`, `docs`.

## #587 — OPEN / incomplete

[C1][P0] Qualify fail-closed lifecycle, kill switch and blinded operational visibility

No direct-proof receipt exists. Fresh physical readiness, executor/lifecycle fault matrix, authentic alert recovery, safe terminal state and restored vertical proof remain required. Current writer/Argo regressions are explicit. Retained October6 assessment, not new execution: Experiment services are Ready but all live draw/outcome/freeze/export/proof/exposure counts remain zero. Disabled deployment does not prove complete fault, kill/disable, recovery, alert routing and rollback qualification.

- **not_fully_proven**: Feature off means zero experiment calls; locked/armed cannot reopen readiness and emergency hold cannot actuate.
- **not_fully_proven**: Normal disable/ID clear/ordinary ownership restoration requires confirmed baseline; only an explicit facility-owned emergency-safe closure may state otherwise without relabeling it baseline.
- **not_fully_proven**: Alerts actually fire and clear through the owning delivery route, no comparative leakage occurs, and the exact-source rollback test is consumed by #783/#641.

Source touchpoints: `experiment_orchestrator`, `api/main.py`, `deploy/k8s`, `db/migrations`, `tests`.

## #588 — OPEN / incomplete

[C2][P0] Lock the qualified pilot identity and finalize exactly one internal random draw

Zero random draws/assigned outcomes; prospective scientific choice, qualified proof, immutable lock and exactly one draw/launch remain incomplete. No launch authority follows from this review. Retained October6 assessment, not new execution: Live randomization count0. No current scientific lock/draw is complete;#782/#783/#641 remain open. Do not finalize or recreate a draw from preparation.

- **not_fully_proven**: Current model/context/request limits, full source identity and #782 scientific choice match the lock byte-for-byte.
- **not_fully_proven**: Exactly one draw exists; duplicate/replacement/caller-time/secret attempts fail without leaking mapping or randomization material.
- **not_fully_proven**: Future first assignment boundary and schedule commitment reproduce; no physical randomized work occurs before the separate #642 authority.

Source touchpoints: `experiment_orchestrator/provider.py`, `experiment_orchestrator/launch_artifacts.py`, `research/planner-efficacy/switchback/v2_randomization.py`, `research/planner-efficacy/protocols`.

## #606 — OPEN / future_owned

[C8][P2] Register the twin build profile only through its owning fleet contract

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Twin fleet profile remains upstream-owned and must be delivered there. This repository mirror was not edited; no new profile acceptance is claimed.

- **not_fully_proven**: Owning registry and generated mirror agree and one verified immutable digest is consumable.
- **not_fully_proven**: Runtime is read-only/device-denied and has no undeclared build or network dependency.
- **not_fully_proven**: This enablement issue is not a blocker for the current #581/#642 component pilot.

Source touchpoints: `twin/Dockerfile`, `.agent-fleet/ci.yaml`, `deploy/k8s/components/firmware-twin`, `docs`.

## #638 — OPEN / future_owned

[C8][P2] Unify future manifest/vector wire schema and content identity offline

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Future wire schema/content identity remains offline/deferred. No all-consumer future protocol acceptance is established and no vector authority is enabled.

- **not_fully_proven**: Cross-language identity and actual service declaration/host call schemas agree; tests fail against the former incompatible interface.
- **not_fully_proven**: Restore admission creates the intended outbox and compiled commit/stage/activate/expiry/abort paths consume host bytes.
- **not_fully_proven**: Generalized vector mode remains off; closing this issue does not switch any active trial transport or claim physical qualification.

Source touchpoints: `verdify_schemas`, `db/migrations`, `firmware`, `ingestor`, `tests`.

## #639 — OPEN / incomplete

[C1][P0] Qualify the existing component executor and full-state recovery before launch

No direct-proof receipt exists. Fresh physical readiness, executor/lifecycle fault matrix, authentic alert recovery, safe terminal state and restored vertical proof remain required. Current writer/Argo regressions are explicit. Retained October6 assessment, not new execution: Retained C1 original callback/full48 evidence is not full lifecycle/executor failure/recovery/restored randomized qualification. Current proof receipts0; no execution/fault matrix was rerun.

- **not_fully_proven**: No prefix opens exposure; two fresh full-48 epochs with current generations are required for baseline and target.
- **not_fully_proven**: Exclusive bundles never interleave with ordinary/MCP/forecast/retry writers; shadow emits zero calls and nonrandom work has no assignment lineage.
- **not_fully_proven**: Recovery closes exposure first, yields to facility rescue, uses linked baseline-only work and verifies two epochs before ordinary-writer handoff. The qualified source receipt is reusable by #641 and #642.

Source touchpoints: `ingestor/tasks/component_experiment.py`, `experiment_orchestrator`, `verdify_schemas`, `db/migrations`, `tests`, `research/planner-efficacy`.

## #640 — OPEN / incomplete

[C3][P0] Freeze the first assigned-day ITT outcome and blinded reproducible export

Zero assignments/freezes/exports. Every genuine assigned day must survive ITT, including null/failure/rescue. Full retained pilot takes60 local days; final blinded freeze/reveal/reproduction cannot fit a short engineering sprint. Retained October6 assessment, not new execution: Live assigned outcomes/freezes/exports are0. No first randomized assigned day exists to freeze; synthetic source fixtures are not observed ITT outcomes.

- **not_fully_proven**: The first full assigned window is frozen twice to identical canonical rows/hashes and consumed by the analyzer without reshaping or reveal.
- **not_fully_proven**: A ledger reconciliation proves every assignment has exactly one row irrespective of exposure or outcome availability.
- **not_fully_proven**: Freezer cannot mutate assignments/read mapping; analyst sees frozen X/Y only. Missing center/DLI/gas and unselected resource fields remain truthfully unavailable.

Source touchpoints: `experiment_orchestrator/outcome.py`, `research/planner-efficacy/switchback/v2_outcomes.py`, `research/planner-efficacy/switchback/v2_day1_export.py`, `db/migrations`, `tests`.

## #641 — OPEN / incomplete

[C1][P0] Execute authorized orphan recovery and one separately attended physical proof

No direct-proof receipt exists. Fresh physical readiness, executor/lifecycle fault matrix, authentic alert recovery, safe terminal state and restored vertical proof remain required. Current writer/Argo regressions are explicit. Retained October6 assessment, not new execution: Live proof receipt count0. Retained controller recovery/C1 restoration is not a sealed baseline→aggressive→baseline GateP proof. Draft#855 remains unmerged; physical readiness and separate exact authority stay unfulfilled.

- **not_fully_proven**: Recovery retains predecessor failures, creates no nonbaseline exposure and earns no proof credit.
- **not_fully_proven**: Exactly one successor-attempt receipt binds baseline-before/aggressive/baseline-after to actual current source and observations.
- **not_fully_proven**: Mismatch closes exposure/revokes nonbaseline before facility-compatible recovery; final baseline-confirmed draft/shadow/closed, feature off, empty ID, vector off and zero exposure are evidenced.

Source touchpoints: `experiment_orchestrator/launch_control.py`, `db/migrations/222-experiment-v2-direct-physical-proof.sql`, `db/migrations/239-experiment-v2-orphaned-preclaim-recovery.sql`, `deploy/k8s`, `docs/runbooks/attended-convergence.md`.

## #642 — OPEN / incomplete

[C2][P0] Authorize and verify one randomized day-1 activation from the locked pilot

Zero random draws/assigned outcomes; prospective scientific choice, qualified proof, immutable lock and exactly one draw/launch remain incomplete. No launch authority follows from this review. Retained October6 assessment, not new execution: No locked draw or live randomized assignment. Exact launch/observed physical target/exposure/freezer/rollback acceptance remains downstream; no launch performed.

- **not_fully_proven**: Exact launch authority, unique assignment, frozen identity, observed physical target, two epochs and exposure lineage exist.
- **not_fully_proven**: Freezer can progress, singleton writer/public health hold and exact-source Argo is Synced + Healthy.
- **not_fully_proven**: Rollback evidence is current and first-day/export ownership hands to #640; closure does not claim experiment success.

Source touchpoints: `experiment_orchestrator/launch_control.py`, `deploy/k8s`, `ingestor/tasks/component_experiment.py`, `docs/runbooks/experiment-rollout.md`.

## #643 — OPEN / sprint

[C5][P1] Extend least-privilege database roles beyond the already-required experiment boundary

PARTIAL: least-privilege roles and runtime-client harness are merged. Later authentic endpoint Jobs and A→B→A reports are progress, but no complete final per-login allow/deny/pool-close/rollback plus full post-fixture reseal receipt is published. Current3/3 rehearsal health is not that proof.

- **not_fully_proven**: Live-role matrix allows intended functions and rejects unrelated DML, mapping reads and cross-workload capabilities.
- **not_fully_proven**: No credentials or Secret annotation values enter issue/log/backup artifacts; ordinary pods lack owner/migration authority.
- **not_fully_proven**: Public/planner/ingestor health and experiment integrity survive rollout/rollback; no unsolicited credential rotation occurs.

Source touchpoints: `db/migrations`, `deploy/k8s`, `tests`, `docs`.

## #644 — OPEN / sprint

[C4][P1] Integrate exact-SHA, path-aware and device-safe delivery receipts

PARTIAL: PR960–962 and successful authentic no-recursive-build receipt exist. Fleet owner PR agents#4747 is OPEN; runtime3648 failure is terminal history. Current production is OutOfSync/Degraded/Failed; 25/26 workload image specs match, ingestor differs. Finish exact-source authority/retry/cancel/concurrency/deployed-consumer matrix; #964 must restore the live adoption boundary.

- **not_fully_proven**: Exact-SHA results are durable, redacted and actionable; no recursive builds from pin commits.
- **not_fully_proven**: Builder cannot deploy/promote and ordinary validation cannot access device/OTA/runtime secrets; immutable digest consumer receipt identifies rollback pins.
- **not_fully_proven**: Verdify integration and owning fleet dependency are linked; current branch policy is measured rather than copied from an August snapshot.
- **not_fully_proven**: #322 — Replace remaining VM-era tests with current k3s delivery contracts
- **not_fully_proven**: #304 — Provide reproducible least-authority firmware and database diagnostic tooling
- **not_fully_proven**: #303 — Run real portable ESPHome compilation for declared firmware targets
- **not_fully_proven**: #390 — Gate firmware releases on topology, cycling, runtime safety and rollback evidence
- **not_fully_proven**: #399 — Prove the HA grow-light writer remains within the single-writer contract

Source touchpoints: `.agent-fleet/ci.yaml`, `deploy/k8s`, `docs`, `Makefile`.

## #670 — CLOSED / historical_closed

[C5][P1] Make backup artifacts sufficient to restore roles, ownership and memberships safely

Current backups and healthy CNPG replicas do not establish full restore/PITR/failover/parity, buffer survival or role rollback. Production remains original StatefulSet; no cutover occurred.

- **retained_historical_acceptance**: A clean restore succeeds using only declared backup/role artifacts and separately provisioned credentials, not a hidden local role seed.
- **retained_historical_acceptance**: Role/membership/ownership/ACL parity and no-elevation deny tests pass; artifacts contain no secret-bearing globals.
- **retained_historical_acceptance**: A scheduled backup produces the matching pair of integrity-checked artifacts and the runbook gives a tested restore order.

Source touchpoints: `deploy/k8s`, `deploy/k8s/components/experiment-v2-restore-rehearsal`, `db`, `docs/runbooks`.

## #671 — CLOSED / historical_closed

[C4][P1] Make Lab publishing failures and cache contention observable

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Failure is visible with class and bounded retry while active contention is not mislabeled corruption.
- **retained_historical_acceptance**: A failed publish never replaces good content with partial output; lock release/recovery is deterministic.
- **retained_historical_acceptance**: Current content hash/source/freshness is exposed and a successful subsequent publish clears the correct failure.

Source touchpoints: `scripts/Dockerfile.lab-publisher`, `scripts`, `deploy/k8s`, `tests`.

## #672 — CLOSED / historical_closed

[C5][P1] Make compressed-chunk ownership restoration a supported blocking rehearsal test

Current backups and healthy CNPG replicas do not establish full restore/PITR/failover/parity, buffer survival or role rollback. Production remains original StatefulSet; no cutover occurred.

- **retained_historical_acceptance**: Clean and adversarial restore fixtures pass through the supported ownership path or fail with an explicit actionable unsupported-state error.
- **retained_historical_acceptance**: No direct unsupported chunk mutation or silent narrowing eliminates the intended hostile test.
- **retained_historical_acceptance**: The real rehearsal consumes current artifacts and treats this invariant as blocking, with version and object/ACL evidence retained.

Source touchpoints: `db/migrations/tests/test-217-runtime-role-boundary.sql`, `deploy/k8s/components/experiment-v2-restore-rehearsal`, `db/migrations`, `tests`.

## #747 — CLOSED / historical_closed

[C1][P0] Verify corrected scheduled backups and current production recovery health

No direct-proof receipt exists. Fresh physical readiness, executor/lifecycle fault matrix, authentic alert recovery, safe terminal state and restored vertical proof remain required. Current writer/Argo regressions are explicit.

- **retained_historical_acceptance**: Scheduled success and artifact metadata are current and controller-owned; source retry regression tests pass.
- **retained_historical_acceptance**: One writer, stable generation, fresh 48-field truth and ordinary planner/data health are maintained.
- **retained_historical_acceptance**: Exact source/digest/Argo receipt exists or the issue remains explicitly open on that missing evidence.

Source touchpoints: `deploy/k8s`, `tests`, `docs/runbooks`.

## #749 — OPEN / incomplete

[C1][P0] Qualify current three-probe readiness and explicit safety dependencies

INCOMPLETE: north/east/west raw telemetry is fresh; south is null while diagnostics say active_probe_count4/probe_healthOK. Current writer is halted and Argo degraded. Reopened#949 is a real native predecessor. Preserve honest three-probe degraded-pass semantics and actual safety/contributor/source-bound packet.

- **not_fully_proven**: 3-of-4 degraded-pass is truthful; two or fewer probes, nonfinite aggregates, excess spread, stale evidence or semantic contradiction fail closed.
- **not_fully_proven**: Hydro is traced as absent from proof/selector/executor/outcome causal inputs; any new dependency is classified before use.
- **not_fully_proven**: Packet is read-only, current at every boundary and cannot grant proof or randomized authority; probe loss closes exposure before recovery.

Source touchpoints: `experiment_orchestrator/readiness.py`, `scripts`, `db/migrations/240-experiment-v2-readiness-reader-grants.sql`, `tests`.

## #750 — OPEN / incomplete

[C1][P0] Recovery/proof campaign: rebaseline current state and seal one safe physical receipt

No direct-proof receipt exists. Fresh physical readiness, executor/lifecycle fault matrix, authentic alert recovery, safe terminal state and restored vertical proof remain required. Current writer/Argo regressions are explicit. Retained October6 assessment, not new execution: Production is Synced/Healthy, 26 workload image specifications match, all 16 long-running workloads Ready, smoke 9/9 and pinned fleet census exactly one writer. These restore service evidence, not complete integrated physical qualification; #749/#641 remain open.

- **not_fully_proven**: Fresh source/Argo/runtime/backup receipts replace old point-in-time assertions and identify any remaining mismatch.
- **not_fully_proven**: #747, #749 and #641 have their own acceptance evidence; proof and orphan recovery never share credit.
- **not_fully_proven**: Final safe state and rollback receipt are verified; no randomized design/draw/launch is performed under this roll-up.
- **not_fully_proven**: #747 — Verify corrected scheduled backups and current production recovery health
- **not_fully_proven**: #749 — Qualify current three-probe readiness and explicit safety dependencies
- **not_fully_proven**: #641 — Execute authorized orphan recovery and one separately attended physical proof

Source touchpoints: `docs/runbooks/attended-convergence.md`, `deploy/k8s`, `experiment_orchestrator/readiness.py`, `experiment_orchestrator/launch_control.py`.

## #751 — OPEN / physical_or_seasonal

[C7][P2] Replace the failed south climate probe and diagnose hydro telemetry in a bounded window

PHYSICAL GAP: south climate triplet remains null; hydro chemistry and interior PAR/DLI are unavailable. No replacement, calibrated physical test or independent plumbing observation was performed. Firmware four/OK is not physical acceptance.

- **not_fully_proven**: South supplies credible fresh finite temperature/RH/VPD and is validated against reference/neighbors; hydro cause and restored or explicit unresolved state are documented.
- **not_fully_proven**: Contributor diagnostics become truthful and alerts resolve from telemetry, not manual deletion.
- **not_fully_proven**: No replacement becomes a surprise pilot gate or silently changes the locked analysis panel.

Source touchpoints: `config`, `firmware/greenhouse`, `ingestor`, `docs`.

## #775 — OPEN / incomplete

[C0][P0] Campaign: trustworthy greenhouse outcomes → qualified pilot → evidence-backed control

ROOT INCOMPLETE: C0 source/measurement repairs and historical S1 acceptances exist; current operational regressions#949/#964 prevent fresh qualification. Zero direct proof receipts, draws, assigned outcomes, freezes, exports or open exposures. Seven-issue S2 completion sprint is the next delivery boundary; C1–C3 and C4–C8 remaining goals are explicitly retained.

- **not_fully_proven**: Every issue open at the snapshot has exactly one owner role, stage, what/why/how, testable acceptance and disposition.
- **not_fully_proven**: The full native dependency graph is acyclic and equals the repository graph; no child is blocked by its own umbrella.
- **not_fully_proven**: The pilot ends in a reproducible decision, including an inconclusive/no-benefit result if that is what the evidence supports; broader efficiency claims remain gated.
- **not_fully_proven**: #778 — Investigate September 4 hot/dry peak and three-hour wetting interruption
- **not_fully_proven**: #779 — Re-run historical planner comparisons on a fixed sensor panel and fixed target contract
- **not_fully_proven**: #780 — Score as-of outdoor forecasts against outdoor truth and separate indoor response
- **not_fully_proven**: #781 — Commission a claim-safe water and electricity endpoint contract
- **not_fully_proven**: #581 — Experiment program: qualify, run and read out the bounded AI-admission pilot
- **not_fully_proven**: #644 — Integrate exact-SHA, path-aware and device-safe delivery receipts
- **not_fully_proven**: #427 — Prove active Hermes/MCP recovery and truthful non-authoritative worker health
- **not_fully_proven**: #75 — Observability program: prove actionable health from source through device
- **not_fully_proven**: #218 — Durability program: recoverable backups, measured RPO/RTO and safe HA option
- **not_fully_proven**: #359 — Control program: truthful crop corridors before evidence-led tuning
- **not_fully_proven**: #214 — Prove crop-deviation trigger → valid plan on the sole operational planner path
- **not_fully_proven**: #350 — Irrigation program: topology truth, overwatering protection and commissioned wall feed
- **not_fully_proven**: #16 — Hardware program: commission only the sensing and equipment justified by evidence
- **not_fully_proven**: #174 — Rehome only current Verdify admin surfaces to global SSO, fail closed
- **not_fully_proven**: #14 — Twin program: qualify declared oracle coverage before using divergence as a gate
- **not_fully_proven**: #638 — Unify future manifest/vector wire schema and content identity offline
- **not_fully_proven**: #586 — Qualify a future atomic firmware policy engine with crash-safe recovery
- **not_fully_proven**: #786 — Design the next comparison against a deterministic forecast-aware selector
- **not_fully_proven**: #787 — Plan measured heating, overnight and whole-resource economics as a separate claim

Source touchpoints: `planning`, `PROJECT_BOARD.md`, `MILESTONES.md`, `EPICS.md`, `LANES.md`, `SPRINTS.md`.

## #778 — CLOSED / historical_closed

[C0][P0] Investigate September 4 hot/dry peak and three-hour wetting interruption

Current telemetry/ledger and historical source repairs distinguish physical crop truth from controller credit. Scientific contract and root campaign remain incomplete.

- **retained_historical_acceptance**: Timeline reproduces the three-hour interruption with raw-row hashes, provenance and explicit unknowns.
- **retained_historical_acceptance**: The flat cumulative 600 and mister-today 157.38 counters are not labeled an exhausted 600-gallon budget without matching semantics/reset/limit evidence.
- **retained_historical_acceptance**: A named cause plus regression evidence, or a fail-closed unresolved constraint, is incorporated into #749; no unsafe wetting proof is allowed by an unexplained safety contradiction.

Source touchpoints: `firmware/lib`, `firmware/greenhouse`, `ingestor/tasks`, `db/migrations`, `research/planner-efficacy`.

## #779 — CLOSED / historical_closed

[C0][P1] Re-run historical planner comparisons on a fixed sensor panel and fixed target contract

Current telemetry/ledger and historical source repairs distinguish physical crop truth from controller credit. Scientific contract and root campaign remain incomplete.

- **retained_historical_acceptance**: Exact matched counts and excluded reasons reproduce from one command and immutable inputs.
- **retained_historical_acceptance**: No changing-probe aggregate crosses the epoch comparison; repaired probes require a new panel/version.
- **retained_historical_acceptance**: Conclusions explicitly distinguish descriptive association from causal identification and supply empirical variance/completeness inputs to study design.

Source touchpoints: `research/planner-efficacy/epoch_analysis.py`, `research/planner-efficacy/tests`, `docs/research`.

## #780 — CLOSED / historical_closed

[C0][P0] Score as-of outdoor forecasts against outdoor truth and separate indoor response

Current telemetry/ledger and historical source repairs distinguish physical crop truth from controller credit. Scientific contract and root campaign remain incomplete.

- **retained_historical_acceptance**: A fixture with correct outdoor forecast and different indoor VPD has zero forecast error, not indoor/outdoor difference.
- **retained_historical_acceptance**: No future-issued forecast enters a historical decision; lead/freshness and sample-count fields reach planner context.
- **retained_historical_acceptance**: Historical and live read-only probes reconcile raw/corrected priors; this fix does not silently retune a locked study.

Source touchpoints: `db/migrations/101-data-trust-and-outcome-views.sql`, `ingestor/tasks/forecast.py`, `scripts/gather-plan-context.sh`, `research/planner-efficacy`.

## #781 — CLOSED / historical_closed

[C0][P0] Commission a claim-safe water and electricity endpoint contract

Current telemetry/ledger and historical source repairs distinguish physical crop truth from controller credit. Scientific contract and root campaign remain incomplete.

- **retained_historical_acceptance**: Frozen daily ledger reproduces 5,047 = 1,361 + 3,465 + 221 gallons for the reviewed 22-day snapshot and explains any later revision.
- **retained_historical_acceptance**: Each electricity result names circuit scope; vent-open minutes are never motor energy, and partial measured versus whole modeled totals are never subtracted as waste.
- **retained_historical_acceptance**: Endpoint definition includes uncertainty, minimum coverage, missingness/reset and cost-input policy; absent gas/DLI remain null, not zero-benefit proof.

Source touchpoints: `db/migrations`, `ingestor/tasks/daily.py`, `api/main.py`, `research/planner-efficacy/switchback/v2_outcomes.py`, `scripts`.

## #782 — OPEN / incomplete

[C0][P0] Choose a season-appropriate exploratory pilot and freeze the scientific contract

INCOMPLETE: data-truth predecessors have historical accepted receipts, but qualified physical targets/contributors, empirical covariance/joint power, exact endpoint window and prospective seasonal choice remain unaccepted. No draw/assignment exists; choose the justified season/question before lock.

- **not_fully_proven**: An explicit decision document chooses pilot versus confirmatory scope and states exactly which claim can be tested with current meters.
- **not_fully_proven**: Power replay is reproducible; include .99^30 ≈ .740 and .95^30 ≈ .215 all-pairs retention sensitivities rather than assuming .9995 reliability.
- **not_fully_proven**: Calendar is future and seasonally justified; 06:00–24:00 remains 18 elapsed hours on the fall clock-change day, with any full-day scheduler implications tested separately.
- **not_fully_proven**: No draw has occurred before design approval/qualification; model is current source-pinned Luna/medium, not stale Sol issue prose.

Source touchpoints: `research/planner-efficacy/protocols/direct-launch-basis-v1.json`, `research/planner-efficacy/protocols/planner-switchback-v2.template.yaml`, `research/planner-efficacy/switchback/v2_power.py`, `research/planner-efficacy/switchback/v2_analysis.py`, `research/planner-efficacy/switchback/v2_selector.py`.

## #783 — OPEN / incomplete

[C1][P0] Prove restore → selector → setter schema → receipt → frozen outcome end to end

INCOMPLETE: real current restored-DB selector→setter schema→receipt→freezer→export→analyzer qualification still lacks an accepted whole-path receipt. Fixtures and separate reseals do not substitute; retain #782/#639/#587 native predecessors.

- **not_fully_proven**: One command/artifact index reproduces the whole path twice with byte-identical canonical hashes and no data reshaping between freezer and analyzer.
- **not_fully_proven**: First-day, missing-day, zero-exposure, reset and failed-delivery rows exist by assignment, never filtered by exposure.
- **not_fully_proven**: Device-denied restore contains no production credentials in logs/artifacts; minimum randomizer/lifecycle/executor/freezer/blinded-analyst boundaries pass. Current source and failure matrix are recorded before #588.

Source touchpoints: `deploy/k8s/components/experiment-v2-restore-rehearsal`, `experiment_orchestrator`, `research/planner-efficacy/switchback/v2_day1_export.py`, `research/planner-efficacy/switchback/v2_analysis.py`, `db/migrations/tests`, `tests`.

## #784 — OPEN / incomplete

[C3][P1] Operate the complete blinded pilot and preserve every assigned day

Zero assignments/freezes/exports. Every genuine assigned day must survive ITT, including null/failure/rescue. Full retained pilot takes60 local days; final blinded freeze/reveal/reproduction cannot fit a short engineering sprint. Retained October6 assessment, not new execution: 0 live assignment outcomes/freezes/exports. No genuine blinded60-day pilot can be operated/accounted for yet; every eventual failed/null/fallback day must remain assigned.

- **not_fully_proven**: All locked assignments are accounted for, including incomplete/no-exposure/aborted days; no replacement pair, redraw or post-hoc endpoint change occurs.
- **not_fully_proven**: Blinded integrity and safety reviews have receipts at the cadence declared before launch; departures are preserved.
- **not_fully_proven**: Study ends safe with stopped admission, closed exposure, frozen outcome/deviation/fidelity/environment hashes ready for #785.

Source touchpoints: `experiment_orchestrator`, `research/planner-efficacy/protocols`, `docs/runbooks/experiment-rollout.md`, `research/planner-efficacy/switchback/v2_day1_export.py`.

## #785 — OPEN / incomplete

[C3][P1] Reveal once, reproduce the frozen analysis and publish the campaign decision

Zero assignments/freezes/exports. Every genuine assigned day must survive ITT, including null/failure/rescue. Full retained pilot takes60 local days; final blinded freeze/reveal/reproduction cannot fit a short engineering sprint. Retained October6 assessment, not new execution: No genuine pilot export/reveal/readout exists. A reproducible final adverse/no-benefit/inconclusive decision remains downstream; source fixtures are not efficacy.

- **not_fully_proven**: Independent rerun from frozen artifacts produces identical tables and decision; reveal occurs once with audited authority.
- **not_fully_proven**: Report permits adverse/no-benefit/inconclusive conclusions and gives no yield/profit/whole-resource/other-season claim beyond measurements.
- **not_fully_proven**: #775/#581 close only on this completed decision boundary; #786/#787 and deferred work have explicit go/no-go rationale, not automatic promotion.

Source touchpoints: `research/planner-efficacy/switchback/v2_analysis.py`, `research/planner-efficacy/protocols/analyzer-interface-v2.golden.json`, `docs/research`, `site`.

## #786 — OPEN / future_owned

[C8][P2] Design the next comparison against a deterministic forecast-aware selector

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Merged deterministic forecast comparator preparation is unregistered. Final go/no-go depends on#785; real training/covariance/power and future separate lock/authority remain unavailable.

- **not_fully_proven**: A go/no-go decision is linked to #785, with a prespecified comparator and no reused revealed schedule.
- **not_fully_proven**: Offline command replay is not described as a physical climate or resource effect.
- **not_fully_proven**: Any new trial has its own lock/draw/authority and a claim that matches its measured endpoint.

Source touchpoints: `research/planner-efficacy`, `experiment_orchestrator`, `docs/research`.

## #787 — OPEN / future_owned

[C8][P2] Plan measured heating, overnight and whole-resource economics as a separate claim

Comparator, twin, atomic transport, MPC and whole-resource economics remain future owned requirements with explicit limitations. No whole gas/electricity/water, crop yield or AI superiority claim is supported by this review. Retained October6 assessment, not new execution: Whole-resource/overnight specification is preparation. Current partial meters, ambiguous shared water, absent gas/interiorDLI/calibration/cost do not support whole-resource or profit claims.

- **not_fully_proven**: Each claimed resource has eligible measured coverage and calibration/uncertainty; modeled proxies remain secondary.
- **not_fully_proven**: A new prospective study can test climate improvement and resource benefit as stated, or the claim is explicitly narrowed.
- **not_fully_proven**: No hardware purchase, gas tuning, yield/profit claim or seasonal controller expansion follows automatically from the current pilot.

Source touchpoints: `research/planner-efficacy`, `db`, `config`, `docs/research`.

## #801 — OPEN / incomplete

[VP-02] Exclude www-dev and proposals-hello from the verdify-tier1-forward HostRegexp

OPEN: source still lacks www-dev/proposals-hello exclusions; agents#4461 is open. Public reachability by priority shadow is not ownership acceptance. Preserve the exact-object, hook-free route delivery contract and writer continuity.

- **not_fully_proven**: Source and live forwarder exclude both named external routes.
- **not_fully_proven**: Both hosts reach their owners with public/TLS behavior verified and ordinary Verdify hosts preserved.

Source touchpoints: .

## #802 — OPEN / incomplete

[VP-01] Put verdify-descheduler under Argo (manual) with Git equal to live enforce mode

OPEN: source descheduler still dry-run; live enforce discrepancy/adoption remains unaccepted. agents#4451 is open; do not apply stale source or disarm enforcement during review.

- **not_fully_proven**: Seven objects adopted under manual parented Argo with unchanged enforce specs/UID/generation.
- **not_fully_proven**: Namespace ownership, residual writer grant and tracking scope remain exact.
- **not_fully_proven**: Natural enforce Job and live sync-classification audit pass without dry-run.

Source touchpoints: .

## #835 — CLOSED / historical_closed

Fix reconnect equipment snapshot provenance and stale relay alerts

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Fenced fresh canonical state capture without cache inference.
- **retained_historical_acceptance**: Snapshot_taken only on complete persisted burst; incomplete otherwise.
- **retained_historical_acceptance**: Focused reconnect tests plus live readback and monitor-owned cited alert resolution.

Source touchpoints: `ingestor/ingestor.py`, `tests/test_ingestor_gap_snapshot.py`.

## #862 — CLOSED / historical_closed

Lab publisher: warm-cache init rescans the public tree for ~5 min, pushing runs past the 10-min schedule and the 20-min freshness window

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Bound warm-cache initialization with measured scan timings and unchanged safety guard.
- **retained_historical_acceptance**: Natural publisher cadence and fresh_until availability satisfy the declared contract.
- **retained_historical_acceptance**: Source/runtime receipt and preserved failure/rollback data distinguish active work from stall.

Source touchpoints: `scripts/prepare-lab-cache.sh`, `deploy/k8s/components/lab-site/lab-publisher.yaml`.

## #949 — OPEN / regression

[C4][P1] Bound planner-policy drift holds and prove autonomous safe convergence

REGRESSION: current stage state is halted (run 76d18133852b490680bce2639e6d8397), generation13. Six repeated blocked_stage messages in 30 minutes and 17 drift warnings per pass. No new confirmed setpoints in the captured24h (three observed rows). Preserve the prior two-hour acceptance as history, reopen for bounded recovery/current plan convergence; do not erase state, replay approval or raise cap.

- **contradicted_current**: Every held field has actual desired/readback provenance and an explicit safe convergence or bounded hold disposition.
- **contradicted_current**: No repeated broad device push, cap increase, fabricated confirmation or replay of expired approval; delivery failure/cancellation remains terminal history.
- **contradicted_current**: Natural or explicitly scoped plan change converges through permitted stages, with one writer, timely tasks, fresh confirmation, and a two-hour exact-source receipt.

Source touchpoints: `ingestor/tasks/dispatcher.py`, `tests`.

## #950 — CLOSED / historical_closed

[C4][P1] Restore the ingestor Slack credential-file contract and operator delivery

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Source-rendered and running file/env contracts agree; token remains secret and existing credential is preserved.
- **retained_historical_acceptance**: Natural evening/midnight/morning delivery succeeds and retries are bounded with actionable failures.
- **retained_historical_acceptance**: Intended alert path is checked independently of brief success; no test message or credential rotation is sent by this triage.

Source touchpoints: `ingestor/config.py`, `ingestor/tasks`, `deploy/k8s`.

## #951 — CLOSED / historical_closed

[C4][P1] Restore authenticated greenhouse camera snapshots after connection refusals

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: Both authenticated camera requests return current valid JPEGs with the declared TLS identity; no broad/admin endpoint or secret is exposed.
- **retained_historical_acceptance**: A natural verdify-vision Job succeeds through capture and analysis; missing/partial/stale input still fails truthfully.
- **retained_historical_acceptance**: Argo aggregate degradation is independently rechecked; old failed Jobs are preserved until disposition, never deleted merely to make health green.

Source touchpoints: `scripts/frigate-snapshot.py`, `deploy/k8s/vision`.

## #952 — CLOSED / historical_closed

[C4][P1] Preserve Verdify Grafana monitoring identity across pod replacement

Current Argo is Failed/Degraded; a green API health snapshot is narrower than exact delivery, authenticated behavior and real fault/recovery proof. Missing compile, replay, release and runtime receipts stay explicit.

- **retained_historical_acceptance**: A least-scope service identity survives or is recreated deterministically after replacement without printing or rotating credentials unsolicited.
- **retained_historical_acceptance**: Local and external authenticated probe evidence succeeds, negative unauthenticated behavior remains denied, and related alerts recover.
- **retained_historical_acceptance**: Rendered/source/runtime identity and rollback are recorded; UI availability alone is insufficient.

Source touchpoints: `deploy/k8s/components/grafana`.

## #953 — OPEN / sprint

[C4][P2] Reconcile Iris planning skill lookup and required reference inventory

PARTIAL: actual worker inventory passes102/102 references,88 indexed skills, zero missing paths. Natural SUNRISE and MIDNIGHT ledger rows are plan_written. Bounded24h worker log still includes skill_manage errors (including empty-name lookup); exact first post-rollout trigger→Hermes session→plan validation and zero required-reference retry evidence remains unsealed. Inventory alone cannot close the issue.

- **not_fully_proven**: Every required prompt reference resolves in the running profile at exact source/config identity.
- **not_fully_proven**: Next natural required cycle writes a valid plan without missing-skill/reference retries; no extra provider calls solely for triage.
- **not_fully_proven**: No static worker-ready signal is presented as plan success; existing ledger/source proofs remain intact.

Source touchpoints: `hermes/iris`.

## #963 — OPEN / incomplete

Greenhouse ESPHome text sensor moisture_exchange publishes >255-char JSON as state (HA drops it to unknown, logs ERROR every update)

OPEN SOURCE-CONFIRMED BUG: firmware publishes moisture-exchange JSON as text state; current native ingestor log contains >255-character payload. HA source report records unknown/error. Qualify a short-state/structured-detail consumer contract offline; OTA remains separately bounded. Not hidden inside S2.

- **not_fully_proven**: Every possible published state fits the HA255-character contract; diagnostic detail is preserved through an explicit tested consumer schema.
- **not_fully_proven**: Native ingestor/planner/Grafana consumers remain compatible; actual HA state is available without repeated overlength errors.
- **not_fully_proven**: Exact binary/source and firmware release/rollback evidence exist; no OTA occurs solely for triage.

Source touchpoints: `firmware/greenhouse/hardware.yaml`, `firmware/greenhouse/controls.yaml`, `ingestor/entity_map.py`.

## #964 — OPEN / sprint

[C4][P0] Recover failed production promotion and schedulable Lab/band refresh

OPEN P0: full promotion Failed October6; Lab replacement/publisher/band refresh are unschedulable against node7 affinity. Last published success October6 07:16:53Z, expired07:36:53Z. Recover supported placement and full exact-source adoption, then three natural publisher runs and band-refresh completion; no force detach or uncoordinated node/NFS action.

- **not_fully_proven**: Exact intended revision/render and running image specifications agree; full operation Succeeded and Argo Synced/Healthy; required migrations/bootstrap/readiness hooks have explicit results.
- **not_fully_proven**: Replacement Lab pods become ready using supported storage placement; existing public tree and recovery data survive. At least three natural publisher runs complete on the declared cadence with current fresh_until and served/success generation agreement.
- **not_fully_proven**: Band-refresh Jobs schedule and complete at natural cadence, with fresh downstream rows and no hidden backlog/deadline failure.
- **not_fully_proven**: Singleton writer, fresh telemetry and authenticated API/MCP behavior remain proven. Any socket-monitor UNKNOWN remains unaccepted until a complete observable census succeeds.
- **not_fully_proven**: Exact prior pins/specs/cache/PVC recovery coordinates and owner decisions are published; no DB/PVC pruning or unrelated estate mutation.

Source touchpoints: `deploy/k8s`, `scripts`, `docs`.

## #965 — OPEN / incomplete

[C4][P1] Separate API grow-light command intent from observed equipment history

OPEN SOURCE/RUNTIME-CONFIRMED BUG: running API light endpoint writes requested on/off into observed equipment history without invoking a writer. No live POST executed. Separate command intent from authentic observations, prevent false runtime/resource credit, and audit affected provenance.

- **not_fully_proven**: Either return an explicit unsupported/disabled result without writing observed history, or deliver a typed command through the existing owning HA/setpoint writer with authorization, idempotency and terminal failure semantics. Do not introduce a second device writer.
- **not_fully_proven**: Only authentic source-bound readback writes observed equipment state. A requested on command while real state remains off cannot fabricate an on interval or resource credit.
- **not_fully_proven**: Focused positive and rejected/failed/timeout tests distinguish intent, delivery and observation; ordinary auth and greenhouse/circuit boundaries hold.
- **not_fully_proven**: Exact source/digest/config adoption and read-only live contract verification are published. Any physical command test has a bounded operator-safe scope and actual observation; no live command is sent merely for triage.
- **not_fully_proven**: Existing affected history is audited for provenance and explicitly bounded; no broad rewrite of historical outcomes, assigned-day denominators or resource claims.

Source touchpoints: `api/main.py`, `tests`, `ingestor/tasks/ha.py`.
