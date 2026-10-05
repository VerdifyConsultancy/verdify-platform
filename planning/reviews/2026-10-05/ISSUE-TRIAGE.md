# Acceptance reconciliation — October 5, 2026

All67 initially open issues and5 newly identified issues were reviewed. Each acceptance clause below retains its actual boundary. “Not fully proven” does not erase existing source/offline/historical evidence; it means the whole criterion cannot justify closure today. One stale tracker,#835, closes on bounded current proof. Campaign#775 remains open.

## #14 — [C8][P2] Twin program: qualify declared oracle coverage before using divergence as a gate

State: **OPEN**; disposition: deferred; stage: C8; accountable role: Research/platform lead.

Future device-denied twin remains deferred. Current source/build/output/coverage and negative oracle receipts are not a full current physical-parity proof.

| Acceptance criterion | Result |
|---|---|
| Coverage and limitations are explicit; unsupported/default-only fields cannot produce a full-parity PASS. | not fully proven |
| Source/build/runtime and actual read-only output receipts exist, with no startup compiler/pip downloads. | not fully proven |
| The current pilot remains independent; a future twin gate is adopted only after useful positive and negative evidence. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/14#issuecomment-4593887671), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/14#issuecomment-5956406146).

## #16 — [C7][P2] Hardware program: commission only the sensing and equipment justified by evidence

State: **OPEN**; disposition: deferred; stage: C7; accountable role: Operator with data/control lead.

Physical inventory/commissioning remains deferred. Missing south, hydro, interior-light and chemistry channels remain unavailable; no installation/physical acceptance was performed.

| Acceptance criterion | Result |
|---|---|
| Each physical item has a named measurement/control need, commissioning worksheet and explicit enabled/disabled state. | not fully proven |
| No center-fertilizer path contradicts #434; absent sensors are not synthesized as real observations. | not fully proven |
| Operator windows and seasonal triggers are tracked without guessed dates or blanket launch blockers. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/16#issuecomment-4593888345), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/16#issuecomment-4594561547), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/16#issuecomment-4758550089), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/16#issuecomment-5468196593), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/16#issuecomment-5956406111).

## #31 — [C8][P2] Close twin setpoint and actuator coverage gaps with generated consumer truth

State: **OPEN**; disposition: deferred; stage: C8; accountable role: Research/platform lead.

Generated consumer/oracle source does not establish live input coverage, meaningful nondefault divergence or current read-only twin output. Do not reuse the old absent-table claim as current evidence.

| Acceptance criterion | Result |
|---|---|
| Every active consumer is covered or explicitly excluded from the oracle claim with a reason. | not fully proven |
| Missing required input and a known nondefault divergence fail loudly; identical-source self-diff passes. | not fully proven |
| No twin result can qualify full current 48-field physical exposure or new transport by default. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/31#issuecomment-4593891670), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/31#issuecomment-4597175391), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/31#issuecomment-4597190100), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/31#issuecomment-4644173584), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/31#issuecomment-5956405836).

## #45 — [C7][P2] Commission soil and runoff feedback before enabling irrigation decisions

State: **OPEN**; disposition: blocked; stage: C7; accountable role: Operator with data/control lead.

Seven-day audit: no input/runoff pH/EC; south soil channels are entirely zero. Calibration and topology remain uncommissioned. Nonzero west moisture does not establish validity.

| Acceptance criterion | Result |
|---|---|
| Required channels produce credible calibrated data, not merely nonzero rows; current gap counts are measured rather than assumed four. | not fully proven |
| Feedback check passes and requirements/alerts close from actual accepted measurements. | not fully proven |
| Unknown chemistry, sensor or plumbing state remains visibly unavailable and cannot enable fertilizer or soil feedback. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/45#issuecomment-4593897910), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/45#issuecomment-4594567103), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/45#issuecomment-4644142182), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/45#issuecomment-5465356726), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/45#issuecomment-5956405774).

## #49 — [C5][P1] Audit and close historical suppressed sensor alerts without false count-parity assumptions

State: **OPEN**; disposition: incomplete; stage: C5; accountable role: Data/platform lead.

Fresh exact query finds61 suppressed sensor_offline rows with resolved_at NULL and zero migration_151_backfill rows. Historical merged-source claim is not live cleanup; original ID/reversal/idempotency receipt still required.

| Acceptance criterion | Result |
|---|---|
| Only the reviewed historical suppressed sensor_offline IDs are updated; a second run changes zero rows. | not fully proven |
| Before/after receipt lists non-secret IDs/counts and verifies no active alert was hidden. | not fully proven |
| Forward lifecycle behavior remains correct and reversal data is retained for the exact touched fields. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/49#issuecomment-4593874233), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/49#issuecomment-4596213253), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/49#issuecomment-4596253666), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/49#issuecomment-4644161958), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/49#issuecomment-5956405761).

## #51 — [C7][P2] Calibrate CO2 against a real reference and publish measurement uncertainty

State: **OPEN**; disposition: incomplete; stage: C7; accountable role: Operator with data/control lead.

Current CO2 is3032.53ppm, but no contemporaneous reference field pairs/tolerance/calibration acceptance exists in this audit. Preserve uncalibrated status; no anchor guess.

| Acceptance criterion | Result |
|---|---|
| New coefficients derive from recorded field pairs and pass the predeclared independent tolerance. | not fully proven |
| Plausibility guards and units remain correct, with uncertainty visible to consumers. | not fully proven |
| If the reference/window is unavailable, retain uncalibrated status without guessing anchors or buying equipment implicitly. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/51#issuecomment-4587567019), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/51#issuecomment-4593900445), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/51#issuecomment-4594571817), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/51#issuecomment-5956405728).

## #52 — [C7][P2] Define species-appropriate seasonal dormancy care from operator observations

State: **OPEN**; disposition: deferred; stage: C7; accountable role: Operator with data/control lead.

Species/crop observations and a supported seasonal care decision are unavailable to this remote review. No change is made; keep seasonal decision deferred.

| Acceptance criterion | Result |
|---|---|
| A documented no-change or specific seasonal-care decision is supported by actual crop observations. | not fully proven |
| Any new dormancy/light/dry-down setting is versioned, reversible and cannot silently alter a locked study. | not fully proven |
| No species-wide agronomic or yield claim is made from uncalibrated DLI or a house-average proxy. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/52#issuecomment-4593901134), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/52#issuecomment-5956405424).

## #75 — [C4][P1] Observability program: prove actionable health from source through device

State: **OPEN**; disposition: incomplete; stage: C4; accountable role: Platform lead.

One-writer scan and alert/exporter metrics are current. Grafana authenticated probes fail, operator Slack delivery fails and vision is unavailable. Children949–953 expose bounded follow-through, not monitoring-rollup completion.

| Acceptance criterion | Result |
|---|---|
| Each child has a failing and recovering real or isolated fault receipt and a clear owner/runbook. | not fully proven |
| No inactive PrometheusRule or static-green worker is accepted as live monitoring. | not fully proven |
| Historical closed children remain history; current coverage and omissions are documented. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/75#issuecomment-4593888816), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/75#issuecomment-5852409374), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/75#issuecomment-5956405398).

## #218 — [C5][P1] Durability program: recoverable backups, measured RPO/RTO and safe HA option

State: **OPEN**; disposition: incomplete; stage: C5; accountable role: Data/platform lead.

Scheduled October5 paired backup succeeded; retained restore/PITR history and current CNPG readiness are separate. Full parity timeout, durability/node-loss boundaries and cutover packet remain incomplete.

| Acceptance criterion | Result |
|---|---|
| Backup, restore, ingest-loss and HA children provide measured evidence rather than deployment counts. | not fully proven |
| RPO/RTO and residual failure domains are explicitly reported, with PITR claims only after a point-in-time restore. | not fully proven |
| No broad live DB mutation is authorized by closing the planning or design tasks. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/218#issuecomment-4644836617), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/218#issuecomment-4645152883), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/218#issuecomment-4930562138), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/218#issuecomment-5472054604), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/218#issuecomment-5956405432).

## #245 — [C5][P1] Execute a separately bounded database cutover only after parity and rollback proof

State: **OPEN**; disposition: blocked; stage: C5; accountable role: Data/platform lead.

Production DB remains verdify-db-0. Rehearsal clusters exist but whole-policy parity is still unproven; no cutover, writer-fencing rehearsal,60-minute live verification or seven-day post-cutover bake was performed.

| Acceptance criterion | Result |
|---|---|
| A rehearsal proves all writers are quiescent and measures downtime, parity, RPO/RTO and both rollback boundaries. | not fully proven |
| Production source/running DB authority and health are verified through at least the declared 60-minute initial observation and seven-day durability bake. | not fully proven |
| No old database/PVC removal before separate verified retention/decommission criteria; no unqualified sub-minute zero-loss promise. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/245#issuecomment-4644808761), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/245#issuecomment-4645152824), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/245#issuecomment-4758927896), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/245#issuecomment-5465356728), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/245#issuecomment-5956405386).

## #296 — [C6][P1] Consider slow-hysteresis firmware irrigation feedback only after dispatcher evidence

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

Feedback inputs are uncommissioned and south channels zero. Existing source/preparation does not justify firmware feedback enablement; a measured no-build or independently qualified implementation decision remains open.

| Acceptance criterion | Result |
|---|---|
| Either a documented no-build decision or a qualified implementation is delivered; no automatic expansion occurs. | not fully proven |
| With commissioned probes a saturated zone suppresses eligible drip conservatively, without duplicate dispatcher/firmware control. | not fully proven |
| Missing/failed sensors, reboot, manual rescue and maximum limits remain safe; physical enablement waits for commissioning. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/296#issuecomment-5956405021).

## #297 — [C6][P1] Add saturation alerts and a commissioned dispatcher-side drip-skip loop

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

Fresh soil target rows remain Canna Lily/Canna Lily/Unknown with saturation80/80/75. Alert-only preparation is separate from commissioned persistence/hysteresis and actual eligible skipped drip. No threshold/actuation change.

| Acceptance criterion | Result |
|---|---|
| Sustained saturation triggers and recovers with explicit sensor validity; no stale seed is treated as crop truth. | not fully proven |
| One eligible saturated zone’s scheduled drip is truthfully skipped/shrunk and recorded, with safe fallback for invalid evidence. | not fully proven |
| No OTA, EC closed-loop dosing or guessed day-mask mitigation is required; alert-only implementation can proceed before physical enablement. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/297#issuecomment-5949126837), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/297#issuecomment-5956405026).

## #298 — [C7][P2] Rebaseline actual crop, pot and probe topology before feedback control

State: **OPEN**; disposition: incomplete; stage: C7; accountable role: Operator with data/control lead.

Live soil seeds still retain Canna Lily/Unknown names and remote telemetry cannot establish physical crop/substrate/probe positions. Actual operator inventory remains the predecessor for threshold commissioning.

| Acceptance criterion | Result |
|---|---|
| Repo and DB map agree with observed physical positions and crop/substrate identities; no Canna-Lily residue is carried as current fact. | not fully proven |
| Each sensor has source ID, location, validity/calibration state and explicit unassigned/disabled status when appropriate. | not fully proven |
| No automatic irrigation is enabled by a topology import alone; #398/#45 own threshold and feedback commissioning. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/298#issuecomment-5465356722), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/298#issuecomment-5956405004).

## #299 — [C6][P1] Preserve center-mister re-fire protection through topology and reset recovery

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

Existing center re-fire protection is preserved; current routing/reset/re-fire preservation and release receipt are not proved by this telemetry scan. Historical live protection is not a current topology release qualification.

| Acceptance criterion | Result |
|---|---|
| No origin or restart can re-fire before the declared protection permits; hard safety remains dominant. | not fully proven |
| Eligible re-fire resumes correctly rather than staying latched without reason. | not fully proven |
| Source/firmware/cycle evidence shows protection survived #434-related changes with a rollback canary. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/299#issuecomment-4929900357), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/299#issuecomment-4929936778), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/299#issuecomment-5956405030).

## #303 — [C4][P1] Run real portable ESPHome compilation for declared firmware targets

State: **OPEN**; disposition: blocked; stage: C4; accountable role: Platform lead.

Local/previous real compile work is separate from a reproducible portable fleet compile for every declared target plus broken-service negative case. Current generated CI contract remains upstream-owned; retain incomplete.

| Acceptance criterion | Result |
|---|---|
| Each declared target actually compiles from a clean checkout; a deliberately broken generated service declaration fails the lane. | not fully proven |
| No network device, secret-bearing configuration or OTA permission is required for compilation. | not fully proven |
| Artifacts bind exact source and toolchain and feed #390 without being labeled physically qualified. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/303#issuecomment-5158491948), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/303#issuecomment-5465356788), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/303#issuecomment-5956404679).

## #304 — [C4][P1] Provide reproducible least-authority firmware and database diagnostic tooling

State: **OPEN**; disposition: incomplete; stage: C4; accountable role: Platform lead.

No new clean diagnostic/compile environment or least-authority fleet delivery receipt was produced. Existing tools/source are preparation, not complete portable-toolchain acceptance.

| Acceptance criterion | Result |
|---|---|
| A clean environment reports pinned tool versions and runs the declared compile fixture reproducibly. | not fully proven |
| No tool install grants Secret, exec, device or cross-namespace access; absence of credentials fails safely. | not fully proven |
| Owning fleet delivery and generated repo references agree; no redundant image is introduced solely for convenience. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/304#issuecomment-5956404599).

## #317 — ArgoCD: unscoped sync operations on verdify-prod-dark get rewritten to a stale 2-resource selective scope

State: **OPEN**; disposition: incomplete; stage: C4; accountable role: Platform lead.

Every-full-sync atomic terminal-state clearing workaround is in current runbook/source. Last operation was full but this triage performed no sync. Underlying stale-selector/cached-result failure is not proved removed; preserve upstream defect and exact hook/run identity requirement.

| Acceptance criterion | Result |
|---|---|
| Plain full sync does not inherit previous resource selectors or cached results. | not fully proven |
| Exact hooks and running digests independently prove the intended full operation. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/317#issuecomment-5361479021), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/317#issuecomment-5465356768), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/317#issuecomment-5472049590), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/317#issuecomment-5851740972), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/317#issuecomment-5956559895).

## #324 — [C6][P1] Extend zonal band lineage compatibly for future deterministic control

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

Compatible zonal/source fields are preparation. Complete per-zone/legacy round-trip, drift guards and separate future consumer decision remain unverified; absent center sensing stays unavailable.

| Acceptance criterion | Result |
|---|---|
| Per-zone and legacy fixtures round-trip through schema, ingest and read APIs without ambiguous field meaning. | not fully proven |
| No new property lacks a model/readback/drift guard, and unavailable center measurements remain unavailable. | not fully proven |
| Only a separately reviewed future control design consumes new zonal authority. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/324#issuecomment-5956404617).

## #350 — [C6][P1] Irrigation program: topology truth, overwatering protection and commissioned wall feed

State: **OPEN**; disposition: incomplete; stage: C6; accountable role: Control lead.

Irrigation physical/chemistry feedback remains uncommissioned. Center-only climate/wall-only fertilizer/source stop safety are distinct from physical delivery and accepted crop/substrate protection.

| Acceptance criterion | Result |
|---|---|
| Software allows only the approved plumbing/origin combinations and fails closed without required calibration. | not fully proven |
| Overwatering-safe feedback is commissioned on actual crop/substrate sensors before actuation. | not fully proven |
| Physical feed, crop safety/yield and irrigation water claims require their own observed evidence. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/350#issuecomment-4929652937), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/350#issuecomment-5956404659).

## #359 — [C6][P1] Control program: truthful crop corridors before evidence-led tuning

State: **OPEN**; disposition: incomplete; stage: C6; accountable role: Control lead.

Current API separates legacy binary diagnostics from credit but physically qualified target evidence is unavailable. No tuning/AI safety delegation is justified by this audit.

| Acceptance criterion | Result |
|---|---|
| Measurement and localized safety are trustworthy independently of AI credit. | not fully proven |
| Every tuning proposal cites fixed-panel realized climate/resource evidence and explicit guardrails/replay/rollback. | not fully proven |
| No PID arm, unvalidated response model, unmeasured efficiency claim or firmware safety delegation to AI enters the current pilot. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/359#issuecomment-4744206130), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/359#issuecomment-4746419315), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/359#issuecomment-4746514368), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/359#issuecomment-4776079391), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/359#issuecomment-5956404228).

## #361 — [C6][P1] Reassess diurnal anchors only after solar parity and measured corridor evidence

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

No anchor retune performed. Band/solar lineage and measured corridor/readout predecessors remain open; future bounded safety/readback/rollback acceptance remains deferred.

| Acceptance criterion | Result |
|---|---|
| No anchor retune precedes source/solar/band truth or changes a locked study. | not fully proven |
| Candidate changes have explicit temperature/VPD safety limits and observed evidence, not target-hugging intent. | not fully proven |
| Any accepted implementation is versioned with current source/readback and rollback values. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/361#issuecomment-5956404197).

## #367 — [C6][P1] Consolidate anti-chatter into one explicit dwell contract

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

No unified dwell release, observed parity/chatter/starvation receipt or current OTA acceptance exists. Prior comments deferred this broad consolidation; preserve existing safety floors.

| Acceptance criterion | Result |
|---|---|
| Exactly one documented dwell authority governs ordinary transitions and all safety exceptions remain explicit. | not fully proven |
| Boundary/reboot/override tests and #299 center-mister re-fire invariants pass. | not fully proven |
| Measured starts/runtime and behavior parity show no new chatter or unsafe starvation; #390 release evidence is present. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/367#issuecomment-4929900339), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/367#issuecomment-4929936772), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/367#issuecomment-5956404227).

## #368 — [C6][P1] Define shared sensor jump, flatline and contributor-integrity semantics

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

Fresh raw north/east/west have values while south is missing; diagnostics report4/OK. This contradiction remains real. Shared validity/jump/flatline semantics and firmware-qualified consumer behavior are incomplete.

| Acceptance criterion | Result |
|---|---|
| Synthetic impossible jump/stuck cases are detected without suppressing legitimate steady values. | not fully proven |
| Control consumes explicitly valid inputs; degraded quorum and true SENSOR_FAULT behavior are documented and tested, not conflated. | not fully proven |
| No silent sensor substitution changes trial endpoints; any firmware portion passes #390 before OTA. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/368#issuecomment-5468196591), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/368#issuecomment-5956404211).

## #369 — [C6][P1] Remove dead tunables and code from a verified consumer inventory

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

No verified complete live-consumer removal inventory or measured code/entity reduction is established. Defer removals until exact consumer and release evidence.

| Acceptance criterion | Result |
|---|---|
| Each removed property has zero required live consumer or an explicit tested migration; no dangling generated IDs or docs remain. | not fully proven |
| Net-negative unused code/entity counts are measured and behavior/safety replay is unchanged. | not fully proven |
| No active trial identity changes silently, no safety control is made AI-writable, and #390 release checks pass for firmware changes. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/369#issuecomment-5472054537), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/369#issuecomment-5956403837).

## #370 — [C6][P1] Consolidate equipment conflict resolution into one pure arbitration function

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

Pure conflict/origin arbitration preparation is not exhaustive real relay/reset/partial-delivery and current firmware-release proof. No control rewrite was made.

| Acceptance criterion | Result |
|---|---|
| Exhaustive conflict/origin fixtures produce one deterministic safe result and an auditable block reason. | not fully proven |
| Reverse conflicts, manual rescue, restart and partial delivery cannot energize incompatible relays. | not fully proven |
| Native/invariant/replay/compile and #390 physical release checks pass without unapproved control-policy changes. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/370#issuecomment-4734088657), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/370#issuecomment-5956403869).

## #371 — [C0][P0] Repair climate score semantics and publish physical outcomes separately from credit

State: **OPEN**; disposition: incomplete; stage: C0; accountable role: Evidence/data lead.

API current evidence separates legacy joint0.8%, temperature11.0%, VPD88.8% and attributable79.5%. Qualified physical target publication is unavailable; observed-minute capture has0 eligible minutes because735 rows lack valid bounds. Separation is partial delivery, not crop-outcome acceptance.

| Acceptance criterion | Result |
|---|---|
| A fixture with high attributable credit and low physical compliance stays distinct through SQL→API→public/planner rendering; no COALESCE renames one metric into the other. | not fully proven |
| Hand-calculated band/distance and missing-sensor cases pass; true crop targets versus historical dispatched bands are identified, not assumed equal. | not fully proven |
| Current-day and historical sample probes agree across consumers with versioned definitions; historical score changes are published as revisions, not silent rewrites. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/371#issuecomment-5873765894), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/371#issuecomment-5877756289), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/371#issuecomment-5894893604), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/371#issuecomment-5916131202), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/371#issuecomment-5956403848).

## #378 — [C6][P1] Decide corridor widths from fixed-target crop and resource evidence

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

No completed pilot/readout exists and physical target evidence is unqualified. Keep corridor-width decisions dependent on fixed-target realized evidence, with no retrospective endpoint retune.

| Acceptance criterion | Result |
|---|---|
| The decision distinguishes temperature versus VPD and cannot use attributable credit as physical compliance. | not fully proven |
| No retrospective redefinition rewrites the completed study endpoint. | not fully proven |
| Any live band change has bounded scope, exact rollback values and required operator/release authority. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/378#issuecomment-5956403843).

## #379 — [C8][P2] Gate grey-box forecasting and MPC on validated response models and simpler baselines

State: **OPEN**; disposition: blocked; stage: C8; accountable role: Research/platform lead.

Forecast/MPC model coverage and held-out response/calibration gates are not established. No new PID/MPC arm or unmeasured efficacy claim is admitted.

| Acceptance criterion | Result |
|---|---|
| Held-out calibration/response gates pass before any counterfactual efficacy or MPC proposal. | not fully proven |
| No PID treatment arm or new outer-loop control enters the current experiment. | not fully proven |
| A separately reviewed bounded implementation/release plan exists for any justified controller change. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/379#issuecomment-5956403494).

## #382 — [C5][P1] Persist ingestor queue and spool across restart and node loss

State: **OPEN**; disposition: incomplete; stage: C5; accountable role: Data/platform lead.

Current ingestor state is mounted on retained verdify-ingestor-state PVC. Source durability and migration265 receipts are retained; this audit did not prove the full accepted-event restart/node-loss recovery, capacity/backlog/recovery-time matrix.

| Acceptance criterion | Result |
|---|---|
| Accepted buffered events survive the declared restart/node-loss boundary with zero unexplained loss and bounded duplicate handling. | not fully proven |
| Replayed data cannot invent fresh device confirmation or reorder writer generations. | not fully proven |
| Storage capacity, backlog alerting, recovery time and rollback of the format are documented and measured. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/382#issuecomment-5919237866), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/382#issuecomment-5919928398), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/382#issuecomment-5920175538), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/382#issuecomment-5920230571), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/382#issuecomment-5956403485).

## #386 — [C4][P1] Prove grow-light minimum-on behavior at the solar-window boundary

State: **OPEN**; disposition: blocked; stage: C4; accountable role: Platform lead.

Current lights reportOFF in latest equipment events. Historical exact outside-window source receipt remains partial; no eligible solar transition/minimum-on timing is forced or proved. Interior PAR/DLI remains absent.

| Acceptance criterion | Result |
|---|---|
| Tests fail for both premature cycling and unintended overrun, with exact transition times and stop reasons. | not fully proven |
| Source and actual grow-light state agree across the boundary; safety/manual override remains dominant. | not fully proven |
| No DLI or lighting-efficiency claim is inferred from runtime while interior light measurement is unavailable. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/386#issuecomment-4929900340), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/386#issuecomment-4929936765), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/386#issuecomment-5942760478), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/386#issuecomment-5947398923), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/386#issuecomment-5956403481).

## #390 — [C4][P1] Gate firmware releases on topology, cycling, runtime safety and rollback evidence

State: **OPEN**; disposition: blocked; stage: C4; accountable role: Platform lead.

Controller is2026.10.2.0637.620a218a-wifi-bound with fresh diagnostic/runtime data. Exact accepted binary floor and the whole negative release/runtime/recovery matrix are not proved by this scan or prior compile; no last-good promotion/OTA.

| Acceptance criterion | Result |
|---|---|
| Negative cases for wrong topology, impossible cycling, stale outdoor inputs and missing compile/runtime receipts refuse release. | not fully proven |
| Exact source/binary/version is tied to observed relay/cfg outcomes and a demonstrated recovery path. | not fully proven |
| No unreviewed firmware or failed watchdog candidate is promoted to last-good; no OTA is performed by ordinary CI. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/390#issuecomment-4929900332), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/390#issuecomment-5865458910), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/390#issuecomment-5867215166), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/390#issuecomment-5895000429), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/390#issuecomment-5956403473).

## #396 — [C5][P1] Design and rehearse CNPG alongside the live database on current storage

State: **OPEN**; disposition: incomplete; recorded predecessors complete; stage: C5; accountable role: Data/platform lead.

CNPG rehearsal and PITR-A/B clusters are currently Ready. Dedicated October2 handoff preserves genuine PITR proof but full SOURCE/A/B policy count+seven timestamp aggregates timed out at120.554741s; cumulative WIP is unmerged. Dataset/app parity and cutover packet remain incomplete.

| Acceptance criterion | Result |
|---|---|
| Candidate uses digest-pinned compatible images and declared storage/anti-affinity; no production writer or device route is acquired. | not fully proven |
| Restore, failover and PITR are proven with data checks, not only Ready replicas. | not fully proven |
| #245 receives an exact cutover/rollback packet and measured downtime/data risks; live DB remains unchanged. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/396#issuecomment-5361479187), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/396#issuecomment-5465356913), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/396#issuecomment-5472054394), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/396#issuecomment-5956406023).

## #398 — [C7][P2] Replace stale soil threshold seeds with commissioned crop/substrate truth

State: **OPEN**; disposition: blocked; stage: C7; accountable role: Operator with data/control lead.

Fresh database retains original three target seeds80/80/75 and Canna Lily/Canna Lily/Unknown. No commissioned crop/substrate replacement, before/after/reversal receipt exists; never activate seeds as calibration truth.

| Acceptance criterion | Result |
|---|---|
| Current map, seed and DB rows agree without overwriting valid operator-specific values. | not fully proven |
| No saturation/drip-skip consumer uses an uncommissioned or mismatched target as valid. | not fully proven |
| Before/after row receipt and exact rollback data exist; #45 can validate the resulting feedback contract. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/398#issuecomment-5158518109), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/398#issuecomment-5465356923), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/398#issuecomment-5472063714), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/398#issuecomment-5956403102).

## #410 — [C6][P1] Measure realized solar-night dry-out before changing the controller

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

Latest24h actions includeHEAT,IDLE,DEHUM_VENT and wet assists. This aggregation does not prove solar-night episodes, response and zero solar-day held-temperature admission; retain realized-episode acceptance rather than fixed-clock/night PASS.

| Acceptance criterion | Result |
|---|---|
| Episodes use device solar semantics, never fixed 02:00–06:00, and incomplete/confounded episodes cannot be PASS. | not fully proven |
| Solar-day held-temperature admission is zero while ordinary daytime dehumidification remains correctly distinguished. | not fully proven |
| Planner/MCP sees realized validity-bearing outcomes and explicit disposition, not a universal VPD target or a crop-safety claim without sensing. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/410#issuecomment-4894235988), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/410#issuecomment-4929653025), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/410#issuecomment-4931040760), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/410#issuecomment-4931409863), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/410#issuecomment-5956403092).

## #412 — [C7][P2] Close the seasonal door screen only when measured night conditions justify it

State: **OPEN**; disposition: deferred; stage: C7; accountable role: Operator with data/control lead.

No measured seasonal door-screen trigger or physical closure observation is available. Reminder/calendar prose is not completion; keep operator seasonal decision deferred.

| Acceptance criterion | Result |
|---|---|
| The trigger and physical completion are recorded from observation, not a reminder date alone. | not fully proven |
| Temperature/moisture/ventilation remain within the declared safety envelope after closure. | not fully proven |
| Any study receives a visible intervention/deviation record rather than an unexplained seasonal covariate change. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/412#issuecomment-5956403096).

## #419 — [C4][P1] Populate real outdoor freshness in replay and enforce branch coverage

State: **OPEN**; disposition: incomplete; stage: C4; accountable role: Platform lead.

Retained handoff reports real outdoor replay corpus still lacksHUMIDIFY coverage. Live outdoor/forecast values are fresh but do not prove corpus fresh/stale branches, no future leakage and known-good replay qualification.

| Acceptance criterion | Result |
|---|---|
| Corpus contains measured finite outdoor age with provenance and both fresh/stale branches; a zero-coverage corpus fails loudly. | not fully proven |
| No future outdoor sample leaks into replay; missing values preserve safe stale behavior. | not fully proven |
| Native/invariant/band and targeted outdoor tests pass against the candidate and known-good self-diff. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/419#issuecomment-4931040832), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/419#issuecomment-4931147602), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/419#issuecomment-4931409862), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/419#issuecomment-5947781002), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/419#issuecomment-5956403082).

## #424 — [C0][P0] Resolve served, consumed and raw-readback band lineage without fabricated device truth

State: **OPEN**; disposition: incomplete; stage: C0; accountable role: Evidence/data lead.

C1 retained original callback/two-epoch/full48 work remains distinct from historical ideal-SQL/served/device semantics. Current rawreadbacks and repeated desired drift holds do not close historical/consumed-band lineage; do not label desired/cache rows device confirmation.

| Acceptance criterion | Result |
|---|---|
| All relevant low/high/target series have documented source, unit, grid, timestamps and consumed/readback relationship. | not fully proven |
| The historical discrepancy is explicitly resolved, still present or unobservable; a desired row/cache is never called device confirmation. | not fully proven |
| #749 can distinguish reporting-only mismatch from a real control contradiction and fails closed on the latter without an OTA workaround. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/424#issuecomment-5554352122), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/424#issuecomment-5555161612), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/424#issuecomment-5855411103), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/424#issuecomment-5857688081), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/424#issuecomment-5956402686).

## #428 — [C6][P1] Diagnose heap/watchdog risk and qualify the actual last-good firmware floor

State: **OPEN**; disposition: incomplete; recorded predecessors complete; stage: C6; accountable role: Control lead.

Seven-day diagnostics include very low memory and software resets around recovery; current uptime277688s and heap61.24 (published KiB despite column heap_bytes). Lowest retained min-free2.336KiB remains historical. Current heap warning12534 and full worst-load/fragmentation/last-good-floor qualification remain open; no watchdog cause inferred from Software reset.

| Acceptance criterion | Result |
|---|---|
| Cause is reproduced or uncertainty explicitly bounded with current load/reset evidence; software-reset versus watchdog counts are distinct. | not fully proven |
| Candidate meets predeclared heap/fragmentation/timing and clean-runtime criteria under representative worst-case load. | not fully proven |
| No last-good promotion occurs solely on compile/replay success, a reset count omission or a projected heap improvement. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/428#issuecomment-4895481188), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/428#issuecomment-4929900349), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/428#issuecomment-5387272888), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/428#issuecomment-5387604698), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/428#issuecomment-5956402658).

## #430 — [C6][P1] Simplify firmware only while preserving autonomous control and observable truth

State: **OPEN**; disposition: incomplete; stage: C6; accountable role: Control lead.

Historical#433 two-hour receipt stays closed and valid for that window. New desired-policy holds are#949, not evidence that theoretical firmware simplification savings or broad tiers are delivered.

| Acceptance criterion | Result |
|---|---|
| Each tier has measured before/after heap/traffic and zero unintended native/replay behavior divergence. | not fully proven |
| Network-isolation autonomy, reset/water-budget conservatism and current full-state evidence survive. | not fully proven |
| #428/#369/#367/#370 receipts define the achieved scope; theoretical entity or heap savings are labeled projections. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/430#issuecomment-4895762628), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/430#issuecomment-4896255226), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/430#issuecomment-4929652835), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/430#issuecomment-5956402670).

## #434 — [C6][P1] Enforce center-only climate mist and commissioned wall-only fertigation

State: **OPEN**; disposition: blocked; stage: C6; accountable role: Control lead.

No current all-origin reverse-conflict/plumbing/calibration/duplicate-dose physical release acceptance exists. Missing chemistry/soil calibration blocks feed; no center non-climate or non-wall fertilization is authorized.

| Acceptance criterion | Result |
|---|---|
| All command origins and reverse conflicts prove zero south/west climate demand, zero center non-climate mist and zero non-wall fertilizer actuation. | not fully proven |
| Missing/stale/nonfinite/zero/out-of-bounds calibration prevents feed; restart/duplicate/missed-window cases cannot double-dose. | not fully proven |
| Native/invariant/replay/band/compile and make irrigation-stack-software-check pass; #390 release ties actual routing/readbacks/sequence to exact binary with rollback. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/434#issuecomment-5956402673).

## #581 — [C2][P0] Experiment program: qualify, run and read out the bounded AI-admission pilot

State: **OPEN**; disposition: incomplete; stage: C2; accountable role: Research/release lead.

Live experiment counts:0 draws,0 assignment outcomes,0 freezes,0 exports,0 proof receipts,0 open exposures. Full physical qualification/design/readout program remains unfinished.

| Acceptance criterion | Result |
|---|---|
| No launch before exact physical proof, tested outcome path, frozen design/no-redraw and separate launch authority. | not fully proven |
| All assigned days, including failed delivery, fallback, rescue and null outcomes, survive into ITT. | not fully proven |
| Final report states whether the result is feasible, promising, adverse or inconclusive and distinguishes climate noninferiority from improvement and runtime from resource savings. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/581#issuecomment-5404337145), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/581#issuecomment-5448496252), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/581#issuecomment-5472054820), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/581#issuecomment-5472171215), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/581#issuecomment-5956402364).

## #586 — [C8][P2] Qualify a future atomic firmware policy engine with crash-safe recovery

State: **OPEN**; disposition: deferred; stage: C8; accountable role: Research/platform lead.

Future atomic firmware policy engine remains device-denied/deferred. No complete per-tick/recovery/worst-load heap proof or separate current OTA/protocol acceptance.

| Acceptance criterion | Result |
|---|---|
| Every consumer reads one atomic per-tick policy and invalid/corrupt/expired state returns to approved safe baseline. | not fully proven |
| Worst-case heap/fragmentation/watchdog margins and independent recovery flashing are measured, not projected. | not fully proven |
| Separate OTA authority and future protocol decision exist before vector mode can enable; current pilot remains unchanged. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/586#issuecomment-5303816689), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/586#issuecomment-5387263592), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/586#issuecomment-5387805240), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/586#issuecomment-5465357022), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/586#issuecomment-5956402352).

## #587 — [C1][P0] Qualify fail-closed lifecycle, kill switch and blinded operational visibility

State: **OPEN**; disposition: incomplete; stage: C1; accountable role: Runtime/release lead.

Component workers areReady and active experiment ID is empty; disabled deployment is not kill/disable/recovery/alert-fault qualification. Current recovery/role/rollback matrix and real routed fault receipts remain incomplete.

| Acceptance criterion | Result |
|---|---|
| Feature off means zero experiment calls; locked/armed cannot reopen readiness and emergency hold cannot actuate. | not fully proven |
| Normal disable/ID clear/ordinary ownership restoration requires confirmed baseline; only an explicit facility-owned emergency-safe closure may state otherwise without relabeling it baseline. | not fully proven |
| Alerts actually fire and clear through the owning delivery route, no comparative leakage occurs, and the exact-source rollback test is consumed by #783/#641. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/587#issuecomment-5440364806), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/587#issuecomment-5448498146), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/587#issuecomment-5472171245), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/587#issuecomment-5800446636), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/587#issuecomment-5956402332).

## #588 — [C2][P0] Lock the qualified pilot identity and finalize exactly one internal random draw

State: **OPEN**; disposition: blocked; stage: C2; accountable role: Research/release lead.

Live randomization count0. No current scientific lock/draw is complete;#782/#783/#641 remain open. Do not finalize or recreate a draw from preparation.

| Acceptance criterion | Result |
|---|---|
| Current model/context/request limits, full source identity and #782 scientific choice match the lock byte-for-byte. | not fully proven |
| Exactly one draw exists; duplicate/replacement/caller-time/secret attempts fail without leaking mapping or randomization material. | not fully proven |
| Future first assignment boundary and schedule commitment reproduce; no physical randomized work occurs before the separate #642 authority. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/588#issuecomment-5440364812), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/588#issuecomment-5448496294), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/588#issuecomment-5472171223), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/588#issuecomment-5850205968), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/588#issuecomment-5956402326).

## #606 — [C8][P2] Register the twin build profile only through its owning fleet contract

State: **OPEN**; disposition: deferred; stage: C8; accountable role: Research/platform lead.

Twin fleet profile remains upstream-owned and must be delivered there. This repository mirror was not edited; no new profile acceptance is claimed.

| Acceptance criterion | Result |
|---|---|
| Owning registry and generated mirror agree and one verified immutable digest is consumable. | not fully proven |
| Runtime is read-only/device-denied and has no undeclared build or network dependency. | not fully proven |
| This enablement issue is not a blocker for the current #581/#642 component pilot. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/606#issuecomment-5387263610), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/606#issuecomment-5465357048), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/606#issuecomment-5472054474), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/606#issuecomment-5956402008).

## #638 — [C8][P2] Unify future manifest/vector wire schema and content identity offline

State: **OPEN**; disposition: deferred; stage: C8; accountable role: Research/platform lead.

Future wire schema/content identity remains offline/deferred. No all-consumer future protocol acceptance is established and no vector authority is enabled.

| Acceptance criterion | Result |
|---|---|
| Cross-language identity and actual service declaration/host call schemas agree; tests fail against the former incompatible interface. | not fully proven |
| Restore admission creates the intended outbox and compiled commit/stage/activate/expiry/abort paths consume host bytes. | not fully proven |
| Generalized vector mode remains off; closing this issue does not switch any active trial transport or claim physical qualification. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/638#issuecomment-5465357054), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/638#issuecomment-5956402001).

## #639 — [C1][P0] Qualify the existing component executor and full-state recovery before launch

State: **OPEN**; disposition: incomplete; stage: C1; accountable role: Runtime/release lead.

Retained C1 original callback/full48 evidence is not full lifecycle/executor failure/recovery/restored randomized qualification. Current proof receipts0; no execution/fault matrix was rerun.

| Acceptance criterion | Result |
|---|---|
| No prefix opens exposure; two fresh full-48 epochs with current generations are required for baseline and target. | not fully proven |
| Exclusive bundles never interleave with ordinary/MCP/forecast/retry writers; shadow emits zero calls and nonrandom work has no assignment lineage. | not fully proven |
| Recovery closes exposure first, yields to facility rescue, uses linked baseline-only work and verifies two epochs before ordinary-writer handoff. The qualified source receipt is reusable by #641 and #642. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/639#issuecomment-5448499782), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/639#issuecomment-5468096590), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/639#issuecomment-5468196588), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/639#issuecomment-5472171207), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/639#issuecomment-5956401956).

## #640 — [C3][P0] Freeze the first assigned-day ITT outcome and blinded reproducible export

State: **OPEN**; disposition: blocked; stage: C3; accountable role: Research lead.

Live assigned outcomes/freezes/exports are0. No first randomized assigned day exists to freeze; synthetic source fixtures are not observed ITT outcomes.

| Acceptance criterion | Result |
|---|---|
| The first full assigned window is frozen twice to identical canonical rows/hashes and consumed by the analyzer without reshaping or reveal. | not fully proven |
| A ledger reconciliation proves every assignment has exactly one row irrespective of exposure or outcome availability. | not fully proven |
| Freezer cannot mutate assignments/read mapping; analyst sees frozen X/Y only. Missing center/DLI/gas and unselected resource fields remain truthfully unavailable. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/640#issuecomment-5440364841), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/640#issuecomment-5448499831), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/640#issuecomment-5472054216), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/640#issuecomment-5472171224), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/640#issuecomment-5956401968).

## #641 — [C1][P0] Execute authorized orphan recovery and one separately attended physical proof

State: **OPEN**; disposition: blocked; stage: C1; accountable role: Runtime/release lead.

Live proof receipt count0. Retained controller recovery/C1 restoration is not a sealed baseline→aggressive→baseline GateP proof. Draft#855 remains unmerged; physical readiness and separate exact authority stay unfulfilled.

| Acceptance criterion | Result |
|---|---|
| Recovery retains predecessor failures, creates no nonbaseline exposure and earns no proof credit. | not fully proven |
| Exactly one successor-attempt receipt binds baseline-before/aggressive/baseline-after to actual current source and observations. | not fully proven |
| Mismatch closes exposure/revokes nonbaseline before facility-compatible recovery; final baseline-confirmed draft/shadow/closed, feature off, empty ID, vector off and zero exposure are evidenced. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/641#issuecomment-5855797659), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/641#issuecomment-5856417176), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/641#issuecomment-5857358991), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/641#issuecomment-5862238829), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/641#issuecomment-5956401570).

## #642 — [C2][P0] Authorize and verify one randomized day-1 activation from the locked pilot

State: **OPEN**; disposition: blocked; stage: C2; accountable role: Research/release lead.

No locked draw or live randomized assignment. Exact launch/observed physical target/exposure/freezer/rollback acceptance remains downstream; no launch performed.

| Acceptance criterion | Result |
|---|---|
| Exact launch authority, unique assignment, frozen identity, observed physical target, two epochs and exposure lineage exist. | not fully proven |
| Freezer can progress, singleton writer/public health hold and exact-source Argo is Synced + Healthy. | not fully proven |
| Rollback evidence is current and first-day/export ownership hands to #640; closure does not claim experiment success. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/642#issuecomment-5450276404), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/642#issuecomment-5450784581), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/642#issuecomment-5472054884), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/642#issuecomment-5472171216), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/642#issuecomment-5956401591).

## #643 — [C5][P1] Extend least-privilege database roles beyond the already-required experiment boundary

State: **OPEN**; disposition: incomplete; recorded predecessors complete; stage: C5; accountable role: Data/platform lead.

Least-privilege source/workload role changes have landed, but current complete allow/deny/login boundaries and rollback are not proved by Ready pods. Preserve no-secret-output and separate owner credentials.

| Acceptance criterion | Result |
|---|---|
| Live-role matrix allows intended functions and rejects unrelated DML, mapping reads and cross-workload capabilities. | not fully proven |
| No credentials or Secret annotation values enter issue/log/backup artifacts; ordinary pods lack owner/migration authority. | not fully proven |
| Public/planner/ingestor health and experiment integrity survive rollout/rollback; no unsolicited credential rotation occurs. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/643#issuecomment-5465357074), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/643#issuecomment-5956401569).

## #644 — [C4][P1] Integrate exact-SHA, path-aware and device-safe delivery receipts

State: **OPEN**; disposition: incomplete; stage: C4; accountable role: Platform lead.

Source-only promotion receipt is delivered; fleet path-impact, builder/promoter authority and durable exact-SHA reliability remain agents#4014(open). Missing original workflow now means terminal history must be read from durable receipt, not restarted.

| Acceptance criterion | Result |
|---|---|
| Exact-SHA results are durable, redacted and actionable; no recursive builds from pin commits. | not fully proven |
| Builder cannot deploy/promote and ordinary validation cannot access device/OTA/runtime secrets; immutable digest consumer receipt identifies rollback pins. | not fully proven |
| Verdify integration and owning fleet dependency are linked; current branch policy is measured rather than copied from an August snapshot. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/644#issuecomment-5470763164), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/644#issuecomment-5472054964), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/644#issuecomment-5472171228), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/644#issuecomment-5917182901), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/644#issuecomment-5956401565).

## #749 — [C1][P0] Qualify current three-probe readiness and explicit safety dependencies

State: **OPEN**; disposition: blocked; stage: C1; accountable role: Runtime/release lead.

Raw north/east/west triplets are fresh and south absent despite4/OK diagnostic. Latest northVPD2.87 versus east1.43/west1.37 makes spread relevant. #424/#778 plus new#949 desired-policy holds require current fail-closed disposition; no fresh GateP packet is claimed.

| Acceptance criterion | Result |
|---|---|
| 3-of-4 degraded-pass is truthful; two or fewer probes, nonfinite aggregates, excess spread, stale evidence or semantic contradiction fail closed. | not fully proven |
| Hydro is traced as absent from proof/selector/executor/outcome causal inputs; any new dependency is classified before use. | not fully proven |
| Packet is read-only, current at every boundary and cannot grant proof or randomized authority; probe loss closes exposure before recovery. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/749#issuecomment-5849624847), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/749#issuecomment-5851509028), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/749#issuecomment-5855261402), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/749#issuecomment-5856448950), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/749#issuecomment-5956401208).

## #750 — [C1][P0] Recovery/proof campaign: rebaseline current state and seal one safe physical receipt

State: **OPEN**; disposition: incomplete; stage: C1; accountable role: Runtime/release lead.

Fresh ArgoSynced/Degraded,26 workload image specs match desired, one writer and paired backup succeeded. #747 ishistorically closed, but#749/#641 remain open. Recovery/proof roll-up cannot close from ready services.

| Acceptance criterion | Result |
|---|---|
| Fresh source/Argo/runtime/backup receipts replace old point-in-time assertions and identify any remaining mismatch. | not fully proven |
| #747, #749 and #641 have their own acceptance evidence; proof and orphan recovery never share credit. | not fully proven |
| Final safe state and rollback receipt are verified; no randomized design/draw/launch is performed under this roll-up. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/750#issuecomment-5468197281), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/750#issuecomment-5472060698), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/750#issuecomment-5472171210), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/750#issuecomment-5472448371), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/750#issuecomment-5956401205).

## #751 — [C7][P2] Replace the failed south climate probe and diagnose hydro telemetry in a bounded window

State: **OPEN**; disposition: incomplete; stage: C7; accountable role: Operator with data/control lead.

South temperature/RH/VPD has0 samples over7d; hydro mostly absent with only27 pH rows and no fresh hydro pH sinceOct1. Diagnostics still4/OK; no replacement/reference calibration or truthful contributor repair completed.

| Acceptance criterion | Result |
|---|---|
| South supplies credible fresh finite temperature/RH/VPD and is validated against reference/neighbors; hydro cause and restored or explicit unresolved state are documented. | not fully proven |
| Contributor diagnostics become truthful and alerts resolve from telemetry, not manual deletion. | not fully proven |
| No replacement becomes a surprise pilot gate or silently changes the locked analysis panel. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/751#issuecomment-5472171222), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/751#issuecomment-5956401224).

## #775 — [C0][P0] Campaign: trustworthy greenhouse outcomes → qualified pilot → evidence-backed control

State: **OPEN**; disposition: incomplete; stage: C0; accountable role: Evidence/data lead.

Current review covers every open issue and acceptance clause, adds five bounded gaps, closes#835, and reconciles the complete native graph. The genuine pilot/readout remains unfinished; this triage does not close the campaign.

| Acceptance criterion | Result |
|---|---|
| Every issue open at the snapshot has exactly one owner role, stage, what/why/how, testable acceptance and disposition. | proven |
| The full native dependency graph is acyclic and equals the repository graph; no child is blocked by its own umbrella. | proven |
| The pilot ends in a reproducible decision, including an inconclusive/no-benefit result if that is what the evidence supports; broader efficiency claims remain gated. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/775#issuecomment-5944113256), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/775#issuecomment-5944226854), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/775#issuecomment-5944734449), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/775#issuecomment-5944858465), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/775#issuecomment-5945346494).

## #778 — [C0][P0] Investigate September 4 hot/dry peak and three-hour wetting interruption

State: **OPEN**; disposition: incomplete; stage: C0; accountable role: Evidence/data lead.

September4 joined hashed timeline/counter-reset/cause or explicit fail-closed safety disposition remains incomplete. Current wetting operation and later OTA recovery do not explain the historical three-hour interruption.

| Acceptance criterion | Result |
|---|---|
| Timeline reproduces the three-hour interruption with raw-row hashes, provenance and explicit unknowns. | not fully proven |
| The flat cumulative 600 and mister-today 157.38 counters are not labeled an exhausted 600-gallon budget without matching semantics/reset/limit evidence. | not fully proven |
| A named cause plus regression evidence, or a fail-closed unresolved constraint, is incorporated into #749; no unsafe wetting proof is allowed by an unexplained safety contradiction. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/778#issuecomment-5896496408), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/778#issuecomment-5917007834), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/778#issuecomment-5917162796), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/778#issuecomment-5917318904), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/778#issuecomment-5956401213).

## #782 — [C0][P0] Choose a season-appropriate exploratory pilot and freeze the scientific contract

State: **OPEN**; disposition: blocked; stage: C0; accountable role: Evidence/data lead.

Merged warm pre-draw contract is preparation forJune1–July30,2027,30pairs/60localdays/18h. Original79source hashes/31forecast vintages are retained but0target/contributor rows and no original provider choice; empirical covariance/jointpower remainNULL. No draw.

| Acceptance criterion | Result |
|---|---|
| An explicit decision document chooses pilot versus confirmatory scope and states exactly which claim can be tested with current meters. | not fully proven |
| Power replay is reproducible; include .99^30 ≈ .740 and .95^30 ≈ .215 all-pairs retention sensitivities rather than assuming .9995 reliability. | not fully proven |
| Calendar is future and seasonally justified; 06:00–24:00 remains 18 elapsed hours on the fall clock-change day, with any full-day scheduler implications tested separately. | not fully proven |
| No draw has occurred before design approval/qualification; model is current source-pinned Luna/medium, not stale Sol issue prose. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/782#issuecomment-5867641606), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/782#issuecomment-5874211338), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/782#issuecomment-5894640314), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/782#issuecomment-5895483789), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/782#issuecomment-5956255448).

## #783 — [C1][P0] Prove restore → selector → setter schema → receipt → frozen outcome end to end

State: **OPEN**; disposition: blocked; stage: C1; accountable role: Runtime/release lead.

Retained7547-byte3-pair synthetic fixture export/analyzer reproducibility is source-only. Actual current-login role whole-path/twice-repeated untouched freezer bytes remain incomplete; do not run clock-mutating fixture on genuineSOURCE/A/B.

| Acceptance criterion | Result |
|---|---|
| One command/artifact index reproduces the whole path twice with byte-identical canonical hashes and no data reshaping between freezer and analyzer. | not fully proven |
| First-day, missing-day, zero-exposure, reset and failed-delivery rows exist by assignment, never filtered by exposure. | not fully proven |
| Device-denied restore contains no production credentials in logs/artifacts; minimum randomizer/lifecycle/executor/freezer/blinded-analyst boundaries pass. Current source and failure matrix are recorded before #588. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5872210017), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5895257032), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5916327577), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5917252283), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5956282639).

## #784 — [C3][P1] Operate the complete blinded pilot and preserve every assigned day

State: **OPEN**; disposition: blocked; stage: C3; accountable role: Research lead.

0 live assignment outcomes/freezes/exports. No genuine blinded60-day pilot can be operated/accounted for yet; every eventual failed/null/fallback day must remain assigned.

| Acceptance criterion | Result |
|---|---|
| All locked assignments are accounted for, including incomplete/no-exposure/aborted days; no replacement pair, redraw or post-hoc endpoint change occurs. | not fully proven |
| Blinded integrity and safety reviews have receipts at the cadence declared before launch; departures are preserved. | not fully proven |
| Study ends safe with stopped admission, closed exposure, frozen outcome/deviation/fidelity/environment hashes ready for #785. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/784#issuecomment-5956400825).

## #785 — [C3][P1] Reveal once, reproduce the frozen analysis and publish the campaign decision

State: **OPEN**; disposition: blocked; stage: C3; accountable role: Research lead.

No genuine pilot export/reveal/readout exists. A reproducible final adverse/no-benefit/inconclusive decision remains downstream; source fixtures are not efficacy.

| Acceptance criterion | Result |
|---|---|
| Independent rerun from frozen artifacts produces identical tables and decision; reveal occurs once with audited authority. | not fully proven |
| Report permits adverse/no-benefit/inconclusive conclusions and gives no yield/profit/whole-resource/other-season claim beyond measurements. | not fully proven |
| #775/#581 close only on this completed decision boundary; #786/#787 and deferred work have explicit go/no-go rationale, not automatic promotion. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/785#issuecomment-5956400824).

## #786 — [C8][P2] Design the next comparison against a deterministic forecast-aware selector

State: **OPEN**; disposition: deferred; stage: C8; accountable role: Research/platform lead.

Merged deterministic forecast comparator preparation is unregistered. Final go/no-go depends on#785; real training/covariance/power and future separate lock/authority remain unavailable.

| Acceptance criterion | Result |
|---|---|
| A go/no-go decision is linked to #785, with a prespecified comparator and no reused revealed schedule. | not fully proven |
| Offline command replay is not described as a physical climate or resource effect. | not fully proven |
| Any new trial has its own lock/draw/authority and a claim that matches its measured endpoint. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/786#issuecomment-5948363026), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/786#issuecomment-5956400809).

## #787 — [C8][P2] Plan measured heating, overnight and whole-resource economics as a separate claim

State: **OPEN**; disposition: deferred; stage: C8; accountable role: Research/platform lead.

Whole-resource/overnight specification is preparation. Current partial meters, ambiguous shared water, absent gas/interiorDLI/calibration/cost do not support whole-resource or profit claims.

| Acceptance criterion | Result |
|---|---|
| Each claimed resource has eligible measured coverage and calibration/uncertainty; modeled proxies remain secondary. | not fully proven |
| A new prospective study can test climate improvement and resource benefit as stated, or the claim is explicitly narrowed. | not fully proven |
| No hardware purchase, gas tuning, yield/profit claim or seasonal controller expansion follows automatically from the current pilot. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/787#issuecomment-5917099536), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/787#issuecomment-5956400808).

## #801 — [VP-02] Exclude www-dev and proposals-hello from the verdify-tier1-forward HostRegexp

State: **OPEN**; disposition: blocked; stage: estate; accountable role: Platform lead.

Live catch-all still lackswww-dev andproposals-hello exclusions. Native agents#4461 predecessor is retained; no route change or unrelated fullsync performed.

| Acceptance criterion | Result |
|---|---|
| Source and live forwarder exclude both named external routes. | not fully proven |
| Both hosts reach their owners with public/TLS behavior verified and ordinary Verdify hosts preserved. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/801#issuecomment-5849604754), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/801#issuecomment-5956559473).

### Exact original verification contract

All commands and subrequirements remain unproven beyond the explicitly stated current observations. The grouped acceptance rows above retain this complete contract:

- The live route contains both new exclusions: `kubectl --context vallery -n verdify-prod get ingressroute verdify-tier1-forward -o jsonpath='{.spec.routes[0].match}'`.
- The operation for `verdify-prod-dark` has phase `Succeeded`, `syncResult.resources` lists only this IngressRoute, and no entry has `hookType`.
  - The IngressRoute is `Synced`.
  - The other OutOfSync resources are exactly the set recorded in step 1.
- `kubectl --context vallery -n traefik-apps logs ds/traefik-apps --since=10m | grep -i tier1-forward` shows no router or rule error.
- Public checks match the step 1 baseline:
  - `https://www-dev.verdify.ai/` returns 200 with the verdify-www page;
  - `https://proposals-hello.verdify.ai/` returns 200;
  - `api`, `lab`, `labs`, `graphs` and `mcp` return the same statuses as before;
  - the unknown host still returns 404.
- The ingestor pod UID and restart count are unchanged. An ESP32 connection count is not a useful signal here, because the ingestor dials the device directly and never passes through Traefik.


## #802 — [VP-01] Put verdify-descheduler under Argo (manual) with Git equal to live enforce mode

State: **OPEN**; disposition: blocked; stage: estate; accountable role: Platform lead.

Live enforce args omitdry-run; no argocd verdify-deschedulerApplication exists. Git/live adoption hazard and native agents#4451/#4459 remain; no apply or eviction changes. Current agents#4459 is closed; agents#4451 remains open.

| Acceptance criterion | Result |
|---|---|
| Seven objects adopted under manual parented Argo with unchanged enforce specs/UID/generation. | not fully proven |
| Namespace ownership, residual writer grant and tracking scope remain exact. | not fully proven |
| Natural enforce Job and live sync-classification audit pass without dry-run. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/802#issuecomment-5849604846), [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/802#issuecomment-5956559057).

### Exact original verification contract

All commands and subrequirements remain unproven beyond the explicitly stated current observations. The grouped acceptance rows above retain this complete contract:

- All 7 objects carry `argocd.argoproj.io/tracking-id: verdify-descheduler:<group>/<kind>:<ns>/<name>`. The Namespace has none.
- The CronJob keeps the same uid and `metadata.generation` 6. Its args are the 3 enforce args, and the evict annotation is `"true"`.
- `argocd app get verdify-descheduler` shows:
  - Synced, with `sync.revision` equal to the pinned SHA;
  - no automated policy;
  - tracked by `agent-fleet-local-prod-apps`;
  - no SharedResourceWarning.
- The next `:00`/`:30` Job completes:
  - its pod args contain no `--dry-run`;
  - its log has the `Number of evictions/requests` line;
  - its log has no `Building a cached client from the cluster for the dry run` line, which prints only in dry-run mode at `--v=4`.
- `render_sync_classification.py --audit-live` lists `verdify-descheduler` as parented and classified `direct`.


## #835 — Fix reconnect equipment snapshot provenance and stale relay alerts

State: **CLOSED**; disposition: accepted; stale tracker closed; stage: C4; accountable role: Platform lead.

Complete bounded acceptance: deployedingestor.py bytehash matchesmain,15 focusedtests pass; complete/incomplete gaps are truthful; all11 cited alerts11537–11547 auto-resolved(system). Closed with exact source/digest/socket/readback receipt; unrelated vision degradation stays#951.

| Acceptance criterion | Result |
|---|---|
| Fenced fresh canonical state capture without cache inference. | proven |
| Snapshot_taken only on complete persisted burst; incomplete otherwise. | proven |
| Focused reconnect tests plus live readback and monitor-owned cited alert resolution. | proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/835#issuecomment-5956558708).

## #862 — Lab publisher: warm-cache init rescans the public tree for ~5 min, pushing runs past the 10-min schedule and the 20-min freshness window

State: **OPEN**; disposition: incomplete; stage: C4; accountable role: Platform lead.

Latest three publisher runs last734/754/759seconds, exceeding10-minute schedule and landing20minutes apart. Served receipt was current at19:03 but expires19:12:31; repeated warm-scan/cadence/freshness defect remains, not a stalled init.

| Acceptance criterion | Result |
|---|---|
| Bound warm-cache initialization with measured scan timings and unchanged safety guard. | not fully proven |
| Natural publisher cadence and fresh_until availability satisfy the declared contract. | not fully proven |
| Source/runtime receipt and preserved failure/rollback data distinguish active work from stall. | not fully proven |

Retained issue evidence: [comment](https://github.com/VerdifyConsultancy/verdify-platform/issues/862#issuecomment-5956558333).

## #949 — [C4][P1] Bound planner-policy drift holds and prove autonomous safe convergence

State: **OPEN**; disposition: new; incomplete; stage: C4; accountable role: Platform lead.

2026-10-04T21:09:43–2026-10-05T19:01:12 bounded 16,000-line log: 260 blocked_broad_restore records. Latest 18:58:59Z has command_count=21, limit=12, connection generation9. At 18:00:02Z a stage was refused because fog_escalation_kpa snapshot did not match the connection. 85 writes confirmed over24h; no failed24h delivery rows. Sole Recreate writer digest4c5fa5d…b664/sourcee91b2a7d; independent socket oracle exactly1. Newly identified; implementation and live acceptance remain open.

| Acceptance criterion | Result |
|---|---|
| Every held field has actual desired/readback provenance and an explicit safe convergence or bounded hold disposition. | not fully proven |
| No repeated broad device push, cap increase, fabricated confirmation or replay of expired approval; delivery failure/cancellation remains terminal history. | not fully proven |
| Natural or explicitly scoped plan change converges through permitted stages, with one writer, timely tasks, fresh confirmation, and a two-hour exact-source receipt. | not fully proven |

## #950 — [C4][P1] Restore the ingestor Slack credential-file contract and operator delivery

State: **OPEN**; disposition: new; incomplete; stage: C4; accountable role: Platform lead.

Current ingestor logs: evening brief2026-10-05T00:04:30Z, midnight watch06:05:25Z, and five morning retries13:30:58–13:34:59Z fail FileNotFoundError for /etc/verdify/slack/iris_slack_bot_token.txt. Source base ingestor mounts HA credentials, but no Slack mount at that path; SLACK_TOKEN_FILE env is absent. No credential contents were read. Newly identified; implementation and live acceptance remain open.

| Acceptance criterion | Result |
|---|---|
| Source-rendered and running file/env contracts agree; token remains secret and existing credential is preserved. | not fully proven |
| Natural evening/midnight/morning delivery succeeds and retries are bounded with actionable failures. | not fully proven |
| Intended alert path is checked independently of brief success; no test message or credential rotation is sent by this triage. | not fully proven |

## #951 — [C4][P1] Restore authenticated greenhouse camera snapshots after connection refusals

State: **OPEN**; disposition: new; incomplete; stage: C4; accountable role: Platform lead.

Jobs29852640,29852820,29853000 (2026-10-05T00:00/03:00/06:00Z) failed. Latest logs: both greenhouse_1/2 ConnectionRefusedError,0/2 captured. CronJob uses https://frigate.frigate.svc.cluster.local:8971 with cameras.vallery.net TLS identity. Service port8971 currently targets endpoint192.168.30.39:8973. A port-number difference is a diagnostic lead, not proof it is wrong. Newly identified; implementation and live acceptance remain open.

| Acceptance criterion | Result |
|---|---|
| Both authenticated camera requests return current valid JPEGs with the declared TLS identity; no broad/admin endpoint or secret is exposed. | not fully proven |
| A natural verdify-vision Job succeeds through capture and analysis; missing/partial/stale input still fails truthfully. | not fully proven |
| Argo aggregate degradation is independently rechecked; old failed Jobs are preserved until disposition, never deleted merely to make health green. | not fully proven |

## #952 — [C4][P1] Preserve Verdify Grafana monitoring identity across pod replacement

State: **OPEN**; disposition: new; incomplete; stage: C4; accountable role: Platform lead.

Current Prometheus/Alertmanager: EndpointProbeCellDown and EndpointProbeAuthEvidenceFailed for graphs.verdify.ai at local and external-cloudflare. #75 comment5852409374 reports service-account loss after pod replacement; current grafana.yaml still mounts /var/lib/grafana from emptyDir. Anonymous/basic scrape success is not authenticated account acceptance. Newly identified; implementation and live acceptance remain open.

| Acceptance criterion | Result |
|---|---|
| A least-scope service identity survives or is recreated deterministically after replacement without printing or rotating credentials unsolicited. | not fully proven |
| Local and external authenticated probe evidence succeeds, negative unauthenticated behavior remains denied, and related alerts recover. | not fully proven |
| Rendered/source/runtime identity and rollback are recorded; UI availability alone is insufficient. | not fully proven |

## #953 — [C4][P2] Reconcile Iris planning skill lookup and required reference inventory

State: **OPEN**; disposition: new; incomplete; stage: C4; accountable role: Platform lead.

Retained live Hermes log reports missing devops/greenhouse-planning-mcp and devops:greenhouse-planning-mcp lookups, plus missing references/full-plan-payload-preflight.md. The seven-day ledger still completes required plans, so this is a degraded lookup surface, not a planner outage. Newly identified; implementation and live acceptance remain open.

| Acceptance criterion | Result |
|---|---|
| Every required prompt reference resolves in the running profile at exact source/config identity. | not fully proven |
| Next natural required cycle writes a valid plan without missing-skill/reference retries; no extra provider calls solely for triage. | not fully proven |
| No static worker-ready signal is presented as plan success; existing ledger/source proofs remain intact. | not fully proven |

