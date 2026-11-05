# Decisions: Verdify dashboard and irrigation program

This file is the decision record for the dashboard.verdify.ai design and the irrigation program. It
holds Jason's direction, his answers on the 27 irrigation decisions, and the few decisions still open.
To answer an open decision, set **Your call** to Accept, Change or Discuss, add a note, then commit it or
tell the agent working the design. It replaces the earlier claude.ai working doc, which was dropped so the
record survives Claude account switches.

Status: Living record (started 2026-11-01). Related: [README](README.md), [product](product.md),
[screens](screens.md), [architecture](architecture.md), [irrigation](irrigation.md),
[execution plan](execution-plan.md).

## Direction (Jason, 2026-11-01)

> "Overall, the design is far too complicated with far too many holds, safety gates, etc. We need to
> massively simplify. We can take risk. This environment is our experimental greenhouse. Priority is on
> forward progress, not on a bunch of constraints which slow us down. Simplify the design and execution
> plan and incorporate my feedback."

What the v2 design keeps and cuts:

- **Keeps:**
  - one device writer (the ingestor);
  - Authentik sign-in;
  - who-changed-what attribution;
  - device readback shown next to requested values;
  - the firmware protections that already exist: the SAF-4 daily water ceiling, relay watchdogs and
    leak detection.
- **One control rule.** Last write wins. A setting marked manual is skipped by Iris. There are no holds
  with expiries, no precedence tiers, no step-up and no typed confirmations.
- **Two roles.** Operator (full control) and viewer (read-only).
- **Agents.** Iris and every MCP agent can read and write, irrigation included, within registry
  min/max.
- **Irrigation.** Every connected clean and fertilizer line can be scheduled from the dashboard and by
  agents. There is no commissioning paperwork, no bench test, no calibration gate and no OTA cadence
  rule.
- **Cut to later:**
  - experiments UI;
  - research depth;
  - replay sandbox;
  - Iris memory governance;
  - dark theme;
  - command palette;
  - live server-push updates (SSE; polling first).

## Earlier decisions (Jason, 2026-11-01)

| Topic | Decision |
| --- | --- |
| Hostname | The dashboard lives at **dashboard.verdify.ai**, behind Authentik sign-in. |
| Look and feel | Continue the weather.vallery.net style, theme and uPlot graphing. |
| Irrigation priority | Bring all clean-water and fertilizer lines online, with scheduling, monitoring and enforcement across firmware, DB, API, MCP and dashboard. Set schedules from the dashboard, see watering minutes by zone, and let agents adjust schedules and monitor through MCP. |
| Misters | Each mister zone can be associated with VPD control or a schedule independently. Center keeps VPD control, and schedules are added for misters. |
| Tracking | Decisions and tracking live in repo markdown, not in claude.ai docs, because Claude accounts will change during implementation. |

## Irrigation decisions (D-IRP-01 to D-IRP-27)

Jason answered all 27 on 2026-11-01. He accepted 16 as written and changed 11; the last column says how
v2 applies each answer.

| ID | Decision | Recommendation | Your call | Your notes | How v2 applies it |
| --- | --- | --- | --- | --- | --- |
| D-IRP-01 | Irrigation architecture | Hybrid: the controller stores and runs the compiled schedule offline; the platform authors it, pushes it through the single writer and verifies runs. | Accept | | As recommended. |
| D-IRP-02 | Failure doctrine | Clean water fails open within existing device caps; fertilizer fails closed. | Accept | | As recommended, without review-date paperwork. |
| D-IRP-03 | Survival fog vs drips | SAFETY_COOL fog preempts clean drips; a drip yields to climate wetting after 15 continuous minutes. | Accept | | As recommended. |
| D-IRP-04 | Survival fog and the daily ceiling | Survival fog is exempt from the SAF-4 ceiling and the soft budget, but still counted. | Accept | | As recommended. |
| D-IRP-05 | Scheduled vs VPD misting | Alternate at pulse boundaries; a VPD emergency outranks schedules; a VPD reserve within the duty cap. | Accept | | As recommended. |
| D-IRP-06 | Cap defaults | SAF-4 360 gal/day; duty caps center 120, south 45, west 45 min; wall drip 60 and center drip 30 min/day; spacing 30 min; scheduled-mist RH ceiling 90%. | Accept | | As recommended. |
| D-IRP-07 | What a wall clean schedule needs | A one-time physical check of the line. | Accept | | A note or checkbox, not a tiered workflow (charter). |
| D-IRP-08 | What a center mister schedule needs | A one-time nozzle, coverage and walkway check. Center VPD control stays on. | Accept | | A note or checkbox, not a workflow (charter). |
| D-IRP-09 | Scheduled-mist rails | Dew gate on, RH gate on, vent gate off, soft budget respected. | Accept | | As recommended. |
| D-IRP-10 | Mist when occupancy is unknown | Wait unless walkway-clear evidence exists. | Change | There should be no hold on mist unless we have confirmed occupancy from Frigate. Don't risk a false failure. | Mist holds only on a confirmed-occupied signal. Unknown or missing occupancy means mist runs. |
| D-IRP-11 | Mist now | Only as a one-off scheduled window on a schedule-member zone. | Accept | | As recommended. |
| D-IRP-12 | Vanda fallback program | Ship a fallback program for when VPD is untrusted. | Accept | | As recommended, without an observe period. |
| D-IRP-13 | South VPD source | Wait for #751. | Change | System should be resilient to specific probe outages and should leverage peer probes. The greenhouse is small and variance from probe to probe is small. | A zone with a probe out falls back to peer probes, then the house average. There is no wait. |
| D-IRP-14 | Fertilizer scope | Wall fert now; center fert after connection; mister fert paths fenced. | Change | Don't artificially constrain, just enable the schedules for fert, even if sensors are offline. | Every physically connected line, fertilizer included, can be scheduled. The firmware fert hard-block becomes a per-line enable. Schedules run with sensors offline. |
| D-IRP-15 | Flush after a cancelled feed | One journaled clean flush after a cancelled feed. | Accept | | As recommended, except that a cancel caused by disabling the line has no flush. A leak no longer cancels a feed (Q-05). |
| D-IRP-16 | In-place fertilizer test runs | Bench tests first; limited attended tests later. | Change | No bench test needed. Fast path, simplify. | Removed. Fert lines go straight to scheduled use once enabled. |
| D-IRP-17 | Leak latch | Latch drips on a leak; expires after 24 h. | Accept | | Superseded by Round 2 #3 and Q-05: no latch and no force-off. `leak_detected` raises the leak alert only; the engine release drops it from every fog, mister and drip force-off and from the irrigation cancel (VD-45, VD-46). |
| D-IRP-18 | Flow enforcement | Only after meter calibration. | Change | Meter is already calibrated. | Whole gallons per run and per day from day one, informational only. No per-run flow flag or rate (Round 2b B, Q-10). |
| D-IRP-19 | Mister watchdog | Limit = commanded pulse + 30 s; VPD-member faults back off before latching. | Accept | | As recommended (a firmware fix). |
| D-IRP-20 | Commissioning review dates | 365 days, advisory. | Change | Fast path here. no artifical validation timelines and calibration | No review dates and no calibration requirement. |
| D-IRP-21 | Agent authority over water | A dedicated agent audience with envelopes; Iris read-only. | Change | Iris is not read only. Dedicated irrigation MCP path is fine, all agents should be able to write. Simplify. | One irrigation MCP surface for every agent, Iris included, that reads and writes within registry min/max. Each write is attributed to its agent. |
| D-IRP-22 | Caretaker water limits | Presets and per-day limits for caretakers. | Change | | Removed. A caretaker is an operator or a viewer. |
| D-IRP-23 | OTA plan | Prep image and an entity-diet week. | Change | No artifical constraints. | No cadence or week rules. Firmware ships when ready through the existing deploy tooling. |
| D-IRP-24 | Home Assistant RUN CLEAN buttons | Keep until dashboard presets are live. | Change | No home assistant controls or integration, decomission anything that touches home assistant. Home assistant is only used to get occupancy data and control the lutron switches. | Every Home Assistant touchpoint is removed except occupancy data, Lutron switch control and, until the direct reader (VD-70), the hydroponic probe read (Round 2 #5). The RUN CLEAN buttons go in the engine release; until then the L01 bridge presses the wall button through the ingestor's ESPHome session, not Home Assistant. |
| D-IRP-25 | Hardware | DS3231 RTC and a physical STOP button. | Change | No, just use NTP, keep it simple. | No RTC and no STOP button. NTP only. |
| D-IRP-26 | Experiments vs water | Refuse mister changes while an experiment hold is armed. | Accept | | Dropped in Round 2 #2: nothing is refused while an experiment is armed; each write carries the experiment id. |
| D-IRP-27 | WiFi reboot timeout | Keep 300 s. | Accept | | As recommended. |

## Round 2 decisions (Jason, 2026-11-01)

Jason answered these in chat on 2026-11-01. They override anything earlier in this file and in the
design docs.

| # | Decision | Jason's answer | How it is applied |
| --- | --- | --- | --- |
| 1 | The controller had taken no setpoints since 2026-10-19 because the #433 bounded writer halted. Clear it? | Yes | Done 2026-11-01 22:07Z. The halted run's state and approval were archived (renamed, not deleted). Autonomous staging then delivered the current plan: 42/42 values confirmed and 43 `setpoint_changes` rows confirmed. A program issue replaces the halt gate with plain convergence. |
| 2 | D-IRP-26: refuse mister changes while an experiment is armed | Drop it for now | Nothing is refused during experiments; changes are logged as deviations. Revisit with the experiments UI. |
| 3 | D-IRP-17 leak latch: the "leak" signal is the shared site meter (hose, sink, hydro) | Alert only, no latch | `leak_detected` raises an alert. No drip latch. |
| 4 | Wall drip L01 has not run since 2026-05-29 | Agree, just get it done | L01 is the first line live and is scheduled first. A run-now bridge for the existing ESPHome `btn_wall_clean` through the ingestor ships ahead of the firmware engine. |
| 5 | Removing the Home Assistant reads drops the hydroponic probe (YINMIK `hydro_*`) | Keep the hydro data; include it in scope | The HA hydro read stays until a direct reader replaces it. A program issue adds the direct reader. |
| 6 | Outdoor data (Tempest) source | The firmware already reads it via UDP | Confirmed: the ESP32 listens for Tempest broadcasts on UDP 50222 (`firmware/greenhouse/external_sensors.yaml`), and the ingestor maps `tempest_*` entities from the ESP32. No API is needed; remove the HA `tempest_sync` remnant. |
| 7 | What sits between the fert master valve (L03) and the wall fert drip (L02) | A tank with an injector | L02 feeds come from the tank through the injector. L03 opens the injector line. |
| 8 | Dashboard edits default to manual | Yes | Applied (resolution rule R3). |
| 9 | Iris's planner path stays unchanged at first; agents write through `set_setting` | Yes | Applied (resolution rule R5). |
| 10 | DLI has had no source since July | Agree. Need a new light meter | DLI is out of the first build. A hardware issue adds a light (PAR) meter and wires it into the firmware, ingest and DLI. |
| 11 | ADR-0012, the dashboard architecture | Accept | ADR-0012 status becomes accepted. |
| 12 | Merge the design-docs PR before filing issues | Yes | Changed in how it is applied (2026-11-02): the issues link to the design at the docs PR's head commit, so every link resolves before the merge. The merge waits for the B-POWER freeze to clear and for green CI; the PR first gains the issue numbers. |

### Round 2b (Jason, 2026-11-01)

These are answers to questions raised by the live-data audit.

| # | Decision | Jason's answer | How it is applied |
| --- | --- | --- | --- |
| A | Occupancy source: the MQTT Frigate topic has never reported a person in 60 days | Yes: use the Home Assistant occupancy sensor | The HA occupancy sensor (Frigate-backed) is the only input for the mist hold. The unused MQTT occupancy path is removed. |
| B | The water meter resolves whole gallons (one pulse per gallon, 60 s updates) | Agree | Water is reported in whole gallons per run and per day, with no per-run flow flags. A higher-resolution meter is optional later. |
| C | Alert noise: 33 open; `soil_sensor_offline` fired more than 1,000 times in 30 days | Agree | A cleanup issue auto-resolves alerts for long-offline probes and deduplicates them. Every surface uses one open-alert count. |
| D | Raw retention: climate 365 days, diagnostics 180 days, readback 90 days | Accept | Graphs beyond retention show daily summaries and say so. |
| E | Mister fert paths L08 and L10; center drips L05 and L06 | L08 and L10 are plumbed. Center drips may be used later but are not used now; they are reserved for the future | L08 and L10 join the fertilizer bring-up. L05 and L06 stay schedulable but are reserved, with no bring-up now. |

## Open decisions

The adversarial audit raised Q-01 to Q-10, and drafting the issues raised Q-11 to Q-14. So the final pass and
the issue filing could go ahead, each one has an **applied default**: the docs and the issues now follow it.
Q-05 and Q-10 follow your Round 2 #3 and Round 2b B answers word for word. Q-11, Q-13 and Q-14 need your
authority, so their default is to wait for you. To change one, set **Your call** to Change and add a note;
the issue it touches is named, so the change is one edit there.

| ID | Decision | Applied default | Issues | Your call | Your notes |
| --- | --- | --- | --- | --- | --- |
| Q-01 | **Ingestor heartbeat.** The setpoints pipeline age (Round 2 #1) and stale climate data cannot tell a dead ingestor from a controller that stopped reporting. | Add the heartbeat: one `system_state` row every 60 s from the ingestor's own loop, stale after 180 s, no migration. The banner names which side is down, and at 3 a.m. it says the controller is running on its own. | VD-54 | Accept | |
| Q-02 | **Iris and manual settings.** Product refused Iris's `set_tunable` on manual items and gave Iris no `set_setting`; architecture left Iris's path as it is and gave Iris `set_setting`. | Iris's path stays exactly as it is (Round 2 #9): `set_plan` and `set_tunable` are unchanged, the dispatcher keeps the person's value on a manual setting, and a MANUAL SETTINGS section in Iris's context names them. Every agent, Iris included, gets `set_setting` (D-IRP-21). It refuses Iris with `manual` on a manual item and lets anyone else's write win. | VD-58 | Accept | |
| Q-03 | **Per-run schedule skips.** | Cut. Pausing a line until after the run covers rain and work days. Per-date skips can come back as program data if pause proves too coarse. | VD-17, VD-37 | Accept | |
| Q-04 | **Forecast action engine.** The ingestor's `forecast_actions` task changes settings today through the NOTIFY fast path, unattributed, and its plan rows read as Iris's. | Keep it and attribute it: its values read "Forecast rule · system" and apply through the next normal dispatcher pass. | VD-65 | Accept | |
| Q-05 | **Live leak interlock.** Today's firmware forces fog (survival fog included), the misters and the drips off while `leak_detected` is up. That signal is the one site meter, which also counts the hose, sink and hydro. | Alert only, as Round 2 #3 says. The engine firmware drops `leak_detected` from the fog, mister and drip force-offs and from the irrigation cancel. Holding Verdify valves closed cannot stop water that is already bypassing them, and hand-watering on a hot afternoon must not block survival fog (D-IRP-10: no false failures). | VD-45, VD-46 | Accept | |
| Q-06 | **The first named agent.** | Changed by Jason on 2026-11-02: agents are MCP clients, in or out of the cluster, each with its own bearer key at `https://mcp.verdify.ai/mcp` (`--add-mcp-client`; no service-side pre-step). The first client is **orbit**. Off-cluster clients need no Cloudflare Access token; VD-59's NetworkPolicy still admits in-cluster agents. | VD-23, VD-59 | Changed | orbit first |
| Q-07 | **Authentik flow tier for the dashboard.** | Member tier: a caretaker enrols a second factor at first sign-in. | VD-23 | Accept | |
| Q-08 | **Migration delivery.** | Keep the per-file `SUCCESSOR_NNN` registration and the receipt re-seals for M1 to M6 and the light meter's migration (VD-71). That is less work than changing the runner. | VD-30, VD-66 | Accept | |
| Q-09 | **Grow-light intent beside the Lutron state.** The controller's switches and the Lutron readback write the same `equipment_state` key. | Split them: the controller's switches map to `grow_light_*_intent`, so Equipment shows both and the readback stops interleaving. | VD-48 | Accept | |
| Q-10 | **Per-run flow flag.** | None, as Round 2b B says. Runs and days show whole gallons. `no_flow`, `flow_flag` and the per-run gal/min go; a drip run that moved no water reads "0 gal". | VD-15, VD-40 | Accept | |
| Q-11 | **Where the ESP32 image compiles for R3 and the PAR meter OTA.** `make firmware-deploy` compiles on the host that runs it, and estate policy keeps CPU-heavy builds such as an ESPHome compile off the Mac unless you grant a task-scoped exception. The in-cluster path, `scripts/firmware-release-job.py`, builds and verifies without your sign-off and flashes only with it, but its preflight, post-OTA sweep, rollback and last-good archive are run by hand from the runbook. | R3 asks you before it runs. No exception is assumed, and nothing compiles on the Mac until you answer. Recommendation: build in the cluster with `firmware-release-job.py`, which needs no exception, and run the preflight and sweep from the runbook. If you prefer `make firmware-deploy`'s automatic preflight, sweep and rollback, grant a scoped exception for its one compile at R3 and at the PAR OTA, a few CPU-minutes each. | VD-50, VD-71 | Change | In the cluster, same as always. If you need to use the Mac, that is fine as well. |
| Q-12 | **Scorecard day and night time in band.** `mv_zone_band_grade` keeps 3 days, so 27 of the scorecard's 30 rows show a dash for the day/night split. `daily_summary` has whole-day temperature and VPD compliance for every day. | The dash, with the reason "band grade kept 3 days" (VD-56 as written). Recommendation: show whole-day time in band for all 30 days and the day/night split for the last 3, with no new table; add a persisted day/night rollup later if night compliance matters week to week. | VD-56, VD-10 | Accept the recommendation | Whole-day time in band for all 30 days from daily_summary; the day/night split for the last 3 days. |
| Q-13 | **Older open issues that overlap this program.** #350 (commissioned wall feed) and #434 (center-only mist, wall-only fertigation) contradict D-IRP-14, D-IRP-16 and D-IRP-20. #297, #298 and #398 (soil feedback and topology) sit outside this program. | The epic links all five and closes none. Recommendation: close #350 and #434 as superseded by the program epic, with a comment naming those decisions; leave #297, #298 and #398 open. | VD-E0 | Change | Close #350 and #434 (closed 2026-11-02). |
| Q-14 | **The ingestor's unused MQTT login after VD-48.** Removing `verdify-mqtt` and its ACL block on the HAOS broker retires a credential and needs a broker restart, which drops Frigate's MQTT client until it reconnects. | The login stays, as HA-09 leaves `ha-gap-backfill`'s. Nothing is revoked until you ask. Recommendation: once VD-48 is live and no `verdify-mqtt` session remains, one attended jvallery/homeassistant change removes the login and its ACL and confirms Frigate reconnects. | VD-53 | Change | If it isn't used, retire. |

## Log

| Date | Change |
| --- | --- |
| 2026-11-01 | Record started; irrigation answers ported from the working doc; simplification direction recorded. |
| 2026-11-01 | Round 2: Jason answered 12 decisions. The halted writer was cleared at 22:07Z, and the controller is following the plan again (42/42 confirmed). |
| 2026-11-01 | Adversarial audit recorded in [audit](audit.md): 189 findings, 188 confirmed or partial (42 high, 103 medium, 43 low) and 1 refuted; the file owners applied the fixes and the issue plan was re-validated (70 issues, 115 edges, all checks pass). Open decisions Q-01 to Q-10 added. D-IRP-26, ADR-0012, the hydroponic probe data, the L03/L02 supply and the L05, L08 and L10 plumbing were already settled in Round 2 and 2b. |
| 2026-11-02 | Defaults applied for Q-01 to Q-10 so the final pass and the issue filing could go ahead; Jason can change any of them. Q-05 and Q-10 follow Round 2 #3 and Round 2b B word for word. Round 2 #12 now links the issues to the docs PR's head commit, because the B-POWER freeze blocks merges. |
| 2026-11-02 | Round 3 consistency check: the D-IRP-15, 17, 18, 24 and 26 cells now say how the later rounds apply them; Q-06 and Q-08 wording tightened. |
| 2026-11-02 | Issue drafting raised Q-11 to Q-14; each has a default, and the three that need Jason's authority wait for him. The plan now has 71 issues (VD-73, the R4 release, is new) and 147 edges. |
| 2026-11-02 | Filed the program: epic [VerdifyConsultancy/verdify-platform#1120](https://github.com/VerdifyConsultancy/verdify-platform/issues/1120) and 71 issues across verdify-platform, jvallery/agents, jvallery/network-infra, jvallery/monitoring-stack and jvallery/homeassistant, linking the design at PR [#1119](https://github.com/VerdifyConsultancy/verdify-platform/pull/1119) head `af8fe5db0`. |
| 2026-11-02 | Jason answered Q-01 to Q-14: Q-11 in the cluster as always with the Mac allowed when needed; Q-12 whole-day time in band for 30 days; Q-13 close #350 and #434 (closed); Q-14 retire the login once unused; every other default accepted. W1 implementation started. |
| 2026-11-02 | Jason's answers during build: keep the pace and spend no tokens on unnecessary checks or repeated work; he attends the first engine flash and the first L01 run; orbit is the first MCP client (Q-06 changed); Pi-hole's local name for the dashboard follows the root lane's broker fix, and no app password comes from this program. UI defaults accepted: Graphs '+ Setting' leaves out read-only rows; other people read by username or source and only the signed-in person by display name; Planner health shows the next row plus the five newest. M2b is filed as VD-74 (#1233), migration 284, after VD-38's 283. The plan now has 72 issues. |
| 2026-11-03 | Jason's answers before and during the first overnight run: flash the engine firmware without waiting for him, safely with rollback, replacing "he attends the first engine flash"; no water runs until he is present (the first L01 run stays attended). With the engine on, a run with no minutes stays `invalid_request`, so an L01 run must send minutes. Iris's growth in prompt and tools is accepted, compared against plan-cycle cost after release. The `high_wind` forecast rule is retired ("it's an indoor greenhouse") and `sw_economiser_enabled` goes on as a manual setting. That rule's stated purpose was to close the vents, but turning the economiser off removes the gate that can block venting, so it did the opposite. |
| 2026-11-03 | Delivery facts: six stacked issues (VD-33, VD-40, VD-41, VD-42, VD-60 and VD-65) landed as one PR, #1259, to save CI rounds and reach the flash sooner, and VD-58 with VD-61 as #1261 likewise. The flash uses the signed in-cluster builder and independent flasher (`scripts/firmware-release-job.py`), which fits Q-11, with the running release as the signed rollback. Production-sized p95 tests are named `perf_*`, so they are not collected in CI and run with `pytest -o python_functions='perf_*'`; the numbers recorded in native qualification stay asserted. |
| 2026-11-04 | Jason: "There is no hardware thermostat" and "Mark the missing thermostat as accepted" (#1296). Accepted as is: no physical thermostat, no fail-warm firmware change; in a sensor fault or with a dead controller nothing heats the house. `VerdifyControllerDark` (monitoring-stack#1382) is the remaining warning. The firmware comments and docs now say so. The same day L01 ran for the first time since May (121 s, 5 gal, outcome ok) and dashboard writes went on. |
| 2026-11-04 | Jason's answers after the closure pass: (1) "Yes, retire the checks": the live checks that could only be seen on the pre-engine firmware are retired, because the engine flash of 2026-11-03 12:35:47Z closed their window and the code and CI tests for them passed. They are #1155 acceptance 7 (the pre-engine read), #1156 acceptance 8 (its before-the-OTA part; a schedule created through API-IRR-03 or MCP still has to be shown), #1162 item 3, #1164 item 7 and #1177 items 1 to 6. (2) YINMIK (#1183): "We will do a direct decode once I get it back on wifi": decoding from the meter's own data points is accepted, and its live check waits for the meter, offline since about 2026-11-02 02:37Z, to be back on Wi-Fi. (3) The live walk in the browser profile that runs the Claude extension is parked ("I need Emily for that"); acceptance is Jason's own walk, and visual QA uses screenshots rendered in the in-cluster CI. The same evening release 288 went live (migration 288 retired six registry rows), the unused firmware toolchain-cache PVC was deleted on Jason's R10 decision, and an agent's MCP `irrigation_clear_fault` read sent. |
| 2026-11-04 | Two spec changes from the visual QA of the redesign's screenshots (#1308), taken by the operator session under Jason's "drive all of the acceleration" and the redesign authority he gave. (1) One centred container on every page: header, main and footer share `.shell` (80rem) on every route, replacing redesign §3.2's `.page-wide`; Graphs and Water → Schedules no longer take `--shell-wide`, which is retired, so page edges stay put when you switch views. (2) Compact time axes without the zone offset, replacing D-UX-08, redesign §7.1 and design system §5.4 for axes: one line ("12:00", the date on the first tick and at each local midnight, "Oct 27" on day ticks), 36 px at 1440 and 28 px at 860 and below; only the repeated fall-back hour carries a short MDT or MST; the foot ranges drop the offset too ("Nov 1, 10:42 → Nov 2, 16:42 (30 h)"). The footer states the zone once; tooltips, data tables, the CSV and every `<time title>` keep full times with their offsets. |
| 2026-11-05 | Jason's answers (about 01:25Z): (1) "deploy without me": the dashboard redesign deploys without waiting for him, once CI and the screenshot QA pass. (2) "yes": Claude may run the daytime operator checks without him (a fan burst then Clear, and a controller Restart), in warm daytime hours only, because there is no hardware thermostat. (3) "Yes": the production measurement is accepted as the evidence for #1169 item 2. (4) "Drive all of the acceleration. Remove any firmware build/rollout/release timing constraints. We can deploy new builds as often as needed." Applied: the firmware preflight loses its 48-hour last-good bake gate and its weekly OTA limit, and the runbooks drop the bake, the 60-minute hold before the pin and the wait before a last-good promotion. The alert, telemetry, rollback-artifact, signature, credential and rollback checks stay. This widens D-IRP-23 ("No cadence or week rules") from the irrigation release to every firmware release. |
| 2026-11-05 | Jason's direction from the Now walk-through (#1337, #1338): (1) each Now band gauge, hero and zone tiles, is one track with a green band box and the value dot; today's low–high rule ("the black line") is gone and stays only in the gauge's name and "All readings"; the scale is the band plus one width each side, fixed, so only the dot moves (reverses redesign §4.6 and §5.1 "today's low–high on each band bar"). (2) A zone's provenance mark is words, not a code: "probe offline · West + East average" (PEER), "no probe · house average" (center), "probe offline · house average"; D-UX-07 and D-IRP-13 stand. (3) The hero headline is steady and never a link or underlined: row 5 reads "Out of band", and a plain line under it names every reading that is out, each with a quiet "Graphs ›"; a reading counts as out after 2 min out and as back in after 2 min in (changes redesign §5.1 and US-NOW-08; the worst-first order is unchanged). |
| 2026-11-05 | Wave 2 after Jason's walk of the live redesign (epic #1325, issues #1327–#1366, label `wave:dash-W5`). His first-hand decisions: (1) **Crops** is approved as a sixth top-level section (#1330); today the only planting is the hanging Vanda orchids and the 60 hydro cells are empty; the whole greenhouse is re-planted once this system is done, which is his gate for restarting it physically (#1329). (2) A seventh top-level **Planner** section takes over Control → Plan and adds plan history, cost, experiments and A/B results (#1361). (1) and (2) replace D-UX-01's five sections and reopen the experiments UI. (3) The planner change is **wholesale and part of the A/B study**: Hermes is retired and the planner becomes one deterministic call to the local Cortex Qwen model (#1350), with its input pack designed in #1366 and the AI-versus-baseline study in #1359/#1360; this supersedes row 9 / Q-02 and the C2 pilot's OpenAI identity. (4) **Local only**: OpenAI goes, embeddings move to the Cortex embedding model, Docker Hub pulls move to the local registry (#1363), the controller uses the estate's local NTP (#1364) and Slack is decommissioned (#1352); Open-Meteo is accepted, and Cloudflare and lab.verdify.ai's Google Analytics and CDNs are accepted for now (#1351). (5) The dashboard is **plant-agnostic**: targets are VPD and temperature, with no crop-specific copy (#1345, #1334). (6) **VPD**: the target band is wrong for the equipment (#1365); Jason agrees to spend more kWh and to use outdoor air for VPD when outdoor air is confirmed drier, which supersedes the energy-saving stance of ADR-0003/0004 for night VPD. |
| 2026-11-05 | Jason's answers to the wave-2 questions (about 19:50Z): (1) **Lines (#1336):** five physical lines, each with a clean and a fertilized valve: Wall drip (all of the east, south and west walls and every shelf), Center drip, Center mister, South mister, West mister. Misters are separate from drips. The configured relays are the source of truth: a valve without a relay is modelled as "not wired" (shown, not runnable). The master valve (L03) is open whenever a fertilized variant runs; whether clean runs need it waits on his plumbing check. Fog stays a climate device. (2) **Run once (#1343):** any wired, enabled line may be run once, misters included. This reverses D-IRP-11. (3) **Manual lights (#1335):** a timed override like the fan burst, 15 minutes by default, then back to Auto. Camera occupancy should turn the lights on (#1370). (4) The planner may write `night_vpd_bias_kpa` (#1366). (5) The door screen-window is closed for the winter (#1365). (6) 40 °F and 100 °F are the hard temperature guardrails (#1333). (7) He is replacing the CO2 sensor (#51), the PAR sensor (#1184) and the south probe (#751); those issues stay open until the new hardware is verified. |
| 2026-11-05 | Jason (about 20:05Z), occupancy lights (#1370): "The system should monitor MQTT topics for occupancy and deliver the change real-time for lights." Occupancy comes from an MQTT subscription to the Frigate greenhouse cameras (event-driven), not the 300 s Home Assistant sensor poll, which stays as a fallback; lights switch through each light's existing single writer (#399) and return to Auto 15 min after the last detection, like the #1335 manual timer. |
