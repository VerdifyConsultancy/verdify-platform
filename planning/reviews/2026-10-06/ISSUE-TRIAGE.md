# October 6 full open-issue acceptance review

Capture: October 6 UTC / October 5 Denver. 63 open issues; 25 closed historical nodes. Every current acceptance checkbox is listed below; #317/#801/#802 additionally retain reviewer-derived conditions from their bug/Verify contracts. A partial receipt never closes the containing issue.

See [platform review and S2](NEXT-SPRINT.md), [native dependencies](NATIVE-DEPENDENCIES.md) and [machine-readable matrix](issue-triage.json).

## #14 — [C8][P2] Twin program: qualify declared oracle coverage before using divergence as a gate

Disposition: deferred; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Future device-denied twin remains deferred. Current source/build/output/coverage and negative oracle receipts are not a full current physical-parity proof. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Coverage and limitations are explicit; unsupported/default-only fields cannot produce a full-parity PASS.
- **not_fully_proven**: Source/build/runtime and actual read-only output receipts exist, with no startup compiler/pip downloads.
- **not_fully_proven**: The current pilot remains independent; a future twin gate is adopted only after useful positive and negative evidence.

## #16 — [C7][P2] Hardware program: commission only the sensing and equipment justified by evidence

Disposition: deferred; owner: Operator with data/control lead; stage: C7.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Physical inventory/commissioning remains deferred. Missing south, hydro, interior-light and chemistry channels remain unavailable; no installation/physical acceptance was performed. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Each physical item has a named measurement/control need, commissioning worksheet and explicit enabled/disabled state.
- **not_fully_proven**: No center-fertilizer path contradicts #434; absent sensors are not synthesized as real observations.
- **not_fully_proven**: Operator windows and seasonal triggers are tracked without guessed dates or blanket launch blockers.

## #31 — [C8][P2] Close twin setpoint and actuator coverage gaps with generated consumer truth

Disposition: deferred; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Generated consumer/oracle source does not establish live input coverage, meaningful nondefault divergence or current read-only twin output. Do not reuse the old absent-table claim as current evidence. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Every active consumer is covered or explicitly excluded from the oracle claim with a reason.
- **not_fully_proven**: Missing required input and a known nondefault divergence fail loudly; identical-source self-diff passes.
- **not_fully_proven**: No twin result can qualify full current 48-field physical exposure or new transport by default.

## #45 — [C7][P2] Commission soil and runoff feedback before enabling irrigation decisions

Disposition: blocked; owner: Operator with data/control lead; stage: C7.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Seven-day audit: no input/runoff pH/EC; south soil channels are entirely zero. Calibration and topology remain uncommissioned. Nonzero west moisture does not establish validity. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Required channels produce credible calibrated data, not merely nonzero rows; current gap counts are measured rather than assumed four.
- **not_fully_proven**: Feedback check passes and requirements/alerts close from actual accepted measurements.
- **not_fully_proven**: Unknown chemistry, sensor or plumbing state remains visibly unavailable and cannot enable fertilizer or soil feedback.

## #49 — [C5][P1] Audit and close historical suppressed sensor alerts without false count-parity assumptions

Disposition: selected_s2; owner: Data/platform lead; stage: C5.

Current exact-ID query: 61 historical suppressed sensor_offline rows remain unresolved. Require original ID list, narrow cleanup, idempotency and reversal; raw/canonical counts are not acceptance.

- **not_fully_proven**: Only the reviewed historical suppressed sensor_offline IDs are updated; a second run changes zero rows.
- **not_fully_proven**: Before/after receipt lists non-secret IDs/counts and verifies no active alert was hidden.
- **not_fully_proven**: Forward lifecycle behavior remains correct and reversal data is retained for the exact touched fields.

## #51 — [C7][P2] Calibrate CO2 against a real reference and publish measurement uncertainty

Disposition: incomplete; owner: Operator with data/control lead; stage: C7.

Current CO2 is about 3067 ppm. Reference-pair calibration and tolerance evidence are absent; preserve uncalibrated status.

- **not_fully_proven**: New coefficients derive from recorded field pairs and pass the predeclared independent tolerance.
- **not_fully_proven**: Plausibility guards and units remain correct, with uncertainty visible to consumers.
- **not_fully_proven**: If the reference/window is unavailable, retain uncalibrated status without guessing anchors or buying equipment implicitly.

## #52 — [C7][P2] Define species-appropriate seasonal dormancy care from operator observations

Disposition: deferred; owner: Operator with data/control lead; stage: C7.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Species/crop observations and a supported seasonal care decision are unavailable to this remote review. No change is made; keep seasonal decision deferred. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: A documented no-change or specific seasonal-care decision is supported by actual crop observations.
- **not_fully_proven**: Any new dormancy/light/dry-down setting is versioned, reversible and cannot silently alter a locked study.
- **not_fully_proven**: No species-wide agronomic or yield claim is made from uncalibrated DLI or a house-average proxy.

## #75 — [C4][P1] Observability program: prove actionable health from source through device

Disposition: incomplete; owner: Platform lead; stage: C4.

S1 closed eight bounded children. Both authenticated Grafana probes are now up; source-to-live and one-writer receipts pass. Six missing Iris skill lookups remain. Monitoring umbrella still requires its broader fault/delivery acceptance.

- **not_fully_proven**: Each child has a failing and recovering real or isolated fault receipt and a clear owner/runbook.
- **not_fully_proven**: No inactive PrometheusRule or static-green worker is accepted as live monitoring.
- **not_fully_proven**: Historical closed children remain history; current coverage and omissions are documented.

## #218 — [C5][P1] Durability program: recoverable backups, measured RPO/RTO and safe HA option

Disposition: incomplete; owner: Data/platform lead; stage: C5.

Latest paired backup succeeded, but current rehearsal ledgers stop at 270 versus production 274. Source/A/B whole-policy parity remains unqualified after the retained 120.554741-second timeout. S2 targets recovery, durable ingest and runtime-role proof; no production database cutover.

- **not_fully_proven**: Backup, restore, ingest-loss and HA children provide measured evidence rather than deployment counts.
- **not_fully_proven**: RPO/RTO and residual failure domains are explicitly reported, with PITR claims only after a point-in-time restore.
- **not_fully_proven**: No broad live DB mutation is authorized by closing the planning or design tasks.

## #245 — [C5][P1] Execute a separately bounded database cutover only after parity and rollback proof

Disposition: blocked; owner: Data/platform lead; stage: C5.

Production remains verdify-db-0. All three rehearsal clusters are healthy but at migration 270, four behind production. Complete parity, writer fencing, cutover packet and post-cutover bake are unproved. S2 does not authorize a cutover.

- **not_fully_proven**: A rehearsal proves all writers are quiescent and measures downtime, parity, RPO/RTO and both rollback boundaries.
- **not_fully_proven**: Production source/running DB authority and health are verified through at least the declared 60-minute initial observation and seven-day durability bake.
- **not_fully_proven**: No old database/PVC removal before separate verified retention/decommission criteria; no unqualified sub-minute zero-loss promise.

## #296 — [C6][P1] Consider slow-hysteresis firmware irrigation feedback only after dispatcher evidence

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Feedback inputs are uncommissioned and south channels zero. Existing source/preparation does not justify firmware feedback enablement; a measured no-build or independently qualified implementation decision remains open. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Either a documented no-build decision or a qualified implementation is delivered; no automatic expansion occurs.
- **not_fully_proven**: With commissioned probes a saturated zone suppresses eligible drip conservatively, without duplicate dispatcher/firmware control.
- **not_fully_proven**: Missing/failed sensors, reboot, manual rescue and maximum limits remain safe; physical enablement waits for commissioning.

## #297 — [C6][P1] Add saturation alerts and a commissioned dispatcher-side drip-skip loop

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Fresh soil target rows remain Canna Lily/Canna Lily/Unknown with saturation80/80/75. Alert-only preparation is separate from commissioned persistence/hysteresis and actual eligible skipped drip. No threshold/actuation change. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Sustained saturation triggers and recovers with explicit sensor validity; no stale seed is treated as crop truth.
- **not_fully_proven**: One eligible saturated zone’s scheduled drip is truthfully skipped/shrunk and recorded, with safe fallback for invalid evidence.
- **not_fully_proven**: No OTA, EC closed-loop dosing or guessed day-mask mitigation is required; alert-only implementation can proceed before physical enablement.

## #298 — [C7][P2] Rebaseline actual crop, pot and probe topology before feedback control

Disposition: incomplete; owner: Operator with data/control lead; stage: C7.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Live soil seeds still retain Canna Lily/Unknown names and remote telemetry cannot establish physical crop/substrate/probe positions. Actual operator inventory remains the predecessor for threshold commissioning. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Repo and DB map agree with observed physical positions and crop/substrate identities; no Canna-Lily residue is carried as current fact.
- **not_fully_proven**: Each sensor has source ID, location, validity/calibration state and explicit unassigned/disabled status when appropriate.
- **not_fully_proven**: No automatic irrigation is enabled by a topology import alone; #398/#45 own threshold and feedback commissioning.

## #299 — [C6][P1] Preserve center-mister re-fire protection through topology and reset recovery

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Existing center re-fire protection is preserved; current routing/reset/re-fire preservation and release receipt are not proved by this telemetry scan. Historical live protection is not a current topology release qualification. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: No origin or restart can re-fire before the declared protection permits; hard safety remains dominant.
- **not_fully_proven**: Eligible re-fire resumes correctly rather than staying latched without reason.
- **not_fully_proven**: Source/firmware/cycle evidence shows protection survived #434-related changes with a rollback canary.

## #303 — [C4][P1] Run real portable ESPHome compilation for declared firmware targets

Disposition: blocked; owner: Platform lead; stage: C4.

Declared fleet compile/negative-fixture steps exist, and firmware input tree is unchanged from the previously reviewed source. A declaration and green native C++ tests are not executed ESPHome evidence: retained CI output lacks the full secretless compile receipt. Keep portable positive/negative acceptance open.

- **not_fully_proven**: Each declared target actually compiles from a clean checkout; a deliberately broken generated service declaration fails the lane.
- **not_fully_proven**: No network device, secret-bearing configuration or OTA permission is required for compilation.
- **not_fully_proven**: Artifacts bind exact source and toolchain and feed #390 without being labeled physically qualified.

## #304 — [C4][P1] Provide reproducible least-authority firmware and database diagnostic tooling

Disposition: incomplete; owner: Platform lead; stage: C4.

Tools and generated CI contract exist; current portable toolchain, clean real compile and least-authority delivery matrix remain unqualified. No generated fleet contract was hand-edited.

- **not_fully_proven**: A clean environment reports pinned tool versions and runs the declared compile fixture reproducibly.
- **not_fully_proven**: No tool install grants Secret, exec, device or cross-namespace access; absence of credentials fails safely.
- **not_fully_proven**: Owning fleet delivery and generated repo references agree; no redundant image is introduced solely for convenience.

## #317 — ArgoCD: unscoped sync operations on verdify-prod-dark get rewritten to a stale 2-resource selective scope

Disposition: incomplete; owner: Platform lead; stage: C4.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Every-full-sync atomic terminal-state clearing workaround is in current runbook/source. Last operation was full but this triage performed no sync. Underlying stale-selector/cached-result failure is not proved removed; preserve upstream defect and exact hook/run identity requirement. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Plain full sync does not inherit previous resource selectors or cached results.
- **not_fully_proven**: Exact hooks and running digests independently prove the intended full operation.

## #324 — [C6][P1] Extend zonal band lineage compatibly for future deterministic control

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Compatible zonal/source fields are preparation. Complete per-zone/legacy round-trip, drift guards and separate future consumer decision remain unverified; absent center sensing stays unavailable. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Per-zone and legacy fixtures round-trip through schema, ingest and read APIs without ambiguous field meaning.
- **not_fully_proven**: No new property lacks a model/readback/drift guard, and unavailable center measurements remain unavailable.
- **not_fully_proven**: Only a separately reviewed future control design consumes new zonal authority.

## #350 — [C6][P1] Irrigation program: topology truth, overwatering protection and commissioned wall feed

Disposition: incomplete; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Irrigation physical/chemistry feedback remains uncommissioned. Center-only climate/wall-only fertilizer/source stop safety are distinct from physical delivery and accepted crop/substrate protection. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Software allows only the approved plumbing/origin combinations and fails closed without required calibration.
- **not_fully_proven**: Overwatering-safe feedback is commissioned on actual crop/substrate sensors before actuation.
- **not_fully_proven**: Physical feed, crop safety/yield and irrigation water claims require their own observed evidence.

## #359 — [C6][P1] Control program: truthful crop corridors before evidence-led tuning

Disposition: incomplete; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Current API separates legacy binary diagnostics from credit but physically qualified target evidence is unavailable. No tuning/AI safety delegation is justified by this audit. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Measurement and localized safety are trustworthy independently of AI credit.
- **not_fully_proven**: Every tuning proposal cites fixed-panel realized climate/resource evidence and explicit guardrails/replay/rollback.
- **not_fully_proven**: No PID arm, unvalidated response model, unmeasured efficiency claim or firmware safety delegation to AI enters the current pilot.

## #361 — [C6][P1] Reassess diurnal anchors only after solar parity and measured corridor evidence

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No anchor retune performed. Band/solar lineage and measured corridor/readout predecessors remain open; future bounded safety/readback/rollback acceptance remains deferred. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: No anchor retune precedes source/solar/band truth or changes a locked study.
- **not_fully_proven**: Candidate changes have explicit temperature/VPD safety limits and observed evidence, not target-hugging intent.
- **not_fully_proven**: Any accepted implementation is versioned with current source/readback and rollback values.

## #367 — [C6][P1] Consolidate anti-chatter into one explicit dwell contract

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No unified dwell release, observed parity/chatter/starvation receipt or current OTA acceptance exists. Prior comments deferred this broad consolidation; preserve existing safety floors. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Exactly one documented dwell authority governs ordinary transitions and all safety exceptions remain explicit.
- **not_fully_proven**: Boundary/reboot/override tests and #299 center-mister re-fire invariants pass.
- **not_fully_proven**: Measured starts/runtime and behavior parity show no new chatter or unsafe starvation; #390 release evidence is present.

## #368 — [C6][P1] Define shared sensor jump, flatline and contributor-integrity semantics

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Fresh raw north/east/west have values while south is missing; diagnostics report4/OK. This contradiction remains real. Shared validity/jump/flatline semantics and firmware-qualified consumer behavior are incomplete. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Synthetic impossible jump/stuck cases are detected without suppressing legitimate steady values.
- **not_fully_proven**: Control consumes explicitly valid inputs; degraded quorum and true SENSOR_FAULT behavior are documented and tested, not conflated.
- **not_fully_proven**: No silent sensor substitution changes trial endpoints; any firmware portion passes #390 before OTA.

## #369 — [C6][P1] Remove dead tunables and code from a verified consumer inventory

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No verified complete live-consumer removal inventory or measured code/entity reduction is established. Defer removals until exact consumer and release evidence. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Each removed property has zero required live consumer or an explicit tested migration; no dangling generated IDs or docs remain.
- **not_fully_proven**: Net-negative unused code/entity counts are measured and behavior/safety replay is unchanged.
- **not_fully_proven**: No active trial identity changes silently, no safety control is made AI-writable, and #390 release checks pass for firmware changes.

## #370 — [C6][P1] Consolidate equipment conflict resolution into one pure arbitration function

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Pure conflict/origin arbitration preparation is not exhaustive real relay/reset/partial-delivery and current firmware-release proof. No control rewrite was made. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Exhaustive conflict/origin fixtures produce one deterministic safe result and an auditable block reason.
- **not_fully_proven**: Reverse conflicts, manual rescue, restart and partial delivery cannot energize incompatible relays.
- **not_fully_proven**: Native/invariant/replay/compile and #390 physical release checks pass without unapproved control-policy changes.

## #378 — [C6][P1] Decide corridor widths from fixed-target crop and resource evidence

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No completed pilot/readout exists and physical target evidence is unqualified. Keep corridor-width decisions dependent on fixed-target realized evidence, with no retrospective endpoint retune. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: The decision distinguishes temperature versus VPD and cannot use attributable credit as physical compliance.
- **not_fully_proven**: No retrospective redefinition rewrites the completed study endpoint.
- **not_fully_proven**: Any live band change has bounded scope, exact rollback values and required operator/release authority.

## #379 — [C8][P2] Gate grey-box forecasting and MPC on validated response models and simpler baselines

Disposition: blocked; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Forecast/MPC model coverage and held-out response/calibration gates are not established. No new PID/MPC arm or unmeasured efficacy claim is admitted. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Held-out calibration/response gates pass before any counterfactual efficacy or MPC proposal.
- **not_fully_proven**: No PID treatment arm or new outer-loop control enters the current experiment.
- **not_fully_proven**: A separately reviewed bounded implementation/release plan exists for any justified controller change.

## #382 — [C5][P1] Persist ingestor queue and spool across restart and node loss

Disposition: selected_s2; owner: Data/platform lead; stage: C5.

Production retains the ingestor state PVC and existing buffered-event implementation. Full accepted-event restart/node-loss, capacity, backlog, format and recovery-time matrix remains unproved. S2 will qualify and fix the implementation rather than rebuild working storage.

- **not_fully_proven**: Accepted buffered events survive the declared restart/node-loss boundary with zero unexplained loss and bounded duplicate handling.
- **not_fully_proven**: Replayed data cannot invent fresh device confirmation or reorder writer generations.
- **not_fully_proven**: Storage capacity, backlog alerting, recovery time and rollback of the format are documented and measured.

## #386 — [C4][P1] Prove grow-light minimum-on behavior at the solar-window boundary

Disposition: blocked; owner: Platform lead; stage: C4.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Current lights reportOFF in latest equipment events. Historical exact outside-window source receipt remains partial; no eligible solar transition/minimum-on timing is forced or proved. Interior PAR/DLI remains absent. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Tests fail for both premature cycling and unintended overrun, with exact transition times and stop reasons.
- **not_fully_proven**: Source and actual grow-light state agree across the boundary; safety/manual override remains dominant.
- **not_fully_proven**: No DLI or lighting-efficiency claim is inferred from runtime while interior light measurement is unavailable.

## #390 — [C4][P1] Gate firmware releases on topology, cycling, runtime safety and rollback evidence

Disposition: blocked; owner: Platform lead; stage: C4.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Controller is2026.10.2.0637.620a218a-wifi-bound with fresh diagnostic/runtime data. Exact accepted binary floor and the whole negative release/runtime/recovery matrix are not proved by this scan or prior compile; no last-good promotion/OTA. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Negative cases for wrong topology, impossible cycling, stale outdoor inputs and missing compile/runtime receipts refuse release.
- **not_fully_proven**: Exact source/binary/version is tied to observed relay/cfg outcomes and a demonstrated recovery path.
- **not_fully_proven**: No unreviewed firmware or failed watchdog candidate is promoted to last-good; no OTA is performed by ordinary CI.

## #396 — [C5][P1] Design and rehearse CNPG alongside the live database on current storage

Disposition: selected_s2; owner: Data/platform lead; stage: C5.

Three CNPG clusters are 3/3 Ready on digest-pinned TimescaleDB and current Longhorn storage. Actual verdify_rehearsal ledgers max at migration 270 while production is 274. Historical PITR is retained; complete current restore/parity/PITR/failover/rollback packet is missing. Full parity query was not rerun or weakened.

- **not_fully_proven**: Candidate uses digest-pinned compatible images and declared storage/anti-affinity; no production writer or device route is acquired.
- **not_fully_proven**: Restore, failover and PITR are proven with data checks, not only Ready replicas.
- **not_fully_proven**: #245 receives an exact cutover/rollback packet and measured downtime/data risks; live DB remains unchanged.

## #398 — [C7][P2] Replace stale soil threshold seeds with commissioned crop/substrate truth

Disposition: blocked; owner: Operator with data/control lead; stage: C7.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Fresh database retains original three target seeds80/80/75 and Canna Lily/Canna Lily/Unknown. No commissioned crop/substrate replacement, before/after/reversal receipt exists; never activate seeds as calibration truth. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Current map, seed and DB rows agree without overwriting valid operator-specific values.
- **not_fully_proven**: No saturation/drip-skip consumer uses an uncommissioned or mismatched target as valid.
- **not_fully_proven**: Before/after row receipt and exact rollback data exist; #45 can validate the resulting feedback contract.

## #410 — [C6][P1] Measure realized solar-night dry-out before changing the controller

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Latest24h actions includeHEAT,IDLE,DEHUM_VENT and wet assists. This aggregation does not prove solar-night episodes, response and zero solar-day held-temperature admission; retain realized-episode acceptance rather than fixed-clock/night PASS. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Episodes use device solar semantics, never fixed 02:00–06:00, and incomplete/confounded episodes cannot be PASS.
- **not_fully_proven**: Solar-day held-temperature admission is zero while ordinary daytime dehumidification remains correctly distinguished.
- **not_fully_proven**: Planner/MCP sees realized validity-bearing outcomes and explicit disposition, not a universal VPD target or a crop-safety claim without sensing.

## #412 — [C7][P2] Close the seasonal door screen only when measured night conditions justify it

Disposition: deferred; owner: Operator with data/control lead; stage: C7.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No measured seasonal door-screen trigger or physical closure observation is available. Reminder/calendar prose is not completion; keep operator seasonal decision deferred. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: The trigger and physical completion are recorded from observation, not a reminder date alone.
- **not_fully_proven**: Temperature/moisture/ventilation remain within the declared safety envelope after closure.
- **not_fully_proven**: Any study receives a visible intervention/deviation record rather than an unexplained seasonal covariate change.

## #419 — [C4][P1] Populate real outdoor freshness in replay and enforce branch coverage

Disposition: incomplete; owner: Platform lead; stage: C4.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Retained handoff reports real outdoor replay corpus still lacksHUMIDIFY coverage. Live outdoor/forecast values are fresh but do not prove corpus fresh/stale branches, no future leakage and known-good replay qualification. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Corpus contains measured finite outdoor age with provenance and both fresh/stale branches; a zero-coverage corpus fails loudly.
- **not_fully_proven**: No future outdoor sample leaks into replay; missing values preserve safe stale behavior.
- **not_fully_proven**: Native/invariant/band and targeted outdoor tests pass against the candidate and known-good self-diff.

## #428 — [C6][P1] Diagnose heap/watchdog risk and qualify the actual last-good firmware floor

Disposition: incomplete; recorded predecessors complete; owner: Control lead; stage: C6.

Current uptime is 307929 seconds and heap is about 61.21 KiB (published heap_bytes column contains KiB). Retained minimum 2.336 KiB is historical. Full worst-load, fragmentation and accepted rollback-floor qualification remains incomplete; no reset cause inferred.

- **not_fully_proven**: Cause is reproduced or uncertainty explicitly bounded with current load/reset evidence; software-reset versus watchdog counts are distinct.
- **not_fully_proven**: Candidate meets predeclared heap/fragmentation/timing and clean-runtime criteria under representative worst-case load.
- **not_fully_proven**: No last-good promotion occurs solely on compile/replay success, a reset count omission or a projected heap improvement.

## #430 — [C6][P1] Simplify firmware only while preserving autonomous control and observable truth

Disposition: incomplete; owner: Control lead; stage: C6.

Closed #433 and #949 keep their scoped acceptance. Current bounded ingestor log has zero broad-restore holds; this does not prove broad firmware simplification savings or all tier reductions.

- **not_fully_proven**: Each tier has measured before/after heap/traffic and zero unintended native/replay behavior divergence.
- **not_fully_proven**: Network-isolation autonomy, reset/water-budget conservatism and current full-state evidence survive.
- **not_fully_proven**: #428/#369/#367/#370 receipts define the achieved scope; theoretical entity or heap savings are labeled projections.

## #434 — [C6][P1] Enforce center-only climate mist and commissioned wall-only fertigation

Disposition: blocked; owner: Control lead; stage: C6.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No current all-origin reverse-conflict/plumbing/calibration/duplicate-dose physical release acceptance exists. Missing chemistry/soil calibration blocks feed; no center non-climate or non-wall fertilization is authorized. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: All command origins and reverse conflicts prove zero south/west climate demand, zero center non-climate mist and zero non-wall fertilizer actuation.
- **not_fully_proven**: Missing/stale/nonfinite/zero/out-of-bounds calibration prevents feed; restart/duplicate/missed-window cases cannot double-dose.
- **not_fully_proven**: Native/invariant/replay/band/compile and make irrigation-stack-software-check pass; #390 release ties actual routing/readbacks/sequence to exact binary with rollback.

## #581 — [C2][P0] Experiment program: qualify, run and read out the bounded AI-admission pilot

Disposition: incomplete; owner: Research/release lead; stage: C2.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Live experiment counts:0 draws,0 assignment outcomes,0 freezes,0 exports,0 proof receipts,0 open exposures. Full physical qualification/design/readout program remains unfinished. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: No launch before exact physical proof, tested outcome path, frozen design/no-redraw and separate launch authority.
- **not_fully_proven**: All assigned days, including failed delivery, fallback, rescue and null outcomes, survive into ITT.
- **not_fully_proven**: Final report states whether the result is feasible, promising, adverse or inconclusive and distinguishes climate noninferiority from improvement and runtime from resource savings.

## #586 — [C8][P2] Qualify a future atomic firmware policy engine with crash-safe recovery

Disposition: deferred; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Future atomic firmware policy engine remains device-denied/deferred. No complete per-tick/recovery/worst-load heap proof or separate current OTA/protocol acceptance. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Every consumer reads one atomic per-tick policy and invalid/corrupt/expired state returns to approved safe baseline.
- **not_fully_proven**: Worst-case heap/fragmentation/watchdog margins and independent recovery flashing are measured, not projected.
- **not_fully_proven**: Separate OTA authority and future protocol decision exist before vector mode can enable; current pilot remains unchanged.

## #587 — [C1][P0] Qualify fail-closed lifecycle, kill switch and blinded operational visibility

Disposition: incomplete; owner: Runtime/release lead; stage: C1.

Experiment services are Ready but all live draw/outcome/freeze/export/proof/exposure counts remain zero. Disabled deployment does not prove complete fault, kill/disable, recovery, alert routing and rollback qualification.

- **not_fully_proven**: Feature off means zero experiment calls; locked/armed cannot reopen readiness and emergency hold cannot actuate.
- **not_fully_proven**: Normal disable/ID clear/ordinary ownership restoration requires confirmed baseline; only an explicit facility-owned emergency-safe closure may state otherwise without relabeling it baseline.
- **not_fully_proven**: Alerts actually fire and clear through the owning delivery route, no comparative leakage occurs, and the exact-source rollback test is consumed by #783/#641.

## #588 — [C2][P0] Lock the qualified pilot identity and finalize exactly one internal random draw

Disposition: blocked; owner: Research/release lead; stage: C2.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Live randomization count0. No current scientific lock/draw is complete;#782/#783/#641 remain open. Do not finalize or recreate a draw from preparation. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Current model/context/request limits, full source identity and #782 scientific choice match the lock byte-for-byte.
- **not_fully_proven**: Exactly one draw exists; duplicate/replacement/caller-time/secret attempts fail without leaking mapping or randomization material.
- **not_fully_proven**: Future first assignment boundary and schedule commitment reproduce; no physical randomized work occurs before the separate #642 authority.

## #606 — [C8][P2] Register the twin build profile only through its owning fleet contract

Disposition: deferred; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Twin fleet profile remains upstream-owned and must be delivered there. This repository mirror was not edited; no new profile acceptance is claimed. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Owning registry and generated mirror agree and one verified immutable digest is consumable.
- **not_fully_proven**: Runtime is read-only/device-denied and has no undeclared build or network dependency.
- **not_fully_proven**: This enablement issue is not a blocker for the current #581/#642 component pilot.

## #638 — [C8][P2] Unify future manifest/vector wire schema and content identity offline

Disposition: deferred; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Future wire schema/content identity remains offline/deferred. No all-consumer future protocol acceptance is established and no vector authority is enabled. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Cross-language identity and actual service declaration/host call schemas agree; tests fail against the former incompatible interface.
- **not_fully_proven**: Restore admission creates the intended outbox and compiled commit/stage/activate/expiry/abort paths consume host bytes.
- **not_fully_proven**: Generalized vector mode remains off; closing this issue does not switch any active trial transport or claim physical qualification.

## #639 — [C1][P0] Qualify the existing component executor and full-state recovery before launch

Disposition: incomplete; owner: Runtime/release lead; stage: C1.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Retained C1 original callback/full48 evidence is not full lifecycle/executor failure/recovery/restored randomized qualification. Current proof receipts0; no execution/fault matrix was rerun. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: No prefix opens exposure; two fresh full-48 epochs with current generations are required for baseline and target.
- **not_fully_proven**: Exclusive bundles never interleave with ordinary/MCP/forecast/retry writers; shadow emits zero calls and nonrandom work has no assignment lineage.
- **not_fully_proven**: Recovery closes exposure first, yields to facility rescue, uses linked baseline-only work and verifies two epochs before ordinary-writer handoff. The qualified source receipt is reusable by #641 and #642.

## #640 — [C3][P0] Freeze the first assigned-day ITT outcome and blinded reproducible export

Disposition: blocked; owner: Research lead; stage: C3.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Live assigned outcomes/freezes/exports are0. No first randomized assigned day exists to freeze; synthetic source fixtures are not observed ITT outcomes. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: The first full assigned window is frozen twice to identical canonical rows/hashes and consumed by the analyzer without reshaping or reveal.
- **not_fully_proven**: A ledger reconciliation proves every assignment has exactly one row irrespective of exposure or outcome availability.
- **not_fully_proven**: Freezer cannot mutate assignments/read mapping; analyst sees frozen X/Y only. Missing center/DLI/gas and unselected resource fields remain truthfully unavailable.

## #641 — [C1][P0] Execute authorized orphan recovery and one separately attended physical proof

Disposition: blocked; owner: Runtime/release lead; stage: C1.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Live proof receipt count0. Retained controller recovery/C1 restoration is not a sealed baseline→aggressive→baseline GateP proof. Draft#855 remains unmerged; physical readiness and separate exact authority stay unfulfilled. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Recovery retains predecessor failures, creates no nonbaseline exposure and earns no proof credit.
- **not_fully_proven**: Exactly one successor-attempt receipt binds baseline-before/aggressive/baseline-after to actual current source and observations.
- **not_fully_proven**: Mismatch closes exposure/revokes nonbaseline before facility-compatible recovery; final baseline-confirmed draft/shadow/closed, feature off, empty ID, vector off and zero exposure are evidenced.

## #642 — [C2][P0] Authorize and verify one randomized day-1 activation from the locked pilot

Disposition: blocked; owner: Research/release lead; stage: C2.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No locked draw or live randomized assignment. Exact launch/observed physical target/exposure/freezer/rollback acceptance remains downstream; no launch performed. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Exact launch authority, unique assignment, frozen identity, observed physical target, two epochs and exposure lineage exist.
- **not_fully_proven**: Freezer can progress, singleton writer/public health hold and exact-source Argo is Synced + Healthy.
- **not_fully_proven**: Rollback evidence is current and first-day/export ownership hands to #640; closure does not claim experiment success.

## #643 — [C5][P1] Extend least-privilege database roles beyond the already-required experiment boundary

Disposition: selected_s2; owner: Data/platform lead; stage: C5.

Role implementation is landed. Metadata confirms nine ordinary runtime group/login pairs without superuser, bypassrls, createdb or createrole. Prior API/MCP/ingestor TCP receipts are bounded; all actual current runtime login allow/deny cases and rollback remain unproved. Do not infer from Ready pods.

- **not_fully_proven**: Live-role matrix allows intended functions and rejects unrelated DML, mapping reads and cross-workload capabilities.
- **not_fully_proven**: No credentials or Secret annotation values enter issue/log/backup artifacts; ordinary pods lack owner/migration authority.
- **not_fully_proven**: Public/planner/ingestor health and experiment integrity survive rollout/rollback; no unsolicited credential rotation occurs.

## #644 — [C4][P1] Integrate exact-SHA, path-aware and device-safe delivery receipts

Disposition: selected_s2; owner: Platform lead; stage: C4.

Source-only promotion receipts exist. Whole path-impact, exact-SHA staging/build/promotion, authority separation, retry and durable runtime identity matrix remain open. Coordinate the bounded Verdify integration in agents#4014; do not make this sprint require closing the entire fleet umbrella.

- **not_fully_proven**: Exact-SHA results are durable, redacted and actionable; no recursive builds from pin commits.
- **not_fully_proven**: Builder cannot deploy/promote and ordinary validation cannot access device/OTA/runtime secrets; immutable digest consumer receipt identifies rollback pins.
- **not_fully_proven**: Verdify integration and owning fleet dependency are linked; current branch policy is measured rather than copied from an August snapshot.

## #749 — [C1][P0] Qualify current three-probe readiness and explicit safety dependencies

Disposition: blocked; owner: Runtime/release lead; stage: C1.

Native predecessors #424/#778/#949 are closed, but fresh south temperature/RH/VPD is absent while diagnostics still report four healthy probes. Physical/contributor/consumed-policy qualification remains incomplete. No Gate P receipt is claimed from a dependency graph.

- **not_fully_proven**: 3-of-4 degraded-pass is truthful; two or fewer probes, nonfinite aggregates, excess spread, stale evidence or semantic contradiction fail closed.
- **not_fully_proven**: Hydro is traced as absent from proof/selector/executor/outcome causal inputs; any new dependency is classified before use.
- **not_fully_proven**: Packet is read-only, current at every boundary and cannot grant proof or randomized authority; probe loss closes exposure before recovery.

## #750 — [C1][P0] Recovery/proof campaign: rebaseline current state and seal one safe physical receipt

Disposition: incomplete; owner: Runtime/release lead; stage: C1.

Production is Synced/Healthy, 26 workload image specifications match, all 16 long-running workloads Ready, smoke 9/9 and pinned fleet census exactly one writer. These restore service evidence, not complete integrated physical qualification; #749/#641 remain open.

- **not_fully_proven**: Fresh source/Argo/runtime/backup receipts replace old point-in-time assertions and identify any remaining mismatch.
- **not_fully_proven**: #747, #749 and #641 have their own acceptance evidence; proof and orphan recovery never share credit.
- **not_fully_proven**: Final safe state and rollback receipt are verified; no randomized design/draw/launch is performed under this roll-up.

## #751 — [C7][P2] Replace the failed south climate probe and diagnose hydro telemetry in a bounded window

Disposition: incomplete; owner: Operator with data/control lead; stage: C7.

Seven-day south temperature/RH/VPD coverage is zero; hydro chemistry is not fresh, and south soil moisture/EC stay zero. Firmware diagnostics still report four/OK. Replacement, reference calibration and truthful contributor repair remain incomplete.

- **not_fully_proven**: South supplies credible fresh finite temperature/RH/VPD and is validated against reference/neighbors; hydro cause and restored or explicit unresolved state are documented.
- **not_fully_proven**: Contributor diagnostics become truthful and alerts resolve from telemetry, not manual deletion.
- **not_fully_proven**: No replacement becomes a surprise pilot gate or silently changes the locked analysis panel.

## #775 — [C0][P0] Campaign: trustworthy greenhouse outcomes → qualified pilot → evidence-backed control

Disposition: incomplete; owner: Evidence/data lead; stage: C0.

All 63 currently open issues and their literal acceptance clauses are reviewed; 85 campaign dependency/parent sets match the 88-node native acyclic graphs. S1 is closed 8/8 and S2 is carved out separately. Physical readiness, scientific lock, genuine pilot and reproducible final readout remain incomplete.

- **proven**: Every issue open at the snapshot has exactly one owner role, stage, what/why/how, testable acceptance and disposition.
- **proven**: The full native dependency graph is acyclic and equals the repository graph; no child is blocked by its own umbrella.
- **not_fully_proven**: The pilot ends in a reproducible decision, including an inconclusive/no-benefit result if that is what the evidence supports; broader efficiency claims remain gated.

## #782 — [C0][P0] Choose a season-appropriate exploratory pilot and freeze the scientific contract

Disposition: blocked; owner: Evidence/data lead; stage: C0.

Closed #371/#779/#780/#781 clear recorded predecessors. Warm June 1–July 30, 2027, 30-pair/60-day pre-draw contract is preparation, not design lock. Physical target/contributor qualification, empirical covariance and joint power remain absent. Historical v1 candidate is superseded by v2, not a current date conflict.

- **not_fully_proven**: An explicit decision document chooses pilot versus confirmatory scope and states exactly which claim can be tested with current meters.
- **not_fully_proven**: Power replay is reproducible; include .99^30 ≈ .740 and .95^30 ≈ .215 all-pairs retention sensitivities rather than assuming .9995 reliability.
- **not_fully_proven**: Calendar is future and seasonally justified; 06:00–24:00 remains 18 elapsed hours on the fall clock-change day, with any full-day scheduler implications tested separately.
- **not_fully_proven**: No draw has occurred before design approval/qualification; model is current source-pinned Luna/medium, not stale Sol issue prose.

## #783 — [C1][P0] Prove restore → selector → setter schema → receipt → frozen outcome end to end

Disposition: blocked; owner: Runtime/release lead; stage: C1.

Synthetic fixture reproducibility is separate from genuine current-login whole-path freezer/export acceptance. #782/#639/#587 remain open. Do not mutate the clocks of genuine SOURCE/A/B rehearsals or substitute fixture bytes for real qualification.

- **not_fully_proven**: One command/artifact index reproduces the whole path twice with byte-identical canonical hashes and no data reshaping between freezer and analyzer.
- **not_fully_proven**: First-day, missing-day, zero-exposure, reset and failed-delivery rows exist by assignment, never filtered by exposure.
- **not_fully_proven**: Device-denied restore contains no production credentials in logs/artifacts; minimum randomizer/lifecycle/executor/freezer/blinded-analyst boundaries pass. Current source and failure matrix are recorded before #588.

## #784 — [C3][P1] Operate the complete blinded pilot and preserve every assigned day

Disposition: blocked; owner: Research lead; stage: C3.

Retained October 5 assessment (historical, not a fresh acceptance receipt): 0 live assignment outcomes/freezes/exports. No genuine blinded60-day pilot can be operated/accounted for yet; every eventual failed/null/fallback day must remain assigned. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: All locked assignments are accounted for, including incomplete/no-exposure/aborted days; no replacement pair, redraw or post-hoc endpoint change occurs.
- **not_fully_proven**: Blinded integrity and safety reviews have receipts at the cadence declared before launch; departures are preserved.
- **not_fully_proven**: Study ends safe with stopped admission, closed exposure, frozen outcome/deviation/fidelity/environment hashes ready for #785.

## #785 — [C3][P1] Reveal once, reproduce the frozen analysis and publish the campaign decision

Disposition: blocked; owner: Research lead; stage: C3.

Retained October 5 assessment (historical, not a fresh acceptance receipt): No genuine pilot export/reveal/readout exists. A reproducible final adverse/no-benefit/inconclusive decision remains downstream; source fixtures are not efficacy. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Independent rerun from frozen artifacts produces identical tables and decision; reveal occurs once with audited authority.
- **not_fully_proven**: Report permits adverse/no-benefit/inconclusive conclusions and gives no yield/profit/whole-resource/other-season claim beyond measurements.
- **not_fully_proven**: #775/#581 close only on this completed decision boundary; #786/#787 and deferred work have explicit go/no-go rationale, not automatic promotion.

## #786 — [C8][P2] Design the next comparison against a deterministic forecast-aware selector

Disposition: deferred; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Merged deterministic forecast comparator preparation is unregistered. Final go/no-go depends on#785; real training/covariance/power and future separate lock/authority remain unavailable. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: A go/no-go decision is linked to #785, with a prespecified comparator and no reused revealed schedule.
- **not_fully_proven**: Offline command replay is not described as a physical climate or resource effect.
- **not_fully_proven**: Any new trial has its own lock/draw/authority and a claim that matches its measured endpoint.

## #787 — [C8][P2] Plan measured heating, overnight and whole-resource economics as a separate claim

Disposition: deferred; owner: Research/platform lead; stage: C8.

Retained October 5 assessment (historical, not a fresh acceptance receipt): Whole-resource/overnight specification is preparation. Current partial meters, ambiguous shared water, absent gas/interiorDLI/calibration/cost do not support whole-resource or profit claims. October 6 review added no physical commissioning, control release or fault rehearsal that would satisfy these remaining clauses; current telemetry availability is in telemetry-columns.json.

- **not_fully_proven**: Each claimed resource has eligible measured coverage and calibration/uncertainty; modeled proxies remain secondary.
- **not_fully_proven**: A new prospective study can test climate improvement and resource benefit as stated, or the claim is explicitly narrowed.
- **not_fully_proven**: No hardware purchase, gas tuning, yield/profit claim or seasonal controller expansion follows automatically from the current pilot.

## #801 — [VP-02] Exclude www-dev and proposals-hello from the verdify-tier1-forward HostRegexp

Disposition: blocked; owner: Platform lead; stage: estate.

Ownership transition still waits on agents#4461, open. Route exclusions and live safe adoption remain unqualified; no route or Argo sync is performed in this review.

- **not_fully_proven**: Source and live forwarder exclude both named external routes.
- **not_fully_proven**: Both hosts reach their owners with public/TLS behavior verified and ordinary Verdify hosts preserved.

## #802 — [VP-01] Put verdify-descheduler under Argo (manual) with Git equal to live enforce mode

Disposition: blocked; owner: Platform lead; stage: estate.

Live descheduler enforce mode differs from Git dry-run. agents#4459 is closed and agents#4451 remains open. Preserve native historical edges; no apply, eviction change or adoption performed.

- **not_fully_proven**: Seven objects adopted under manual parented Argo with unchanged enforce specs/UID/generation.
- **not_fully_proven**: Namespace ownership, residual writer grant and tracking scope remain exact.
- **not_fully_proven**: Natural enforce Job and live sync-classification audit pass without dry-run.

## #953 — [C4][P2] Reconcile Iris planning skill lookup and required reference inventory

Disposition: selected_s2; owner: Platform lead; stage: C4.

The bounded four-hour Hermes log contains six missing greenhouse-planning-mcp lookups. Actual plans still complete; current nonterminal overdue count is zero. This is degraded lookup behavior, not a planner outage. Require full skill/reference inventory and next natural-cycle proof without extra model calls.

- **not_fully_proven**: Every required prompt reference resolves in the running profile at exact source/config identity.
- **not_fully_proven**: Next natural required cycle writes a valid plan without missing-skill/reference retries; no extra provider calls solely for triage.
- **not_fully_proven**: No static worker-ready signal is presented as plan success; existing ledger/source proofs remain intact.


Criteria provenance: 181 literal issue-body acceptance checkboxes and 7 retained reviewer-derived bug/Verify conditions for #317/#801/#802; those issues have no Acceptance checkbox section.
