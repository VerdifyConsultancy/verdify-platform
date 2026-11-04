# Dashboard design system

dashboard.verdify.ai is a sibling of weather.vallery.net: it copies weather's stylesheet with the tokens unchanged (IBM
Plex, sticky white header, ink hero, cards, chips, pills, uPlot charts) and swaps the vallery.net V for the Verdify
mark-as-V lockup with a `· DASHBOARD` eyebrow. It adds only what an operator app needs: a five-tab bottom bar on
phones; the badges REQUESTED, CONFIRMED, NOT CONFIRMED, HELD, MANUAL, AI and PLANNED; number fields that show min, max
and grid step; one edit drawer; one confirm dialog, for water runs only; toasts; honest empty, stale and not-reporting
states; and four uPlot primitives (time series with band ribbon and setting steps, equipment lanes, plan lanes, minutes
by zone).

Status: Draft v2 (2026-11-01)

[README](README.md) · [product](product.md) · [screens](screens.md) · [architecture](architecture.md) ·
[irrigation](irrigation.md) · [design system](design-system.md) · [execution plan](execution-plan.md) ·
[OpenAPI](openapi/dashboard-api.v1.yaml)

## 1. The answer

| Area | Decision |
|---|---|
| Look | weather.vallery.net, copied from jvallery/weather@249fba3 `web/app/styles.css`; tokens byte for byte |
| Deltas | Two accessibility fixes (focus ring, input border) as rules, not token edits; new tokens only for badges and charts |
| Brand | Verdify leaf mark as the V, "erdify" in Plex Sans 620, `· DASHBOARD` eyebrow |
| Nav | Weather's top nav above 860 px; a fixed five-tab bottom bar at 860 px and below ([screens](screens.md) §1.1) |
| Type | IBM Plex Sans (variable 100-700) and Plex Mono 400, self-hosted, copied from weather |
| Units | Fixed: °F, % RH, kPa, gal, min. No °C toggle in v2 |
| Theme | Light only. New colours are CSS custom properties and charts read tokens, so dark can be added later |
| Charts | uPlot 1.6.32 (weather's pin), lazy-loaded, colours read from tokens |
| Code | Copy weather files into `dashboard/` now with provenance; a shared package only when a third app needs it (§8) |
| Later | Dark theme (the Lab's dark tokens on these names), command palette, keyboard map, °C toggle, sparklines, keyboard chart cursor, compare periods, annotation rail, calendar heatmaps, Day Arc, icon set, palette checks in CI |

Every screen follows five rules:
1. **Lead with the answer**; detail goes in `<details>`.
2. **State once**: time zone, units and cadence in the panel eyebrow ("MEASURED · EVERY 60 S · AMERICA/DENVER").
3. **Humanize codes**: `SEALED_MIST` stage `S1` → "Sealed, misting one zone", from the one words table that a unit
   test keeps complete; a code unknown at run time shows raw.
4. **Requested is not confirmed**: a sent value shows REQUESTED until the API confirms it from the controller.
5. **Gaps stay gaps**: null renders "—" with a reason, charts break the line, nothing missing is drawn as 0 or off,
   and stale data never reads as an all clear.

## 2. Tokens

Weather's `:root` block is copied byte for byte; dashboard tokens and two accessibility rules are appended after it.

### 2.1 Copied exactly (weather `web/app/styles.css` lines 6-15)

```css
:root{
  --ink:#112231;--muted:#626b76;--line:#dcede7;--line-strong:#b9d8cd;--field:#f4f7f4;
  --forest:#0e5a43;--leaf:#2e7d5c;--mint:#7de2b8;--amber:#d6a93b;--fault:#b3261e;
  --ink-2:#1b2d3d;--ink-muted:#b9c4cc;--tint-ok:#e3f3ec;--tint-warn:#f8eccb;--tint-fault:#fbeae8;
  --radius-sm:.375rem;--radius:.625rem;--radius-lg:1rem;
  --shadow-xs:0 1px 2px rgb(17 34 49/6%);--shadow-sm:0 2px 8px rgb(17 34 49/8%);
  --shell:80rem;--gutter:clamp(1rem,4vw,2.5rem);
  --mono:"IBM Plex Mono",ui-monospace,monospace;
  color:var(--ink);background:#fff;font:100%/1.5 "IBM Plex Sans",system-ui,sans-serif;
}
```

Roles (contrast on white): ink for text, headings, hero and current chip (16.19:1); muted for secondary text and axes
(5.41:1); line and field for borders, grid, thead, footer, hover; forest for links, primary buttons, eyebrows (8.20:1);
leaf for hover; fault for errors (6.54:1). Mint (1.56:1) and amber (2.19:1) carry text only on ink (10.36:1, 7.40:1).

### 2.2 Added in the dashboard section of the same file

```css
:root{
  --focus:#0e5a43;--control-border:#6f867d;
  --ok-border:#bfe3d3;--warn-border:#ecd798;--warn-text:#5c4407;--fault-border:#f1c4bf;
  --manual-bg:#ecebf6;--manual-border:#d5d2ea;--manual-text:#3f3a77;--dot-off:#e98b82;
  --c-temp:#112231;--c-vpd:#7d3ab7;--c-rh:#0d98a4;--c-outdoor:#6c7278;--c-band-wash:#e2ede8;
  --c-band-edge:#6e9c8e;--c-setpoint:#0e5a43;--c-requested:#a77601;--c-bound:#626b76;--c-grid:#dcede7;
  --eq-mister:#9d2398;--eq-fog:#1b9bc7;--eq-fan1:#22a096;--eq-fan2:#484fa7;--eq-vent:#056190;
  --eq-heat1:#d47a00;--eq-heat2:#b61f02;--eq-light-main:#4d9f45;--eq-light-grow:#2855ad;
  --eq-water:#2389e2;--eq-fert:#6331a0;--eq-master:#0e9580;
}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px} .hero :focus-visible{outline-color:var(--mint)}
input,select,textarea{border-color:var(--control-border)} .brand-leaf{fill:var(--leaf)}
.badge-ai{background:var(--ink);border-color:var(--ink);color:var(--mint)}
.badge-planned{background:transparent;border:1px dashed var(--muted);color:var(--ink)} .badge-superseded{color:var(--muted)}
.badge-held{background:#fff;border-color:var(--ink);color:var(--ink)}
.btn-stop{background:var(--fault);border-color:var(--fault);color:#fff}
```

| Fix | Weather today | Measured | Dashboard | Measured |
|---|---|---|---|---|
| Focus ring | 3px `--amber` (line 20) | 2.19:1 on white | 2px `--focus` (forest); mint on the hero | 8.20:1; 10.36:1 on ink |
| Input and select border | `--line-strong` (line 32) | 1.53:1 | `--control-border` | 3.90:1 on white, 3.62:1 on field |

Both weather values fail WCAG 2.2 SC 1.4.11 (3:1); chips and outline buttons keep `--line-strong` (text identifies them).

### 2.3 Type, space and targets (weather's, unchanged)

| Role | Spec |
|---|---|
| h1 / h2 / h3 | clamp(1.75rem,3.6vw,2.6rem) / clamp(1.2rem,2vw,1.45rem) / 1.02rem; weight 620, -0.02em |
| Eyebrow; pills | `.eyebrow` mono .72rem uppercase .1em forest (mint on ink); `.tag`/`.status`/`.badge` mono .68rem 500 uppercase, 999px |
| Hero value / stat | clamp(3.5rem,9vw,5.5rem) 620 -0.04em / label mono .66rem mint, value 1.3rem 600, sub-line .72rem |
| Tables | .875rem tabular-nums; thead mono .68rem forest on field |
| Setting key (new) | Mono .78rem muted, next to the label: `mister_engage_kpa` |
| Space, targets | Weather's literals (.25 to 3rem), grid gap 1rem, card padding 1rem; 44 px (2.75rem) buttons, inputs, nav and summary; chips 2.5rem; checkboxes 24 px |
| Numbers | Tabular figures; a no-break space before units ("72.4 °F"), none before % ("61.8%"); true minus ("−0.4 °F") |

### 2.4 Layout tokens and stylesheet sections ([redesign](redesign.md) §3.3, §10.1)

`app/styles.css` keeps weather's copy and the §2.2 block last, with exactly its two `:root` blocks. Everything else is
one file per owner under `app/styles/`, which `root.tsx` loads after `styles.css` in this fixed order (`STYLESHEETS`):

| File | Section | Owner |
|---|---|---|
| `tokens.css` | Tokens: the layout tokens below, and no colour | WP0 |
| `primitives.css` | Primitives: layout classes, the card system, the micro-visualizations | WP0 |
| `shell.css` | Shell: header, account menu, phone bar, banners, SCR-09's cards | WP0 |
| `now-activity.css` | Now and Activity (prefixes `now-`, `act-`) | WP1 |
| `graphs.css` | Graphs (prefix `gr-`) | WP2 |
| `water.css` | Water (prefixes `wat-`, `sch-`) | WP3 |
| `control.css` | Control (prefix `ctl-`) | WP4 |

```css
:root{
  --space-1:.25rem;--space-2:.5rem;--space-3:.75rem;--space-4:1rem;--space-5:1.5rem;--space-6:2rem;--space-7:3rem;
  --fs-label:.72rem;--fs-xs:.75rem;--fs-sm:.875rem;--fs-md:1rem;--fs-lg:1.25rem;--fs-xl:1.75rem;
  --shell-wide:96rem;--aside:22rem;--rail:15rem;--tap:2.75rem;--bar-h:3.5rem;--header-h-phone:3.25rem;
  --z-raised:3;--z-sticky:10;--z-nav:20;--z-popover:30;--z-toast:40;
}
```

The section files load after the §2.2 block, so none may redefine `:focus-visible` or the input borders, and none
carries a colour literal but white (`app/styles/sections.test.ts`). WP0.1 moved the screen rules out of `styles.css`
unchanged; no moved rule sets a property a §2.2 rule sets on the same element at the same specificity, and the rules
whose relative order changed between files style different screens' elements. The breakpoints are declared once in
`app/lib/narrow.ts` (`PHONE_PX = 600`, `NARROW_PX = 860`, `WIDE_PX = 1200`) and mirrored by the media queries.

## 3. Shell and header

Weather's header, nav and footer, with a Verdify lockup.

```text
1440 │ [leaf]erdify · DASHBOARD   Now  Graphs  Plan  Water  Settings  Activity 2  Equipment   [OPERATOR] Jason  Sign out │
390  │ [leaf]erdify · DASHBOARD                  [OPERATOR] │
     │  Now  │  Water  │  Graphs  │  Activity 2  │  More    │   fixed bar: 56 px plus the safe-area inset
```

| Part | Spec |
|---|---|
| Lockup | Link `.brand`, `aria-label="Verdify dashboard, home"`. The glyph is `#verdify-v-glyph` from `site/quartz/static/brand/verdify-wordmark.svg`, inlined as JSX without its `<style>` and Inter `<text>`: leaf path `.brand-leaf`, ink path `.brand-mark`, dot `.brand-dot`. About 1.2em tall, it is the V, so no gap follows it; then "erdify" in Plex Sans 620 at 1.1rem, then `<small> · DASHBOARD</small>` (weather `.brand small`). Never animated. Favicon: `site/quartz/static/brand/favicon.svg`. |
| Nav | Weather's `SECTIONS` model (label, path, `owns(path)`), `aria-current="page"`; the open-alert count is plain text in the link ("Activity 2"): unacknowledged `v_open_alerts` rows, repeats of one `alert_type` and `sensor_id` counted once (as Activity lists them), the one count every surface uses. Above 860 px the top nav lists every section with weather's 2px forest underline ([screens](screens.md) §1.1). At 860 px and below a fixed bottom bar (a labelled `nav`) holds Now, Water, Graphs, Activity and More as text tabs of at least 44 px, the current one ink with a 2px forest top rule; More opens `/more`: Plan, Settings, Equipment, Sign out and the build line. |
| Header tools | Role pill (OPERATOR or VIEWER); above 860 px also the display name and "Sign out" (POST form), which move to More at 860 px and below. No units or theme toggle. |
| Banners | Under the header, only while true: one fault banner at a time, worst first (API down, ingestor not running, controller not reporting, leak), then warn, one line each, each linking to the page that explains it. [Screens](screens.md) §1.2 lists the banners, when they show and their words; §4.5 has the states |
| Footer | Weather `.site-footer`: "Verdify dashboard · build `<sha>` · API `<sha>`", graphs.verdify.ai ↗, lab.verdify.ai ↗, IBM Plex licences |
| Page grammar | `.page-head` (eyebrow, one h1, lede, actions) → sections with `.section-head` (eyebrow, h2, "All … →") → cards in `.grid-2`, `.grid-3` or `.grid-auto`. The ink hero appears on Now only. |
| Sign-in pages | The same header without nav or tools; one card with the message and a forest "Sign in" button |

## 4. Components

Weather classes are reused as they are; "new" means added in the dashboard section of `styles.css`. React components
live in `dashboard/app/components/`.

| Component | Base | Spec | Used on |
|---|---|---|---|
| `Card` | `.card`, `.card-head` | 1px line, radius .625rem, xs shadow, 1rem padding; an interactive card is one link with a visible focus ring | All |
| `Hero` | `.hero`, `.stats` | Ink band, radius 1rem. Left: eyebrow, the one-sentence answer as the title (worst first; wording in [screens](screens.md) SCR-01), indoor temperature as the hero value, a sub-line against the band, freshness. Right: 8 stat tiles (4 × 2; on phones 4, 2-up, the rest in the "All readings" disclosure) | Now |
| `StatTile` | `.stat` (ink), `.summary-stats div` (white) | Eyebrow label, value and unit, sub-line; freshness and badge only when they differ from the panel; null is "—" plus the reason | Now, Water, Graphs |
| `Chips` | `.chips`, `.chips-sm` | Ranges, metrics, filters; links with `aria-current` or buttons with `aria-pressed`; current is filled ink | Graphs, Settings, Activity |
| `Table` | `.table-wrap`, `caption` | Focusable scroll region (`role="region"`, `tabindex="0"`, labelled); fixed group order and a text filter; no column sort | Settings, Activity, Water |
| `EventRow` | `.events`, `.event` | Mono time column, bold title, badges, muted detail; writes name actor and source in text ("Jason · dashboard"; "iris" or "steward · agent" with AI; "forecast-engine · system", never AI) and show the reason. `AlertRow`, shared by Now and Activity, adds a 3px left rule in severity colour (critical `--fault`, warning `--amber` as weather's `.notice-warn`, info `--muted`), the severity word and Acknowledge | Activity and alerts, Now |
| `Kv`, `Details` | `.kv`, `details`/`summary` | Facts; progressive detail with a 44 px summary | All |
| `Button` | `.btn`, `.btn-primary` | 44 px; verb and object ("Run wall drip", "Save"); busy reads "Sending…" and is disabled; no spinners. New `.btn-stop`: fault fill, white text (6.54:1), never confirmed | All |

### 4.1 Badges

Text always; colour is the second channel. AI is ink and mint, not weather's warn-tinted AI badge: warn means REQUESTED.

| Badge | Meaning | Class (fill / border / text) |
|---|---|---|
| REQUESTED | Saved; waiting for the controller to report it (`cfg_*` readback, or the program hash) | `.status-pending`: tint-warn / warn-border / warn-text (7.81:1) |
| CONFIRMED | The API reports a matching readback, or the program that carries the value | `.status-complete`: tint-ok / ok-border / forest (7.14:1) |
| NOT CONFIRMED | No match 5 min after the request: the ingestor's `setpoint_unconfirmed` window (`ingestor/tasks/_common.py`, section 16). Display only; it blocks nothing; its reason shows beside it (§4.2) | `.status-failed`: tint-fault / fault-border / fault (5.61:1) |
| HELD | The platform applies another value on purpose (a dispatcher clamp or an armed experiment); the reason shows beside it (§4.2) | New `.badge-held`: white / ink / ink (16.19:1) |
| NO READBACK | The API reports no readback for the setting: it is sent, never confirmed | `.tag` base |
| MANUAL | The item's manual flag is on (a setting, schedule or line): Iris skips it; people and other agents can still change it | `.badge-manual`: manual-bg / manual-border / manual-text (8.54:1) |
| AI | Last written by Iris or another agent (source `iris` or `agent:<name>`); the name follows in text. System writes, such as forecast rules, never carry it | New `.badge-ai`: ink fill, mint text (10.36:1) |
| PLANNED | Future intent: an Iris plan waypoint or a schedule occurrence | New `.badge-planned`: no fill, dashed muted border |
| SUPERSEDED | A plan waypoint that will not apply, because a newer value holds until Iris's next plan (the API marks it) | New `.badge-superseded`: `.tag` base, muted text (5.01:1); its chart segment is muted too |
| STALE | Older than its limit, or from a controller that is not reporting (§4.5) | `.status-stale` |
| CLEAN / FERT | Water type of a line or run | `.tag` with a leading dot in `--eq-water` / `--eq-fert` |
| RUNNING / IDLE / DISABLED | Line state; L05 and L06 read "Reserved" with DISABLED until a person enables them. Equipment reads ON / OFF / UNKNOWN in the same families, exactly as the API sends it ([screens](screens.md) SCR-07) | Ok family / base / base with muted text |

### 4.2 Requested → confirmed

```text
VPD Mister Engage    1.10 kPa  CONFIRMED · 12 s ago                                  [Edit]
                     → 1.20 kPa  REQUESTED · 40 s
                     → 1.20 kPa  NOT CONFIRMED · Sent 06:02; no matching readback yet
                     → 1.20 kPa  HELD · Clamped to 1.15 kPa: VPD-high moisture guardrail
```
The device value (readback and age) is the large number; the request sits under it until confirmed. For a value the
irrigation program carries, it is the program's value on the controller ("On controller · rev 42: 360 gal"). The API
returns the state (the UI never compares binary32 readbacks) and its reason in words, under HELD and NOT CONFIRMED
always and under REQUESTED once the request is 60 s old ("Applies with the irrigation firmware" at once): clamped
(with the applied value), controller heap low, push failed, ingestor not running, controller not reporting or held by
an experiment; else "Sent 06:02; no matching readback yet". REQUESTED rows poll every 5 s; no Send again, as the
dispatcher re-sends.

### 4.3 Form controls and the edit drawer

```text
┌ Edit setting ──────────────────────── [×] ┐   1440: 28rem drawer on the right
│ PLANNER POLICY · mister_engage_kpa         │   390: full-screen sheet, actions pinned
│ VPD Mister Engage     Graph this setting › │
│ Device 1.10 kPa · CONFIRMED 12 s ago · AI  │
│ New value  [−][ 1.20       ] kPa [+]       │
│ Allowed 0.50–2.50 kPa · step 0.05          │
│ [x] Manual: Iris skips this                │
│ Reason (optional) [                      ] │
│                          [Cancel] [Save]   │
└────────────────────────────────────────────┘
```

| Control | Spec |
|---|---|
| `NumberField` | `<input inputmode="decimal">` with a unit suffix; the hint shows the registry `min`–`max` and the entity step; − and + move one step. The client checks only min and max. On submit the server snaps to the grid; Save closes the drawer, so the snapped value shows in the Save toast ("Requested 1.20 kPa for VPD Mister Engage (snapped from 1.22)"). Errors sit beside the field ("Must be 0.50–2.50 kPa") with `aria-invalid` and `aria-describedby` |
| `Switch` | `role="switch"`, `aria-checked`, visible ON/OFF word; `sw_*` settings, line enables, mister associations |
| `Select` | Native `<select>` for enum settings (`enum_values`) |
| `TimeField` | "At [time]" or "[Sunrise ▾] + [30] min", with today's resolved clock time beside it; weekday chips |
| `ManualToggle` | Checkbox "Manual: Iris skips this", ticked by default on every dashboard edit form, for settings and irrigation items alike; unticking it saves manual off. An inline switch (line enable, VPD control, Schedule) also saves manual on; the item's drawer can untick it. Quick actions (Run, Stop, Pause, Resume, Clear fault) send no manual value and never change it |
| `Reason` | Optional one-line text, stored with the write |
| `EditDrawer` | One drawer for settings, schedules and line options: native `<dialog>` with `showModal()`; focus goes to the first field and back to the opener; Escape and × close without asking. Save sends once with one `Idempotency-Key`, closes, toasts; the row then shows REQUESTED → CONFIRMED. A 422 keeps it open with the server's message beside the field. "Graph this setting ›" (`/graphs?set=<param>`) shows only when the setting's unit has a Graphs panel |

### 4.4 Confirm dialog: water runs only

```text
┌────────────────────────────────────────────────────────┐
│ Run L01 Wall drip (clean) for 10 min?                  │
│ Starts now · wall drip today 10 of 60 min              │
│ Duration [ 10 ] min (1–120)     [Cancel] [Run 10 min]  │
└────────────────────────────────────────────────────────┘
```
The only confirmation in the app; fertilizer lines show FERT in the title. Before the irrigation firmware only L01 has
it, with no Duration field: the run lasts `irrig_wall_duration_min`, and the dialog says so. Stop and "Stop all water"
are one tap with no dialog; settings and schedule saves never confirm. Values are illustrative; limits come from the API.

### 4.5 Toasts, banners and states

| State | When | What shows |
|---|---|---|
| Toast | After a write | Bottom right, 24rem (on phones full width, above the bottom bar). Success is polite, 5 s ("Requested 1.20 kPa for VPD Mister Engage"); failure is `role="alert"`; both failures and results that carry guidance or buttons (Stop's) stay until dismissed. Activity keeps the record |
| Loading | First fetch | Skeleton at the panel's reserved height; a refetch keeps the old render; no spinner or shimmer |
| Empty | Query ok, no rows | "No runs today." plus what would appear here and a link that widens the range. An alert list says "No open alerts" only while the ingestor heartbeat and `climate` are fresh; otherwise "In-app alerts may be missing: ingestor not running since 06:10" (heartbeat stale) or "… no data since 06:10" (controller not reporting); "Alerts unavailable" when the read failed; and "… can't reach the greenhouse data" once API-02 has answered and then gives no freshness |
| Stale | Sensor values: age > 2 × `expected_interval_s` (the `v_sensor_staleness` rule, migration 198). Equipment and `irr_state` report only on change, so they read unknown only while the controller is not reporting (the API decides) | Value in muted, STALE badge, "Last data 14 min ago (06:02)"; the dot turns amber |
| Not reporting | The ingestor heartbeat (`system_state`, every 60 s) older than 180 s; or, with it fresh, the newest `climate` row older than 300 s (the `GET /health` threshold in `api/main.py`) | Fault banner on every page ([screens](screens.md) §1.2): "Ingestor not running since 06:10. The controller keeps running on its own.", else "Controller not reporting since 06:10"; device values STALE and no verdict; never "Controller offline". Writes still save and apply once both are back |
| Panel error | One query fails | "Couldn't load runs. Retry" in that panel only (per-panel error boundary) |
| Busy, signed out | 429 or 503 with `Retry-After`; 401 | "Busy, retrying in 5 s"; the sign-in page, which returns to the same URL |
| Viewer, changes off | Role viewer; or `writes_enabled: false` (`DASHBOARD_WRITES_ENABLED`, off until the setting-writes release, VD-28) | Viewers get no write controls. With changes off, operators see controls disabled beside the warn banner "Changes are switched off; reading still works"; Stop and Stop all water stay live |

### 4.6 Domain composites (built in the screen issues)

| Composite | Made of | Notes |
|---|---|---|
| `SettingRow` | Label and key, the §4.2 value with its reason, MANUAL / AI / NO READBACK, the next PLANNED value or "superseded until Iris's next plan", Edit | Table row at 1440; stacked card at 390 |
| `LineCard` | `.card` with L-id and name, CLEAN or FERT, state in words, when it last watered, its last outcome and next run, today's minutes, "Run…" (§4.4), Stop, enable switch | L01–L11 per [irrigation](irrigation.md); a line that cannot run says why in one line ("Paused until 14:00", "Backing off until 13:20", "Faulted: no misting until midnight" with Clear fault); L05 and L06 read "Reserved" with DISABLED. Before the irrigation firmware Stop works on every line and Run on L01 only (§4.4); the other lines' Run and Clear fault read "arrives with the irrigation firmware" |
| `ScheduleWeek` | Week grid: rows L01, L02 and L04–L10 (L05 and L06 marked reserved), columns today and the next 6 days. Each occurrence is a block with its time, minutes or pulses and MANUAL when set; one that will not run is hatched with why (line paused or disabled, Schedule off); "Last 7 days" shows past blocks with their outcome word; the editor's draft previews as PLANNED outlines; selecting a block offers Edit, or Pause the line until after it ([screens](screens.md) SCR-04) | At 390 a day switcher shows one day for every line, with no horizontal scroll |
| `MisterAssociation` | Two `Switch`es per mister zone, "VPD control" and "Schedule", a one-line summary and the zone's VPD source; a change reads REQUESTED until the controller reports the program that carries it, then CONFIRMED | Any combination; both off reads "This zone will not mist automatically"; a watchdog back-off or fault shows on the `LineCard` |

As built (VD-12): `SettingRow` (`dashboard/app/components/setting-row.tsx`) shows each row as API-09 serves it and
decides nothing. The device line is the readback with its unit, CONFIRMED and "· 2 min ago"; STALE with "Last data …
ago (HH:MM)" while readbacks are stale or the controller is not reporting; "—" with `readback.reason` when there is no
value; keyed on `group`, a Band in force row reads "62.0 °F · DERIVED · 4 min ago" and NO READBACK shows only for
another row with `readback_via` none; a site cap reads "On controller · rev 42: 360 gal", without the rev while
API-IRR-01 fails, and STALE like any device value. Under it sits the request line while REQUESTED, NOT CONFIRMED or
HELD ("→ 1.55 kPa REQUESTED · 20 s · MANUAL · jason · “Vandas drying”", then `StateReasonLine` with the desired row's
`state_reason`; a plan value carries none, so it reads the default), a write to a row with no readback path while it
is in force ("Sent. This setting has no readback, so it can never show CONFIRMED"), else who wrote the value in force
("Iris changed this at 07:02" or "steward changed this at 06:47" with AI, "jason changed this at 08:00 · Iris may
adjust", "Forecast rule · system", "Released: plan, band or default applies"), and a read-only row's
`read_only_reason` verbatim. Values keep the decimals they carry (`keptValueText`: Iris's 48.75 on a 30 s grid reads
48.75), and so does the Save toast. `EditDrawer` gained `readOnly` for a row served with `writable: false`: read-only
for operators too, with Done and no Save or actions.
### 4.7 As built (VD-15)

The Water screen is `dashboard/app/routes/water.tsx` (the layout) with `water-board.tsx`, `water-schedules.tsx` and
`water-history.tsx`, the composites `line-card.tsx`, `mister-association.tsx` and `schedule-week.tsx`, and the rules in
`dashboard/app/lib/water.ts` (#1135). Where it settles a detail §4.6 and screens SCR-04 leave open, or differs:

| Topic | As built |
|---|---|
| Phone layout | "At 390" is the shell's phone breakpoint, 860 px and below: the tabs and Stop all water ("■ Stop all") are one sticky row under the header (on a phone too narrow for both, under about 360 px, Stop wraps below the tabs inside that row rather than widening the page), running lines sort first, and the limits strip and mister zones fold into two disclosures below the cards |
| Program banner | Not a shell banner: one `.notice` line per the first rule that holds. REQUESTED names what the controller holds too ("On controller: rev 42 · rev 43 sending"); not reporting with the built-in program says it keeps running "the built-in program" |
| Status read failing | The board shows "Water status unavailable." with Retry in place of the cards whenever API-IRR-01's newest read failed, even with an older answer cached, so no line ever reads idle from old data |
| Line head | ON or OFF is the line's enable ("Enabled:" for screen readers); a reserved line still disabled reads DISABLED Reserved there, before the engine too, and has no second DISABLED state line |
| Last water and last run | `last_water` (the newest run that opened a valve, VPD episodes included) and `last_outcome` (the newest ended ledger row) are separate facts. The outcome follows the last water ("Last water Fri 07:31, 12 min · ok") only when it is that run's own: a run that watered, stamped no earlier than the water began. A skip, miss or cap after it, or an older run's outcome beside a newer VPD episode, reads with its own time ("Last water Fri 07:31, 12 min · last run Mon 07:35 · skipped · line paused") |
| Running schedule | "RUNNING · schedule · Wall MWF" takes the label from API-IRR-02, which the Board also reads (60 s); without it the slot reads "slot 11" |
| Mister fault | The watchdog line shows on both the zone and its `LineCard` (L08 and L10 latch too and have no zone); a blocked zone with a fault shows the fault line alone |
| Limits strip | Mister zones first, then capped drips named by zone and kind ("Wall drip", "Center feed"); a reserved line still disabled is left out |
| Week | "Last 7 days" (`past_days=7`) shows the seven days before today; today stays in "Next 7 days", where its past blocks already read their outcome. A past skipped block reads "skipped · <reason>", one ahead the reason alone. L05 and L06 are marked Reserved while they are disabled, as their cards are. The API's fixed occurrence notes pass through the words table (`occurrence_reason`): "only while VPD sensing is degraded" reads "only if VPD sensing is degraded" |
| Run ledger | The window is `from` and `to` local days in the URL (To inclusive, the last 7 days by default, cut to 31 days, or 30 when 31 local days hold the fall-back day and so run 31 days and an hour); older pages load once and stay, and only the newest page polls |
| Graphs link | `graphWindowUrl` writes whole seconds in UTC (`2026-11-01T08:00:00Z`), and `runGraphUrl` links an unreadable time to Graphs' default view |
| Stop | `useStop` notes the running lines from the newest API-02 answer the page holds, the shell's own poll included, so Water offers Disable for L01 without a second API-02 poll |

## 5. Charts on uPlot

Four primitives share one `UPlotChart` wrapper (`dashboard/app/charts/`) on uPlot 1.6.32, loaded with
`import("uplot")` in an effect; `uPlot.min.css` loads only on charting routes. The wrapper owns creation,
`ResizeObserver` → `setSize`, `destroy()` on unmount and a CSS-reserved height (no layout shift), and reads colours with
`getComputedStyle`, replacing the hex weather hard-codes in `history-plot.tsx` and `linked-plots.tsx`. Series arrive as
columns (`[t[], ...series[]]`, epoch seconds, nulls for gaps, at most 1,500 points per series, bucketed by the server);
intervals arrive as `{start, end, state}` lists and are drawn in `draw` hooks, never as fake series.

### 5.1 Colours and line styles

v1 validated these for contrast and colour-vision separation; labels, line style and position still carry meaning too.

| Mark | Token | Hex | On white | Style |
|---|---|---|---|---|
| Indoor temperature | `--c-temp` | #112231 | 16.19 | Solid 2px; always the loudest line |
| Indoor VPD / RH | `--c-vpd` / `--c-rh` | #7d3ab7 / #0d98a4 | 6.64 / 3.47 | Solid 2px |
| Outdoor; forecast | `--c-outdoor` | #6c7278 | 4.87 | Solid; forecast dashed [8,4] |
| Band ribbon | `--c-band-wash`, `--c-band-edge` | #e2ede8, #6e9c8e | ink over fill 13.50; edge 3.08 | Fill between low and high, 1px edges |
| Device setting (readback) | `--c-setpoint` | #0e5a43 | 8.20 | 2px step |
| Requested, not confirmed | `--c-requested` | #a77601 | 4.01 | 2px dashed [6,4] step from the request time |
| Planned (Iris, schedule) | Series colour | — | — | 2px dashed [8,4], right of now only; a superseded waypoint in `--c-bound`, labelled SUPERSEDED |
| Bounds (min, max, clamps) | `--c-bound` | #626b76 | 5.41 | 1px dotted [1,3], labelled at the right edge |
| Now | `--ink` | #112231 | 16.19 | 1.5px solid with a "Now" label |
| Equipment lanes | `--eq-*` | mister #9d2398, fog #1b9bc7, fan1 #22a096, fan2 #484fa7, vent #056190, heat1 #d47a00, heat2 #b61f02, lights #4d9f45 / #2855ad | ≥ 3.19 | Filled segments |
| Water | `--eq-water`, `--eq-fert`, `--eq-master` | #2389e2, #6331a0, #0e9580 | 3.65, 8.48, 3.73 | Filled; the `leak_detected` flag uses `--fault` |

Causes (VD-07). A wet line's interval in the lanes and its bar in minutes by zone take the colour of their
`IrrigationCause`. There are no cause tokens, so the causes reuse existing ones, and every surface prints the words
(`dashboard/app/charts/causes.ts`; Now's running chips and Water read them from there):

| Cause | Words | Token |
|---|---|---|
| `vpd` | VPD | `--eq-mister` |
| `schedule` | schedule | `--eq-water` |
| `run_now` | run now | `--eq-master` |
| `feed` | feed | `--eq-fert` |
| `prewet`, `flush` | prewet, flush | `--eq-fog` |
| `fallback` | fallback | `--eq-heat1` |
| `unknown` | cause unknown | `--c-bound` |

A wet interval whose `cause` is null (API-05 returns null until the run ledger fills it) draws in the line's own colour
with no cause word; a cause the table does not know reads raw, in `--c-bound`.

### 5.2 Primitives

| Primitive | Shows | Data | Rules |
|---|---|---|---|
| `TimeSeries` | One metric over time with band ribbon, setting steps (device, requested, planned) and outdoor | API-04 series, API-06 band, API-12 setting steps, API-07 plan | One unit per plot, no second axis: temperature, VPD and RH are linked panels on one cursor (weather `LinkedPlots`), and a metric added from the picker joins its unit's panel or opens a new one. The ribbon carries the API's label for its source. 320 px at 1440, 220 px at 390, 150 px per linked panel |
| `EquipmentLanes` | What ran, when, how long | API-05: intervals seeded with the state before the window, equal repeated rows collapsed | Groups: Climate (fan1, fan2, vent, heat1, heat2); Water in L01–L11 order (drip_wall, drip_wall_fert, fert_master_valve, mister_center, drip_center, drip_center_fert, mister_south, mister_south_fert, mister_west, mister_west_fert, fog); Light (the Lutron readback `grow_light_main`, `grow_light_grow`; the intent is on the Equipment board); Flags (occupancy, leak_detected, water_flowing). Each row: current state, runtime and starts at right. Commanded state, so the eyebrow says COMMANDED; sensed rows (the Lutron readback, `water_flowing`) are outlined. Unknown spans come from the API (controller not reporting, or no row) and are hatched, never drawn as off; rows idle all range fold into one line |
| `PlanLanes` | What is planned, the next 24 h by default | API-07 lanes only; schedule occurrences render in Water's `ScheduleWeek` (§4.6) | One row per planned setting; device value at the left edge; dashed segments labelled with the planned value; PLANNED and AI in the row header (a forecast rule's value reads "Forecast rule · system", never AI); MANUAL rows muted with "Iris skips"; waypoints the API marks not effective are muted and labelled SUPERSEDED under "YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan" |
| `MinutesByZone` | Watering minutes per zone per day | API-IRR-10 | Rows Wall, Center, South, West (fog apart, as humidification); daily bars (`uPlot.paths.bars`), clean below fertilizer; days with no data hatched; today's remaining scheduled minutes as a dashed outline; the daily cap as a dotted tick (D-IRP-06: center mist 120, south 45, west 45, wall drip 60 min); "today 46 min · cap 120" at right; 7 d and 30 d; tooltip and table split by line and cause, in words |

```text
TEMPERATURE · BAND · AMERICA/DENVER                           [24 h] [72 h] [7 d] [30 d]
 75 °F ┤▒▒▒▒/‾‾‾‾‾‾‾\▒▒▒▒   ━━ setting ━━━  │▒▒▒▒ - - plan - - ▒▒▒▒
 70 °F ┤▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒   (gap)   ▒▒▒▒▒▒│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒   ▒ band ribbon
 Fan 1 ┤ ██████     ██████████              │                         lanes share the cursor
        10/31 12:00 GMT-06:00   11/01 00:00 GMT-06:00   11/01 06:00 GMT-07:00  Now
WATERING MINUTES BY ZONE · LAST 7 DAYS              today    cap
 Wall    ▁ ▃ ▃ ▅ ▃ ▃ ▆┆                             10 min   60
 Center  ▆ ▇ ▅ ▆ ▇ ▆ ▃┆                             46 min   120
```

### 5.3 Shared behaviour

| Behaviour | Rule |
|---|---|
| Cursor | `cursor.sync.key` = `verdify:<route>:<range>`; every time chart on a page with the same x range syncs, lanes included |
| Tooltip, legend | One tooltip per chart: time with offset, then every series value (lanes: name, state, start–end, duration), set with `textContent`. Legend below the plot at 1440, in a disclosure at 390; click toggles a series; none on single-series charts |
| Range, download | Chips 24 h · 72 h · 7 d · 30 d; Graphs adds weather's custom date range; drag-x zoom with "Reset zoom" on desktop, none on touch; "CSV of this window", built in the browser |
| Live | Now every 15 s, other pages 60 s, rows with REQUESTED 5 s; paused while the tab is hidden (weather `polling.ts`); points append with no animation |

### 5.4 Honesty, units and time

- **Gaps:** `spanGaps: false` everywhere; nulls come from the server. A summary line under each chart gives low, mean and
  high with times, coverage % and gap count (weather `seriesSummary`). A stale series stops at its last sample and the
  chart's freshness line shows STALE; nothing is extended to now. Beyond raw retention (`climate` 365 days,
  `diagnostics` 180, `setpoint_snapshot` 90) the chart shows daily summaries and says so; a gap the API explains (an
  ingestor outage) carries that reason.
- **Units** (fixed, canonical): temperature °F 1 dp; RH % 1 dp; VPD kPa 2 dp; water gal 0 dp, per run and per day (a
  whole-gallon meter: a run that moved no water reads "0 gal", and no run shows a rate or flow flag); run time min
  ("h min" from 120); the meter's flow gal/min 0 dp; lux, W/m², ppm, dBm, angles and heap kB 0 dp; other units 1 dp.
  DLI is out of the first build and returns with the PAR meter.
- **Time:** America/Denver, stated once in the eyebrow; clock times are 24-hour ("06:02", `hourCycle: "h23"` as in
  weather's plots). X values are UTC epoch seconds; labels use `Intl.DateTimeFormat` with `timeZone:
  "America/Denver"`: two lines with the offset at 1440 (weather `plotTime`: "11/01/2026 / 01:00 GMT-06:00"), time
  only at 390 (the date once ticks are a day or more apart, §5.5). Today, 2026-11-01, falls back: 01:00–01:59 occurs
  twice, so times in that hour carry their offset, and local days span real elapsed time (25 h today).

### 5.5 As built (VD-06)

The chart layer is `dashboard/app/charts/` (#1126). Where it settles a detail §5 leaves open, or differs from it:

| Topic | As built |
|---|---|
| Stylesheets | A charting route spreads `chartLinks()` into its `links`: `uPlot.min.css` and `app/charts/charts.css` (reserved heights on weather's `.plot` and `.plot-small`, tooltip, legend, notes). Chart rules stay out of `styles.css`, whose weather sections are byte for byte and whose §2.2 block stays last. A source test (`charts/chunks.test.ts`) keeps `import("uplot")` in `uplot-chart.tsx` only and makes any route that imports chart code link `chartLinks()` |
| Layers | Retention shading and the band wash paint in `drawClear` (under the grid), the band's 1px edges in `drawAxes`, and the steps, bounds, now line and all labels in `draw`. Labels never overlap: they are placed in the order Now, retention, bounds, the plan (soonest first), the request and the device line (newest first), then the band's source, and one with no free place at its start or end is dropped, so a crowded chart loses the band labels first. A dropped label's words stay in the chart notes, the tooltip or the data table |
| Band | A ribbon that changes source (in force to crop, at now or at the 90-day boundary) joins at a shared point, so the change never reads as a gap; a null in force inside 90 days stays a gap |
| Steps | Device: each readback change point held until the next, the last until now. Planned: the waypoint in force at now draws from now to the next waypoint, and the last one to the window's end. Requested, not confirmed and held rows draw with their state word; the tooltip adds who asked and the API's reason when it gives one (with the applied value when clamped). A SUPERSEDED step's tooltip names what holds it off, from the desired row in force now (architecture §5.2): "manual 1.55 kPa (jason) holds until the setting is written again", or "1.55 kPa (jason) holds until a newer plan" when that row is not manual |
| Phone axis | At 860 px and below ticks a day or more apart read the date ("10/28"), not "00:00" on every tick |
| Zoom | Double-click does not zoom out (uPlot's would zoom to the data's extent, not the window): "Reset zoom" puts every chart on the sync key back, lanes included, and LinkedPanels shows it while any panel is zoomed (a panel rebuilt with new metrics starts on the window). A poll keeps the zoom; an unzoomed chart follows its sliding window |
| Legend | A hidden series also leaves the data table, the summary line and the CSV, so each holds exactly what is plotted |
| Colours | The climate keys take `--c-temp`, `--c-vpd` and `--c-rh`, outdoor and forecast keys `--c-outdoor`, a key drawn dotted as a cross-check `--c-setpoint` (the controller's target on `/graphs/zones`, VD-18); a further line in one unit takes the unit's colour if free, else the next unused of `--eq-fan2`, `--eq-heat1`, `--eq-vent`, `--eq-mister`, `--eq-light-grow`, `--eq-fert`, `--eq-heat2`, `--eq-fog` (each at least 3:1). Planned steps take the panel's first line colour. A label drawn on the plot takes its mark's colour only where that reads at 4.5:1 on white (§7); otherwise a darker token of the same meaning: `--warn-text` for REQUESTED (`--c-requested` is 4.01:1), `--ink` for the rest (the RH plan: `--c-rh` is 3.47:1). Lines keep their §5.1 colours |
| Points | A lone sample between gaps draws as a point; a daily-summary series draws every day as a point |
| Stale | "STALE · last data 06:02" names the start of the last bucket with data, and its date ("Oct 30, 06:02") when that was on an earlier local day; on daily buckets the day alone ("Oct 30") |
| Gap words | A null run reads its own start and end ("No data 02:10–02:40"), with both dates when it ends on another local day ("Oct 5, 00:00–Oct 6, 00:00"); on daily buckets its days ("No data Oct 5–Oct 7") |
| Data table | One row per bucket: the time with its offset, each series, the band and its source, and a note (the null run's "No data …" on its first row, the retention words on the first row, STALE on the first row after the last value, a `daily.*` bucket's reason). A second table lists the setting steps, with the bounds in its caption. Rows build only once the disclosure opens |
| CSV | `time (America/Denver)` as ISO 8601 local time with the offset ("2026-11-01T01:30:00-06:00"), then one `<key> (<unit>)` column per plotted series with the raw `avg`; a UTF-8 byte-order mark and CRLF rows. LinkedPanels offers one file for the window, rows on the union of the panels' bucket starts |

### 5.6 As built (VD-07)

The lanes and the bars are `dashboard/app/charts/equipment-lanes.tsx`, `plan-lanes.tsx` and `minutes-by-zone.tsx`,
with `lanes-draw.ts`, `causes.ts` and the line register `dashboard/app/lib/lines.ts` (#1127). Where they settle a
detail §5.2 leaves open, or differ from it:

| Topic | As built |
|---|---|
| Lane layout | Rows are HTML at a fixed pitch: the label (the API's words, cut with an ellipsis and whole in `title`) and the right-hand text are text, and an x-only uPlot sits over the plot column and paints each row's band at the same pitch in a `draw` hook, so canvas and HTML rows line up and the reserved height is the rows' own. A spacer under the rows holds the time axis (54 px above 860 px, 32 px at and below) |
| Equipment rows | Design order whatever order API-05 sends; a key the order does not list follows its group's own. A lane with no `label` reads its key, never a made-up name. `_intent` keys are dropped from the rows, the key and the table. The now line draws when the page passes now |
| Equipment marks | ON fills the row's band; a sensed row's ON is a 1.5 px outline; unknown is 1 px `--muted` diagonals every 6 px, only where the API says unknown; off paints nothing. A wet line's run of pulses with one cause is named once, after the run, and an unknown span after it ("unknown 02:10–02:40", or "unknown" where only that fits); a label that would overlap a span or another label, or pass the next span, is dropped |
| Equipment text | Each row ends with the state at the window's end, on time and starts ("off · 80 min · 1"; runtime alone at 860 px and below); a span already on when the window opens is no start. Idle rows fold into one "N idle rows" button (`aria-expanded`, `aria-controls`) at the end of the rows, and open below it. The data table lists every span of every row: equipment, state, from, until, duration and a wet line's cause in words. A key under the lanes names each cause drawn, the hatch (unknown) and the outline (sensed) |
| Tooltips | A lane or bar tooltip shows only for the row under the pointer. A cursor moved by another chart on the sync key has no row there: that chart shows its own tooltip, and the lanes only move their cursor line |
| Zoom | A drag on the lanes zooms them with every chart on the sync key. The lanes show their own "Reset zoom" while zoomed (`zoomControl`, default on), so a page with no panels (Equipment, Plan) is never left zoomed; a page whose panels offer one passes false |
| Plan rows | Each row is a header line (label, then the header words: PLANNED · AI, Forecast rule · system, MANUAL · Iris skips · Jason since 10/30, or YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan; two lines at 860 px and below) over a 24 px strip, with the device value in words at the strip's left ("—" with the API's reason in its title, the tooltip and the table). YOUR VALUE prints the value at its unit's decimals without the unit, which the device value carries. A row with no waypoint drawn and no desired or manual value has no header words |
| Plan strip | The device value holds as a solid `--c-setpoint` line from now to the first drawn segment, the plan's end or the horizon. Effective waypoints are dashed [8,4] in `--ink` (a lane has no series colour), superseded ones dashed `--c-bound`, each labelled as TimeSeries labels its steps ("1.70 kPa PLANNED · AI", "1.80 kPa SUPERSEDED"; the value or SUPERSEDED alone when the label does not fit). A manual row draws its held value as a solid `--c-bound` line and no waypoint. The plan's end is a dashed `--muted` marker labelled "Plan ends 17:30 · band/default" in an 18 px band above the rows, the words after the dot being what the screen says takes over. No x grid, so the header words stay clear |
| Minutes rows | Wall, Center, South, West, then Fog (humidification), one 84 px bars chart each (the last 112 px, with the day labels). The four watering zones share one minutes scale so their bars compare; fog has its own. The y axis is whole minutes at one width on every row, so the rows' days line up. Stacks use uPlot's bands (each cause's bar runs from the total below it). Drag zoom is off on the bars (UPlotChart `zoom={false}`) |
| Minutes marks | A no-data day is hatched the full plot height; a partial day has a dashed `--ink` top edge; today's still-planned minutes are a dashed `--muted` outline on today's bar; the cap is a dotted `--c-bound` line across the row. The right reads "today 46 min · cap 120", with "(partial)" while today is partial |
| Minutes words | The tooltip and the data table split each day by line and cause ("L04 Center mister · VPD · 46 min · 3 gal"); the line's name comes from the screen (API-IRR-01 `name`), and without it the line id stands alone. L03's supply minutes and a report grouped by cause draw no bar |
| Group by (VD-15) | `group` cuts the same report into rows: zone (the default, above); line, one row per line in the report in L01–L11 order, L03's supply row included, and L11 as fog; cause, one row per cause in the cause table's order, then fog. Fog keeps its own scale and the humidification note in every grouping; by cause L03 is left out, as each feed would count twice. `caps` and `planned` are keyed as the rows are (a zone, a line id or a cause). By line needs a report by line; by cause works from a report by line or by zone |

## 6. Responsive rules

| Pattern | 390 px | 1440 px |
|---|---|---|
| Shell | 16 px gutter; no horizontal page scroll (weather's 390 px capture overflowed; the dashboard must not) | 80rem shell, 40 px gutter |
| Nav | Fixed five-tab bottom bar, 56 px plus the safe-area inset; the page pads its bottom to clear it | Inline top nav |
| Hero | One column; 4 stats 2-up | Value left (1fr), stats right (1.6fr), 4-up |
| Cards, tables | One column; tables in a `.table-wrap` scroll region | `.grid-3` or `.grid-auto`; full tables |
| Setting rows, line cards | Stacked cards; Edit, Run, Stop all water and Acknowledge full width | Table rows; three-column cards |
| Schedule week | Day switcher: one day, every line | Line × day grid |
| Time charts | 220 px; time-only labels; legend in a disclosure | 320 px; two-line labels; legend below |
| Lanes | 24 px rows, 6rem labels, runtime only | 20 px rows, 10rem labels, state, runtime and starts |
| Drawer / dialog / toast | Full-screen sheet / full width minus 32 px / full width above the bar | 28rem right / 32rem centred / 24rem bottom right |

## 7. Accessibility basics

Target: WCAG 2.2 AA, checked in VD-08 by Playwright and axe (`wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`) against the
fixture API at 320, 768 and 1440 px, with no-horizontal-scroll assertions at 360 and 390 px (also on WebKit) and a
reduced-motion check.
- **Contrast:** text at least 4.5:1; focus ring, input borders, chart lines and lanes at least 3:1 (§2, §5.1).
- **Keyboard:** every action reachable; the focus ring is never removed; drawer and dialog use native `<dialog>` (focus
  trapped and returned to the opener); targets 44 px (24 px minimum, SC 2.5.8).
- **Colour never alone:** badges carry words, lanes carry labels, requested and planned differ by line style.
- **Forms:** `<label for>`; hints and errors linked with `aria-describedby`; `aria-invalid`; errors beside the field.
- **Live regions:** success toasts polite, failures assertive, each once; ticking ages and pending seconds are silent.
- **Charts:** `role="img"` with an `aria-label` naming what is plotted and that a table follows. Every chart has a "Data
  table · N rows" disclosure (local time with offset and each series; lanes list device, on, off, duration; minutes
  list day × zone) and the §5.4 summary. The table is the keyboard and screen-reader equivalent; a keyboard chart
  cursor is Later.
- **Times** are `<time dateTime>` with the full local form in `title`. **Motion:** `prefers-reduced-motion: reduce`
  turns off every transition (weather's rule).

## 8. Copy weather now, share later

Copy from jvallery/weather@249fba3 `web/` (MIT; fonts OFL-1.1) into `dashboard/` now, keeping paths and SPDX headers:
- `app/styles.css` sections: tokens, base, shell, type helpers, page structure, cards, tags, buttons, chips, filters,
  tables, kv, hero and stats, event rows, plots and summary stats, the one-rule error page, reduced motion (not
  camera, keogram, calendar, stills, almanac, search or embed rules).
- `app/lib/format.ts`, `polling.ts`, `use-current.ts` and `chunk-reload.ts` (which `entry.client.tsx` imports; it
  reloads a tab held across a release once, as the image keeps no previous assets); as bases,
  `app/components/common.tsx`, `site-header.tsx` (shell), `current-conditions.tsx` (hero), `events.tsx`,
  `history-plot.tsx` and `linked-plots.tsx` (charts); `public/fonts/` with the two Plex files and both OFL notices.
  `dashboard/VENDORED-FROM-WEATHER.md` records the commit, paths and changes.

No shared package yet: two apps in two GitHub organisations, no estate npm registry, and the dashboard diverges (control
components, chart tokens). When a third app wants this look, extract the tokens first, then components.

## 9. Where it is built

VD-01 copies weather (§8, §2), VD-03 builds §4 to §4.2 (`AlertRow` included) and §4.5, VD-04 §3, VD-05 §4.3 and
§4.4, VD-06 and VD-07 §5, and VD-08 the §7 checks ([execution plan](execution-plan.md)); the screen issues build the
§4.6 composites.
