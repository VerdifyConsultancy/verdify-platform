# Verdify Platform work pending

> Superseded Lab finding (2026-08-30): the public Astro artifact described in
> this point-in-time audit was an emergency outage substitution, not a completed
> generator cutover. Production history showed Quartz was authoritative through
> 2026-08-24. The Lab is now standardized on Quartz; see
> `docs/reviews/lab-generator-standardization-2026-08-30.md`.

**Audit date:** 2026-08-29 16:48 MDT

**Scope:** all 106 GitHub issues that were open at the start of the audit

**Source revision:** `6a22ab3eb655907389d1b5f7be8e89db58abb598`

**Live contexts:** `verdify-prod`, `verdify-platform`, ArgoCD applications `verdify-prod-dark` and `verdify-platform-lab-stage`

## Executive result

This audit kept 95 issues open and closed 11. Of the open work, 69 issues remain valid as written and 26 remain valid but have stale premises, partial implementations, or obsolete acceptance language that must be re-scoped before execution.

| Disposition | Count | Meaning |
|---|---:|---|
| Valid | 69 | The current source, runtime, data, or hardware state still demonstrates the requested gap. |
| Stale / valid | 26 | Residual work exists, but the issue must be rewritten around the current architecture or partial end state. |
| Close | 11 | The end state is complete, or the work was superseded and its remaining concerns are tracked elsewhere. |

The production control plane is operational: the API, database, ingestor, planner, Hermes, MCP, setpoint server, MQTT, Grafana, Astro lab, and the three experiment-v2 services are Ready. Climate and setpoint-band data are fresh. That operational health does not mean the backlog is cosmetic. The public Lab remains a provisional build with no immutable occurrence selected, the experiment has no randomization/approval/proof/outcome rows, production is not converged to the current Git revision, backup/restore and HA gaps remain, and several physical sensor/calibration gates are still unresolved.

## Evidence and limits

The review correlated each issue with its complete GitHub body and comments, the current repository, commit/PR history, Kubernetes and ArgoCD objects, public endpoints, production PostgreSQL state, and attached storage. Storage reads followed the storage-infra data inventory; no volume was changed or cleaned.

Key observed facts:

- `verdify-prod-dark` was Healthy but OutOfSync at the audited revision; `verdify-platform-lab-stage` was Healthy and Synced. No unmanaged sync or cluster mutation was performed.
- The public API health and detailed health endpoints were ready. Public MCP rejected six unauthenticated initialize attempts with HTTP 401, but the live MCP image was behind current desired source and an authenticated Hermes receipt was not available.
- Both public Lab endpoints served the same provisional 143-graph/2-camera build with zero materialized occurrence blobs and no selected occurrence.
- Production PostgreSQL was about 3.5 GiB. It contained 10,398 alerts, including 61 unresolved suppressed historical `sensor_offline` rows; all digital-twin and experiment proof/outcome tables were empty.
- The active plan and device readback both reported `band_track_fraction=0`, and the solar-phase fixtures and materialized band curve matched the shipped migration.
- The old pre-portable Hermes PVC, PV, and Longhorn volume were absent; the portable Hermes claim was in use. The ingestor still uses `emptyDir`, so its old iSCSI symptom is obsolete while the durability gap remains.
- Secrets were inspected only for object/key presence. No credential values were read into this report or exposed.
- Hardware-dependent acceptance was judged from telemetry and declared topology, not from a physical inspection. Issues requiring a real reference instrument, probe installation, OTA, or seasonal condition remain gated.

## Normalized execution model

GitHub milestones remain the outcome/epic boundary; lane labels identify the owning execution stream; one new sprint label identifies the proposed sequence. Existing historical sprint and wave labels are retained for provenance but are not the current sequencing authority.

| Sequence | Sprint label | Why now | How / exit condition |
|---:|---|---|---|
| 0 | `sprint:S0-launch-safety` | Security, truthful writes, planner delivery, and the direct experiment launch gate have the highest blast radius. | Converge the MCP/Hermes source and runtime, prove authenticated cycles, stop reconcile storms, and produce a blinded assignment with approval, daily proof, outcome, and reveal controls. |
| 1 | `sprint:S1-runtime-truth` | Delivery cannot be trusted while CI, Argo scope, firmware compilation, replay, health, and alert-delivery contracts have gaps. | Make images immutable, require checks, compile real ESPHome targets, test GitOps sync behavior, and turn runtime/alert assertions into executable receipts. |
| 2 | `sprint:S2-durability-ha` | Backups omit globals, restore ownership is advisory, the ingestor is ephemeral, and database HA remains undesigned for the current storage plane. | Complete role-aware restore, choose the current CNPG/Longhorn design, add WAL/PITR and writer fencing, and prove failover/recovery. |
| 3 | `sprint:S3-control-safety` | Control cleanup and irrigation feedback should follow trustworthy delivery and data durability. | Simplify firmware, complete sensor-integrity and anti-chatter work, align device/DB contracts, and close loops only after realized evidence. |
| 4 | `sprint:S4-lab-product` | The site is public but provisional; immutable publication depends on the earlier runtime and durability contracts. | Provision isolated occurrence/reporting tiers, publish 143+2 immutable artifacts, soak stage, select an approval-eligible snapshot, then finish product/auth surfaces. |
| gated | `sprint:gated-deferred` | Hardware, seasonal, digital-twin, and platform-v2 items need a physical event or an explicit activation decision. | Keep designs and prerequisites ready; execute only when the named gate is satisfied. |

### Execution lanes and epics

| Lane | Epic/outcome boundary | What remains | Immediate handoff |
|---|---|---|---|
| `lane:experiment` | Fast planner experiment / G3 | MCP convergence, truthful writer behavior, selector/randomization, approval, proof, outcomes, and reveal; platform-v2 stays deferred. | Run S0 as one launch-safety program, not as independent deployments. |
| `lane:L5-deploy` | Deploy Enablement + CI/CD/GitOps | Immutable images, required checks, real firmware compilation, Argo scope proof, retired branch/overlay cleanup, twin build profile. | Establish a trustworthy delivery receipt before additional OTA or experiment activation. |
| `lane:L2-obs` | Data Hygiene & Observability | Historical alert closure, replay age truth, external rule delivery, corridor/outcome views, route smoke. | Convert prose expectations into CI/runtime assertions and dashboards. |
| `lane:L6-ha` | M7 HA + DB durability | Role-complete backup/restore, CNPG/PITR design, ephemeral ingestor data, writer fencing, portable cache completion. | Resolve current Longhorn/NAS architecture before writing cutover steps. |
| `lane:L1-climate` | Greenhouse Control Optimization | Firmware simplification, band/solar evidence, anti-chatter, input integrity, deterministic per-zone bands. | Preserve current safe rails while reducing on-device surface. |
| `lane:L3-planner` / `lane:L4-data` | Planner and data contracts | Dynamic planning proof, schema/source-of-truth cleanup, future anticipatory control. | Sequence after S0 runtime truth and before advanced control. |
| `lane:L7-irrig` | Irrigation and hardware backlog | Probe/topology truth, soil targets, slow irrigation feedback, saturation/skip loop, physical calibration. | Separate software-ready work from explicit Jason/hardware gates. |
| `lane:L8-product` | Lab, publishing, auth, residual product plane | Immutable occurrence production, reporting isolation, Astro contract parity, auth re-home, agent autonomy. | Keep public provisional output fail-closed until S4 acceptance passes. |

## Issue-by-issue disposition

“Stale / valid” means keep open and edit the issue to the stated residual scope. “Close” entries include the closure basis; they do not enter a future sprint.

| Issue | Disposition | Sprint | Live/source trace and required next action |
|---|---|---|---|
| [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14) Firmware digital-twin epic | Valid | gated | Twin schema exists but all twin tables have zero rows and no twin workload is deployed. Keep as the gated umbrella for the twin program. |
| [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16) Hardware backlog epic | Valid | gated | Physical sensing, calibration, and equipment items remain unverified. Refresh the inventory at the next operator window. |
| [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) Twin setpoint coverage | Valid | gated | There is no populated twin divergence surface, so coverage cannot yet be trusted. Implement after a twin runtime exists. |
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) Irrigation feedback bring-up | Stale / valid | gated | Center moisture/EC/pH remain absent and south moisture is again stuck at zero; rewrite around the current fault, then install/repair before bring-up. |
| [#49](https://github.com/VerdifyConsultancy/verdify-platform/issues/49) Historical suppressed alerts | Valid | S2 | Migration 151 is applied, but 61 suppressed `sensor_offline` rows still have no `resolved_at`. Correct lifecycle semantics and backfill with an auditable receipt. |
| [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51) CO2 field calibration | Valid | gated | Software transforms do not establish absolute accuracy. Calibrate against the named reference NDIR and record coefficients/evidence. |
| [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) Orchid dormancy | Valid | gated | Seasonal phase/clamp/dry-down acceptance has not been exercised. Execute during the appropriate physical season. |
| [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75) Observability epic | Valid | S1 | Core health is good, but route, writer, replay, and alert-delivery gaps remain. Retain as the observability umbrella. |
| [#89](https://github.com/VerdifyConsultancy/verdify-platform/issues/89) G10 smoke and route monitor | Valid | S1 | Current public health probes do not prove the full device route or ESTAB behavior. Add the bounded post-deploy smoke/monitor. |
| [#114](https://github.com/VerdifyConsultancy/verdify-platform/issues/114) Dev/stage read-only ingestor | Close | — | The former three-environment writer topology was retired; production is the only writer and Lab stage is a presentation runtime. Close as superseded by the current single-writer/GitOps model. |
| [#174](https://github.com/VerdifyConsultancy/verdify-platform/issues/174) Admin auth re-home | Valid | S4 | The global auth re-home acceptance is not complete in current source/runtime. Execute after core launch and immutable Lab work. |
| [#207](https://github.com/VerdifyConsultancy/verdify-platform/issues/207) Platform drift SOTU | Close | — | The broad 2025 drift inventory is decomposed into current, evidence-specific issues in this report; live core services are healthy. Close as superseded, not as proof that all child work is done. |
| [#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) Dynamic crop planning | Valid | S3 | `planner_graph_runs` has no production rows and trigger failures/timeouts remain. Prove dynamic planning end-to-end after S0 delivery is trustworthy. |
| [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218) DB HA/PITR epic | Valid | S2 | Production is a single TimescaleDB StatefulSet; backups exist, but no standby/WAL/PITR end state exists. Retain as durability umbrella. |
| [#220](https://github.com/VerdifyConsultancy/verdify-platform/issues/220) Dev-stage-prod promotion | Close | — | The issue's retired workflow/equality architecture no longer exists. Current immutable central GitOps delivery is tracked by #644. |
| [#225](https://github.com/VerdifyConsultancy/verdify-platform/issues/225) HA epic | Valid | S2 | Database, writer, and persistent-data failure modes remain. Re-baseline the epic on current Longhorn/NAS and external network ownership. |
| [#235](https://github.com/VerdifyConsultancy/verdify-platform/issues/235) Pi-hole HA | Close | — | This is owned by network-infra issue #252, not Verdify; the old records/replica design is no longer authoritative here. Close as transferred/superseded. |
| [#238](https://github.com/VerdifyConsultancy/verdify-platform/issues/238) Traefik/MetalLB HA | Close | — | `traefik-apps` now runs four DaemonSet pods with disruption controls; a single MetalLB controller is the intended controller model. Remaining ownership is external to this repo. |
| [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) Atomic CNPG cutover | Stale / valid | S2 | The live DB is now on Longhorn, not the issue's Synology-iSCSI design, and no CNPG cluster exists. Redesign the gated cutover after #396 is rewritten. |
| [#287](https://github.com/VerdifyConsultancy/verdify-platform/issues/287) Control optimization epic | Stale / valid | S3 | Several June requirements were implemented or superseded, while control and irrigation residuals remain. Refresh child scope/counts and retain the umbrella. |
| [#288](https://github.com/VerdifyConsultancy/verdify-platform/issues/288) Deploy enablement epic | Stale / valid | S1 | Agent k3s/DB access exists, but real ESPHome tooling/compile, immutable delivery, OTA proof, and secret finalization remain. Rewrite to the residuals. |
| [#291](https://github.com/VerdifyConsultancy/verdify-platform/issues/291) Dispatcher solar re-anchor | Close | — | Firmware v2 replaced dusk/night/fog clock rails with solar phase, and the dispatcher has a tested seasonal grow-light anchor. Close as superseded by the safer current design. |
| [#293](https://github.com/VerdifyConsultancy/verdify-platform/issues/293) DB solar-phase parity | Close | — | Migration 186 is applied; all March/June/September/December fixtures match source exactly; `mv_band_curve` is populated and refreshes are current. |
| [#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) Soil-feedback sub-FSM | Valid | S3 | No commissioned center/south feedback loop exists. Build the slow, overwatering-safe FSM only after topology/probe truth. |
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) Saturation alert and drip skip | Valid | S3 | Production has no proven saturation-to-skip closed loop. Implement dispatcher-side first with bounded evidence. |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) Probe/topology re-baseline | Stale / valid | gated | Live telemetry and `soil_moisture_targets` disagree with the old plant/pot narrative. Re-inventory physical probes and update declarative topology before control changes. |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) Center mister re-fire | Valid | S3 | Protection exists in code, but no current topology-recovery proof closes the acceptance. Add a deterministic test and realized receipt. |
| [#300](https://github.com/VerdifyConsultancy/verdify-platform/issues/300) Firmware registry/doc drift | Stale / valid | S3 | Some dusk/night concepts were removed, but retired burst/readback/comment contracts remain. Narrow to the registry rows and documentation still present. |
| [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) Firmware compile CI | Stale / valid | S1 | Central CI now runs native firmware logic tests, but not a real ESPHome compile. Replace obsolete workflow language with the current Agent Fleet build contract. |
| [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) Agent tooling image | Valid | S1 | The live repo pod has kubectl, psql, gh, jq, and kustomize but no `esphome`. Add/pin the tool and prove a compile in-pod. |
| [#317](https://github.com/VerdifyConsultancy/verdify-platform/issues/317) Argo selective-scope rewrite | Stale / valid | S1 | No active sync operation exists, while the app is broadly OutOfSync. Reproduce with a controlled dry-run/sync receipt before changing Argo behavior. |
| [#318](https://github.com/VerdifyConsultancy/verdify-platform/issues/318) Grafana ConfigMap sync | Close | — | Oversized dashboard ConfigMaps use `Replace=true`, smaller ones use server-side apply, and all four live resources report Synced. |
| [#319](https://github.com/VerdifyConsultancy/verdify-platform/issues/319) Promote rebuild/equality race | Close | — | The rebuild-on-publish/dev-equality workflow was retired. Current immutable image and branch protection gaps are tracked by #644. |
| [#321](https://github.com/VerdifyConsultancy/verdify-platform/issues/321) Retired overlay/branch/app cleanup | Valid | S1 | `origin/live/platform-main` and the `verdify-prod-dark` app name remain; only part of the requested cleanup is complete. Finish declaratively. |
| [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) Destroyed VM tests | Valid | S1 | VM-era Docker/systemd assertions remain in the suite. Replace them with k3s-era contract and runtime tests. |
| [#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) Per-zone deterministic bands | Valid | S3 | Current setpoint schema/readback does not satisfy the full zone/role/target contract. Migrate with compatibility tests. |
| [#326](https://github.com/VerdifyConsultancy/verdify-platform/issues/326) Retire consolidated wet-stress params | Stale / valid | S3 | Some parameters are gone, but `direct_wet_stress_latest_hour` and fog-window registry entries remain. Limit work to surviving consumers and policy rows. |
| [#335](https://github.com/VerdifyConsultancy/verdify-platform/issues/335) CI/CD hardening epic | Valid | S1 | No required status checks are configured on `main`, and current image/runtime convergence is not immutable. Retain as #644's umbrella. |
| [#336](https://github.com/VerdifyConsultancy/verdify-platform/issues/336) Argo/GitOps cleanup epic | Stale / valid | S1 | Stage is synced and current, but prod is manually OutOfSync and retired naming/branch state remains. Refresh the epic around today's topology. |
| [#337](https://github.com/VerdifyConsultancy/verdify-platform/issues/337) Decommission/auth epic | Valid | S4 | Auth re-home and residual product-plane cleanup remain. Retain, with obsolete multi-environment children removed. |
| [#347](https://github.com/VerdifyConsultancy/verdify-platform/issues/347) Data/schema source-of-truth epic | Valid | S3 | Alert lifecycle, setpoint schema, topology targets, and planning data truth still have open children. Refresh child links while retaining the epic. |
| [#348](https://github.com/VerdifyConsultancy/verdify-platform/issues/348) Observability/KPI epic | Valid | S1 | Fresh core health does not cover replay, writer absence, rule loading, or realized outcome grading. Retain as S1 umbrella. |
| [#349](https://github.com/VerdifyConsultancy/verdify-platform/issues/349) Lighting/occupancy epic | Valid | S3 | Minimum-on boundary proof and deterministic lighting acceptance remain. Retain and sequence after runtime truth. |
| [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350) Irrigation/fertilization epic | Valid | S3 | Missing/bad probes and stale crop targets prevent closed-loop acceptance. Retain as the software-plus-hardware umbrella. |
| [#351](https://github.com/VerdifyConsultancy/verdify-platform/issues/351) Lab/publishing epic | Stale / valid | S4 | Astro is publicly cut over, but it serves a provisional build with no immutable occurrence. Rewrite the epic around immutable publication and reporting isolation. |
| [#352](https://github.com/VerdifyConsultancy/verdify-platform/issues/352) Testing/research epic | Valid | S4 | Experiment, twin, replay, and outcome evidence remains absent. Retain as the research/validation umbrella. |
| [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359) Floating-corridor control epic | Valid | S3 | Safe corridor rails are active, but edge action, outcome grading, and follow-on width/forecast decisions remain. Retain as the control umbrella. |
| [#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) Reassess diurnal anchors | Valid | S3 | Solar parity is complete, but float outcome evidence is not. Hold anchor changes until the required evidence window exists. |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) One anti-chatter mechanism | Valid | S3 | Redundant dwell/anti-chatter paths remain in the control surface. Choose one mechanism and prove boundary behavior. |
| [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) Sensor input integrity | Valid | S3 | Current telemetry includes a south probe stuck at zero; jump/flatline/stuck-in-range rejection is not complete. Implement shared integrity semantics. |
| [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) Dead code/tunable purge | Valid | S3 | Surviving obsolete registry/policy entries show the purge is incomplete. Remove only after consumer and readback searches are clean. |
| [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) Pure equipment resolver | Valid | S3 | Equipment anti-fight/mode resolution is not yet reduced to the requested pure function. Refactor with exhaustive transition tests. |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) Outcome grading/homepage | Valid | S1 | Production has no experiment outcomes and incomplete realized-control grading. Define availability-gated metrics before rendering them. |
| [#377](https://github.com/VerdifyConsultancy/verdify-platform/issues/377) Retire 0.25 band fraction | Close | — | Current source pins zero; the active plan and direct device readback both report `band_track_fraction=0`, with no stale 0.25 intent observed. |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) Decide corridor widths | Valid | S3 | There is not yet enough realized float outcome data for a defensible decision. Preserve current widths and define the evidence window. |
| [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) Grey-box/MPC outer loop | Valid | S3 | No accepted actuator model or anticipatory-control proof exists. Keep behind input integrity, outcome grading, and stable corridor control. |
| [#381](https://github.com/VerdifyConsultancy/verdify-platform/issues/381) k3s-agent autonomy | Stale / valid | S1 | The repo pod now has cluster/DB/GitHub tools, but lacks ESPHome and complete OTA/runtime workflows. Rewrite from “laptop blockers” to the residual autonomous paths. |
| [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) Ingestor restart storage wedge | Stale / valid | S2 | The old iSCSI target-cap cause no longer applies: the ingestor uses `emptyDir`. Re-scope to ephemeral queue/durability and restart-loss behavior. |
| [#383](https://github.com/VerdifyConsultancy/verdify-platform/issues/383) Solar-night dehumidification | Valid | S3 | The safety path exists, but a current realized dry-out evidence gate is not closed. Preserve it and collect bounded night evidence before tuning. |
| [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) Grow-light minimum-on | Valid | S1 | Logic/tests do not yet prove the current device across the solar-window boundary. Add compile/replay proof before OTA. |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) OTA cycling/runtime gate | Valid | S1 | Native logic checks exist, but there is no real ESPHome compile plus topology-aware cycling/soak gate for every firmware target. Build the gate. |
| [#394](https://github.com/VerdifyConsultancy/verdify-platform/issues/394) Writer-absent/split-brain alert | Stale / valid | S1 | A writer-watchdog CronJob now records alerts/events, but the requested externally loaded Prometheus rules and delivery receipt are absent. Re-scope to that residual. |
| [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) Author production CNPG | Stale / valid | S2 | No CNPG cluster exists and the issue assumes retired Synology-iSCSI placement. Redesign for current Longhorn/NAS failure domains, then deploy alongside. |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) Soil-target data truth | Stale / valid | S3 | Live targets still say Canna Lily/Canna Lily/Unknown while current probe health/topology has changed. Re-inventory, then update seed rows and production truth together. |
| [#399](https://github.com/VerdifyConsultancy/verdify-platform/issues/399) Single-writer safety contract | Valid | S1 | `SERVICE_MAP` and setpoint-server mappings do not provide the requested explicit refusal assertion for an unexpected grow-light writer. Add a negative test and owner row. |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) Solar-night dry-out evidence | Valid | S3 | No accepted realized-evidence receipt closes the night response. Collect bounded telemetry without relaxing safety rails. |
| [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412) Seasonal door screen | Valid | gated | This is an operator/seasonal physical gate. Execute only when outdoor nights cross the specified floor and record before/after evidence. |
| [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) Replay outdoor age | Valid | S1 | The checked replay corpus has zero populated `outdoor_data_age_s` values. Capture post-OTA device telemetry and exercise every outdoor-aware estimator path. |
| [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) Device/DB VPD anchor divergence | Valid | S0 | The accepted-risk classification does not remove the divergence. Decide/codify authority and prove the #410 bake floor before launch. |
| [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) Hermes/MCP planner delivery | Stale / valid | S0 | Routine health is currently good, but the trigger ledger still contains failures/timeouts and no controlled recovery receipt closes the broad acceptance. Narrow and prove it. |
| [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) ESP32 heap exhaustion | Valid | S3 | No accepted post-OTA heap soak or controlled-restart safety proof exists. Reduce heap use through #430 before activation. |
| [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430) Firmware simplification epic | Valid | S3 | The on-chip surface remains larger than the irreducible control floor. Treat this as the structural fix for #428, with behavior parity tests. |
| [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) Truthful device writes | Valid | S0 | A prior proof failed closed and the full writer acceptance has not passed. Stop stable-connection reconciles and verify acknowledged, idempotent device truth. |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) Center-only mist/wall-only fertigation | Valid | S3 | The hard topology safety contract is not proven end-to-end. Encode explicit allowlists/refusals and test the commissioned topology. |
| [#475](https://github.com/VerdifyConsultancy/verdify-platform/issues/475) Lab search/CSP/camera/contact | Valid | S4 | Public Astro is live, but the requested product/security surface is not fully accepted. Finish against the immutable occurrence runtime. |
| [#476](https://github.com/VerdifyConsultancy/verdify-platform/issues/476) Secure occurrence fallbacks | Valid | S4 | Public Lab is provisional and fallbacks are not backed by an immutable selected occurrence. Make graph/camera failure fail closed without leakage. |
| [#479](https://github.com/VerdifyConsultancy/verdify-platform/issues/479) Same-snapshot semantic parity | Valid | S4 | Both sites serve the same provisional metadata, not an approved immutable occurrence. Prove all content/search/media from one occurrence identity. |
| [#480](https://github.com/VerdifyConsultancy/verdify-platform/issues/480) Immutable S3 publishing/runtime | Valid | S4 | No materialized occurrence blobs or selected occurrence exist. Build immutable publish, verification, selection, and release-runtime flow. |
| [#482](https://github.com/VerdifyConsultancy/verdify-platform/issues/482) Astro cutover/Quartz retirement | Stale / valid | S4 | Astro production cutover happened, but immutable occurrence acceptance and final Quartz/publisher retirement did not. Rewrite to the remaining cutover exit criteria. |
| [#533](https://github.com/VerdifyConsultancy/verdify-platform/issues/533) Stage occurrence store/credentials | Stale / valid | S4 | Scoped reader/writer Secrets exist, but store isolation and end-to-end occurrence proof are incomplete. Re-scope around the provisioned objects and missing acceptance. |
| [#534](https://github.com/VerdifyConsultancy/verdify-platform/issues/534) Reporting tier/read-only feed | Valid | S4 | No dedicated reporting reader/runtime was found. Provision isolation and prove it cannot mutate the occurrence store. |
| [#535](https://github.com/VerdifyConsultancy/verdify-platform/issues/535) 143+2 producer/reporting GitOps | Stale / valid | S4 | The provisional build enumerates 143 graphs and two cameras, but materializes zero occurrence blobs. Rewrite from enumeration to executable immutable production. |
| [#536](https://github.com/VerdifyConsultancy/verdify-platform/issues/536) Public-output/media contracts | Valid | S4 | The final immutable public-output and media acceptance remains open. Validate only against a selected occurrence. |
| [#541](https://github.com/VerdifyConsultancy/verdify-platform/issues/541) Two-pass stage activation/soak | Valid | S4 | Stage is healthy but serves provisional content. Run activation only after 143+2 immutable convergence and enforce the freshness soak. |
| [#542](https://github.com/VerdifyConsultancy/verdify-platform/issues/542) Packed publisher/dormant runtime | Valid | S4 | The release runtime object is dormant at zero replicas and no immutable pack is selected. Complete publisher, then activate two pods through GitOps. |
| [#557](https://github.com/VerdifyConsultancy/verdify-platform/issues/557) Portable Lab cache | Stale / valid | S2 | The cache is now a portable Longhorn PVC with co-location affinity, but full rollout/failure acceptance and old PR closure are incomplete. Narrow to those residuals. |
| [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) Alert-delivery contract | Valid | S1 | The repository lacks the requested executable negative contract banning inert `PrometheusRule` delivery. Document the ConfigMap owner path and test it. |
| [#570](https://github.com/VerdifyConsultancy/verdify-platform/issues/570) Approval-eligible Lab release | Valid | S4 | Current metadata says `approvalEligible=false`, `selected_occurrence=null`, and `provisional-only`. Produce and select a verified immutable release. |
| [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581) Fast experiment epic | Valid | S0 | Experiment services are Ready, but all launch evidence tables are empty and faults are present. Retain as the S0 launch program umbrella. |
| [#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) Platform-v2 policy engine | Stale / valid | gated | Policy-engine source exists, but current ADRs defer platform-v2 behind the fast experiment. Rewrite as a deferred recovery/OTA program, not a launch blocker. |
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) Experiment kill switch/lifecycle | Valid | S0 | Lifecycle workloads exist, but the feature is disabled and there is no approval/outcome receipt. Prove kill, freeze, and blinded integrity behavior during activation. |
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) Selector/randomization | Valid | S0 | No randomization or assignment rows exist. Freeze identity and produce a power-justified, deterministic blinded draw. |
| [#606](https://github.com/VerdifyConsultancy/verdify-platform/issues/606) Twin build profile | Stale / valid | S1 | `.agent-fleet/ci.yaml` still omits `verdify-twin`, but current ADRs say it is not a fast-launch blocker. Add the profile as enablement work and remove blocker language. |
| [#611](https://github.com/VerdifyConsultancy/verdify-platform/issues/611) Pre-portable Hermes PVC | Close | — | The exact PVC, PV, and Longhorn volume are absent; portable Hermes is mounted; storage-infra #289 records intentional deletion and source prevents recreation. |
| [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) Platform-v2 content identity | Stale / valid | gated | The unified manifest/vector transport is deferred by the current experiment architecture. Preserve the design issue but remove fast-launch implications. |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) Daily executor/device truth | Valid | S0 | There are 111 experiment work rows but zero runtime snapshots/proof receipts. Run one verified daily delivery and derive device truth. |
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) Outcomes/export/reveal | Valid | S0 | Outcome, export, approval, and reveal tables contain no completed evidence. Implement immutable daily outcomes and the one-way reveal gate. |
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) Physical start gate | Valid | S0 | No approval record or physical-start receipt exists. Keep Jason-gated and bind approval to one explicit window. |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) Randomized launch controller | Valid | S0 | No assignment/approval/proof/outcome chain exists; open activation work has not landed successfully. Activate only after all S0 receipts pass. |
| [#643](https://github.com/VerdifyConsultancy/verdify-platform/issues/643) Experiment DB role split | Stale / valid | S2 | Workload roles remain insufficiently proven, while platform-v2 is deferred. Re-scope to least privilege for the actual fast-experiment services first. |
| [#644](https://github.com/VerdifyConsultancy/verdify-platform/issues/644) CI/CD get-well | Valid | S1 | `main` requires only linear history, no status checks; production runs an older image and is OutOfSync. Require immutable build/test/pin/promotion evidence. |
| [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) Backup globals/roles | Valid | S2 | The backup CronJob runs only `pg_dump -Fc`; restore compensates by seeding roles. Add `pg_dumpall --globals-only`, protect it, and rehearse restore. |
| [#671](https://github.com/VerdifyConsultancy/verdify-platform/issues/671) Publisher errors/stale locks | Valid | S1 | Recent publisher Jobs recovered, but upload errors can still be swallowed and lock expiry is weak. Make failure visible and enforce a bounded TTL. |
| [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) Restore compressed-chunk owners | Valid | S2 | Restore Jobs complete, but migration 217 ownership remains explicitly advisory for hostile compressed chunks. Make the ownership proof mandatory or document a safe invariant. |
| [#676](https://github.com/VerdifyConsultancy/verdify-platform/issues/676) Hermes OpenAI rollback | Valid | S0 | Runtime cycles exist, but the final protected credential/SOPS metadata and rollback receipt are not complete. Finish declaratively without exposing secret material. |
| [#686](https://github.com/VerdifyConsultancy/verdify-platform/issues/686) Public MCP authentication | Stale / valid | S0 | Six unauthenticated initialize attempts now return 401 and current source is stateless/auth-enforcing, but live image convergence and an authenticated Hermes tool receipt remain unproven. Rewrite from active exploit to convergence/acceptance gate. |

## Closure receipts

The 11 completed closures divide into completed end states and architectural supersessions:

- Completed with live proof: #293, #318, #377, and #611.
- Superseded by the current architecture or transferred owner: #114, #207, #220, #235, #238, #291, and #319.

Closing a superseded umbrella does not close the residual work named in its comment. The comment must point at the current issue/report that owns that work.

## Operating sequence and dependencies

1. **Converge and secure S0.** Reconcile the desired MCP/Hermes version through GitOps, prove authenticated planner/tool delivery, finish truthful device writes, then run selector, approval, executor, outcome, and reveal gates as one controlled chain.
2. **Make S1 the delivery gate.** Require CI checks and immutable image pins, ship actual ESPHome tooling/compiles, prove Argo scope, replace VM-era tests, and make alert/replay/runtime assertions executable.
3. **Fix S2 recovery before expanding state.** Add global-role backup, close compressed-chunk ownership, choose a current Longhorn-aware CNPG design, and prove writer/DB recovery before risky cutover or immutable publication growth.
4. **Execute S3 control simplification.** Address heap and sensor integrity first, then anti-chatter/registry/schema cleanup, then realized band/lighting evidence, and only then irrigation closed loops.
5. **Complete S4 immutable Lab.** Provision isolated occurrence/reporting identities, publish and verify 143+2 assets, soak stage, approve/select one occurrence, and finish public/auth surfaces.
6. **Pull gated work only on a real gate.** Physical probes, calibration, seasonal changes, twins, and platform-v2 should not displace the active sequence without the required hardware event or explicit program decision.

## Definition of done for this backlog

An issue closes only when declarative source, CI evidence, live runtime state, and any required data/volume receipt agree. A merged PR or a healthy pod alone is not sufficient. Hardware-gated items additionally require a timestamped physical/operator receipt; security and secret work records only metadata and successful behavior, never raw secret values.
