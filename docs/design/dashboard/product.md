# Dashboard product: personas, journeys and user stories

This document covers who dashboard.verdify.ai and the irrigation program serve and what each must be able to do. It
defines four personas (P1 owner-operator, P2 caretaker, P3 AI agents, P4 platform maintainer) with 35 numbered needs,
nine short journeys and 93 user stories in eleven areas, each with a priority and 2–3 testable acceptance criteria.
MVP is the first build: Jason's four asks (graphs, every setting, the plan, irrigation), the irrigation program and
production delivery. Next follows straight after; Later is outside the first build. The control model is deliberately
simple: one device writer, last write wins, a manual flag that makes Iris skip a setting or irrigation item,
requested-versus-confirmed readback, attribution on every write and two roles. A coverage matrix maps every need to
its stories.

Status: Draft v2 (2026-11-01)

[README](README.md) · **product** · [screens](screens.md) · [architecture](architecture.md) ·
[irrigation](irrigation.md) · [design system](design-system.md) · [execution plan](execution-plan.md) ·
[OpenAPI](openapi/dashboard-api.v1.yaml) · [decisions](decisions.md)

## 1. Scope at a glance

| Jason's ask (2026-11-01) | MVP stories |
|---|---|
| Graphs in the weather.vallery.net look, on uPlot | US-GRF-01 to 04, US-GRF-07, US-GRF-09, US-PLAT-06, US-PLAT-10 |
| Control all settings | US-SET-01 to 05, US-AGT-07, US-PLAT-04, US-PLAT-05 |
| Know what is planned | US-PLN-01 to 03, US-SET-04 |
| Set, change and monitor irrigation: schedules from the dashboard, minutes by zone, MCP tools for agents | US-WAT-01 to 19, US-WAT-21 to 23, US-AGT-01 to 06, US-AGT-08, US-PLAT-12, US-PLAT-13 |
| Misters on VPD control or a schedule, independently per zone | US-WAT-10, US-WAT-11, US-WAT-22 |
| Authentik sign-in; production at dashboard.verdify.ai | US-AUTH-01 to 04, US-PLAT-01 to 03, US-PLAT-07 to 09, US-PLAT-11, US-PLAT-14 |
| Always on: status, honesty, phone, activity, alerts, equipment | US-NOW-01 to 04, US-NOW-06 to 08, US-ACT-01 to 04, US-EQP-01, US-EQP-06 |

## 2. Rules every story assumes

- **One device writer.** Only the ingestor talks to the ESP32. Setting writes from people and agents append to
  `desired_state`; irrigation writes go to the irrigation tables ([architecture](architecture.md) §5,
  [irrigation](irrigation.md) §4.1). The dispatcher and its `irrigation_sync` step apply them through the existing
  fences and re-apply them after an ESP32 reboot. The dispatcher converges in batches of at most 12 with readback and
  never waits for an approval file or manual custody ([decisions](decisions.md), Round 2). Nothing uses the
  `setpoint_changes` NOTIFY fast path, the forecast engine included (Q-04).
- **Last write wins.** The newest write applies, whoever made it: a setting's effective value is the newer of its
  desired row and the active Iris plan's value, and a manual desired row always beats Iris. A **manual** flag makes
  Iris skip a setting or irrigation item until someone clears it; people and other agents may still overwrite it,
  attributed, and a write stores exactly the flag it carries. Every dashboard edit form has one "Manual: Iris skips
  this" box, ticked by default; agent writes default to manual off; pause, run now, stop and clear never change the
  flag. Release returns a setting to plan, band or default. No expiries, no precedence tiers.
- **Validate on submit.** Settings: registry min/max and the entity grid (`verdify_schemas/tunable_registry.py`).
  Water: the irrigation registry bounds and device limits (`verdify_schemas/irrigation.py`). Out of range is refused
  with the bounds; values snap to the grid. Every write accepts an `Idempotency-Key` (`request_id` on MCP), so a
  retry applies once. No dry-run step, step-up, typed confirmation or approval queue; the one confirm dialog is for
  starting water ([design system](design-system.md) §4.4).
- **Requested → confirmed.** Every change shows REQUESTED next to the device readback: a `cfg_*` sensor for a setting,
  the program hash `irr_program_id` for schedules, lines and the site caps. No match within 5 minutes reads NOT
  CONFIRMED (the `setpoint_unconfirmed` window): a display state, not a gate. Run and stop show the relay edge and the
  line's `irr_state`.
- **Attribution.** Every write records source (`dashboard`, `iris`, `agent:<name>` or `system`), actor (the person's
  Authentik username, the agent's name or `forecast-engine`), time and an optional reason in `activity_log`. A write made while an
  experiment is armed goes ahead and carries the experiment id; nothing is refused (D-IRP-26 is dropped for now,
  Round 2).
- **Two roles.** Operator (full control) and viewer (read-only), from the Authentik groups
  `verdify-dashboard-operators` and `verdify-dashboard-viewers`; MFA happens at sign-in.
- **One switch.** `DASHBOARD_WRITES_ENABLED` turns dashboard writes off for rollback; Stop is never blocked.
- **Honest UI.** Every live number shows its age; gaps stay gaps; unknown is never drawn as off or 0; AI-written text
  is labelled AI; units are fixed (°F, % RH, kPa, gal, min) and times are America/Denver with the offset.
- **Protections.** Existing protections stay: the SAF-4 daily water ceiling, the direct-wet relay watchdog and relay
  guards, and leak detection, which raises an alert and does nothing else (Round 2 #3, US-WAT-18). The engine release adds the
  D-IRP-19 mister watchdog (irrigation F10) and the caps and rails in the accepted irrigation decisions
  ([irrigation](irrigation.md) §1). The dashboard adds none.

## 3. Lines and zones

[irrigation](irrigation.md) §3 owns the register; this is enough to read the stories. North and east have no wet
actuator.

| Line | What | Zone | Today (2026-11-01) |
|---|---|---|---|
| L01 | Wall drip, clean (`wall_drips`) | wall (one valve for the south and west wall plants) | Not run since 2026-05-29; the Home Assistant "Wall Drips - RUN CLEAN" button is unused. The first line live: run-now before the engine (US-WAT-23), then the first line scheduled |
| L02 + L03 | Wall drip, fertilized, fed from a tank through an injector; L03 (fertilizer master valve) opens the injector line | wall | Weekly feed exists, never armed |
| L04 | Center mister, clean | center (hanging Vandas) | Live on VPD control only; the only VPD member |
| L05 / L06 | Center drip, clean / fertilized | center | Reserved: physically unconnected, no bring-up now; L06 hard-blocked |
| L07 / L08 | South mister, clean / fertilized | south | L07 idle since 2026-07-10; L08 plumbed, hard-blocked |
| L09 / L10 | West mister, clean / fertilized | west | L09 idle since 2026-07-10; L10 plumbed, hard-blocked |
| L11 | Fog (AquaFog XE 2000) | house | Live, climate only; never schedulable |

After the irrigation firmware release, every wet line has an enable (fertilizer lines included: the hard-blocks become
enables, D-IRP-14) and can carry schedules; L03 opens only with a fertilizer run; misters take schedules through their
`schedule_member` flag; fog stays climate only. L08 and L10 join L02 in the fertilizer bring-up. L05 and L06 stay in
the model and can hold schedules but get no bring-up now: Water shows them reserved and disabled until a person
enables them (Round 2b E).

## 4. Personas

### P1 · Owner-operator

- **Goals:** run the greenhouse as a fast-moving experiment; know the truth at a glance; change anything the platform
  can change; get every clean and fertilizer line watering on schedule; correct Iris and agents when he disagrees.
- **Context:** Jason. Backyard greenhouse in Longmont, Colorado (America/Denver). Desktop for analysis, settings,
  schedules and plan review; phone (390 px) for the 06:30–07:30 check, evenings, work inside and alerts at 3 a.m.
  Expert in the controller, registry and planner; wants density and raw truth, not wizards.
- **Needs:**
  - **P1-N1** Know at a glance whether the greenhouse is in band, what is running and whether the data is fresh.
  - **P1-N2** Graph climate, equipment, the plan and any measured signal over any range, at a stated resolution, with gaps left as gaps.
  - **P1-N3** Change any remotely changeable setting and see the device confirm it.
  - **P1-N4** See what Iris plans and take any setting manual so Iris leaves it alone.
  - **P1-N5** Set, change and control watering: drip, fertilizer and mist schedules, line enables, caps, run now, stop and pause.
  - **P1-N6** Put each mister zone on VPD control, a schedule, both or neither.
  - **P1-N7** See watering minutes by zone and cause, missed runs and water use.
  - **P1-N8** Know who changed what (person, Iris or agent) and respond fast to alerts and equipment problems.

### P2 · Caretaker

- **Goals:** know the plants are fine; see whether water is running and when each zone last got water; take a few safe
  actions if Jason made them an operator; never break anything.
- **Context:** phone only, often 360 px, inside the greenhouse: humid, glare, wet or gloved hands, 10–30 minute visits.
  Knows plants, not the controller; reads plain words. Has whichever role Jason assigns: operator or viewer.
- **Needs:**
  - **P2-N1** An "is everything OK?" answer in plain words on a phone.
  - **P2-N2** See what is running, whether water is on, when each zone was last watered or misted, and what comes next.
  - **P2-N3** Stop water, or start a fan or fog burst, from the phone (operator role).
  - **P2-N4** Not be misted while inside, and see why the misters are quiet.
  - **P2-N5** Large targets, plain language and contrast that reads in sunlight.
  - **P2-N6** See open alerts and acknowledge the ones handled on site.
  - **P2-N7** Sign in simply with the account Jason gave; the viewer role cannot change anything.
  - **P2-N8** Look at the plants remotely and log what was done (Later).

### P3 · AI agents (Iris and other MCP agents)

- **Goals:** keep the crop in its corridor and the plants watered with minimal intervention; read the same truth people
  see; write within registry bounds; (Iris) never overwrite what a person took manual.
- **Context:** headless, reaching the platform only through MCP; people see their work in Activity. Iris runs on planner
  triggers (MIDNIGHT 00:15, SUNRISE, SOLAR_MAX, two TRANSITIONs, SUNSET, WEEKLY Monday 02:00, FORECAST_DEVIATION,
  MANUAL; `ingestor/tasks/_common.py`) and writes through `set_plan` and `set_tunable` (its planner path, unchanged),
  `set_setting` and the irrigation tools. Other agents, for example a watering steward, use the `agent` audience on a
  schedule or on request and write settings through `set_setting`.
- **Needs:**
  - **P3-N1** Read status, schedules, minutes by zone, associations and settings through MCP, matching the dashboard.
  - **P3-N2** Create and change schedules, pause, run now, stop, clear faults, and set line flags and caps through one irrigation MCP surface.
  - **P3-N3** Change settings within registry min/max through the same desired-state path as people.
  - **P3-N4** Get a structured yes or no with the reason, the bounds and the current value.
  - **P3-N5** Know which settings and irrigation items are manual.
  - **P3-N6** Have its own identity, so every write is attributed to the agent.
  - **P3-N7** (Iris) Plan with irrigation state in its context.
  - **P3-N8** Discover every tool, its arguments, defaults, bounds and refusal codes from MCP itself.
  - **P3-N9** Learn what happened after each write: confirmed or not, sent or failed, and who changed it since.

### P4 · Platform maintainer

- **Goals:** ship dashboard releases fast without the device-gated `verdify-prod-dark` sync; verify the live end
  state; keep the single device writer and the security posture; ship irrigation firmware when it is ready.
- **Context:** Jason or fleet sessions (coding sessions, the coordinator, the independent verifier); verdify-platform has
  no repo pod (jvallery/agents#4855). Desktop and CLI; Argo CD GitOps; in-cluster CI (Kaniko to `registry.vallery.net`,
  digest pins); cross-repo steps in jvallery/agents, jvallery/network-infra, jvallery/monitoring-stack and
  jvallery/homeassistant.
- **Needs:**
  - **P4-N1** Release the UI on its own train, independent of the `verdify-prod-dark` sync.
  - **P4-N2** Verify a release live: Argo, digest, `SOURCE_REVISION`, sign-in, probe.
  - **P4-N3** Keep exactly one device writer: every dashboard and agent write goes through desired state and the ingestor.
  - **P4-N4** Turn dashboard writes off with one switch.
  - **P4-N5** See pipeline health: the ingestor heartbeat, data freshness, the writer, Iris triggers and the controller; platform alerts stay in Alertmanager and Slack.
  - **P4-N6** Build cheaply: weather's stack, a typed contract, a fixture API and fast charts.
  - **P4-N7** Ship and verify irrigation firmware through existing tooling, with no calendar rules.
  - **P4-N8** Keep the attack surface small: private API off the public edge, no tokens in the browser, Home Assistant only for occupancy, Lutron and, until a direct reader replaces it, the hydroponic probe.
  - **P4-N9** Back out a bad release quickly.
  - **P4-N10** Revoke or add an agent without a device-gated sync.

## 5. Journeys

Values are illustrative. Screens follow [screens](screens.md): Now, Graphs, Plan, Water, Settings, Activity (holds
alerts), Equipment, Plants and Sign-in; phones use the five-tab bottom bar (Now, Water, Graphs, Activity, More).

### J1 · Morning check

**Who:** P1 on the phone at 06:45; P2 sees the same on a visit. **Stories:** US-NOW-01 to 04, US-NOW-06, US-NOW-08,
US-ACT-02 to 04, US-AUTH-01.

1. Open dashboard.verdify.ai; the 12-hour session is still valid, or a passkey sign-in returns to Now.
2. The hero answers first: "1 thing needs someone", then 64.8 °F, 0.62 kPa, 71 % RH, "in band", "Heating (heat1)",
   "Updated 20 s ago".
3. No banner, so the ingestor, controller, writer and dashboard writes are fine; tiles show Iris's MIDNIGHT plan
   OK, the writer's last push 4 min ago and 1 warning.
4. Water card: center misted 03:58 (VPD), 14 min overnight; nothing running; no flow with the valves closed; wall drip
   next 07:30 (sunrise + 60 min, 12 min); SAF-4 21 of 360 gal.
5. Tap the warning (`relay_stuck` on fan2): it expands in place with the fan2 state and its runbook, and "Open in
   Graphs" shows the fan2 lane; acknowledge with a note.
6. Done in under a minute; nothing needed a laptop.

### J2 · See what Iris plans, take a setting manual and see it confirmed

**Who:** P1 at the desk. **Stories:** US-PLN-01, US-PLN-02, US-SET-02 to 04, US-AGT-05, US-ACT-01.

1. Plan shows the SUNRISE plan (written 07:02, valid to 17:30) with its AI summary.
2. The lanes show `mister_engage_kpa` stepping 1.60 → 1.70 kPa at 13:00 (PLANNED, AI).
3. Open the lane: the setting drawer shows device 1.60 (CONFIRMED 2 min ago), bounds 0.50–2.50, step 0.05.
4. Enter 1.53; "Manual: Iris skips this" is already ticked; reason "Vandas drying". Save: "saved as 1.55, snapped from
   1.53".
5. The row reads REQUESTED, then CONFIRMED when `cfg_mister_engage__kpa_` reports 1.55 (typically 30–90 s); with no
   match in 5 minutes it would read NOT CONFIRMED with the platform's reason.
6. The lane reads MANUAL; at 13:00 nothing changes, because the dispatcher keeps Jason's value, and Iris's next
   context lists the setting under MANUAL SETTINGS.
7. Activity: "Jason · dashboard · mister_engage_kpa 1.60 → 1.55 · manual · Vandas drying". Two days later Jason
   releases it to auto, and Iris's plan value applies on the next dispatcher pass.

### J3 · Set and change drip and fertilizer schedules

**Who:** P1. **Stories:** US-WAT-06 to 09, US-WAT-04, US-WAT-14, US-WAT-15, US-WAT-23.

1. Before the engine, Run now on L01 waters the wall through the bridge (US-WAT-23). Once the engine ships, L01 is the
   first line scheduled: tick its one-time check (stagnant line flushed, emitters checked), then New schedule
   "Wall MWF", Mon/Wed/Fri at sunrise + 60 min, 12 min.
2. On submit the bounds pass; the week view shows each run against the wall's 60 min/day cap.
3. The program reads REQUESTED, then CONFIRMED when `irr_program_id` reports the new hash; the L01 card shows
   "next Wed 07:31 · 12 min".
4. Enable L02 (wall feed) with its check: tank, injector and a leak check; L03 opens the injector line.
5. New schedule on L02: Sundays 09:00, feed 300 s, flush 240 s on L01; L03 opens with it. It runs even with soil probes
   offline. An attended Run now on L02 first is optional.
6. Wednesday's ledger: L01, 12.0 min, cause `schedule` ("Wall MWF"), actor "controller", 9 gal, outcome `ok`.
7. Rain forecast: pause L01 until Saturday, then shorten the schedule to 10 min; both confirmed and in Activity.

### J4 · Switch a mister zone between VPD and schedule

**Who:** P1. **Stories:** US-WAT-09 to 11, US-WAT-17, US-WAT-03.

1. Water: center has VPD on, Schedule off; south and west are off (the seed).
2. Enable L07 (south mister): tick Checked, note "line drained, nozzles ok, no walkway spray".
3. South: VPD on. Its probe (address 4) is out (#751), so the zone reads "VPD from peer probes"; the program hash
   confirms it.
4. Center: Schedule on asks once for the Checked box (nozzles over the Vandas, walkway). Add a mist schedule at
   sunrise + 15 min, 8 pulses of 60 s with 60 s gaps; the editor shows today's minutes against center's 120 min cap.
5. Next morning, minutes by zone split center into `vpd` and `schedule`.
6. To make center schedule-only, switch its VPD off; the program hash confirms it and Activity records it.

### J5 · Check watering minutes by zone

**Who:** P1 at the desk; P2 on a phone. **Stories:** US-WAT-03 to 05, US-NOW-04.

1. Water: minutes for wall, center, south and west over the last 7 days, stacked by cause, with fog separate and each
   zone's cap as a tick.
2. Tuesday had an ingestor outage: its bar is marked partial and shows the gap; nothing is filled in.
3. Tap Tuesday's wall bar: the run ledger lists start, minutes, cause, actor, whole gallons and outcome.
4. A `missed` occurrence shows the controller's reason (for example, no valid clock after a power cut).
5. Switch to 30 days; open the chart window for one run.

### J6 · Respond to an alert

**Who:** P1 on the phone at 03:00; P2 if on site. **Stories:** US-ACT-02 to 04, US-WAT-13, US-WAT-18, US-NOW-06.

1. The Slack post arrives (Slack is unchanged); open dashboard.verdify.ai, Activity.
2. The alert reads "Water flowing with every Verdify valve closed · critical · 03:02 (4 min)", with "a leak, or the
   hose, sink or hydro in use", the gallons in the last 10 min, no open valve and the last runs.
3. No Verdify valve is open, so there is nothing to stop: the page says "Close the spigot". Jason decides whether to go
   out.
4. The meter shows no flow for two readings and the banner reads "Flow stopped"; acknowledge with a note.
5. In the morning, fix the cause and resolve the alert; drips kept their schedules, because nothing latches.
6. Had a valve been stuck open (`irrigation_fence_breach`), Stop all water would have been one tap on the alert,
   confirmed OFF by the relay edges within 30 s. Had the ingestor been down, every page would have said "Ingestor not
   running since 02:51. The controller keeps running on its own.", never "all clear" or "No open alerts".

### J7 · An agent adjusts a schedule

**Who:** P3 (Iris here; any named agent works the same way); P1 sees it. **Stories:** US-AGT-01 to 06, US-WAT-07,
US-ACT-01.

1. At SUNRISE, Iris reads `irrigation_status`, `irrigation_history` and the forecast (high 92 °F).
2. It calls `irrigation_schedule_set` to lengthen L01's schedule from 12 to 15 min, reason "forecast 92 °F"; this
   changes watering only and does not count as its plan for the trigger.
3. The shared service layer checks the bounds and accepts; the write is attributed `iris` with its trigger id.
4. `irrigation_sync` pushes the program; Iris's next `irrigation_status` read shows the schedule confirmed.
5. Jason sees "15 min · iris (AI) · forecast 92 °F" on the L01 card and in Activity.
6. He disagrees and sets it back to 12 with Manual ticked (the default), so Iris's next attempt is refused with
   `manual`. A non-Iris agent could still change it: last write wins.

### J8 · Deploy and verify the dashboard

**Who:** P4. **Stories:** US-PLAT-01, US-PLAT-02, US-PLAT-07 to 09, US-PLAT-14.

1. Merge to main; the verdify-platform CI builds `verdify-dashboard-web`, and its digest lands in
   `deploy/k8s/dashboard/` through a pin PR. The web ships when that PR merges.
2. The auto-synced `verdify-dashboard` Argo app applies that revision; `verdify-prod-dark` is untouched (operator API
   changes in the `verdify-api` image ride the normal release pins).
3. Argo shows Synced and Healthy at a revision that contains the pin; pods run the pinned digest; `/readyz` and the
   footer report the commit the image was built from.
4. `dashboard-smoke` passes: anonymous `GET /` answers 302 to `/auth/login`, which answers 302 to Authentik's authorize
   endpoint, and a viewer session renders live Now data through the BFF.
5. The monitoring-stack route-coverage probe is green.
6. To back out behaviour, merge a one-line change setting `DASHBOARD_WRITES_ENABLED` to `"false"`; the pod rolls within
   5 minutes. To back out a release, revert the pin (US-PLAT-14).

### J9 · Caretaker visit

**Who:** P2 as an operator on a 360 px phone; a viewer sees the same screens with no controls. **Stories:** US-AUTH-01,
US-NOW-03, US-NOW-04, US-NOW-08, US-WAT-12, US-WAT-13, US-WAT-16, US-WAT-18, US-EQP-06, US-ACT-02, US-ACT-04.

1. A passkey sign-in on the phone lands on Now.
2. Now reads "All OK" and "Updated 20 s ago".
3. The water card reads "Wall today 07:31, 12 min · Center misted 09:58 (VPD)".
4. "Someone inside: misters wait (Frigate, 1 min ago)".
5. More → Equipment → Fans 15 min; Now then shows "Fans burst · 14 min left" from the readback.
6. Water → L01 Run… → Run, then Stop.
7. Hand-watering with the hose shows "Water flowing with every Verdify valve closed"; afterwards the caretaker
   acknowledges the alert with the note "me, hose".

A viewer gets the same screens, no controls and the spigot guidance. The whole visit runs end to end at 360 × 740
against the fixture API, as an operator and as a viewer.

## 6. User stories

Each story gives persona(s) · priority · screen, then 2–3 acceptance criteria. **MVP**: the first build. **Next**:
right after MVP. **Later**: not in the first build. Totals: 75 MVP, 9 Next, 9 Later. Operation ids (API-NN,
API-IRR-NN) are the `operationId`s in the [OpenAPI](openapi/dashboard-api.v1.yaml) file.

### NOW · Now

#### US-NOW-01 · Is the greenhouse OK?
*P1, P2 · MVP · Now.* As the owner-operator, I want Now to open with indoor temperature, VPD and RH against the band in force, the mode in plain words and the reading's age, so that I know in five seconds whether the plants are in band and the numbers are current.
- The hero (API-02 `GET /v1/now`, from the newest `climate`, `diagnostics` and `system_state` rows and the dispatcher's band rows) shows each value with its unit, its position against the band in force (the band the dispatcher computes from `crop_band_anchors`, labelled DERIVED with its age; the controller's own targets are the cross-check), an in/above/below verdict per metric and "Updated N s ago" from the reading's timestamp.
- The mode shows as a phrase from one mapping table (VENTILATE → "Venting to cool"), with the raw code and `mode_reason` in a disclosure.
- A null value shows "—" with the reason, never 0 or the previous value; if the band cannot be computed the verdict reads "band unavailable".

#### US-NOW-02 · Trouble shows on every page
*P1, P4 · MVP · every page.* As the owner-operator, I want every page to tell me when the ingestor, the controller, the device writer or the planner is in trouble, so that I know whether to trust what I am about to read or change.
- A banner shows on every page for the ingestor or controller state (US-NOW-06), while settings are not reaching the controller ("Settings not reaching the controller since HH:MM (N differ)", raised by the writer's stuck-delivery alert) and while dashboard writes are off; the Activity nav item shows the one open-alert count (US-ACT-02).
- Now's tiles show data age, the writer's health as the setpoints pipeline age (`v_data_pipeline_health` source `setpoints`), Iris's last required trigger (OK, late or failed, from `v_planner_trigger_health`) and the alerts that need someone.
- A state the API cannot read shows "unknown", never healthy; each banner and tile links to the page that explains it.

#### US-NOW-03 · What is running now
*P2, P1 · MVP · Now.* As a caretaker, I want to see which equipment is running and since when, so that I know whether fan noise, fog or wet floors are expected.
- Each running actuator is a chip with its plain name and "since HH:MM", the start of its current run of equal states (repeated rows from reconnect snapshots or polls are collapsed); wet lines add the cause (VPD, schedule, run now, feed, flush, fallback) and, once `irr_state` publishes it, the time left.
- A state is known while the controller reports (the ingestor heartbeat at most 180 s old and the newest `climate` row at most 300 s old, [architecture](architecture.md) §7); an actuator reads "unknown" with the age of its last row only while the controller is not reporting or it has no row, never "off".
- Occupancy always shows, from `equipment_state` `occupancy` and `occupancy_inhibit`: "Someone inside: misters wait (Frigate, N min ago)", "Nobody seen: misters may run (last person HH:MM)", "Occupancy unknown: misters may run" and, while `occupancy_inhibit` is off, "Occupancy mist hold is off: misters may run".

#### US-NOW-04 · Water today
*P1, P2 · MVP · Now.* As the owner-operator, I want a water card on Now with today's minutes by zone, when each zone last got water, what is running, the next run per line and any unexplained flow, so that the morning check covers water without another page.
- The card (API-IRR-01) shows today's watering minutes for wall, center, south and west (US-WAT-03), the running line if any, the next occurrence per enabled line, any water flowing with every valve closed and SAF-4 use today against the 360 gal ceiling; selecting it opens Water.
- Each zone shows when it last got water, the newest `last_water` among its lines ("Wall today 07:31, 12 min · Center misted 09:58 (VPD) · West 3 days ago"); a zone with no enabled line reads "not set up", never 0, and one with no ledger data "—" with the reason.
- For operators the card carries Stop all water from the start, working on every line (through the bridge before the engine, US-WAT-23). Run and Stop on a line live on Water; before the engine the card says L01 runs from Water and run-now on the other lines arrives with the irrigation firmware.

#### US-NOW-05 · What happens next
*P1 · Next · Now.* As the owner-operator, I want the next plan transition, the next Iris trigger, the next run per line and sunrise and sunset in one list, so that I anticipate instead of react.
- The next transition shows its time, the settings it changes and an AI label; manual settings are left out.
- The next occurrence per line shows time, minutes and schedule; a paused one says so, and by whom.
- Sunrise and sunset come from `ingestor/solar.py`, which mirrors the firmware's solar code.

#### US-NOW-06 · Honest degraded states
*P1, P2 · MVP · every page.* As the owner-operator, I want "API unavailable", "ingestor not running", "controller not reporting" and "data stale" told apart and each panel to fail alone, so that I never mistake an outage for an all-clear or act on old numbers.
- A panel whose request fails shows its own error and Retry; the other panels keep working.
- Fault banners show worst first: API down; with the ingestor heartbeat (one `system_state` row every 60 s from the ingestor's own loop, whether or not the ESP32 is connected; API-02 `freshness.ingestor`) older than 180 s, "Ingestor not running since HH:MM. The controller keeps running on its own."; with the heartbeat fresh and the newest `climate` row older than 300 s, "Controller not reporting since HH:MM"; then water flowing with every Verdify valve closed (US-WAT-18). No page says the controller is offline. A stale series (older than twice its expected interval, the `v_sensor_staleness` rule) reads STALE with "Last data N min ago" and no verdict.
- With the operator API unreachable, every page says "Dashboard API unavailable" and shows no green state.

#### US-NOW-07 · Phone first
*P2, P1 · MVP · every page.* As a caretaker, I want every MVP page to work one-handed on a phone in the greenhouse, so that I can check and act without a laptop.
- Every MVP page works at 360–390 px with no horizontal scroll and 44 px targets, under the five-tab bottom bar (Now, Water, Graphs, Activity, More); Now answers above the fold, at 360 × 740 too.
- For operators, Stop all water is one tap from Now and from Water; on phones Stop all water, Run and Acknowledge are full width.
- The one words table covers the OpenAPI enums, the modes and mist stages in `firmware/lib/greenhouse_types.h`, the fog block reasons in `verdify_schemas/climate_intent.py` and `ALERT_TYPES` in `verdify_schemas/alerts.py`, written for someone who knows plants rather than the controller (a unit test fails on a missing entry; an unknown code at run time still shows raw); pages pass axe (WCAG 2.2 AA) at 320, 768 and 1440 px.

#### US-NOW-08 · Everything OK? in one sentence
*P2, P1 · MVP · Now.* As a caretaker, I want Now to answer "is everything OK?" in one plain sentence, so that I know at a glance whether anything needs me.
- Now's first line is the first true row of a worst-first table, in the banners' order: API down ("Can't reach the greenhouse data"); ingestor not running; controller not reporting (US-NOW-06, for example "Controller not reporting since 03:12"); water flowing with every Verdify valve closed; a metric outside its band ("Too hot: 84.1 °F (target 62–70 °F)", "Too cold", "Air too dry", "Air too damp"); open alerts nobody has acknowledged (the one count, US-ACT-02: "2 things need someone"); stale data ("Waiting for fresh data (last 14 min ago)"); otherwise "All OK".
- The sentence uses no codes, reads the same for operators and viewers, links to the page that explains it and sits above the fold at 360 × 740.
- It never reads "All OK" while any row above it is true or a read it depends on has failed.

### GRF · Graphs

#### US-GRF-01 · Climate against the band, with the plan ahead
*P1 · MVP · Graphs.* As the owner-operator, I want temperature, VPD and RH against the band with the plan ahead of now, so that I see past, present and plan in one view.
- Temperature, VPD and RH are linked panels on one cursor, each with the band in force as a ribbon (API-06: the dispatcher's band rows in `setpoint_snapshot`, DERIVED, within their 90-day retention; older spans draw the crop band reconstructed from today's anchors and say so), the measured trace solid up to now, and setting steps: device (`setpoint_snapshot`), requested (`desired_state`) and planned (`setpoint_plan`, dashed after now, AI); a superseded planned step is drawn muted with SUPERSEDED, never as the plan ahead.
- Gaps break the line; nothing is interpolated or zero-filled, and a request is never drawn as device truth.
- The tooltip shows each value, the band and the time with its UTC offset.

#### US-GRF-02 · Equipment lanes under the climate charts
*P1 · MVP · Graphs.* As the owner-operator, I want an equipment lane per actuator under the climate charts, so that I see what the controller did about the climate.
- Lanes share the cursor and show ON intervals (API-05, `equipment_state` seeded by `fn_equip_at`): fan1, fan2, vent, heat1 (electric), heat2 (gas); the wet relays L01–L11; `grow_light_main`, `grow_light_grow`; flags for occupancy, `leak_detected` and `water_flowing`.
- A span with no data is hatched "unknown", never drawn as off; for windows over 31 days the lanes panel reads "Equipment lanes cover at most 31 days. Narrow the range to see them" while the climate panels render, and the URL keeps the range.
- Wet-line intervals are coloured by cause: `vpd`, `schedule`, `run_now`, `feed`, `prewet`, `flush`, `fallback` or `unknown`.

#### US-GRF-03 · Any range, at a stated resolution, and a CSV
*P1 · MVP · Graphs.* As the owner-operator, I want any range at a stated resolution and the data behind it, so that I look at an hour or a year the same way.
- Chips 24 h, 72 h, 7 d and 30 d plus a custom range up to 400 days where daily data exists; the server picks the bucket (1 min to 1 d) and the chart states it ("one point per 5 minutes"). Raw data is kept 365 days for climate, 180 for controller diagnostics and 90 for setting readbacks and band history; past a metric's raw retention the chart draws its daily summary and says so ("daily summary: raw data kept N days"), and a metric with no daily summary reads "beyond retention (N days kept)", not a bare gap.
- A summary under each chart gives low and when, mean, high and when, coverage % and gap count.
- Range and metrics live in the URL, so a copied link reproduces the view; "CSV of this window" downloads exactly what is plotted.

#### US-GRF-04 · Units and time
*P1, P2 · MVP · every page.* As the owner-operator, I want fixed units and Denver time with the offset, so that no number or time is ambiguous.
- Units are fixed and canonical: °F, % RH, kPa, gal, min; a °C toggle is Later.
- Times are America/Denver, stated once per panel; on 2026-11-01, 01:00–01:59 occurs twice and each time in that hour carries its offset.
- A setting is always submitted in its registry unit.

#### US-GRF-05 · Indoor against outdoor, and the forecast
*P1 · Next · Graphs.* As the owner-operator, I want outdoor conditions and the forecast on the same charts, so that I see what the weather is doing to the greenhouse.
- Outdoor temperature, RH and VPD overlay the indoor trace, each stating its source (the Tempest, received by the ESP32 over UDP) and age.
- The forecast continues past now, dashed, labelled with the time it was issued.

#### US-GRF-06 · Zones
*P1 · Next · Now, Graphs.* As the owner-operator, I want a tile and a chart per zone against that zone's target, so that I see microclimates and which probe each number comes from.
- Now shows a tile per zone (north, south, east, west, center) with temperature, RH and VPD against the zone's band in force (`band_<zone>_*`, cross-checked with the controller's `vpd_target_<zone>`). North has no zone band (the dispatcher bands center, south, west and east), so its tile reads against the house band and says so.
- Each value is tagged MEASURED, PEER or HOUSE; center has no probe of its own, so it reads HOUSE, and south reads PEER or HOUSE while its probe (address 4, #751) is out.
- Selecting a tile opens that zone's charts.

#### US-GRF-07 · Graph any measured signal
*P1 · MVP · Graphs.* As the owner-operator, I want to add any measured or derived signal to Graphs, so that I can graph everything worth graphing without Grafana or SQL.
- "+ Metric" lists every API-03 key grouped as house climate, zones, water and soil, light, outdoor, targets and controller, with unit, cadence and provenance; choosing one adds it to the linked panel for its unit, or to a new linked panel, never a second axis; up to 8 metrics per window, kept in the URL as `m=`.
- API-03 covers every numeric column of `climate` and the controller's free heap (kB), largest block, Wi-Fi RSSI and uptime; a unit test parses `CREATE TABLE public.climate` in `db/schema.sql` and fails when a column has neither a registry key nor an exclusion with a written reason. DLI stays out until the PAR meter (US-GRF-10).
- With the fixture, `/graphs?range=7d&m=climate.soil_moisture_south_1&m=climate.flow_gpm` renders soil moisture and flow on one cursor with the L01 lane, gaps broken and the resolution stated.

#### US-GRF-08 · Events on the charts
*P1 · Later · Graphs.* As the owner-operator, I want setting changes, watering runs, alerts and plan transitions marked on the charts, so that I connect cause and effect.
- Markers show kind and source (`dashboard`, `iris`, `agent:<name>`, `system`); selecting one opens its Activity entry.
- Each kind can be toggled.

#### US-GRF-09 · Simple daily scorecard
*P1 · MVP · Graphs.* As the owner-operator, I want one row per day with time in band, water and planner reliability, so that I can tell whether the experiment improves week to week.
- `/graphs/scorecard` shows one row per America/Denver day for 30 days: time in band day and night (`mv_zone_band_grade`), gallons used (`daily_summary.water_used_gal`), watering minutes by zone, Iris required plans written and missed, and alerts raised by severity; a 23- or 25-hour day counts as it ran.
- It reads API-04 `daily.*` keys bound to a new view `v_daily_scorecard`, plus API-IRR-10 for minutes, with no new operation; a missing value is "—" with its reason (for water, `v_water_meter_daily.quality`), never 0.
- "CSV of this table" downloads exactly the 30 rows shown.

#### US-GRF-10 · Light and DLI from a PAR meter
*P1 · Next · Graphs.* As the owner-operator, I want light measured by a real PAR meter and DLI graphed from it, so that light decisions rest on a measurement.
- A new PAR meter, wired into the firmware and ingest ([decisions](decisions.md), Round 2), feeds PPFD and a daily DLI with availability and provenance through `v_dli_current` and `v_dli_daily`. Graphs plots PPFD and one daily key, `daily.dli_mol_m2_day` over `v_dli_daily`, from the metric picker; Now gets no DLI element, and until the meter ships no page shows DLI.
- DLI never comes from the broken interior lux sensor or the legacy forensic columns (ADR-0008).

### PLN · Plan

#### US-PLN-01 · The plan in force, in plain words
*P1, P2 · MVP · Plan.* As the owner-operator, I want a card with the Iris plan in force, why it was written and how long it lasts, so that I know what the greenhouse will be told to do without Slack or SQL.
- The card (API-07 `GET /v1/plan`) shows plan id, trigger (MIDNIGHT, SUNRISE, SOLAR_MAX, TRANSITION, SUNSET, WEEKLY, FORECAST_DEVIATION, MANUAL), written at, valid until and the hypothesis summary labelled AI; a disclosure holds conditions, stress windows and per-setting rationale.
- With no plan in force, the card says which values apply instead and since when.
- It reads the same source as MCP `plan_status` (`plan_journal` and the future `setpoint_plan` waypoints).

#### US-PLN-02 · Timeline per setting
*P1 · MVP · Plan.* As the owner-operator, I want one lane per planned setting across the next 24 hours (72 on request), so that I see what the controller will be told and when.
- Each lane (API-07) shows Iris's waypoints (dashed, PLANNED, AI) and the device value at now; a forecast-rule value reads "Forecast rule · system", never AI; a plan expiry inside the window is marked with what takes over.
- A manual setting's lane reads "MANUAL · Iris skips" with no waypoints. When a setting's newest desired row is newer than the plan in force and not manual, the lane reads "YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan" and draws that plan's later waypoints muted, labelled SUPERSEDED; Iris's next plan returns it to PLANNED.
- Every lane opens its setting drawer (US-SET-03, US-SET-04).

#### US-PLN-03 · Planner health
*P1, P4 · MVP · Plan.* As the owner-operator on call, I want the trigger ledger and what runs if Iris is down, so that I can tell whether a planner failure is urgent.
- The last 36 h of `planner_trigger_ledger` show trigger, due time, outcome in words (due, delivered, acknowledged, plan written, setting changed, fallback, wrong action, delivery failed, timed out, missed) and failure class.
- A failed, timed-out or missed required trigger turns Now's Iris tile amber and states the plan in force and its expiry.

#### US-PLN-04 · Planned against confirmed
*P1 · Later · Plan.* As the owner-operator, I want each past transition classed against what the device confirmed, so that I can trust the plan is really executing.
- Each transition shows its class from `fn_plan_transition_audit` (matched, already at value, guardrailed, missed, mismatched) with push and confirm latency.
- A transition not yet confirmed reads "pending", never "matched".

#### US-PLN-05 · Ask Iris to replan now, with a note
*P1 · Later · Plan.* As the owner-operator, I want to trigger a MANUAL plan with a note for Iris, so that Iris reacts now to something I know.
- The note reaches Iris's context for that trigger, and the trigger shows in the ledger with its outcome.
- The request and note appear in Activity.

### WAT · Water

#### US-WAT-01 · Water board
*P1, P2 · MVP · Water.* As the owner-operator, I want one board with every wet line and the shared limits, so that I see what can water, what is watering and what comes next.
- A row per line L01–L11 (API-IRR-01) shows zone, CLEAN or FERT, enabled, VPD and Schedule flags, VPD source, check, live state (US-WAT-02), today's minutes, when it last watered (`last_water`), the last outcome and the next run; fog (L11) is read-only "climate only"; L05 and L06 read RESERVED, disabled until a person enables them.
- A limits strip shows SAF-4 use against 360 gal, the soft budget, each mister zone's duty minutes with its VPD reserve marked, the drip line caps, any flow with every valve closed and occupancy.
- A sync line reads "On controller: rev 42, confirmed 2 min ago" from `irr_program_id`, or "Not confirmed: <reason>".

#### US-WAT-02 · Live job state, and why not
*P1, P2 · MVP · Water.* As a caretaker, I want each line to say whether it is idle, running, waiting or blocked and why, so that I never guess why water is or is not running.
- State comes from the controller's `irr_state` readback with its age: running shows cause, schedule and time left; waiting or blocked shows the reason.
- Reasons read as words: line disabled, paused until HH:MM, backing off or faulted (watchdog), someone inside (Frigate), cap reached, VPD reserve, RH or dew limit, no clock, waiting for the manifold ("L02 until 07:42").
- Until the irrigation firmware ships, rows show relay state only and say live job state arrives with it; schedule, line and mister changes are saved and read "Applies with the irrigation firmware" until `irr_program_id` first appears; only L01 runs, and Stop works on every line (US-WAT-23).

#### US-WAT-03 · Watering minutes by zone and cause
*P1, P2 · MVP · Water.* As the owner-operator, I want watering minutes per zone per day split by cause and chemistry, so that I know each zone got the water I intended.
- `v_irrigation_minutes_daily` (API-IRR-10) gives valve-open minutes, pulses and gallons per America/Denver day for wall, center, south and west (fog on its own), stacked by cause (`vpd`, `schedule`, `run_now`, `feed`, `prewet`, `flush`, `fallback`, `unknown`) with clean below fertilizer and each zone's cap as a tick; today, 7 and 30 days.
- Minutes come from the relay runs already recorded (`v_water_relay_runs`) and ship before the firmware, with history back to 2024-11-17 (before the engine, L04 and fog count as `vpd` and L01 as `unknown`); 23- and 25-hour days are correct.
- Today and any day with an ingestor outage are marked partial; an outage shows as a gap, never as zero, and nothing is back-filled.

#### US-WAT-04 · Run ledger
*P1 · MVP · Water.* As the owner-operator, I want every run listed with its cause, actor and outcome, so that I can check any watering after the fact.
- Each `irrigation_ledger` row (API-IRR-11) shows line, zone, chemistry, cause, schedule, actor, start, end, open minutes, pulses, gallons and outcome (`ok`, `short`, `stopped`, `preempted`, `skipped`, `missed` or `cap`, with its reason); a fertilizer run's prewet, feed and flush are their own rows.
- Gallons are the whole-gallon meter delta (±1 gal), informational (D-IRP-18); a run that moved no water reads "0 gal". No run carries a flow flag or a gal/min rate (Q-10).
- Filters by line, cause, outcome and date (31 days per page); each run opens its chart window.

#### US-WAT-05 · Missed runs are loud
*P1 · MVP · Water.* As the owner-operator, I want an occurrence that did not run flagged and alerted, so that no zone silently goes dry again (in 2026 the feed was dropped for 41 days with no alert).
- A start may be up to 60 min late; after that the occurrence is `missed` with the controller's reason, and `irrigation_run_missed` (warning, `alert_log` category water) fires; it also fires when an occurrence the platform watched while connected has no outcome 2 h after its planned start.
- An occurrence whose outcome was lost while the ingestor was disconnected reads `unknown`: a gap, never `missed`, and no alert.
- The week view (line × day) shows each line's planned occurrences for the next 7 days and, under "Last 7 days", past outcomes; at 390 px a day switcher shows one day, with no horizontal scroll.

#### US-WAT-06 · Create a drip schedule
*P1 · MVP · Water.* As the owner-operator, I want to create drip schedules from the dashboard, so that the drip lines water on time without the HA button.
- A schedule (API-IRR-03) takes line, weekdays (at least one), start (clock time, or sunrise, solar noon or sunset ± 0–240 min) and run length (`on_s`, shown in minutes); an out-of-range value is refused with `out_of_range` and its bounds, and two schedules on one line less than 30 min apart with `too_close`.
- The week view previews the draft against the line's daily cap (wall 60, center 30 min); runs past the cap end `cap` and are not refused, and a schedule on a disabled line is accepted, its occurrences ending `skipped` ("line disabled").
- The program reads REQUESTED, then CONFIRMED when `irr_program_id` reports its hash; the editor shows the device limit of 24 schedules, and a 25th is refused with `too_many_schedules`.

#### US-WAT-07 · Change, disable or delete a schedule
*P1 · MVP · Water.* As the owner-operator, I want to edit, disable or delete a schedule and see the controller confirm it, so that changing is as easy as creating.
- Edit, enable or disable (API-IRR-04) and delete (API-IRR-05) are each confirmed by the program hash; the ledger keeps past runs as they ran.
- The editor's "Manual: Iris skips this" box starts ticked; unticking it lets Iris adjust the schedule. Iris's writes to a manual schedule are refused with `manual` (US-AGT-05); people and other agents may still change it.
- Activity records each change with who, before → after and why.

#### US-WAT-08 · Fertilizer schedule
*P1 · MVP · Water.* As the owner-operator, I want to schedule fertilizer like any other line, so that feeds go out on time without hand-holding.
- A schedule on L02 takes weekdays, start, feed time (`on_s`), `prewet_s` and `flush_s` on the clean partner L01; L03 opens the injector line from the fertilizer tank with it; L06, L08 and L10 work the same with L05, L07 and L09 once enabled.
- No sensor gates a fertilizer run: it runs with soil or climate probes offline (D-IRP-14).
- A fertilizer run is never paused or resumed: a stop, SAFETY_COOL or a reboot ends it and its flush runs; disabling the line ends it with no flush (D-IRP-15).

#### US-WAT-09 · Line enables
*P1 · MVP · Water.* As the owner-operator, I want an enable switch on every wet line, fertilizer lines included, so that I bring lines online as soon as they are plumbed.
- `enabled` (API-IRR-08) exists for every wet line; turning on a line with no check needs the "Checked" box (who and when are recorded) and takes an optional note of up to 500 characters (D-IRP-07), or is refused with `check_required`. L01 is seeded enabled but unchecked, so its first schedule asks once for the check (flush the stagnant line, check the emitters).
- Every line change is confirmed by the program hash and survives reboots (the program lives in NVS); turning a line off stops everything on it, VPD misting included.
- Only a person records a check, on the dashboard: MCP tools take no check, and an agent that enables an unchecked line or first turns on its Schedule gets `check_required`; once a check exists, agents enable, disable and flag the line freely.

#### US-WAT-10 · Mister zones: VPD and schedule, independently
*P1 · MVP · Water.* As the owner-operator, I want each mister zone on VPD control, a schedule, both or neither, so that center stays on VPD and I add schedules where I want them.
- L04, L07 and L09 each have `vpd_member` and `schedule_member` switches (line fields, API-IRR-08) in any combination; the seed is L04 VPD only; both off reads "This zone will not mist automatically"; fog shows "climate only".
- Turning Schedule on for a line with no check needs the "Checked" box (nozzles, coverage, walkway; D-IRP-08); each switch is confirmed by the program hash, and the zone's VPD source reads own probe, peer probes or house average.
- With more than one VPD member, the existing rank arbiter picks the zone (center rank 1); a change made while an experiment is armed goes ahead and carries the experiment id, and nothing is refused.

#### US-WAT-11 · Mist windows
*P1 · MVP · Water.* As the owner-operator, I want mist schedules on schedule-member zones, so that misters water on a timer as well as on VPD.
- A mist schedule takes weekdays, start, pulse length (`on_s` 10–90 s), pulses (`count` 1–60) and gap (`gap_s` 30–600 s, never shorter than `mister_min_off_s` at run time), for example a dawn drench at sunrise + 15 min, 8 × 60 s with 60 s gaps.
- Scheduled and VPD pulses alternate on the one mister manifold and share SAF-4, the soft budget and the zone's duty cap less its VPD reserve (center 120 − 60, south 45 − 15, west 45 − 15 min); the editor shows today's projected minutes against them.
- A pulse is skipped on measured RH ≥ 90 % (`sched_mist_rh_max_pct`), a dew-margin breach, confirmed occupancy or L03 open; a missing sensor reading does not block (D-IRP-02); pulses still undelivered 60 min after the last planned one end the window `short`.

#### US-WAT-12 · Run now
*P1, P2 · MVP · Water.* As the owner-operator, I want to run a line now from the dashboard, so that I can water by hand without Home Assistant.
- Run now (API-IRR-06, one confirm dialog naming line and minutes) runs a drip for N minutes, or a one-off window on a mister with `schedule_member` on (D-IRP-11); before the engine only L01 runs (US-WAT-23). It starts at once or is refused in plain words, and nothing is queued: `manifold_busy` ("Another drip line (L02 Wall feed) is running until 07:42; stop it or try then"), `line_busy`, `cap_reached`, `line_disabled`, `line_paused`, `line_faulted` or `not_schedule_member`.
- On a fertilizer line, Run now takes `prewet_s` and `flush_s` with the feed: minutes on the fertilizer drips L02 and L06, and `on_s`, `count` and `gap_s` within the mister bounds on the fertilizer misters L08 and L10; the dialog title carries FERT; the run prewets on the clean partner, opens L03 with the feed, then flushes, and the ledger shows the prewet, feed and flush rows linked to the command.
- It returns 202 with a `command_id`, and a double tap sends one `Idempotency-Key`, so the line never runs twice; if the line has not started within 30 s, it shows the line's `irr_state` reason in words, or "Not started". The engine release removes the HA RUN CLEAN buttons `btn_wall_clean` and `btn_center_clean` (D-IRP-24).

#### US-WAT-13 · Stop
*P1, P2 · MVP · Water, Now.* As an operator, I want Stop on a line and Stop all water, so that I can end any water fast, including at 3 a.m. on a phone.
- Stop on a line and Stop all water are one tap with no dialog (API-IRR-07), work on every line from the start (through the bridge before the engine, US-WAT-23), are never heap-deferred and never blocked by `DASHBOARD_WRITES_ENABLED`; every relay that was on reads OFF from `equipment_state` within 30 s.
- Stop ends what is running and disables nothing: the result reads "Stopped every running valve. VPD misting, survival fog and the next scheduled run can still start on their own; to keep a line off, disable it", with Disable for each line that was running (US-WAT-09). Before the irrigation firmware it reads "Stopped every running valve. VPD misting and fog can still start on their own." with no Disable, because until then a disable is only stored.
- While the ingestor heartbeat is stale, Stop is still stored (202) and reads "Stop saved but not sent: ingestor not running since HH:MM. Running drips end on their own timer; close the spigot if water must stop now." If the relays have not read OFF within 30 s, or the controller is not reporting, the Stop area reads "The controller hasn't confirmed the stop. Close the spigot to stop water now."

#### US-WAT-14 · Pause and resume
*P1, P2 · MVP · Water.* As the owner-operator, I want to pause lines until a time and resume early, so that rain, work or a sick plant need no schedule edits.
- Pause one line, or every line with `line_id` all, until a time (API-IRR-08 `paused_until`), and resume early by clearing it (null or now); each is confirmed by the program hash, survives reboots and leaves the manual flag as it was.
- A paused line starts no scheduled or run-now water until the time; VPD misting carries on (turn VPD control off, or disable the line, to stop it), and the row says "paused until HH:MM · VPD misting continues".
- Occurrences that fall in a pause show in the week view and the ledger as `skipped` (paused), with who paused and why.

#### US-WAT-15 · Watering runs on the controller, even offline
*P1, P4 · MVP · Water.* As the owner-operator, I want the confirmed program to keep running on the controller when the platform is down, so that a database or network outage is not a watering outage.
- With the ingestor, database or Wi-Fi down, the device runs its last program, VPD misting, every cap and the pauses already pushed (D-IRP-01); `irr_program_id` reads `src=nvs`.
- A missing or unreadable program falls back to today's behaviour (`src=rom`: L04 VPD only, L01 run-now, no schedules, fertilizer off), and the board says so.
- Time comes from NTP with no RTC (D-IRP-25): with no valid time, nothing starts on schedule, while run-now and Stop still work.

#### US-WAT-16 · Misting waits only for someone Frigate sees
*P2, P1 · MVP · Water, Now.* As a caretaker, I want misting to wait while I am inside and the dashboard to say so, so that I am not sprayed and know why the misters are quiet.
- Mist and fog hold only while Frigate confirms occupancy, as Home Assistant's `binary_sensor.greenhouse_zone_person_occupancy` reports it (polled every 300 s, so a first detection can take up to 5 min), the only occupancy input: the unused MQTT path (`sentinel/occupancy/greenhouse_zone` into `ingestor/occupancy.py`) goes (Round 2b A). The board says "Waiting: someone inside (Frigate, seen N min ago)".
- While Home Assistant reports the sensor ON, each poll extends the hold, up to 60 min after the visit's first detection (the firmware fail-safe); OFF releases mist on the next poll, so with the sensor ON for 40 min the board shows someone inside for 40 min.
- Unknown or stale occupancy means mist runs (D-IRP-10); drips ignore occupancy.

#### US-WAT-17 · A failed probe does not stop VPD misting
*P1 · MVP · Water.* As the owner-operator, I want a zone whose probe is out to use its peers or the house average, so that south and west can join VPD control now.
- A zone uses its own probe when fresh and finite, otherwise the mean of its fresh peer probes (south, west, east), otherwise the house average; center always uses the house average (D-IRP-13).
- `irr_state` publishes each zone's source and the board shows it, for example south "VPD from peer probes (address 4 out, #751)"; nothing waits for the probe repair.

#### US-WAT-18 · Water flowing with every valve closed
*P1, P2 · MVP · Water, Now.* As the owner-operator, I want flow with every Verdify valve closed to be impossible to miss and plain about what it means, so that a burst line costs minutes, not a night.
- `leak_detected` (flow on the one site meter while no Verdify valve is open) shows on Now, on Water and as a banner titled "Water flowing with every Verdify valve closed", with "a leak, or the hose, sink or hydro in use" beneath, its start time and the gallons in the last 10 min; the banner carries no Stop and says "Close the spigot".
- It raises the existing critical alert and does nothing else (Round 2 #3, Q-05): nothing latches, and the irrigation firmware drops `leak_detected` from every fog, mister and drip force-off and from the irrigation cancel. Until that release, today's firmware keeps its interlock.
- Once flow is under 0.1 gal/min for two meter readings the banner and alert read "Flow stopped"; while it continues they never read as all clear.

#### US-WAT-19 · Caps and headroom
*P1 · MVP · Water.* As the owner-operator, I want to see every water cap with today's use and change its value, so that I tune limits as the greenhouse changes.
- Site caps, in Settings (API-10; MCP `set_setting`): SAF-4 `mister_daily_volume_max_gal` 360 (100–600), scheduled-mist RH ceiling `sched_mist_rh_max_pct` 90 % (75–98), climate yield `climate_yield_min` 15 min (5–30); per line (API-IRR-08): `daily_cap_min` (L01 60, L04 120, L05 30, L07 45, L09 45) and `vpd_reserve_min` (L04 60, L07 15, L09 15).
- Each cap shows today's use and headroom; counters reset at local midnight and survive soft resets. The site caps travel in the irrigation program: each reads REQUESTED until a confirmed push compiled after the write, then CONFIRMED with the value inside the applied program ("On controller · rev 42: 360 gal"); before the irrigation firmware a write is stored and reads REQUESTED ("applies with the irrigation firmware") until the first confirmed push compiled after it.
- Survival fog in SAFETY_COOL is never vetoed by SAF-4 or the soft budget, though its water still counts (D-IRP-04); the remaining fog force-offs (irrigation F9) still apply.

#### US-WAT-20 · Daily water use
*P1 · Next · Water.* As the owner-operator, I want daily gallons split by what was open, so that I spot waste and water nothing in Verdify accounts for.
- Daily gallons from the calibrated site meter (`v_water_meter_daily`, whole gallons) are split by line and cause from `irrigation_ledger`, with water that ran while no valve was open (hose, sink, hydro, leak) shown separately.
- 30 and 90 days as bars and a table; missing days are gaps.

#### US-WAT-21 · The Vandas keep water if VPD sensing fails
*P1 · MVP · Water.* As the owner-operator, I want timed center misting while VPD control is degraded, so that a sensor fault does not leave the hanging Vandas dry.
- Three seeded `degraded_only` schedules on L04 (solar noon −120, 0 and +120 min; 4 × 45 s with 60 s gaps) run only while VPD control is degraded (`sensor_degraded`), following `vpd_member` (D-IRP-12).
- Their runs carry cause `fallback`, and the board shows when the fallback is active.

#### US-WAT-22 · A latched mister is loud and quick to clear
*P1 · MVP · Water, Now.* As the owner-operator, I want a mister the watchdog latched off to be impossible to miss and one tap to clear, so that the Vandas never go without VPD misting until midnight unnoticed.
- While `irr_state` reports a back-off or a latch on L04, L07 or L09, its line card and Now's water card read "Backing off until HH:MM" or "Faulted: no misting until midnight (watchdog)", from API-IRR-01 `fault` and `fault_until`.
- "Clear fault" (API-IRR-13 with the `line_id`; MCP `irrigation_clear_fault`; operators only, no dialog) reads "Sending…", then the card returns to idle or running from `irr_state`; Activity records who cleared it and why.
- A latch raises `irrigation_line_faulted` (warning, category water) in `alert_log`, resolved when the latch clears; a back-off alone raises nothing.

#### US-WAT-23 · Run the wall drip and stop any line before the irrigation firmware
*P1, P2, P3 · MVP · Water, MCP.* As the owner-operator, I want to run the wall drip (L01) and stop any line from the dashboard and MCP before the irrigation engine ships, so that the wall plants get water now (L01 has not run since 2026-05-29).
- Run now on L01 (API-IRR-06; MCP `irrigation_run`) has the ingestor press the existing ESPHome button `btn_wall_clean` over its single native-API session, never through Home Assistant, which runs L01 for the configured `irrig_wall_duration_min` (named in the confirm dialog); Stop on any line and Stop all water (API-IRR-07; MCP `irrigation_stop`) go through the same bridge, which turns the open wet relays off (their guards block only turn-on), and the result says VPD misting may pulse again. Both are attributed in Activity.
- The L01 card shows the run from the `wall_drips` relay edges, and the minutes count for the wall zone; Run now while L01 is already running is refused with `line_busy` and never presses again (a second press would queue a second full run).
- Until then the other lines' Run, Clear fault and the bursts read "arrives with the irrigation firmware". Once the engine ships, L01 is the first line scheduled, run-now and Stop move to the engine, and the HA RUN CLEAN buttons go in that release (D-IRP-24).

### SET · Settings

#### US-SET-01 · One catalog of every setting
*P1 · MVP · Settings.* As the owner-operator, I want one searchable list of every setting with its device value, so that "control all settings" starts from a complete list.
- API-09 lists every `REGISTRY` row in `verdify_schemas/tunable_registry.py` by `control_class` with unit, min, max, step, plan value, device value and age; a drift test lists every number and switch in `firmware/greenhouse/tunables.yaml` and fails unless each has a `REGISTRY` row (writable, or read-only with a reason) or retires in the irrigation firmware release, so the catalog really is every setting.
- Each row is editable or read-only with the reason: `readback_context` rows, among them `sw_onchip_band_enabled`, which decides which side owns the band; `retired` rows; values the dispatcher derives (the 3 activity-mirror values, and the legacy wide limits `temp_low`, `temp_high`, `vpd_low`, `vpd_high` and `vpd_target_*`, which are not the band while the controller runs its on-chip band); and the legacy feed-commissioning rows that retire with the engine. The band in force shows as one read-only "Band in force" group, house and per zone.
- A row reads NO READBACK only when it has no `cfg_*` readback, no native entity readback and no `equipment_state` mirror (switches confirm from `equipment_state`); search matches names and plain labels.

#### US-SET-02 · Requested and confirmed, side by side
*P1, P3 · MVP · Settings.* As the owner-operator, I want each setting to show the device value next to any pending request, so that I never mistake a request for a fact.
- The device value is the newest `cfg_*` readback in `setpoint_snapshot`, in canonical decimals (float32 1.6 shows as 1.6), with its age; a readback matches within half a grid step, not a percentage dead-band, so `site_pressure_hpa` 840 → 845 reads REQUESTED until the device reports 845.
- A request reads REQUESTED, then CONFIRMED, or NOT CONFIRMED with no match within 5 minutes, and a late readback turns it CONFIRMED by itself; the dispatcher re-sends a differing value on every pass, so there is no "Send again". The requested value never replaces the device value until confirmed.
- A REQUESTED row older than 60 s, or a NOT CONFIRMED one, shows the platform's reason when it has one: "Clamped to 2.00 kPa: <guard>" (`setpoint_clamps`), "Waiting: controller heap low" or "Push failed: <status>" (`setpoint_changes.delivery_status`), "Sent HH:MM", "Held by experiment <id> until it ends", or "Ingestor not running since HH:MM"; otherwise "No matching readback yet".

#### US-SET-03 · Change a setting
*P1 · MVP · Settings.* As the owner-operator, I want to change any editable setting from the dashboard, so that I never need SQL, ESPHome or Home Assistant for it.
- `PUT /v1/settings/{param}` (API-10) checks min/max, snaps to the grid (`mister_engage_kpa` 1.53 is "saved as 1.55, snapped from 1.53"; a value equal to the setting's current plan or device value is stored as is, so taking Iris's 48.75 s manual keeps 48.75) and appends a `desired_state` row with actor, manual and optional reason; out of range returns `out_of_range` with the bounds.
- A crop-band anchor or width (house `band_*`, `zone_vpd_target_*`, `zone_vpd_width_*`) also updates, in the same transaction, the `crop_band_anchors` rows the dispatcher reads (the best-matching season; a width updates every anchor row of its series), and the next band read draws the new band; the row then reads REQUESTED → CONFIRMED from its `cfg_*` readback.
- With the controller online and nothing else waiting, a valid change reads CONFIRMED within 2 minutes (the dispatcher is woken by `desired_state_changed` and sends at most 12 changes a pass, never waiting for approval); sending the same change twice with one `Idempotency-Key` creates one row.

#### US-SET-04 · Take a setting manual
*P1 · MVP · Settings, Plan.* As the owner-operator, I want a manual flag that keeps Iris off a setting, so that I override the planner without fighting it.
- The edit drawer opens with "Manual: Iris skips this" ticked; unticking it saves manual = false. With manual on, the value beats Iris's plan whatever its age: the dispatcher keeps it, Iris's context lists it under MANUAL SETTINGS, Iris's `set_setting` on it is refused with `manual` (`set_plan` and `set_tunable` are unchanged), and it shows MANUAL in Settings and on the Plan lanes.
- Without manual, the newest write wins: a newer Iris plan or one-shot replaces it and the row reads "Iris changed this at HH:MM" (a forecast-rule value reads "Forecast rule · system", never AI); a non-manual write newer than the plan in force holds until Iris's next plan, and the "Next (plan)" cell reads "superseded until Iris's next plan".
- "Release to auto" (API-11) appends a `value = NULL` row, so plan, band or default applies on the next pass; that holds for every writable class, the `gl_*` lighting rows included, whose next pass applies the lighting policy's own value, not the released one.

#### US-SET-05 · Values survive reboots
*P1 · MVP · Settings.* As the owner-operator, I want my settings re-applied after an ESP32 reboot or OTA, so that a power blip does not quietly undo a decision.
- After a reconnect the dispatcher compares every desired value with the live readbacks and re-sends the ones that differ, `restore_value: no` settings such as `fog_rh_ceiling_pct` included, in batches of at most 12 per pass with readback; it never waits for an approval file or manual custody.
- After an ESP32 reboot or OTA, each desired setting reads CONFIRMED again within one dispatcher pass (300 s) per 12 differing values, with no one acting; the dispatcher's reboot re-push test covers it, and the engine release checks live that a manual setting written before the OTA reads CONFIRMED after it.

#### US-SET-06 · Setting history and revert
*P1 · Next · Settings.* As the owner-operator, I want a history chart per setting and a one-step revert, so that I see what changed and can undo it.
- API-12 charts requests stepped by source, the readback line (`setpoint_snapshot` keeps 90 days), Iris's planned values dashed ahead and the min/max bounds.
- Revert writes the previous value (or releases to auto) through the normal change path.

#### US-SET-07 · Preview a band edit
*P1 · Next · Settings.* As the owner-operator, I want a curve preview when I edit band anchors, so that I change the climate target deliberately and can undo it.
- Editing an anchor value or width previews the diurnal curve from `fn_crop_band_value` before saving; the save itself is the ordinary change (US-SET-03).
- The previous band is one revert away.

### ACT · Activity and alerts

#### US-ACT-01 · One activity log
*P1, P4 · MVP · Activity.* As the owner-operator, I want every write by anyone in one time-ordered log, so that I always know who changed what and why.
- `activity_log` (API-17) records every write with source (`dashboard`, `iris`, `agent:<name>` or `system`), actor, action (`setting.write`, `setting.release`, `plan.write`, `alert.ack`, `alert.resolve`, `irrigation.*`), target, before → after, reason and readback outcome; an Iris plan is one entry listing its changes. Iris `set_tunable` one-shots and forecast-rule writes (source `system`, actor `forecast-engine`) are included: a test writes through `set_plan`, `set_tunable` and the forecast engine and finds exactly one row for each.
- Filters by source, actor, action and target; every setting and line links to its filtered log.
- A write made while an experiment is armed (a `control_experiments` row armed or running) carries that experiment id on its own row and shows as a deviation from it; nothing is refused.

#### US-ACT-02 · Open alerts
*P1, P2 · MVP · Activity.* As a caretaker, I want the open alerts in plain words with their age, so that I know what needs someone.
- API-14 lists every `v_open_alerts` row by severity (critical, warning, info), then age, with a plain title, zone or line, first seen and the current value where the alert has one; repeats of one alert type and zone show as one row with a count and first and last seen, acknowledged ones collapse into "Acknowledged, still open (N)", and "recently resolved" covers the last 24 h. The nav, Now, Activity, API-02 `alerts`, API-14 and MCP `alerts` share one open-alert count: these rows nobody has acknowledged (disposition `open`), repeats counted once; a probe that stays offline keeps one row and auto-resolves after a set period (Round 2b C).
- "No open alerts" shows only when the read succeeded, the ingestor heartbeat is fresh and the newest `climate` row is at most 300 s old. With the heartbeat stale, Now's "Needs someone" and the alert list read "In-app alerts may be missing: ingestor not running since HH:MM"; with the controller not reporting, "In-app alerts may be missing: no data since HH:MM"; a failed read shows "Alerts unavailable".

#### US-ACT-03 · Alert detail in place
*P1 · MVP · Activity.* As the owner-operator on call, I want each alert to open in place with the data around it, so that I decide within a minute whether to act.
- Expanding an alert in Activity (addressable as `/activity?alert=<id>`) shows its fields, the related equipment or line state and the runbook from `slack_alert_runbooks` when one exists.
- "Open in Graphs" opens the ±2 h window (climate panels and equipment lanes, including the wet relays and the `water_flowing` and `leak_detected` flags).
- `irrigation_fence_breach` shows the open valves and Stop all water; the water-flowing-with-every-valve-closed alert shows the gallons in the last 10 min and "Close the spigot" instead, since no Verdify valve is open.

#### US-ACT-04 · Acknowledge and resolve
*P1, P2 · MVP · Activity.* As an operator, I want to acknowledge and resolve alerts under my name, so that others know it is handled.
- Acknowledge (API-15) and resolve (API-16) each take an optional note of up to 500 characters and record the person in `alert_log` and in Activity; viewers see but cannot act.
- Resolve is never refused, `planner_tool_dependency_failed` included; an alert whose condition persists re-opens on the next monitor cycle.
- Slack keeps posting as it does today.

#### US-ACT-05 · Platform alerts beside greenhouse alerts
*P4, P1 · Later · Activity.* As the platform maintainer, I want Alertmanager's Verdify alerts in the same list, so that a dead writer or database is visible where we look.
- Alertmanager alerts for Verdify (monitoring-stack rules) appear read-only, labelled "platform"; until this ships they stay in Alertmanager and Slack.
- An unreachable Alertmanager reads "Platform alerts unavailable", never as none.

### EQP · Equipment

#### US-EQP-01 · Equipment board
*P1, P2 · MVP · Equipment.* As the owner-operator, I want every actuator's state, since-time and today's runtime, so that I see what the controller is doing.
- API-13 (from `equipment_state`) shows fan1, fan2, vent, heat1 (electric), heat2 (gas), the wet relays L01–L11 (fog and the fertilizer master included), `grow_light_main` and `grow_light_grow` as on, off or unknown, with the age of the last report; since-time, and runtime and cycles since America/Denver midnight, come from the collapsed on/off edges.
- A state is known while the controller reports, as in US-NOW-03; it reads "unknown" only while the controller is not reporting or the actuator has no row, never "off".
- Grow lights show both the Lutron readback (`grow_light_main`, `grow_light_grow`) and the controller's intent (`grow_light_main_intent`, `grow_light_grow_intent`); with Home Assistant down the Lutron state reads unknown, never off.

#### US-EQP-02 · Why it is on or off
*P1 · Next · Equipment.* As the owner-operator, I want the reason behind each actuator's state, so that I tell control from fault.
- `mode_reason`, `fog_block_reason` and `moisture_block_reason` (`system_state`) show as words from one mapping table; an unmapped code shows raw.
- Each reason links to the chart window where it started.

#### US-EQP-03 · Controller health
*P4, P1 · Later · Equipment.* As the platform maintainer, I want the controller's vital signs, so that I see a sick ESP32 before it fails.
- Firmware version (`diagnostics.firmware_version`), uptime and last reset reason, free heap and largest block, Wi-Fi RSSI and last seen, each with its age.
- Free heap under the 32 kB floor (`firmware/lib/heap_diagnostics.h`) is highlighted with a 7-day trend.

#### US-EQP-04 · Sensor health
*P1 · Later · Equipment.* As the owner-operator, I want every probe's health and what stands in for a failed one, so that I know which numbers to trust.
- Each climate probe (Modbus addresses 1–6) and soil probe (7–9) shows last reading age, validity, known fault (south, address 4, #751) and the peer or house value standing in.
- The water meter shows its last pulse and today's total.

#### US-EQP-05 · Grow lights by hand
*P1 · Next · Equipment.* As the owner-operator, I want to switch the main and grow lights on, off and back to automatic, so that I light the house for work or a photo.
- On and off go through a new operation, API-20, to `verdify-setpoint-server` (`/lights/{main,grow}/{on,off}`, the Lutron path through Home Assistant that stays, reached by the operator API under its own NetworkPolicy allow); the board shows the confirmed Lutron state.
- "Back to automatic" writes 1 to `sw_gl_main_auto_mode` or `sw_gl_grow_auto_mode` through API-10. Once that reads CONFIRMED, it sends one API-20 command to match the controller's intent when the intent and the Lutron state are both known and differ, because the controller's `apply_light` switches a light only when its own decision changes. Both actions appear in Activity.

#### US-EQP-06 · Bursts and restart from the dashboard
*P1, P2 · MVP · Equipment.* As a caretaker, I want FANS, HUMIDIFY and VENT-BYPASS bursts on the phone, so that I can clear the air while I work, now that the HA buttons are gone.
- Bursts (API-IRR-12, the firmware's `manual_override(kind, minutes)` from irrigation F12, called through the ingestor; they arrive with the irrigation firmware) set only the FANS, HUMIDIFY and VENT-BYPASS deadline latches and never change occupancy, so a burst while someone is inside leaves mist and fog held and Now says why; the physical push buttons drive the same latches.
- A running burst shows on Now and Equipment from the controller's readback (`fan_burst_active`, `fog_burst_active`, `vent_bypass_active`) with the time left; a burst the controller holds off says why from `fog_block_reason` ("Humidify held off: someone inside", "RH above the ceiling"), one during emergency cooling, emergency heating or a sensor fault says it is ignored, and Clear ends it with the readback confirming it off. Each call is in Activity.
- Restart controller (Equipment, under More actions; operators only; no dialog) sends one API-IRR-12 `{kind: "restart"}`; the controller's uptime resets within 3 minutes, and after the reconnect the desired settings are re-sent (US-SET-05) and `irr_program_id` returns `src=nvs` with the same hash; Activity records who restarted it and why.

### PLT · Plants (Later)

#### US-PLT-01 · Zones and crops on a map
*P1, P2 · Later · Plants.* As the owner-operator, I want a greenhouse map with zones, positions, crops, probes and cameras, so that I see what grows where.
- The map draws zones and positions from the topology tables (`zones`, `water_systems`) with each crop and the lines that water it.
- Selecting a crop shows its health trend and its zone's charts.

#### US-PLT-02 · Log an observation with a photo
*P2 · Later · Plants.* As a caretaker, I want to log what I saw or did with a photo from my phone, so that Jason sees it without Slack.
- An observation takes a crop or zone, text and up to 4 photos, attributed to me.
- It appears in Activity and on the crop.

#### US-PLT-03 · Latest camera frames
*P2 · Later · Plants, Now.* As a caretaker away from the greenhouse, I want the latest frame from each greenhouse camera, so that I check the plants without walking out.
- Each frame shows "captured HH:MM (N min ago)" and is marked STALE past its cadence.
- Frames load through the signed-in same-origin path, not the public camera route.

### AUTH · Sign-in and roles

#### US-AUTH-01 · Sign in with Authentik
*P1, P2 · MVP · Sign-in.* As anyone Jason invites, I want to sign in once with my Authentik account and its second factor, so that the dashboard knows who I am with no extra prompts.
- Anonymous `GET /` answers 302 to `/auth/login?returnTo=%2F`, and `/auth/login` answers 302 to Authentik's authorization endpoint from the discovery document of issuer `https://auth.vallery.net/application/o/verdify-dashboard/` (`https://auth.vallery.net/application/o/authorize/`, with `client_id=verdify-dashboard` and `code_challenge_method=S256`; confidential client, redirect `https://dashboard.verdify.ai/auth/callback`); after sign-in it returns to the requested same-origin path.
- After sign-in the browser holds only `__Host-verdify_session` (HttpOnly, Secure, SameSite=Lax, 12 h); no access, ID or refresh token reaches the browser or the logs.
- A person in neither dashboard group is refused at Authentik, and the BFF refuses to create a session.

#### US-AUTH-02 · Two roles from Authentik groups
*P1, P2 · MVP · every page.* As the owner-operator, I want to make each person operator or viewer in Authentik, so that I manage access in one place.
- `verdify-dashboard-operators` gives operator; `verdify-dashboard-viewers` alone gives viewer; both give operator; the header shows the role pill (OPERATOR or VIEWER) and the name.
- A group change applies at the next sign-in (12 h at most); deleting a person's `dashboard_session` rows ends their sessions within 30 s.
- Adding a caretaker needs only Authentik steps (a user, an invitation link and one dashboard group); the member-tier flow has them enrol a second factor, such as a passkey on their own phone, at first sign-in, which lands on Now with the right role pill and no dashboard-side setup. Until jvallery/agents#5754 (passkey-first login) and #5755 (enrolment) land, Authentik identifies people only through Google, so a caretaker signs in with a vallery.net or verdify.ai Google identity.

#### US-AUTH-03 · Viewers cannot change anything
*P2 · MVP · every page.* As a caretaker with the viewer role, I want to see everything without being able to break anything, so that I can look around safely.
- Viewers see every page with the same data and no write controls rendered.
- Any write request from a viewer session returns 403 and changes nothing.

#### US-AUTH-04 · Writes are protected
*P4 · MVP · every page.* As the platform maintainer, I want the web tier to reject forged and cross-site writes, so that a signed-in browser cannot be turned against the greenhouse.
- A non-GET request to `/api/*` without `Origin: https://dashboard.verdify.ai` gets 403, and without a session 401; `X-Authentik-*` and bearer headers are ignored.
- `/api/*` and `/auth/*` responses carry `Cache-Control: no-store`; `POST /auth/logout` deletes the session row and ends the Authentik session.

### AGT · Agents through MCP

Irrigation tool names, arguments and payloads are owned by [irrigation](irrigation.md) §8.2; settings, activity and
the audiences by [architecture](architecture.md) §5.6.

#### US-AGT-01 · Read the water program through MCP
*P3 · MVP · MCP.* As an agent, I want the irrigation state, schedule and history on MCP, so that I reason from the same numbers people see.
- `irrigation_status` returns what API-IRR-01 returns: lines with state, flags, VPD source, pause, fault and last water, active runs, the next occurrences, program sync, today's minutes and cap use, with `server_time` and each line's `state.observed_at`.
- `irrigation_schedule` returns schedules and the next 1–7 days of occurrences (as API-IRR-02); `irrigation_history` returns runs (31 days at most) and minutes by zone, line or cause (400 days at most); answers match the Water screen for the same instant (one service layer, US-PLAT-05), and unknown is `null`, never 0.
- `activity(actor=<me>, since)` returns my writes with their readback state; a later change to the same target by a person or another agent appears there, with before → after, within 60 s.

#### US-AGT-02 · Change water through MCP
*P3 · MVP · MCP.* As an agent, I want one irrigation MCP surface for every water write, so that I can manage watering without a person.
- `irrigation_schedule_set` (create, edit or delete), `irrigation_line_set` (enables, mister flags, caps and pause; `line_id` all), `irrigation_run`, `irrigation_stop` and `irrigation_clear_fault` run the dashboard's validation with no agent-specific caps, envelopes or approvals, and show on Water and in Activity within 60 s; the site caps go through `set_setting`, and bursts have no MCP tool.
- Every agent, Iris included, uses the same tools, and `irrigation_stop` is accepted from any agent at any time, on every line (through the bridge before the engine, US-WAT-23); until the controller has the irrigation engine, run on any line but L01 and clear return `unavailable` (not retryable) and store nothing.
- Table writes return the object with `sync` `requested` until the program hash matches, then `confirmed`; run, stop and clear return a command that goes from `pending` to `sent` or `failed`, and the line's `irr_state` and relay edge show whether water started or stopped.

#### US-AGT-03 · Every agent has a name, and any one can be cut off
*P3, P1, P4 · MVP · MCP.* As the owner-operator, I want every agent write attributed to that agent and any one agent easy to cut off, so that Activity tells me which agent did what and a misbehaving agent cannot keep overriding me.
- A new `agent` audience joins `KNOWN_AUDIENCES` (`mcp/server.py`); each agent's own token resolves to source `agent:<name>` (the admin token to `agent:admin`), Iris stays `iris`, and a write with no resolvable identity is refused.
- Agents run in the cluster and reach `http://verdify-mcp.verdify-prod.svc:8000/mcp` from a pod admitted by the one added `allow-mcp-from-named-agents` NetworkPolicy; Jason names the first agent when its key is sealed, and off-cluster agents are out of scope. Token values live in the jvallery/agents KSOPS lane, never in Git or a browser.
- Removing an agent's key from Secret `verdify-mcp-agents` refuses that agent within 3 minutes of the change syncing, and adding one admits a new `agent:<name>`, both without restarting MCP; Iris and the other agents keep working, and Activity filtered to `agent:<name>` shows no write after the removal.

#### US-AGT-04 · A clear yes or no
*P3 · MVP · MCP.* As an agent, I want structured accept and reject answers, so that I correct myself instead of looping.
- A rejection carries a stable `code` from the OpenAPI `ProblemCode` list with `detail`, the field, the bounds, the current value and `retryable` (true only where waiting helps, such as `manifold_busy`): settings use `unknown_target`, `read_only` and `out_of_range`; water uses `unknown_target`, `out_of_range`, `check_required`, `too_close`, `too_many_schedules`, `not_schedule_member`, `line_disabled`, `line_busy`, `manifold_busy`, `cap_reached`, `line_paused`, `line_faulted` and `unavailable`; Iris also gets `manual`.
- An acceptance returns the stored value after snapping.
- A retry with the same `request_id` and payload returns the stored result (`replayed: true`); the same `request_id` with a different payload is refused with `request_id_reused`.

#### US-AGT-05 · Iris respects manual
*P3 · MVP · MCP.* As Iris, I want to know which settings and irrigation items a person took manual, so that I never overwrite a person's choice.
- The planner context gains a "MANUAL SETTINGS: held by a person; your plan values for these are ignored" section (parameter, value, who, since) from `v_desired_state_current`, plus the manual schedules and lines, and tells Iris to keep every Tier-1 parameter in its plans, as `set_plan` requires.
- `set_plan` and `set_tunable` stay exactly as today (same `setpoint_plan` rows and replies, no refusal), and the dispatcher keeps the person's value on a manual setting; Iris's `set_setting` on a manual setting and its irrigation writes to a manual schedule or line are refused with `manual`.
- Other agents and people see the flag but are not stopped by it; their write stores the flag it carries (MCP defaults to off), and Activity shows "manual cleared" when one clears it.

#### US-AGT-06 · Iris plans with water
*P3 · MVP · MCP.* As Iris, I want irrigation state in my context and the irrigation tools in my allowlist, so that I plan water and climate together.
- Section 11 of the context pack (`scripts/gather-plan-context.sh` and its configmap copy) prints, from the same line-state view as `irrigation_status`, one compact table per line (enabled, flags, manual, paused, fault, today's minutes against cap, last outcome), the next 24 h of occurrences and the manual schedules, replacing the retired views `v_irrigation_schedule_current` and `v_irrigation_fertigation_runs`; on a fixture its line fields equal `irrigation_status`'s, it stays within about 60 lines, and `make planner-dry` renders it.
- The `iris` audience gets the irrigation tools in lockstep across `HERMES_REQUIRED_TOOLS` and `TOOL_AUDIENCES` (`mcp/server.py`), `hermes/iris/config.yaml`, `deploy/k8s/components/hermes-iris/hermes-config.yaml` and `tests/test_mcp_audience_auth.py`, checked with `make planner-dry`.
- Iris's irrigation writes carry its trigger id and show as `iris` (AI) in Activity; they never satisfy or fail a planning trigger (Iris still ends each trigger with `set_plan` or `acknowledge_trigger`), and each tool's description says so.

#### US-AGT-07 · Agents change settings within bounds
*P3 · MVP · MCP.* As an agent, I want to set any writable setting within registry min/max, so that routine tuning needs no person and no SQL.
- `set_setting(param, value or release=true, manual=false, reason, request_id)`, open to the `iris`, `agent` and `admin` audiences, validates through `verdify_services.settings` and appends one `desired_state` row attributed to the caller (`iris` or `agent:<name>`); it needs no `trigger_id`, accepts any writable row and returns the stored value after snapping, with state `requested`. It refuses Iris with `manual` on a manual setting; anyone else's write wins.
- Iris keeps `set_plan` and `set_tunable`, which write `setpoint_plan` as today: an Iris one-shot is still an `iris-oneshot` plan row that expires as before.
- `settings(param?)` returns the API-09 rows, including `applied_from`, so an agent sees whether its own value or a newer Iris plan is in force; a write to a read-only row, the legacy feed-commissioning rows included, is refused with `read_only`.

#### US-AGT-08 · Tools explain themselves
*P3 · MVP · MCP.* As an agent, I want each tool to describe its arguments, bounds and refusals, so that I write valid requests without out-of-band docs.
- With an agent token, `tools/list` returns exactly the agent allowlist, and every new tool has typed parameters (enums for `line_id`, `start.kind`, `view` and `group_by`; never a JSON string) and a description naming its REST twin, its manual default and its refusal codes; the server instructions add one paragraph on `set_setting`, the irrigation tools, `manual`, `request_id`, the stable codes and "irrigation_stop always works".
- `irrigation_schedule` returns the bounds per line kind and the limit of 24 schedules; `settings` returns min, max and step per row.
- A test fails if a new tool (`set_setting`, `settings`, `activity`, `irrigation_*`) takes a free-form JSON string argument or has an empty description.

### PLAT · Platform

#### US-PLAT-01 · Its own namespace and Argo app
*P4 · MVP · platform.* As the platform maintainer, I want the dashboard in its own namespace with its own Argo app, so that UI releases never ride the device-gated `verdify-prod-dark` sync.
- Namespace `verdify-dashboard` renders `deploy/k8s/dashboard/` through an auto-synced Application and AppProject declared in jvallery/agents; syncing it never touches `verdify-prod` or the ingestor.
- PSA restricted, and NetworkPolicies in the network-infra#839 pattern already on `verdify-prod`: `egress-deny-management` plus the allows the web needs (traefik-apps to web; web to the operator API, auth.vallery.net through traefik-apps, and DNS).

#### US-PLAT-02 · dashboard.verdify.ai reaches the dashboard
*P4 · MVP · platform.* As the platform maintainer, I want the hostname routed to the dashboard and nowhere else, so that it works from the internet and the LAN.
- One PR adds ``!Host(`dashboard.verdify.ai`)`` to the tier-1 HostRegexp exclusions in `deploy/k8s/overlays/prod/traefik/verdify-tier1-apps-forward.yaml` and to `tests/test_verdify_tier1_owner_exclusions.py`; the dashboard's own IngressRoute on traefik-apps serves it with the `*.verdify.ai` wildcard.
- jvallery/network-infra's verdify.ai zone carries `dashboard.verdify.ai` (proxied to the tunnel from the internet, 192.168.7.10 on the LAN), and the tunnel's `*.verdify.ai` rule covers it; from both, anonymous `GET /` answers 302 to `/auth/login?returnTo=%2F`.

#### US-PLAT-03 · A private operator API
*P4 · MVP · platform.* As the platform maintainer, I want the dashboard's API on an internal-only listener, so that private endpoints never reach the public edge.
- `verdify-operator-api`, built from the `verdify-api` image, listens on `:8090` with no IngressRoute; a NetworkPolicy admits only the web pods, which also present a shared bearer token.
- `api.verdify.ai`, which forwards every path to `verdify-api:8080`, answers 404 for every operator path.
- It uses `verdify_dashboard_runtime_login` (`statement_timeout` 10 s, `CONNECTION LIMIT 8`, no INSERT on `setpoint_changes`).

#### US-PLAT-04 · Every write goes through desired state and the single writer
*P4, P3 · MVP · platform.* As the platform maintainer, I want dashboard and agent writes to reach the ESP32 only through the ingestor, so that two writers never touch the device.
- A setting write is one append-only `desired_state` row (value, manual, source, actor, reason) whose insert sends the wake-only `NOTIFY desired_state_changed`; irrigation writes land in the irrigation tables that `irrigation_sync` compiles on its own wake and every 60 s; no dashboard or MCP login can insert into `setpoint_changes` (a database test asserts it).
- The ingestor stays the only writer (replicas 1, `strategy: Recreate`, `VERDIFY_DEVICE_WRITE_ENABLED=1` only in `overlays/prod`), through its existing fences, and re-applies desired state after every reconnect.

#### US-PLAT-05 · One service layer for MCP and the dashboard
*P4, P3 · MVP · platform.* As the platform maintainer, I want one Python service layer behind MCP and the operator API, so that the same input gets the same answer everywhere.
- `verdify_services/` (`settings`, `activity`, `alerts`, `reads`, `series`, `irrigation`) validates against `verdify_schemas` and is copied into the `verdify-api` and `verdify-mcp` images; TypeScript never re-implements bounds.
- A contract test sends the same writes through MCP and the operator API and gets identical results and `activity_log` rows, apart from source and actor.

#### US-PLAT-06 · A series API built for uPlot
*P4, P1 · MVP · platform.* As the platform maintainer, I want one bounded series endpoint, so that every chart is fast and honest.
- API-04 takes up to 8 metrics and a half-open window (400 days at most; past a metric's raw retention, its daily summary) and returns uPlot columns (`t` in epoch seconds; `avg`, `min`, `max`, `count` per metric) from `time_bucket_gapfill`, 1,500 points by default; gaps are `null`.
- Over 31 days, metrics with a daily binding read `daily_summary`; a 30-day, six-metric request returns in under 2 s at p95, and a timeout returns 503 "narrow the window".
- Column names never come from the browser: only metric registry keys.

#### US-PLAT-07 · One switch for dashboard writes
*P4 · MVP · platform.* As the platform maintainer, I want one deploy flag that turns dashboard writes off, so that I roll back behaviour without a release.
- With `DASHBOARD_WRITES_ENABLED` set to `"false"` in the generated, hash-suffixed ConfigMap `verdify-dashboard-web`, the BFF refuses every non-GET `/api/*` request except irrigation Stop with 503 `writes_disabled`, `/api/v1/me` returns `writes_enabled: false` and a banner reads "Changes are switched off; reading still works".
- Merging a one-line change to the flag rolls `verdify-dashboard-web`, and within 5 minutes of the merge `/api/v1/me` reports the new value; MCP and Iris are unaffected.

#### US-PLAT-08 · Build, deploy and verify
*P4 · MVP · platform.* As the platform maintainer, I want a merge to become a verified live release, so that "deployed" means working, not merged.
- `verdify-dashboard-web` is built in-cluster by the verdify-platform CI (Kaniko to `registry.vallery.net`), its Node checks and Playwright with axe run on every PR that touches `dashboard/**` or the OpenAPI file, and its digest is pinned in `deploy/k8s/dashboard/` by a pin PR; the web ships when that PR merges (the Deployment sits at 0 replicas until R1's pin PR sets the image and scales it to 1).
- Done means the `verdify-dashboard` app is Synced and Healthy at a main revision that contains the pin commit, pods run the pinned digest, `/readyz` and the footer report the commit that image was built from, and `dashboard-smoke` passes (a viewer session created inside the web pod, live Now data through the BFF, the Origin check enforced, `https://api.verdify.ai/v1/me` 404); Jason checks a passkey sign-in once, at R1.
- The Authentik application, the KSOPS-sealed Secrets and the Argo Application and AppProject ship as jvallery/agents PRs and count as done once the coordinator's consolidated pin PR has delivered them and they are Synced and Healthy.

#### US-PLAT-09 · The dashboard is watched
*P4 · MVP · platform.* As the platform maintainer, I want an external probe on the dashboard, so that we learn it is down before a person does.
- jvallery/monitoring-stack's route-coverage probe requests `https://dashboard.verdify.ai/` without following redirects and expects 302 to `/auth/login`; its existing alert fires after 5 minutes of failure.
- Logs carry hashed user ids and no secrets.

#### US-PLAT-10 · Weather's stack and look
*P4, P1 · MVP · every page.* As the platform maintainer, I want to copy the weather.vallery.net stack and look, so that we build fast on code that already works.
- React 19 and TypeScript, React Router framework mode with SSR on an Express BFF (`server.mjs`), TanStack Query, zod, uPlot, IBM Plex and the vallery.net tokens, copied from weather with provenance ([design system](design-system.md)); light theme only.
- Polling is visibility-aware: Now about every 15 s, REQUESTED rows every 5 s, other pages every 60 s; no SSE.
- vitest, and Playwright with axe at 320, 768 and 1440 px, run in CI.

#### US-PLAT-11 · Typed contract and fixture API
*P4 · MVP · platform.* As the platform maintainer, I want the API contract generated and checked, so that web and API cannot drift.
- `openapi/dashboard-api.v1.yaml` is hand-maintained and canonical; any PR that changes it re-renders the TypeScript client in the same PR, and a test asserts that every served route's method, path, `operationId` and schema equal the committed file (committed operations not yet served are allowed).
- A fixture API (weather's `e2e/fixture-api.mjs` pattern) runs e2e tests with no database or device.

#### US-PLAT-12 · Irrigation firmware ships when it is ready
*P4 · MVP · platform.* As the platform maintainer, I want to flash the irrigation firmware as soon as it is ready, so that calendar rules do not hold up the water program.
- The one release (irrigation F1–F14) ships through `make firmware-deploy`; if the preflight's weekly or 48-hour rule blocks a fix-forward, `FIRMWARE_OTA_FREEZE_OVERRIDE_REASON` is set to "irrigation v2 fix-forward (D-IRP-23)".
- Verified by the `diagnostics.firmware_version` query, then `irr_program_id` showing `src=rom` before the first push and `src=nvs` after it; once the first push confirms and the first scheduled run lands, `make firmware-promote-last-good` makes the engine firmware last-good.
- Until then a rollback (`make firmware-rollback`, or the deploy's own) means pre-engine firmware: the platform sees the engine services missing, reports "engine not on controller" (`program.src` null), runs L01 and stops every line through the bridge (US-WAT-23) and answers other runs and clear with `unavailable`, never a 202 that silently expires.

#### US-PLAT-13 · Home Assistant only for occupancy and Lutron
*P4 · MVP · platform.* As the platform maintainer, I want Home Assistant reduced to occupancy reads and the Lutron switches, so that no greenhouse feature depends on a second control path (D-IRP-24).
- Every touchpoint in [irrigation](irrigation.md) §7 (HA-01 to HA-13) is removed or replaced, keeping only occupancy, Lutron (`verdify-setpoint-server`, the firmware grow-light calls and the light readback) and the YINMIK hydroponic probe until a direct reader without Home Assistant replaces it ([decisions](decisions.md), Round 2); `ha_sensor_sync` then reads only lights, occupancy and hydro. Outdoor weather already comes from the Tempest through the ESP32's UDP listener, so the HA `tempest_sync` remnant goes.
- No dashboard or MCP feature calls Home Assistant except the grow-light route; `docs/SERVICE_MAP.md` and `scripts/health-check.sh` change in the same PRs.

#### US-PLAT-14 · Roll back a release
*P4 · MVP · platform.* As the platform maintainer, I want a known way to back out a bad release, so that a broken release costs minutes, not a day.
- Web: a PR reverting the offending commit, or the pin commit, merges; within one Argo refresh `verdify-dashboard` runs the previous digest and `/readyz` reports the previous source revision.
- `verdify-prod`: api, mcp and ingestor (and the orchestrator if needed) return to the promotion receipt's rollback pins in a digest-only `release-pins.yaml` commit, `verdify-migrate` keeps a digest whose image contains every applied migration, the full sync completes and `scripts/k3s-smoke.sh smoke` passes against the rolled-back API digest.
- Firmware rolls back as in US-PLAT-12; the procedure is written into AGENTS.md "Deliver and verify", and ARGOCD.md lists the `verdify-dashboard` Application.

## 7. Persona coverage matrix

| Need | Stories |
|---|---|
| P1-N1 | US-NOW-01, US-NOW-02, US-NOW-03, US-NOW-04, US-NOW-05, US-NOW-06, US-NOW-07, US-NOW-08, US-EQP-01, US-GRF-06 |
| P1-N2 | US-GRF-01, US-GRF-02, US-GRF-03, US-GRF-04, US-GRF-05, US-GRF-07, US-GRF-08, US-GRF-09, US-GRF-10, US-PLAT-06 |
| P1-N3 | US-SET-01, US-SET-02, US-SET-03, US-SET-05, US-SET-06, US-SET-07, US-EQP-05 |
| P1-N4 | US-PLN-01, US-PLN-02, US-PLN-03, US-PLN-04, US-PLN-05, US-SET-04 |
| P1-N5 | US-WAT-01, US-WAT-02, US-WAT-06, US-WAT-07, US-WAT-08, US-WAT-09, US-WAT-12, US-WAT-13, US-WAT-14, US-WAT-15, US-WAT-19, US-WAT-21, US-WAT-23 |
| P1-N6 | US-WAT-10, US-WAT-11, US-WAT-16, US-WAT-17, US-WAT-22 |
| P1-N7 | US-WAT-03, US-WAT-04, US-WAT-05, US-WAT-20, US-GRF-09 |
| P1-N8 | US-ACT-01, US-ACT-02, US-ACT-03, US-ACT-04, US-WAT-18, US-WAT-22, US-EQP-02, US-EQP-04, US-AUTH-02, US-AGT-03 |
| P2-N1 | US-NOW-01, US-NOW-06, US-NOW-08 |
| P2-N2 | US-NOW-03, US-NOW-04, US-WAT-01, US-WAT-02, US-WAT-03 |
| P2-N3 | US-WAT-13, US-WAT-12, US-WAT-14, US-WAT-23, US-EQP-06 |
| P2-N4 | US-WAT-16, US-WAT-02, US-NOW-03, US-EQP-06 |
| P2-N5 | US-NOW-07, US-NOW-08 |
| P2-N6 | US-ACT-02, US-ACT-04 |
| P2-N7 | US-AUTH-01, US-AUTH-02, US-AUTH-03 |
| P2-N8 | US-PLT-01, US-PLT-02, US-PLT-03 (all Later) |
| P3-N1 | US-AGT-01, US-PLAT-05 |
| P3-N2 | US-AGT-02, US-WAT-23 |
| P3-N3 | US-AGT-07, US-PLAT-04, US-SET-02 |
| P3-N4 | US-AGT-04 |
| P3-N5 | US-AGT-05, US-SET-04, US-WAT-07 |
| P3-N6 | US-AGT-03, US-ACT-01 |
| P3-N7 | US-AGT-06 |
| P3-N8 | US-AGT-08 |
| P3-N9 | US-AGT-01, US-AGT-02 |
| P4-N1 | US-PLAT-01, US-PLAT-08 |
| P4-N2 | US-PLAT-08, US-PLAT-09, US-PLAT-02 |
| P4-N3 | US-PLAT-04, US-PLAT-05, US-PLAT-03 |
| P4-N4 | US-PLAT-07 |
| P4-N5 | US-NOW-02, US-PLN-03 (MVP); US-EQP-03, US-ACT-05 (Later) |
| P4-N6 | US-PLAT-10, US-PLAT-11, US-PLAT-06 |
| P4-N7 | US-PLAT-12, US-WAT-15 |
| P4-N8 | US-PLAT-03, US-AUTH-01, US-AUTH-04, US-PLAT-13 |
| P4-N9 | US-PLAT-14 |
| P4-N10 | US-AGT-03 |
