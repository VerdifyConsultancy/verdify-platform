# Verdify Unified Backlog — Full Replan (2026-06-09)

Authoritative replan of the entire Verdify board, executed live against `VerdifyConsultancy/verdify-platform` Issues + Org Project "Iris / Verdify" #1. Supersedes the prior board maps (#135 Iris Charter, #222 post-cutover roll-up, #134 Product Plane, #249 firmware-optimization). Companion docs: `docs/voice-memo-triage-2026-06-09.md` (verified control diagnoses).

**Method:** every one of the 62 then-open issues was independently classified (keep / relabel / re-milestone / close-done / close-superseded / merge); the firmware-optimization program + prior replans were superseded & unified into three new epics; all diagnoses were re-derived by adversarial code-walk, not taken on faith.

## Outcome

- **3 new epics:** #286 (unified roll-up), #287 (Greenhouse Control Optimization, req A–E), #288 (Deploy Enablement).
- **18 new child issues** (#289–#306) carrying the verified root-cause + fix + acceptance for each requirement.
- **22 issues closed** (5 done, 16 superseded, 1 merged) — each with a pointer comment to its new home.
- **7 issues relabeled / re-milestoned**; the rest kept.
- **2 new milestones**, **~60 new labels** (req:A–E, wave:W0–W2, workstream:*, epic:*).
- All new items wired into Org Project #1.

## Workstreams

| Workstream | Owns | Label |
|---|---|---|
| firmware | ESP32 C++/ESPHome, OTA, replay | `area:firmware` |
| ingestor | dispatcher, HA/Tempest sync, alerts | `workstream:ingestor` |
| genai | planner, MCP, prompts, scorecard | `workstream:genai` |
| web | api, Quartz site, lab.verdify.ai | `workstream:web` |
| saas | future product plane, auth | `workstream:saas` |
| coordinator-infra | schemas, migrations, CI, k3s/GitOps | `workstream:coordinator-infra` |
| **deploy-enablement (NEW)** | agent k3s/DB access, firmware CI/OTA, secret sealing | `workstream:deploy-enablement` |

## Milestone map

| Milestone | Purpose |
|---|---|
| Enablement: Three-Env (dev/stage parity) | Reuse existing. The dev/stage/prod k3s cutover spine (epics #111/#112): MQTT fan-out bus, read-only subscribe ingest, GitOps promotion, platform-drift closeout (#207/#200). Holds the Track-B infra cutover work. |
| Enablement: Compliance & Twins | Reuse existing. Migration-147 reward/compliance rearchitecture (#13/#17/#20) and the firmware digital-twin program (#14/#31, with #32/#33/#34 landed). Trust-the-twin gate (#31) lives here. |
| Enablement: Data Hygiene & Observability | Reuse existing. Observability substrate (#75/#89), data-durability/PITR (#218), cutover-regression bugfixes (#210/#214/#215), data-hygiene one-offs (#38/#43/#49/#219), and the F1 irrig boot-clamp default fix (#37). |
| Enablement: Decommission & Auth | Reuse existing. Final-stage irreversible decom + auth-rehome (#91/#104/#118/#174/#175/#177). |
| Hardware / Seasonal (operator-gated) | Reuse existing. Everything blocked on a physical install or a seasonal window: hardware epic #16, OTA promotion #35, probe/sensor installs #45/#51, seasonal dormancy #52. |
| M7 — HA: first-principles resilience | Reuse existing. The HA program from the 2026-06-07 node7-overload incident (#225 umbrella + HA-1.7/2.x/3.x/4.x children). Separate workstream, NOT folded into the firmware unification. |
| Greenhouse Control Optimization *(new)* | NEW. The unified firmware-optimization program (req A-E from the 2026-06-09 voice memo): button-override precedence, dusk-cutoff removal / solar-aligned overnight VPD, two-zone lighting split, forecast feed-forward + Equation-of-Time, soil-feedback irrigation. Supersedes the #249 milestone family (Firmware control fixes / Climate band coherence / Two-zone lighting / Irrigation feedback loop). All children reference docs/voice-memo-triage-2026-06-09.md. |
| Deploy Enablement (agent access + firmware CI/OTA) *(new)* | NEW. The sixth, previously-unticketed workstream: agent-pod read access to live DB, firmware compile/OTA capability for agent pods, OTA password sealing into k3s, CI firmware-compile lane, and the safe SHADOW iteration loop (absorbs #221). Unblocks every gated firmware change in the Control Optimization milestone. |

## New epics & children

### #286 — EPIC: Iris / Verdify Unified Backlog (2026-06-09 full replan)


### #287 — EPIC: Greenhouse Control Optimization (req A-E, 2026-06-09 voice memo)

- #289 [req:A][W0] FANS/HUMID button bug + absolute-priority button-override state machine
- #290 [req:A/R3] Vent-bypass winter mode: fans ON + vent CLOSED (house-air pull) — design + FSM carve-out
- #291 [req:B][W0] Dispatcher solar re-anchor push: dusk/night/fog-window + dawn anchor (no OTA)
- #292 [req:B][W0] Remove the firmware dusk dry-cutoff; smooth 24h VPD band governs overnight
- #293 [req:B/E][W1] Forecast feed-forward + Equation-of-Time on the served band
- #294 [req:C][W1] Per-circuit lighting crop-policy: MAIN=orchid photoperiod / GROW=hydroponic (schema/DB)
- #295 [req:C][W1] Two-zone lighting firmware: solar-phasing + lux_on_threshold realism + west grow-light dead-fixture detection
- #296 [req:D][W2] Soil-feedback irrigation sub-FSM (slow hysteresis; over-watering is the failure mode)
- #297 [req:D][W2] Saturation/overwatering alert + dispatcher drip-skip first closed loop (no OTA)
- #298 [req:D][W2] Probe/topology re-baseline: lime tree, cannabis, hydro pot, homeless SEN0600
- #299 Actuator-wear hardening: misters into dwell-protected array + per-relay max-cycles/hr governor
- #300 Firmware registry/doc-drift cleanup: burst tunables, dusk/night rows + cfg readbacks, vent-bypass clamp, OVERRIDE_EVENT_TYPES, stale comments

### #288 — EPIC: Deploy Enablement — agent k3s/DB access + firmware CI/OTA + secret sealing

- #301 [deploy-enablement] Seal ESP32 OTA password into k3s (SealedSecret) + re-home firmware-deploy preflight
- #302 [deploy-enablement] Live-DB read access for the agent pod (DSN secret + NetworkPolicy, OR RBAC exec)
- #303 [deploy-enablement] Firmware-compile CI lane + portable esphome compile paths
- #304 [deploy-enablement] Agent-pod tooling image: esphome + kubectl + psql client
- #305 [deploy-enablement] Admin-token least-privilege review for agent/CI access
- #306 [deploy-enablement] Safe SHADOW iteration loop: verdify-dev ns, device-write=0, deny-esp32-egress

## Per-issue triage (all 62)

| # | Title | Action | Workstream | Pri | Reason |
|---|---|---|---|---|---|
| #13 | EPIC: Band + Compliance Rearchitecture — finish migration 147 reward s | keep | genai | P1 | Valid in-flight epic, NOT part of the firmware-optimization program or the superseded #135/#222 replans. Migration 147 is still STAGED (roll |
| #14 | EPIC: Firmware digital twins (TWIN-1..17) | keep | firmware | P1 | Valid umbrella epic. The Phase-0 harness, migration 155, and the Phase-1 shadow are landed/deployed (commit 87f5610, #255), but the epic sta |
| #16 | EPIC: Hardware backlog (operator-gated sensing/equipment installs) | keep | cross-cutting | P3 | Valid operator-gated hardware epic (center VPD probe, fert mister, night-humidity source, etc.). Every child blocks on a physical install, s |
| #17 | Re-baseline the 147 anchor-stability gate (live fn_plan_anchor_score r | keep | genai | P1 | Valid gating analysis for the 147 apply (#20); 147 is still staged so this re-baseline is unstarted. Distinct deliverable from #20 (analysis |
| #20 | Apply migration 147: re-point planner reward to compliance_v2_attribut | keep | genai | P1 | Valid; 147 is staged but not applied (fn_plan_anchor_score still SELECTs binary compliance_pct; v_daily_kpi has 0 refs to the v2 column). Se |
| #31 | TWIN-3 (P0): close the setpoint-coverage gap so the twin divergence me | relabel | firmware | P1 | The state:MERGED label is wrong/premature: the twin-shadow deploy (commit 87f5610) explicitly states replay_emit_follow still warns 48 sp_*  |
| #32 | TWIN-1/TWIN-2 (Phase 0): gated --stream mode + climate_action column o | close-done | firmware | P1 | Done. firmware/test/replay_emit.cpp now carries the gated --stream / climate_action additions (18 matching hits, REPLAY_EMIT_STREAM build, T |
| #33 | TWIN-6: migration for twin_decisions + firmware_twin_divergence hypert | close-done | coordinator-infra | P1 | Done. Migration db/migrations/155-twin-observability-tables.sql exists and its header explicitly cites 'TWIN-6 (issue #33)' — creates twin_d |
| #34 | Digital twin MVP (Phase 1): prod twin shadowing last-good + prod-vs-re | close-done | firmware | P1 | Done. The twin MVP is built (docker-compose.twins.yml, twin/Dockerfile, offline_driver.py, migration 155, divergence dashboard) and deployed |
| #35 | Promote last-good OTA to 2026.5.30.1418.aa6518c after its 48h behavior | keep | firmware | P1 | Valid; last-good rollback floor is still the stale 2026-05-17 binary per the 2026-05-31 state doc, and the artifacts dir is local/gitignored |
| #37 | F1: change firmware default irrig_center_start_hour 10 -> 6 (shared-te | keep | firmware | P2 | Valid; globals.yaml:1113 irrig_center_start_hour initial_value is still '10'. This is a discrete boot-clamp default-fallback fix (orthogonal |
| #38 | Prune active planner_lessons 57 -> <=25 (fixes the failing lessons-cou | close-done | genai | P2 | Done. Migration db/migrations/156-prune-active-planner-lessons.sql exists and its header cites issue #38 — canonicalizes active lessons down |
| #43 | Schedule periodic refresh of the site_content RAG table (7 days stale, | keep | web | P2 | Valid and unaddressed; the 2026-05-31 state doc confirms site_content remained stale (8 days) with no timer/cron/Makefile ref for populate-s |
| #45 | Run irrigation-feedback bring-up after south-1 probe repair + center f | keep | ingestor | P2 | Valid hardware-enablement companion to the new unified irrigation epic (#285/#277). This is the operator probe-repair/install side that the  |
| #49 | Backfill resolved_at on the 61 historical suppressed orphan sensor_off | relabel | ingestor | P3 | Valid low-priority one-time data backfill of pre-existing suppressed rows already excluded from v_open_alerts. Ingestor alert-lifecycle terr |
| #51 | CO2 two-point field calibration against a reference NDIR (software tra | keep | firmware | P3 | Software CO2 correction shipped+verified; the residual is a reference-NDIR two-point field calibration of the co2_cal_* globals (firmware se |
| #52 | SEA1: orchid dormancy phase + dormant lighting clamp + dry-down govern | keep | genai | P3 | Seasonal dormancy crop-policy + dormant lighting clamp, explicitly deferred to fall/winter and gated on OPN-4 empirical observation. Genai c |
| #75 | EPIC: Observability & health (smoke, metrics, device-route monitor) | keep | coordinator-infra | P1 | Live, valid sub-epic for the k3s monitoring substrate (metrics, ServiceMonitor, Grafana/Loki, G10 smoke, device-route ESTAB alert). Coordina |
| #89 | G10 post-deploy smoke + device-route ESTAB monitor | relabel | coordinator-infra | P1 | Device-safety smoke gate + single-writer ESTAB monitor; spec already authored (PR #183) and load-bearing for the M4.4/M5.1 cutover proof. Va |
| #91 | Phase 6: mask + decommission iris VM (irreversible, Jason-gated) | keep | coordinator-infra | P2 | Valid M6 irreversible decommission step (mask ingestor+setpoint-server, retain verified-restorable snapshot before destroy). Correctly Jason |
| #104 | Commit the live verdify-vault working tree before VM decommission (64  | keep | web | P1 | Valid data-loss-prevention task: capture/commit the live vault working tree (generated lab.verdify.ai content + CSVs) before the VM is wiped |
| #111 | EPIC: Three-Env Platform (dev/stage/prod) | keep | coordinator-infra | P0 | Active, authoritative cutover epic (M0-M7 program tracked in the 2026-06-03/06-04 lane docs). Distinct from the firmware-opt program Jason s |
| #112 | EPIC: MQTT telemetry fan-out bus (prod publishes all sources; dev/stag | keep | coordinator-infra | P0 | Active infra epic for the prod-publish / dev+stage-subscribe MQTT bus; child of #111. Real, in-flight k3s work, not part of the superseded f |
| #114 | dev/stage ingestor: subscribe-from-prod-MQTT read-only ingest mode | keep | ingestor | P1 | Valid child of #112/#111: dev/stage ingestor read-only subscribe mode (no device writes, no HA), composes with SHADOW_MODE (#25). Ingestor c |
| #118 | Model verdify-setpoint-server (2nd device writer, grow-lights via HA)  | keep | coordinator-infra | P1 | Valid: the setpoint-server is a real second device-affecting writer (grow-lights via HA) and is a prod-only k3s modeling decision with singl |
| #134 | EPIC: Verdify Product Plane (compliance, twins, observability, www/lab | close-superseded | cross-cutting | P1 | Sprawling old-layout roll-up (folds #13/#14/#75/#16 and references the retired #135 charter) that overlaps the new unified decomposition. Pe |
| #135 | 📋 Iris Charter & Board Map (2026-06-01 replan) | close-superseded | cross-cutting | P1 | Explicitly named by Jason as a prior replan to supersede & unify. Its swim-lane/board-map function is replaced by the new committed master d |
| #174 | Verdify admin auth-rehome → global auth.vallery.net SSO (post .100-Aut | remilestone | saas | P2 | Valid live gap: analytics/logs/auth.verdify.ai still down after the .100 Authentik stop; only the auth gating is missing (backends up 302/20 |
| #175 | botauth.verdify.ai backend dead (.152:8788) — recover or retire (pre-e | keep | saas | P3 | Pre-existing dead backend, explicitly low priority and a recover-or-retire decision requiring Root/Nexus infra access. Not greenhouse-affect |
| #177 | DEFERRED: internet-friendly device channel for fleet scale-out (MQTT / | keep | saas | P3 | Jason-decided DEFER (2026-06-04): migrate as-is over ESPHome native API, no firmware change for cutover. Blocked until k3s cutover complete  |
| #200 | [AUDIT-2026-06][APP] Verdify drift, direct VIP, secret placeholders, a | merge-into | coordinator-infra | P1 | External 2026-06-k3s-gitops audit child for Verdify covering Argo drift, direct VIP retirement, secret placeholders, resource limits. Substa |
| #207 | [SOTU][Verdify] Close platform drift across DNS, auth, observability,  | relabel | coordinator-infra | P1 | SOTU-2026-06-07 Verdify lane umbrella to break platform-drift findings (Argo drift, Progressing apps, DNS, auth rehome, observability, VIP r |
| #210 | Ingestor in-pod MCP venv missing in k3s -> forecast_actions task dead  | remilestone | ingestor | P1 | Live post-cutover regression: k3s ingestor image lacks the in-pod MCP subprocess venv, so forecast_actions is dead and floods errors every ~ |
| #214 | [P1][bug] Crop-deviation dynamic planning non-functional post-cutover  | remilestone | genai | P1 | Deviation detection works but trigger->plan dies at gather_context (#211) yielding 0 off-schedule plans since cutover, and plan_context_fail |
| #215 | [P2][bug] Ingestor stale dead-host polls (.150:9100, immich .108 GPU) | keep | ingestor | P2 | Ingestor still polls decommissioned .150:9100 and immich .108 GPU (No route to host); repoint to in-cluster infra_cpu/gpu targets. Valid low |
| #218 | [EPIC][P1] DB HA + PITR (verdify-db backups, WAL, standby) | keep | coordinator-infra | P1 | Data-durability epic: verdify-db replicas=1, WAL off, backup CronJob never succeeded -> unbounded RPO. Valid P1 durability work. Owner:root  |
| #219 | [P1] Website/lab content pipeline (CI rebuild on vault change) | keep | web | P2 | lab.verdify.ai is a manual one-off rebuild today; content silently goes stale with no CI rebuild on vault edit. Valid web automation item (s |
| #220 | [P1] Prod-promotion automation (dev→stage→prod GitOps promotion) | keep | deploy-enablement | P1 | Formalize ArgoCD-driven dev->stage->prod image promotion off the manual-sync tier, gated and git-auditable. Core deploy/CI enablement; re-ho |
| #221 | [P1] Verdify firmware/dev agent + safe iteration loop (SHADOW) | close-superseded | deploy-enablement | P1 | Stand up a dev-agent + SHADOW iteration path (verdify-dev ns, device-write=0, deny-esp32-egress) for firmware/planner changes without touchi |
| #222 | [EPIC] Post-cutover state + organized board roll-up (2026-06-07) | close-superseded | coordinator-infra | P2 | Prior post-cutover roll-up/replan epic that Jason explicitly chose to supersede & unify. Its outcome-based milestones and enablement groupin |
| #225 | [EPIC] HA — first-principles resilience | keep | coordinator-infra | P0 | Valid, in-flight HA program from the 2026-06-07 node7-overload incident — a SEPARATE workstream from the firmware-optimization program Jason |
| #232 | HA-1.7 — Rebalance node7 (spread + soft anti-affinity + CSI DaemonSet  | keep | coordinator-infra | P1 | The HA-1.5 hard hostname spread already landed (ha-stateless-spread.patch.yaml) but the gated node7 hardware resize (8c->12c/24GB or batch-o |
| #235 | HA-2.1 — pihole DNS SPOF: priorityClass+requests (A) → records-as-code | keep | coordinator-infra | P0 | Highest-priority edge DNS SPOF; nexus-owned and lives in the network-infra repo (not verdify-platform), so no landing evidence here and not  |
| #237 | HA-2.3 — .7.10/.7.53 BGP-only (drop dual L2 advert) + UDM timer tune → | keep | coordinator-infra | P1 | Network-edge (BGP/UDM) change owned by nexus, un-IaC'd UDM seam requiring net-baseline snapshot first. Not in verdify-platform scope, not su |
| #238 | HA-2.4 — traefik-apps PDB/resources/priority + metallb-controller 1→2  | keep | coordinator-infra | P1 | Cluster-edge HA hardening (traefik-apps PDB/priority, metallb-controller 1->2, reject Deployment->DaemonSet) filed under network-infra/deplo |
| #239 | HA-3.1 — Ingestor fast unpinned failover: short tolerations + verdify- | keep | ingestor | P0 | Partially landed: ingestor PDB (maxUnavailable:1) and RBAC shipped (859f3a4), but the risky tolerationSeconds:20 + verdify-device-critical p |
| #240 | HA-3.2 — Ingestor exactly-one FENCE: coordination.k8s.io Lease + renew | keep | ingestor | P0 | Lease-fence code landed (esp32_push.py push-gate + shared.writer_lease_held + RBAC) but is in pre-arm/inert mode by design (writer_lease_hel |
| #242 | HA-3.4 — Singleton-writer chaos acceptance run (hard-kill + partition  | keep | ingestor | P0 | The combined Sprint-3 life-safety acceptance run; blocked until #239 tolerations and #240 fence are both armed. A1 never-two is the hard pas |
| #243 | HA-4.1 — Build/validate TimescaleDB-on-CNPG 2.25.2-pg16 image in verdi | close-done | coordinator-infra | P1 | Gate G0 delivered and DEV-PROVEN by commit 4fd8f96: TimescaleDB-on-CNPG 2.25.2-pg16 operand image (deploy/k8s/cnpg/image/Dockerfile), dev CN |
| #245 | HA-4.3 — Gated atomic live-DB cutover to CNPG (quiesce-writer → flip D | keep | coordinator-infra | P1 | Only the runbook landed (deploy/k8s/cnpg/docs/PROD-CNPG-MIGRATION-RUNBOOK.md, commit 4fd8f96 explicitly 'NOT a green light'). The riskiest l |
| #249 | [EPIC] Firmware optimization — orchid-TOD removal, VPD/temp band align | close-superseded | firmware | P2 | The firmware-optimization roll-up epic Jason explicitly chose to supersede & unify. Its scope (orchid-TOD, band alignment, band-adjustment v |
| #250 | FW-OPT-1: Re-author / rip out the orchid time-of-day band curve (crop_ | close-superseded | firmware | P1 | Mis-framed by verified findings: the smooth solar cos2 diurnal band (migration 145, fn_diurnal_interp) already exists and is HEALTHY — do no |
| #251 | FW-OPT-2: VPD + temperature band-alignment refactor (coherent shoulder | close-superseded | genai | P2 | Band re-authoring/re-grading work for coherent VPD/temp shoulders and feasible widths. The smooth cos2 diurnal band already exists and is he |
| #277 | [TRACE-3][DATA] Dataset characterization for control-optimization — co | close-superseded | ingestor | P2 | Read-only dataset characterization study. Pure reference findings about data-collection gaps (zero orchid root-zone feedback, dead south_2 p |
| #278 | [LENS-ML][DESIGN] ML/control-model opportunity map — what the greenhou | close-superseded | genai | P3 | Read-only ML/control-model readiness map; no model trained, design-only. Most model classes are flagged PREMATURE (irrigation feedback, ligh |
| #279 | [LENS-Solar] Forecast-driven, solar-aligned climate+lighting control ( | close-superseded | genai | P2 | Read-only solar/forecast design study. Confirms the cos2 curve already exists and is healthy (do not rebuild) and identifies the three real  |
| #280 | [LENS-mechanical] Actuator wear: misters bypass dwell table, fogger/mi | close-superseded | firmware | P2 | Read-only actuator-wear study. Actionable findings (misters not in the dwell-protected R[] array so they can chatter with no min-on/min-off  |
| #281 | FW-OPT-3 [W0]: FANS-button bug + absolute-priority button-override sta | close-superseded | firmware | P1 | The req-A button-override fix: fan press silently swallowed because controls.yaml:808 passes force_on=false (dwell blocks restart within ~90 |
| #282 | FW-OPT-4 [W0]: Remove the firmware dusk dry-cutoff; smooth 24h VPD ban | close-superseded | firmware | P1 | The req-B 'weird orchid dry cutoff' fix lives in firmware actuation (past_dusk_cutoff() + vpd_dry_override + the clock *_latest_hour/wet_cut |
| #283 | FW-OPT-5 [W1]: Two-zone lighting policy split — MAIN=orchid / GROW=hyd | close-superseded | firmware | P2 | The req-C lighting split: two LightingState machines already exist but fn_lighting_minutes_policy emits byte-identical policy to both. Needs |
| #284 | FW-OPT-6 [W1]: Forecast feed-forward + Equation-of-Time on the served  | close-superseded | genai | P2 | The req-B/E served-band enrichment: fn_band_setpoints reads clock-solar geometry only while the rich weather_forecast feed is alert-only, an |
| #285 | FW-OPT-7 [W2]: Soil-moisture/EC -> irrigation/fertigation feedback loo | close-superseded | firmware | P2 | The req-D irrigation feedback loop: irrigation today is pure calendar/day-mask with zero soil feedback (soil sensors telemetry-only, soil_mo |

## Deploy-enablement: capability matrix

| Capability | Status | Unlocks |
|---|---|---|
| live_db_read | blocked (upstream) | Either (Option A) a Role+cross-namespace RoleBinding granting agent-fleet-runners:default pods/exec on verdify-db-0 in ns verdify-prod PLUS kubectl+kubeconfig baked into the agent image; or (Option B, |
| firmware_compile | blocked (mixed) | scripts/firmware-esphome-worktree.sh hard-requires /srv/greenhouse/.venv/bin/esphome and a secrets.yaml; the pod has python3 but no esphome/platformio and the .150 paths are gone. Two unlock paths, bo |
| firmware_ota | blocked (upstream) | Needs all of: (1) the ESPHome ota_password (firmware/greenhouse.yaml:205-207, !secret ota_password) — NOT in any k3s Secret, device-affecting, Jason-gated; (2) operator confirmation of canonical value |
| ci_cd_drive | partial (mixed) | The repo-scoped GitHub token can push to live/platform-main (proven), which drives app build+publish + staging auto-deploy via the in-repo kustomize digest write-back, and can workflow-dispatch prod-p |
| application_deploy_verify | partial (mixed) | Staging (verdify-local-staging) auto-syncs from the in-repo digest write-back, so a push to live/platform-main self-serviceably deploys+verifies on staging. verdify-prod is ArgoCD manual-sync (it is t |

## Upstream asks (human/cluster-admin — cannot be self-served)

1. **Provide live-DB read access for the firmware-deploy alerts preflight and replay-corpus-refresh. Preferred least-privilege: create the verdify_ro Postgres role (from optionB-pg-ro.sql), seal a verdify-agent-db-ro DSN Secret into ns agent-fle**
   - who: cluster-admin for NetworkPolicies + RBAC; laptop-root for the SOPS+age seal (SECRETS.md delivery is needs:root); coordinator to serialize optionB-pg-ro.sql as a db/migrations change; operator Jason to
   - artifact: deploy/k8s/agent-access/{optionA-rolebinding.yaml, optionB-pg-ro.sql, optionB-db-dsn.secret.shape.md, optionB-networkpolicy.yaml, README.md} — authored in-repo so the grant is a one-shot reviewed apply.
   - residual (human-only): A human runs the SOPS+age seal (private age key is NAS+operator-only), kubectl-applies the NetworkPolicies/RBAC to verdify-prod and agent-fleet-runners, and runs the CREATE ROLE SQL against the live D

2. **Bake the pinned esphome+platformio+ESP-IDF toolchain (~1.5GB) into the agent-fleet firmware-runner image and point ESPHOME_BIN/SECRETS_SRC/FIRMWARE_PYTHON at a non-.150 location (provide a build-time secrets.yaml with wifi_*/api_encryption_**
   - who: fleet-platform owner (agent-fleet image build); operator Jason / cluster-admin laptop-root for the re-homed build host
   - artifact: firmware/build/ pinned-requirements file + a Dockerfile stanza fragment + a firmware-agent doc section naming the env knobs; plus a proposed ci.yml full-compile job. The agent-fleet image/podspec is upstream and NOT in this repo, so the sta
   - residual (human-only): Fleet-platform owner edits the upstream agent-fleet image (not in this repo) to install the toolchain and mount the build secrets; only they can rebuild/republish that image.

3. **Seal the ESPHome ota_password into a standalone k3s Secret verdify-firmware-ota (key ota_password) in ns verdify-prod via the same fleet SOPS+age local-k8s-secret-sync, after confirming the canonical value and carry-vs-rotate (rotation impl**
   - who: operator Jason (canonical value + carry-vs-rotate + no-reflash confirmation — hard device-affecting gate); laptop-root (seal-secret.sh pipe-only + protected sync runner); James/laptop-root (fleet regi
   - artifact: Already-authored docs/runbooks/firmware-ota-secret-sealing.md (full operator runbook) + deploy/k8s/overlays/prod/firmware-ota-secret.placeholder.yaml (lint-only shape the sealed Secret matches) + SECRETS.md row. firmware-rollback.sh already
   - residual (human-only): Jason confirms the value and gate; laptop-root runs the pipe-only seal + protected sync (private age key + protected runner are operator-only); the secret-meta lands in the upstream agent-fleet-contro

4. **Grant the agent pod device-VLAN egress to the ESP32 at 192.168.10.111:3232 (espota2) and provide a re-homed OTA tooling host with cross-VLAN reach, OR keep OTA an operator-host human action (current intended posture per k3s-cutover DoD #7).**
   - who: operator Jason (device firewall posture + freeze sign-off); cluster-admin/network owner (device-VLAN egress)
   - artifact: An uncomment-only egress NetworkPolicy stanza for the device subnet referencing the gated §3.4 placeholder in networkpolicy.yaml, plus the firmware-ota-secret-sealing.md re-home section (tooling-host option via existing env knobs; in-cluste
   - residual (human-only): Operator opens the device-VLAN egress and runs `make firmware-deploy` with OTA_PW fed from the sealed Secret from a host with :3232 reach. The agent will NEVER run OTA itself.

5. **Enable PR-only branch protection on the live branch so firmware changes always land via PR and the firmware-replay-diff + no-new-fire-and-forget gates (pull_request-only) actually run. This is what made e7781a3's direct-to-live fire-and-for**
   - who: repo admin / fleet-platform owner (operator Jason)
   - artifact: A docs/agents/firmware.md addendum codifying 'firmware changes land via PR into a firmware/* branch, never direct-to-live' + a branch-protection config note.
   - residual (human-only): Repo admin sets branch protection in GitHub settings (the repo token cannot set protection rules).

## Decisions gated on Jason

1. R4 fan-button incident timestamp → run the telemetry discriminator (#289).
2. R2 manual-fog supersede tier; whether fan-boost + fog-boost must co-exist (#289).
3. R3 winter vent-bypass: also suppress heat/fan interlock? (#290).
4. "Forecasted" sunrise/sunset = astronomical (have) vs cloud-adjusted (#293).
5. Dusk-cutoff: re-anchor+soften vs remove; approve staged migration 160 (#292).
6. PID stance: better rails (recommended) vs revisit continuous control (#287).
7. Lighting circuit→fixture mapping + cannabis photoperiod (#294/#295).
8. Second south probe re-home; cucumber-pot vs unpotted history (#298).
9. OK to set `sw_gl_grow_auto_mode=0` now (dead west circuit) (#295).
10. Seal OTA password into k3s — blocks all firmware OTA (#301).

## Open PRs to reconcile

- #101 Sprint 2 (secrets/psql/SHADOW); #125 SOTU doc; #203 hermes-iris CrashLoop fix; #208 CODEOWNERS baseline; #271 CNPG TimescaleDB (HA-6); #272 route-exposure metadata.

---
*Generated 2026-06-09 by the firmware agent (Claude) under operator authorization; executed live with an admin-scoped repo token. See the unified roll-up epic #286 for the living board.*

## Follow-up — 2026-06-09 PM

This section records post-replan actions taken on the afternoon of 2026-06-09, weaving in operator (Jason) input and the GitHub tracking work completed.

### Native GitHub sub-issue hierarchy created

The replan's three epics are now linked via native GitHub sub-issue relationships (not just prose references), so the hierarchy is navigable directly in the issue UI and on the Org Project board:

- **#286** (replan roll-up) → **#287** (Greenhouse Control Optimization, req A–E) and **#288** (Deploy Enablement)
- **#287** → children **#289 … #300**
- **#288** → children **#301 … #307**

### Two issues added

- **#307 — PR-only branch protection.** Codifies that firmware/code/shared-territory changes land via PR (the firmware CI gates run only on `pull_request`); docs may go direct. Filed as a child of #288.
- **#308 — Public lab-site crop exposure.** Tracks exposing crop data on the public lab site.

### Two PRs opened

- **#309 — Firmware-OTA SealedSecret shape + runbook** → targets `live/platform-main`. Carries the turnkey OTA password-sealing artifacts (the #301 prerequisite for the firmware-OTA subset).
- **#310 — BACKLOG canonical pointer** → targets `main`. Establishes the canonical pointer for the backlog.

### R4 fan button — operator answer resolves the path forward

Operator (Jason) confirmed the failed R4 fan press was **yesterday evening (2026-06-08 PM)** and that **it happens REGULARLY** (recurring, not a one-off). A recurring/systematic failure points primarily at the min-off dwell cause (H2: fans applied with `force_on=false` at `controls.yaml` ~808–809, so a press within ~90 s of a fan cycle-off is silently swallowed) and/or a habitual double-press toggle-cancel (H1).

Decision: the fix — pass `force_on=true` for fans/vent during the manual window, mirroring the fog micropulse — addresses the recurring case and **proceeds WITHOUT waiting on the exact-timestamp telemetry discriminator**. The DB pull is now confirmation, not a blocker. Tracked as **#289**.

### OTA password sealing reframed (#301)

OTA password sealing is reframed from "hard blocker" to a **tracked backlog prerequisite for the firmware-OTA subset only**. Most of the control-optimization program (req B/C/D — dispatcher/registry/DB-policy work) ships WITHOUT any OTA. Only the firmware halves — button fix **#289** and dusk-cutoff firmware **#292** — need the compile → bake → OTA path. Turnkey sealing artifacts live in **PR #309**. Tracked as **#301**.

### Full coverage

All work is tracked: **63 issues + 8 PRs**, all on Org Project "Iris / Verdify" **#1**.
