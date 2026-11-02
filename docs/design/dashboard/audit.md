# Dashboard v2 design audit

This records the adversarial audit of the v2 design for dashboard.verdify.ai and the irrigation program, run on
2026-11-01 against Jason's charter and answers. Nine critics raised 189 findings; skeptics confirmed 102, partly
confirmed 86 and refuted one; each file's owner applied the fixes, and the issue plan was rebuilt and re-checked. Every
persona need is closed by MVP stories except one the charter defers, every MVP and Next story maps to an issue, and the
dependency graph is acyclic. Round 3 (§11) applied defaults to the ten open decisions and resolved the cross-doc
conflicts; still to do: the docs PR and filing.

Status: Audit record (2026-11-01; Round 3 note 2026-11-02) for Draft v2

[README](README.md) · [product](product.md) · [screens](screens.md) · [architecture](architecture.md) · [irrigation](irrigation.md) ·
[design system](design-system.md) · [execution plan](execution-plan.md) · [OpenAPI](openapi/dashboard-api.v1.yaml) · [decisions](decisions.md) · **audit**

## 1. What was audited and how

- **Scope.** The v2 package as first drafted (README; product with 4 personas, 31 needs, 8 journeys and 87 stories; screens; architecture; irrigation; design system; execution plan; the OpenAPI draft; ADR-0011 and ADR-0012; the issue plan of 61 issues under 8 epics), read against the charter, the 27 D-IRP answers, rules R1–R12, Jason's Round 2 and 2b answers, the repository at `origin/main` and production data, read-only.
- **Nine critics.** Four persona critics walked every need of P1 owner-operator, P2 caretaker, P3 AI agents and P4 platform maintainer through stories, screens, API and issues. Simplicity checked the charter (what to cut); traceability and plan checked stories against issues, dependencies and closure; accuracy checked each claim against source; structural checked that each mechanism works; live data checked claims against production.
- **A skeptic per critic** re-checked every finding against the docs, source or data: confirmed, partial (real in part, or its fix had to change) or refuted.
- **File-owning fixers** applied the fixes, one per file set: product; screens; architecture with the OpenAPI and ADR-0012; irrigation with ADR-0011; design system. Round 2 overrode the rules, the charter and the docs; a conflict no rule settled became an open decision.
- **Plan re-validation.** A plan fixer rebuilt the issue plan, execution plan, README and issue template from the fixed docs (61 → 70 issues, 8 epics → 1); an independent script checked it (§8), re-run for this record. The graph as it stands is [execution plan](execution-plan.md) §8.

## 2. Results in numbers

| Critic | Raised | Confirmed | Partial | Refuted | High | Medium | Low |
|---|---|---|---|---|---|---|---|
| Persona P1 (owner-operator) | 19 | 9 | 10 | 0 | 9 | 9 | 1 |
| Persona P2 (caretaker) | 14 | 1 | 12 | 1 | 3 | 7 | 3 |
| Persona P3 (AI agents) | 19 | 9 | 10 | 0 | 5 | 11 | 3 |
| Persona P4 (platform maintainer) | 20 | 9 | 11 | 0 | 7 | 10 | 3 |
| Simplicity | 22 | 14 | 8 | 0 | 1 | 12 | 9 |
| Traceability and plan | 31 | 18 | 13 | 0 | 6 | 18 | 7 |
| Accuracy | 33 | 23 | 10 | 0 | 4 | 17 | 12 |
| Structural | 15 | 11 | 4 | 0 | 2 | 10 | 3 |
| Live data | 16 | 8 | 8 | 0 | 5 | 9 | 2 |
| **Total** | **189** | **102** | **86** | **1** | **42** | **103** | **43** |

Severity counts cover the 188 confirmed or partial findings. Fixes applied per file set (a finding touching several files counts in each): product 157, architecture with the OpenAPI and ADR-0012 158, screens 60, design system 34, irrigation with ADR-0011 26; skipped parts were superseded by Round 2 or a later cut, or belonged to another file's owner.

## 3. Persona coverage

**Answer:** P1, P3 and P4 are covered; P2 is covered for the first build, with P2-N8 left to Later by the charter's Plants cut. This is product §7's matrix as fixed; the `US-` prefix is dropped below.

| Persona | Verdict | Each need and the MVP stories that close it |
|---|---|---|
| P1 Owner-operator | Covered, 8 of 8 | N1 NOW-01–04, NOW-06–08, EQP-01 · N2 GRF-01–04, GRF-07, GRF-09, PLAT-06 · N3 SET-01–03, SET-05 · N4 PLN-01–03, SET-04 · N5 WAT-01, 02, 06–09, 12–15, 19, 21, 23 · N6 WAT-10, 11, 16, 17, 22 · N7 WAT-03–05, GRF-09 · N8 ACT-01–04, WAT-18, WAT-22, AUTH-02, AGT-03 |
| P2 Caretaker | Covered for the first build, 7 of 8 | N1 NOW-01, 06, 08 · N2 NOW-03, 04, WAT-01–03 · N3 WAT-12–14, WAT-23, EQP-06 · N4 WAT-02, WAT-16, NOW-03, EQP-06 · N5 NOW-07, 08 · N6 ACT-02, 04 · N7 AUTH-01–03 · N8 (plants remotely, log work) Later: PLT-01–03 |
| P3 AI agents | Covered, 9 of 9 | N1 AGT-01, PLAT-05 · N2 AGT-02, WAT-23 · N3 AGT-07, PLAT-04, SET-02 · N4 AGT-04 · N5 AGT-05, SET-04, WAT-07 · N6 AGT-03, ACT-01 · N7 AGT-06 · N8 AGT-08 · N9 AGT-01, 02 |
| P4 Platform maintainer | Covered, 10 of 10 | N1 PLAT-01, 08 · N2 PLAT-02, 08, 09 · N3 PLAT-03–05 · N4 PLAT-07 · N5 NOW-02, PLN-03 · N6 PLAT-06, 10, 11 · N7 PLAT-12, WAT-15 · N8 PLAT-03, 13, AUTH-01, 04 · N9 PLAT-14 · N10 AGT-03 |

The audit added needs P3-N8, P3-N9, P4-N9 and P4-N10; stories US-NOW-08, US-WAT-22, US-WAT-23, US-AGT-08 and US-PLAT-14 (MVP) and US-GRF-10 (Next); and journey J9, the caretaker visit. It moved US-GRF-07 and US-GRF-09 to MVP and US-PLN-04, US-PLN-05, US-ACT-05, US-EQP-03 and US-EQP-04 to Later. All 93 stories are in the matrix, each with 2–3 acceptance criteria; P3's verdict holds under either answer to Q-02.

## 4. Confirmed findings

One row per root cause: 94 rows hold all 188 confirmed or partial findings, each id once. Severity is the row's highest; (M) or (L) marks an id rated lower. Critic: P1–P4 are the persona critics, plan is traceability and plan, live is live data. Resolutions name the issue that carries the fix; Q-nn is a decision in [decisions](decisions.md), now with an applied default.

| IDs | Critic | Severity | Target | Problem | Resolution |
|---|---|---|---|---|---|
| ACC-02, LD-01, F14 (M) | accuracy, live, plan | high | architecture §5.2; US-SET-03, US-SET-05; VD-33 | The staged reconcile held any change set over 12 until an approval file appeared, so desired values and Iris's plan stopped reaching the controller (live: no push for 28.6 h, 19 settings off plan) | Round 2 #1: halt cleared live, 42/42 confirmed. VD-62 (W1): at most 12 per pass with readback, no approval file or custody, a `setpoint_delivery_stuck` alert; VD-33 and VD-28 depend on it |
| P1-01, P4-14 (M), LD-12 (M) | P1, P4, live | high | US-NOW-02, US-NOW-06, US-ACT-02; screens §1.2; VD-54 | The writer heartbeat behind the banners had no source: a dead ingestor read as "Controller offline", alerts as "No open alerts", and a halted writer showed nothing | `Now.writer`: the setpoints pipeline age, the stuck alert and a differing count (Round 2 #1); Q-01's ingestor heartbeat (every 60 s, stale after 180 s; VD-54) lets the banner name the side that is down; "No open alerts" only while data is fresh; no page says "Controller offline" |
| SC-02, ACC-22 (L), ACC-23 (L) | structural, accuracy | high | architecture §5.2; VD-33 | The overlay force-upserted every desired value on every pass, past the delta, drift and 12-command logic, and would push retired parameters; reboot and fast-path mechanics were misdescribed | VD-33 resolves each parameter before dedupe and the limit, sends only values that differ from the last push or readback, skips rows not writable now, and relies on the reconnect reseed after reboots |
| P1-04 | P1 | high | US-ACT-01, US-PLN-02; VD-65 | The forecast action engine writes settings through the NOTIFY fast path, unattributed, and its plan rows read as Iris's | VD-65: no `setpoint_changes` inserts, source `system`, actor `forecast-engine`, applied by the next normal dispatcher pass and never the NOTIFY fast path, labelled "Forecast rule · system" (Q-04) |
| P1-08 | P1 | high | US-SET-04; VD-33 | Releasing a `gl_*` lighting setting re-applied the released value, because the lighting policies read manual `setpoint_changes` rows as overrides | Migration M5 (VD-33): both policies ignore source `manual`; a release test |
| P1-07 | P1 | high | US-SET-01; VD-63 | Six live firmware tunables, `sw_onchip_band_enabled` among them, had no registry row, so "every setting" was not every setting | VD-63 registers or retires each and adds a tunables.yaml drift test |
| P1-05 | P1 | high | US-PLN-02, US-GRF-01, US-SET-04; VD-54, VD-11 | A non-manual write newer than the plan beats its later waypoints, yet the lanes still drew them as the plan ahead | API-07 returns `applied_from` per lane and `effective` per waypoint from the dispatcher's rule; superseded waypoints draw muted |
| LD-02, ACC-09 (M) | live, accuracy | high | architecture §7; API-06; US-NOW-01, US-GRF-01; VD-54, VD-56 | The band came from a deprecated function and the parked legacy limits (40–95 °F) passed for the band, while the controller runs its on-chip band | The band in force is the dispatcher's newest `band_role` rows (DERIVED, with age); older windows show the reconstructed crop band, labelled; the legacy limits are read-only |
| P3-01, F01, SC-12 (M), SIM-05 (M) | P3, plan, structural, simplicity | high | architecture §5.6; US-AGT-05, US-AGT-07; ADR-0012; VD-58 | VD-58 repointed `set_tunable` to `desired_state` for every audience, against R5, and `set_setting` had no contract | R5: `set_plan` and `set_tunable` keep writing `setpoint_plan`, unchanged; `set_setting` writes `desired_state` for every agent, Iris included, and refuses only Iris with `manual` on a manual item; Iris's context lists MANUAL SETTINGS (Q-02) |
| P3-04, P2C-11 (M), SIM-06 (M), F16 (M) | P3, P2, simplicity, plan | high | product §2; OpenAPI write schemas; VD-05, VD-12, VD-41, VD-58, VD-61 | Manual defaults contradicted R3: two opposite controls, agent writes defaulting to manual, and a skip or pause silently taking an item from Iris | One "Manual: Iris skips this" box, ticked on dashboard edits; REST defaults true, MCP false; a write stores the flag it carries (`manual_before`); pause, run, stop and clear never change it |
| ACC-04, P1-11 (M), SC-09 (M), LD-14 (M), F10 (M), P3-18 (L) | accuracy, P1, structural, live, plan, P3 | high | D-IRP-26 (R2); irrigation §4.3; VD-41, VD-33, VD-66 | The `experiment_armed` refusal rested on holds that live only in ingestor memory, invisible to the API, MCP and UI | Round 2 #2 drops the refusal; "armed" is `fn_mcp_armed_assignment_context()` (EXECUTE for the dashboard login), used only to stamp the experiment id on the write's activity row |
| ACC-01, SC-01, P4-02 | accuracy, structural, P4 | high | architecture §9; VD-30, VD-38, VD-66 | New grants change the sealed api, ingestor and MCP boundary digests, and the migrations were not registered successors: PreSync would refuse them or every runtime login would fail attestation | VD-30's rules for M1–M6: preflight receipt check, re-seal the changed receipts in the same transaction, `SUCCESSOR_NNN` registration and qualification fixtures, an attestation test; M2 attests with its own function |
| P4-03, F05 | P4, plan | high | VD-30, VD-32, VD-26; execution-plan §5 | The M2 bootstrap entry and the operator-api Deployment referenced Secrets that exist only after Jason seals them, stalling the next verdify-prod sync for every team | M2 (VD-66) is SQL only; VD-32 merges without its overlay line; VD-26's wiring PR adds both once the Secrets read back |
| P4-01, F06 | P4, plan | high | VD-24, VD-01, VD-08 | verdify-platform's bespoke CI builds eight fixed images and a curated test list, so the web image could not be built, checked or pinned and new tests would never run | VD-24: a `dashboard-web` path-impact profile and build step, a Node and Playwright step, the program's test paths; the web ships when its digest-pin PR merges |
| P4-04, F13 (M), SC-13 (L) | P4, plan, structural | high | VD-23, VD-24; execution-plan §5, §6 | The new Applications and Secrets go live only through the coordinator's consolidated pin PR, and the AppProject could not sync a Namespace | A Secret-only AppProject and Application (VD-23); VD-24's AppProject whitelists the Namespace and kinds; done means Synced and Healthy after the pin PR; §6 counts the train |
| P4-05, SC-07 (M) | P4, structural | high | architecture §5.7; VD-22, VD-28 | `DASHBOARD_WRITES_ENABLED` came from a plain ConfigMap env var, so flipping it changed nothing in running pods | A hash-suffixed `configMapGenerator` rolls the pod on any change; checks read `/api/v1/me` |
| P4-06, F30 (L) | P4, plan | high | execution-plan §5; US-PLAT-14; VD-22, VD-26, VD-32 | "Re-pin and sync" cannot roll back once new migrations apply, and the app had no runbook, ARGOCD.md row or OWNERSHIP.md rows | P4-N9 and US-PLAT-14: digest-only rollback keeping a migrate image that holds every applied migration, in AGENTS.md (VD-26); runbook and ARGOCD.md row (VD-22); OWNERSHIP.md rows (VD-32) |
| SIM-01, F21 (M) | simplicity, plan | high | architecture §12; US-PLAT-11; VD-02, VD-32 | Two sources of truth: the hand-written canonical OpenAPI and a FastAPI regeneration that would overwrite it | The committed file stays canonical; routes set `operation_id` to the design id; a test checks served operations against it (unserved allowed); a change re-renders the TS client in the same PR |
| F04 | plan | high | execution-plan §2.2, §4; issue template | Closure deadlocked: an issue closes only when live, but the releases that make it live depend on it | `depends_on` means merged (cross-repo and credential issues mean live); releases record each shipped issue's live check; R1.1 (VD-68) ships W2's server work |
| LD-05 | live | high | irrigation §2, §3; product §3; VD-38, VD-51 | L01 has not run since 2026-05-29 but was seeded as checked and in service | Round 2 #4: L01 goes first through the VD-64 run-now bridge; seeded enabled with no check, so its first schedule asks for the one-time check |
| P3-05, F03, ACC-03, P1-10 (M), SC-06 (M) | P3, plan, accuracy, P1, structural | high | architecture §5.3; irrigation §4.1; US-WAT-19; VD-37, VD-33, VD-40 | The three R12 site caps had no writable path (no `esp_object_id` made them read-only), three docs named three tools, and their readback state was undefined | `irrigation_site` class: written through API-10 and `set_setting`, skipped by the dispatcher, sent in the program push, confirmed by the program hash; ACC-03's new API-IRR-14 rejected under R1 |
| P2C-02, P4-10 (M), P3-19 (L) | P2, P4, P3 | high | SCR-01, SCR-04; API-IRR-06, -07, -13; VD-16, VD-40, VD-42 | Before the engine firmware, or after a rollback to it, Stop, Run and Clear looked accepted and quietly expired | `IrrigationStatus.engine` from a live capability check; before the engine, run is L01 only and Stop works on every line through the VD-64 bridge, other engine commands return 503 and store nothing; irrigation §5 adds the OTA override and promote-last-good |
| P1-02, P2C-06 (M) | P1, P2 | high | US-WAT-13, US-NOW-03; SCR-01, SCR-04 | "All Verdify water stopped" overclaimed (VPD misting, fog and the next run can restart), and unknown occupancy drew nothing | Stop ends what runs and says what can restart, with Disable per line; no main shutoff; occupancy always shows, "unknown: misters may run" included |
| P2C-03, LD-04 | P2, live | high | D-IRP-17; US-WAT-18; irrigation §4.7 | `leak_detected` is flow with every valve closed on a meter shared with the hose, sink and hydro; the 24 h drip latch would have fired on 3 of the last 30 days | Round 2 #3: alert only, no latch, titled "Water flowing with every Verdify valve closed"; the engine firmware drops `leak_detected` from every force-off and the irrigation cancel (Q-05; VD-45, VD-46), and today's interlock stays until then |
| P2C-01, ACC-13 (M) | P2, accuracy | high | irrigation F12; US-EQP-06; VD-47 | Driving "the same latches" as the HA buttons runs `occupancy_quiet_override`, so a burst would ignore a person inside for an hour | `manual_override` sets only the three deadline latches and never touches occupancy; the override script goes; the physical buttons drive the same latches |
| P1-03, P3-10 (M) | P1, P3 | high | US-WAT-22; SCR-04; VD-16, VD-42, VD-45, VD-61 | The D-IRP-19 watchdog can latch a Vanda mister off until midnight, with no story, alert or agent tool to see or clear it | US-WAT-22: back-off and latch shown on Water and Now, Clear fault (API-IRR-13), the `irrigation_line_faulted` alert, MCP `irrigation_clear_fault` |
| P4-07, F02, ACC-19 (M) | P4, plan, accuracy | high | VD-53; irrigation §7 | VD-53 was a tracking issue in the wrong repo, and its keep list would disable the Lutron and occupancy entities the controller uses and the sensors monitoring alerts on | Filed in jvallery/homeassistant (R11); keeps the controller's sensors, Lutron, Frigate occupancy and the YINMIK hydro sensors; disables the unused grow-light proxies |
| P3-02 | P3 | high | architecture §5.6; US-AGT-03; VD-59 | Named agents had no network path: Cloudflare Access blocks mcp.verdify.ai and the internal policy admits three pods | VD-59 adds `allow-mcp-from-named-agents` for labelled in-cluster pods; the first named agent runs in the cluster, and off-cluster agents are out of scope (Q-06) |
| P3-03, P3-06 (M), ACC-07 (M) | P3, accuracy | high | irrigation §8.2; architecture §5.6; VD-58 to VD-61 | The MCP tools had names but no arguments, defaults, returns, codes or audiences, and `TOOL_AUDIENCES` was missing from the lockstep (MCP fails closed at start) | A typed tool table with REST twins and an audience table; P3-N8 and US-AGT-08; schedule bounds in the agenda; `TOOL_AUDIENCES` in the lockstep |
| P1-06 | P1 | high | US-GRF-07; VD-10, VD-56 | MVP Graphs plotted only temperature, VPD and RH; the metric picker was Next and the registry had no completeness test | US-GRF-07 is MVP; every numeric `climate` column gets a key or a written exclusion (a test against db/schema.sql), plus the controller vitals; the picker ships in VD-10 |
| LD-03 | live | high | US-NOW-03, US-EQP-01; SCR-07; VD-54 | Equipment rows are written on change only, so a per-actuator cadence would show idle actuators unknown (13 of 18), and HA polls inflated grow-light cycles | Known while the controller reports; since, runtime and cycles from collapsed edges; a fixed actuator list |
| P1-09, P2C-09 (M), F24 (M), ACC-20 (M), LD-15 (L) | P1, P2, plan, accuracy, live | high | product §2, WAT, AGT and PLAT stories, J2, J7 | Story ACs cited pre-R1 operation ids and old codes, enums and behaviour, so tests written from them would fail a correct build | Rewritten to the OpenAPI ids and irrigation.md's vocabulary (R1, R8) and to Round 2 |
| ACC-08 | accuracy | medium | architecture §5.5; VD-29, VD-33 | The dispatcher's 1 % dead-band would never push a one-step change on 19 settings (840 → 845 hPa) and would show it confirmed at once | A grid-aware comparator in `verdify_schemas` (VD-29) decides pushes and readback states |
| P3-08, ACC-15 | P3, accuracy | medium | architecture §5.3; VD-67, VD-33 | "Any writable row" included the legacy feed-commissioning knobs and the dispatcher-derived activity mirror: a second irrigation control path | Those rows and the legacy band limits are read-only with a reason; the overlay skips rows not writable under the current registry |
| LD-10 | live | medium | architecture §5.3; VD-29, VD-55 | Iris writes off-grid values (48.75 s on a 30 s step); snapping would step them when a person took the setting manual | Snap to the registry step; a value equal to the current effective or device value is stored as is |
| ACC-05, SC-03 | accuracy, structural | medium | architecture §5.2, §5.5; US-SET-02; VD-33 | Under an armed policy manifest the firmware serves the experiment's value, so a desired value for those 48 fields can never apply and would be re-sent every pass | Stored and shown "Held by experiment <id>: applies when it ends", sent once after disarm; component-authority holds pass and bundle holds wait |
| SIM-14, F29 (L) | simplicity, plan | medium | architecture §5.2; irrigation §4.4; VD-33 | The ingestor would log deviations on every push and every VPD-source switch: a second activity writer for telemetry | The experiment id is stamped once, on the write's own activity row; VPD-source logging cut (Water shows the source live) |
| P4-09, P1-13 | P4, P1 | medium | US-SET-02; OpenAPI `DesiredSetting`; VD-21, VD-55, VD-66 | NOT CONFIRMED never said why (clamp, heap deferral, failed push, writer down), and one failed write could not be traced across tiers | `state_reason` from `setpoint_clamps` and `setpoint_changes` (granted to the dashboard login); `X-Request-Id` is the Idempotency-Key in both tiers' logs and `activity_log` |
| P1-17, ACC-17, SC-15 (L) | P1, accuracy, structural | medium | US-SET-03; architecture §5.3; VD-67, VD-12 | Band writes had no MVP test, and "the matching row" ignored season-scoped anchor rows and widths stored on four rows | A write updates every row at the best season score; a width updates all four anchors of the zone crop; US-SET-03 tests it |
| ACC-16, SIM-04 (L), SIM-13 (L) | accuracy, simplicity | medium | US-SET-02, US-SET-05; SCR-05; VD-12, VD-29 | Settings carried Send again, a persistence column built on a field tunables.yaml lacks, a drawer sparkline and an Escape confirm | All four cut; the dispatcher re-sends by itself and re-applies desired values after a reboot |
| P3-13, F11 | P3, plan | medium | architecture §5.3; API-15, API-16; VD-30 | A reused request id returned an earlier write as success, scope differed by table, and ack and resolve took no key (R7) | The activity row is the idempotency record (`request_id`, `request_sha256`, unique per source); another payload gets `request_id_reused`; every write takes the key |
| F08, F09 | plan | medium | VD-30, VD-38, VD-39, VD-66 | VD-30 bundled two migrations, M4 had no owner, and M2's grants missed sources that L4 and L6 read | M1 (VD-30), M2 SQL only (VD-66), M3 (VD-38) and M4 (VD-39), merged one at a time; VD-66 grants every relation it reads, with a DB test |
| P4-08, P1-18, ACC-06 | P4, P1, accuracy | medium | US-AGT-03; SECRETS.md; VD-59 | Revoking or adding an agent needed a device-gated sync, and per-agent token env names would make MCP not ready | Secret `verdify-mcp-agents` mounted as a volume and re-read every 60 s: a key change takes effect within 3 min with no restart; Rotate lines |
| P4-11, F12 | P4, plan | medium | VD-48; irrigation §7 | With prune off, removing the HA backfill left its CronJob running, the new egress test would break, and the weather-API switch had no network path | VD-48 deletes the CronJob after the sync and fixes the egress test; the Tempest already arrives through the ESP32 (Round 2 #6), so only the HA `tempest_sync` remnant goes (VD-49 folded in) |
| P4-12, F31 (L) | P4, plan | medium | execution-plan §5; VD-26, VD-28, VD-50 | "Inert until its data exists" was false for the HA retirement, entity retirement, Iris's context and the MCP write tools | §5 lists what is inert and what goes live at the next sync; each release runs and records every shipped issue's live check |
| P4-13 | P4 | medium | US-PLAT-08; VD-26, VD-28, VD-50 | Release checks needed a passkey sign-in, so fleet verifiers could not verify, and smoke ignored the operator API | `scripts/dashboard-smoke.sh` mints a viewer session in the pod; k3s smoke checks the operator API; Jason signs in once at R1 |
| P4-15, ACC-18 | P4, accuracy | medium | US-AUTH-01, US-PLAT-02, US-PLAT-09; SCR-09; VD-26, VD-27 | The redirect checks expected a 302 to the issuer path, which a correct deployment never sends | `/` → `/auth/login?returnTo=%2F` → Authentik's `/application/o/authorize/` with the client id and S256 |
| P4-16, P2C-08 | P4, P2 | medium | architecture §4; US-AUTH-02; VD-23, VD-26 | The Authentik setup named no MFA flow or policy mode, and nothing created a caretaker's account or a viewer check account | VD-23: prepare helper, member-tier flow, policy mode `any`, MFA contract entry, the viewer check account; VD-26: the dashboard-access runbook (Q-07) |
| P4-17 | P4 | medium | VD-25; architecture §10 | VD-25 was a check that might change nothing, though LAN resolution needs an explicit zone row | VD-25 adds the proxied row with its internal record in jvallery/network-infra |
| SC-08 | structural | medium | VD-21, VD-32 | A viewer could reach the session-mint route through an encoded dot-segment path and mint an operator session | The proxy refuses dot and encoded segments before routing; INT-01 refuses any request carrying `X-Dashboard-Session` |
| F19, ACC-11, SC-04 | plan, accuracy, structural | medium | US-WAT-13; architecture §5.8; irrigation §6.1; VD-42, VD-64 | No wake reached `irrigation_sync` and the docs disagreed on its cadence, so Stop could wait 60 s or more | `irrigation_changed` force-launches the sync; pending commands polled every 5 s; stop first, never heap-deferred; one cadence (60 s plus the wake) |
| SIM-02, SC-05 | simplicity, structural | medium | irrigation §4.7, §5, §6.1; OpenAPI `Command`; VD-42, VD-45 | A five-state command lifecycle with device acks was rebuilt, while `irr_state`'s single outcome slot lost outcomes in bursts and outages | Commands are pending, then sent or failed: sent at most once and never late; outcomes go to `irr_events` with sequence numbers, and a lost one is a gap, never "missed" |
| SIM-03 | simplicity | medium | irrigation §5 F3; US-WAT-09, US-WAT-10 | Program data was confirmed twice: by the program hash and by published masks | The hash alone confirms enables, pauses and mister flags; the masks go |
| F20, SC-11, ACC-25 (L) | plan, structural, accuracy | medium | irrigation §5; VD-36 | The frozen program record's field sizes did not add up, and its length did not determine its contents | A fixed-capacity 584 B record with a slot count and reserved bytes, versioned by its magic; golden vectors |
| SIM-08, P3-14 | simplicity, P3 | medium | irrigation §4.3, §5; US-WAT-14; VD-36, VD-41 | Per-run skips duplicated pause and lost concurrent edits as a whole-array replace | Skips cut; pausing a line across a run covers rain and work days (Q-03) |
| P1-14, SIM-09 | P1, simplicity | medium | SCR-04; design-system `ScheduleWeek`; irrigation §4.3; VD-15, VD-17 | R9's week view had no spec, while the model carried every-N-days, cycle-and-soak drips, three horizons and two schedule views | One 7-day week view that also previews drafts (a day switcher at 390 px); weekday sets only; API-IRR-02 takes 1–7 days |
| SIM-07 | simplicity | medium | irrigation §4.3; API-IRR-03 | Schedule writes were refused for conditions the controller already handles at run time (over the cap, a disabled line) | Those refusals are gone; the preview shows the cap, and the controller ends a run at it |
| P3-15, SIM-19 (L) | P3, simplicity | medium | irrigation §4.2; VD-41, VD-69 | Any writer, agents included, could record the physical check, and it needed a box and a note in three places | One "Checked" box with an optional note, recorded only from the dashboard; an agent gets `check_required` |
| P1-15 | P1 | medium | US-WAT-12; D-IRP-16; VD-16, VD-52 | Fertilizer run-now (prewet, feed with L03, flush) had no acceptance criterion, and the first run was called "the test" | US-WAT-12 covers it; D-IRP-16 reads "no bench or in-place test step" |
| LD-06, LD-11, SIM-20 (L) | live, simplicity | medium | irrigation §6.2; US-WAT-04; VD-39 | The run ledger rebuilt runs that `v_water_relay_runs` already holds, back-filled outages, and assumed sub-gallon flow from a whole-gallon meter | M4 is a view over `v_water_relay_runs` (history from 2024-11-17); no back-fill; whole gallons per run and per day, with no flow flags (`no_flow` included) and no per-run rate (Round 2b B, Q-10) |
| ACC-14 | accuracy | medium | irrigation §4.6, F9; VD-44, VD-46 | SENSOR_FAULT's relay lock does not cover misters, so scheduled mist could run in it, and F9's fog veto list was incomplete | The engine refuses scheduled, run-now and degraded mist in SENSOR_FAULT; F9 names every remaining fog veto |
| SC-10 | structural | medium | irrigation §4.5, §4.7; VD-44 | RTC no-init memory holds garbage after power-on and ESPHome defers NVS writes, so counters and exactly-once claims could break | Magic, version, day and CRC32 on the RTC block, with integer counters; each claim is saved and synced before the relay opens |
| LD-07 | live | medium | irrigation F7, §7; US-WAT-16; VD-48 | The MQTT occupancy topic never reported a person, and the 15 min ingestor latch released mid-visit | Round 2b A: the HA occupancy sensor is the only input, and VD-48 removes the MQTT path; each ON poll extends the latch, up to 60 min |
| P1-16, P2C-07 | P1, P2 | medium | US-EQP-06; SCR-07; VD-16, VD-47, VD-54 | Restart had no acceptance criterion, and a burst's "15 min left" had no device readback | US-EQP-06 covers restart (uptime resets, settings re-pushed, program back from NVS); bursts show from their `bs_*` readbacks, with why when held off |
| ACC-12 | accuracy | medium | US-EQP-01; VD-48 | Controller intent and the Lutron state write one `equipment_state` key, so they cannot be shown apart | VD-48 maps the controller's switches to `grow_light_*_intent` keys (Q-09) |
| F23 | plan | medium | VD-52 | VD-52 ended in open-ended plumbing and could never close | VD-52 brings L02 with L03 online, then L08 and L10 (plumbed, Round 2b E); L05 and L06 stay reserved |
| P3-07, F17 | P3, plan | medium | architecture §5.1, §6; VD-59 | The caller's identity never reached the code that writes, the alerts tool took its actor from arguments, and admin writes had no R4 source | A request-scoped actor from the token (`iris`, `agent:<name>`, `agent:admin`); services ignore actor arguments; a write with no actor is refused |
| P3-09 | P3 | medium | irrigation §8.2; VD-58, VD-61 | An Iris irrigation write could lock its planner trigger and fail it as `wrong_action` | `trigger_id` goes only into `activity_log.detail`; these tools never touch the trigger ledger |
| P3-11 | P3 | medium | US-AGT-01; VD-58 | Agents could not learn what happened to their writes or who changed them since | P3-N9 and the MCP `activity` tool |
| P3-12 | P3 | medium | US-AGT-04; OpenAPI `ProblemCode`; architecture §5.3 | The agents' code list used codes the enum lacks, omitted real refusals and said nothing about retrying | `ProblemCode` is the one list (`line_paused`, `line_faulted`, `request_id_reused` added), with a `retryable` rule |
| P3-16 | P3 | medium | US-AGT-06; VD-60 | Iris's irrigation context could not be built in the bash context script without re-deriving state | A Python entry point in the ingestor image prints about 60 lines from the view `irrigation_status` reads; planner-dry reports the cost |
| P2C-04, P2C-12 (L) | P2 | medium | US-NOW-08, US-NOW-07; VD-09, VD-04 | The caretaker had no single plain "is everything OK?" answer, and "plain words" was untestable | US-NOW-08: one worst-first sentence; a words-table completeness test (VD-04) |
| P2C-05 | P2 | medium | US-NOW-04, US-WAT-01; VD-40 | No field said when each zone last got water | `IrrigationLine.last_water`; Now shows each zone's last water |
| P1-12 | P1 | medium | US-GRF-09; VD-10, VD-56 | The charter's simple scorecard was Next, with no data source | US-GRF-09 is MVP: 30 days from `v_daily_scorecard` (M6) and API-IRR-10, missing values "—" with a reason, CSV |
| ACC-10, LD-13, ACC-24 (L) | accuracy, live | medium | architecture §7; US-GRF-07; VD-56 | The registry seeded DLI, which has had no source since July, and named the heap metric in bytes though it holds kB | DLI is out of the first build (Round 2 #10; PAR meter VD-71); `diagnostics.heap_free_kb` |
| ACC-21, LD-09 | accuracy, live | medium | US-PLN-03; OpenAPI `PlannerTrigger`; VD-54 | The outcome enum missed five ledger statuses (`missed` among them), the health view is one summary row, and waypoints reach about 4 h ahead | API-07 reads the ledger with its ten statuses verbatim; lanes default to 24 h |
| LD-08, ACC-30 (L), ACC-33 (L), SIM-16 (L) | live, accuracy, simplicity | medium | US-ACT-02 to -04; SCR-06; API-14, API-16; VD-13, VD-72 | Open-alert counts disagreed (3 against 33), "high" is no database severity, churn was heavy, and a separate alert page and a mandatory note added weight | One count on every surface (unacknowledged `v_open_alerts` rows, disposition open, repeats of one `alert_type` and zone counted once); R6 severities; VD-72 auto-resolves long-offline probes and merges repeats (Round 2b C); alerts expand in place (API-18 reserved); the note is optional |
| SIM-15, SIM-17 (L) | simplicity | medium | OpenAPI; SCR-01, SCR-03 | The MVP contract carried operations and fields no MVP story needs, and a mister endpoint duplicated line updates | API-08, API-18 and API-IRR-09 reserved; Now reads water from API-IRR-01; mister flags are line fields; API-03 kept for the picker |
| F07, F22, F26 (L), P3-17 (L) | plan, P3 | medium | issue plan; execution-plan §6 | Real blockers were missing (router includes, services, R1 and R2 reads, form controls, lockstep files, the agent identity), so the critical paths were wrong | VD-31 split (VD-67 holds the services); edges added; L4's read and write files separated; critical paths recomputed |
| F15, F18 | plan | medium | VD-16, VD-69, VD-43, VD-46, VD-48 | VD-16 bundled two write models on the critical path, and the firmware issues overlapped in controls.yaml | VD-16 (commands) and VD-69 (line settings) split; F9 moved to VD-46; a second firmware agent from VD-45; VD-49 folded into VD-48 |
| SIM-10, F25 (L), F27 (L) | simplicity, plan | medium | execution-plan §4; VD-18 to VD-20, VD-35, VD-57 | W4 issues were grab-bags, and some Next stories leaned on operations or routes nobody owned (replan, setpoint-server) | VD-35 retired; replan, platform alerts and controller and sensor health moved to Later; VD-57 adds its own Next operations first |
| SIM-11 | simplicity | medium | VD-50; execution-plan §6 | The irrigation release waited on the setting-writes release only to flip the writes flag | VD-50 no longer depends on VD-28; R3 flips the flag itself if R2 has not shipped |
| SIM-12, SIM-22 (L), F28 (L) | simplicity, plan | medium | work-item tables in architecture, irrigation, design system and screens; README | Four work-item tables (64 items) ran a second id space and graph, cut lists were restated, and cited sections carried superseded rules | The tables are gone and VD keys are the only ids; cited sections were edited; README §3 now links [decisions](decisions.md) instead of restating it (Round 3) |
| P4-18, P4-19 | P4 | low | architecture §10, §11; VD-21, VD-22 | The BFF's egress rule to Authentik was a guess, and `/readyz` waited on OIDC discovery, so an Authentik outage could take reads and Stop down | network-infra#839's `egress-deny-management`, vendored as in `verdify-prod` (#1114); kube-dns, the operator API and `auth.vallery.net` lie outside the management set, so no egress allow; `/readyz` reports Authentik without gating on it |
| P4-20 | P4 | low | product P4; execution-plan §2.2; VD-26 | Releases ignored the estate's coordination rules (steer, locks, merge train, verifier) | §2.2 and the release issues follow them; verdify-platform has no repo pod |
| P1-19, ACC-29, LD-16 | P1, accuracy, live | low | US-GRF-02, US-GRF-03; VD-10, VD-56 | Lanes cover at most 31 days and raw retention is 90 to 365 days, so long windows had silent gaps | Lanes say so beyond 31 days; `retention_days` per metric, and beyond raw retention a chart shows daily summaries and says so (Round 2b D) |
| P2C-13 | P2 | low | design system §1, §3; SCR-01 | The design system contradicted the five-tab bottom bar and tested other widths | R10 applied; axe at 320, 768 and 1440 px |
| P2C-14 | P2 | low | product §5; VD-16 | No journey had the caretaker as its main persona | J9 caretaker visit and `e2e/caretaker-visit.spec.ts` |
| SIM-18 | simplicity | low | architecture §10, §11; VD-27 | Two scrape jobs, a metrics port, custom metrics and three alerts surrounded the one probe the charter keeps | One route-coverage probe; OPS-03 reserved |
| SIM-21 | simplicity | low | issue plan | Seven lane epics duplicated the lane table | One epic, VD-E0, with lane and wave labels |
| ACC-26, ACC-27, ACC-28 | accuracy | low | irrigation §2, §5, §6; architecture §1, §2; product §2 | Entity counts and file citations were off, and the docs claimed a mister watchdog already exists | Corrected; the D-IRP-19 watchdog is new engine work (F10) |
| ACC-31 | accuracy | low | VD-31, VD-32 | The api image held neither `operator_api.py` nor `verdify_services` | VD-31 copies `verdify_services` into the api and MCP images; VD-32 adds `operator_api.py` |
| ACC-32 | accuracy | low | irrigation §7 HA-05; `v_data_pipeline_health` | Retiring the HA hydro read would leave a permanently dead hydro source | Round 2 #5 keeps the hydro read until VD-70's direct reader |
| SC-14 | structural | low | irrigation §4.3; VD-41, VD-42 | Concurrent writers could pass the 24-schedule limit and break every compile | An advisory lock around the cross-row checks; the compile reads one snapshot |

## 5. Refuted findings

- P2C-10 (persona P2): its skeptic refuted it on re-check, so no fix was made; the finding's text and the skeptic's note were not carried into this record.

## 6. Live data results

| Checked (read-only, 2026-11-01) | What it showed | Where it landed |
|---|---|---|
| Device writer | The #433 bounded writer was halted (since 2026-10-19): no setpoint push for 28.6 h, and the active plan differed from the device on 19 settings | Cleared live at 22:07Z, 42/42 values and 43 `setpoint_changes` rows confirmed (Round 2 #1); VD-62 |
| Band in force | The controller runs its on-chip band (house 74.92–84.92 °F, 0.873–1.303 kPa); `temp_low`, `temp_high`, `vpd_low` and `vpd_high` are parked at 40, 95, 0.35 and 2.8; at 14:36 indoor VPD 1.26 kPa sat inside the device band but above the projected and firmware bands | LD-02: band from `band_role` rows |
| Equipment | Rows are written on change only; 13 of 18 board actuators had last reported 54 h earlier; HA re-inserts the grow lights every 5 min poll | LD-03 |
| Wall drip L01 | No ON edge since 2026-05-29; the HA RUN CLEAN button is unused | Round 2 #4; VD-64 |
| Water meter and leak signal | Whole-gallon steps; `flow_gpm` reads only 0 or 1; daily `water_used_gal` is null on 6 of 30 days; per-line runtime already exists back to 2024-11-17. `leak_detected` fires on hose, sink and hydro use: a 24 h latch would have stopped drips on 3 of the last 30 days | Round 2b B and Round 2 #3; M4 view (VD-39) |
| Occupancy | The MQTT topic never reported a person (140 observations in 60 days); every detection came from the HA sensor, polled every 300 s; the 15 min latch released mid-visit 4 times in a week | Round 2b A; VD-48 |
| Alerts | 3 unacknowledged open alerts against 33 unresolved (28 of them 2–5 months old); 1,480 raised in 30 days, mostly `soil_sensor_offline` | Round 2b C; VD-72 |
| Planner | The health view is one summary row; the ledger uses ten statuses; waypoints reach about 4.4 h ahead; Iris writes off-grid values (48.75 s on a 30 s step) | LD-09, LD-10 |
| Light, heap and retention | No DLI source since 2026-07-10; `diagnostics.heap_bytes` holds kB (about 65); raw retention is `climate` 365 days, `diagnostics` 180, `setpoint_snapshot` 90 | Round 2 #10 (VD-71); Round 2b D |
| Experiments | None armed | Nothing to hold or stamp today |

## 7. Resolution rules applied

| Rule | As applied |
|---|---|
| R1 | OpenAPI operation ids are canonical: 38 operationIds (31 on `/v1`) used by every doc and issue; the "Arch id" column is gone; API-08, API-18, API-IRR-09 and OPS-03 stay reserved |
| R2 | **Superseded** by Round 2 #2: nothing is refused while an experiment is armed; each write carries the experiment id, and a value the armed manifest overrides waits until it ends |
| R3 | One manual flag that only Iris skips (Round 2 #8): dashboard edits default it on, agent writes off; one code, `manual`; pause, run, stop and clear never change it |
| R4 | Sources `dashboard`, `iris`, `agent:<name>` (admin as `agent:admin`) and `system`; the actor comes from the session or token, never from arguments |
| R5 | `set_plan` and `set_tunable` keep writing `setpoint_plan`; `set_setting` writes `desired_state`; the newer value wins and a manual row always beats Iris (Round 2 #9). Q-02: every agent, Iris included, gets `set_setting`, which refuses only Iris on a manual item |
| R6 | `info`, `warning` and `critical` everywhere; "high" removed |
| R7 | Every write takes an `Idempotency-Key`, ack and resolve included, stored on its activity row; a reused key with another payload gets `request_id_reused` |
| R8 | irrigation.md wins: 24 schedules, no feed window, run-now starts or is refused (never queued), pause leaves VPD misting running |
| R9 | `ScheduleWeek`: line × day over 7 days, with a day switcher at 390 px |
| R10 | The five-tab bottom bar (Now, Water, Graphs, Activity, More) in screens and the design system |
| R11 | VD-53 is filed in jvallery/homeassistant |
| R12 | The three site caps are `irrigation_site` rows written through API-10 or `set_setting`, sent in the program push and confirmed by its hash |

## 8. Plan checks

From the plan fixer, re-run for this record against the fixed docs: all pass. The counts move with the plan, so this record does not quote them: [execution plan](execution-plan.md) §8 has the graph, §6 the critical paths and §7 the start-today list.

| Check | Result |
|---|---|
| Acyclic dependency graph (DFS over every issue and edge) | Pass |
| Every `depends_on` key exists, and none is an epic or a self-reference | Pass |
| Every MVP and Next story (75 + 9) is in at least one issue; no issue lists a Later or unknown story | Pass, 84/84 |
| Every issue has lane, wave, size, parent, stories, summary, repo, title, sources and 3–6 acceptance criteria | Pass |
| Every issue's parent is VD-E0, the only epic | Pass |
| W1 issues (31) depend only on W1 issues | Pass |
| Design references resolve to real headings in architecture, screens, irrigation, design system, product and decisions | Pass |
| Every operation id in issue text exists in the OpenAPI (38; API-08, API-18 and API-IRR-09 cited only as cut or folded) | Pass |
| VD-53 in jvallery/homeassistant (R11); agents VD-23 and VD-24, network-infra VD-25, monitoring-stack VD-27 in their owners | Pass |
| No local machine paths in the issue plan, execution plan or README | Pass |
| The execution plan's mermaid nodes and edges equal the plan's issues and dependencies | Pass |
| Execution plan §10 rows (lane, wave, size, depends on) equal the plan for every issue; wave counts and start-today table match | Pass |
| README numbers match the plan and the docs (issues, stories, operationIds, needs, journeys, start today) | Pass |
| Transitive-redundancy scan: execution plan §8 names each edge kept although others imply it | Pass |
| Critical paths in points, for every release, the L01 bridge and the feeds | Computed; execution plan §6 |

## 9. Residual risks

1. **Defaults stand in for answers.** Q-01 to Q-14 follow applied defaults that Jason may still change; each names the issue it touches, so a change is one edit there ([decisions](decisions.md)).
2. **The writer can halt again until VD-62 ships.** The halt was cleared by hand; until plain convergence is live, a change set over 12 (an ESP32 reboot, a burst of agent writes) can stop delivery and need the custody steps again.
3. **R1 waits on work no point count shows:** the jvallery/agents merge train and consolidated pin PR (VD-23, VD-24), Jason sealing the Secrets and naming the first agent, and one passkey sign-in.
4. **Serial migrations.** Six migrations, and later the light meter's, merge one at a time with receipt re-seals; a wrong re-seal, or another migration merged in between, breaks api, ingestor or MCP attestation at the next verdify-prod sync. The ephemeral attestation tests in VD-30 and VD-66 are the guard.
5. **The engine firmware is the largest controller change since July's heap starvation:** about 70 entities out, five services and a 584 B program in, and no soak by charter. A failed OTA lands on pre-engine firmware until promote-last-good; the capability check keeps the UI honest, but schedules stop.
6. **The leak alert fires on every hose, sink or hydro use** (the latch would have fired on 3 of the last 30 days). It no longer latches, but it is critical and posts to Slack, and until the engine release today's firmware still holds mist, fog (survival fog included) and new drip starts while it is up; the engine drops that interlock (Q-05).
7. **Experiments are unprotected (Round 2 #2).** Dashboard and agent writes during an armed experiment land and are only stamped with its id, so a run can be confounded; values its manifest owns wait until it ends.

## 10. Readiness checklist

| Item | Ready | Evidence |
|---|---|---|
| Every persona need closed by an MVP story | Yes | 34 of 35; P2-N8 is Later by the charter (§3) |
| Every story has a priority and 2–3 testable acceptance criteria | Yes | 93 stories (75 MVP, 9 Next, 9 Later): 62 with three, 31 with two |
| Every MVP and Next story is in an issue | Yes | 84/84 (§8) |
| Every issue states what, why (stories), how (summary, sources), its lane and its done (3–6 acceptance criteria) | Yes | §8; every issue in execution plan §10 |
| Dependency graph complete, acyclic and matching the execution plan | Yes | Execution plan §8 (mermaid) and §10 equal the plan (§8 here) |
| Parallel lanes with work today | Yes | 7 lanes; the start-today issues cover all 7 (execution plan §7) |
| Contract canonical and parseable | Yes | OpenAPI 3.1.0 parses: 35 paths, 38 unique operationIds |
| Cross-repo work planned in its owner repos | Yes | jvallery/agents, network-infra, monitoring-stack and homeassistant (§8) |
| Round 2 and 2b answers carried into the plan | Yes | VD-62 (#1), VD-64 (#4), VD-70 (#5), VD-48 (#6, 2b A), VD-71 (#10), VD-72 (2b C), VD-52 (2b E); ADR-0012 accepted (#11) |
| Docs agree with each other and with [decisions](decisions.md) | Yes | Round 3 (§11): the Q-01 to Q-10 defaults applied and the nine cross-doc conflicts resolved |
| Open decisions settled | Yes | Q-01 to Q-14 carry applied defaults (2026-11-02) that the docs and the plan follow; Jason may change any |
| No local paths or secrets in the package | Yes | grep over this folder and both ADRs: none |
| Design-docs PR open, so issues can link its head commit (Round 2 #12) | Yes | PR [#1119](https://github.com/VerdifyConsultancy/verdify-platform/pull/1119), head `af8fe5db0`; it merges once CI is green |
| Issues filed | Yes | Epic [VerdifyConsultancy/verdify-platform#1120](https://github.com/VerdifyConsultancy/verdify-platform/issues/1120) and 71 issues (2026-11-02); verdify-platform issues are its sub-issues, the five cross-repo issues name it; 147 blocked-by links |
| Jason's W1 inputs | No | Seal the Secrets and name the first agent (VD-23) |
| CI runs the new code | No | W1 work: VD-24 adds the web build, the Node and Playwright checks and the new test paths |

## 11. Round 3 (2026-11-02)

The ten open decisions got applied defaults ([decisions](decisions.md) Q-01 to Q-10); the docs and the plan follow them, and Jason may change any. The cross-doc conflicts the plan check found were resolved in this pass, each to one rule:

- **`set_setting` audience:** iris, agent and admin; it refuses only Iris, with `manual`, on a manual item, and `set_plan` and `set_tunable` stay as they are (Q-02, VD-58). **Heartbeat:** one `system_state` row every 60 s, stale after 180 s; banners worst first: API down, ingestor not running, controller not reporting, leak (Q-01, VD-54).
- **Stop before the engine:** Stop and Stop all water work on every line through the wall-clean bridge, and Run on L01 only (irrigation §6.5; VD-16, VD-64). **`AlertRow`:** built in VD-03; Now and Activity reuse it. **`Setting.group`:** gains `band_in_force`, the read-only "Band in force" group that VD-55 serves, named the same in screens and product.
- **`no_flow` against Round 2b B:** whole gallons per run and per day, with no flow flags and no per-run rate (Q-10; VD-15, VD-40). **MQTT occupancy:** the HA sensor is the only input, and VD-48 removes the topic path (Round 2b A).
- **README staleness:** §1, §3, §4, §8 and the nav brought current; §3 now links decisions. **M2 issue key:** VD-66, not VD-30 (Q-08).
- **Filing:** the issues link the design at the docs PR's head commit, so filing does not wait for the merge (Round 2 #12).
