# Dashboard screens

dashboard.verdify.ai has five sections, the same at every width: Now, Water, Graphs, Activity and Control. They hold the
nine screens this doc specifies: Now; Water (Today, Schedules, History); Graphs (Climate, Zones, Statistics); Activity
(Alerts, Log); Control (Settings, Plan, Equipment); Plants (Later); and Sign-in and errors. Desktop uses
weather.vallery.net's sticky header with the five sections; phones get the same five in a fixed bottom bar, each an
icon over its label. Operators and viewers see the same screens and data, and viewers never see a write control. Each
spec gives the purpose, the stories served, the layout as built with its API operations and refresh, the actions with
what the user sees from requested to confirmed, the degraded states and the acceptance criteria. Every write follows
one model: validate on submit, last write wins, a Manual flag that Iris skips, and REQUESTED → CONFIRMED from device
readback; the one dialog confirms starting water. A story-to-screen map and the issues that build each screen close the
doc.

Status: v3, built (2026-11-04). The [redesign](redesign.md) rebuilt the navigation, layout, cards, charts and
schedules; this doc now describes that build. Its wireframes are the redesign's (§5, §6), which this doc points to
rather than repeats. Behaviour, data, honesty, role and write rules are unchanged from v2 (Round 2, 2b and Q-01 to
Q-14 applied).

[README](README.md) · [product](product.md) · **screens** · [redesign](redesign.md) · [architecture](architecture.md) ·
[irrigation](irrigation.md) · [design system](design-system.md) · [execution plan](execution-plan.md) ·
[OpenAPI](openapi/dashboard-api.v1.yaml)

Operation ids (API-NN, API-IRR-NN, BFF-NN, INT-NN, OPS-NN) are the [OpenAPI](openapi/dashboard-api.v1.yaml) file's,
which is canonical. Stories are [product](product.md) §6. Components, badges and states are the
[design system](design-system.md)'s. "Round 2 #N", "Round 2b X" and "Q-NN" are entries in [decisions](decisions.md),
which wins over this doc. Wireframe values are illustrative; "steward" stands in for the first named agent (VD-23).

## 1. Information architecture

**Answer:** five sections, each one tap from the nav at every width, holding the screens as views behind in-page tabs
(redesign §2.1, D-UX-01). Detail opens in a drawer, a popover or in place; there are no wizards and no screen exists
only to confirm something.

| Id | Screen | Section › view | Routes | Answers | Build |
|---|---|---|---|---|---|
| SCR-01 | Now | Now | `/` | Is everything OK? What is running? Is water fine? What needs someone? | MVP |
| SCR-02 | Graphs | Graphs › Climate, Zones, Statistics | `/graphs`, `/graphs/zones` (`?zone=` focuses one), `/graphs/statistics` | What happened and what is planned, over any range? How does each zone compare? Is it getting better? | MVP |
| SCR-03 | Plan | Control › Plan | `/plan`, `/plan?edit=<param>` | What will Iris tell the controller, and is Iris healthy? | MVP |
| SCR-04 | Water | Water › Today, Schedules, History | `/water`, `/water/schedules`, `/water/history` | What waters, when, why not, and how many minutes per zone? | MVP |
| SCR-05 | Settings | Control › Settings | `/settings`, `/settings?edit=<param>` | What is every setting, what does the device report, and change it | MVP |
| SCR-06 | Activity | Activity › Alerts, Log | `/activity`, `/activity?alert=<id>`, `/activity/log` | What needs someone? Who changed what? | MVP |
| SCR-07 | Equipment | Control › Equipment | `/equipment`, `/equipment?edit=<param>` | What is each actuator and the controller doing? Bursts and restart | MVP |
| SCR-08 | Plants | — (out of the nav) | `/plants` | What grows where? | Later |
| SCR-09 | Sign-in and errors | — | `/auth/login`, `/auth/callback`, error pages | Who are you, and what went wrong? | MVP |

Removed and redirected (redesign §2.1): `/more` is gone and answers "Page not found"; `/graphs/scorecard` redirects to
`/graphs/statistics` with its query; `/activity` with a log filter (`source`, `actor`, `action`, `target`) redirects,
replacing history, to `/activity/log` with the same query, while `?alert=` and `?resolved=` stay on Alerts.

### 1.1 Navigation

The redesign's §2.2 and §2.3 draw the header and the phone bar.

| Rule | Spec |
|---|---|
| Desktop (861 px and up) | Weather's sticky header: the lockup, then Now, Water, Graphs, Activity and Control with the 2 px forest underline on the current section (`navigation "Sections"`), then the account button. Five links fit, so the nav never scrolls sideways. Control links to `/settings` and is current on `/settings`, `/plan` and `/equipment` |
| Phone (860 px and below) | A 52 px header with the lockup and the account button (the role pill stays visible), and a fixed bottom bar (`navigation "Sections, phone"`, 56 px plus the safe-area inset) with the same five in the same order, each an inline SVG icon over a one-word label (a gauge, a droplet, a line chart, a bell, sliders), a fifth of the width and at least 44 px. There is no More |
| Count | The one open-alert count (§1.2) is a pill after "Activity" (on the bell's corner on phones), filled by the worst open severity: critical white on `--fault`, warning `--warn-text` on `--tint-warn`, info ink on `--field`. Its screen-reader text is "N open alerts" |
| Account menu | The role pill (OPERATOR or VIEWER) and, from 1100 px, the display name open a disclosure: "Signed in as Jason (jason)", the role in words ("Operator: can change anything", "Viewer: read-only") and Sign out, a POST form |
| Page header | Every view has one: the eyebrow is the breadcrumb (the section's name, a link to its default view except on that view; "GRAPHS › ZONES" on a focused zone), the h1 names the view (redesign §2.1's titles), one status line from data whose height is reserved, at most two actions, then the section's tabs (a labelled `nav` of chips, the current one `aria-current="page"`). On Water the tabs and "■ Stop all" share one sticky row at 860 px and below. Now has no page header: its hero is its head |
| Document title | "Schedules · Water · Verdify dashboard", or "Water today · Verdify dashboard" when the title already names its section |
| Plants | Out of the nav until it ships |
| URLs | Every view state is in the URL (range, settings and metrics on charts, layers, filters, the open drawer or popover, the Water view and day, the expanded alert), so a copied link reproduces the view. Tabs, ranges, filters and views push history; drawers, dialogs and popovers push when they open and replace when they close, so Back closes them first |

### 1.2 Shell on every page

| Element | Shows | Data | Refresh |
|---|---|---|---|
| Account menu | Role pill, name (from 1100 px) and the disclosure of §1.1 with Sign out | API-01 | load, 60 s |
| The fault banner, one at a time, worst first | A row inside the sticky header, so it never scrolls away (`[data-banner=fault]`), at most two lines on a phone with its link in view. "Dashboard API unavailable" after two failed polls in a row; nothing green shows while it is up. "Ingestor not running since 03:12. The controller keeps running on its own." while the ingestor heartbeat (`freshness.ingestor`, a `system_state` row every 60 s, Q-01) is older than 180 s. "Controller not reporting since 03:12. If it has power, it keeps running VPD misting, its caps and (with the irrigation firmware) its schedules" while the heartbeat is fresh and the newest `climate` row is older than 300 s; no page ever says "Controller offline". "Water flowing with every Verdify valve closed since 03:02 · 3 gal in 10 min: a leak, or the hose, sink or hydro in use. If no one is using water, close the spigot" while the `leak_detected` alert is open, with no Stop (no Verdify valve is open); once the meter reads under 0.1 gal/min twice it reads "Flow stopped at 03:20", never an all clear | API-02 `freshness`, `leak` and alerts | 15 s |
| Warn banners | In flow at the top of `<main>`, above the page header, one line each (`[data-banner=warn]`): "Settings not reaching the controller since 06:10 (4 differ)" while `setpoint_delivery_stuck` is open (API-02 `writer.stuck`, VD-62; Round 2 #1); "Changes are switched off; reading still works" (`writes_enabled: false`) | API-02, API-01 | 15 s, 60 s |
| Activity count | The one open-alert count (VD-55, VD-72): unacknowledged (disposition open) `v_open_alerts` rows, repeats of one `alert_type` and `sensor_id` counted once, so it equals the unacknowledged rows Alerts lists (API-02 `alerts.open`). It is the nav's pill (§1.1); in a page body it shows once: in Now's answer or its Needs someone title, and in the Alerts status line. MCP uses the same count | API-02 | 15 s |
| Footer | "Verdify dashboard · build `<sha>` · API `<sha>`", "Times are America/Denver" (the time zone is stated once here, on chart axes and in every `<time title>`; D-UX-08), graphs.verdify.ai ↗, lab.verdify.ai ↗ | BFF `SOURCE_REVISION`, API-01 | load |
| Toasts | After each write ([design system](design-system.md) §4.5); Stop's stays until dismissed | — | — |

Each banner links to the page that explains it (ingestor → Now's "All readings" at `/#data`, not reporting →
Equipment, flow → its alert in Activity, settings → Settings filtered to Requested). Polling is weather's
`polling.ts`: paused while the tab is hidden, backing off from 15 s to 120 s on errors; a chart whose window has closed
never refetches.

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
| **P** Program | API-IRR-03, -04, -05, -08 | Save; the item and Water's program state (its status line) read REQUESTED ("rev 43 sending"), polled every 5 s; CONFIRMED when `irr_program_id` reports the new hash; "Not confirmed: <reject in words>" after 5 min. Before the irrigation firmware the item is stored and stays REQUESTED with "applies with the irrigation firmware" until `irr_program_id` first appears |
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
someone, with the caretaker's on-site actions. Everything else is one tap away.
**Stories:** US-NOW-01, US-NOW-02, US-NOW-03, US-NOW-04, US-NOW-05, US-NOW-06, US-NOW-07, US-NOW-08, US-GRF-06,
US-WAT-13, US-WAT-16, US-WAT-18, US-WAT-22, US-EQP-06.

The layout is the redesign's §5.1 (wireframes at 1440 and 360 px): the ink hero, the quick actions under it, Needs
someone beside Water today (stacked below 861 px), "Running and next", then the zone strip. Each fact shows once on
the page: the open-alert count in the answer or Needs someone's title, Stop all water once, the next runs only in
"Running and next".

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Hero | The answer as the h1, worst first, in the banners' order, never a link and never underlined: "Can't reach the greenhouse data" (API-02 failing); "Ingestor not running since 03:12"; "Controller not reporting since 03:12"; "Water flowing with every Verdify valve closed"; a fresh metric outside its band, the steady "Out of band" with every reading that is out on one plain line under it ("Too hot: 84.1 °F (band 62–70 °F) · Air too dry: 1.67 kPa (band 0.86–1.29 kPa)", each with a quiet "Graphs ›"; a reading counts as out after 2 min out and as back in after 2 min in, Jason 2026-11-05, #1338); open alerts nobody has acknowledged, the one count of §1.2 ("2 things need someone"); "Waiting for fresh data" (its age is the line under the value, never repeated); otherwise "All OK". Where a row is explained is a quiet link under the h1. Under it the indoor temperature as the hero value with "Updated N s ago" (`#data`, which opens "All readings" in place). Three band bars (temperature, VPD and RH; [design system](design-system.md) §4.6 `BandBar` on ink), each linking to its Graphs panel, with the band in force and the value (one track, the band box and the dot; today's low and high in its name only); a verdict word only out of band; RH reads "no band". "In band today" (temperature and VPD) against yesterday's, only with data. The status line: the mode in words (to `/equipment#why`), Iris and the plan in force (to `/plan`), outdoor (to Graphs with the outdoor layer), occupancy. "All readings" holds every source with its time, the band in force (DERIVED with its age) and the raw mode code with `mode_reason` | API-02 (`climate`, `system_state` and the band rows); API-04 over the last 25 h for today's range, and its `daily.temp_in_band_pct` and `daily.vpd_in_band_pct` for today and yesterday | API-02 15 s; API-04 60 s |
| Quick actions (operators) | Stop all water first (full width on a phone), then Fans 15 min, Humidify 5 min and Vent bypass 10 min (D-UX-05), each one API-IRR-12 with no dialog. Before the irrigation firmware the row holds Stop all water and "Bursts arrive with the irrigation firmware". Viewers get no row | API-IRR-01 (engine flag) | 15 s |
| Needs someone | A list card: the top three alerts nobody has acknowledged, by severity then age (`AlertRow`, shared with Activity: severity word, title, zone or line, age, "Details ›" into Activity, Acknowledge with an optional note); a mister's watchdog back-off or latch with Clear fault on a latch; "N settings not confirmed ›" into Settings' Not confirmed filter, unless the stuck-delivery banner already says so. A fence breach points to the quick actions' Stop all water instead of repeating it. Its title carries the count unless the answer already does; empty, it reads "No open alerts" per §1.5 with what was resolved in the last 24 h: "3 resolved in 24 h ›" into Activity's resolved group, or "No open alerts · nothing resolved in 24 h" with no link | API-14 `status=recent` (Activity's read, one key); API-IRR-01; API-02 `writer.differing` | 15 s |
| Water today | A row per zone (wall, center, south, west): minutes today against the cap as a `Meter` ("14 / 120 min", "+12 min over") and when it last got water ("Wall last Fri 07:31, 12 min · Center now (VPD) · West 9 days ago"; a zone with no enabled line reads "Not set up", never 0); SAF-4 gallons against 360 as a meter in `--eq-water` with "daily mist and fog ceiling", the words Water's Site today shows; the flow state in words; "Water ›" | API-IRR-01 | 15 s; 5 s while a Stop is pending |
| Running and next (`#coming-up`) | "Running now": a row per running actuator with a dot in its equipment token and "since HH:MM"; wet lines add the cause and, with the irrigation firmware, the time left; bursts from the controller's readback ("Fans burst · 12 min left") with one Clear for operators; one occupancy row ("Someone inside: misters wait (Frigate, 4 min ago)", "Nobody seen: misters may run (last person 05:10)", "Occupancy unknown: misters may run", or "Occupancy mist hold is off: misters may run"). From 861 px a `TimeTrack` over the last 6 h and the next 12 h: heat, vent and fans, fog and mist and the drips done (from API-05, read once the track nears the screen), the runs planned ahead, what a run or burst still has to go, Iris's triggers and the plan's changes as markers, sunrise and sunset with night washed, and the now rule. At 860 px and below the same future as the "Next 12 h" list (`[data-coming]`). "Later": each line with no run in the next 12 h | API-02; API-05 (last 6 h); API-IRR-02 and API-07 | 15 s; 60 s |
| Zones (`#zones`) | A tile per zone (north, center, south, east, west, the order Graphs → Zones uses): temperature and VPD, a band bar of its VPD against the zone's band in force with today's low and high and a 24 h VPD sparkline over that band, the two named by one mono "VPD" label, and its tag as a text mark only when the numbers are not the zone's own probe (PEER or HOUSE; MEASURED, the normal case, shows none), amber only when a zone with a probe has fallen back. The title's link covers the tile and opens `/graphs/zones?zone=<zone>` | API-04 (last 15 min, three requests of at most 8 keys), API-06 per tile, API-03, API-IRR-01; the day read for the sparkline | 60 s |

Mode words (one table in the app; an unmapped code shows raw): `IDLE` "In band, holding"; `VENTILATE` "Venting to
cool"; `DEHUM_VENT` "Venting to dry the air"; `SEALED_MIST` with stage `WATCH`, `FOG`, `S1`, `S2` "Sealed, watching",
"Sealed, fogging", "Sealed, misting one zone", "Sealed, misting all zones"; `THERMAL_RELIEF` "Venting a heat burst";
`SAFETY_COOL` "Emergency cooling"; `SAFETY_HEAT` "Emergency heating"; `SENSOR_FAULT` "Sensor fault: equipment off".

| Action (operator) | Operation | Feedback |
|---|---|---|
| Stop all water (quick actions) | API-IRR-07 `{}` | C, then the Stop result (§1.4). One tap, no dialog, never blocked by the writes flag; before the irrigation firmware it works through the bridge (§1.5); running wet lines turn OFF from relay edges within 30 s |
| Fans 15 min, Humidify 5 min, Vent bypass 10 min (quick actions) | API-IRR-12 `{kind, minutes}` | C. No dialog; the burst shows in "Running now" once its readback turns on |
| Acknowledge an alert, optional note | API-15 | R |
| Clear a running burst | API-IRR-12 `{kind: "clear"}` | C |
| Clear a mister fault | API-IRR-13 `{line_id}` | C. The line returns to idle or running; Activity records who cleared it and why |

| State | Now shows |
|---|---|
| Loading | The hero, the band bars and every card keep their reserved heights as skeletons; each card loads on its own |
| Empty | "Nothing running"; "No open alerts" per §1.5 |
| Stale | The answer reads "Waiting for fresh data" over "Last data 14 min ago (10:28)"; values muted with STALE; no verdict: the mode reads "Last mode: holding (10:28)" (`IDLE`'s "In band, holding" asserts the band), and the band bars say "As of 10:28" and draw their bands neutral, as with the API down (hatching stays for unknown) |
| Not reporting | The answer reads "Ingestor not running since 03:12" or "Controller not reporting since 03:12" (§1.2); values STALE and the mode "Mode unknown"; "Running now" reads "Equipment state unknown since HH:MM"; Needs someone reads "In-app alerts may be missing: ingestor not running since 03:12" or "… no data since 03:12" (§1.5) |
| Error | API-02 down: "Can't reach the greenhouse data" in the hero, nothing green, and neither the band bars nor "In band today" render. A failed or not yet served API-IRR-01 reads "Water status unavailable" with Retry, never idle; every card fails alone |
| Before the irrigation firmware | Water today shows relay states, Stop all water works on every line through the bridge (§1.5), and the quick actions say when the bursts arrive; L01 runs from Water |

Acceptance:
1. With the fixture "in band" payload and no open alert, the h1 reads "All OK" with 64.8 °F, "Updated 20 s ago" and
   the three band bars above the bottom bar at 360 × 740; Stop all water sits above it at 390 × 844.
2. With the heartbeat fresh and the newest `climate` row 6 min old, every page shows "Controller not reporting since
   …" and none says "Controller offline"; with the heartbeat 4 min old, every page shows "Ingestor not running since
   … The controller keeps running on its own." In both, Now shows no verdict and Needs someone reads "In-app alerts
   may be missing", never "No open alerts". With API-02 failing twice, the hero reads "Can't reach the greenhouse
   data" and nothing is green.
3. An operator taps Stop all water once, with or without the irrigation firmware: one API-IRR-07 request, "Sending…",
   then the fixture's relay edge turns the wet line OFF and the Stop toast names what can still start, with Disable
   for that line (none before the irrigation firmware). Fans 15 min sends one API-IRR-12. A viewer sees no quick
   actions, Acknowledge or Clear.
4. A null RH renders "—" with its reason; with the controller reporting, an idle actuator reads OFF since its last
   real change, never "unknown"; with it not reporting, every state reads "unknown" with its age.
5. Occupancy always shows: confirmed reads "Someone inside: misters wait (Frigate, 4 min ago)"; unknown reads
   "Occupancy unknown: misters may run".
6. Each band bar draws the band, the value and today's low and high; out of band adds a verdict word and in band adds
   none; the open-alert count and Stop all water each appear once in the page body; no next-run sentence appears
   outside "Running and next"; a zone tile opens `/graphs/zones?zone=<zone>`.

**As built.** Where the build (VD-09, VD-16, VD-18 and the redesign's WP1) settles a detail this section leaves open,
or differs from it:

| Topic | As built |
|---|---|
| The answer's stale row | "Waiting for fresh data" holds while API-02 marks the climate source stale, and also while an indoor reading is older than twice the climate cadence at the answer's `server_time`, or a reading with a band in force (temperature, VPD) has no value. In production the readings come from the newest climate row, so this is the same test; it also covers the `stale-data` fixture (climate fresh, readings 14 min old) and a probe that drops out of a fresh row. A missing RH with no RH band is not stale. Before API-02 first answers the sentence reads "Checking the greenhouse…" and claims nothing |
| One API-02 request | Now reads the shell's API-02 poll; the shell polls under the same key the write hooks read, so Stop notes what runs from it and reads it every 5 s while it confirms, and an Acknowledge refetches the one count there. The answer's first row follows the newest read of that entry (the poll or a Stop's readback), and a 401 is the session-ended card, never "Can't reach the greenhouse data" |
| Phones | 860 px and below: the hero stacks the answer, the value with "Updated", the band bars and the status line; Stop all water is full width over the three bursts side by side; "Running and next" is two lists. The hero is about 520 px tall at 360 px, because the band rows, the status links and `#data` are 44 px targets; the required items still sit above the bottom bar (the band bars end near y ≈ 390 at 360 × 740, Stop all water near y ≈ 700 at 390 × 844, by layout; the e2e suite measures them in CI). Opening `/#data` (the ingestor banner's link) opens "All readings" |
| Water today | Minutes read whole minutes against the cap, the Meter's own words ("132 / 120 min · +12 min over", never "2 h"). Stop all water is the quick actions', not the card's. Last water reads "misted 09:58 (VPD)" for a mister and "last 07:31, 12 min" for a drip ("last Fri 07:31, 12 min" on another day, so the time never reads as a next run) (times are the instant's own clock, so a Friday before the fall-back reads MDT) |
| Line states before the irrigation firmware | Water today reads Unknown only while an enabled line's state is unknown, or, with the firmware, while `irr_state` is stale. Before it no `irr_state` exists (a source never seen reads stale), and the relay states stand alone (irrigation §9) |
| Needs someone | The top three keep API-14's order (severity, then age as the API sorts it: last seen, newest first), so they are the first unacknowledged rows Alerts lists. A row acknowledged on the page stays in place for the visit, reading "Acknowledged by Jason · 10:42" (the signed-in person by name); the title and the answer follow the one count as API-02 refetches. "N settings not confirmed" counts API-02 `writer.differing` (values that have differed from the readback for more than 10 min), not every API-09 row |
| Clear on a burst | One "Clear" after the burst rows in "Running now", however many bursts run, because `{kind: "clear"}` ends them all. It shows while API-IRR-01 reads `engine: true`, so before the irrigation firmware, or while that read fails, a push-button burst keeps its row with no Clear |
| Clear fault | In Needs someone, beside the mister's words, only on a `mister_fault` of L04, L07 or L09: API-IRR-13 names no other line, so a latch reported only on L08 or L10 reads in words with no button. A latch whose `irrigation_line_faulted` alert is in the list carries its Clear fault on that alert's row, so it reads once |
| Next transition | A waypoint that repeats what the lane already holds is no change, so it never counts as a plan change or reads "1.60 → 1.60": Iris restates its whole plan at each time. A plan change is a time, after now, at which a lane's value changes; values compare as they print, so a binary32 readback's noise is no change. A switch (`sw_*`) reads ON or OFF, and a value keeps the decimals it carries. A lane in force by hand is left out, as Iris skips it |
| Running and next's parts | Each read fails alone with Retry ("Plan unavailable", "Next run unavailable"), never "none", because a paused line would otherwise read as a run. API-05 for the track's last 6 h is read only once the track nears the screen, so a phone, which shows the lists, never reads it. The plan's change markers take turns above and below in a taller row, so labels a few hours apart stay readable. Iris's triggers read as plan events with their times ("SOLAR_MAX plan 12:41"; a past one "SUNRISE plan 07:00 ✓", or with its outcome, "· missed"), so the Iris row never reads as a second sun row. With the engine off the runs read "Schedules run with the irrigation firmware" |
| Fallback slots | A `degraded_only` occurrence is the line's next run as the rule says, and carries its note: "L04 today 11:47 · 3 min · vanda-degraded · only if VPD sensing is degraded" |
| Who | "by Jason" is the signed-in person's display name (API-01); anyone else reads by `paused_by.name` |
| Zone reads | Three API-04 requests of at most 8 keys, each holding its zones' keys whole, over the last 15 minutes on the server's clock (API-02's skew), so a device clock running behind never reads a live probe as old; one API-06 per tile; API-03's cadences. All poll every 60 s. A failed API-04 fails the card with Retry; a failed API-06 reads "band unavailable" on that tile |
| Freshness | A key's newest non-null bucket counts from its end, against the answer's `server_time`, as the charts' STALE does (`charts/retention.ts`). The sample's own time is somewhere in that bucket, and counting from its start would flag a healthy 60 s feed |
| Peers | Each metric's PEER value is the mean of the peers fresh on that metric; none reads "—" with "No fresh peer probe". The tag itself follows the VPD keys alone |
| Tile | The tag is a text mark (D-UX-07): PEER, or HOUSE on a zone with a probe, in the warn colour; center's HOUSE is neutral. The band words read "band 0.45–0.80 kPa", north "house band …", with "above band" first when out of band. RH has no band |

## 3. SCR-02 Graphs · `/graphs`, `/graphs/zones`, `/graphs/statistics`

**Purpose:** past, present and plan in one instrument: climate against the band, any measured signal, setting steps,
events and equipment lanes on one cursor, at any range with its resolution stated; the zones side by side; the data
behind it; and whether the greenhouse is getting better, day by day.
**Stories:** US-GRF-01, US-GRF-02, US-GRF-03, US-GRF-04, US-GRF-05, US-GRF-06, US-GRF-07, US-GRF-08, US-GRF-09,
US-PLAT-06. Next: US-GRF-10.

Three views share the section's page header and the shell's one container: Climate, Zones and Statistics.
The redesign draws them (§5.5 to §5.7) and specifies the chart framework (§7).

| Panel | View | Shows | Data | Refresh |
|---|---|---|---|---|
| Story line | Climate | The page's status line: "Last 24 h: in band 96% of points · 2 excursions: too dry 13:10–13:40 (peak 1.31 kPa), too cold 05:50–06:05", at most three excursions ranked by duration times depth, each a link to its window ±30 min. It shows only with the climate panels' reads and the band, and never while the dashboard API is down | API-04, API-06 | with the series |
| Toolbar | Climate | Range chips Today, 24 h, 72 h, 7 d, 30 d and 90 d (Today runs to the next local midnight; a window that reaches now pads its own length ahead, at most 72 h; the default is 72 h, so the page opens on 72 h back and 72 h of forecast; decisions 2026-11-05, replacing D-UX-13), ‹ › to step by the window's length, Zoom out (×2) and Custom… (From and To days, up to 400). Then Preset (Climate, Water and soil, Light, Controller, which sets `m=`), Layers (outdoor and forecast, events by source, the min–max envelope, compare with yesterday or last week), + Metric and + Setting. Under it the chips of the settings drawn as steps (none by default) and the metrics on the charts, each with × to take it off. All in the URL: `range`, `from`, `to`, `set`, `m`, `preset`, `events`, `cmp`, `env`, `outdoor` | API-09 (setting chooser), API-03 (metric picker) | — |
| Chart stack | Climate | One card on one time axis ([design system](design-system.md) §5): the event rail on top (setting writes by people, Iris and agents, the forecast rule, alerts raised and resolved, water runs; marks closer than 8 px cluster; each opens its Activity entry, and "Events in this window (N)" lists them all); the temperature, VPD and RH panels (220, 200 and 160 px; 160, 150 and 120 at 860 px and below) and one panel per picked unit, each with its header row (name and unit, in band % when a band is drawn, low and when, mean, high and when; the cursor's values instead while the cursor is on the stack), one time chip riding the cursor, no floating tooltip; the band ribbon with the excursions outside it shaded `--tint-warn`; the min–max envelope from 7 d (`env=0` hides it); a comparison dashed with its delta in each header (`cmp=day\|week`); setting steps (device solid, REQUESTED dashed amber, PLANNED dashed right of now with AI, SUPERSEDED muted); the outdoor trace and the forecast dashed past now; the equipment lanes in a disclosure, open from 861 px. The foot holds the resolution line ("one point per 5 min · gaps stay empty, never zero"), the data table and "CSV of this window". Notes about the window (STALE, retention, daily summaries, the outdoor source and age, the rail's limits) sit once above the stack | API-04 per panel (and per compared panel), one for outdoor and one for the forecast; API-06; API-12 per setting; API-05 (lanes, up to 31 days); API-17, API-14 and API-IRR-11 (events; runs up to 31 days) | 60 s while the window includes now |
| Metric picker | Climate | "+ Metric" lists every API-03 key by group (house climate, zones, water and soil, light, outdoor, targets, controller, daily) with unit, cadence and provenance. A choice joins the panel for its unit, or opens a new panel, never a second axis; up to 8 metrics, kept as `m=`. `daily.*` keys may be charted and plot one point per day (D-UX-04). No DLI until the PAR meter (VD-71) | API-03, API-04 | — |
| Grid | Zones | A `ChartGrid` of small multiples: rows are metrics (temperature and VPD by default; RH through the Metrics chips, `metric=`), columns are north, center, south, east and west. One cursor, a uniform y scale per row, each cell washed with its zone's band, 120 px plots; only the first column draws a y axis and only the bottom row an x axis. Each column head is the zone's name, a link that focuses it, and its tag as a text mark (MEASURED, PEER or HOUSE; amber words when PEER). The status line reads "Last 24 h · each zone against its band · south from peer probes (address 4 out)". Range chips 24 h, 72 h and 7 d with ‹ ›, Zoom out and Custom…. On phones one metric at a time and the five zones stacked at 110 px | API-04 per row (every zone's key), API-06 per zone, API-IRR-01 and the probes' VPD for the tags | 60 s while the window includes now |
| Focused zone | Zones | `?zone=south`: the h1 "South zone" and the eyebrow "GRAPHS › ZONES", a Zone chip list (All zones first), the tag's note ("South's tile reads the mean of the fresh peer probes while its probe is out. These charts draw only South's own samples: a missing sample stays a gap."), and a chart stack of the zone's temperature, VPD and RH against its band, the controller's own target dotted | API-04, API-06, API-03 | 60 s |
| Key figures | Statistics | Over the last 30, 90 or 365 days or a custom run of days (`days=`, `from`, `to`): four KPI tiles with their change against the window before ("▲ 1.3 pts vs previous 30 d", in ink, never green or red): temperature in band, VPD in band, water (gallons a day and the total, linking to Water → History) and Iris (required plans written and missed) | API-04 `daily.*` keys for the window and the one before | 60 s while the window reaches today; the window before once |
| Day by day | Statistics | `StatusStrip` rows for temperature in band, VPD in band, Iris missed and alerts raised, with the levels of redesign §5.7, plus a gallons bar row; no data hatched, today outlined as partial; every cell opens that day in Climate (`/graphs?from=<day>&to=<day>`); on phones 30 days fit at once and longer windows scroll inside the strip's region | as above | as above |
| Typical day and daily range | Statistics | Side by side from 600 px. Typical day: the last 14 or 30 days (`typical=`) by hour, the median as a line, the middle half dashed and the band of the last full day behind them, one chart each for temperature and VPD (240 px). Daily range: each day's low to high in `--ink-muted` with a mean tick, for temperature and VPD (200 px) | API-04 hourly and daily climate buckets, API-06 | once a day; 60 s |
| Time in band per day and Data | Statistics | Per-day bars for temperature and VPD against a dotted 90% target; then "Data", the scorecard table in a disclosure (time in band day and night for the last 3 days only, Q-12; gallons; watering minutes by zone; Iris required plans; alerts by severity), a missing value "—" with its reason, and "CSV of this table", exactly the rows shown | API-04 `daily.*`, API-IRR-10 | 60 s |

Graphs writes nothing. A setting chip links to its drawer on Settings (`/settings?edit=<param>`).

| State | Graphs shows |
|---|---|
| Loading | Every panel's frame at its reserved height, so nothing shifts as the reads answer |
| Empty | "No data in this window" and a "Last 24 h" link |
| Stale | The trace stops at its last sample and the notes read "STALE · last data 06:02"; nothing is drawn to now |
| Not reporting | As stale, plus the banner; band and plan still draw |
| API down | The charts keep what they read but grade nothing: no band, no excursion shading, no story line and no in-band words; Statistics reads "No statistics while the dashboard API is unavailable." |
| Error | A read that fails says so in words with Retry ("Couldn't load part of this window."); the other panels render |
| Gaps and DST | Lines break and lanes hatch "unknown"; on 2026-11-01 every label from 01:00 to 01:59 carries its offset |
| Long windows | Over 31 days the lanes read "Equipment lanes cover at most 31 days. Narrow the range to see them" and runs leave the event rail with a note, while the climate panels render and the URL keeps the range. A span older than a metric's raw retention (climate 365 days, diagnostics 180, `setpoint_snapshot` readbacks 90; Round 2b D) draws its daily summaries and says so ("Daily summaries before 11/02/2025: raw data kept 365 days"); a metric with no daily summary reads "beyond retention (180 days kept)" and counts as no coverage. Nothing is clamped |

Acceptance:
1. `/graphs?range=72h&set=mister_engage_kpa` renders one chart stack with the temperature, VPD and RH panels, the band
   ribbon, the setting's device, requested and planned steps, the lanes and one time axis, all on one cursor;
   reloading the copied URL renders the same view.
2. `/graphs?range=7d&m=climate.soil_moisture_south_1&m=climate.flow_gpm` adds their panels to the stack with the L01
   lane, gaps broken and the resolution stated; at 7 d the min–max envelope shows and `env=0` hides it.
3. A fixture window with a 30-minute hole shows a broken line and a hatched lane span; no value reads 0; the resolution
   line states the bucket API-04 returned.
4. "CSV of this window" downloads exactly the plotted columns with local times and offsets; on Statistics, "CSV of
   this table" downloads exactly the rows shown, a missing value as "—" with its reason.
5. Moving the cursor updates every panel's header row to the values at that time with one time chip and no floating
   tooltip; wet-line lane intervals name their cause; a 90-day window shows the lanes message and still renders the
   climate panels; a 400-day window labels the span past climate's 365 days "Daily summaries before …".
6. The story line states time in band and the excursions, and an excursion link sets `from` and `to` around it; Today
   ends at the next local midnight, the 24 h view pads 6 h ahead, and ‹ ›, Zoom out and the layers live in the URL.
7. `/graphs/zones` shows every zone on one scale per row with one cursor; `?zone=south` reads "South zone" with the
   controller's dotted target.
8. `/graphs/statistics` shows 30, 90 and 365 days with four KPIs and their deltas, and a strip cell opens Climate for
   that day.

As built (VD-10, VD-18 and the redesign's WP2). Where the build settles a detail above, or differs from it:

| Topic | As built |
|---|---|
| Files | `dashboard/app/routes/graphs.tsx`, `graphs.zones.tsx`, `graphs-statistics.tsx` and `graphs-scorecard.tsx` (the redirect), `components/graphs/` (the toolbar's menus and chips, the metric picker, setting chooser, resolution line, story line, event marks, the statistics model, key figures, day by day and the scorecard table), `charts/stats-*` (typical day, daily range, time-in-band bars), `lib/graph-url.ts` (the URL and the window), `lib/graph-reads.ts` (the page clock and the window reads) and `lib/graph-story.ts` (the story line) |
| Export | No separate header "Export ▾" menu: "CSV of this window" and the data table stay in the stack's foot, which the chart stack always renders, so a second link of the same name never appears |
| Layers | Outdoor and the forecast share one Layers toggle (`outdoor=0`, kept in every link on the page, the custom range's form included); the forecast alone hides through its header swatch, which is not in the URL. Excursion shading is part of the band, always on, and off only while the API is down |
| Buckets | API-04 has no 30-minute bucket (15 min, then 1 h), so with the envelope on Climate asks for at most one point per 30 min from 7 d, which the API answers hourly; `env=0` returns to 15-minute buckets. The outdoor and compare reads ask the same |
| Zoom out | On a live window it stays live and ends at now (24 h becomes `range=48h`), through last-N ranges (`range=<n>h` or `<n>d`, at most 400 d); day and exact windows grow around their centre without passing now |
| Daily panels | A daily panel's % reads "% of day", so a setting in % (fog RH ceiling) never draws on it |
| Notes | The chart stack has no slot per panel for notes, so STALE, retention, daily summaries and the outdoor source and age render once, in the "About this window" list above the stack |
| URL notices | A range, window, metric or setting the page cannot use is dropped from the view with a notice in words ("Removed unknown metric …", "Removed setting mister_pulse_gap_s: no chart in s"). The address bar is left as it was; every link on the page starts from the cleaned view. A climate key in `m` is always drawn and drops silently |
| Window | A window of 400 local days can run 400 d 1 h across a net fall-back; each read then keeps its last 400 days. The lanes rule counts the window without the pad ahead, so a 31-day custom range that spans the fall-back shows the lanes message, as API-05 would refuse it. An ISO time whose `+` offset reached the link unencoded counts as `+`, and links write it as `%2B`; a day that does not exist is refused with the window's notice |
| Polling | The page clock is the current minute. A window that reaches now moves with it every 60 s (every second minute while a read fails) while the tab is visible, and shows its previous answer until the next minute's arrives; a custom or exact window that reaches now keeps its x range, so its band and setting history poll every 60 s instead. Reads are keyed by their exact request, so a closed window reads once |
| Zones | An unknown zone shows the grid with a notice. Each zone's current value shows in the cell's readout rather than the column head: "65.1 now" only while the window reaches now and the value is the newest bucket's, else "65.1 at 08:10". The first column's y axis sits in the row name's column, so every cell's plot is one width. The grid's data table has one block per metric row, a column per zone. On a phone the grid picks its one row through `usePhone`: the charts render only on the client, and the server's two-row loading frame collapses to one at hydration |
| Phone toolbar | Two rows at 860 px and below: Today, 24 h, 72 h, 7 d, 30 d and Custom… as a chip, then ‹ ›, Layers and + Metric. 90 d and Zoom out are left out; Preset, the settings drawn as steps with + Setting, and the picked metrics move into the Layers sheet. The status line takes its short wording ("24 h · in band 96% · 2 excursions ›", the count a link to the worst one) |
| Lanes in the stack | The equipment lanes draw on the panels' plot area (56 px from the left, 10 px from the right) with no time axis of their own, so the stack keeps one axis and one cursor. Each row's name and its state, on time and starts ("off · 80 min on · 1 start") sit on a 16 px line over its track; "N idle rows" is a button under the rows |
| Setting chips | A chip reads the setting's label and its panel ("VPD Mister Engage · VPD ×"), its key in the chip's title |
| Statistics | "Daily range" shows VPD as well as temperature, so the card matches the typical day's two charts; the typical day defaults to 30 days, with `typical=14` in the URL |
| Scorecard columns | The Data table is keyed by VD-56's registry keys (`daily.temp_in_band_pct`, `daily.vpd_in_band_pct`, `daily.time_in_band_day_pct`, `daily.time_in_band_night_pct`, `daily.water_gal`, `daily.iris_required_written`, `daily.iris_required_missed`, `daily.alerts_critical`, `_warning`, `_info`). A `daily` key API-03 lists beyond these follows as its own column; one it does not list reads "— not served"; a failed read "— couldn't load" with a Retry. The date cell carries the day's length when it is not 24 h ("Sun, Nov 1 · 25 h") and "partial" for today; the CSV writes the date as YYYY-MM-DD with its note in parentheses |
| Overlay reads | One API-04 request for `climate.outdoor_temp_f` and `climate.outdoor_rh_pct` over the measured window and one for the forecast keys over the part of the x range past now, each failing alone. Each key is asked for only if API-03 lists it |
| Outdoor and forecast | Outdoor traces in `--c-outdoor` over each panel's own buckets; outdoor VPD heads the CSV as `derived.outdoor_vpd_kpa` and reads "Outdoor VPD · DERIVED". The forecast draws from its own time array, 2 px dashed, a missing hour left open, with "Forecast · issued 07:00" in the notes; "+ Metric" does not list the forecast keys, so a forecast never draws left of now |
| Target | Dotted [2,4] in `--c-setpoint`, scaled by the device pixel ratio, with a note in the focused zone's notes |
| Chart types | `dashboard/app/charts/types.ts` re-exports the generated client's types (through `Lenient`), so a parsed response goes to a chart unchanged |

## 4. SCR-03 Plan · Control › Plan · `/plan`

**Purpose:** what Iris has told and will tell the controller, why and until when, whether Iris is healthy, and a
fast way to take any planned setting manual, in place.
**Stories:** US-PLN-01, US-PLN-02, US-PLN-03, US-SET-04, US-AGT-05.

The layout is the redesign's §5.11: the status line ("SUNRISE plan · written 07:02 · valid to 17:30 (6 h 12 min left)
· AI", or what applies with no plan), "Find a setting" in the header actions (a search that goes to `/settings?q=`),
Plan in force beside Planner health (stacked below 600 px), then Planned settings full width.

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Plan in force | Plan id; trigger (MIDNIGHT, SUNRISE, SOLAR_MAX, TRANSITION, SUNSET, WEEKLY, FORECAST_DEVIATION, MANUAL); a validity bar from written to valid until with now marked and the time left in hours and minutes; the hypothesis summary with AI; conditions, stress windows and per-setting rationale in a disclosure; what applies after expiry. With no plan in force: "Band and defaults apply since 05:40" | API-07 (`plan_journal`, `v_active_plan`) | 60 s |
| Planner health | The last 36 h of triggers as a row of status marks (quiet when done, amber when late, fault when failed or missed, an optional trigger shorter), with a legend and the words on hover and in the list; the next trigger; any required trigger that needs someone kept in view; "Last 14 days: 41 of 42 required plans" with a `StatusStrip` from `daily.iris_required_written` and `daily.iris_required_missed`; the ledger (`planner_trigger_ledger`: trigger, due, status in words and failure class) in a disclosure. A missed, failed or late required trigger (MIDNIGHT, SUNRISE, SUNSET) is marked here and turns Now's Iris phrase amber | API-07; API-04 `daily.iris_required_*` | 60 s |
| Planned settings | `PlanLanes`, unchanged in drawing, for the next 24 h (72 h on a chip, `?hours=72`), its caption ("Next 24 h · one lane per planned setting") a meta line under the card's title: one lane per planned setting with its device value at now (readback), Iris's waypoints dashed and labelled PLANNED with AI, the plan expiry with what takes over (most of a lane is band or default, as live plans reach a few hours ahead). A manual setting reads "MANUAL · Iris skips · Jason since 10/30" with no waypoints, and the head counts manual settings. A newer non-manual desired value reads "YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan" with the waypoints the API marks not effective muted as SUPERSEDED. A forecast rule's value reads "Forecast rule · system", never AI. Each lane's label opens its setting drawer here (`/plan?edit=<param>`), and the lane shows its newest write's one pill | API-07 | 60 s; 5 s while a lane's newest write reads REQUESTED |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Take a planned setting manual, or change it, from its lane's label | The setting drawer in place (`/plan?edit=<param>`, SCR-05), Manual ticked: API-10 or API-11 | S. The lane reads REQUESTED, then CONFIRMED; after a save with Manual the lane reads MANUAL and drops its waypoints; after Release to auto Iris's waypoints return on the next refresh |

| State | Plan shows |
|---|---|
| Loading | The two cards and the lanes as skeletons at their reserved heights |
| Empty | "No plan in force: band and defaults apply since 22:58"; lanes show device values only |
| Stale | Device values at now marked STALE; waypoints still draw |
| Not reporting | As stale, plus the banner; the plan still shows (it lives in the database) |
| Error | Each panel shows Retry; no earlier answer stays on screen |

Acceptance:
1. The card shows trigger, written at, the validity bar with its time left and the AI-labelled hypothesis; with no
   plan in force it names what applies and since when.
2. A manual setting's lane reads "MANUAL · Iris skips" and draws no waypoints; its label opens the setting drawer on
   Plan (`/plan?edit=<param>`), and closing it returns focus to the label. There is no "Open" link.
3. With a fixture plan written 07:02 (13:00 waypoint 1.70) and a non-manual desired row at 08:00 (1.55), the lane
   reads "holds until Iris's next plan" and the 13:00 waypoint is muted and labelled SUPERSEDED.
4. A missed SUNSET trigger in the fixture shows as a fault mark and "missed" with its failure class here, and turns
   Now's Iris phrase amber; the 14-day strip reads the required plans written and missed.

### 4.1 As built (VD-11)

The screen is `dashboard/app/routes/plan.tsx` with `components/plan-card.tsx`, `components/planner-health.tsx` and
`components/control/` (the validity bar, the trigger marks and the setting drawer) (#1131 and the redesign's WP4).
Where it settles a detail this section leaves open, or differs from it:

| Topic | As built |
|---|---|
| Reads | One API-07 read under its shared key, every 60 s (every 5 s while a lane's newest write reads REQUESTED) and paused while the tab is hidden; the window is in the URL (`?hours=72`, anything else 24 h). Device staleness is the shell's API-02 `freshness.readback.stale`. The time left, a trigger's lateness and the lanes' window count from when API-07 answered, on the server's clock (the shell's API-02 skew, as every age on the page), so a device clock running ahead never marks a trigger late early |
| Card | "iris-20261102-0702 · SUNRISE · written 07:02 · to 17:30 (6 h left)"; a time on another local day carries its date ("to Nov 5, 12:00 (71 h left)"), and under an hour the time left reads in minutes. The hypothesis is quoted with AI; with none it reads "— no hypothesis recorded" and carries no AI. Details lists Conditions (`conditions_summary`) and Expected outcome ("—" with "not recorded" when null), the stress windows of `detail.hypothesis_structured` (kind and severity in words, local times, mitigation), the per-setting rationale (`hypothesis_structured.rationale`: label, key, old → new, expected effect, evidence; also a climate intent that names its setting) and, under Waypoint reasons, each other `climate_intents` reason at its time. The intents' knob values are not listed |
| Names | An actor reads as API-07 sends it, the Authentik username: "YOUR VALUE 1.55 · jason 08:00 · holds until Iris's next plan". "Jason" in this doc is illustrative |
| Health | The marks are the last 36 h of triggers, each at its time on a scale from "−36 h" to "now" (as TimeTrack's ends read), a done mark outlined so it shows on white; a required trigger's mark is taller than an optional one's, which the key says ("short = optional"). The ledger, in its disclosure: newest first by `expected_at`. The ledger seeds a whole day's rows at midnight, so the expected rows ahead would fill "the five newest": the list shows the next row ahead (it reads "due"), the five newest that have fired and every marked required trigger, and "All triggers · N more" opens the rest, the later rows ahead included. The time shown is `due_at`, with its date on another day. A row still due or delivered after `due_at` adds "· late" (a due time that does not parse is never late); only a required one gets ▲ and the amber rule. `failure_class` reads raw: the ledger's classes are open-ended and the words table has no family for them yet |
| Lanes | `PlanLanes` gains `stale` (each device value muted with "STALE · read HH:MM" in its title, "STALE · device values last read HH:MM" over the rows, the device segment muted and labelled STALE; waypoints draw as before) and `rowEnd`, which Plan uses for the lane's link and its pill: the link lies over the lane's head row, where the label is printed (underlined, as a link), opens `/plan?edit=<param>` in place, and is named by the label; the lane's newest write shows its one pill at the head's end (REQUESTED, NOT CONFIRMED or HELD, CONFIRMED for 10 minutes). The plot keeps its full width. The plan lanes' labels are 6rem at every width. The head reads "1 setting MANUAL · Iris skips it" ("N settings … them") beside the 24 h and 72 h chips, and nothing when none is manual |
| States | One read feeds all three panels, so API-07 failing shows "Couldn't load …" with Retry in each; a poll that fails after an answer replaces it, so no earlier plan stays on screen. No panel shows an age line, so nothing green shows. The 14-day strip reads `daily.iris_required_*` on its own and fails alone with Retry |
| Fixtures | `no-plan` (no plan since 09:30, device values only), `manual-lane` (`mister_pulse_on_s`, manual by jason since 10/30) and `forecast-rule` (`mister_engage_delay_s` 90 s from 14:00, `preemptive`) join `superseded-lane` and `missed-sunset`. The browser flows at 1440 and 390 px (`e2e/plan.spec.ts`) come with #1128's harness |

## 5. SCR-04 Water · `/water`, `/water/schedules`, `/water/history`

**Purpose:** see and run all watering: what is watering and why, what runs next, what is stopping water, minutes per
zone, and every schedule, line and mister association.
**Stories:** US-WAT-01 to US-WAT-23, US-NOW-04, US-AGT-01, US-AGT-02 (agent writes show here), US-AGT-05.

Three views share one page header (redesign §5.2 to §5.4 and §6 draw them): Today (the default), Schedules and
History. The header's status line is the program state ("Program rev 42 on controller · confirmed 2 min ago",
`[data-program]`), then what runs (Today, each linked to its row; on a phone "L01 running ›", since the row can sit below the fold), the next run (Schedules) or the period's summary
(History). Its actions are Pause lines… on Today and "+ New schedule" on Schedules (operators; an outline, so the
emergency action is the one filled button), then **Stop all water**, the last action on every view for operators, as
redesign §2.5 places it; at 860 px and below it moves to the end of the tabs row, which sticks under the header, and
reads "■ Stop all" (both render and the stylesheet shows one, sharing one Stop).

| Panel | View | Shows | Data | Refresh |
|---|---|---|---|---|
| Program state | all | The status line: "Program rev 42 on controller · confirmed 2 min ago"; REQUESTED while a push is pending ("rev 43 sending"); "Not confirmed: <reject in words>"; with `src=rom`, "Running the built-in program: L04 VPD only, L01 run-now, no schedules"; before the irrigation firmware, "No irrigation engine on the controller yet: relay states only; L01 runs, and every line stops, through the bridge" | API-IRR-01 | 15 s; 5 s while REQUESTED |
| Zone cards | Today | In a fixed order that never changes with state: Wall (L01, L02 with L03 as its sub-row), Center (L04, L05, L06), South (L07, L08), West (L09, L10), then Fog (L11, climate only). Each head has the zone's name and kind, minutes today against the cap line's cap as a `Meter` (planned minutes dashed), the VPD reserve and when it last watered ("running now" while one of its lines runs; once today's minutes are above 0, never an earlier day's time). A mister zone (center, south, west) adds "VPD control" and "Schedule" switches with the VPD source (own probe, peer probes, house average), a watchdog back-off or latch with Clear fault, and "Will not mist automatically" when both are off | API-IRR-01; API-IRR-02 for the planned minutes | 15 s; 60 s |
| Line rows | Today | An `ObjectRow` per line (`article[data-line][data-state]`, named by its h3 "L01 Wall drip"): CLEAN or FERT, one state sentence ("Idle · last 07:31, 12 min · ok", "Paused until Sat 09:00 · VPD misting continues", "Faulted: no misting until midnight"), one sync pill (D-UX-07), a today strip (00–24 h: runs done, planned and skipped, the valve's open time, night and the now rule) with "next 16:00 · 10 min", one primary action by state (Run… while idle and runnable), its Stop only while it runs or its relay reads unknown, filled red (D-UX-06 as changed on 2026-11-06: no Stop on an idle row; Stop all water in the header) and ⋯ (the Enabled switch with its check sheet, Options…, Pause until…, "Activity for L01 ›", "Runs of L01 ›", "Graphs ›"). L05 and L06 read "Reserved" with disabled until a person enables them; L11 Fog reads "Climate only" with no controls | API-IRR-01; API-IRR-02; API-05 for today's valve time | 15 s (5 s while a command is in flight); 60 s |
| Site today | Today | SAF-4 gallons against 360 and the soft budget as meters, both `--eq-water` (SAF-4 links to its setting and reads "daily mist and fog ceiling" under its meter, the words Now's water card shows), the flow state in words, occupancy in the Now row's words, and "VPD fallback active" while the `degraded_only` slots run. In the aside from 1200 px, sticky; first on phones, where it holds SAF-4, the flow and occupancy (the soft budget once reached, fallback misting while on), so Wall and L01 stay above the fold | API-IRR-01 | 15 s |
| Week strip | Schedules | Monday to Sunday of the selected week, ‹ › by a week: past days' outcomes ("5 ran ok", "1 missed") with a strip level, today ("2 ok, 3 next", outlined as partial), future counts ("6 next"), and apart from the runs the fallback windows still ahead ("3 fallback windows"; the today strips and the status line count them the same way, "nothing planned · 2 fallback windows"); each day selects `day=` | API-IRR-02 with `past_days=7` | 60 s; 5 s while a schedule settles |
| Day timeline | Schedules | The default on desktop: one local day, 00–24 (25 h on the fall-back day), ‹ Today › across the 7 days before and the 6 after. Rows by manifold, each line's blocks as long as their schedules (done solid in the cause's colour, planned dashed, running solid to now and dashed after, skipped or blocked hatched with the reason, missed a fault outline, unknown a muted hatch, a `degraded_only` fallback window a dotted `--eq-heat1` outline reading "11:47 fallback · only if VPD sensing is degraded", or "not needed" once it passed without running), night shaded, the now rule, past valve time as ticks, a pause as a hatched band with who paused it, and each row ending with minutes against the cap and [+]. A block opens its popover (`?block=`); an empty slot opens the editor prefilled with the line, the day and the time rounded to 15 minutes (operators) | API-IRR-02; API-05 for the day shown, up to today | 60 s |
| Week matrix, agenda | Schedules | `view=week` (desktop): lines down, days across, one mark per occurrence and the minutes against the cap, each cell opening its day; in a labelled scroll region below 1100 px. The agenda (the default on phones, `view=agenda` on desktop): by day from the selected one for 3 days, NEXT on the next planned run, skipped rows hatched with their reason, "Earlier today" folded past noon. A Key popover explains the marks | API-IRR-02 | 60 s |
| Rules list | Schedules | "Schedules by line" with "14 of 24 schedules" and a meter: per line, every schedule including disabled ones (muted), each with its days ("Daily", "Weekdays"), start in words with today's time (an offset with its unit, "sunrise + 1 h (07:35 today)"), amount, MANUAL or "Iris may adjust", FERT, the degraded-only note, one sync pill, "changed by" with when and why, its enabled switch (reading ON or OFF, "Enabled" its accessible name alone), Edit and ⋯ (Duplicate, Delete); a line's two or more `degraded_only` schedules as one "Fallback (3)" row with their starts as one list ("solar noon −2 h, 0, +2 h"), opening to each schedule's own row; "+ Schedule" per line. On phones each line folds | API-IRR-02 | 60 s |
| Key figures | History | One period for the page (`range=7d\|30d\|90d\|365d`) with Group by (zone, line, cause) and Measure (minutes, gallons): watering time, gallons, runs, and missed, short or cap, each against the previous equal period with a sparkline | API-IRR-10 (this period and the one before), API-IRR-11 (runs, counted up to 31 days) | 60 s |
| Minutes or gallons by group | History | Daily bars (`MinutesByZone`) for the period by zone, line or cause, fog apart as humidification, clean below fertilizer, cap ticks, today partial and minutes still planned dashed, days with no data hatched; "CSV of this chart" | API-IRR-10, API-IRR-02 | 60 s |
| Daily water use | History | Meter gallons per day split by line or cause (`water_use_by=`), a 1 px background gap between parts, the water that ran while no valve was open apart in neutral `--muted` (hose and sink use is no fault; a leak is the unexplained-flow alert's to say), a no-data day a faint hatch behind the grid; the unit in its section meta ("gallons · last 30 days"); 30 days for periods up to 30 days and 90 above, which its section meta says; its CSV | API-21 | 60 s |
| Runs | History | A table with a sticky head (stacked rows on phones): line, zone, CLEAN or FERT, cause, schedule, actor, start, end, minutes (pulses for mist), whole gallons from the site meter (±1 gal; it also counts the hose, sink and hydro; Q-10), outcome and reason; filters for line, cause, outcome and dates applied on change; 31 days a page with Load older; "CSV of these runs"; each run's "Graphs ›" | API-IRR-11 | 60 s |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Stop all water; Stop on a line | API-IRR-07 `{}` or `{line_id}` | C, then the Stop result (§1.4). One tap, no dialog, never disabled by the writes flag; OFF from relay edges within 30 s; lines and schedules stay as they are |
| Run… (drip `{minutes}`; FERT drip, L02 and L06, `{minutes, prewet_s, flush_s}`; FERT mister, L08 and L10, `{on_s, count, gap_s, prewet_s, flush_s}` within the mister bounds; a FERT dialog's title carries FERT, and the run prewets on the clean partner, feeds through L03, then flushes; Mist now `{on_s, count, gap_s}`, shown only with Schedule on) | API-IRR-06 | The one confirm dialog ([design system](design-system.md) §4.4), then C. It starts at once or is refused in words, and nothing queues: `line_busy`, `manifold_busy` ("L01 holds the drip manifold until 07:42; stop it or try then"), `cap_reached`, `line_disabled`, `line_paused`, `line_faulted`, `not_schedule_member` |
| Enable or disable a line (⋯) | API-IRR-08 `{enabled, checked, check_note}` | P. A line with no recorded check asks once for the "Checked" box (who and when are recorded; an optional note of up to 500 characters); disabling stops everything on it, VPD misting included |
| VPD control and Schedule switches (L04, L07, L09, in the zone head) | API-IRR-08 `{vpd_member, schedule_member}` | P per switch. Schedule on for a line with no check asks for the "Checked" box (nozzles, coverage, walkway) |
| Line options… (⋯): daily cap, VPD reserve, Manual | API-IRR-08 | P |
| Pause lines… (header), Pause until… (⋯), "Pause L01 until after this run" (block popover); Resume | API-IRR-08 `{paused_until}` on one line or `all`; Resume sets it to now | P. Rows read "Paused until 14:00 · VPD misting continues" (turn VPD control off, or disable the line, to stop it). Pausing a line until after a planned block skips that one run |
| New, edit, enable, duplicate or delete a schedule | API-IRR-03, API-IRR-04, API-IRR-05 | P |
| SAF-4 ceiling, scheduled-mist RH ceiling, climate yield | API-10 on `mister_daily_volume_max_gal`, `sched_mist_rh_max_pct`, `climate_yield_min`, in the Settings drawer | S (state from API-09, confirmed by the program hash) |
| Clear a mister fault latch | API-IRR-13 `{line_id}` or `{}` | C. The row returns to idle or running; Activity records who cleared it and why |

Editor fields follow [irrigation](irrigation.md) §4.3 and redesign §6.5: line and label; days (weekday chips with
"Daily" and "Weekdays"); a clock start or sunrise, solar noon or sunset ± 0-240 min (resolved for today beside it);
minutes (drips, fertilizer drips included); `on_s` 10-90 s, `count` 1-60 and `gap_s` 30-600 s (misters, fertilizer
misters included); prewet and flush on the clean partner (fertilizer); degraded-only (misters); the one-time check when
the line needs it; the Manual box, ticked; Reason. The editor opens from "+ New schedule", "+ Schedule" on a line, an
empty slot, Duplicate or Edit, prefilled accordingly. While it is open its draft draws dashed on the timeline of the
next day it runs, and its preview reads, before Save, the day totals against the cap ("Wed would reach 72 of 60 min:
runs past the cap end at the cap"), `too_close` ("Starts 20 min after Wall MWF on L01: schedules on one line need
30 min"), the 24-schedule limit and manifold waits. Save stays enabled: the server decides, and a 422 keeps the drawer
open with the message beside its field (`out_of_range`, `too_close`, `too_many_schedules`, `unknown_target`). A
schedule on a disabled line, or a mist schedule with Schedule off, saves, and its blocks read "Skipped: line disabled"
or "Skipped until Schedule is on".

| State | Water shows |
|---|---|
| Loading | The status line keeps its height; the zone cards, Site today, the strip, the timeline and the tables load as skeletons at their reserved heights |
| Empty | "No schedules yet. Lines water only from VPD control and Run now." with "+ New schedule"; "No runs in this window" with a wider period |
| Stale | Line states follow §1.5: known while the controller reports, "unknown · 3 min ago" only while it does not; relay edges still show |
| Not reporting | "Controller not reporting since 03:12. If it has power, it keeps running rev 42, VPD misting and caps"; saves store and push when data returns |
| Error | Retry per panel; a failed status read says "Water status unavailable", never idle. Before API-05 answers, or when it fails, a day that had valve time reads "valve time unknown", never "no runs and no valve time" |
| Before the irrigation firmware | §1.5. L01 has Run now through the bridge: it runs for the Wall drip duration setting (`irrig_wall_duration_min`) and the dialog says so. Every line has Stop, and Stop all water works, through the same bridge. The other lines' Run and Clear fault read "arrives with the irrigation firmware" |
| Flow, occupied | Flow: the fault banner and "Water flowing with every Verdify valve closed since 03:02 · 3 gal in 10 min". It is an alert only (Q-05): the irrigation firmware stops no drip, mister or fog for it; until then the current firmware holds them off while the flag is up. Occupied: mister rows "Waiting: someone inside (Frigate, seen 3 min ago)"; unknown occupancy never waits |
| Not confirmed | The status line and the item show the reject in words and "retrying at 1, 5 and 15 min" |

Acceptance:
1. Today shows every line L01-L11 inside its zone card in register order, each an article named "L0n …" with one
   state sentence, a today strip and its next run; L05 and L06 read "Reserved" with disabled; L11 reads "Climate only"
   with no controls; the center zone's head holds VPD control and Schedule with the VPD source.
2. Creating an L01 schedule (Mon, Wed, Fri, sunrise + 60, 12 min) draws dashed on the timeline against the 60 min cap,
   then reads REQUESTED, then CONFIRMED when the fixture's `irr_program_id` reports the new hash; Activity lists it
   under the operator's name.
3. The day timeline shows each occurrence at its time and length grouped by manifold, with night shaded and the now
   rule; a past skipped block is hatched with its reason; `view=week` shows the matrix (`.week-table` with
   `tr[data-line] td[data-day]`); at 390 px the agenda is the default and `view=day` fits with no sideways scroll.
4. Stop all water appears on all three views for operators, sends one request with no dialog and stays enabled with
   changes switched off; only a running line has a filled Stop, idle lines a quiet one that still works; before the
   irrigation firmware every line shows Stop and only L01 shows Run; viewers see no Stop, Run, switch or editor.
5. A run refused with `manifold_busy` shows the reason in words and changes nothing; a double tap on the dialog's Run
   sends one `Idempotency-Key`. A fixture mister latch reads "Faulted: no misting until midnight (watchdog)", and
   Clear fault sends one API-IRR-13 request.
6. History's one period drives the page; minutes by zone draws a day with no data as a hatched gap, never 0, and
   marks today partial; runs, minutes and water use each export CSV; south reads "VPD from peer probes" while its
   probe is out.

As built (VD-15, VD-16, VD-17, VD-20, VD-69 and the redesign's WP3). Where the build settles a detail this section
leaves open, or differs from it:

| Topic | As built |
|---|---|
| Files | `dashboard/app/routes/water.tsx` (the layout: the header, the drawers and the run dialog), `water-board.tsx` (Today), `water-schedules.tsx` and `water-history.tsx`; `components/water/` (zone card, today strip, Site today, the status line), `components/schedule/` (the day model, timeline, week strip, week matrix, agenda, rules list, block popover), `line-card.tsx` (the line row), `schedule-editor.tsx`, `line-enable.tsx`, `check-sheet.tsx`, `line-options.tsx`, `pause-lines.tsx`, `run-dialog.tsx`, `stop-button.tsx`; `lib/water.ts`, `lib/line-checks.ts` and `lib/schedule-preview.ts` |
| One status read | The layout polls API-IRR-01 once for all three views (every 15 s, 5 s while an item settles with the engine on), under the key Now's water card and `useStop` share. The check sheet, line options, the pause drawer and the Run dialog open there from the URL, so a failed read that replaces a view with its error leaves an open drawer or dialog and what was typed in it |
| Drawers and popovers in the URL | `?check=<line_id>`, `?options=<line_id>`, `?pause=<line_id or all>`, `?run=<line_id>`, `?block=<slot>-<epoch>` and `?schedule=new\|<slot_id>` (with its prefill `line`, `at` and `copy`): opening pushes and closing replaces, so Back closes them. Schedule pressed on a disabled mister with no check adds `&for=schedule`. A viewer gets no drawer, dialog, editor, [+], switch, Edit or pause from any link; a link to L03, L11, a line with nothing to ask or a stray id opens nothing. Each drawer closes on a 200 with a toast ("Requested: L04 daily cap 130 min, 60 min kept for VPD") |
| A row's sync pill | `lines[].sync` is the row's one pill (redesign §4.4): REQUESTED with "rev 43 sending", NOT CONFIRMED with the reject in words and "retrying at 1, 5 and 15 min", and CONFIRMED only within 10 minutes of the newest write. Before the engine every line reads REQUESTED with "Applies with the irrigation firmware", because `sync` is always `requested` then |
| A switch's own badge | `sync` is per line, so the client works out which mister switch shows it. While it settles, the switch pressed in this tab carries it, or both when none was pressed here (another writer, a reload). A press counts only while the line reads what it asked for, so a cancelled check sheet or a refused write carries none |
| Stop | Only on a row whose relay is on (it runs) or reads unknown, fog included, filled red (`.btn-stop`), one tap (D-UX-06 as Jason changed it on 2026-11-06: no Stop on an idle row; Stop all water stops everything, a relay on outside its program included). While it confirms, the row reads "Waiting for the valve to read off" and API-IRR-01 is read every 5 s. The lines Stop must see go OFF are those running in the newest API-02 answer or in API-IRR-01, so a Stop right after a Run still confirms and offers Disable |
| Row controls | At most two buttons plus ⋯: Run… while idle and runnable, and Stop. The menu holds the Enabled switch (its check sheet when the line needs one), Options…, Pause until…, "Activity for L01 ›", "Runs of L01 ›" and "Graphs ›". L03's sub-row under L02 drops the check words it has no check of its own for |
| Run dialog | Every dialog also has "Reason (optional)", sent only when typed. A cap of 0 reads "wall feed today 0 min, no cap". "Mist now…" opens the same dialog, titled "Run L04 Center mister (clean) for 4 × 45 s?". `manifold_busy` reads the API's `detail`, gaining "until HH:MM" from `retry_after_s` if it has no end time; every other code reads the words table. After the 202 the row reads "Waiting for the valve to open" until the relay edge shows RUNNING, or within 30 s the line's reason or "Not started" |
| A feed's start | A fertilizer run counts as started once its clean partner reads `running` with cause `prewet`, because its own relay opens only after the prewet |
| Pause words | "Paused until 14:00 · VPD misting continues · by jason: rain". The VPD clause shows only on a line with `vpd_member` on; who and why come last. Fog never reads paused, though `all` sets `paused_until` on it. A pause stored before the engine reads too, beside its REQUESTED |
| Pause drawer | "Pause lines…" is Today's header action. Until is an America/Denver wall clock in any browser zone, starts on the next whole hour at least 30 minutes ahead, and must be ahead of now. Resume sends now on the server's clock. "Resume all" shows while any line is paused, L03 and fog included |
| Block popover | A block opens its popover (a sheet on phones): the line and schedule, the day and time with the start in words, the outcome or the next times, the rule, Edit (operators, any block whose schedule is stored) and, on a block still planned ahead, "Pause L0n until after this run", which skips that run and any earlier one on the line; "Runs of L0n ›" and "Graphs around this run ›". Escape and Back close it. Edit opens the editor over it, so closing the editor comes back to the popover |
| Day strips and timeline | A running block is solid to now and dashed after. Each track isolates its stacking, so the now rule never rides over the sticky tabs. Before API-05 answers (or when it fails) for a day that has had valve time, the strip reads "valve time unknown" and the ticks' third is a muted hatch up to now, never empty. A day ahead has no valve time yet, so it reads today's window |
| Editor | The backdrop dims less, and from 1200 px the page narrows beside the drawer, so the timeline and the draft stay readable. A new schedule starts on L01, today's weekday, sunrise + 0 and the run dialog's amounts, unless a slot, a line or Duplicate prefills it. A drip's Run and a feed's Feed take whole minutes; a stored `on_s` that is not one opens as "11.5" and, left as it is, saves unchanged. The draft replaces the schedule's blocks still ahead; a block already past stays as it ran. A disabled draft reads "Disabled: this schedule will not run". Before the engine a draft reads hatched "arrives with the irrigation firmware" and still counts against the cap. A check recorded by a Save whose schedule was then refused stays recorded. A slot that is not stored opens the drawer with "This schedule no longer exists" and Done. Save toast: "Requested Wall MWF on L01", "Requested deleting Wall MWF on L01" |
| Line options | The reserve's bound follows the typed cap, and a reserve above it is flagged in the hint and sent, so the API's `out_of_range` shows beside the field. The client refuses only what is not whole minutes from 0 to 480 |
| History | KPIs compare with the previous equal period. Runs are counted only up to 31 days ("Counted over 31 days at most"); the ledger loads 31 days a page across the period with Load older. Daily water use states its own window (30 days for periods up to 30, 90 above, "the shortest" or "the longest this chart serves"); `water_use_by=cause` splits it by cause. Each day's stack sits left of its tick and the no-valve bar right of it; the summary reads "Last 30 days: 612 gal · 40 gal with no Verdify valve open · 1 day with no data" |
| Before the irrigation firmware | Operators read "Run arrives with the irrigation firmware" on the other lines and "Clear fault arrives with the irrigation firmware" on L04, L07 and L09. Viewers read neither, as they have no such control |

## 6. SCR-05 Settings · Control › Settings · `/settings`

**Purpose:** "control all settings": every registry row in one list with its device value, and one change path for
every writable one.
**Stories:** US-SET-01, US-SET-02, US-SET-03, US-SET-04, US-SET-05, US-SET-06, US-SET-07, US-WAT-19 (site caps),
US-AGT-05, US-AGT-07.

The layout is the redesign's §5.10: the header with the search in its actions and the status line ("261 settings · 2
requested · 0 not confirmed · 5 manual"), then a sticky rail beside the catalog (`l-rail`). At 860 px and below the
rail becomes two selects above the catalog (Show, and "Jump to group"), and the rows stack.

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Rail | Show: All, Recently changed (a desired write in the last 7 days; left out when API-09 serves no write time), Requested, Not confirmed, Manual, Changed by an agent and Read-only, each with its count as a pill, the current one with a forest rule (`filter=`, keeping `?edit=`); Groups: an anchor per group with the rows it holds under the filter and search as a plain muted number, the API's `read_only` group named "Reference only" (retired, legacy, readback-only and derived rows), so it never shares the Read-only filter's name | API-09 | 60 s |
| Catalog | One table, one `tbody` per group under a sticky group head, in a fixed order with no column sort: planner policy, scheduled policy, controller safety and crop band (anchors and widths edit through the same drawer), "Site water caps" for the three irrigation site values, the read-only "Band in force" (API-09 `group` `band_in_force`, VD-55: the house and per-zone band the controller runs, DERIVED with its age), then read-only rows with their reason: the legacy wide limits (`temp_low`, `temp_high`, `vpd_low`, `vpd_high`, `vpd_target_*`: "legacy limit, read-only: not the band while the on-chip band runs"), values the dispatcher derives (the activity mirror), the old commissioning rows ("retiring with the irrigation engine"), readback context and retired. No card frames the table | API-09 | 60 s; 5 s while a row is REQUESTED |
| Row | Two lines. Line 1: the label and key (mono, muted), the device value at `--fs-lg` with its unit, read as Plan's lanes read it (`settingDisplay`: the step's decimals; the unit its key names where the registry has none, `_s`, `_ms`, `_min`, `_pct` or `_f`, never for an `_hour` or `_minute` time of day; a long duration in a unit people read, "300000 ms" as "5 min", which the drawer keeps as stored), a bounds bar (the allowed range, the value as a dot, the next plan as a hollow ring, a pending request as a dashed amber ring), one pill by the priority of [design system](design-system.md) §4.1 (NOT CONFIRMED, REQUESTED, HELD, STALE, FAULT, MANUAL, then CONFIRMED only while the newest write is under 10 minutes old), and Edit (operators) or Details. Line 2, the meta line: who wrote the value in force ("jason · manual · 'Vandas drying' · 06:47", or "Iris changed this at 07:02 · AI", "Forecast rule · system"); the next plan ("next 1.70 at 13:00 · AI", or "superseded until Iris's next plan" while a newer non-manual value holds); while pending, the request and, once late, its reason: "Clamped to 2.00 kPa: <guardrail>", "Sent 06:02; waiting: controller heap low", "Sent 06:02; push failed: <status>", "Held by experiment <id>: applies when it ends", "Ingestor not running since 03:12", "Controller not reporting since 03:12", else "Sent 06:02; no matching readback yet". AI, DERIVED and NO READBACK are text marks. Iris's values show as stored, unsnapped. A site cap's device value is the one in the program on the controller ("On controller · rev 42: 360 gal"); before the irrigation firmware a site cap still saves and reads REQUESTED with "applies with the irrigation firmware" (§1.4 S). On phones the row's link covers it | API-09 | as above |
| Drawer `?edit=<param>` | `components/control/setting-drawer.tsx`, opened in place on Settings, Plan and Equipment. Top: the device value, its pill, the bounds bar and the request, writer and plan lines. Then the field (NumberField, Switch or Select), the band preview for a crop-band anchor or width, Manual (ticked), Reason, Release to auto, Cancel and Save. Then "Change history" (the chart and its entries in words, "1.60 → 1.55 · jason · manual · 'Vandas drying' · Confirmed 06:48", with Revert) and "Activity for this setting ›", plus "Graph this setting ›" when its unit has a Graphs panel. Escape and × close without asking, and focus returns to the opener | API-09 (read only while a drawer is asked for on Plan and Equipment); API-12; API-19 | on open |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Save a value (Manual ticked by default; reason optional) | API-10 `{value, manual, reason}` with an `Idempotency-Key` | S. Save always sends manual |
| Turn Manual on or off without a new value | API-10 with the current value, stored as is (an Iris value stays unsnapped) | S |
| Release to auto | API-11 `?reason=` | S. MANUAL drops; the row reads "Released: plan, band or default applies", then shows the readback |
| Revert (Change history) | API-10 with the previous value, or API-11 | S |

| State | Settings shows |
|---|---|
| Loading | Skeleton rows per group at their reserved height; the rail's counts read "—" |
| Empty | One line and one action: "No setting matches 'pulse gap x'" with Clear search, or a filter's own words ("No setting is manual") with Show all |
| Stale | The device value muted with STALE and its age, and a note over the catalog ("Device values are stale: 14 min ago"); a REQUESTED row stays REQUESTED |
| Not reporting | Save still works; while the ingestor or controller is down the drawer says so before Save: "Save still works: the change applies when the controller reports again" |
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
   reads "superseded until Iris's next plan" in the row's meta line.
5. With the fixture's program at rev 42, `mister_daily_volume_max_gal` reads "On controller · rev 42: 360 gal";
   before the irrigation firmware, saving it reads REQUESTED with "applies with the irrigation firmware".
6. Each row shows at most one pill; CONFIRMED shows only within 10 minutes of the newest write; the rail's Show
   filters carry counts and live in the URL with `?edit=`; at 360 px the rail is two selects and nothing scrolls
   sideways, the drawer included.

### 6.1 As built: history, Revert and the band preview (VD-19)

The drawer's depth is `dashboard/app/components/setting-history.tsx`, `band-preview.tsx` and `lib/revert.ts` (#1139). Where
it settles a detail this section leaves open, or differs from it:

| Topic | As built |
|---|---|
| Place | Band preview under the value field, inside the fields. History and Revert under Reason, outside the fieldset the read-only state disables (`EditDrawer`'s `below`), so a viewer keeps Retry and Reset zoom. A Band in force row is derived and has no history |
| Reads | API-12 and API-19 are not polled and not cached past the drawer, so a drawer opened after a write never shows the answer from before it. API-12 reads again if the setting's newest desired row changes while the drawer is open; Revert is disabled until that answer arrives |
| Chart | A `TimeSeries` with no metric: its `SettingSteps` take `allRequests` (every desired row is a request, up to the next row or now) and `format` (values as stored, a switch ON or OFF). Canvas labels read "Jason · dashboard", "steward · agent · AI", "iris · AI", as a canvas draws no badge. The legend is a key of each source present, the plan and the bounds; the data table lists the steps with who asked, the row's state and its reason; the CSV has one row at each step's start and end inside the window, with who asked beside each request. A crowded chart drops labels by the order in design system §5.5, and a bound outside the y range is an edge label as on Graphs, so the key and the table keep the words. A window chip starts its chart on that window, as a Graphs range does, so a zoom ends with it |
| Device line | Readings that print alike are one step, as the ingestor's 0.83 and the snapshot's 0.8299999833 alternate in `setpoint_snapshot` (`mister_engage_kpa` has about 7,500 change points in 90 days, half of them this). Only the newest device step is labelled, as a tuned setting moves dozens of times a day; the tooltip and the table carry the rest. Graphs keeps every change point |
| Revert reason | Its own "Revert reason" field beside the button, prefilled and editable; the drawer's Reason stays Save's. Enter in it presses nothing (the drawer is one form) |
| After Revert | As after a Save: the drawer closes, the toast names the value, and the row reads REQUESTED, then CONFIRMED. A refusal stays open with its words beside the button. Revert has its own write hook, so a refusal never lands beside the value field; one write goes at a time, as Save and Release wait for a Revert that is out and it waits for them |
| A crop-band row with no earlier value | API-11 appends a release and leaves `crop_band_anchors` as the last write set it, so a release would not bring the previous band back. Where a crop-band anchor or width's previous row is a release or missing, Revert writes the value the device reported just before the newest row through API-10, without its binary32 noise: "Revert to 1.10 kPa (device, 10/31 07:00)". With no such reading, or with the device already on the newest row's value (that write changed only Manual), it reads "Revert to auto". Every other setting releases as this section says |
| Nothing to go back to | A setting whose newest row is a release with no value before it is already on auto, so it has no Revert, as with no desired row at all |
| Preview | On opening the panel reads the value in force, so the band as stored shows before any edit, and a draft that changes nothing draws no PREVIEW. The draft is dashed `--c-requested`, labelled PREVIEW where it stands furthest from the target. The solar times also read under the plot, as a crowded plot may drop a mark's label. The tooltip, a data table and a CSV follow the rest of the charts. A field that is not a number reads nothing |
| Fixtures | `setting-history`, `setting-history-release` and `band-preview-center` as the issue names them, plus `setting-history-older` (the earlier row lies before the 7 d window, so Revert reads 90 days back). API-12 now serves `desiredLog` and `readbackLog` with a write appended, and API-19's `current` follows API-10, never API-11 |

## 7. SCR-06 Activity · `/activity`, `/activity/log`

**Purpose:** what needs someone, and who changed what: every write by a person, Iris, another agent or the system in
one log.
**Stories:** US-ACT-01, US-ACT-02, US-ACT-03, US-ACT-04, US-AGT-03, US-WAT-05, US-WAT-18, US-WAT-22.

Two views (redesign §5.8 and §5.9, D-UX-03): Alerts (`/activity`, the default) and Log (`/activity/log`). Tab labels
never carry counts; the Alerts status line states them once ("2 open · 1 acknowledged, still open · 3 resolved in
24 h").

| Panel | View | Shows | Data | Refresh |
|---|---|---|---|---|
| Needs action | Alerts | `v_open_alerts` rows nobody has acknowledged, by severity (critical, warning, info) then age, each an `ObjectRow` with its severity rule and word: the plain title from the one words table (`leak_detected` reads "Water flowing with every Verdify valve closed", with "a leak, or the hose, sink or hydro in use" beneath), zone or line, age, and a repeat count with first and last seen ("x37 since Mon · last 06:02"; repeats of one type and sensor merge into one row, and a long-offline probe's alert resolves itself, VD-72); Acknowledge with an optional note; "Details ›". It is not drawn while nothing is unacknowledged, and the status line then says so | API-14 `status=recent` | 15 s |
| Acknowledged, still open (N); Resolved in the last 24 h (N) | Alerts | Collapsed groups, each opening when it holds the expanded alert (`?resolved=24h` opens the resolved one); an alert acknowledged on the page moves into the acknowledged group, which opens to show who and when | API-14 | 15 s |
| An alert, expanded | Alerts | In place at `?alert=<id>` (opening pushes, closing replaces): every field, the runbook from `slack_alert_runbooks` when one exists, "Open in Graphs ›" (±2 h around the first seen), and the note that Acknowledge and Resolve send; Stop all water on an `irrigation_fence_breach` (with the valve that opened), Clear fault on an `irrigation_line_faulted` latch; the flow alert's live lines and spigot words, with no Stop | API-14, API-02 `leak`, API-IRR-01 | 15 s |
| Recent activity | Alerts | The five newest log entries, one line each under the log's day headers (Today, Yesterday, Fri 30 Oct), so each time is HH:MM, and "Open the log ›" | API-17 `limit=5`, API-09 | 60 s |
| Filters | Log | Who (People `source=dashboard`, Iris, Agents, System) and What (Settings, Water, Alerts, Plan, Equipment) as chips from 861 px and selects below, a Target field and From and To dates, each applied on change and kept in the URL (`source`, `action`, `target`, `from`, `to`; an old `actor` filter shows as a chip that clears it); "Clear filters" | — | — |
| Log | Log | Entries newest first under sticky day headers (Today, Yesterday, Mon 3 Nov), one line each: time, actor and source ("Jason · dashboard", "iris" with AI, "steward · agent" with AI, "forecast-engine · system"), the action and object, before → after with "manual" or "manual cleared", the reason, and the outcome. An Iris plan is one entry with "N changes" that expands to them; a change made while an experiment is armed carries the experiment's id; an automatic entry names its cause. "N entries loaded" and "CSV of these entries" (the loaded rows) head the list; Load older (cursor) ends it | API-17, API-09 | 60 s |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Acknowledge, optional note | API-15 | R, plus an `alert.ack` log entry |
| Resolve, optional note (up to 500 characters) | API-16 | R. Never blocked; an alert whose condition persists re-opens on the next monitor cycle |
| Stop all water, on `irrigation_fence_breach` | API-IRR-07 | C, then the Stop result (§1.4). A flow alert offers no Stop: no Verdify valve is open |
| Clear fault, on `irrigation_line_faulted` | API-IRR-13 `{line_id}` | C |

| State | Activity shows |
|---|---|
| Loading | Skeleton cards at their reserved heights; the status line keeps its height |
| Empty | "No open alerts" per §1.5; "No activity matches these filters" with Clear filters |
| Stale | Alert values carry their age ("3 gal in 10 min · 40 s ago") |
| Not reporting | Alerts and the log still load (they live in the database); the status line reads "In-app alerts may be missing: ingestor not running since 03:12" or "… no data since 03:12" (§1.5); the banner shows |
| Error | "Alerts unavailable." with Retry, never "No open alerts"; the log and Recent activity fail on their own |

Slack keeps posting alerts exactly as it does today.

Acceptance:
1. With API-14 failing, Needs action reads "Alerts unavailable." and Recent activity still loads.
2. Acknowledge with a note shows "Acknowledged by Jason · 06:47", moves the alert under "Acknowledged, still open",
   lowers the nav count by one and adds an `alert.ack` entry.
3. A dashboard write, an Iris plan, an agent write and a forecast-rule write show "Jason · dashboard", "iris" with AI,
   "steward · agent" with AI and "forecast-engine · system", each with before → after and its reason; the Iris plan
   reads as one entry with N changes.
4. The fixture flow alert reads "Water flowing with every Verdify valve closed", expands at `/activity?alert=<id>` to
   its runbook and "Open in Graphs", and offers no Stop.
5. `/activity?target=L01` lands on `/activity/log?target=L01`, which lists only L01 entries; `/activity?alert=7` stays
   on Alerts.
6. The log's facets apply on change and live in the URL, its day headers stick, and "CSV of these entries" downloads
   the loaded rows.

As built (VD-13 and the redesign's WP1, `dashboard/app/routes/activity.tsx`, `activity-log.tsx` with
`app/lib/activity.ts`), where it settles a detail this section leaves open:

| Topic | As built |
|---|---|
| Names | The API names people by Authentik username (`Actor.name`, `acknowledged_by`, `resolved_by`). The signed-in person reads by their API-01 display name ("Jason · dashboard", "Acknowledged by Jason · 06:47"), anyone else by username |
| Log entries | One line: time, who ("Jason · dashboard", "forecast-engine · system": the attribution words, as the schedule editor's history reads them), the action in words with its target and the change ("Set VPD Mister Engage 1.60 → 1.55 kPa · manual · "Vandas drying""), with "requested 1.53" only when the server snapped the value, "manual cleared", a plan's rule by name or Iris's trigger, a run's length, "during experiment <id>"; an automatic entry starts with its cause. Setting labels and units are API-09's as written, so the page reads API-09 every 60 s; a param it lacks reads raw; an action the words table lacks reads raw |
| What facet | API-17 takes a single action prefix, so "Equipment" is `irrigation.override` (bursts and Restart); grow-light writes (`light.*`) appear only under All, and Water (`irrigation.`) includes the overrides |
| Day headers | "Today", "Yesterday", then "Mon 3 Nov" (en-GB), as the redesign's mock writes them |
| Alert rows | Each row expands and collapses with its own "Details ›" or "Hide details" link, which keeps the scroll position. Acknowledge and Resolve each leave a toast ("“Relay stuck” acknowledged by Jason · 10:42"). A closed alert reads "Resolved by Jason · 10:42" with its resolution. Expanded, every code reads through the words table, the type as the title does. An open flow alert says its live lines once (the gallons or "Flow stopped at HH:MM"); its message, the ingestor's words for the same facts, shows only among the fields |
| Needs action | Its head carries no count, because the status line already states it (redesign §2.5: never twice in one page body). It is not drawn when nothing is unacknowledged; the status line then gives the honest empty words |
| Linked alert | An `alert=<id>` missing from the rows shown sits under "Linked alert" at the top: "Alert <id> is not open or resolved in the last 24 h" |
| Nothing resolved | `?resolved=24h` with nothing resolved in the last 24 h reads one line where the group would be, once a read succeeds: "Nothing resolved in the last 24 h · Open the log ›" |
| Reads | API-14 `status=recent` with `limit=200` every 15 s; Recent activity's API-17 (`limit=5`), API-IRR-01 and API-09 every 60 s. On the log, Load older asks for one more API-17 page, and each poll reads every page shown again from the newest, so a refresh leaves no gap between them |
| Shell | The shell polls API-01 and API-02 under the shared read keys, so the R hook's refresh lowers the nav count at once and Stop notes what the shell's API-02 says is running |
| Words and fixtures | The words table adds the activity actions, the schedule, line and run fields and the alert statuses. The fixtures add `fence-breach`, `run-missed`, `activity-mixed`, `alert-repeat` and `activity-paged` (60 rows, for Load older) |
| Browser test | `dashboard/e2e/activity.spec.ts` and `caretaker-visit.spec.ts` run on #1128's Playwright harness; `routes/activity.test.tsx` covers the acceptance against the fixture API |

## 8. SCR-07 Equipment · Control › Equipment · `/equipment`

**Purpose:** what every actuator and the controller is doing, why, and how much it has run; the bursts that replace
the Home Assistant buttons, restart, and the grow lights by hand.
**Stories:** US-EQP-01, US-EQP-02, US-EQP-05, US-EQP-06, US-PLAT-13.

The layout is the redesign's §5.12: the status line states the controller ("Controller up 3 d 4 h · firmware
2026.11.3 · Wi-Fi −61 dBm · updated 12 s ago") and the header holds "Find a setting". Two balanced columns from 861 px:
Climate, Lights and Flags on the left; Water relays and the Controller card on the right. Then, full width: Bursts,
Why, the lanes and the daily runtime. A light's "Automation setting ›" opens its setting drawer in place (`?edit=`).

| Panel | Shows | Data | Refresh |
|---|---|---|---|
| Board (Climate, Lights, Water relays) | A fixed list: climate (fan1, fan2, vent, heat1 electric, heat2 gas), light (`grow_light_main`, `grow_light_grow`) and water (the 11 wet relays L01-L11, fog and the fertilizer master included). Each row: the name, its state as a word with a dot in the equipment's token (ON, OFF or UNKNOWN; the vent OPEN or CLOSED; only UNKNOWN is muted and hatched), then since (the start of the current run of equal states), time on today and cycles from the collapsed edges, and report age. A state is the newest row while the controller reports, and "unknown" with its age only while it does not, or with no row. Grow lights show both the Lutron readback and the controller's intent (VD-48), marked when they differ, with their controls | API-13 (`v_equipment_now`, `equipment_state`) | 15 s |
| Flags | Occupancy, the leak flag and water flowing, each a name and its state in words; fault and warn in their text colours | API-13, API-IRR-01 | 15 s |
| Controller | API-02 `controller`: firmware version, uptime and last reset reason, free heap as a meter against the 32 kB floor (fault below it), largest block, RSSI in dBm and last seen, each with its age | API-02 | 15 s |
| Bursts | The shared burst controls (operators): Fans 15 min, Humidify 5 min and Vent bypass 10 min, each its name bold and its length muted as on Now, "Clear bursts" while a burst runs (or while what runs is unknown), and "More actions" with Restart controller; what runs from the controller's readback (`fan_burst_active`, `vent_bypass_active`, `fog_burst_active`) with the time left; a burst the controller holds off says why ("Humidify held off: someone inside"); during emergency cooling, emergency heating or a sensor fault it says the burst is ignored | API-02 (`overrides`), API-IRR-01 (engine flag) | 15 s; 60 s |
| Why | `mode_reason`, `fog_block_reason` and `moisture_block_reason` in words, each with its since and a link to the Graphs window where it began (`#why`) | API-13 | 15 s |
| Last 24 h | Equipment lanes with 24 h and 72 h chips (`?range=`) | API-05 | 60 s |
| Daily runtime · 14 days | One bar row per heat 1, heat 2, fan 1, fan 2, vent and fog on a shared scale, each in its equipment's colour with its daily average, and the table under it | API-05 | 60 s |

| Action (operator) | Operation | Feedback |
|---|---|---|
| Fans, Humidify or Vent bypass for N minutes | API-IRR-12 `{kind, minutes}` | C. No dialog; the burst shows here and on Now once its readback turns on. A burst never changes occupancy |
| Clear (ends bursts) | API-IRR-12 `{kind: "clear"}` | C; the readback confirms it off |
| Restart controller (under "More actions") | API-IRR-12 `{kind: "restart"}` | C, no dialog. The controller's uptime (API-02 `controller.uptime_s`) resets within 3 minutes; after the reconnect the desired settings are sent again and `irr_program_id` returns `src=nvs` with the same hash; Activity records who restarted it and why |
| Lights on, off or automatic | On and off: API-20, through `verdify-setpoint-server`. Automatic: API-10 on `sw_gl_main_auto_mode` or `sw_gl_grow_auto_mode` (its drawer opens in place) and, once it reads CONFIRMED, one API-20 command to match the intent when the intent and the Lutron state are both known and differ | S for automatic; the Lutron readback confirms each API-20 command |

| State | Equipment shows |
|---|---|
| Loading | The board's cards and the Controller card as skeletons at their reserved heights |
| Empty | "Running: none" when no burst runs |
| Stale, not reporting | §1.5: states read "unknown · last report 14 min ago" only while the controller is not reporting, never OFF; the Controller card's facts carry their ages |
| Error | The board, Why, the lanes and the daily runtime each fail on their own with Retry |
| Before the irrigation firmware | Bursts and restart are not rendered: "Bursts arrive with the irrigation firmware (`manual_override`)" |

Acceptance:
1. Every actuator in the fixed list shows ON, OFF or unknown with since, runtime today and cycles; with the
   controller reporting, `fan1` reads OFF since 10/27 04:26, never unknown; with it not reporting, every state reads
   unknown with its age.
2. "Fans 15 min" sends one API-IRR-12 request `{kind: "fans", minutes: 15}`, reads "Sending…", then "Fans burst · 15
   min left" here and on Now once `fan_burst_active` turns on; a Humidify burst while someone is inside reads
   "Humidify held off: someone inside"; Clear ends a burst and its readback reads off.
3. Grow lights always show the controller's intent and the Lutron state; a fixture with them different reads "differ";
   `/equipment?edit=sw_gl_main_auto_mode` opens the setting drawer on Equipment.
4. Restart sends one API-IRR-12 `{kind: "restart"}`; the fixture's uptime resets and `irr_program_id` returns
   `src=nvs` with the same hash after the reconnect.
5. The h1 is "Equipment" and the board's two columns are balanced; the Controller card shows the heap against the
   32 kB floor; "Daily runtime · 14 days" shows one bar row each for heat 1, heat 2, fan 1, fan 2, vent and fog.
6. Viewers see the board, the Controller card, Why and the lanes with no burst, clear, restart or light control.

As built (VD-14, `dashboard/app/routes/equipment.tsx`), where the board settles a detail this section leaves open:

| Topic | As built |
|---|---|
| Row | The API's label and its state as a word with a dot in the equipment's token (ON, OFF or UNKNOWN; the vent OPEN or CLOSED; no tint), over one line of facts in words: "since 08:31 · on today 2 h 10 min · cycles 6 · reported 2 h ago" (the vent "open today"); one line from 1200 px. A null since, time on or cycle count reads "—" |
| Since | "HH:MM" on today's America/Denver day and "MM/DD HH:MM" before it, 24-hour, where the wireframe's "Fri 07:43" reads "10/30 07:43"; the full time is in the `title`. A known state whose run has not changed within API-13's 31-day edge window (#1167) has a null since and reads "since — (no change in 31 days)" |
| Unknown | "UNKNOWN · last report 14 min ago" beside the name, and the facts read "since — · on today — · cycles —", because API-13 serves none of them while the state is unknown |
| Grow lights | "OFF (controller) · Lutron OFF", with "▲ DIFFER" in an amber tag when both are known and differ; an unknown Lutron state reads "Lutron UNKNOWN · 11 min ago"; a null intent reads "— (controller)". A note in the Light card calls the Lutron state sensed and says since, time on and cycles follow it |
| Omitted actuator | Its `equipment_state` key, with "—" and "no data", never a made-up name or OFF. An item outside the fixed list follows its group's fixed rows; an `_intent` key is never a row |
| Errors | A failed API-13 read replaces the board's groups with one "Couldn't load equipment." and Retry, while Flags and the Controller card keep their own reads; a failed API-05 read shows "Couldn't load equipment lanes." and Retry in the lanes panel |
| Lanes | The panel reads "Last 24 h" or "Last 72 h" with "Updated N s ago". Each 60 s read asks API-05 for the window ending at that moment on the server's clock (API-02's skew), and the lanes draw the window it answered for, on the sync key `verdify:equipment:<range>` |

As built (VD-16, `dashboard/app/components/burst-panel.tsx`), where the panel settles a detail this section leaves open:

| Topic | As built |
|---|---|
| Panel | The "Bursts" card under the board, its buttons the same `BurstButton` as Now's quick actions (name bold, length muted) with the same commands. Running bursts are chips after "Running:", the same words for viewers and operators; while API-02 fails, the ingestor is not running or the controller is not reporting it reads "Running: unknown", never none |
| Bursts | "Fans burst · 12 min left"; a held burst reads only "Humidify held off: someone inside", with the code in words and no time left |
| Engine flag | Read from API-IRR-01 at the 60 s cadence. Until it answers nothing is offered; while it fails the panel says so with Retry. Before the firmware operators read "Bursts arrive with the irrigation firmware (`manual_override`)" and "Restart arrives with the irrigation firmware"; a running burst still shows |
| Restart | The button reads "Restart sent" and is disabled until `controller.uptime_s` falls below its last read, the one held at the press included (with none held, under 180 s), then "The controller restarted". After 3 min with no drop it reads "The controller has not restarted" and can be pressed again, and a later drop still reads restarted |

As built (VD-20, `dashboard/app/components/equipment/why-panel.tsx` and `light-controls.tsx`), where the panel and the controls settle a detail this section leaves open:

| Topic | As built |
|---|---|
| Why | A card titled "Why" (`#why`, the target of Now's mode words) under the board and Bursts and above the lanes, on the board's own API-13 read, one row per reason at every width; at phone width the label heads its row. A code the table lacks reads raw in mono with its since and link. A null code with no `observed_at` reads "unknown · no data", and a null code has no link. A failed read shows "Couldn't load the reasons." with Retry |
| Automatic line | Under a light's facts, for every role: "Automatic" or "Automation off" from the switch's device value, or "Automation —" with the API's reason while it is not reported or API-09's newest read fails. REQUESTED, NOT CONFIRMED or HELD replace the words while API-09 says so, and "CONFIRMED Automatic" stands once this control's own write reads it, until another write takes its place |
| Controls | Operators only, by state (redesign §4.5, one primary): in automatic and matching the controller, the switch to the other state alone ("Turn on" while off); overridden by hand (automation off, or Lutron differing from the intent), "Back to automatic" first, then that switch; with the Lutron state unknown both switches, with Back to automatic unless automation is known on and matching. "Activity ›" (straight to the log, filtered to the light) sits beside "Automation setting ›", which opens the light's automatic switch in its setting drawer, in place. "Waiting for Lutron" during the readback, when both switches show, the pressed one disabled and the other free to override it. Back to automatic is disabled until API-09 confirms or refuses its write |
| A 503 | `Problem.result` in words, kept beside "Lutron hasn't confirmed: still OFF" after 30 s ("its state is unknown" when the readback is); a readback that matches clears both. `unavailable` with no `result`, when no answer reached the page, reads the readback the same way with its own words; `writes_disabled` and other refusals send nothing, so they read none. Pressing Back to automatic ends that readback and clears its words, and Turn on or Turn off clears a refused write's |
| Reason | Turn on and Turn off are one tap with no reason, so their Activity rows show who and where from with none. The resync carries "Back to automatic", as its write does; so does the operator API's own resync, whose row API-17 tells apart by `detail.via` `server_resync` |
| Resync | Held above the board, so a failed API-13 read does not lose it. A Turn on or Turn off pressed while the write waits stands in its place. If the read after CONFIRMED fails, none is sent and the row says "Couldn't read the light, so Lutron was not matched to the controller". Leaving or reloading the page before CONFIRMED drops the page's resync, and the operator API sends it instead, 22 s after CONFIRMED, when no light command was recorded since the write and the controller decided the light in the last 2 minutes (#1300). Only an operator API restart before then drops it: the row then reads its "differ", and pressing Back to automatic again sends it |
| Activity | "Lights on Main lights", "Lights off Grow lights". A call Lutron did not confirm adds its result in words |

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
| US-NOW-01 | MVP | SCR-01 | The hero value and its band bars |
| US-NOW-02 | MVP | Shell, SCR-01 | Banners, the Activity count pill, the status line |
| US-NOW-03 | MVP | SCR-01, SCR-07 | "Running now", the occupancy row, the board |
| US-NOW-04 | MVP | SCR-01, SCR-04 | Water today's zone meters, last water per zone |
| US-NOW-05 | Next | SCR-01 | "Running and next": the track and the "Next 12 h" list |
| US-NOW-06 | MVP | Shell, all | States, API-unavailable, ingestor and not-reporting banners |
| US-NOW-07 | MVP | All | Phone layouts, the five-tab bottom bar, Stop all water on Now and Water |
| US-NOW-08 | MVP | SCR-01 | The hero's one-sentence answer |
| US-GRF-01 | MVP | SCR-02 | The chart stack, band in force with excursions, setting steps, SUPERSEDED |
| US-GRF-02 | MVP | SCR-02 | Equipment lanes, 31-day limit |
| US-GRF-03 | MVP | SCR-02 | The toolbar's ranges, the resolution line, header statistics, CSV, URL, retention |
| US-GRF-04 | MVP | All | Units and time (§1.5) |
| US-GRF-05 | Next | SCR-02 | Outdoor and forecast |
| US-GRF-06 | Next | SCR-01, SCR-02 | Zone tiles, Zones' small multiples and the focused zone |
| US-GRF-07 | MVP | SCR-02 | Metric picker |
| US-GRF-09 | MVP | SCR-02 | Statistics (`/graphs/statistics`; `/graphs/scorecard` redirects there) |
| US-GRF-10 | Next | SCR-02 | PPFD and daily DLI in the metric picker once the PAR meter ships (VD-71); no DLI before |
| US-PLN-01 | MVP | SCR-03 | Plan in force with its validity bar |
| US-PLN-02 | MVP | SCR-03 | Lanes (24 h, 72 h chip), MANUAL and SUPERSEDED |
| US-PLN-03 | MVP | SCR-03, SCR-01 | Planner health (marks, 14-day strip), Now's Iris phrase |
| US-WAT-01 | MVP | SCR-04 | Today: zone cards and line rows |
| US-WAT-02 | MVP | SCR-04 | Line state and reasons |
| US-WAT-03 | MVP | SCR-04, SCR-01 | History's minutes or gallons by group, Now's Water today |
| US-WAT-04 | MVP | SCR-04 | Runs |
| US-WAT-05 | MVP | SCR-04, SCR-06 | Week strip and timeline outcomes, `irrigation_run_missed` alert |
| US-WAT-06 | MVP | SCR-04 | Schedule editor, its preview words and its draft on the timeline |
| US-WAT-07 | MVP | SCR-04 | Edit, disable, delete; the Manual box |
| US-WAT-08 | MVP | SCR-04 | Fertilizer schedules with prewet and flush |
| US-WAT-09 | MVP | SCR-04 | Line enables with the check |
| US-WAT-10 | MVP | SCR-04 | Mister zone heads on Today |
| US-WAT-11 | MVP | SCR-04 | Mist schedules |
| US-WAT-12 | MVP | SCR-04 | Run… and the confirm dialog |
| US-WAT-13 | MVP | SCR-04, SCR-01, SCR-06 | Stop, Stop all water (every line, through the bridge before the firmware), the Stop result |
| US-WAT-14 | MVP | SCR-04 | Pause and resume (a pause past a block skips it) |
| US-WAT-15 | MVP | SCR-04 | The program state in the status line (`src=nvs` or `rom`), not-reporting state |
| US-WAT-16 | MVP | SCR-04, SCR-01 | Occupancy in Site today and on mister rows, Now's occupancy row |
| US-WAT-17 | MVP | SCR-04 | VPD source per mister zone |
| US-WAT-18 | MVP | Shell, SCR-01, SCR-04, SCR-06 | Flow banner, hero, alert row |
| US-WAT-19 | MVP | SCR-04, SCR-05 | Zone cap meters, Site today, line options, site caps |
| US-WAT-20 | Next | SCR-04 | History's daily water use |
| US-WAT-21 | MVP | SCR-04 | Fallback slots on the timeline and in the rules list, "VPD fallback active" |
| US-WAT-22 | MVP | SCR-04, SCR-01, SCR-06 | Mister back-off and fault, Clear fault, `irrigation_line_faulted` |
| US-WAT-23 | MVP | SCR-04 | L01 Run now through the bridge before the firmware (§1.5) |
| US-SET-01 | MVP | SCR-05 | The rail and the catalog, Band in force |
| US-SET-02 | MVP | SCR-05 | Device value, request and its reason |
| US-SET-03 | MVP | SCR-05 | Drawer and Save, crop band rows |
| US-SET-04 | MVP | SCR-05, SCR-03 | Manual, Release, MANUAL lanes |
| US-SET-05 | MVP | SCR-05 | Rows read CONFIRMED again after a reboot (no column of their own) |
| US-SET-06 | Next | SCR-05 | The drawer's Change history and Revert |
| US-SET-07 | Next | SCR-05 | Crop band preview |
| US-ACT-01 | MVP | SCR-06 | Activity → Log |
| US-ACT-02 | MVP | SCR-06, SCR-01 | Alerts' Needs action, Now's Needs someone |
| US-ACT-03 | MVP | SCR-06 | Expanded alert row, Open in Graphs |
| US-ACT-04 | MVP | SCR-06 | Acknowledge, resolve |
| US-EQP-01 | MVP | SCR-07 | The board and the Controller card |
| US-EQP-02 | Next | SCR-07 | Why |
| US-EQP-05 | Next | SCR-07 | Lights |
| US-EQP-06 | MVP | SCR-07, SCR-01 | Bursts and restart, Now's quick actions and burst rows |
| US-AUTH-01 | MVP | SCR-09 | Sign-in redirect and callback |
| US-AUTH-02 | MVP | Shell, SCR-09 | The account menu's role pill, no-access page |
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
| US-PLAT-07 | MVP | Shell | Changes-off warn banner, disabled controls |
| US-PLAT-08 | MVP | Shell | Footer build and API revisions |
| US-PLAT-09 | MVP | — (monitoring) | — |
| US-PLAT-10 | MVP | All | Weather's stack and look, polling |
| US-PLAT-11 | MVP | — (contract) | Fixture API behind every acceptance criterion here |
| US-PLAT-12 | MVP | SCR-04 | The program state's `src`; the before-the-firmware state |
| US-PLAT-13 | MVP | SCR-07 | Bursts replace the Home Assistant buttons |
| US-PLAT-14 | MVP | — (deploy) | — |

US-GRF-08 (events on the charts) shipped with the redesign as Climate's event rail. Later: US-PLN-04 (transition
audit), US-PLN-05 (replan now), US-ACT-05 (platform alerts, which stay in Alertmanager and Slack), US-EQP-03 and
US-EQP-04 (controller and sensor health) and US-PLT-01 to US-PLT-03 (SCR-08).

## 12. Who builds each screen

The issues in the [execution plan](execution-plan.md) build this doc against the committed OpenAPI and the fixture
API, so a screen can start before its endpoints land; its live check waits for them.

- **VD-03:** the content components, badges and states ([design system](design-system.md) §9), among them `AlertRow`,
  the alert row Now and Activity share.
- **VD-04:** §1 (routes for SCR-01 to SCR-09 and `/more`, which the redesign removed, the desktop nav and the phone bottom bar, the shell poll of
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
- **The redesign** ([redesign](redesign.md) §10, epic #1120): WP0 the five sections, the shell, the page and section
  headers, the card system, the micro-visualizations and the chart stack; WP1 Now and Activity; WP2 Graphs (Climate,
  Zones, Statistics); WP3 Water (Today, Schedules, History); WP4 Control (Settings, Plan, Equipment); WP5 the
  close-out (one page rhythm across the packages, the removal of what nothing used, and these docs).
