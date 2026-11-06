# November 6 full triage: open work, closures and the replanned graph

An independent verifier triaged all **118 open issues** at about 13:00–14:00Z on 2026-11-06. The triage read each issue and its comments, gathered read-only live evidence (prod DB, cluster, merged PRs, release revisions) and commented on every issue.

- **Live baseline:** prod `verdify-prod-dark` is Synced and Healthy at `5196f955` (release 3, migrations through 302). Firmware `2026.11.6.0938.36c7630c` is accepted. Dashboard web is `e437741f`; the Crops/Planner web pin is in flight.
- **Known defect:** the #1388 egress NetworkPolicy blocks pods from reaching Cortex. The re-embed has written 0 rows and knowledge_search fails.
- **Closed: 30.** 15 met acceptance and 15 were superseded.
  - Superseded by #1360: #581, #587, #588, #639, #640, #641, #642, #749 and #782–#785.
  - Superseded by #1365: #410 and #378.
  - Superseded by #1358: #1314.
- **Still open: 86.** The triage left 88 open: 51 partial, 17 need Jason, 10 blocked, 6 not started and 4 time-gated. Afterwards the coordinator closed #1350 and #1366 as not planned, because Jason parked the Cortex planner and is keeping Hermes.

## Tracks, in order

Each track is the `execution_plan` sprint of the same id in `planning/backlog.yaml`.

- **T0, unblock.**
  1. Land the #1388 Cortex egress fix, then verify #1351 (re-embed), #1118 (vision) and #1346 (brief).
  - #1351, #1352 and #1363 are re-scope pending Jason: local-only except the planner, which stays on OpenAI through Hermes. Slack goes by turning off Hermes's Slack output, and Hermes's image moves to the local registry instead of being retired.
  2. Land the Crops/Planner web pin, then verify #1330, #1361, #1327 and #1328.
- **T1, live verification.** The code has shipped. What is left is one signed-in walk and one attended L01 run with the Stops. This covers 32 issues, including #1125–#1186, #1300 and #1332–#1358.
- **T2, bounded fixes.**
  - Dashboard: #1345 (soil targets migration), #1354 (`--cause-prep`), #1322 (phone separator) and #1329 (lab reads plantings).
  - Firmware and DB: #1277 (C1 pin), #1283 (25 h day), #1312 (replay rounding) and #1313 (aborted-OTA accounting).
  - Platform: #1389 (experiment CPU), #1363 (images) and #1010 (client IP).
  - Firmware rebase: #299 and #367 (rebase PR #1095), then #368, #369, #370, #390 and #386.
- **T3, planner A/B study (Hermes arm).** #1360 with Hermes as the AI arm: PR B, a 4-day dry run, then the study (about 60 days). It needs #1365 (band freeze) and #1389 (CPU fix) first.
- **T4, soaks.**
  - #1364 NTP: 2026-11-07.
  - #1346 brief cadence: 2026-11-07.
  - #1365 dry-out, 7 nights: 2026-11-13.
  - #1352 Slack-free 7 days: 2026-11-13.
  - #1165 first scheduled L01 run: today.
- **T5, hardware and replant (Jason).**
  - Sensors: #51 (CO2), #751 (south probe), #1184 (PAR) and #1183 (YINMIK on Wi-Fi).
  - Replant chain: #298 → #398 → #45 → #297, plus #52 and #1166 (fert tank).
  - Also: #1161 (hose run) and #1271 (backup misting).
- **T6, external owners and upstream.**
  - #1001 (agents#4468 and network-infra#602).
  - #1009 (network-infra#839, plus Jason's OTA password rotation).
  - #1113 (upstream Chromium).
  - #1397 (CI after the repo transfer; PR #1402, re-key PR #1421).
  - #31 (Jason: defer or retire).
- **Roll-ups:** #16, #75, #359, #430, #775, #1120 and #1325.

**Critical path.** #1389 (CPU fix) and #1365 (7 nights of dry-out, ending 2026-11-13) feed #1360. Then #1360 PR B, a 4-day dry run and the study (about 60 days). The longest dependency chain is physical: replant → #298 → #398 → #45 → #297.

## Per-issue disposition
| # | Title | Class | Closed now | Track | Blocked by (open) | Remaining / evidence |
|---|---|---|---|---|---|---|
| #16 | [C7][P2] Hardware program: commission only the sensing and equipment j | NEEDS-JASON |  | ROLLUP |  | Track children #45 #51 #52 #298 #398 #751 #1184 |
| #31 | [C8][P3] Close twin setpoint and actuator coverage gaps with generated | NOT STARTED |  | T6 |  | Select an oracle question or defer/retire with the replay-sandbox cut. |
| #45 | [C7]Commission soil and runoff feedback before enabling irrigation dec | BLOCKED |  | T5 | #398, #298 | After replant+targets, verify live probes and run feedback check |
| #51 | [C7][P3] Calibrate CO2 against a real reference and publish measuremen | NEEDS-JASON |  | T5 |  | Install new CO2 sensor and verify plausible live readings |
| #52 | [C7][P3] Define species-appropriate seasonal dormancy care from operat | NEEDS-JASON |  | T5 | #298 | Post-replant seasonal-care decision from observations (Dec recheck) |
| #75 | [C4][P2] Observability program: prove actionable health from source th | PARTIAL |  | ROLLUP |  | Criterion 3 coverage doc after #749/#1118 and replacement probes (#751,#51). |
| #297 | [C6]Add saturation alerts and a commissioned dispatcher-side drip-skip | BLOCKED |  | T5 | #45, #398 | Bind real targets/probes then enable alert + engine slot skip |
| #298 | [C7]Rebaseline actual crop, pot and probe topology before feedback con | NEEDS-JASON |  | T5 |  | Record per-position pot/substrate/probe/plumbing truth at the replant |
| #299 | [C6][P1] Preserve center-mister re-fire protection through topology an | PARTIAL |  | T2 | #390 | Rebase/re-cut #1095 onto engine firmware; add restart/manual-origin min-off cases; one live re-fire observation. |
| #359 | [C6][P1] Control program: truthful crop corridors before evidence-led  | PARTIAL |  | ROLLUP |  | Open children #367 #368 #369 #370 #386 #1365 |
| #367 | [C6][P1] Consolidate anti-chatter into one explicit dwell contract | PARTIAL |  | T2 | #390 | Rebase+merge #1095; show start/runtime parity on next release. |
| #368 | [C6][P1] Define shared sensor jump, flatline and contributor-integrity | PARTIAL |  | T2 |  | Jump/flatline detection + tests; doc degraded quorum vs SENSOR_FAULT; pair with #1274. |
| #369 | [C6][P3] Remove dead tunables and code from a verified consumer invent | PARTIAL |  | T2 | #390 | Firmware/dispatcher removal of retired tunables with net-negative entity count; #1278. |
| #370 | [C6][P3] Consolidate equipment conflict resolution into one pure arbit | NOT STARTED |  | T2 | #367 | All criteria, after #367. |
| #378 | [C6][P3] Decide corridor widths from fixed-target crop and resource ev | SUPERSEDED | yes |  |  | none — Superseded by #1365 |
| #386 | [C4][P2] Prove grow-light minimum-on behavior at the solar-window boun | PARTIAL |  | T2 |  | Capture one natural ON-before-cutoff episode with min-on expiry via /v1/lighting. |
| #390 | [C4][P1] Gate firmware releases on topology, cycling, runtime safety a | PARTIAL |  | T2 |  | Add topology/cycling/stale-outdoor deny checks; close #1273/#1274. |
| #398 | [C7]Replace stale soil threshold seeds with commissioned crop/substrat | BLOCKED |  | T5 | #298 | Append ledgered target correction with inverse after replant |
| #410 | [C6][P2] Measure realized solar-night dry-out before changing the cont | SUPERSEDED | yes |  |  | none — Criteria 1,3 accepted; 0 held-admission mode rows in 7d; superseded by #1365 |
| #412 | [C7][P3] Close the seasonal door screen only when measured night condi | DONE | yes |  |  | none — Closed: trigger/completion recorded, envelope held, no study deviation |
| #430 | [C6][P1] Simplify firmware only while preserving autonomous control an | PARTIAL |  | ROLLUP |  | Per-tier measured heap/traffic after #367 then #369/#370. |
| #581 | [C2]Complete the bounded AI-admission pilot and publish a decision | SUPERSEDED | yes |  |  | none — All children closed as superseded by #1360 (Jason accepted planner-ab report 2026-11-05); closed 2026-11-06. |
| #587 | [C1]Qualify lifecycle authority and blinded monitoring in containment | SUPERSEDED | yes |  |  | None here; work lives in #1360 (dry run + 30-pair study) — Jason 2026-11-05 ~21:45Z accepted planner-ab: close hot/dry campaign, keep data; live: 0 randomization/outcome/freeze/proof/selector rows, 1 safe closure (09-30); experiment-v2 pods capability_off; #1 |
| #588 | [C2][P2] Bind exact scientific/runtime identity and finalize one inter | SUPERSEDED | yes |  |  | None here; work lives in #1360 (dry run + 30-pair study) — Jason 2026-11-05 ~21:45Z accepted planner-ab: close hot/dry campaign, keep data; live: 0 randomization/outcome/freeze/proof/selector rows, 1 safe closure (09-30); experiment-v2 pods capability_off; #1 |
| #639 | [C1]Qualify the component executor, exclusivity and recovery behavior  | SUPERSEDED | yes |  |  | None here; work lives in #1360 (dry run + 30-pair study) — Jason 2026-11-05 ~21:45Z accepted planner-ab: close hot/dry campaign, keep data; live: 0 randomization/outcome/freeze/proof/selector rows, 1 safe closure (09-30); experiment-v2 pods capability_off; #1 |
| #640 | [C3][P2] Freeze and reconcile the first real assigned-day outcome and  | SUPERSEDED | yes |  |  | None here; work lives in #1360 (dry run + 30-pair study) — Jason 2026-11-05 ~21:45Z accepted planner-ab: close hot/dry campaign, keep data; live: 0 randomization/outcome/freeze/proof/selector rows, 1 safe closure (09-30); experiment-v2 pods capability_off; #1 |
| #641 | [C1]Complete one fresh attended physical baseline–target–baseline proo | SUPERSEDED | yes |  |  | None here; work lives in #1360 (dry run + 30-pair study); close draft PR #855 unmerged — Jason 2026-11-05 ~21:45Z accepted planner-ab: close hot/dry campaign, keep data; live: 0 randomization/outcome/freeze/proof/selector rows, 1 safe closure (09-30); experiment-v2 pods capability_off; #1 |
| #642 | [C2][P2] Activate and verify one randomized day 1 from the locked pilo | SUPERSEDED | yes |  |  | None here; work lives in #1360 (dry run + 30-pair study) — Jason 2026-11-05 ~21:45Z accepted planner-ab: close hot/dry campaign, keep data; live: 0 randomization/outcome/freeze/proof/selector rows, 1 safe closure (09-30); experiment-v2 pods capability_off; #1 |
| #749 | [C1]Qualify current three-probe readiness and explicit safety dependen | SUPERSEDED | yes |  |  | none — Jason 11-05 ~21:45Z accepted planner-ab report: close hot/dry campaign, no blinding, no #641 gate, drop OpenAI selector; replaced by two-arm study (#1399 e136a4fb, release 5196f955); experiment_v2 ran |
| #751 | [C7][P2] Replace the failed south climate probe and diagnose hydro tel | NEEDS-JASON |  | T5 |  | Install new south probe, verify vs neighbours; hydro back on Wi-Fi (#1183) |
| #775 | [C0]Campaign: trustworthy greenhouse outcomes → qualified pilot → evid | PARTIAL |  | ROLLUP |  | Close once #16, #31, #75, #359 and #390 are dispositioned. |
| #782 | [C0][P2] Finish the empirical inputs and frozen exploratory scientific | SUPERSEDED | yes |  |  | none — Jason 11-05 ~21:45Z accepted planner-ab report: close hot/dry campaign, no blinding, no #641 gate, drop OpenAI selector; replaced by two-arm study (#1399 e136a4fb, release 5196f955); experiment_v2 ran |
| #783 | [C1][P2] Qualify the complete current-login selector-to-frozen-analysi | SUPERSEDED | yes |  |  | none (byte-identical analyzer rerun carries into #1360) — Jason 11-05 ~21:45Z accepted planner-ab report: close hot/dry campaign, no blinding, no #641 gate, drop OpenAI selector; replaced by two-arm study (#1399 e136a4fb, release 5196f955); experiment_v2 ran |
| #784 | [C3][P3] Operate the full blinded assigned-day pilot with immutable cu | SUPERSEDED | yes |  |  | none — Jason 11-05 ~21:45Z accepted planner-ab report: close hot/dry campaign, no blinding, no #641 gate, drop OpenAI selector; replaced by two-arm study (#1399 e136a4fb, release 5196f955); experiment_v2 ran |
| #785 | [C3][P3] Reveal once, reproduce the frozen analysis and decide the pro | SUPERSEDED | yes |  |  | none — Jason 11-05 ~21:45Z accepted planner-ab report: close hot/dry campaign, no blinding, no #641 gate, drop OpenAI selector; replaced by two-arm study (#1399 e136a4fb, release 5196f955); experiment_v2 ran |
| #963 | [C4][P2] Bound moisture-exchange state and preserve structured consume | DONE | yes |  | #390 | None. — Live fw 2026.11.6.0938.36c7630c; controls.yaml char[256]+overflow guard; 170/170 climate_action_log rows since 09:53Z have object. |
| #1001 | [OWN] Finish Verdify transitions owned elsewhere: retire ns verdify-ed | BLOCKED |  | T6 |  | Delete verdify-edge after agents#4468; drop D-16 hosts after network-infra#602; update docs. |
| #1009 | [AUD] Sign greenhouse ESP32 firmware and flash only reviewed archived  | BLOCKED |  | T6 |  | UniFi tcp/3232+6053 readback; ota_password rotation receipt. |
| #1010 | [AUD] Finish trusted client IP attribution for successful verdify-mcp  | PARTIAL |  | T2 |  | One public off-cluster (orbit) call showing the real client IP. |
| #1097 | Keep greenhouse moisture-exchange telemetry within Home Assistant stat | DONE | yes |  |  | None. — Same evidence as #963; short action state live; HA registry entry disabled 10-26. |
| #1113 | security: grafana-image-renderer chromium CVE-2026-118333/118337 - bum | BLOCKED |  | T6 |  | Bump when upstream ships chromium >=156.0.8078.73 (v5.12.10 pin optional now). |
| #1118 | verdify-vision crop-health analysis step fails (exit 1) after B-POWER; | PARTIAL |  | T0 |  | Forward analyze stderr to logs; confirm runs after Cortex egress fix (next 15:00Z). |
| #1120 | [VD-E0] Program: dashboard.verdify.ai and the irrigation program | PARTIAL |  | ROLLUP |  | One Jason walk, one attended L01 run, physical checks, hardware, post-replant line bring-up. |
| #1125 | [VD-05] Build the form controls, edit drawer, water confirm dialog and | PARTIAL |  | T1 | #1143, #1135, #1164 | Live Stop all (Now, Water) and Run/Stop from Water: owned by #1143 item 5, #1135 DoD, #1164 item 2 |
| #1130 | [VD-10] Build the Graphs screen (SCR-02) with the metric picker and th | NEEDS-JASON |  | T1 |  | Jason opens /graphs/statistics once (30 rows) |
| #1132 | [VD-12] Build the Settings screen (SCR-05) with the edit drawer | NEEDS-JASON |  | T1 |  | Jason opens /settings once (also closes #1181 item 1) |
| #1135 | [VD-15] Build the Water screen (SCR-04): read views and Stop all water | NEEDS-JASON |  | T1 |  | Jason opens /water/history and presses Stop all water from Water |
| #1143 | [VD-26] Ship R1: put the read-only dashboard live at dashboard.verdify | NEEDS-JASON |  | T1 |  | Item 5: agent starts L01 via MCP, Jason taps Stop all water on Now (relay OFF <=30s) |
| #1156 | [VD-41] Add the irrigation write services and API-IRR-03 to -08, -12 a | DONE | yes |  |  | none — activity irrigation.schedule.create jason/dashboard 2026-11-05 18:19:28Z; irrigation_push rev3 confirmed 18:19:45Z |
| #1161 | [VD-46] Rework the mist path: membership, scheduled pulses, occupancy, | PARTIAL |  | T5 |  | On-site hose run with all Verdify valves closed, then read block reasons |
| #1162 | [VD-47] Remove the Home Assistant buttons, add manual_override and ret | PARTIAL |  | T1 |  | Daytime Clear during a fans burst (any operator/Claude allowed) + physical push buttons at panel |
| #1164 | [VD-50] Ship R3: deploy the irrigation platform and flash the engine f | PARTIAL |  | T1 | #1161, #1162 | L01 Run/Stop from Water with Jason; daytime Restart + Clear; #1161 hose run; #1162 push buttons |
| #1165 | [VD-51] Bring the clean lines online: L01 first, then L04, L07 and L09 | TIME-GATED |  | T4 | #1329 | First scheduled L01 run (~13:48Z today); L01 check note; L07/L09 bring-up after re-plant |
| #1166 | [VD-52] Bring the fertilizer lines online: L02 with L03, then L08 and  | BLOCKED |  | T5 | #1165, #1329 | Tank+injector setup, checks, enable fert lines, one scheduled feed each |
| #1177 | [VD-64] Run L01 now and stop any wet line through the bridge, ahead of | DONE | yes |  |  | none (carried to #1143) — orbit run 19:59:54Z/stop 20:00:21Z relay 6s/5s; activity rows 28/29; no all-lines stop -> carry #1143 |
| #1181 | [VD-68] Ship R1.1: put every read screen, the water reads and the MCP  | NEEDS-JASON |  | T1 |  | Jason opens /settings once |
| #1183 | [VD-70] Read the YINMIK hydroponic probe directly, without Home Assist | BLOCKED |  | T5 |  | Meter back on Wi-Fi + seal keys; live freshness check; PR2 HA removal |
| #1184 | [VD-71] Add a PAR light meter: hardware, firmware, ingest and DLI | NEEDS-JASON |  | T5 |  | Fit meter, then firmware+migration+ingest+DLI, in-cluster build/OTA, live checks |
| #1186 | [VD-73] Ship R4: put the Next screens live | PARTIAL |  | T1 | #1300 | Jason: /band preview, /water/history 90d, Back to automatic with page closed (#1300) |
| #1271 | firmware: give sensor_degraded a boot grace before timed L04 fallback  | NEEDS-JASON |  | T5 |  | Jason says the word -> re-enable the 3 backup-misting slots, read back enabled=t |
| #1272 | firmware-builder: real escaping and a generated-literal check for comp | DONE | yes |  |  | none — #1291 (266db77f) escaping + literal check; fw-build-57fe2a2e5 and fw-build-36c7630c1 logs show the credential check before sign. |
| #1273 | firmware release: pin step in the signed path, and sweep skip when liv | DONE | yes |  |  | none — #1291 pin step + sweep warning; pod pin file = live 36c7630c; mismatch alert 13997 resolved 20 min after flash by the pin. |
| #1274 | sensor-health sweep: fix false passes and false failures | DONE | yes |  |  | none — #1291 sweep fixes (provenance, errors=FAIL, Guru, uptime, water); gated 57fe2a2e and 36c7630c flashes. |
| #1275 | dispatcher: record a stage and raise an alert on blocked readbacks | DONE | yes |  |  | none — #1291 held:readbacks stage + stuck alert; live in ingestor since 11-03; no setpoint_delivery_stuck in 4 d. |
| #1276 | firmware: close wet relays in ota on_begin | DONE | yes |  |  | none (aborted-upload accounting is #1313) — #1290 ota.on_begin closes L01-L10+fog; carried by 57fe2a2e so the 36c7630c upload ran under it. |
| #1277 | c1 wire validation: move the firmware pin to the engine firmware | PARTIAL |  | T2 |  | Bump pin to 36c7630c or derive it from the accepted firmware pin |
| #1278 | retire the four equipment.irrigation_* registry rows | DONE | yes |  |  | none — Migration 288 applied 11-04 23:19Z; no open sensor_offline for equipment.irrigation_*. |
| #1283 | v_water_meter_daily may read a 25-hour day as complete with its last h | NOT STARTED |  | T2 |  | Migration: local-date day end; 25 h fixture test; fix slot-1 test assertion |
| #1300 | Back to automatic leaves a grow light off its intent when the Equipmen | PARTIAL |  | T1 |  | Operator presses Back to automatic with light off intent, closes page; expect via=server_resync activity row |
| #1312 | firmware-replay-diff: rounding lets up to 14 divergent rows pass at TH | NOT STARTED |  | T2 |  | Fail on any divergent row at threshold 0; unrounded compare; print count (both scripts) |
| #1313 | firmware: runtime and drip accounting after an aborted OTA upload | NOT STARTED |  | T2 |  | Fix both + native test; ships with next flash |
| #1314 | mv_zone_band_grade has not refreshed since 2026-06-12, so Statistics'  | SUPERSEDED | yes |  |  | none here; #1358 open for Jason definition confirm — Folded into #1358; migration 290 (release 1 b050f191) -> v_daily_scorecard day split 29/30, night 30/30 days. |
| #1322 | dashboard: chart corner labels overlap the band edge; stray separator  | PARTIAL |  | T2 | #1354 | Fix phone status-line separator at wrap; item 1 closes with #1354 |
| #1325 | [Epic] Dashboard wave 2: Jason's acceptance feedback on the redesign ( | PARTIAL |  | ROLLUP |  | Live checks, web pin, Cortex egress fix, Cortex cutover, soaks. |
| #1327 | [W5] Model plantings on the location tree and serve them from the oper | PARTIAL |  | T0 | #1330 | After Crops web pin: operator test planting create/remove via BFF shows in History |
| #1328 | [W5] Let agents record, move and harvest plantings through MCP | PARTIAL |  | T0 | #1330 | Named-agent live test: plant test variety in hydro 1-10, verify API/Crops page/activity_log actor, then remove |
| #1329 | [W5] Reconcile greenhouse locations and current plantings with the phy | PARTIAL |  | T2 |  | Make lab publisher read plantings/occupancy, repin lab publisher, verify lab == /v1/crops/locations |
| #1330 | [W5] Add a Crops section: hydro slot grid, wall shelves and planting h | PARTIAL |  | T0 |  | Land the in-flight web pin, then run the live plant/remove check; Jason confirms the nav order. |
| #1332 | [W5] Explain every setting: what it does to the controller and what ra | PARTIAL |  | T1 |  | Repin+run lab-publisher so lab text matches; Jason live read of 3 settings |
| #1333 | [W5] Define the Settings filters and states in the UI, and explain or  | PARTIAL |  | T1 |  | Live walk desktop+phone: definitions, not_confirmed semantics, House temp high links anchors |
| #1334 | [W5] Research the band math and design a plant-agnostic band editor wi | PARTIAL |  | T1 |  | Live: change night target in preview, save, band_temp_target_mid confirmed within 2 dispatcher cycles |
| #1335 | [W5] Give each light circuit a findable Auto / On / Off control that h | PARTIAL |  | T1 |  | Live: Main On via Equipment holds across controller decision until timer/Auto; Auto returns within 30 s; equipment mode/auto_reason |
| #1336 | [W5] Model one physical line with clean and fertilized schedules; give | PARTIAL |  | T1 |  | BFF live check (six circuits); South-mister fertilized schedule once South enabled |
| #1337 | [W5] Redraw Now's band gauges as one track with a green band box, and  | PARTIAL |  | T1 |  | Screenshot regeneration and the live check on the deployed page. |
| #1338 | [W5] Keep the Now headline steady and move the out-of-band detail into | PARTIAL |  | T1 |  | Screenshots plus the live midday-vent check. |
| #1339 | [W5] Replace codes on Now with plain labels and add a shared glossary  | PARTIAL |  | T1 |  | Screenshots, the live check, and Jason's call on the primary water limit. |
| #1340 | [W5] Give Running and next room for its labels and add Main and Grow l | PARTIAL |  | T1 |  | The live evening check and screenshots; optionally an API-05 intent lane for past intent differences. |
| #1341 | [W5] Show line names everywhere and remove the L01/L02 relay codes fro | NEEDS-JASON |  | T1 |  | Operator or Jason walk on dashboard.verdify.ai, then close. |
| #1342 | [W5] Add Main and Grow light controls to Now's quick actions | PARTIAL |  | T1 |  | The daytime live on/auto check with the Lutron readback. |
| #1343 | [W5] Allow a one-time run of any line, misters included | NEEDS-JASON |  | T1 |  | Attended: Center mister (L04) run once with Schedule off -> ledger cause=run_now on L04 |
| #1344 | [W5] Explain VPD control, Manual and Fallback on Water; show VPD contr | PARTIAL |  | T1 |  | The live check on the South mister. |
| #1345 | [W5] Remove plant-specific copy and make soil-moisture targets configu | PARTIAL |  | T2 |  | A migration and a service change for the soil targets and the slot label rename, then live readback. |
| #1346 | [W5] Write a greenhouse snapshot with the local Cortex Qwen model and  | PARTIAL |  | T0 |  | 24h cadence (falls 2026-11-07 12:35Z); web pin live; number-match sample, stale/error fixtures, MCP parity; Jason spot check; fix plan-read ValueError |
| #1347 | [W5] Make the schedule timeline readable: daylight band on every lane, | NEEDS-JASON |  | T1 |  | Visual QA screenshots and Jason's walk. |
| #1348 | [W5] Separate irrigation runs from VPD mist and fog everywhere | PARTIAL |  | T1 |  | BFF live check of /v1/irrigation/runs?purpose=irrigation\|climate |
| #1349 | [W5] Redesign Water cards for clear line and group delineation and rep | NEEDS-JASON |  | T1 |  | Jason's D-UX-06 call recorded in decisions.md, plus the screenshots and his walk. |
| #1350 | [W5] Replace the Hermes/OpenAI planner with one deterministic call to  | CLOSED (not planned, coordinator: Cortex parked) | yes |  |  | none; Jason parked the Cortex planner and keeps Hermes — cortex_planner + mig 294 via #1388 a7e0b49f, release 5196f955; verdify-cortex-planner shadow running; planner_inference_runs=0, planner_pack=0; hermes-iris + verdify-planner still Running; egress fix  |
| #1351 | [W5] Audit and remove Verdify's external dependencies (local-only goal | BLOCKED |  | T0 | #1352 | re-scope pending Jason: local-only except planner (OpenAI via Hermes); Cortex egress fix + re-embed; retire Hermes/OpenAI planner (#1350); drop outbound openai  |
| #1352 | [W5] Decommission Slack from Verdify: alerts on the dashboard, paging  | TIME-GATED |  | T4 |  | re-scope pending Jason: turn off Hermes Slack output; 7 days zero slack_ts (to ~2026-11-13); retire Hermes (#1350); remove KSOPS verdify-slack in agents; test c |
| #1353 | [W5] Show 72 h of history and 72 h of forecast on Graphs by default | PARTIAL |  | T1 |  | The live side-by-side check against Grafana; an optional phone default and `ahead=`. |
| #1354 | [W5] Move chart labels out of the plots and use one chart vocabulary a | PARTIAL |  | T2 |  | The `--cause-prep` fix on Water schedules and the screenshot set. |
| #1355 | [W5] Make event markers on Graphs show what each event is and what it  | PARTIAL |  | T1 |  | The live 72 h screenshot QA. |
| #1356 | [W5] Redesign the equipment lanes: shared time axis, solid bars, group | PARTIAL |  | T1 |  | The live screenshot QA at 24 h and 72 h. |
| #1357 | [W5] Add a Lighting tab to Graphs: light state, outdoor lux and thresh | PARTIAL |  | T1 |  | The two-day live spot check and the timing. |
| #1358 | [W5] Research and fix band compliance: grade time in band against the  | NEEDS-JASON |  | T1 |  | Jason confirms definition; cross-surface live screenshots (Statistics/Now/Graphs/Grafana site-home); confirm CI grep guard |
| #1359 | [W5] Research the experiment system's current state and how to measure | DONE | yes |  |  | none — docs/research/w5/planner-ab.md merged #1380 d4c8dae1; Jason accepted all decisions 11-05 21:45Z; #1360/#1361 updated via #1399 e136a4fb. |
| #1360 | [W5] Run an AI-planner-versus-baseline study end to end by extending t | PARTIAL |  | T3 | #1365, #1389 | Hermes is the AI arm; PR B then dry run and study after #1365 and #1389 |
| #1361 | [W5] Add a Planner section: plan history, cost, experiments, arm calen | PARTIAL |  | T0 | #1360 | The web pin and the live viewer check; results after #1360. |
| #1362 | [W5] Daily energy goes negative: daily_summary.kwh_total has read belo | DONE | yes |  |  | None (physical: reversed Shelly ch1 clamp noted by Jason, not acceptance) — PR #1369 5402e88d mig 291 (release 1 07:47Z); 0 negative kwh_total rows; 10-20 3.60=3.60 and 11-04 26.54=26.54 vs v_energy_daily |
| #1363 | [W5] Pull every Verdify image through the local registry (no direct Do | PARTIAL |  | T2 |  | re-scope pending Jason: Hermes image via local registry; Hermes retire (#1350); descheduler custody+repin (#802); imageIDs clean after reschedule; firmware-buil |
| #1364 | [W5] Use the estate's local NTP for the greenhouse controller instead  | TIME-GATED |  | T4 |  | 24 h check after 2026-11-07 09:55Z; network-infra chronyc clients readback of 192.168.10.111 |
| #1365 | [W5] Research sprint: a VPD band the greenhouse can actually hold, and | TIME-GATED |  | T4 |  | Build nightly dried-out flag in 290 rollup; amend ADR-0003/0004; file follow-ups; 7-night live check |
| #1366 | [W5] Research the planner's 60K-token input pack: what the Cortex plan | CLOSED (not planned, coordinator: Cortex parked) | yes |  |  | none; Jason parked the Cortex planner and keeps Hermes — Research posted; decision recorded in decisions.md; pack.py + test_cortex_planner_pack.py on main (#1388); docs/planner/pack-v1.md missing; planner_pack 0 rows live. |
| #1370 | [W5] Turn the lights on when the cameras see someone in the greenhouse | PARTIAL |  | T1 |  | Observe one real walk-in: lights on <=5 s, back to Auto 15 min after last sighting, cause logged |
| #1381 | [W5] leak_detected raises false positives: find the trigger and stop t | DONE | yes |  |  | none — #1385 (1863ccf7) attended-flow rule, replay test of 10-26/27/29 = no alert, unattended still raises; live since release 1; no leak alert since 10-29. |
| #1389 | experiment-v2 lifecycle/freezer/selector CPU-throttled >50% (limits 25 | NOT STARTED |  | T2 |  | profile idle loop/backoff; right-size requests/limits |
| #1391 | [W5] heap_pressure_warning alert cannot resolve until reboot; 2026-11- | DONE | yes |  |  | none — #1393 (cc9dac2e) in release 1; successor 13989 resolved 08:01:50Z with heap_min_free_kb 2.5; tests cover new-low raise. |
| #1397 | Move CI, image builds and pin automation to GitHub Actions on self-hos | BLOCKED |  | T6 |  | Transfer -> rekey Argo -> runners + ci.yml -> ruleset swap -> 5 GHA PRs -> GHA digest deploys -> remove native wiring |
