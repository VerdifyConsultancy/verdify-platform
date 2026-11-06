# Dashboard design system

dashboard.verdify.ai is a sibling of weather.vallery.net: it copies weather's stylesheet with the tokens unchanged (IBM
Plex, sticky white header, ink hero, cards, chips, pills, uPlot charts) and swaps the vallery.net V for the Verdify
mark-as-V lockup with a `· DASHBOARD` eyebrow. It adds only what an operator app needs: five sections at every width,
with a five-tab bottom bar on phones; one page header, section header and card frame; the badges REQUESTED, CONFIRMED,
NOT CONFIRMED, HELD, MANUAL, AI and PLANNED, one pill a head; number fields that show min, max and grid step; one edit
drawer; one confirm dialog, for water runs only; toasts; honest empty, stale, unknown, not-reporting and API-down
states; SVG micro-visualizations (band bars, meters, sparklines, deltas, status strips, time tracks); and uPlot charts
(one chart stack on one time axis, small multiples, equipment lanes, plan lanes, minutes by zone, statistics).

Status: v3, built (2026-11-04). The [redesign](redesign.md) applied D-UX-01 to D-UX-13 to §1, §3 and §4 to §7; §2's
tokens are unchanged and no colour token was added.

[README](README.md) · [product](product.md) · [screens](screens.md) · [redesign](redesign.md) ·
[architecture](architecture.md) · [irrigation](irrigation.md) · [design system](design-system.md) ·
[execution plan](execution-plan.md) · [OpenAPI](openapi/dashboard-api.v1.yaml)

## 1. The answer

| Area | Decision |
|---|---|
| Look | weather.vallery.net, copied from jvallery/weather@249fba3 `web/app/styles.css`; tokens byte for byte |
| Deltas | Two accessibility fixes (focus ring, input border) as rules, not token edits; new tokens only for badges and charts |
| Brand | Verdify leaf mark as the V, "erdify" in Plex Sans 620, `· DASHBOARD` eyebrow |
| Nav | Five sections at every width (D-UX-01): weather's top nav above 860 px; a fixed five-tab bottom bar with icons at 860 px and below ([screens](screens.md) §1.1) |
| Type | IBM Plex Sans (variable 100-700) and Plex Mono 400, self-hosted, copied from weather |
| Units | Fixed: °F, % RH, kPa, gal, min. No °C toggle in v2 |
| Theme | Light only. New colours are CSS custom properties and charts read tokens, so dark can be added later |
| Charts | uPlot 1.6.32 (weather's pin), lazy-loaded, colours read from tokens |
| Code | Copy weather files into `dashboard/` now with provenance; a shared package only when a third app needs it (§8) |
| Built with the redesign | Sparklines and the other micro-visualizations, compare periods, the event (annotation) rail, day-by-day status strips, five navigation icons (D-UX-10, and no other icon set) |
| Later | Dark theme (the Lab's dark tokens on these names), command palette, keyboard map, °C toggle, keyboard chart cursor, Day Arc, a general icon set, palette checks in CI |

Every screen follows five rules:
1. **Lead with the answer**; detail goes in `<details>`.
2. **State once**: units and cadence in the panel eyebrow or header row ("MEASURED · EVERY 60 S"); the time zone once
   per page, in the footer ("Times are America/Denver"), on chart axes and in every `<time title>` (D-UX-08).
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
  --cause-vpd:#c2410c;--cause-schedule:#8a5a00;--cause-prep:#65731f;
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
  --aside:22rem;--rail:15rem;--tap:2.75rem;--bar-h:3.5rem;--header-h-phone:3.25rem;
  --z-raised:3;--z-sticky:10;--z-nav:20;--z-popover:30;--z-toast:40;
}
```

The section files load after the §2.2 block, so none may redefine `:focus-visible` or the input borders, and none
carries a colour literal but white (`app/styles/sections.test.ts`). WP0.1 moved the screen rules out of `styles.css`
unchanged; no moved rule sets a property a §2.2 rule sets on the same element at the same specificity, and the rules
whose relative order changed between files style different screens' elements. The breakpoints are declared once in
`app/lib/narrow.ts` (`PHONE_PX = 600`, `NARROW_PX = 860`, `WIDE_PX = 1200`) and mirrored by the media queries.

## 3. Shell and header

Weather's header, nav and footer, with a Verdify lockup, rebuilt for the five sections of [redesign](redesign.md) §2
(D-UX-01). The section model is one file, `app/lib/sections.ts`: the nav, the phone bar, each page's tabs, its
breadcrumb and its document title read it.

```text
1440 │ [leaf]erdify · DASHBOARD    Now   Water   Graphs   Activity (2)   Control              [OPERATOR  Jason  v] │
     │ ▌Controller not reporting since 03:12. It keeps running VPD misting and its caps.          Equipment > │  only while a fault is true
360  │ [leaf]erdify · DASHBOARD [OPERATOR v] │   52 px
     │ (ic)Now │ (ic)Water │ (ic)Graphs │ (ic)2 Activity │ (ic)Control │   fixed bar: 56 px plus the safe-area inset
```

| Part | Spec |
|---|---|
| Lockup | Link `.brand`, `aria-label="Verdify dashboard, home"`. The glyph is `#verdify-v-glyph` from `site/quartz/static/brand/verdify-wordmark.svg`, inlined as JSX without its `<style>` and Inter `<text>`: leaf path `.brand-leaf`, ink path `.brand-mark`, dot `.brand-dot`. About 1.2em tall, it is the V, so no gap follows it; then "erdify" in Plex Sans 620 at 1.1rem, then `<small> · DASHBOARD</small>` (weather `.brand small`). Never animated. Favicon: `site/quartz/static/brand/favicon.svg`. |
| Sections | Now · Water · Graphs · Activity · Control, in that order at every width (`SECTIONS`, `aria-current="page"` on the section that owns the path). Control's link goes to Settings and it owns `/settings`, `/plan` and `/equipment`. Above 860 px the top nav lists the five with weather's 2px forest underline and never scrolls sideways. At 860 px and below a fixed bottom bar (`navigation "Sections, phone"`) holds the same five, each a fifth of the width and at least 44 px: an inline SVG icon (`components/icons.tsx`: gauge, droplet, line chart, bell, sliders; `currentColor`, 1.75 px stroke) over a one-word label, the current one ink with a 2px forest top rule. There is no More and no `/more`. |
| Count | API-02 `alerts.open`, the one open-alert count every surface uses (unacknowledged `v_open_alerts` rows, repeats of one `alert_type` and `sensor_id` counted once), as a pill after "Activity" (`.nav-count`) and on the phone's Activity icon. Its fill is the worst open severity (`data-severity`): critical `--fault` with white, warning `--tint-warn` with `--warn-text`, info `--field` with ink. Zero draws no pill; the link still reads "Activity 0 open alerts" to a screen reader. Unknown (no answer yet, or the API down) draws nothing. |
| Account | The account button at the header's right (`components/account-menu.tsx`, a `Menu` disclosure): the role pill (OPERATOR or VIEWER) at every width and the display name from 1100 px. It opens to "Signed in as Jason (jason)", "Operator: can change anything" or "Viewer: read-only", and Sign out (the POST form). Before API-01 answers it reads "Account" and still holds Sign out. No units or theme toggle, no Stop, no freshness, no search in the header. |
| Banners | The one fault banner, worst first (API down, ingestor not running, controller not reporting, leak), is a row inside the sticky header while it is true, so it never scrolls away (two lines at most on a phone, its link in view). Warn banners sit in flow at the top of `<main>`, above the page header, one line each. Each links to the page that explains it. [Screens](screens.md) §1.2 lists the banners, when they show and their words; §4.5 has the states |
| Footer | Weather `.site-footer`: "Verdify dashboard · build `<sha>` · API `<sha>`", "Times are America/Denver" (D-UX-08), graphs.verdify.ai ↗, lab.verdify.ai ↗, IBM Plex licences |
| Page grammar | `PageHeader` (`components/layout/page-header.tsx`): the eyebrow is the breadcrumb (the section, a link to its default view except on that view; a focused state reads section › view, both links), one h1 named by the view, the status line (one `.meta` sentence of state, its height reserved), at most two actions, then the section's tabs (`SectionTabs`, nav chips with `aria-current="page"`; sticky under the header on Water's phones, beside Stop all water). Then sections and cards ([redesign](redesign.md) §3, §4). The ink hero appears on Now only, which has no page header. Every route shares the header's one centred container, `.shell`, with the footer (decisions log, 2026-11-04). |
| Sign-in pages | The same header without nav or tools; one card with the message and a forest "Sign in" button |

## 4. Components

Weather classes are reused as they are; the dashboard's own rules live in `styles.css`'s dashboard section and in the
section files of §2.4. React components live in `dashboard/app/components/`: the layout primitives in `layout/`, the
micro-visualizations in `data/` (§4.7), and each screen's composites in its own folder (`now/`, `activity/`,
`graphs/`, `water/`, `schedule/`, `control/`, `equipment/`). The [redesign](redesign.md) §3 and §4 set the grammar
they share: one page header, one section header, one card frame, one `Chips`, one `Table`, one `.meta` line, a 6-step
type scale and a 4 px spacing scale.

| Component | Base | Spec | Used on |
|---|---|---|---|
| `Page`, page rhythm | `.page`, `.page-toolbar` (primitives.css) | Every view's body stacks its blocks at the section gap (`--space-6`, `--space-5` on phones); cards within a block sit at the card gap (`--space-4`, `--space-3`). A view's toolbar (its range, period, filters or view switch) heads what it controls, `--space-4` above it, in a `.page` stack as in plain flow | All |
| `PageHeader`, `SectionTabs` | `.page-header`, `.page-tabs`, `.section-tabs` | The eyebrow as breadcrumb, one h1, the status line (`.page-status.meta`, its height reserved), at most two actions (under the status line on phones, a primary one full width), then the section's tabs (a labelled `nav` of nav chips, `aria-current="page"`); `stickyTabs` and `tabsEnd` give Water its sticky row with "■ Stop all" (§3) | Every view but Now |
| `SectionHeader`, `Section` | `.section-header`, `.page-section` | The only section heading: an h2 at weather's size, an optional meta (`.meta`, no leading separator), optional chips and one "All … ›" link. The eyebrow is never a heading | Statistics, Water → History, the rules list |
| `Card` | `.card`, `.card-head` | Weather's frame: 1px `--line`, radius .625rem, xs shadow, 1rem padding. The head: an optional eyebrow, the title (h2, or h3 inside a section) with at most one pill (§4.1), then actions, one "All … ›" link and a ⋯ `Menu`; then the body and a `.meta` foot. Abnormal is a 3 px `--amber` or `--fault` rule on the left, never a tinted background, and drops while the API is down. A card that is one link holds no other control | All |
| `Panel` | `Card` | The data card: the same head, plus its state machine (§4.5): a skeleton at its reserved height, its age when ready, the empty words, STALE with its age, the not-reporting line, its own error and Retry, "Busy, retrying in 5 s" over the old render; a render failure stays inside it | All |
| `ListCard` | `Panel` | A head with the title and its count ("Needs someone · 2", shown once per page) and one "All … ›" link, then rows, or one empty line with one widening action | Now (Needs someone), Activity (Needs action, Recent activity) |
| `ObjectRow` | `.object-row` | A thing as one row: title (h3, its link the only way in; the row is never itself a link), kind tag (CLEAN, FERT, a severity word), one state sentence, one pill, one visual (a strip, meter or sparkline), the meta line, then one primary action chosen by state, Stop only while the thing runs (D-UX-06, 2026-11-06) and ⋯. Rows divide with a 1px `--line`; on phones the actions take the full width | Water lines, alerts, schedule rules |
| `MetricTile` | `.card.metric-tile` | A label, the value at `--fs-xl` in tabular figures with its unit small (`.metric-unit`), a `Delta` line, one visual and the meta line, at one reserved height in every state | Statistics, Water → History |
| `DataList` | `.data-list` | Short mono terms over their values, in pairs that wrap: what a " · " chain longer than three items becomes | Popovers, Equipment |
| `Chips` | `.chips`, `.chips-sm` | One chip row with three kinds: `nav` (tabs and ranges, links with `aria-current="page"`), `filter` (links that narrow a list, `aria-current="true"`) and `toggle` (buttons with `aria-pressed`). The current or pressed chip is filled ink. Toolbar chips are the small ones (36 px; 44 px on phones) | All |
| `Menu` | `<details>` `.menu`, `.menu-panel` | The ⋯ menu and the account menu: a disclosure, not an ARIA menu, holding links and buttons, each a 44 px item; Escape closes it | Water rows, schedule rules, Graphs' Preset and Layers, the header |
| `Popover` | native `<dialog>` `.popover` | Anchored under its opener from 861 px (placed through CSSOM), a bottom sheet in a modal dialog at 860 px and below; focus moves in and returns to the opener; Escape closes it | Schedules (block, Key) |
| `StopAllWater` | `.btn-stop` | One shared component in exactly three places: Now's quick actions, the Water tabs row ("■ Stop all" on phones) and an `irrigation_fence_breach` alert. One tap, no dialog, never disabled by the writes flag | Now, Water, Activity |
| `Table` | `.table-wrap`, `caption` | Focusable scroll region (`role="region"`, `tabindex="0"`, labelled); fixed group order; numbers right-aligned in tabular figures, a shared unit in the column head; stacked rows on phones where the screen says so; no column sort | Settings, Water → History, Statistics |
| `ScrollRegion` | `.l-scroll-x` | A labelled, focusable horizontal scroll region for a wide table, the week matrix or a long strip, so the page itself never scrolls sideways | Schedules, Statistics |
| `EventRow`, `AlertRow` | `.event`, `.object-row` | `EventRow` is one log entry on one line: the mono time, who wrote it in text ("Jason · dashboard"; "iris" or "steward · agent" with AI; "forecast-engine · system", never AI), the action and its target with before → after, the reason and the outcome. `AlertRow` is an `ObjectRow` with a 3px left rule in severity colour (critical `--fault`, warning `--amber`, info `--muted`), the severity word first, the facts with a repeat count, "Details ›" and Acknowledge with an optional note | Activity, Now |
| `Kv`, `Details` | `.kv`, `details`/`summary` | Facts; progressive detail with a 44 px summary | All |
| `Button` | `.btn`, `.btn-primary` | 44 px; verb and object ("Run wall drip", "Save"); busy reads "Sending…" and is disabled; no spinners. `.btn-stop`: fault fill, white text (6.54:1), never confirmed; a line's Stop shows only while its relay is on or unknown (D-UX-06, changed 2026-11-06; the quiet outline is retired) | All |

### 4.1 Badges

Text always; colour is the second channel. AI is ink and mint, not weather's warn-tinted AI badge: warn means REQUESTED.
A row or card head carries **at most one pill** (D-UX-07), by this priority: NOT CONFIRMED, REQUESTED, HELD, STALE,
FAULT, MANUAL, then CONFIRMED. CONFIRMED shows only while the newest write is under 10 minutes old; afterwards the
value and its age stand alone. AI and provenance (MEASURED, PEER, HOUSE, DERIVED, NO READBACK) are text marks in the
meta line, mono and muted, amber only when the provenance is the news (a zone fallen back to its peers); log rows keep
the AI badge beside the actor.

| Badge | Meaning | Class (fill / border / text) |
|---|---|---|
| REQUESTED | Saved; waiting for the controller to report it (`cfg_*` readback, or the program hash) | `.status-pending`: tint-warn / warn-border / warn-text (7.81:1) |
| CONFIRMED | The API reports a matching readback, or the program that carries the value; shown for 10 minutes after the write | `.status-complete`: tint-ok / ok-border / forest (7.14:1) |
| NOT CONFIRMED | No match 5 min after the request: the ingestor's `setpoint_unconfirmed` window (`ingestor/tasks/_common.py`, section 16). Display only; it blocks nothing; its reason shows beside it (§4.2) | `.status-failed`: tint-fault / fault-border / fault (5.61:1) |
| HELD | The platform applies another value on purpose (a dispatcher clamp or an armed experiment); the reason shows beside it (§4.2) | New `.badge-held`: white / ink / ink (16.19:1) |
| NO READBACK | The API reports no readback for the setting: it is sent, never confirmed. A text mark on a setting row | `.tag` base |
| MANUAL | The item's manual flag is on (a setting, schedule or line): Iris skips it; people and other agents can still change it | `.badge-manual`: manual-bg / manual-border / manual-text (8.54:1) |
| AI | Last written by Iris or another agent (source `iris` or `agent:<name>`); the name follows in text. System writes, such as forecast rules, never carry it. A badge in log rows and the plan lanes' header words; a text mark elsewhere | New `.badge-ai`: ink fill, mint text (10.36:1) |
| PLANNED | Future intent: an Iris plan waypoint or a schedule occurrence | New `.badge-planned`: no fill, dashed muted border |
| SUPERSEDED | A plan waypoint that will not apply, because a newer value holds until Iris's next plan (the API marks it) | New `.badge-superseded`: `.tag` base, muted text (5.01:1); its chart segment is muted too |
| STALE | Older than its limit, or from a controller that is not reporting (§4.5) | `.status-stale` |
| CLEAN / FERT | Water type of a line or run | `.tag` with a leading dot in `--eq-water` / `--eq-fert` |
| ON / OFF | A schedule rule's enabled state for a viewer, and a relay's state before the irrigation firmware | Ok family / base |

States that are not pills read as words: a line's state is its row's state sentence, with a 0.55rem dot in its cause
token while it runs; an actuator's ON, OFF or UNKNOWN on Equipment is a mono word with a dot in its equipment token,
only UNKNOWN muted and hatched ([screens](screens.md) SCR-07).

### 4.2 Requested → confirmed

```text
VPD Mister Engage   mister_engage_kpa
  1.60 kPa    [0.50 ──●────○─────── 2.50]   REQUESTED                                   [Edit]
  jason · manual · "Vandas drying" · 06:47 · → 1.55 · 40 s · next 1.70 at 13:00 AI
```

The device value (readback and age) is the large number, with a bounds bar (the allowed range, the value as a dot,
the next plan as a hollow ring, the request as a dashed amber ring) and the row's one pill; the meta line under it
says who wrote the value in force, what Iris plans next and, while pending, the request. For a value the irrigation
program carries, the device value is the program's on the controller ("On controller · rev 42: 360 gal"). The API
returns the state (the UI never compares binary32 readbacks) and its reason in words, under HELD and NOT CONFIRMED
always and under REQUESTED once the request is 60 s old ("Applies with the irrigation firmware" at once): clamped
(with the applied value), controller heap low, push failed, ingestor not running, controller not reporting or held by
an experiment; else "Sent 06:02; no matching readback yet". REQUESTED rows poll every 5 s; no Send again, as the
dispatcher re-sends.

### 4.3 Form controls and the edit drawer

```text
┌ Edit setting ──────────────────────── [×] ┐   1440: 28rem drawer on the right
│ PLANNER POLICY · mister_engage_kpa         │   390: full-screen sheet, actions pinned
│ VPD Mister Engage                          │
│ 1.10 kPa  [0.50 ──●──○──── 2.50]  REQUESTED│   the device value, bounds bar and one pill
│ → 1.20 · 40 s · jason · next 1.30 at 13:00 │   the request, writer and plan lines
│ New value  [−][ 1.20       ] kPa [+]       │
│ Allowed 0.50–2.50 kPa · step 0.05          │
│ [x] Manual: Iris skips this                │
│ Reason (optional) [                      ] │
│ [Release to auto]       [Cancel] [Save]    │
│ Change history ▸ · Activity for this setting › · Graph this setting › │
└────────────────────────────────────────────┘
```

| Control | Spec |
|---|---|
| `NumberField` | `<input inputmode="decimal">` with a unit suffix; the hint shows the registry `min`–`max` and the entity step; − and + move one step. The client checks only min and max. On submit the server snaps to the grid; Save closes the drawer, so the snapped value shows in the Save toast ("Requested 1.20 kPa for VPD Mister Engage (snapped from 1.22)"). Errors sit beside the field ("Must be 0.50–2.50 kPa") with `aria-invalid` and `aria-describedby` |
| `Switch` | `role="switch"`, `aria-checked`, visible ON/OFF word; `sw_*` settings, line enables, mister associations, schedule rules |
| `Select` | Native `<select>` for enum settings (`enum_values`) |
| `TimeField` | "At [time]" or "[Sunrise ▾] + [30] min", with today's resolved clock time beside it; weekday chips with "Daily" and "Weekdays" |
| `ManualToggle` | Checkbox "Manual: Iris skips this", ticked by default on every dashboard edit form, for settings and irrigation items alike; unticking it saves manual off. An inline switch (line enable, VPD control, Schedule) also saves manual on; the item's drawer can untick it. Quick actions (Run, Stop, Pause, Resume, Clear fault, bursts) send no manual value and never change it |
| `Reason` | Optional one-line text, stored with the write |
| `EditDrawer` | One drawer for settings, schedules and line options: native `<dialog>` with `showModal()`; focus goes to the first field and back to the opener; Escape and × close without asking; it lives in the URL (`?edit=`, `?schedule=`, `?options=`…), pushing when it opens and replacing when it closes, so Back closes it. Save sends once with one `Idempotency-Key`, closes, toasts; the row then shows REQUESTED → CONFIRMED. A 422 keeps it open with the server's message beside the field |
| `SettingDrawer` | `components/control/setting-drawer.tsx`: the setting's `EditDrawer`, opened in place on Settings, Plan and Equipment. Top: the device value, its pill, the bounds bar and the request, writer and plan lines. Then the field, the band preview (a crop-band anchor or width), Manual, Reason, Release to auto, Cancel and Save. Then "Change history" (the chart and its entries in words, with Revert) and "Activity for this setting ›"; "Graph this setting ›" (`/graphs?set=<param>`) shows only when the setting's unit has a Graphs panel |

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

Every card, primitive and visual renders every state at its reserved height ([redesign](redesign.md) §4.3).

| State | When | What shows |
|---|---|---|
| Toast | After a write | Bottom right, 24rem (on phones full width, above the bottom bar). Success is polite, 5 s ("Requested 1.20 kPa for VPD Mister Engage"); failure is `role="alert"`; both failures and results that carry guidance or buttons (Stop's) stay until dismissed. Activity keeps the record |
| Loading | First fetch | Skeleton at the card's reserved height; a refetch keeps the old render; no spinner or shimmer. A page's status line keeps its height |
| Empty | Query ok, no rows | One line of what would appear here, plus one action that widens it ("No runs in 7 days · Show 30 days"). An alert list says "No open alerts" only while the ingestor heartbeat and `climate` are fresh; otherwise "In-app alerts may be missing: ingestor not running since 06:10" (heartbeat stale) or "… no data since 06:10" (controller not reporting); "Alerts unavailable" when the read failed; and "… can't reach the greenhouse data" once API-02 has answered and then gives no freshness |
| Stale | Sensor values: age > 2 × `expected_interval_s` (the `v_sensor_staleness` rule, migration 198). Equipment and `irr_state` report only on change, so they read unknown only while the controller is not reporting (the API decides) | Value in muted, STALE badge, "Last data 14 min ago (06:02)"; the dot turns amber; visuals muted |
| Unknown | No value, or a state the API cannot vouch for | "—" with its reason; visuals hatched, never drawn as off or 0 |
| Not reporting | The ingestor heartbeat (`system_state`, every 60 s) older than 180 s; or, with it fresh, the newest `climate` row older than 300 s (the `GET /health` threshold in `api/main.py`) | The fault row in the header on every page ([screens](screens.md) §1.2): "Ingestor not running since 06:10. The controller keeps running on its own.", else "Controller not reporting since 06:10"; device values STALE and no verdict; never "Controller offline". Writes still save and apply once both are back |
| API down | Two failed API-02 polls in a row | Neutral everywhere (`useApiDown()`): no green fill, no verdict, no "in band", "All OK" or "CONFIRMED"; band bars, meters, strips and tracks go neutral; abnormal rules drop |
| Panel error | One query fails | "Couldn't load runs." with Retry in that card only (per-card error boundary) |
| Busy, signed out | 429 or 503 with `Retry-After`; 401 | "Busy, retrying in 5 s" over the old render; the session-ended card, whose Sign in returns to the same URL |
| Viewer, changes off | Role viewer; or `writes_enabled: false` (`DASHBOARD_WRITES_ENABLED`, off until the setting-writes release, VD-28) | Viewers get the same cards with no write controls; menus keep their links. With changes off, operators see controls disabled beside the warn banner "Changes are switched off; reading still works"; Stop and Stop all water stay live |

### 4.6 Domain composites (built in the screen packages)

| Composite | Made of | Notes |
|---|---|---|
| Now's hero (`now/hero.tsx`) | The ink hero (weather's `.hero`, radius 1rem): the answer as the h1 (worst first; wording in [screens](screens.md) SCR-01), the hero value with "Updated N s ago", three `BandBar`s on ink (temperature, VPD, RH), "In band today" against yesterday, the status line (mode, Iris, outdoor, occupancy, each a link where it is explained) and "All readings" | Now only; it replaces weather's eight stat tiles |
| Quick actions (`now/quick-actions.tsx`) | `StopAllWater`, then the three burst buttons (a name over its length on a phone) | Operators only; before the irrigation firmware Stop all water and the arrives line |
| `SettingRow` (`setting-row.tsx`) | Two lines: label and key, the device value at `--fs-lg`, the bounds bar, one pill and Edit; then the meta line (§4.2) | Table row from 861 px; a stacked card below, its Edit or Details link covering the row |
| `LineCard` (`line-card.tsx`) | An `ObjectRow` per line: "L01 Wall drip", CLEAN or FERT, one state sentence, one sync pill, the today strip with its next run, Run… by state, the state-aware Stop and ⋯ (enable switch with its check sheet, Options…, Pause until…, Activity, Runs, Graphs) | Inside its zone card on Water → Today (`water/zone-card.tsx`, whose head carries the zone's minutes meter and `MisterAssociation`). Before the irrigation firmware Stop works on every line and Run on L01 only (§4.4) |
| Schedule views (`schedule/`) | `DayTimeline` (rows by manifold, blocks as long as their schedules, night, the now rule, valve ticks, pause bands, cap meters and [+]), `WeekStrip` (seven days, past outcomes and future counts), `WeekMatrix` (lines × days in a scroll region), `Agenda` (by day, NEXT, skips hatched), `RulesList` (by line, "N of 24" with a meter) and `BlockPopover` | One day model (`schedule/model.ts`) feeds all of them; the editor's draft draws dashed on the timeline. They replace `ScheduleWeek` |
| `MisterAssociation` | Two `Switch`es per mister zone, "VPD control" and "Schedule", and the zone's VPD source; a change reads REQUESTED until the controller reports the program that carries it, then CONFIRMED | In the zone card's head; both off reads "This zone will not mist automatically" |
| Bursts (`burst-panel.tsx`) | The burst buttons, Clear, "More actions" with Restart controller, and what runs from the readback | Equipment's Bursts card; Now's quick actions reuse its commands |
| Equipment board (`equipment/`) | The board's group cards, Flags, the Controller card (heap meter against the 32 kB floor), Why, the light controls and the daily runtime | Equipment |
| Plan health (`planner-health.tsx`, `control/`) | Trigger marks for 36 h, the next trigger, the 14-day `StatusStrip` and the ledger in a disclosure; the plan card's validity bar | Plan |

### 4.7 Micro-visualizations

SVG or HTML in `components/data/` ([redesign](redesign.md) §4.6), never uPlot, so Now loads no chart code
(`components/data/*` imports nothing from `app/charts/`). SVG uses attributes and token classes, and HTML positions are
CSSOM custom properties; neither uses a style attribute. Each is `aria-hidden` beside the same numbers as text, or
`role="img"` with an `aria-label` that states them. Each takes ready, loading, empty, stale, unknown and error at its
final height, and goes neutral while the API is down.

| Primitive | Anatomy |
|---|---|
| `BandBar` | One track, the band box and the value dot; nothing else is drawn (today's low–high is in the name only, Jason 2026-11-05). On white: a 2 px `--line-strong` track, the band as a 10 px `--c-band-wash` box with `--c-band-edge` ends, the value as a 10 px ink dot with a white ring. On the hero: a dimmed `--ink-muted` track, the band as a 12 px `--c-band-edge` box (5.3:1 on ink) with mint ends, and a 12 px mint dot ringed in ink. The scale is the band plus one band width each side, fixed, so only the dot moves; a value off scale clamps with an arrowhead; unknown hatches the track; RH has no band box and reads on 0–100% |
| `Meter` | A 6 px `--field` track with a `--line` edge, filled in the domain token (`--eq-water` drip minutes and gallons, `--eq-mister` mist minutes, `--eq-fert` feeds, `--muted` otherwise), planned minutes as a dashed outline, the cap as a 2 px ink tick that turns `--fault` at or over it. Its words ("14 / 120 min", "cap reached", "+12 min over") count minutes in whole minutes on both sides, as the caps are set |
| `Sparkline` | A 1.5 px line in the metric token over the band, gaps broken, the last point a dot; or bars in the domain token. 28 px in tiles, 20 px in rows |
| `Delta` | "▲ 1.3 pts vs previous 30 d" in ink; the arrow and the words carry direction, never green or red |
| `StatusStrip` | Day cells at least 8 px wide with 2 px gaps; `data-level` 0–3 is `--tint-ok`, `--tint-warn`, `--amber`, `--fault` (quiet when good, loud when bad); no data a `--line` hatch, a partial day a dashed outline. Each cell is a link or button named in words, with a legend and an equivalent table |
| `TimeTrack` | HTML rows (28 px, 24 px on phones) over one local time scale (`lib/time-scale.ts`, DST-correct): done spans solid in their token, planned dashed, skipped hatched, unknown a muted hatch; night washed; the now rule; Iris and plan markers dashed with labels; an optional end column. The track is one tab stop whose arrow keys move along its spans |

### 4.8 As built

`SettingRow` (VD-12, `dashboard/app/components/setting-row.tsx`) shows each row as API-09 serves it and decides
nothing. The device value is the readback with its unit; STALE with "Last data … ago (HH:MM)" while readbacks are stale
or the controller is not reporting; "—" with `readback.reason` when there is no value; keyed on `group`, a Band in
force row reads "62.0 °F" with DERIVED and its age as text marks, and NO READBACK shows only for another row with
`readback_via` none; a site cap reads "On controller · rev 42: 360 gal", without the rev while API-IRR-01 fails, and
STALE like any device value. The meta line carries the request while REQUESTED, NOT CONFIRMED or HELD, then
`StateReasonLine` with the desired row's `state_reason` (a plan value carries none, so it reads the default); a write
to a row with no readback path while it is in force ("Sent. This setting has no readback, so it can never show
CONFIRMED"); else who wrote the value in force ("Iris changed this at 07:02" or "steward changed this at 06:47" with
AI, "jason changed this at 08:00 · Iris may adjust", "Forecast rule · system", "Released: plan, band or default
applies"); and a read-only row's `read_only_reason` verbatim. Values keep the decimals they carry (`keptValueText`:
Iris's 48.75 on a 30 s grid reads 48.75), and so does the Save toast. `EditDrawer` has `readOnly` for a row served
with `writable: false`: read-only for operators too, with Done and no Save or actions.

The Water rules (VD-15, `dashboard/app/lib/water.ts`, #1135) that still hold after the redesign:

| Topic | As built |
|---|---|
| Status read failing | Today shows "Water status unavailable." with Retry in place of the zone cards whenever API-IRR-01's newest read failed, even with an older answer cached, so no line ever reads idle from old data |
| Program state | The header's status line, by the first rule that holds. REQUESTED names what the controller holds too ("Program rev 42 on controller · rev 43 sending"); not reporting with the built-in program says it keeps running "the built-in program" |
| Last water and last run | `last_water` (the newest run that opened a valve, VPD episodes included) and `last_outcome` (the newest ended ledger row) are separate facts. The outcome follows the last water ("last 07:31, 12 min · ok") only when it is that run's own: a run that watered, stamped no earlier than the water began; otherwise each reads with its own time |
| Running schedule | "Running · schedule · Wall MWF" takes the label from API-IRR-02, which Today also reads (60 s); without it the slot reads "slot 11" |
| Mister fault | The watchdog line shows on its zone and on its line row (L08 and L10 latch too); a blocked zone with a fault shows the fault line alone |
| Graphs link | `graphWindowUrl` writes whole seconds in UTC (`2026-11-01T08:00:00Z`), and `runGraphUrl` links an unreadable time to Graphs' default view |
| Stop | `useStop` notes the running lines from the newest API-02 answer the page holds, the shell's own poll included, so Water offers Disable for L01 without a second API-02 poll |

## 5. Charts on uPlot

Every chart shares one `UPlotChart` wrapper (`dashboard/app/charts/`) on uPlot 1.6.32, loaded with `import("uplot")`
in an effect; `uPlot.min.css` loads only on charting routes. The [redesign](redesign.md) §7 composes them: a chart
stack is one instrument on one time axis, small multiples share a cursor and a scale per row, and statistics chart the
daily summaries. The wrapper owns creation,
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
| Planned (Iris, schedule) | Series colour | — | — | 2px dashed [8,4], right of now only; a superseded waypoint in `--c-bound`, named SUPERSEDED in the panel key |
| Bounds (min, max, clamps) | `--c-bound` | #626b76 | 5.41 | 1px dotted [1,3], on a setting's own history chart only (never on Graphs: they are the setting's allowed range, #1354) |
| Now | `--ink` | #112231 | 16.19 | 1.5px solid with a "Now" label, the only words inside a plot (besides a retention note at its shaded span); the plot past now is shaded `--field` at half strength (#1353) |
| Equipment lanes | `--eq-*` | mister #9d2398, fog #1b9bc7, fan1 #22a096, fan2 #484fa7, vent #056190, heat1 #d47a00, heat2 #b61f02, lights #4d9f45 / #2855ad | ≥ 3.19 | Filled segments |
| Water | `--eq-water`, `--eq-fert`, `--eq-master` | #2389e2, #6331a0, #0e9580 | 3.65, 8.48, 3.73 | Filled; the `leak_detected` flag uses `--fault` |
| Excursion | `--tint-warn` | #f8eccb | — | Fills between the measured line and the nearest band edge wherever it leaves the band; the line keeps its colour and stays on top |
| Min–max envelope | `--ink-muted` | #b9c4cc | — | A 2 px vertical bar per bucket under the mean line, for buckets of 30 min and more (7 d and longer) |
| Compare | `--c-bound` | #626b76 | 5.41 | 1.25 px dashed, labelled "yesterday" or "last week" |
| Events | `--forest` (a person's write), `--ink` (Iris, "AI" in its label), `--eq-fan2` (another agent), `--muted` hollow (system), severity colours (alerts, hollow when resolved), the cause token (runs) | — | ≥ 3:1 | Diamonds, triangles and 3 px bars on a 20 px rail across the top of the stack |

One chart vocabulary (#1354, decisions 2026-11-05). A colour names what ran; words say why.

- **No words inside a plot** but "Now" (and a retention note at its shaded span). Every line is named in the panel's
  swatches and key (`ChartStack`) or legend (`TimeSeries`), and in the tooltip or the header readout at the cursor. The
  band's and the forecast's provenance ("Band in force · DERIVED", "Band ahead of now · reconstructed from today's crop
  anchors", "Forecast · issued 07:00", STALE past 3 h) is said once in the notes above the chart and in the key's hover
  words, never painted on the band edge.
- **Settings are opt-in on Graphs** (+ Setting); none is drawn by default. A drawn setting gets a key item at its value
  in force; its min and max are not drawn. The y axis fits the traces, the forecast and the band; a setting outside
  that range reads "above the chart ↑" or "below the chart ↓" in its key item instead of stretching the axis.
- **One colour per line.** Each irrigation line takes its kind's colour (`charts/palette.ts`: drip `--eq-water`,
  fertilized lines `--eq-fert`, misters `--eq-mister`, fog `--eq-fog`, the master valve `--eq-master`) on the Graphs
  equipment lanes, Water → History's daily water use by line, the Equipment board and Now's running dots. A lane's run
  is filled in its line's colour and its cause is written after the run.
- **One colour per cause**, never a line's colour (`charts/causes.ts`): the minutes by zone, the daily water use by
  cause, a line's state dot on Water and the run marks on the Graphs event rail. Every surface prints the words, so
  colour is never alone.

| Cause | Words | Token |
|---|---|---|
| `vpd` | VPD | `--cause-vpd` |
| `schedule` | schedule | `--cause-schedule` |
| `run_now` | run now | `--forest` |
| `feed` | feed | `--eq-fert` |
| `prewet`, `flush` | prewet, flush | `--cause-prep` |
| `fallback` | fallback | `--eq-heat1` |
| `unknown` | cause unknown | `--c-bound` |

`--cause-vpd` #c2410c (5.18:1), `--cause-schedule` #8a5a00 (5.93:1) and `--cause-prep` #65731f (5.22:1) are new and
share no hue with a water line. Run now is a person's action, in `--forest` as a person's write is on the event rail;
feed is fertilizer, in the fertilized lines' colour; fallback keeps the orange of the schedules' fallback-only runs.
A cause the table does not know reads raw, in `--c-bound`.

### 5.2 Primitives

| Primitive | Shows | Data | Rules |
|---|---|---|---|
| `ChartStack` | One card of panels on one x scale: temperature, VPD and RH (220, 200 and 160 px; 160, 150 and 120 at 860 px and below) and a panel per added unit (160, 120), each with an `id` for anchors (`#temperature`, `#vpd`, `#rh`) | API-04 series, API-06 band, API-12 setting steps, API-05 lanes, the event reads | Only the bottom panel draws the time axis; a 1px `--line` divider separates panels and the header row is the only chrome between them. Each header row carries the name and unit, the window's statistics (in band % when a band is drawn, low with its time, mean, high with its time, gaps) series swatches that toggle (`aria-pressed`), replacing the legend, and a key for the band and each setting drawn (#1354); while the cursor is on any panel every header row reads the cursor's values instead ("14:22 71.9 °F · band 62.0–70.0 · above", then the forecast hour and each step) and one time chip rides the cursor line; no floating tooltip. Layers: the band ribbon with excursion shading, the min–max envelope, a comparison dashed with its delta, setting steps, the forecast, the event rail and the equipment lanes in a disclosure (open from 861 px). The foot holds the resolution line, the data table and "CSV of this window" |
| `ChartGrid` | Small multiples: rows are metrics, columns are zones | API-04, API-06 | One sync key and a uniform y scale per row; only the first column draws a y axis and only the bottom row an x axis; plot height from props (120 px on Zones, 110 px on phones) |
| `RangeToolbar` | The page's range presets as nav chips, ‹ › to step by the window's length, Zoom out (×2) and Custom… (From and To days), then the page's own tools | — | Every control is a link, so each pushes history. Drag-to-zoom on fine pointers and "Reset zoom" stay. The future pad matches the window's length, at most 72 h, so Graphs opens on 72 h back and 72 h of forecast ahead; Today ends at the next local midnight (decisions 2026-11-05, replacing D-UX-13) |
| `TimeSeries` | One metric over time with band ribbon and setting steps | API-04, API-06, API-12 | The setting drawer's Change history; the chart stack builds its panels from the same model (`buildTimeSeriesModel`) |
| `EquipmentLanes` | What ran, when, how long | API-05: intervals seeded with the state before the window, equal repeated rows collapsed | Groups: Climate (fan1, fan2, vent, heat1, heat2); Water in L01–L11 order (drip_wall, drip_wall_fert, fert_master_valve, mister_center, drip_center, drip_center_fert, mister_south, mister_south_fert, mister_west, mister_west_fert, fog); Light (the Lutron readback `grow_light_main`, `grow_light_grow`; the intent is on the Equipment board); Flags (occupancy, leak_detected, water_flowing). Each row: current state, runtime and starts at right. Commanded state; sensed rows (the Lutron readback, `water_flowing`) are outlined. Unknown spans come from the API (controller not reporting, or no row) and are hatched, never drawn as off; rows idle all range fold into one line; "CSV of these lanes" |
| `PlanLanes` | What is planned, the next 24 h by default | API-07 lanes only; schedule occurrences render in Water's schedule views (§4.6) | One row per planned setting; device value at the left edge; dashed segments labelled with the planned value; PLANNED and AI in the row header (a forecast rule's value reads "Forecast rule · system", never AI); MANUAL rows muted with "Iris skips"; waypoints the API marks not effective are muted and labelled SUPERSEDED under "YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan"; `rowEnd` takes the screen's lane link and pill |
| `MinutesByZone` | Watering minutes or gallons per zone, line or cause per day | API-IRR-10 | `measure: "minutes" \| "gal"`, any window up to 400 days. Rows Wall, Center, South, West (fog apart, as humidification); daily bars (`uPlot.paths.bars`), clean below fertilizer; days with no data hatched; today's remaining scheduled minutes as a dashed outline; the daily cap as a dotted tick (D-IRP-06: center mist 120, south 45, west 45, wall drip 60 min); "today 46 min · cap 120" at right; tooltip and table split by line and cause, in words; "CSV of this chart" |
| `DailyWaterUse` | Meter gallons per day split by line or cause, the water that ran while no valve was open apart | API-21 | 30 or 90 days; its CSV |
| Statistics (`stats-*`) | The typical day (the median by hour with the middle half dashed and the band behind, 240 px), each day's range (low to high in `--ink-muted` with a mean tick, 200 px) and time in band per day (bars against a dotted 90% target) | API-04 hourly and daily buckets, API-06 | Each with its data table |

```text
TEMPERATURE °F   in band 98% · low 61.2 (05:10) · mean 66.0 · high 71.9 (14:22)          ● Indoor ─ Outdoor
 72 ┤▒▒▒▒▒▒▒▒/‾‾‾‾‾‾\▒▒▒▒▒▒▒▒▒▒▒▒▒▒   ━━ setting ━━━  │▒▒▒▒ - - plan - - ▒▒▒▒       band ribbon, excursions shaded
 62 ┤▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒   (gap)   ▒▒▒▒▒▒│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒
VPD kPa          in band 91% · low 0.41 · mean 0.66 · high 1.31 (13:22)
 Fan 1 ┤ ██████     ██████████              │                         lanes share the cursor
        00:00          03:00          06:00          09:00          12:00     Now           one axis, at the bottom
WATERING MINUTES BY ZONE · LAST 7 DAYS              today    cap
 Wall    ▁ ▃ ▃ ▅ ▃ ▃ ▆┆                             10 min   60
 Center  ▆ ▇ ▅ ▆ ▇ ▆ ▃┆                             46 min   120
```

### 5.3 Shared behaviour

| Behaviour | Rule |
|---|---|
| Cursor | `cursor.sync.key` = `verdify:<route>:<range>`; every time chart on a page with the same x range syncs, lanes included. One readout per synced group: a chart stack's header rows and its one time chip, never a tooltip per chart |
| Tooltip, legend | Lanes and bars keep their own tooltip, for the hovered row only, set with `textContent`. A stack's series swatches in its header rows toggle a series; a chart key (`ChartKey`) under the lanes and bars names each colour once |
| Range, download | `RangeToolbar` on Graphs (Today, 24 h, 72 h, 7 d, 30 d, 90 d, ‹ ›, Zoom out, Custom…); Zones 24 h · 72 h · 7 d; Equipment and Plan 24 h · 72 h; drag-x zoom with "Reset zoom" on desktop, none on touch; "CSV of this window" (and of the lanes, the minutes and water use), built in the browser |
| Live | Now every 15 s, other pages 60 s, rows with REQUESTED 5 s; paused while the tab is hidden (weather `polling.ts`); points append with no animation |

### 5.4 Honesty, units and time

- **Gaps:** `spanGaps: false` everywhere; nulls come from the server. Each panel's header row gives low, mean and high
  with times, in band % and the gaps (weather `seriesSummary`). A stale series stops at its last sample and the
  chart's freshness line shows STALE; nothing is extended to now. Beyond raw retention (`climate` 365 days,
  `diagnostics` 180, `setpoint_snapshot` 90) the chart shows daily summaries and says so; a gap the API explains (an
  ingestor outage) carries that reason.
- **Units** (fixed, canonical): temperature °F 1 dp; RH % 1 dp; VPD kPa 2 dp; water gal 0 dp, per run and per day (a
  whole-gallon meter: a run that moved no water reads "0 gal", and no run shows a rate or flow flag); run time min
  ("h min" from 120); the meter's flow gal/min 0 dp; lux, W/m², ppm, dBm, angles and heap kB 0 dp; other units 1 dp.
  DLI is out of the first build and returns with the PAR meter.
- **Time:** America/Denver, stated once per page in the footer, on the axes (with the offset) and in every
  `<time title>` (D-UX-08); clock times are 24-hour ("06:02", `hourCycle: "h23"` as in
  weather's plots). X values are UTC epoch seconds; labels use `Intl.DateTimeFormat` with `timeZone:
  "America/Denver"`: two lines with the offset at 1440 (weather `plotTime`: "11/01/2026 / 01:00 GMT-06:00"), time
  only at 390 (the date once ticks are a day or more apart, §5.5). Today, 2026-11-01, falls back: 01:00–01:59 occurs
  twice, so times in that hour carry their offset, and local days span real elapsed time (25 h today).

### 5.5 As built (VD-06)

The chart layer is `dashboard/app/charts/` (#1126). Where it settles a detail §5 leaves open, or differs from it:

| Topic | As built |
|---|---|
| Stylesheets | A charting route spreads `chartLinks()` into its `links`: `uPlot.min.css` and `app/charts/charts.css` (reserved heights, the stack's panels and header rows, the grid, the rail, lanes, keys and notes). Chart rules stay out of `styles.css`, whose weather sections are byte for byte and whose §2.2 block stays last. A source test (`charts/chunks.test.ts`) keeps `import("uplot")` in `uplot-chart.tsx` only and makes any route that imports chart code link `chartLinks()` |
| Layers | Retention shading and the band wash paint in `drawClear` (under the grid), the band's 1px edges in `drawAxes`, and the steps, bounds, now line and all labels in `draw`. Labels never overlap: they are placed in the order Now, retention, bounds, the plan (soonest first), the request and the device line (newest first), then the band's source, and one with no free place at its start or end is dropped, so a crowded chart loses the band labels first. A dropped label's words stay in the chart notes, the tooltip or the data table |
| Band | A ribbon that changes source (in force to crop, at now or at the 90-day boundary) joins at a shared point, so the change never reads as a gap; a null in force inside 90 days stays a gap |
| Steps | Device: each readback change point held until the next, the last until now. Planned: the waypoint in force at now draws from now to the next waypoint, and the last one to the window's end. Requested, not confirmed and held rows draw with their state word; the tooltip adds who asked and the API's reason when it gives one (with the applied value when clamped). A SUPERSEDED step's tooltip names what holds it off, from the desired row in force now (architecture §5.2): "manual 1.55 kPa (jason) holds until the setting is written again", or "1.55 kPa (jason) holds until a newer plan" when that row is not manual |
| Axis labels | One line at every width (decisions log, 2026-11-04): the time, the date at each local midnight and before the first tick above 860 px ("Nov 1 12:00"), and "Oct 28" once ticks are a day or more apart; only the repeated fall-back hour adds MDT or MST. The axis is 36 px (28 px at 860 px and below), and a label whose box would cross the canvas edge is left out |
| Zoom | Double-click does not zoom out (uPlot's would zoom to the data's extent, not the window): "Reset zoom" puts every chart on the sync key back, lanes included, and the chart stack shows it while any panel is zoomed (a panel rebuilt with new metrics starts on the window). A poll keeps the zoom; an unzoomed chart follows its sliding window |
| Legend | A hidden series also leaves the data table, the summary line and the CSV, so each holds exactly what is plotted |
| Colours | The climate keys take `--c-temp`, `--c-vpd` and `--c-rh`, outdoor and forecast keys `--c-outdoor`, a key drawn dotted as a cross-check `--c-setpoint` (the controller's target on `/graphs/zones`, VD-18); a further line in one unit takes the unit's colour if free, else the next unused of `--eq-fan2`, `--eq-heat1`, `--eq-vent`, `--eq-mister`, `--eq-light-grow`, `--eq-fert`, `--eq-heat2`, `--eq-fog` (each at least 3:1). Planned steps take the panel's first line colour. A label drawn on the plot takes its mark's colour only where that reads at 4.5:1 on white (§7); otherwise a darker token of the same meaning: `--warn-text` for REQUESTED (`--c-requested` is 4.01:1), `--ink` for the rest (the RH plan: `--c-rh` is 3.47:1). Lines keep their §5.1 colours |
| Points | A lone sample between gaps draws as a point; a daily-summary series draws every day as a point |
| Stale | "STALE · last data 06:02" names the start of the last bucket with data, and its date ("Oct 30, 06:02") when that was on an earlier local day; on daily buckets the day alone ("Oct 30") |
| Gap words | A null run reads its own start and end ("No data 02:10–02:40"), with both dates when it ends on another local day ("Oct 5, 00:00–Oct 6, 00:00"); on daily buckets its days ("No data Oct 5–Oct 7") |
| Data table | One row per bucket: the time with its offset, each series, the band and its source, and a note (the null run's "No data …" on its first row, the retention words on the first row, STALE on the first row after the last value, a `daily.*` bucket's reason). A second table lists the setting steps, with the bounds in its caption. Rows build only once the disclosure opens |
| CSV | `time (America/Denver)` as ISO 8601 local time with the offset ("2026-11-01T01:30:00-06:00"), then one `<key> (<unit>)` column per plotted series with the raw `avg`; a UTF-8 byte-order mark and CRLF rows. The chart stack offers one file for the window, rows on the union of the panels' bucket starts |

### 5.6 As built (VD-07)

The lanes and the bars are `dashboard/app/charts/equipment-lanes.tsx`, `plan-lanes.tsx` and `minutes-by-zone.tsx`,
with `lanes-draw.ts`, `causes.ts` and the line register `dashboard/app/lib/lines.ts` (#1127). Where they settle a
detail §5.2 leaves open, or differ from it:

| Topic | As built |
|---|---|
| Lane layout | Rows are HTML at a fixed pitch: the label (the API's words, cut with an ellipsis and whole in `title`) and the right-hand text are text, and an x-only uPlot sits over the plot column and paints each row's band at the same pitch in a `draw` hook, so canvas and HTML rows line up and the reserved height is the rows' own. A spacer under the rows holds the time axis (36 px above 860 px, 28 px at and below) |
| Equipment rows | Design order whatever order API-05 sends; a key the order does not list follows its group's own. A lane with no `label` reads its key, never a made-up name. `_intent` keys are dropped from the rows, the key and the table. The now line draws when the page passes now |
| Equipment marks | ON fills the row's band; a sensed row's ON is a 1.5 px outline; unknown is 1 px `--muted` diagonals every 6 px, only where the API says unknown; off paints nothing. A wet line's run of pulses with one cause is named once, after the run, and an unknown span after it ("unknown 02:10–02:40", or "unknown" where only that fits); a label that would overlap a span or another label, or pass the next span, is dropped |
| Equipment text | Each row ends with the state at the window's end, on time and starts ("off · 80 min · 1"; runtime alone at 860 px and below); a span already on when the window opens is no start. Idle rows fold into one "N idle rows" button (`aria-expanded`, `aria-controls`) at the end of the rows, and open below it. Inside a chart stack the lanes take the panels' plot area and draw no time axis; each row's name and facts ("off · 80 min on · 1 start") head its track on a 16 px line, and the fold is a button under the rows. The data table lists every span of every row: equipment, state, from, until, duration and a wet line's cause in words. A run fills in its line's own colour and its cause is written after it (#1354); a key under the lanes names the hatch (unknown) and the outline (sensed) |
| Tooltips | A lane or bar tooltip shows only for the row under the pointer. A cursor moved by another chart on the sync key has no row there: that chart shows its own tooltip, and the lanes only move their cursor line |
| Zoom | A drag on the lanes zooms them with every chart on the sync key. The lanes show their own "Reset zoom" while zoomed (`zoomControl`, default on), so a page with no panels (Equipment, Plan) is never left zoomed; a page whose panels offer one passes false |
| Plan rows | Each row is a header line (label, then the header words: PLANNED · AI, Forecast rule · system, MANUAL · Iris skips · Jason since 10/30, or YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan; two lines at 860 px and below) over a 24 px strip, with the device value in words at the strip's left ("—" with the API's reason in its title, the tooltip and the table). YOUR VALUE prints the value at its unit's decimals without the unit, which the device value carries. A row with no waypoint drawn and no desired or manual value has no header words |
| Plan strip | The device value holds as a solid `--c-setpoint` line from now to the first drawn segment, the plan's end or the horizon. Effective waypoints are dashed [8,4] in `--ink` (a lane has no series colour), superseded ones dashed `--c-bound`, each labelled as TimeSeries labels its steps ("1.70 kPa PLANNED · AI", "1.80 kPa SUPERSEDED"; the value or SUPERSEDED alone when the label does not fit). A manual row draws its held value as a solid `--c-bound` line and no waypoint. The plan's end is a dashed `--muted` marker labelled "Plan ends 17:30 · band/default" in an 18 px band above the rows, the words after the dot being what the screen says takes over. No x grid, so the header words stay clear |
| Minutes rows | Wall, Center, South, West, then Fog (humidification), each a 76 px plot: 92 px with 8 px under the baseline, the last 112 px with the day labels. The four watering zones share one minutes scale so their bars compare; fog has its own, set apart by a divider and a head ("Humidification (fog) · own scale, 0–10 min"), its ticks in mono. The y axis is whole minutes at one width on every row, so the rows' days line up. Stacks use uPlot's bands (each cause's bar runs from the total below it). Drag zoom is off on the bars (UPlotChart `zoom={false}`) |
| Minutes marks | A no-data day is hatched the full plot height; a partial day has a dashed `--ink` top edge; today's still-planned minutes are a dashed `--muted` outline on today's bar; the cap is a dotted `--c-bound` line across the row. The right reads "today 46 min · cap 120", with "(partial)" while today is partial |
| Minutes words | The tooltip and the data table split each day by line and cause ("L04 Center mister · VPD · 46 min · 3 gal"); the line's name comes from the screen (API-IRR-01 `name`), and without it the line id stands alone. L03's supply minutes and a report grouped by cause draw no bar |
| Group by (VD-15) | `group` cuts the same report into rows: zone (the default, above); line, one row per line in the report in L01–L11 order, L03's supply row included, and L11 as fog; cause, one row per cause in the cause table's order, then fog. Fog keeps its own scale and the humidification note in every grouping; by cause L03 is left out, as each feed would count twice. `caps` and `planned` are keyed as the rows are (a zone, a line id or a cause). By line needs a report by line; by cause works from a report by line or by zone |

## 6. Responsive rules

Three breakpoints, each with one job ([redesign](redesign.md) §3.2), declared once in `app/lib/narrow.ts` and mirrored
by the media queries: 600 px (card grids go from one column to two), 860 px (navigation, chart axes and tables to
rows) and 1200 px (the main and aside split). Where phone and desktop differ in structure (Now's track or lists,
Schedules' agenda or day, Settings' rail or selects), both render and CSS shows one; `usePhone()` is for behaviour only.

| Pattern | 860 px and below | 861 px and up |
|---|---|---|
| Shell | 16 px gutter; no horizontal page scroll at 360 and 390 px, on WebKit too; wide content scrolls inside its own labelled region | 80rem shell, 40 px gutter: one centred container for header, main and footer on every page (decisions log, 2026-11-04) |
| Nav | A 52 px header and the fixed five-tab bottom bar (icon over label), 56 px plus the safe-area inset; the page pads its bottom to clear it | The five sections inline in the sticky header |
| Page header | Actions under the status line, a primary one full width; tabs at 44 px; Water's tabs and "■ Stop all" sticky under the header | Actions at the h1 row's right |
| Page rhythm | Blocks `--space-5` apart, cards `--space-3` | Blocks `--space-6` apart, cards `--space-4` |
| Card grids | One column below 600 px, two from 600 px (`.l-cols-2`, `.l-cols-3`); KPI tiles 2-up (`.l-cols-4`, one column under 360 px) | Up to three or four columns; Now's pair side by side; Equipment's two balanced columns |
| Rows and tables | `ObjectRow` actions take the full width, the primary first; Settings' rows and History's runs become stacked rows whose link covers the row | Table rows; the actions at the row's end |
| Rail and aside | Settings' rail becomes two selects above the catalog; Water's Site today comes first | Settings' rail sticky beside the catalog; from 1200 px Water's Site today sticky in the aside |
| Schedules | The agenda by default, Day on a toggle, 24 h across with 6-hour ticks and no sideways scroll; the week strip's steps above seven 44 px days; no week matrix | The day timeline by default, Week and Agenda on the toggle |
| Time charts | Panels 160, 150 and 120 px (temperature, VPD, RH; added panels 120); time-only labels; the lanes closed in a disclosure; a tap or drag moves the cursor and the header rows show its values; no zoom on touch | Panels 220, 200 and 160 px (added panels 160); two-line labels with the offset; the lanes open |
| Zones grid | One metric at a time, the five zones stacked at 110 px | The 5 × N grid at 120 px |
| Lanes | 24 px rows, 6rem labels, runtime only | 20 px rows, 10rem labels, state, runtime and starts |
| Drawer / popover / dialog / toast | Full-screen sheet with pinned actions / bottom sheet / full width minus 32 px / full width above the bar | 28rem right (the page narrows beside the schedule editor from 1200 px) / anchored under its opener / 32rem centred / 24rem bottom right |
| Targets | Every button, link, chip, tab, menu item, switch and summary at least 44 px | At least 24 px (SC 2.5.8), 44 px wherever weather already sets it; toolbar controls 36 px |

## 7. Accessibility basics

Target: WCAG 2.2 AA, checked in the in-cluster CI by Playwright and axe (`wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`)
against the fixture API at 320, 768 and 1440 px on every route, both setting drawers, the schedule editor and an open
popover, with no-horizontal-scroll assertions at 360 and 390 px (also on WebKit) and a reduced-motion check
([redesign](redesign.md) §8).
- **Contrast:** text at least 4.5:1; focus ring, input borders, chart lines, lanes, band edges, meters and series at
  least 3:1 (§2, §5.1). Tints (`--tint-ok`, `--tint-warn`, `--c-band-wash`) never carry meaning alone.
- **Keyboard:** every action reachable in a logical order (header, banners, page header, tabs, content); the focus ring
  is never removed; drawers, dialogs and sheets use native `<dialog>` (focus trapped and returned to the opener);
  popovers and menus close on Escape; timeline blocks, strip cells and event marks are buttons or links, and a row of
  marks too small to be targets of their own is one tab stop with arrow keys (`rovingKeys`).
- **Colour never alone:** badges carry words, outcomes have words and fills (solid, dashed, hatched), unknown is
  hatched, lanes carry labels, requested and planned differ by line style.
- **Forms:** `<label for>`; hints and errors linked with `aria-describedby`; `aria-invalid`; errors beside the field.
- **Screen readers:** visuals are `aria-hidden` beside equivalent text, or `role="img"` with their numbers. Tabs are a
  labelled `nav` with `aria-current="page"` (ranges too); filter chips use `aria-current="true"`; toggles use
  `aria-pressed`. Live regions are toasts only: success polite, failures assertive, each once; ticking ages and pending
  seconds are silent.
- **Charts:** `role="img"` with an `aria-label` naming what is plotted and that a table follows. Every chart has its
  data table (local time with offset and each series; lanes list device, on, off, duration; minutes list day × zone),
  which is the keyboard and screen-reader equivalent; Climate's events also list under the stack. A keyboard chart
  cursor is Later.
- **Times** are `<time dateTime>` with the full local form in `title`. **Motion:** there is no animation; hover colour
  transitions of 120 ms or less, and none under `prefers-reduced-motion: reduce` (weather's rule).

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
§4.6 composites. The [redesign](redesign.md) (§10, epic #1120) rebuilt them: WP0 the shell, the page and section
headers, the card system, the micro-visualizations (§4.7) and the chart stack (§5.2); WP1 to WP4 the screens' composites
(§4.6); WP5 one page rhythm across them, the removal of what nothing used and these docs.
