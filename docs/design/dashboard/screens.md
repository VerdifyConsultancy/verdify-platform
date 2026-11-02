# Dashboard screens

dashboard.verdify.ai has nine screens: Now, Graphs, Plan, Water, Settings, Activity and alerts, Equipment and
controller, Plants (Later) and Sign-in and errors. Desktop uses weather.vallery.net's top nav; phones get a five-tab
bottom bar. Operators and viewers see the same screens and data, and viewers never see a write control. Each spec
gives the purpose, the stories served, a 1440 px wireframe (390 px too where the layout really changes), the panels
with their API operations and refresh, the actions with what the user sees from requested to confirmed, the degraded
states and the acceptance criteria. Every write follows one model: validate on submit, last write wins, a Manual flag
that Iris skips, and REQUESTED → CONFIRMED from device readback; the one dialog confirms starting water. A
story-to-screen map and the issues that build each screen close the doc.

Status: Draft v2 (2026-11-02; Round 2, 2b and Q-01 to Q-14 applied)

[README](README.md) · [product](product.md) · **screens** · [architecture](architecture.md) ·
[irrigation](irrigation.md) · [design system](design-system.md) · [execution plan](execution-plan.md) ·
[OpenAPI](openapi/dashboard-api.v1.yaml)

Operation ids (API-NN, API-IRR-NN, BFF-NN, INT-NN, OPS-NN) are the [OpenAPI](openapi/dashboard-api.v1.yaml) file's,
which is canonical. Stories are [product](product.md) §6. Components, badges and states are the
[design system](design-system.md)'s. "Round 2 #N", "Round 2b X" and "Q-NN" are entries in [decisions](decisions.md),
which wins over this doc. Wireframe values are illustrative; "steward" stands in for the first named agent (VD-23).

## 1. Information architecture

**Answer:** nine flat screens, each one tap from the nav. Detail opens in a drawer, a sub-route or in place; there are
no wizards and no screen exists only to confirm something.

| Id | Screen | Routes | Answers | Build |
|---|---|---|---|---|
| SCR-01 | Now | `/` | Is everything OK? What is running? Is water fine? | MVP |
| SCR-02 | Graphs | `/graphs`, `/graphs/scorecard` (Next: `/graphs/zones`) | What happened and what is planned, over any range? How is each day going? | MVP |
| SCR-03 | Plan | `/plan` | What will Iris tell the controller, and is Iris healthy? | MVP |
| SCR-04 | Water | `/water`, `/water/schedules`, `/water/history` | What waters, when, why not, and how many minutes per zone? | MVP |
| SCR-05 | Settings | `/settings`, `/settings?edit=<param>` | What is every setting, what does the device report, and change it | MVP |
| SCR-06 | Activity and alerts | `/activity`, `/activity?alert=<id>` | What needs someone? Who changed what? | MVP |
| SCR-07 | Equipment and controller | `/equipment` | What is each actuator doing? Bursts and restart | MVP |
| SCR-08 | Plants | `/plants` | What grows where? | Later |
| SCR-09 | Sign-in and errors | `/auth/login`, `/auth/callback`, error pages | Who are you, and what went wrong? | MVP |

### 1.1 Navigation

```text
1440  [leaf]erdify · DASHBOARD  Now  Graphs  Plan  Water  Settings  Activity 2  Equipment   [OPERATOR] Jason  Sign out
      ━━━━ banner row, only while one is true (§1.2) ━━━━

390   [leaf]erdify · DASHBOARD                          [OPERATOR]
      … page …
      ┌─────────┬─────────┬─────────┬────────────┬─────────┐
      │   Now   │  Water  │ Graphs  │ Activity 2 │  More   │   fixed bar, 56 px, safe-area inset
      └─────────┴─────────┴─────────┴────────────┴─────────┘
```

| Rule | Spec |
|---|---|
| Desktop (> 860 px) | Weather's header and `SECTIONS` model with Now, Graphs, Plan, Water, Settings, Activity, Equipment; `aria-current="page"`; the one open-alert count (§1.2) as text ("Activity 2") |
| Phone (≤ 860 px) | The header keeps the lockup and role pill. A fixed bottom bar holds Now, Water, Graphs, Activity (with the count) and More, as text labels (no icon set in v2), each at least 44 px. More opens `/more`, a plain list: Plan, Settings, Equipment, Sign out and the build line. Stop all water, Run and Acknowledge are full width |
| Plants | Out of the nav until it ships |
| URLs | Every view state is in the URL (range, settings and metrics on charts, filters, open drawer, Water view, expanded alert), so a copied link reproduces the view |

### 1.2 Shell on every page

| Element | Shows | Data | Refresh |
|---|---|---|---|
| Header tools | Role pill (OPERATOR or VIEWER), name (desktop), Sign out (a POST form) | API-01 | load, 60 s |
| Fault banners, one at a time, worst first | "Dashboard API unavailable" after two failed polls in a row; nothing green shows while it is up. "Ingestor not running since 03:12. The controller keeps running on its own." while the ingestor heartbeat (`freshness.ingestor`, a `system_state` row every 60 s, Q-01) is older than 180 s. "Controller not reporting since 03:12. If it has power, it keeps running VPD misting, its caps and (with the irrigation firmware) its schedules" while the heartbeat is fresh and the newest `climate` row is older than 300 s; no page ever says "Controller offline". "Water flowing with every Verdify valve closed since 03:02 · 3 gal in 10 min: a leak, or the hose, sink or hydro in use. If no one is using water, close the spigot" while the `leak_detected` alert is open, with no Stop (no Verdify valve is open); once the meter reads under 0.1 gal/min twice it reads "Flow stopped at 03:20", never an all clear | API-02 `freshness`, `leak` and alerts | 15 s |
| Warn banners | "Settings not reaching the controller since 06:10 (4 differ)" while `setpoint_delivery_stuck` is open (API-02 `writer.stuck`, VD-62; Round 2 #1); "Changes are switched off; reading still works" (`writes_enabled: false`) | API-02, API-01 | 15 s, 60 s |
| Activity count | The one open-alert count (VD-55, VD-72): unacknowledged (disposition open) `v_open_alerts` rows, repeats of one `alert_type` and zone counted once, so it equals the unacknowledged rows Activity lists (API-02 `alerts.open`). It shows as text in the nav; Now's hero and Alerts tile, Activity and MCP use the same count | API-02 | 15 s |
| Footer | "Verdify dashboard · build `<sha>` · API `<sha>`", graphs.verdify.ai ↗, lab.verdify.ai ↗ | BFF `SOURCE_REVISION`, API-01 | load |
| Toasts | After each write ([design system](design-system.md) §4.5); Stop's stays until dismissed | — | — |

Each banner links to the page that explains it (ingestor → Now's Data tile, not reporting → Equipment, flow → its
alert in Activity, settings → Settings filtered to Requested). Polling is weather's `polling.ts`: paused while the tab
is hidden, backing off from 15 s to 120 s on errors; a chart whose window has closed never refetches.

### 1.3 Roles

- **Operator** (`verdify-dashboard-operators`): every read and every write.
- **Viewer** (`verdify-dashboard-viewers` only): read-only everywhere. Same screens, same data. Write controls (Edit,
  Save, Release, Run, Stop, switches, Pause, Resume, New schedule, Clear fault, Acknowledge, Resolve, bursts,
  Restart) are not rendered, drawers open read-only, and the API answers 403 to any write. The pill reads VIEWER.
- **Changes switched off** (`DASHBOARD_WRITES_ENABLED=false`): operators see controls disabled with the banner text;
  Stop and Stop all water stay live.

### 1.4 Write feedback (all screens)

| Kind | Operations | From requested to confirmed |
|---|---|---|
| **S** Setting | API-10, API-11, the three site caps included | Save closes the drawer and toasts "Requested 1.55 kPa for VPD Mister Engage" ("snapped from 1.53" when snapped). The row reads REQUESTED and polls every 5 s; CONFIRMED when the readback matches (typically 30-90 s); NOT CONFIRMED after 5 min. A REQUESTED row older than 60 s, or a NOT CONFIRMED one, shows the platform's reason when it has one (SCR-05). The dispatcher re-sends on its own, so there is no Send again; a late readback turns the row CONFIRMED by itself. A site cap's state comes from API-09 like any setting's: CONFIRMED once the controller's program hash covers the write, NOT CONFIRMED after 5 min without it; before the irrigation firmware it stays REQUESTED with the `no_engine` reason, "applies with the irrigation firmware" |
| **P** Program | API-IRR-03, -04, -05, -08 | Save; the item and the program banner read REQUESTED ("rev 43 sending"), polled every 5 s; CONFIRMED when `irr_program_id` reports the new hash; "Not confirmed: <reject in words>" after 5 min. Before the irrigation firmware the item is stored and stays REQUESTED with "applies with the irrigation firmware" until `irr_program_id` first appears |
| **C** Command | API-IRR-06, -07, -12, -13 | The button reads "Sending…" until the 202; API-IRR-01 is then polled every 5 s and the readback answers: the relay edge shows RUNNING or OFF (a burst, its readback flag; a cleared fault, the line's state). If nothing has changed within 30 s the item shows the line's reason in words, or "Not started". A command the writer could not send within 2 min is dropped, never sent late |
| **R** Record | API-15, API-16 | Saved at once; the row reads "Acknowledged by Jason · 06:47" |

- A 422 keeps the drawer or dialog open with the code in words beside the field ("Must be 0.50–2.50 kPa"); nothing
  else is sent.
- Every write carries one `Idempotency-Key` header, so a double tap or a retry applies once.
- **Manual.** Every edit form (setting drawer, schedule editor, line options) has one "Manual: Iris skips this" box,
  ticked by default; unticking it lets Iris adjust the item, and Save always sends the flag. An inline switch (line
  enable, VPD control, Schedule) saves manual on as well. Run, Stop, Pause, Resume, Clear fault and bursts carry no
  manual value and never change it. Only Iris skips manual items (Q-02); people and other agents may change them, each
  write storing the flag it carries (MCP's default is off), and Activity says so.
- **Stop result.** Stop and Stop all water leave one toast until dismissed: "Stopped every running valve. VPD misting,
  fog and the next scheduled run can still start on their own; to keep a line off, disable it", with Disable for each
  line that was running (never L03 or fog L11, which have no enable on the board). Before the irrigation firmware it reads "Stopped every running valve. VPD misting and fog can
  still start on their own", with no Disable (a disable applies only with that firmware). While the firmware state is
  unknown (water status not served yet, as at R1, or failing), the toast reads "Stopped every running valve. VPD
  misting, fog and the next scheduled run can still start on their own", also with no Disable. Verdify has no main shutoff:
  a hose, sink or break upstream needs the spigot. If the relays have not read OFF within 30 s, or the controller is
  not reporting, it reads "The controller hasn't confirmed the stop. Close the spigot to stop water now". While the
  ingestor is not running, Stop still saves (202) and reads "Stop saved but not sent: ingestor not running since 03:12.
  Running drips end on their own timer; close the spigot if water must stop now".
- Every write shows in Activity with actor, source and reason. A write made while an experiment is armed is logged
  with the experiment's id and is never refused (Round 2 #2).

### 1.5 States (every panel)

| State | Rule |
|---|---|
| Loading | Skeleton at the panel's reserved height; a refetch keeps the previous render |
| Empty | Says what would appear and why there is none ("No runs today"). An alert list says "No open alerts" only after a successful read while the controller reports. Otherwise it says "In-app alerts may be missing: ingestor not running since 03:12" while the ingestor heartbeat is stale, or "In-app alerts may be missing: no data since 03:12" while the controller is not reporting |
| Stale | Sensor values: age over twice the expected interval (`v_sensor_staleness`): muted value, STALE, "Last data 14 min ago (06:02)"; no verdict from stale data. Equipment and `irr_state` report only on change: the newest row holds while the controller reports (the ingestor heartbeat fresh and the newest `climate` row at most 300 s old) and reads "unknown" with its age only while it does not, or when there is no row |
| Not reporting | The ingestor or controller fault banner (§1.2); device values STALE. Writes still save and apply when data returns; the controller keeps running its last program, VPD misting and caps |
| Before the irrigation firmware | While API-IRR-01 reports no engine (`program.src` null): relay states only; schedule, line and mister edits (§1.4 P) and site-cap edits (§1.4 S) store and wait for it. Run now works on L01 only: the bridge presses `btn_wall_clean` (Round 2 #4, VD-64). Stop on a line and Stop all water work on every line through the same bridge, which turns the open wet relays off (their guards block only turn-on). The other lines' Run, Clear fault, the bursts and restart read "arrives with the irrigation firmware" |
| Error | "Couldn't load runs. Retry" in that panel only; other panels keep working |
| Unknown or null | "—" with the reason, never 0, off or the previous value |

Units and times follow [design system](design-system.md) §5.4 (°F, % RH, kPa, gal, min; America/Denver with the
offset). Every screen passes axe (WCAG 2.2 AA) at 320, 768 and 1440 px with no horizontal page scroll at 360 and
390 px.

## 2. SCR-01 Now · `/`

**Purpose:** the five-second answer: is everything OK, what is running, whether water is fine, and what needs
someone. Everything else is one tap away.
**Stories:** US-NOW-01, US-NOW-02, US-NOW-03, US-NOW-04, US-NOW-06, US-NOW-07, US-NOW-08, US-WAT-13, US-WAT-16,
US-WAT-18, US-WAT-22, US-EQP-06. Next: US-NOW-05, US-GRF-06.

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ GREENHOUSE · LONGMONT · AMERICA/DENVER        ┌ VPD ─────────┬ RH ──────────┬ OUTDOOR ─────┬ MODE ─────────┐ │
│ 1 thing needs someone                         │ 0.62 kPa     │ 71.0%        │ 41.2 °F      │ In band,      │ │
│ 64.8 °F · in band                             │ band .45-.80 │ no RH band   │ RH 58% · 2 m │ holding       │ │
│ band in force 62.0–70.0 °F · DERIVED          ├ IRIS ────────┼ WATER ───────┼ ALERTS ──────┼ DATA ─────────┤ │
│ ● Updated 20 s ago                            │ OK · SUNRISE │ Center mist  │ 1 warning    │ Fresh · 8 s   │ │
│                                               │ to 17:30     │ next Mon     │ fan2 relay   │ writer 41 min │ │
│ › All readings · sources, times, mode reason  └──────────────┴──────────────┴──────────────┴───────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
RUNNING · COMMANDED
[Heat 1 (electric) · since 05:40]  [Center mister · VPD · since 06:58]
[Someone inside: misters wait (Frigate, 4 min ago)]
┌ WATER TODAY ─────────────────────────────────────┐ ┌ NEEDS SOMEONE · 1 ───────────────────────────────────────┐
│ Center 14 min · Wall 0 · South 0 · West 0        │ │ ▌WARNING · fan2 · since 06:02 (40 min)                   │
│ Last water: Wall Fri 07:31, 12 min · Center now  │ │  Fan 2 relay stuck: on with no command                   │
│   (VPD) · South 3 days ago · West 9 days ago     │ │  [Acknowledge]                                  Open ›   │
│ Running: Center mister (VPD)                     │ │ All alerts ›                                             │
│ Next: L04 Mon 06:49 · L01 Mon 07:34, 12 min      │ └──────────────────────────────────────────────────────────┘
│ SAF-4 21 of 360 gal · Unexplained flow: none     │
│ [■ Stop all water]                     Water ›   │
└──────────────────────────────────────────────────┘
```

```text
390 ┌──────────────────────────────────────┐
    │ [leaf]erdify · DASHBOARD  [OPERATOR] │
    │ ▓ GREENHOUSE · AMERICA/DENVER       ▓│   ink hero
    │ ▓ 1 thing needs someone             ▓│   the answer: above the fold at 360 × 740
    │ ▓ 64.8 °F · 0.62 kPa · 71.0%        ▓│
    │ ▓ in band 62–70 °F · ● 20 s ago     ▓│
    │ ▓ MODE In band   │ IRIS OK SUNRISE  ▓│   4 tiles; outdoor and data move
    │ ▓ WATER mist on  │ ALERTS 1 warning ▓│   under "All readings"
    │ [Center mister · VPD · since 06:58]  │
    │ [Someone inside: misters wait]       │
    │ WATER TODAY Center 14 min · Wall 0   │
    │ [■ Stop all water                  ] │   operators
    │ NEEDS SOMEONE · 1 …                  │
    │ Now  Water  Graphs  Activity 1  More │
    └──────────────────────────────────────┘
```

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Hero | The one-sentence answer, worst first, in the banners' order: "Can't reach the greenhouse data" (API-02 failing); "Ingestor not running since 03:12"; "Controller not reporting since 03:12"; "Water flowing with every Verdify valve closed"; a fresh metric outside its band ("Too hot: 84.1 °F (target 62–70 °F)", "Too cold", "Air too dry", "Air too damp"); open alerts nobody has acknowledged, the one count of §1.2 ("2 things need someone"); "Waiting for fresh data (last 14 min ago)"; otherwise "All OK". Under it, indoor temperature as the hero value, VPD and RH, each with its verdict against the band in force (in, above, below, or "band unavailable"); the mode in words, with the raw code and `mode_reason` in the disclosure; "Updated N s ago" | API-02 (`climate`, `system_state` and the band rows; the band in force is the newest house `band_role` rows the dispatcher derives from the anchors, shown DERIVED with their age) | 15 s |
| Tiles (8; 4 on phone) | VPD, RH, Outdoor (the Tempest through the controller, with age), Mode, Iris (newest required trigger OK, late, missed or failed; plan in force and its end), Water (running or idle; next run), Alerts (the one open-alert count of §1.2, by severity), Data (climate age and the ingestor heartbeat's age; the writer's health as the setpoints pipeline age, `v_data_pipeline_health` `setpoints`, Round 2 #1) | API-02; Water from API-IRR-01 | 15 s |
| Running | A chip per running actuator with "since HH:MM"; wet lines add the cause and, with the irrigation firmware, the time left; bursts from the controller's readback ("Fans burst · 12 min left"); and always one occupancy chip: "Someone inside: misters wait (Frigate, 4 min ago)", "Nobody seen: misters may run (last person 05:10)", "Occupancy unknown: misters may run", or, while `occupancy_inhibit` is off, "Occupancy mist hold is off: misters may run" | API-02; occupancy from API-IRR-01 | 15 s |
| Water today | Minutes today by zone (wall, center, south, west); when each zone last got water ("Wall Fri 07:31, 12 min · Center now (VPD) · West 9 days ago"; a zone with no enabled line reads "not set up", never 0); the running line; the next run per enabled line; the flow state in words; SAF-4 used of 360 gal; a mister in watchdog back-off ("South: backing off until 13:20") or latched ("South: faulted, no misting until midnight (watchdog)", with Clear fault) | API-IRR-01 | 15 s |
| Needs someone | Top 3 open alerts nobody has acknowledged, by severity (critical, warning, info) then age: plain title, zone or line, age, Acknowledge (`AlertRow`, shared with Activity), Open | API-14 | 15 s |
| Coming up (Next) | Next plan transition (AI; manual settings left out), next Iris trigger, next run per line (pauses say by whom), sunrise and sunset | API-07, API-IRR-02 | 60 s |
| Zones (Next) | A tile per zone: temperature, RH and VPD against its target, tagged MEASURED, PEER or HOUSE; opens `/graphs/zones` | API-04, API-06, API-IRR-01 | 60 s |

Mode words (one table in the app; an unmapped code shows raw): `IDLE` "In band, holding"; `VENTILATE` "Venting to
cool"; `DEHUM_VENT` "Venting to dry the air"; `SEALED_MIST` with stage `WATCH`, `FOG`, `S1`, `S2` "Sealed, watching",
"Sealed, fogging", "Sealed, misting one zone", "Sealed, misting all zones"; `THERMAL_RELIEF` "Venting a heat burst";
`SAFETY_COOL` "Emergency cooling"; `SAFETY_HEAT` "Emergency heating"; `SENSOR_FAULT` "Sensor fault: equipment off".

| Action (operator) | Operation | Feedback |
|---|---|---|
| Stop all water | API-IRR-07 `{}` | C, then the Stop result (§1.4). One tap, no dialog, never blocked by the writes flag; before the irrigation firmware it works through the bridge (§1.5); wet chips turn OFF from relay edges within 30 s |
| Acknowledge an alert, optional note | API-15 | R |
| Clear a running burst | API-IRR-12 `{kind: "clear"}` | C |
| Clear a mister fault | API-IRR-13 `{line_id}` | C. The line returns to idle or running; Activity records who cleared it and why |

| State | Now shows |
|---|---|
| Loading | Hero and tiles keep their height as skeletons; the water card and alert list load on their own |
| Empty | "Nothing running"; "No open alerts" per §1.5 |
| Stale | The answer reads "Waiting for fresh data (last 14 min ago)"; values muted with STALE; no verdict |
| Not reporting | The answer reads "Ingestor not running since 03:12" or "Controller not reporting since 03:12" (§1.2); tiles STALE; Needs someone reads "In-app alerts may be missing: ingestor not running since 03:12" or "… no data since 03:12" (§1.5) |
| Error | API-02 down: "Can't reach the greenhouse data" in the hero and nothing green. A failed or not yet served API-IRR-01 reads "Water status unavailable" with Retry, never idle; the alert list fails alone |
| Before the irrigation firmware | The water card shows relay states, and Stop all water works on every line through the bridge (§1.5); L01 runs from Water |

Acceptance:
1. With the fixture "in band" payload and no open alert, the hero reads "All OK" with 64.8 °F, the band and "Updated
   20 s ago", above the fold at 360 × 740; the water card with Stop all water sits above the fold at 390 × 844.
2. With the heartbeat fresh and the newest `climate` row 6 min old, every page shows "Controller not reporting since
   …" and none says "Controller offline"; with the heartbeat 4 min old, every page shows "Ingestor not running since
   … The controller keeps running on its own." In both, Now shows no verdict and Needs someone reads "In-app alerts
   may be missing", never "No open alerts". With API-02 failing twice, the hero reads "Can't reach the greenhouse
   data" and nothing is green.
3. An operator taps Stop all water once, with or without the irrigation firmware: one API-IRR-07 request, "Sending…",
   then the fixture's relay edge turns the wet chip OFF and the Stop toast names what can still start, with Disable
   for that line (none before the irrigation firmware). A viewer sees no Stop, Acknowledge or Clear.
4. A null RH renders "—" with its reason; with the controller reporting, an idle actuator reads OFF since its last
   real change, never "unknown"; with it not reporting, every state reads "unknown" with its age.
5. Occupancy always shows: confirmed reads "Someone inside: misters wait (Frigate, 4 min ago)"; unknown reads
   "Occupancy unknown: misters may run".

## 3. SCR-02 Graphs · `/graphs`

**Purpose:** past, present and plan in one view: climate against the band, any measured signal, setting steps and
equipment lanes on one cursor, at any range with its resolution stated; the data behind it; and a daily scorecard.
**Stories:** US-GRF-01, US-GRF-02, US-GRF-03, US-GRF-04, US-GRF-07, US-GRF-09, US-PLAT-06. Next: US-GRF-05,
US-GRF-06, US-GRF-10.

```text
GREENHOUSE HISTORY · AMERICA/DENVER
Climate
Oct 31, 07:00 → Nov 1, 18:00 (36 h across the fall-back) · one point per 5 minutes · gaps stay empty, never zero
[24 h] [72 h] [7 d] [30 d]   From [10/31/2026] To [11/01/2026] [Show]                         [CSV of this window]
On charts: [VPD · mister_engage_kpa ×] [RH · fog_rh_ceiling_pct ×] [+ Setting] [+ Metric]          Scorecard ›
┌ LOW ─────────────┐┌ MEAN ────────────┐┌ HIGH ────────────┐┌ COVERAGE ────────┐  one summary row per panel
│ 61.2 °F · 05:10  ││ 65.0 °F          ││ 71.9 °F · 14:22  ││ 99.6% · 1 gap    │
┌ TEMPERATURE · °F · MEASURED · BAND IN FORCE ─────────────────────────────────────────────────────────────────┐
│ 72 ┤▒▒▒▒▒▒/‾‾‾‾\▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒▒▒▒│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒       ▒ band ribbon                 │
│ 62 ┤▒▒▒__/      \___▒▒▒▒  (gap)  ▒▒▒▒▒│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒                                      │
├ VPD · kPa ───────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 1.6 ┤━━━━ 1.60 device ━━━━┓┄┄ 1.55 REQUESTED ┄┄┄│- - 1.70 PLANNED · AI - - -                                    │
│ 0.6 ┤▒▒▒▒/‾‾\▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒│                                                             │
├ RH · % ──────────────────────────────────────────────────────────────────────────────────────────────────────┤
├ EQUIPMENT · COMMANDED ─────────────────────────────────────────────────────────────────── on time · starts ───┤
│ Fan 1            ██████       ████        │                                                 2 h 10 · 6      │
│ Heat 1 (elec.)   ███               ██████ │                                                 3 h 05 · 4      │
│ L04 Center mist  ▌▌▌ ▌▌ VPD          ▌▌▌ │                                                 46 min · 41     │
│ L01 Wall drip            ███ schedule     │                                                 12 min · 1      │
│ Unexplained flow ░░░░ unknown 02:10–02:40 │                                                                 │
│ 9 idle rows ›                             │                                                                 │
└ 10/31 12:00 GMT-06:00 ── 11/01 01:00 GMT-06:00 ── 11/01 01:00 GMT-07:00 ── Now ── 11/01 18:00 GMT-07:00 ──────┘
› Data table · 432 rows     › Legend
```

At 390 the order is the same: chips wrap, panels are 220 px, lane labels 6rem, the legend sits in a disclosure, and
there is no drag zoom.

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Controls | Range chips 24 h, 72 h, 7 d, 30 d (each is the past window plus the next 12 h); custom From and To up to 400 days; settings on charts, chosen from settings in the panel's unit; metrics from the picker; CSV. All in the URL: `?range=72h&set=mister_engage_kpa&m=climate.flow_gpm` | API-09 (setting chooser), API-03 (metric picker) | — |
| Resolution line | Window, "one point per N minutes" from `bucket_s`, "gaps stay empty, never zero" | API-04 | with the series |
| Summary per panel | Low and when, mean, high and when, coverage %, gap count | API-04 (`count`) | with the series |
| Temperature, VPD, RH (linked; one unit each) | Measured trace solid to now; the band ribbon is the band in force: the dispatcher's house band rows (DERIVED) within their 90-day retention, and before that the crop band "reconstructed from today's anchors". Setting steps: device solid, REQUESTED dashed amber from the request, PLANNED dashed right of now with AI (a forecast rule's step reads "Forecast rule · system"); a planned step that a newer value supersedes is muted and labelled SUPERSEDED, never drawn as the plan ahead. Defaults: `mister_engage_kpa` on VPD, `fog_rh_ceiling_pct` on RH; temperature shows the band only | API-04 (`climate.temp_avg`, `climate.vpd_avg`, `climate.rh_avg`), API-06, API-12 per setting | 60 s while the window includes now |
| Metric picker | "+ Metric" lists every API-03 key by group (house climate, zones, water and soil, light, outdoor, targets, controller) with unit, cadence and provenance. A choice joins the linked panel for its unit, or opens a new linked panel, never a second axis; up to 8 metrics per window, kept in the URL as `m=`. No DLI until the PAR meter (VD-71) adds PPFD and DLI to "light" | API-03, API-04 | — |
| Equipment lanes (same cursor) | Climate (fan1, fan2, vent, heat1, heat2); water L01-L11 coloured by cause (`vpd`, `schedule`, `run_now`, `feed`, `prewet`, `flush`, `fallback`, `unknown`); light (the Lutron readback `grow_light_main`, `grow_light_grow`); flags (occupancy, `leak_detected` as "Unexplained flow", `water_flowing`). Unknown spans (the controller not reporting, or no row) hatched; idle rows fold; on time and starts at the right | API-05 (windows up to 31 days) | 60 s |
| Data table, legend | "Data table · N rows" (local time with offset, every series); legend below the plot | same | — |
| Scorecard `/graphs/scorecard` | One row per America/Denver day for 30 days: time in band day and night, gallons used, watering minutes by zone, Iris required plans written and missed, alerts raised by severity. A missing value is "—" with its reason, never 0; a 23- or 25-hour day counts as it ran. "CSV of this table" downloads exactly the 30 rows shown | API-04 (`daily.*` keys over `v_daily_scorecard`), API-IRR-10 | 60 s |
| Outdoor and forecast (Next) | Outdoor temperature, RH and VPD over the indoor trace with source and age; the forecast dashed past now with its issue time | API-04 (`climate.outdoor_*`, a forecast key over `weather_forecast`) | 60 s |
| Zones (Next) `/graphs/zones` | Panels per zone against its target; MEASURED, PEER or HOUSE | API-04, API-06, API-IRR-01 | 60 s |

Graphs writes nothing. A setting in the legend links to its drawer on Settings (SCR-05).

| State | Graphs shows |
|---|---|
| Loading | Chart frames at reserved heights (320 px desktop, 220 px phone, 150 px per linked panel) |
| Empty | "No data in this window" and a "Last 24 h" link |
| Stale | The trace stops at its last sample; the eyebrow reads "STALE · last data 06:02"; nothing is drawn to now |
| Not reporting | As stale, plus the banner; band and plan still draw |
| Error | API-04 503: "This window took too long. Narrow it." in that panel; the others render |
| Gaps and DST | Lines break and lanes hatch "unknown"; on 2026-11-01 every label from 01:00 to 01:59 carries its offset |
| Long windows | Over 31 days the lanes panel reads "Equipment lanes cover at most 31 days. Narrow the range to see them" while the climate panels render, and the URL keeps the range. A span older than a metric's raw retention (climate 365 days, diagnostics 180, `setpoint_snapshot` readbacks 90; Round 2b D) draws its daily summaries and says so on the chart ("Daily summaries before 11/02/2025: raw data kept 365 days"); a metric with no daily summary reads "beyond retention (180 days kept)" there and counts as no coverage. Nothing is clamped |

Acceptance:
1. `/graphs?range=72h&set=mister_engage_kpa` renders the three linked panels with the band ribbon, the setting's
   device, requested and planned steps, and the lanes on one cursor; reloading the copied URL renders the same view.
2. `/graphs?range=7d&m=climate.soil_moisture_south_1&m=climate.flow_gpm` renders soil moisture and flow on one cursor
   with the L01 lane, gaps broken and the resolution stated.
3. A fixture window with a 30-minute hole shows a broken line, a hatched lane span and "1 gap"; no value reads 0; the
   resolution line states the bucket API-04 returned ("one point per 15 minutes" for 7 d at 1,500 points).
4. "CSV of this window" downloads exactly the plotted columns with local times and offsets; on `/graphs/scorecard`,
   "CSV of this table" downloads exactly the 30 rows shown, a missing value as "—" with its reason.
5. The tooltip shows each value, the band and the time with its offset, and wet-line intervals name their cause; a
   90-day window shows the lanes message and still renders the climate panels; a 400-day window labels the span past
   climate's 365 days "Daily summaries before …".

## 4. SCR-03 Plan · `/plan`

**Purpose:** what Iris has told and will tell the controller, why and until when, whether Iris is healthy, and a
fast way into any planned setting.
**Stories:** US-PLN-01, US-PLN-02, US-PLN-03, US-SET-04, US-AGT-05.

```text
IRIS PLAN · AI · AMERICA/DENVER
Plan
┌ PLAN IN FORCE · AI ─────────────────────────────────────────────────┐ ┌ PLANNER HEALTH · LAST 36 H ─────────────┐
│ iris-20261101-0702 · SUNRISE · written 07:02 · to 17:30 (10 h left) │ │ MIDNIGHT  00:15  plan written   OK      │
│ "Hot, dry afternoon: engage misting earlier from 13:00 and widen    │ │ SUNRISE   07:00  plan written   OK      │
│  the pulse gap at dusk."                                      AI    │ │ SOLAR_MAX 12:41  due                    │
│ › Conditions, stress windows and per-setting rationale              │ │ SUNSET    10/31  missed         ▲       │
│ After 17:30: band and defaults until the next plan                  │ │ › All triggers                          │
└─────────────────────────────────────────────────────────────────────┘ └─────────────────────────────────────────┘
NEXT 24 H · ONE LANE PER PLANNED SETTING                  [24 h] [72 h]            1 setting MANUAL · Iris skips it
                  Now│         13:00             17:30 expiry          00:15                  07:00
VPD Mister Engage    │1.60 ━━━━━━━┓- - 1.70 - - - ┊ band/default ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  PLANNED · AI   [Open]
Mister pulse gap     │YOUR VALUE 50 s · Jason 08:00 · holds until Iris's next plan · 60 s SUPERSEDED     [Open]
Mister pulse on      │60 s  MANUAL · Iris skips · Jason since 10/30 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    [Open]
```

At 390 the card, health and lanes stack; lanes use 6rem labels.

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Plan in force | Plan id; trigger (MIDNIGHT, SUNRISE, SOLAR_MAX, TRANSITION, SUNSET, WEEKLY, FORECAST_DEVIATION, MANUAL); written at; valid until and time left; hypothesis summary with AI; conditions, stress windows and per-setting rationale in a disclosure; what applies after expiry | API-07 (`plan_journal`, `v_active_plan`) | 60 s |
| Lanes, next 24 h (72 h chip) | One lane per planned setting: device value at now (readback), Iris waypoints dashed and labelled PLANNED with AI, the plan expiry with what takes over (live plans reach a few hours ahead, so most of a lane is band or default). A manual setting reads "MANUAL · Iris skips · Jason since 10/30" with no waypoints; the head counts manual settings. When a setting's newest desired value is newer than the plan in force and not manual, the lane reads "YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan" and the waypoints the API marks not effective draw muted as SUPERSEDED; after Iris's next plan the lane returns to PLANNED. A forecast rule's value reads "Forecast rule · system", never AI | API-07 | 60 s |
| Planner health | The last 36 h of `planner_trigger_ledger`: trigger, due, status in words (due, delivered, acknowledged, plan written, setting changed, fallback, wrong action, delivery failed, timed out, missed) and failure class. A missed, failed or late required trigger (MIDNIGHT, SUNRISE, SUNSET) is marked here and turns Now's Iris tile amber | API-07 | 60 s |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Open a lane | `/settings?edit=<param>`: the setting drawer on Settings (SCR-05), Manual ticked | After a save with Manual the lane reads MANUAL and drops its waypoints; after Release to auto Iris's waypoints return on the next refresh |

| State | Plan shows |
|---|---|
| Loading | Card, health and lanes as skeletons |
| Empty | "No plan in force: band and defaults apply since 22:58"; lanes show device values only |
| Stale | Device values at now marked STALE; waypoints still draw |
| Not reporting | As stale, plus the banner; the plan still shows (it lives in the database) |
| Error | The failing panel shows Retry; the others render |

Acceptance:
1. The card shows trigger, written at, valid until and the AI-labelled hypothesis; with no plan in force it names
   what applies and since when.
2. A manual setting's lane reads "MANUAL · Iris skips", draws no waypoints, and Open goes to `/settings?edit=<param>`.
3. With a fixture plan written 07:02 (13:00 waypoint 1.70) and a non-manual desired row at 08:00 (1.55), the lane
   reads "holds until Iris's next plan" and the 13:00 waypoint is muted and labelled SUPERSEDED.
4. A missed SUNSET trigger in the fixture shows "missed" with its failure class here and turns Now's Iris tile amber.

### 4.1 As built (VD-11)

The screen is `dashboard/app/routes/plan.tsx` with `components/plan-card.tsx` and `components/planner-health.tsx`
(#1131). Where it settles a detail this section leaves open, or differs from it:

| Topic | As built |
|---|---|
| Reads | One API-07 read under its shared key, every 60 s and paused while the tab is hidden; the window is in the URL (`?hours=72`, anything else 24 h). Device staleness is the shell's API-02 `freshness.readback.stale`. The time left, a trigger's lateness and the lanes' window count from when API-07 answered |
| Card | "iris-20261102-0702 · SUNRISE · written 07:02 · to 17:30 (6 h left)"; a time on another local day carries its date ("to Nov 5, 12:00 (71 h left)"), and under an hour the time left reads in minutes. The hypothesis is quoted with AI; with none it reads "— no hypothesis recorded" and carries no AI. Details lists Conditions (`conditions_summary`) and Expected outcome ("—" with "not recorded" when null), the stress windows of `detail.hypothesis_structured` (kind and severity in words, local times, mitigation), the per-setting rationale (`hypothesis_structured.rationale`: label, key, old → new, expected effect, evidence; also a climate intent that names its setting) and, under Waypoint reasons, each other `climate_intents` reason at its time. The intents' knob values are not listed |
| Names | An actor reads as API-07 sends it, the Authentik username: "YOUR VALUE 1.55 · jason 08:00 · holds until Iris's next plan". "Jason" in this doc is illustrative |
| Health | Newest first by `expected_at`. The ledger seeds a whole day's rows at midnight, so the expected rows ahead would fill "the five newest": the list shows the next row ahead (it reads "due"), the five newest that have fired and every marked required trigger, and "All triggers · N more" opens the rest, the later rows ahead included. The time shown is `due_at`, with its date on another day. A row still due or delivered after `due_at` adds "· late"; only a required one gets ▲ and the amber rule. `failure_class` reads raw: the ledger's classes are open-ended and the words table has no family for them yet |
| Lanes | `PlanLanes` gains `stale` (each device value muted with "STALE · read HH:MM" in its title, "STALE · device values last read HH:MM" over the rows, the device segment muted and labelled STALE; waypoints draw as before) and `rowEnd`, a 3.5rem column beside the plot where Plan puts Open, a link to `/settings?edit=<param>` named "Open <label>". The plan lanes' labels are 6rem at every width. The head reads "1 setting MANUAL · Iris skips it" ("N settings … them") beside the 24 h and 72 h chips, and nothing when none is manual |
| States | One read feeds all three panels, so API-07 failing shows "Couldn't load …" with Retry in each; a poll that fails after an answer replaces it, so no earlier plan stays on screen. No panel shows an age line, so nothing green shows |
| Fixtures | `no-plan` (no plan since 09:30, device values only), `manual-lane` (`mister_pulse_on_s`, manual by jason since 10/30) and `forecast-rule` (`mister_engage_delay_s` 90 s from 14:00, `preemptive`) join `superseded-lane` and `missed-sunset`. The browser flows at 1440 and 390 px (`e2e/plan.spec.ts`) come with #1128's harness |

## 5. SCR-04 Water · `/water`

**Purpose:** see and run all watering: what is watering and why, what runs next, what is stopping water, minutes per
zone, and every schedule, line and mister association.
**Stories:** US-WAT-01 to US-WAT-19, US-WAT-21 to US-WAT-23, US-NOW-04, US-AGT-01, US-AGT-02 (agent writes show
here), US-AGT-05. Next: US-WAT-20.

Three views share one page head, and **Stop all water** sits in it on every view for operators.

```text
WATER · AMERICA/DENVER                                                                         [■ Stop all water]
Water                                                    [Board] [Schedules] [History]
On controller: rev 42 · CONFIRMED 2 min ago                                                      [Pause lines…]
LIMITS TODAY  SAF-4 21 of 360 gal · soft budget 21 of 300 gal · Center 14 of 120 min (60 kept for VPD) · South 0 of 45
              West 0 of 45 · Wall drip 0 of 60 min · Unexplained flow: none · Nobody seen: misters may run (05:10)
MISTER ZONES
┌ CENTER · L04 ─────────────────────┐ ┌ SOUTH · L07 ─────────────────────┐ ┌ WEST · L09 ──────────────────────┐
│ VPD control  [ON]   CONFIRMED     │ │ VPD control  [ON]   CONFIRMED    │ │ VPD control  [OFF]  REQUESTED    │
│ Schedule     [ON]   CONFIRMED     │ │ Schedule     [OFF]               │ │ Schedule     [OFF]               │
│ VPD from the house average        │ │ VPD from peer probes (#751)      │ │ Will not mist automatically      │
│ Misting now (VPD)                 │ │ Faulted: no misting until        │ │ Last misted 9 days ago           │
│                                   │ │ midnight (watchdog) [Clear fault]│ │                                  │
└───────────────────────────────────┘ └──────────────────────────────────┘ └──────────────────────────────────┘
LINES
┌ L01 Wall drip · CLEAN · ON ───────┐ ┌ L02 Wall feed · FERT · OFF ──────┐ ┌ L04 Center mister · CLEAN · ON ──┐
│ IDLE · next Mon 07:34 · 12 min    │ │ Not checked yet                  │ │ RUNNING · VPD · 0:40 left        │
│ Last water Fri 07:31, 12 min · ok │ │ [Enable…]                        │ │ Today 14 of 120 min              │
│ [Run…]  [Stop]  [Options]         │ │                                  │ │ [Mist now…] [Stop] [Options]     │
└───────────────────────────────────┘ └──────────────────────────────────┘ └──────────────────────────────────┘
… L07 to L10 the same · L05, L06 Reserved · DISABLED until someone enables them · L11 Fog: climate only
```

```text
[Board] [Schedules] [History]        14 of 24 schedules · [Next 7 days] [Last 7 days]            [+ New schedule]
WEEK · PLANNED   Sun 11/1      Mon 11/2      Tue 11/3      Wed 11/4      Thu 11/5      Fri 11/6      Sat 11/7
L01 Wall drip    ·             07:34 12m M   ·             07:34 12m M   ·             07:34 12m M   ·
L02 Wall feed    ░09:00 FERT M ·             ·             ·             ·             ·             ·
L04 Center mist  06:49 8×60s   06:49 8×60s   06:49 8×60s   06:49 8×60s   06:49 8×60s   06:49 8×60s   06:49 8×60s
                 09:44 FB      09:44 FB      09:44 FB      09:44 FB      09:44 FB      09:44 FB      09:44 FB
L07 South mist   ·             ░14:00 4×45s  ·             ░14:00 4×45s  ·             ░14:00 4×45s  ·
L05, L06 (reserved), L08-L10  no schedules
M MANUAL (Iris skips it) · ░ will not run, with why (L02 is disabled; L07's Schedule is off) · FB fallback: only
if VPD sensing is degraded · FERT 09:00: prewet 2 min on L01, feed 5 min through L03, flush 4 min
Select a block: Edit, or Pause the line until after it
┌ New schedule ───────────────────────────────────── [×] ┐   1440: 28rem drawer · 390: full-screen sheet
│ Line [L01 Wall drip ▾]       Label [Wall MWF       ]   │
│ Days (Mo)(Tu)(We)(Th)(Fr)(Sa)(Su)                      │
│ Start [Sunrise ▾] + [60] min   → 07:34 today           │
│ Run [12] min                                           │
│ [x] Manual: Iris skips this  Reason [              ]   │
│ [ ] Checked: line flushed, emitters ok (L01 has none)  │
│ Previewed in the week: Mon 12 · Wed 12 · Fri 12 min    │
│ Wall drip cap 60 min a day         [Cancel] [Save]     │
└────────────────────────────────────────────────────────┘
```

```text
[Board] [Schedules] [History]   MINUTES BY ZONE · DAILY        [7 d] [30 d]     Group by [zone] [line] [cause]
 Wall    ▃ ▃ ▅ ░ ▃ ▆ ▂┆  today 0 min (partial) · cap 60      ░ no data (ingestor outage, Thu)
 Center  ▆ ▇ ▅ ░ ▇ ▆ ▃┆  today 14 min · cap 120              ▇ VPD ▇ schedule ▇ run now ┆ still planned today
 South · West · Fog (humidification minutes, no cap) the same
RUNS · LAST 7 DAYS                      Line [All ▾]  Cause [All ▾]  Outcome [All ▾]
 Fri 07:31–07:43  L01 wall CLEAN  schedule "Wall MWF"    controller   12.0 min   9 gal                  ok
 Sat 06:58–07:41  L04 center      VPD                    controller   14 pulses  3 gal                  ok
 10/25 09:00      L02 wall FERT   schedule "Weekly feed"              0 min                 skipped · line paused
```

```text
390 ┌──────────────────────────────────────┐
    │ Water                  [■ Stop all]  │
    │ [Board] [Schedules] [History]        │   sticky under the header
    │ rev 42 · CONFIRMED · SAF-4 21/360    │   limits and mister zones fold
    │ ┌ L04 Center mister · RUNNING ─────┐ │   into links below the cards;
    │ │ VPD · 0:40 left   [Stop] [Opts]  │ │   running lines sort first;
    │ └──────────────────────────────────┘ │   Schedules shows one day for
    │ Now  Water  Graphs  Activity 1  More │   every line, with a day switcher
    └──────────────────────────────────────┘
```

| Panel | View | Shows | Data | Refresh |
|---|---|---|---|---|
| Program banner | all | "On controller: rev 42 · CONFIRMED 2 min ago"; REQUESTED while a push is pending; "Not confirmed: <reject in words>"; with `src=rom`, "Running the built-in program: L04 VPD only, L01 run-now, no schedules"; before the irrigation firmware, "No irrigation engine on the controller yet: relay states only; L01 runs, and every line stops, through the bridge" | API-IRR-01 | 15 s; 5 s while REQUESTED |
| Limits strip | Board | SAF-4 used of 360 gal; soft budget; center, south and west minutes against their duty caps with the VPD reserve marked; drip caps; the flow state in words; occupancy, in the Now chip's words; "VPD fallback active" while the `degraded_only` slots run | API-IRR-01 | 15 s |
| Mister zones | Board | Center (L04), south (L07), west (L09): "VPD control" and "Schedule" switches, one badge each, REQUESTED until the controller reports the program that carries the change, then CONFIRMED; the VPD source (own probe, peer probes, house average); when it last misted; a watchdog back-off ("Backing off until 13:20") or latch ("Faulted: no misting until midnight (watchdog)") with Clear fault; "Will not mist automatically" when both switches are off | API-IRR-01 | 15 s |
| Line cards | Board | L01, L02 (fed from the fertilizer tank through L03's injector line), L04-L10: CLEAN or FERT, enabled, state in words (idle; running with cause, schedule and time left; paused, disabled, backing off or faulted with the reason), when it last watered, today's minutes, next run, last outcome. L05 and L06 (center drips) read "Reserved" with DISABLED until a person enables them; they can still hold schedules (Round 2b E). L11 Fog: "Climate only", no controls | API-IRR-01 | 15 s; 5 s while a command is in flight |
| Week | Schedules | Rows L01, L02, L04-L10; columns today and the next 6 days. Each occurrence is a block with its time and minutes or pulses, MANUAL or "Iris may adjust"; one that will not run is hatched with why (line paused or disabled, Schedule off); the three fallback slots read "only if VPD sensing is degraded"; the schedule count (of 24). "Last 7 days" shows past blocks with their outcome word (ok, short, stopped, preempted, skipped, missed, cap). The editor's draft previews as PLANNED outlines | API-IRR-02 `days=7` (`past_days=7` for Last 7 days) | 60 s |
| Minutes by zone | History | Daily bars for wall, center, south and west (fog apart, as humidification), stacked by cause, clean below fertilizer, cap ticks; today partial; days with no data hatched "no data"; 7 d or 30 d; grouped by zone, line or cause | API-IRR-10 | 60 s |
| Runs | History | Line, zone, CLEAN or FERT, cause, schedule, actor, start, end, minutes (pulses for mist), whole gallons from the site meter (±1 gal; it also counts the hose, sink and hydro; a run that moved no water reads "0 gal"; Q-10), outcome and reason; filters for line, cause, outcome and dates (up to 31 days); each run opens its Graphs window | API-IRR-11 | 60 s |
| Daily water use (Next) | History | Meter gallons per day split by line and cause, with water that ran while no valve was open shown apart; 30 and 90 days | API-21 | 60 s |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Stop all water; Stop on a line | API-IRR-07 `{}` or `{line_id}` | C, then the Stop result (§1.4). One tap, no dialog, never disabled by the writes flag; OFF from relay edges within 30 s; lines and schedules stay as they are |
| Run… (drip `{minutes}`; FERT drip, L02 and L06, `{minutes, prewet_s, flush_s}`; FERT mister, L08 and L10, `{on_s, count, gap_s, prewet_s, flush_s}` within the mister bounds; a FERT dialog's title carries FERT, and the run prewets on the clean partner, feeds through L03, then flushes; Mist now `{on_s, count, gap_s}`, shown only with Schedule on) | API-IRR-06 | The one confirm dialog ([design system](design-system.md) §4.4), then C. It starts at once or is refused in words, and nothing queues: `line_busy`, `manifold_busy` ("L01 holds the drip manifold until 07:42; stop it or try then"), `cap_reached`, `line_disabled`, `line_paused`, `line_faulted`, `not_schedule_member` |
| Enable or disable a line | API-IRR-08 `{enabled, checked, check_note}` | P. A line with no recorded check asks once for the "Checked" box (who and when are recorded; an optional note of up to 500 characters); disabling stops everything on it, VPD misting included |
| VPD control and Schedule switches (L04, L07, L09) | API-IRR-08 `{vpd_member, schedule_member}` | P per switch. Schedule on for a line with no check asks for the "Checked" box (nozzles, coverage, walkway) |
| Line options: daily cap, VPD reserve, Manual | API-IRR-08 | P |
| Pause lines until a time; Resume | API-IRR-08 `{paused_until}` on one line or `all`; Resume sets it to now | P. Cards read "Paused until 14:00 · VPD misting continues" (turn VPD control off, or disable the line, to stop it). Pausing a line until after a planned block skips that one run |
| New, edit, enable or delete a schedule | API-IRR-03, API-IRR-04, API-IRR-05 | P |
| SAF-4 ceiling, scheduled-mist RH ceiling, climate yield | API-10 on `mister_daily_volume_max_gal`, `sched_mist_rh_max_pct`, `climate_yield_min`, in the Settings drawer | S (state from API-09, confirmed by the program hash) |
| Clear a mister fault latch | API-IRR-13 `{line_id}` or `{}` | C. The card returns to idle or running; Activity records who cleared it and why |

Editor fields follow [irrigation](irrigation.md) §4.3: line (L01, L02, L04-L10); weekdays (at least one); a clock
start or sunrise, solar noon or sunset ± 0-240 min (resolved for today beside it); minutes (drips, fertilizer
drips included); `on_s` 10-90 s, `count` 1-60 and `gap_s` 30-600 s (misters, fertilizer misters included); prewet
and flush on the clean partner
(fertilizer); the Manual box, ticked. The draft previews in the week view with API-IRR-02's solar times and each
line's daily total against its cap; a run past the cap is not refused, and the controller ends it at the cap
(`cap`). Refusals sit beside the field in words: `out_of_range`, `too_close` (30 min), `too_many_schedules` (24),
`unknown_target`. A schedule on a disabled line, or a mist schedule with Schedule off, saves, and its blocks read
"Skipped: line disabled" or "Skipped until Schedule is on". If a drip line is enabled with no recorded check (L01 in
the seed), the editor shows the "Checked" box once and records it with the save (API-IRR-08).

| State | Water shows |
|---|---|
| Loading | Program banner, limits and cards as skeletons, each loading on its own |
| Empty | "No schedules yet. Lines water only from VPD control and Run now." with New schedule; "No runs in this window" |
| Stale | Line states follow §1.5: known while the controller reports, "unknown · 3 min ago" only while it does not; relay edges still show |
| Not reporting | "Controller not reporting since 03:12. If it has power, it keeps running rev 42, VPD misting and caps"; saves store and push when data returns |
| Error | Retry per panel; a failed status read says "Water status unavailable", never idle |
| Before the irrigation firmware | §1.5. L01 has Run now through the bridge: it runs for the Wall drip duration setting (`irrig_wall_duration_min`) and the dialog says so. Every line has Stop, and Stop all water works, through the same bridge. The other lines' Run and Clear fault read "arrives with the irrigation firmware" |
| Flow, occupied | Flow: the fault banner and "Water flowing with every Verdify valve closed since 03:02 · 3 gal in 10 min". It is an alert only (Q-05): the irrigation firmware stops no drip, mister or fog for it; until then the current firmware holds them off while the flag is up. Occupied: mist cards "Waiting: someone inside (Frigate, seen 3 min ago)"; unknown occupancy never waits |
| Not confirmed | The banner and the item show the reject in words and "retrying at 1, 5 and 15 min" |

Acceptance:
1. Board shows every line L01-L11 with enabled, state and reason in words, when it last watered, today's minutes,
   and next and last run; L05 and L06 read "Reserved" with DISABLED; L11 reads "Climate only" with no controls.
2. Creating an L01 schedule (Mon, Wed, Fri, sunrise + 60, 12 min) previews in the week view against the 60 min cap,
   then reads REQUESTED, then CONFIRMED when the fixture's `irr_program_id` reports the new hash; Activity lists it
   under the operator's name.
3. With the fixture's three schedules the week view shows each occurrence in its line × day cell and a past L02 block
   reads "skipped · line paused"; the 390 px view shows one day for every line with no horizontal scroll.
4. Stop all water appears on all three views for operators, sends one request with no dialog and stays enabled with
   changes switched off; before the irrigation firmware every line shows Stop and only L01 shows Run; viewers see no
   Stop, Run, switch or editor.
5. A run refused with `manifold_busy` shows the reason in words and changes nothing; a double tap on the dialog's Run
   sends one `Idempotency-Key`. A fixture mister latch reads "Faulted: no misting until midnight (watchdog)", and
   Clear fault sends one API-IRR-13 request.
6. Minutes by zone draws a day with no data as a hatched "no data" gap, never 0, and marks today partial; south reads
   "VPD from peer probes" while its probe is out.

## 6. SCR-05 Settings · `/settings`

**Purpose:** "control all settings": every registry row in one list with its device value, and one change path for
every writable one.
**Stories:** US-SET-01, US-SET-02, US-SET-03, US-SET-04, US-SET-05, US-WAT-19 (site caps), US-AGT-05, US-AGT-07.
Next: US-SET-06, US-SET-07.

```text
SETTINGS · DEVICE VALUES FROM READBACKS · AMERICA/DENVER
Settings                                                          [Search settings…                         ]
[All] [Manual 3] [Requested 1] [Not confirmed 0] [Changed by an agent 14] [Read-only]
PLANNER POLICY
 Setting                    Device                        Next (plan)                         Allowed · step
 VPD Mister Engage          1.60 kPa  CONFIRMED · 2 min   MANUAL · Iris skips                 0.50–2.50 · 0.05  [Edit]
   mister_engage_kpa        → 1.55 REQUESTED · 20 s · MANUAL · Jason · "Vandas drying"
 Mister pulse gap           50 s  CONFIRMED · 1 min       superseded until Iris's next plan   10–60 · 5         [Edit]
   mister_pulse_gap_s       Jason 08:00 · Iris may adjust
 Mister pulse on            60 s  CONFIRMED · 30 s  AI    75 s at 13:00 · AI                  30–90 · 5         [Edit]
   mister_pulse_on_s        Iris changed this at 07:02
CONTROLLER SAFETY
 Fog RH ceiling             88%  CONFIRMED · 1 min        not planned                         75–98             [Edit]
   fog_rh_ceiling_pct       → 92 REQUESTED · 3 min · Sent 06:12; waiting: controller heap low
SCHEDULED POLICY ›   CROP BAND ›   SITE WATER CAPS ›   BAND IN FORCE ›   READ-ONLY ›
 Temp low                   40.0 °F · legacy limit, read-only: not the band while the on-chip band runs
```

At 390 rows become stacked cards with the device value large and Edit full width; the drawer
([design system](design-system.md) §4.3) becomes a full-screen sheet.

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Search and filters | Search on name, key and plain label; chips All, Manual, Requested, Not confirmed, Changed by an agent, Read-only; all in the URL | API-09 | — |
| Catalog | Groups by `control_class`: planner policy, scheduled policy, controller safety and crop band (anchors and widths edit through the same drawer; the curve preview is Next), plus "Site water caps" for the three irrigation site values, and the read-only "Band in force" (API-09 `group` `band_in_force`, VD-55): the house and per-zone band the controller runs, DERIVED with its age; then read-only rows with their reason: the legacy wide limits (`temp_low`, `temp_high`, `vpd_low`, `vpd_high`, `vpd_target_*`: "legacy limit, not the band while the on-chip band runs"), values the dispatcher derives (the activity mirror), the old commissioning rows ("retiring with the irrigation engine"), readback context and retired. Fixed order, no column sort | API-09 | 60 s; 5 s while a row is REQUESTED |
| Row | Label and key; device value with age and badge (CONFIRMED, NOT CONFIRMED, HELD, or NO READBACK only for a setting with no readback at all; switches confirm from their equipment mirror); the pending request under it, with its reason once late: "Clamped to 2.00 kPa: <guardrail>", "Sent 06:02; waiting: controller heap low", "Sent 06:02; push failed: <status>", "Held by experiment <id>: applies when it ends", "Ingestor not running since 03:12", "Controller not reporting since 03:12", else "Sent 06:02; no matching readback yet" (with no send time known: "Waiting: controller heap low", "Push failed: <status>", "No matching readback yet"); MANUAL; AI with the agent name ("Iris changed this at 07:02"), or "Forecast rule · system"; the next PLANNED value, or "superseded until Iris's next plan" while a newer non-manual value holds; bounds and step; read-only reason. Iris's values show as stored, unsnapped. A site cap's device value is the one in the program on the controller ("On controller · rev 42: 360 gal"); before the irrigation firmware a site cap still saves and reads REQUESTED with "applies with the irrigation firmware" (§1.4 S) | API-09 | as above |
| Drawer `?edit=<param>` | Class and key, device value and badge, NumberField, Switch or Select, Manual (ticked), Reason, Save, Release to auto; "Graph this setting ›" (`/graphs?set=<param>`) only when its unit has a Graphs panel. Escape and × close without asking | API-09 | on open |
| History (Next) | Requests stepped by source, the readback line, Iris's planned values ahead and the bounds over a chosen window; Revert | API-12 | on open |
| Crop band preview (Next) | A curve preview from `fn_crop_band_value` while an anchor or width is edited, and a one-step band revert | API-19; Save and revert stay API-10 | on change |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Save a value (Manual ticked by default; reason optional) | API-10 `{value, manual, reason}` with an `Idempotency-Key` | S. Save always sends manual |
| Turn Manual on or off without a new value | API-10 with the current value, stored as is (an Iris value stays unsnapped) | S |
| Release to auto | API-11 `?reason=` | S. MANUAL drops; the row reads "Released: plan, band or default applies", then shows the readback |
| Revert (Next) | API-10 with the previous value, or API-11 | S |

| State | Settings shows |
|---|---|
| Loading | Skeleton rows per group |
| Empty | "No setting matches 'pulse gap x'" |
| Stale | The device value muted with STALE and its age; a REQUESTED row stays REQUESTED |
| Not reporting | Save still works: "Saved; it applies when the controller reports again" |
| Error | The catalog shows Retry; a failed Save keeps the drawer open with the message |
| No readback | "Sent. This setting has no readback, so it can never show CONFIRMED" |

Acceptance:
1. The rows per `control_class` match the fixture built from `verdify_schemas/tunable_registry.py`; read-only rows
   show their reason and no Edit; `temp_low` reads "legacy limit, read-only"; "Band in force" lists the house and each
   zone with no Edit.
2. `/settings?edit=mister_engage_kpa` opens the drawer with Manual ticked; entering 1.53 with a reason shows "Saved as
   1.55 kPa (snapped from 1.53)", REQUESTED, then CONFIRMED on the fixture readback; with no readback by 5 min
   (fixture clock) it shows NOT CONFIRMED with its reason, and there is no Send again.
3. 2.60 is refused beside the field with "Must be 0.50–2.50 kPa" and the drawer stays open.
4. Release to auto removes MANUAL and shows the plan value as the next value; a non-manual value newer than the plan
   reads "superseded until Iris's next plan" in Next (plan).
5. With the fixture's program at rev 42, `mister_daily_volume_max_gal` reads "On controller · rev 42: 360 gal";
   before the irrigation firmware, saving it reads REQUESTED with "applies with the irrigation firmware".

## 7. SCR-06 Activity and alerts · `/activity`

**Purpose:** what needs someone, and who changed what: every write by a person, Iris, another agent or the system in
one log.
**Stories:** US-ACT-01, US-ACT-02, US-ACT-03, US-ACT-04, US-AGT-03, US-WAT-05, US-WAT-18, US-WAT-22.

```text
ACTIVITY · PEOPLE, IRIS, AGENTS AND SYSTEM · AMERICA/DENVER
Activity and alerts
OPEN ALERTS · 2                                                                  Show resolved in the last 24 h ›
┃ CRITICAL  Water flowing with every Verdify valve closed · since 03:02 (4 min) · 3 gal in 10 min    [Acknowledge]
┃ ▾ A leak, or the hose, sink or hydro in use. If no one is using water, close the spigot.
┃   Runbook: 1. Check the hose, sink and hydro. 2. Walk the wall line …   Open in Graphs (±2 h) ›    [Resolve…]
┃ WARNING   Fan 2 relay stuck · fan2 · since 06:02 (40 min) · seen 3 times · on with no command      [Acknowledge]
ACKNOWLEDGED, STILL OPEN · 1 ›
ACTIVITY                                     Source [All ▾]  Actor [All ▾]  Action [All ▾]  Target [            ]
07:41  Jason · dashboard          VPD Mister Engage 1.60 → 1.55 kPa · manual · "Vandas drying"   CONFIRMED 07:42
07:02  iris · AI                  Plan iris-20261101-0702 (SUNRISE): 6 settings ›
06:47  steward · agent · AI       L01 "Wall MWF" 12 → 15 min · manual cleared · "forecast 92 °F"  CONFIRMED 06:49
06:45  Jason · dashboard          Acknowledged "Fan 2 relay stuck" · "breaker reset"
06:30  forecast-engine · system   Mister pulse gap 45 → 60 s · rule "hot afternoon"
                                                                                                    [Load older]
```

At 390 alerts come first, then the log; alert rows stack with full-width buttons.

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Open alerts | `v_open_alerts` rows nobody has acknowledged, by severity (critical, warning, info) then age: the plain title from the one words table (`leak_detected` reads "Water flowing with every Verdify valve closed", with "a leak, or the hose, sink or hydro in use" beneath), zone or line, first and last seen, how many times a repeat of the same type and zone was seen (repeats merge into one row, and a long-offline probe's alert resolves itself, VD-72), current value. Acknowledged ones collapse into "Acknowledged, still open (N)" with who acknowledged; "Show resolved in the last 24 h". Expanding a row (`/activity?alert=<id>`) shows every field, the runbook from `slack_alert_runbooks` when one exists, and "Open in Graphs": the ±2 h window with the climate panels and the equipment lanes, wet relays and the flow lanes included | API-14 | 15 s |
| Activity log | Time; actor and source ("Jason · dashboard", "iris" with AI, "steward · agent" with AI, "forecast-engine · system"); action in words; target; before → after, with "manual cleared" when a write took an item off manual; reason; readback outcome. An Iris plan is one entry with its changes in a disclosure; a change made while an experiment is armed carries the experiment's id. Filters for source, actor, action and target in the URL; Load older (cursor) | API-17 | 60 s |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Acknowledge, optional note | API-15 | R, plus an `alert.ack` log entry |
| Resolve, optional note (up to 500 characters) | API-16 | R. Never blocked; an alert whose condition persists re-opens on the next monitor cycle |
| Stop all water, on `irrigation_fence_breach` | API-IRR-07 | C, then the Stop result (§1.4). A flow alert offers no Stop: no Verdify valve is open |
| Clear fault, on `irrigation_line_faulted` | API-IRR-13 `{line_id}` | C |

| State | Activity shows |
|---|---|
| Loading | Skeleton rows |
| Empty | "No open alerts" per §1.5; "No activity matches these filters" with Clear filters |
| Stale | Alert values carry their age ("3 gal in 10 min · 40 s ago") |
| Not reporting | Alerts and the log still load (they live in the database); the list reads "In-app alerts may be missing: ingestor not running since 03:12" or "… no data since 03:12" (§1.5); the banner shows |
| Error | "Alerts unavailable. Retry", never "No open alerts"; the log fails on its own |

Slack keeps posting alerts exactly as it does today.

Acceptance:
1. With API-14 failing, the alert panel reads "Alerts unavailable" and the log still loads.
2. Acknowledge with a note shows "Acknowledged by Jason · 06:47", moves the alert under "Acknowledged, still open",
   lowers the nav count by one and adds an `alert.ack` entry.
3. A dashboard write, an Iris plan, an agent write and a forecast-rule write show "Jason · dashboard", "iris" with AI,
   "steward · agent" with AI and "forecast-engine · system", each with before → after and its reason.
4. The fixture flow alert reads "Water flowing with every Verdify valve closed", expands at `/activity?alert=<id>` to
   its runbook and "Open in Graphs", and offers no Stop.
5. `/activity?target=L01` lists only L01 entries.

## 8. SCR-07 Equipment and controller · `/equipment`

**Purpose:** what every actuator is doing, the bursts that replace the Home Assistant buttons, restart, and (Next)
why each actuator is on or off and the grow lights by hand.
**Stories:** US-EQP-01, US-EQP-06, US-PLAT-13. Next: US-EQP-02, US-EQP-05.

```text
EQUIPMENT · COMMANDED STATE · AMERICA/DENVER
Equipment and controller
BURSTS  [Fans 15 min] [Humidify 5 min] [Vent bypass 10 min] [Clear]          Running: none        › More actions
┌ CLIMATE ─────────────────────────────────┐ ┌ WATER ──────────────────────────────────┐ ┌ LIGHT ────────────────────┐
│ Fan 1            OFF   10/27 04:26 0 · 0 │ │ Center mister L04  ON  06:58  14 m · 14 │ │ Main  OFF · Lutron OFF    │
│ Fan 2            ON    08:31  2 h 10 · 6 │ │ Wall drip L01      OFF Fri 07:43  0 · 0 │ │ Grow  ON (controller)     │
│ Vent             OPEN  08:29             │ │ Fog L11            OFF 05:10   6 m · 9  │ │       Lutron OFF ▲ differ │
│ Heat 1 electric  OFF   07:10  3 h 05 · 4 │ │ Fert master L03    OFF          0 m · 0 │ │                           │
│ Heat 2 gas       OFF   06:40    0 m · 0  │ │ … L02, L05 to L10                       │ │                           │
└──────────────────────────────────────────┘ └─────────────────────────────────────────┘ └───────────────────────────┘
LAST 24 H  [24 h] [72 h]   equipment lanes, as on Graphs
```

At 390 the groups stack and burst buttons go full width.

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Board | A fixed list: climate (fan1, fan2, vent, heat1 electric, heat2 gas), water (the 11 wet relays L01-L11, fog and the fertilizer master included) and light (`grow_light_main`, `grow_light_grow`). Each reads ON, OFF or unknown with since (the start of the current run of equal states; repeated equal rows from reconnect snapshots or polls are collapsed), runtime and cycles today from the collapsed edges, and report age. A state is the newest row while the controller reports, and "unknown" with its age only while it does not, or with no row. Grow lights show both the Lutron readback (`grow_light_main`, `grow_light_grow`) and the controller's intent (`grow_light_main_intent`, `grow_light_grow_intent`, VD-48), marked when they differ | API-13 (`v_equipment_now`, `equipment_state`) | 15 s |
| Bursts | A running burst from the controller's readback (`fan_burst_active`, `vent_bypass_active`, `fog_burst_active`) with the time left (a push-button burst shows without one). A burst the controller holds off says why ("Humidify held off: someone inside", "RH above the ceiling"); during emergency cooling, emergency heating or a sensor fault it says the burst is ignored | API-02 (`overrides`) | 15 s |
| Last 24 h | Equipment lanes with 24 h and 72 h chips | API-05 | 60 s |
| Why (Next) | `mode_reason`, `fog_block_reason` and `moisture_block_reason` in words, each linked to the Graphs window where it began | API-13 | 15 s |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Fans, Humidify or Vent bypass for N minutes | API-IRR-12 `{kind, minutes}` | C. No dialog; the burst shows here and on Now once its readback turns on. A burst never changes occupancy |
| Clear (ends bursts) | API-IRR-12 `{kind: "clear"}` | C; the readback confirms it off |
| Restart controller (under "More actions") | API-IRR-12 `{kind: "restart"}` | C, no dialog. The controller's uptime (API-02 `controller.uptime_s`) resets within 3 minutes; after the reconnect the desired settings are sent again and `irr_program_id` returns `src=nvs` with the same hash; Activity records who restarted it and why |
| Lights on, off or automatic (Next) | On and off: API-20, through `verdify-setpoint-server`. Automatic: API-10 on `sw_gl_main_auto_mode` or `sw_gl_grow_auto_mode` and, once it reads CONFIRMED, one API-20 command to match the intent when the intent and the Lutron state are both known and differ | S for automatic; the Lutron readback confirms each API-20 command |

| State | Equipment shows |
|---|---|
| Loading | Groups as skeletons |
| Empty | "Running: none" when no burst runs |
| Stale, not reporting | §1.5: states read "unknown · last report 14 min ago" only while the controller is not reporting, never OFF |
| Error | The board or the lanes fail on their own with Retry |
| Before the irrigation firmware | Bursts and restart are not rendered: "Bursts arrive with the irrigation firmware (`manual_override`)" |

Acceptance:
1. Every actuator in the fixed list shows ON, OFF or unknown with since, runtime today and cycles; with the
   controller reporting, `fan1` reads OFF since 10/27 04:26, never unknown; with it not reporting, every state reads
   unknown with its age.
2. "Fans 15 min" sends one API-IRR-12 request `{kind: "fans", minutes: 15}`, reads "Sending…", then "Fans burst · 15
   min left" here and on Now once `fan_burst_active` turns on; a Humidify burst while someone is inside reads
   "Humidify held off: someone inside"; Clear ends a burst and its readback reads off.
3. Grow lights always show the controller's intent and the Lutron state; a fixture with them different reads "differ".
4. Restart sends one API-IRR-12 `{kind: "restart"}`; the fixture's uptime resets and `irr_program_id` returns
   `src=nvs` with the same hash after the reconnect.
5. Viewers see the board and the lanes with no burst, clear or restart control.

## 9. SCR-08 Plants (Later) · `/plants`

**Purpose:** what grows where, observations with photos, and the latest camera frames. Not in the first build and not
in the nav; no issue until it is scheduled.
**Stories:** US-PLT-01, US-PLT-02, US-PLT-03 (all Later).

```text
PLANTS · LATER
┌ GREENHOUSE MAP ────────────────────────────────────┐ ┌ CENTER · HANGING VANDAS ─────────────────┐
│ north · east · center (L04 to L06)                 │ │ Health trend · zone charts ›             │
│ wall (L01, L02) · south (L07) · west (L09)         │ │ Watered by L04 · last 06:58              │
└────────────────────────────────────────────────────┘ └──────────────────────────────────────────┘
```

| When built | Data |
|---|---|
| A map of zones, positions, crops, probes, cameras and the lines that water each zone | The topology tables `zones` and `water_systems` |
| An observation log with up to 4 photos, attributed and shown in Activity | A new observations table and file store |
| The latest frame per greenhouse camera, "captured 08:40 (2 min ago)", STALE past its cadence | A signed-in same-origin path, never the public camera route |

## 10. SCR-09 Sign-in and errors

**Purpose:** get a person in with their Authentik account and second factor in one step, and say plainly what went
wrong. These pages use the header without nav or tools ([design system](design-system.md) §3) and look the same at
390.
**Stories:** US-AUTH-01, US-AUTH-02, US-AUTH-03, US-AUTH-04, US-PLAT-02, US-PLAT-07, US-NOW-06.

```text
┌──────────────────────────────────────────────────────────────────┐
│ [leaf]erdify · DASHBOARD                                         │   no nav, no role pill
├──────────────────────────────────────────────────────────────────┤
│ ┌ SIGN-IN DIDN'T COMPLETE ─────────────────────────────────────┐ │   one card per page
│ │ The sign-in link expired or was already used. Try again.      │ │
│ │ [Sign in]                                                     │ │
│ └───────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

| Page | When | What the user sees |
|---|---|---|
| Sign-in redirect `/auth/login?returnTo=` | Any page without a session | Nothing: 302 to `/auth/login?returnTo=<path>`, which answers 302 to Authentik's authorization endpoint `https://auth.vallery.net/application/o/authorize/` (from the discovery document of issuer `https://auth.vallery.net/application/o/verdify-dashboard/`) with `client_id=verdify-dashboard` and `code_challenge_method=S256`; the second factor happens there, and the member-tier flow has a caretaker enrol one at first sign-in (VD-23) |
| Callback `/auth/callback` | Back from Authentik | 302 to `returnTo`; only a same-origin relative path is kept, anything else goes to `/` |
| Sign-in failed (rendered by the callback) | State mismatch, expired login cookie, Authentik error | "Sign-in didn't complete. Try again." with Sign in; no session cookie is set |
| Sign-in unavailable | `/auth/login` cannot reach Authentik | 503 "Sign-in is unavailable. Try again in a minute."; people already signed in keep reading, and Stop works |
| No access | Signed in, but in neither dashboard group | "Your account doesn't have dashboard access. Ask Jason to add you to a dashboard group." with Sign out |
| Session ended (a card over the current page) | A poll or a write gets 401 (the 12 h session ended or was removed) | "Your session ended. Sign in again." Sign in returns to the same URL and query |
| Signed out | After POST `/auth/logout` | Authentik's end-session page, then its sign-in page |
| Not found | Unknown route | "Page not found" with a link to Now |
| Something went wrong | BFF 500 | "Something went wrong. Retry" with the request id |

Loading is the redirect itself; there is no stale or offline state here. The API being down is a banner (§1.2), and a
viewer's read-only view is §1.3.

Acceptance:
1. `curl -sI https://dashboard.verdify.ai/water` with no cookie returns 302 to `/auth/login?returnTo=%2Fwater`;
   `/auth/login` returns 302 to a Location starting `https://auth.vallery.net/application/o/authorize/` with
   `client_id=verdify-dashboard` and `code_challenge_method=S256`; after sign-in the browser lands on `/water` holding
   only `__Host-verdify_session`.
2. `returnTo=//evil.example` lands on `/`.
3. A callback with a mismatched state shows "Sign-in didn't complete" and sets no session cookie.
4. A 401 during polling shows the session-ended card; after sign-in the same URL, query included, reopens.
5. Sign out sends a POST with the exact Origin, deletes the session and ends at Authentik's sign-in page.

## 11. Story-to-screen map

Every MVP and Next story in [product](product.md). "Shell" is §1.2-§1.5; "—" marks a story with no screen of its own.

| Story | Priority | Screen | Where |
|---|---|---|---|
| US-NOW-01 | MVP | SCR-01 | Hero values and verdicts |
| US-NOW-02 | MVP | Shell, SCR-01 | Banners, Activity count, tiles |
| US-NOW-03 | MVP | SCR-01, SCR-07 | Running chips, occupancy chip, board |
| US-NOW-04 | MVP | SCR-01, SCR-04 | Water today card, last water per zone |
| US-NOW-05 | Next | SCR-01 | Coming up |
| US-NOW-06 | MVP | Shell, all | States, API-unavailable, ingestor and not-reporting banners |
| US-NOW-07 | MVP | All | 390 layouts, bottom tabs, Stop all water on Now and Water |
| US-NOW-08 | MVP | SCR-01 | The hero's one-sentence answer |
| US-GRF-01 | MVP | SCR-02 | Linked panels, band in force, setting steps, SUPERSEDED |
| US-GRF-02 | MVP | SCR-02 | Equipment lanes, 31-day limit |
| US-GRF-03 | MVP | SCR-02 | Ranges, resolution line, summaries, CSV, URL, retention |
| US-GRF-04 | MVP | All | Units and time (§1.5) |
| US-GRF-05 | Next | SCR-02 | Outdoor and forecast |
| US-GRF-06 | Next | SCR-01, SCR-02 | Zone tiles, `/graphs/zones` |
| US-GRF-07 | MVP | SCR-02 | Metric picker |
| US-GRF-09 | MVP | SCR-02 | `/graphs/scorecard` |
| US-GRF-10 | Next | SCR-02 | PPFD and daily DLI in the metric picker once the PAR meter ships (VD-71); no DLI before |
| US-PLN-01 | MVP | SCR-03 | Plan in force |
| US-PLN-02 | MVP | SCR-03 | Lanes (24 h, 72 h chip), MANUAL and SUPERSEDED |
| US-PLN-03 | MVP | SCR-03, SCR-01 | Planner health, Iris tile |
| US-WAT-01 | MVP | SCR-04 | Board |
| US-WAT-02 | MVP | SCR-04 | Line state and reasons |
| US-WAT-03 | MVP | SCR-04, SCR-01 | Minutes by zone, Water today |
| US-WAT-04 | MVP | SCR-04 | Runs |
| US-WAT-05 | MVP | SCR-04, SCR-06 | Week view outcomes, `irrigation_run_missed` alert |
| US-WAT-06 | MVP | SCR-04 | Schedule editor and week preview |
| US-WAT-07 | MVP | SCR-04 | Edit, disable, delete; the Manual box |
| US-WAT-08 | MVP | SCR-04 | Fertilizer schedules with prewet and flush |
| US-WAT-09 | MVP | SCR-04 | Line enables with the check |
| US-WAT-10 | MVP | SCR-04 | Mister zones |
| US-WAT-11 | MVP | SCR-04 | Mist schedules |
| US-WAT-12 | MVP | SCR-04 | Run… and the confirm dialog |
| US-WAT-13 | MVP | SCR-04, SCR-01, SCR-06 | Stop, Stop all water (every line, through the bridge before the firmware), the Stop result |
| US-WAT-14 | MVP | SCR-04 | Pause and resume (a pause past a block skips it) |
| US-WAT-15 | MVP | SCR-04 | Program banner (`src=nvs` or `rom`), not-reporting state |
| US-WAT-16 | MVP | SCR-04, SCR-01 | Occupancy in limits and mist cards, occupancy chip |
| US-WAT-17 | MVP | SCR-04 | VPD source per mister zone |
| US-WAT-18 | MVP | Shell, SCR-01, SCR-04, SCR-06 | Flow banner, hero, alert row |
| US-WAT-19 | MVP | SCR-04, SCR-05 | Limits strip, line options, site caps |
| US-WAT-20 | Next | SCR-04 | Daily water use |
| US-WAT-21 | MVP | SCR-04 | Fallback slots in the week view, "VPD fallback active" |
| US-WAT-22 | MVP | SCR-04, SCR-01, SCR-06 | Mister back-off and fault, Clear fault, `irrigation_line_faulted` |
| US-WAT-23 | MVP | SCR-04 | L01 Run now through the bridge before the firmware (§1.5) |
| US-SET-01 | MVP | SCR-05 | Catalog, Band in force |
| US-SET-02 | MVP | SCR-05 | Device value, request and its reason |
| US-SET-03 | MVP | SCR-05 | Drawer and Save, crop band rows |
| US-SET-04 | MVP | SCR-05, SCR-03 | Manual, Release, MANUAL lanes |
| US-SET-05 | MVP | SCR-05 | Rows read CONFIRMED again after a reboot (no column of their own) |
| US-SET-06 | Next | SCR-05 | Drawer history and Revert |
| US-SET-07 | Next | SCR-05 | Crop band preview |
| US-ACT-01 | MVP | SCR-06 | Activity log |
| US-ACT-02 | MVP | SCR-06, SCR-01 | Open alerts, Needs someone |
| US-ACT-03 | MVP | SCR-06 | Expanded alert row, Open in Graphs |
| US-ACT-04 | MVP | SCR-06 | Acknowledge, resolve |
| US-EQP-01 | MVP | SCR-07 | Board |
| US-EQP-02 | Next | SCR-07 | Why |
| US-EQP-05 | Next | SCR-07 | Lights |
| US-EQP-06 | MVP | SCR-07, SCR-01 | Bursts, restart, burst chip |
| US-AUTH-01 | MVP | SCR-09 | Sign-in redirect and callback |
| US-AUTH-02 | MVP | Shell, SCR-09 | Role pill, no-access page |
| US-AUTH-03 | MVP | All | Viewer read-only (§1.3) |
| US-AUTH-04 | MVP | SCR-09, all writes | Exact-Origin POSTs, sign-out |
| US-AGT-01 | MVP | — (MCP) | Same reads as SCR-04 |
| US-AGT-02 | MVP | — (MCP) | Agent writes show on SCR-04 and SCR-06 |
| US-AGT-03 | MVP | SCR-06 | `steward · agent` attribution |
| US-AGT-04 | MVP | — (MCP) | Same refusal codes the drawers show |
| US-AGT-05 | MVP | SCR-03, SCR-05, SCR-04 | MANUAL lanes, rows and items |
| US-AGT-06 | MVP | — (MCP) | Iris water writes show as `iris` with AI on SCR-04 and SCR-06 |
| US-AGT-07 | MVP | SCR-05 | AI badge with the agent name |
| US-AGT-08 | MVP | — (MCP) | — |
| US-PLAT-01 | MVP | — (deploy) | — |
| US-PLAT-02 | MVP | SCR-09 | Anonymous 302 to `/auth/login`, then Authentik |
| US-PLAT-03 | MVP | — (deploy) | — |
| US-PLAT-04 | MVP | All writes | REQUESTED → CONFIRMED (§1.4) |
| US-PLAT-05 | MVP | — (services) | Same answers on MCP and every screen |
| US-PLAT-06 | MVP | SCR-02 | Series and resolution line |
| US-PLAT-07 | MVP | Shell | Changes-off banner, disabled controls |
| US-PLAT-08 | MVP | Shell | Footer build and API revisions |
| US-PLAT-09 | MVP | — (monitoring) | — |
| US-PLAT-10 | MVP | All | Weather's stack and look, polling |
| US-PLAT-11 | MVP | — (contract) | Fixture API behind every acceptance criterion here |
| US-PLAT-12 | MVP | SCR-04 | Program banner `src`; the before-the-firmware state |
| US-PLAT-13 | MVP | SCR-07 | Bursts replace the Home Assistant buttons |
| US-PLAT-14 | MVP | — (deploy) | — |

Later: US-GRF-08 (SCR-02 event markers), US-PLN-04 (transition audit), US-PLN-05 (replan now), US-ACT-05 (platform
alerts, which stay in Alertmanager and Slack), US-EQP-03 and US-EQP-04 (controller and sensor health) and US-PLT-01 to
US-PLT-03 (SCR-08).

## 12. Who builds each screen

The issues in the [execution plan](execution-plan.md) build this doc against the committed OpenAPI and the fixture
API, so a screen can start before its endpoints land; its live check waits for them.

- **VD-03:** the content components, badges and states ([design system](design-system.md) §9), among them `AlertRow`,
  the alert row Now and Activity share.
- **VD-04:** §1 (routes for SCR-01 to SCR-09 and `/more`, the desktop nav and the phone bottom bar, the shell poll of
  API-01 and API-02 driving the banners, the Activity count and the footer revisions, role gating, the changes-off
  state, and the one words table for mode, reason, refusal and alert codes) and SCR-09.
- **VD-05:** the S, P and C write hooks with one `Idempotency-Key` per write, the Manual box, the edit drawer, the
  water dialog and the Stop result toast.
- **Screens:** VD-09 SCR-01; VD-10 SCR-02 with the metric picker and the scorecard; VD-11 SCR-03; VD-12 SCR-05;
  VD-13 SCR-06; VD-14 the SCR-07 board and lanes.
- **Water (SCR-04):** VD-15 the read-only board, week view, minutes and runs; VD-16 Run, Stop, Stop all water (through
  VD-64's bridge before the engine), Clear fault and the SCR-07 bursts and restart; VD-69 line enables with the check,
  the mister switches, caps and pause; VD-17 the schedule editor and its draft in the week view.
- **Next panels:** VD-18 (coming up, zones, outdoor and forecast), VD-19 (setting history and revert, the band
  preview) and VD-20 (why, grow lights, daily water use). Plants (SCR-08) has no issue until it is scheduled.
