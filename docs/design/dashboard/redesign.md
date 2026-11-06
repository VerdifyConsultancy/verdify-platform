# Dashboard redesign: navigation, layout, cards, charts and schedules

dashboard.verdify.ai has strong data, honesty and write layers and a weak presentation. Pages are stacks of identical
white cards filled with sentences joined by " · ". Seven flat sections push three of them behind a phone More tab.
Graphs leaves about 78 px of plot per climate panel. The north-star metric, time in band, never reaches Now, and the
schedules are a text table with no time axis. This spec rebuilds the information architecture, navigation, layout,
cards, charts, statistics and schedule interface. It keeps weather.vallery.net's visual language exactly as
[design system](design-system.md) §2 copies it: the tokens, colours, IBM Plex type, sticky white header, ink hero,
white cards, chips, pills and mono eyebrows. It adds no colour.

Status: Built (2026-11-04): WP0 to WP5 landed together; where the build settles a detail this spec leaves open, or
differs from it, [screens](screens.md)' "As built" tables and [design system](design-system.md) §4 to §7 say so.
Related: [decisions](decisions.md) · [product](product.md) · [screens](screens.md) · [design system](design-system.md)
· [architecture](architecture.md) · [irrigation](irrigation.md)

**Precedence.** [Decisions](decisions.md) overrides this file. This file overrides [screens](screens.md) and
[design system](design-system.md) where they differ on information architecture, page layout, component
composition, charts and the schedule views. Their behaviour, data, honesty, role and write rules stay in force:
screens §1.2–§1.5, design system §1 rules 1 and 3–5, §4.1–§4.5 and §5.4.

**Evidence.** Two reviews from 2026-11-04 feed this spec:

- A code and information-architecture review of `dashboard/` at `dd10213b9`.
- A survey of about 40 commercial products and guidelines: Grafana, Datadog, Stripe, Home Assistant, Apple Home and
  HIG, Material, Rachio, Hydrawise, Rain Bird IQ4, ecobee, Nest, Priva, AROYA, the AGP report, ISA-101 and EEMUA 191.

The live walk of the running site could not run, because Authentik sign-in was required and no credentials were
entered. A live walk at 1440 × 900 and 360 × 740 is therefore part of acceptance (§11). Code citations are
`path:line` under `dashboard/app/`, except `e2e/…` and `security-headers.mjs`, which are under `dashboard/`.

## 0. Decisions this spec applies

Each row changes a design doc or a pinned test, so each is listed here with its applied default. To change one, edit
its row; [decisions](decisions.md) wins if it says otherwise.

| Id | Applied default | Changes |
|---|---|---|
| D-UX-01 | Five destinations at every width, in this order: **Now · Water · Graphs · Activity · Control**. `/more` is removed. The name, role and Sign out move to an account menu. | screens §1.1, design system §3, `layout.spec.ts:15`, `app-shell.test.tsx`, `caretaker-visit.spec.ts` |
| D-UX-02 | Control holds three tabs: Settings (the default), Plan and Equipment. The setting drawer opens in place on all three. | screens SCR-03, SCR-05, SCR-07; `plan.spec.ts` "Open" link |
| D-UX-03 | Activity splits into Alerts (`/activity`) and Log (`/activity/log`). Old log links redirect. | screens SCR-06, `activity.spec.ts` |
| D-UX-04 | The scorecard becomes Statistics (`/graphs/statistics`, 30, 90 or 365 days). `daily.*` keys may be charted. | The VD-10 detail "daily keys show on the scorecard" (`lib/graph-url.ts:211`), `graphs.spec.ts` |
| D-UX-05 | For operators, the bursts (Fans, Humidify, Vent bypass) are quick actions on Now. They stay on Equipment too. | US-EQP-06 placement, J9 step 5 |
| D-UX-06 | Changed by Jason on 2026-11-06 ([decisions](decisions.md), #1349): a line's Stop shows only while that line runs (or its relay reads unknown), filled red, plus one Stop all water; an idle row has no Stop. It is still one tap and never blocked. (Was: Stop on every line in every state, a quiet outline while idle.) | The Stop in screens SCR-04: present only while the line runs (2026-11-06) |
| D-UX-07 | A row or card head carries at most one state pill. CONFIRMED shows only while the newest write is under 10 minutes old. AI and provenance (MEASURED, PEER, HOUSE, DERIVED) are text marks, except in log rows. | design system §4.1 usage; settings, now and graphs specs |
| D-UX-08 | The time zone is stated once: in the footer, on chart axes and in every `<time title>`. Eyebrows drop "America/Denver". | design system rule 2 |
| D-UX-09 | Layout tokens (space, type, widths, z-index) live in `app/styles/tokens.css`. There are no new colour tokens. | design system §2 |
| D-UX-10 | Five navigation icons (inline SVG) on the phone bar, and no other icon set. | design system §1 "icon set: Later" |
| D-UX-11 | No header "Water running · Stop" pill. VPD misting and fog cycle every few minutes, so it would flicker, and Stop all water is one tap away on Now and Water. | The research suggestion, rejected |
| D-UX-12 | No drag editing on the schedule timeline. A click on an empty slot prefills the editor. | — |
| D-UX-13 | Superseded 2026-11-05 ([decisions](decisions.md)): the future pad matches the window's length, at most 72 h, and Graphs opens on 72 h back and 72 h ahead. Was: 25% of the window, at most 12 h. Today runs to the next local midnight. | `lib/graph-url.ts:27,267` |

## 1. Design principles

1. **Answer first, exceptions next, detail on demand.**
   - Finding: Now gives eight equal tiles with no entry point (`components/now/tiles.tsx:117-118`). It repeats the
     open-alert count four times, running water three times and the next run three times. ISA-101 and NN/g say to
     spend colour and position on what deviates.
   - Practice: every page header has one status line. Now leads with the answer and three band bars. A fact appears
     once per screen. The normal state is quiet.
2. **Draw quantities, write labels.**
   - Finding: the main encoding is " · " sentences (water card, line cards, limits strip, chart summaries).
     Minutes against a cap are text (`lib/water.ts:273-276`). The scorecard is a 30 × 15 number table.
   - Practice: anything against a limit is a bar (band bar or meter), and anything over time is a sparkline, track
     or chart. Text names and qualifies. Tables stay as the accessible equivalent.
3. **Five places, the same at every width.**
   - Finding: the phone's More hides Plan, Settings and Equipment (`components/site-header.tsx:9-25`). The
     caretaker's bursts sit two taps deep (`e2e/caretaker-visit.spec.ts:41-45`). Plan's only action jumps to Settings
     (`routes/plan.tsx:33-37`). Nav labels and h1s disagree. NN/g and Apple's HIG both reject hidden or overflow
     navigation.
   - Practice: Now · Water · Graphs · Activity · Control everywhere. Sub-views are in-page tabs. The h1 names the
     view and the eyebrow names the section.
4. **One grammar.**
   - Finding: there are five container patterns for one concept, two heading hierarchies (a 1.45rem h2 and a
     0.72rem eyebrow used as a heading), three chip implementations with different `aria-current` values, 28 font
     sizes and 15 "muted small" classes.
   - Practice: one page header, one section header, one card frame, one `Chips`, one `Table`, one `.meta` line, a
     6-step type scale and a 4 px spacing scale.
5. **A chart stack is one instrument.**
   - Finding: each Graphs panel is 150 px with its own 54 px axis, which leaves about 78 px of plot. About 100 px of
     chrome sits between panels. Every synced chart shows its own tooltip at once. Every range adds 12 h of empty
     future. Min/max and events are never drawn.
   - Practice: one card with one bottom axis and taller panels. Statistics sit in the panel headers, with one
     readout, an event rail, excursion shading and a min–max envelope.
6. **Time is an axis, not a sentence.**
   - Finding: the schedule week is a text table with no time axis. Past and future are separate modes, there is no
     list of rules and no cap context. Now's "Coming up" and the water card's "Next" are sentences.
   - Practice: schedules get a day timeline with week context and a separate rules list. Now shows the hours around
     now as a track. Each line on Water carries its own today strip.
7. **Colour means state, and red means stop.**
   - Finding: nine filled red Stop buttons show while nothing runs (`components/stop-button.tsx:24`). Provenance
     pills sit beside state pills.
   - Practice: neutral by default. Amber and fault mark the abnormal, always with words. One state pill per head.
     Stop is filled only while water can be flowing.
8. **Honest by construction.**
   - Finding: tests enforce honesty (`e2e/states.spec.ts`: nothing green while the API is down; "All OK", "No open
     alerts", "CONFIRMED" and "in band" never shown without data; unknown is never off). New visuals are where it
     could regress.
   - Practice: every new primitive takes known, unknown, stale and API-down states. Unknown is hatched, stale is
     muted and API-down is neutral. Gaps stay gaps in bars, tracks and strips.

## 2. Information architecture and navigation

### 2.1 Sections, views and routes

| Section | View (tab) | Route | h1 | Document title |
|---|---|---|---|---|
| Now | — | `/` | The answer sentence ("All OK") | Now · Verdify dashboard |
| Water | **Today** (default) | `/water` | Water today | Water today · Verdify dashboard |
| | Schedules | `/water/schedules` | Schedules | Schedules · Water · Verdify dashboard |
| | History | `/water/history` | Water history | Water history · Verdify dashboard |
| Graphs | **Climate** (default) | `/graphs` | Climate | Climate · Graphs · Verdify dashboard |
| | Zones | `/graphs/zones`; `?zone=south` focuses one | Zones; "South zone" when focused | South zone · Graphs · … |
| | Statistics | `/graphs/statistics` | Statistics | Statistics · Graphs · … |
| Activity | **Alerts** (default) | `/activity` | Alerts | Alerts · Activity · … |
| | Log | `/activity/log` | Activity log | Activity log · Verdify dashboard |
| Control | **Settings** (default) | `/settings` | Settings | Settings · Control · … |
| | Plan | `/plan` | Plan | Plan · Control · … |
| | Equipment | `/equipment` | Equipment | Equipment · Control · … |

- **Removed and redirected:**
  - `/more` is removed and answers 404 through the catch-all.
  - `/graphs/scorecard` answers a loader redirect to `/graphs/statistics` and keeps the query.
  - `/activity` with any log filter (`source`, `actor`, `action`, `target`) redirects, replacing history, to
    `/activity/log` with the same query.
  - `/plants` stays out of the IA until SCR-08 ships. SCR-09's sign-in and error cards are unchanged.
- **Paths stay where they are.** Plan, Settings and Equipment keep their paths, so drawer links, banner links and
  bookmarks keep working. The Control section owns them through `owns()`, as Graphs owns `/graphs/*` today.
- **New URL state.** Every view state stays in the URL. These are added; existing parameter names are kept:
  - `/water/schedules`: `view=day|week|agenda` and `day=YYYY-MM-DD`.
  - `/water/history`: `range=7d|30d|90d|365d` and `measure=min|gal`.
  - `/graphs`: `range` also takes `today` and `90d`; `events=<sources>`, `cmp=day|week`, `env=0` and
    `preset=climate|water-soil|light|controller`.
  - `/graphs/zones`: `metric=temp|vpd|rh`.
  - `/graphs/statistics`: `days=30|90|365`, `from`, `to` and `typical=14|30`.

### 2.2 Desktop navigation (861 px and up)

```text
1440 ┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
     │ [V]erdify · DASHBOARD    Now   Water   Graphs   Activity (2)   Control              [OPERATOR  Jason  v] │
     │ ▌Controller not reporting since 03:12. It keeps running VPD misting and its caps.          Equipment > │
     └────────────────────────────────────────────────────────────────────────────────────────────────────────┘
       the second row shows only while a fault banner is true; it scrolls with the sticky header
```

- **The header** is weather's header, unchanged in look: sticky, white at 95% with blur, the lockup, then the five
  links with the 2 px forest underline on the current section. The nav's hidden horizontal scroll
  (`styles.css:67-71`) is removed, because five links fit from 861 px.
- **The count** is a pill after "Activity" (`.nav-count`). Its number is API-02 `alerts.open`, the one open-alert
  count. Its fill follows the worst open severity in API-02 `alerts`: critical uses `--fault` with white text,
  warning uses `--tint-warn` with `--warn-text`, and info-only uses `--field` with ink. The screen-reader text stays
  "N open alerts".
- **The account button** shows the role pill (`.role-pill`, OPERATOR or VIEWER) and, from 1100 px, the display
  name. It opens a disclosure (a native `<details>`) holding:
  - "Signed in as Jason (jason)";
  - "Operator: can change anything", or "Viewer: read-only";
  - Sign out, the existing POST form.
- **Nothing else goes in the header:** no Stop (D-UX-11), no freshness indicator and no search.

### 2.3 Phone navigation (860 px and below)

```text
360 ┌────────────────────────────────────┐
    │ [V]erdify · DASHBOARD [OPERATOR v] │  sticky, 52 px
    │ ▌Controller not reporting · 03:12 >│  only while a fault banner is true, at most two lines
    │                                    │
    │  page                              │
    │                                    │
    ├──────┬──────┬──────┬────────┬──────┤
    │ (ic) │ (ic) │ (ic) │ (ic) 2 │ (ic) │  fixed bar, 56 px plus the safe-area inset
    │ Now  │Water │Graphs│Activity│Control│  icon 20 px over a one-word label; current: ink and a 2 px forest top rule
    └──────┴──────┴──────┴────────┴──────┘
```

- **The bar** (`navigation "Sections, phone"`) holds the same five destinations in the same order as desktop. Each
  tab takes a fifth of the width and is at least 44 px tall. Icons are inline SVG (`currentColor`, 1.75 px stroke):
  a gauge for Now, a droplet for Water, a line chart for Graphs, a bell for Activity and sliders for Control. Labels
  always show.
- **The count** sits on the Activity icon's top-right corner, as the same severity-coloured pill. The link's
  accessible name stays "Activity N open alerts".
- **The header** keeps the full lockup and the account button (the role pill stays visible) and drops to 52 px.

### 2.4 Titles, breadcrumbs and back

- **Two levels only: section › view.** The page header's eyebrow is the breadcrumb.
  - It reads the section name, and that name links to the section's default view, except on the default view itself.
  - A focused zone reads "GRAPHS › ZONES", with both parts as links.
  - The in-app "‹ Graphs" back links (`routes/graphs-scorecard.tsx:52`) go: the eyebrow and the tabs replace them.
- **Tabs** are links (`aria-current="page"`). A tab push adds to history, so Back returns to the previous tab.
- **Drawers, dialogs, popovers and sheets** that live in the URL (`?edit=`, `?schedule=`, `?run=`, `?alert=`) push
  when they open and replace when they close. Back therefore closes them and never leaves the page while one is open.
- **Range, filter and view chips** push, so Back restores the previous view.
- A **cross-section deep link** (for example "Open in Graphs ±2 h") carries its context, and Back returns to where
  the person was.

### 2.5 Where alerts, banners and Stop live

- **Open alerts.**
  - The nav count pill appears on every page.
  - On Now, the answer sentence counts them when it is the top condition. Otherwise the "Needs someone" title does.
  - On Activity, the Alerts status line states it ("2 open · …"). Tab labels never carry counts.
  - The count is never shown twice in one page body.
- **Banners.**
  - The one fault banner is a row inside the sticky header, so it never scrolls away. It is worst first, as in
    screens §1.2, and keeps `[data-banner=fault]`.
  - Warn banners sit in flow at the top of `<main>`, above the page header, one line each (`[data-banner=warn]`).
  - Every banner links to the page that explains it, as today.
- **Stop all water** (one shared component, `StopAllWater`) appears in exactly three places:
  - Now, first in the quick actions row: full width on phones and above the fold at 390 × 844.
  - The Water page header actions on all three Water tabs. At 860 px and below it shares the sticky tab row and
    reads "■ Stop all".
  - Inside an `irrigation_fence_breach` alert in Activity.

  A page never holds two Stop all water buttons: on Now, a fence-breach alert in Needs someone points to the quick
  action instead of repeating it. Stop is one tap, has no dialog and is never disabled by the writes flag. A line's
  own Stop lives on its Water row (D-UX-06).

### 2.6 Links between sections

| From | Element | To |
|---|---|---|
| Now hero | Temperature, VPD or RH band bar | `/graphs?range=24h#temperature`, `#vpd` or `#rh` (panel anchors in the chart stack) |
| Now status line | Mode words | `/equipment#why` |
| Now status line | Iris chip | `/plan` |
| Now status line | Outdoor chip | `/graphs?range=24h#temperature` with the outdoor layer on |
| Now status line | "Updated N s ago" (`#data`) | Opens "All readings" in place |
| Now | Zone tile | `/graphs/zones?zone=<zone>` |
| Now | "Details ›" on an alert | `/activity?alert=<id>` |
| Water line menu | "Activity for L01 ›", "Runs of L01 ›", "Graphs ›" | `/activity/log?target=L01`, `/water/history?line=L01`, `/graphs` around its last run |
| Water site card | SAF-4 meter | `/settings?edit=mister_daily_volume_max_gal` |
| Plan | Lane label | `/plan?edit=<param>` (the drawer, in place) |
| Graphs | Setting chip | `/settings?edit=<param>` |
| Setting drawer | "Graph this setting ›", "Activity for this setting ›" | `/graphs?set=<param>`, `/activity/log?target=<param>` |
| Alert | "Open in Graphs ±2 h" | `/graphs?from=…&to=…` |
| Statistics | A day cell or table row | `/graphs?from=<day>&to=<day>` |

"History" now means only two things: Water's History tab and the setting drawer's "Change history". The line card's
"History ›" (`components/line-card.tsx:148`) becomes "Activity for L01 ›".

## 3. Layout system

### 3.1 Page template

```text
DESKTOP (861 px and up)                                       PHONE (860 px and below)
┌ header, sticky ─────────────────────────────────────┐       ┌ header 52 px, sticky ────────────┐
│ lockup · five sections ............... [account v]  │       │ lockup ............ [account v]   │
│ ▌fault row, only while true                         │       │ ▌fault row, only while true      │
└──────────────────────────────────────────────────────┘       └───────────────────────────────────┘
 warn notices, in flow                                          warn notices
 SECTION > VIEW                            [actions]           SECTION
 Page title (h1)                                               Page title
 status line: what matters now, one line                       status line (may wrap)
 [Tab] [Tab] [Tab]                                             [Tab][Tab][Tab]   (sticky on Water)
 ┌ main ─────────────────────────────┐ ┌ aside 22rem ┐         [primary action, full width]
 │ section: h2 · meta     [link >]   │ │ list card    │         ┌ card ───────────────────────┐
 │ cards in l-cols-2, -3 or -4       │ └──────────────┘         └─────────────────────────────┘
 └───────────────────────────────────┘                         one column
 footer: build · Times are America/Denver · links              footer
                                                               ┌ bottom bar 56 px ──────────┐
```

`Page` wraps every screen. `PageHeader` renders the eyebrow, h1, status line, actions and the section's tabs from the
section model (`lib/sections.ts`). The status line's height is reserved, so it never shifts the page when it loads.

### 3.2 Grid and breakpoints

- **Widths.** The shell stays `.shell` at 80rem with weather's gutter. Graphs and Water → Schedules use `.page-wide`,
  which is `--shell-wide` (96rem), because charts and timelines gain from width.
- **Three breakpoints**, each with one job:
  - **600 px:** card grids go from one column to two.
  - **860 px:** navigation (bottom bar or top nav), chart axes and tables-to-rows. This is today's breakpoint,
    unchanged.
  - **1200 px:** the main and aside split.

  They are declared once as constants in `lib/narrow.ts` (`PHONE_PX = 600`, `NARROW_PX = 860`, `WIDE_PX = 1200`)
  and mirrored by the CSS media queries. Weather's 520/700/900 rules stay in weather's sections; new code does not
  use them.
- **Layout classes** (new, in `app/styles/primitives.css`). Weather's `.grid-*` stay byte for byte, unused by new
  code.

| Class | Rule |
|---|---|
| `.l-stack` | Vertical flow with a gap of `--space-4`; `.l-stack-sm` uses `--space-2` and `.l-stack-lg` uses `--space-6` |
| `.l-cluster` | A wrapping row with a gap of `--space-2`, aligned centre |
| `.l-cols-2` | 1 column, then 2 from 600 px |
| `.l-cols-3` | 1, then 2 from 600 px, then 3 from 861 px |
| `.l-cols-4` | 2, then 4 from 861 px (for KPI tiles; 1 column below 360 px) |
| `.l-split` | Main `minmax(0,1fr)` plus aside `var(--aside)` from 1200 px; stacked, main first, below that |
| `.l-rail` | A sticky rail of `var(--rail)` plus content from 861 px. Below that the rail becomes selects above the content |
| `.l-scroll-x` | A labelled horizontal scroll region (`role="region"`, `tabindex="0"`, `aria-label`) for wide tables, the week matrix and long strips. The page itself never scrolls sideways |

### 3.3 Tokens added (D-UX-09)

A third `:root` lives in its own file, `app/styles/tokens.css`, loaded right after `styles.css`. Weather's block and
the §2.2 block stay byte for byte, and `styles.css` keeps exactly two `:root{` blocks. These are layout tokens only;
every new visual uses the existing colour tokens.

```css
:root{
  --space-1:.25rem;--space-2:.5rem;--space-3:.75rem;--space-4:1rem;--space-5:1.5rem;--space-6:2rem;--space-7:3rem;
  --fs-label:.72rem;--fs-xs:.75rem;--fs-sm:.875rem;--fs-md:1rem;--fs-lg:1.25rem;--fs-xl:1.75rem;
  --shell-wide:96rem;--aside:22rem;--rail:15rem;--tap:2.75rem;--bar-h:3.5rem;--header-h-phone:3.25rem;
  --z-raised:3;--z-sticky:10;--z-nav:20;--z-popover:30;--z-toast:40;
}
```

The z-index literals in use map onto these tokens:

| Literal today | Token |
|---|---|
| 3 and 5 (chart tooltip, raised rows) | `--z-raised` |
| 10 (header, sticky rows) | `--z-sticky` |
| 20 (skip link, bottom bar) | `--z-nav` |
| 30 (menus, popovers) | `--z-popover` |
| 40 (toasts) | `--z-toast` |

Native `<dialog>` uses the top layer and needs none.

### 3.4 Type and spacing

| Role | Spec | Replaces |
|---|---|---|
| Page title (h1) | Weather's h1: clamp(1.75rem, 3.6vw, 2.6rem), 620, −0.02em | — |
| Section title (h2) | Weather's h2: clamp(1.2rem, 2vw, 1.45rem), 620 | The 12 eyebrow-styled h2/h3 headings |
| Card or row title (h3) | Weather's h3: 1.02rem, 620 | — |
| Hero value | Weather's `.hero-temp` | — |
| KPI value | `--fs-xl`, 620, tabular figures | — |
| Row value | `--fs-lg`, 600, tabular figures | — |
| Body | `--fs-md` | — |
| Meta line (`.meta`) | `--fs-sm`, `--muted` | The 15+ muted-small classes (`.small`, `.state-note`, `.line-note`, `.zone-band`…) |
| Small (axis ticks, footnotes) | `--fs-xs` | .66–.8rem one-offs |
| Label (`.eyebrow`, KPI and table-head labels) | Weather's mono, uppercase, .1em, `--forest` (mint on ink) | — |
| Pill | Weather's `.tag`, `.badge`, `.status` | — |

| Gap | Desktop | Phone |
|---|---|---|
| Page header to content | `--space-5` | `--space-4` |
| Between sections | `--space-6` | `--space-5` |
| Section header to its content | `--space-3` | `--space-3` |
| Card grid gap | `--space-4` | `--space-3` |
| Card padding | `--space-4` (weather's 1rem) | `--space-4` |
| Rows inside a card | `--space-3`, with a 1px `--line` divider | `--space-3` |
| Inline items (chips, buttons) | `--space-2` | `--space-2` |

### 3.5 Page header and section header

```text
GRAPHS > ZONES                                                    [Export v]   actions: at most 2 buttons, primary last
South zone                                                                     h1
Last 24 h · one point per 5 min · in band 94% · probe out, using peers         status line: one line, .meta, reserved
[Climate] [Zones] [Statistics]                                                 tabs: Chips (nav), current filled ink

Watering minutes · last 7 days                          [7 d][30 d]  All runs >   section header: h2, meta, chips, link
```

- **Page header.** The status line is the page's one-sentence state, from data, and may wrap on phones. Actions sit
  at the right of the h1 row on desktop; on phones they sit under the status line, and a primary action there is
  full width.
- **Section header** (`SectionHeader`): an h2, an optional count or meta, optional chips and one "All … ›" link at
  the right. It is the only section heading. The eyebrow is never a heading; it labels provenance or cadence above a
  heading.

### 3.6 Density rules

1. Each fact appears once per screen. The nav is global and does not count.
2. A row or card shows at most: a title, one state sentence, one state pill, one visual (bar, meter, strip or
   sparkline) and three meta facts. More goes to Details, the drawer or the menu.
3. A " · " chain has three items at most. Longer runs become a `DataList`.
4. Numbers use tabular figures and are right-aligned in tables. Units sit in the column header when a column shares
   one. Never colour a number: colour its verdict word or pill.
5. Prose lines stay under 46rem.
6. An empty state is one line plus one action ("No runs in 7 days · Show 30 days").
7. On phones there are no side-by-side cards below 600 px, tables become stacked rows, and every action has a 44 px
   target.
8. Fixed heights: every chart, track, meter and skeleton reserves its final height, so loading never shifts the page.

## 4. Cards and tiles

### 4.1 Anatomy

```text
┌───────────────────────────────────────────────────────────┐
│ LABEL · PROVENANCE (optional eyebrow)                      │
│ Title (h3)                        [ONE PILL]  [action] [⋯] │  head
│ Value or visual: band bar, meter, sparkline, track         │  body
│ State sentence: "Running · VPD · since 06:58"              │
│ meta: source · age                          Details >      │  foot (.meta)
└───────────────────────────────────────────────────────────┘
 abnormal: a 3 px left rule in --amber (warning) or --fault (fault); the background stays white
```

`Card` is the static frame: weather's `.card`, 1px `--line`, `--radius`, `--shadow-xs` and 1rem padding. `Panel` is
the data card. It keeps its state machine (`components/panel.tsx`) and takes the new head. The hand-rolled cards
(`line-card`, `mister-zone`, `burst-panel`, `now-water`, `equipment-error`) migrate to one of the two.

### 4.2 Variants

| Variant | Made of | Used on |
|---|---|---|
| **Status hero** (ink) | The answer h1, hero value, three band bars, a status line and "All readings" | Now only |
| **Metric tile** (`MetricTile`) | A label, value and unit, a delta line ("▲ 1.3 pts vs previous 30 d"), one visual (sparkline, band bar or meter) and meta | Now zones, Statistics, Water history, Equipment controller |
| **Object row** (`ObjectRow`) | A title, kind tag (CLEAN, FERT), state sentence, optional strip, one primary action, Stop when relevant and a ⋯ menu | Water lines, alerts, schedule rules, equipment, settings (as table rows) |
| **List card** (`ListCard`) | A section-like head (title, count, "All … ›"), rows (`DataList` or `ObjectRow`) and an empty line | Needs someone, Site today, Recent activity |
| **Chart card** (`ChartStack`, `ChartGrid`) | A takeaway title, panel header rows (statistics, legend and readout), fixed-height plots and a foot (resolution, data table, CSV) | Graphs, Water history, Plan, Equipment |

### 4.3 States

Every card variant renders every state at its reserved height.

| State | Shows |
|---|---|
| Loading | A skeleton at the final height; a refetch keeps the old render |
| Ready | The content with its age when it differs from the page's |
| Empty | One line of what would appear, plus one widening action |
| Stale | Values muted, a STALE pill, "Last data 14 min ago (06:02)" and no verdict |
| Not reporting | Device values STALE with the banner's reason line |
| Unknown | "—" plus its reason; visuals hatched |
| Error | "Couldn't load X." plus Retry, in that card only |
| Busy | "Busy, retrying in 5 s" over the old render |
| API down | Neutral everywhere (`useApiDown()`): no green fills, no verdicts and no "in band" words |

### 4.4 Status colour

| Condition | Treatment |
|---|---|
| Normal | No colour: ink on white, white and mint on the hero |
| Running or active | A 0.55rem dot in the equipment or cause token (`--eq-*`) beside the word "Running"; no tint |
| Pending (REQUESTED) | `.status-pending` pill; a dashed `--c-requested` underline on the requested value |
| Warning or out of band | A 3 px `--amber` left rule plus the pill or the verdict word in `--warn-text` |
| Fault or critical | A 3 px `--fault` left rule plus `.status-failed` or `.tag-fault` |
| Stale or unknown | Muted values, STALE, hatching in visuals |
| CONFIRMED | Pill only while the newest write is under 10 minutes old; afterwards the value and its age alone (D-UX-07) |
| Manual | MANUAL pill (it asks something of people: Iris skips it) |
| AI, provenance | Text marks in the meta line ("AI", "PEER", "DERIVED"), mono and muted; amber only when the provenance is the news (a zone fallen back to peers) |

**Pill priority** for the one pill per head: NOT CONFIRMED, REQUESTED, HELD, STALE, FAULT, MANUAL, then CONFIRMED.

### 4.5 Actions on cards

- **One primary action, chosen by state:**
  - running: Stop;
  - idle and runnable: Run…;
  - faulted: Clear fault;
  - paused: Resume;
  - a setting: Edit.
- **The emergency action** (a line's Stop) shows whenever it can matter (D-UX-06). Everything else goes in the ⋯ menu
  (`Menu`): a disclosure, not an ARIA menu, holding links and buttons, with Escape to close and 44 px items.
- **No nested interactives.** A card with buttons is never itself a link. Its detail opens from the title link or
  "Details ›".
- **Viewers** get the same cards with no write controls. Menus keep their links.

### 4.6 Micro-visualizations

These are SVG or HTML in `components/data/`. They never load uPlot, so Now stays free of chart code. SVG uses
attributes (`x`, `width`, `d`) and token classes; HTML positions use CSSOM custom properties. Neither uses a style
attribute. Each is `aria-hidden` when the same numbers sit beside it as text; otherwise it is `role="img"` with an
`aria-label` that states the numbers.

| Primitive | Anatomy and tokens |
|---|---|
| `BandBar` | One track, the band as a box on it, and the value as a dot; nothing else is drawn (Jason 2026-11-05, #1337: today's low–high is in the bar's accessible name and in "All readings", not drawn). **On white:** a 2 px `--line-strong` track; the band as a 10 px `--c-band-wash` box with 1 px `--c-band-edge` ends; the value as a 10 px `--ink` dot with a white ring. **On the hero:** a 2 px `--ink-muted` track at 45% opacity; the band as a 12 px `--c-band-edge` box (5.3:1 on ink) with 1.5 px `--mint` ends; the value as a 12 px `--mint` dot with a 2 px `--ink` ring. **Scale:** the band plus one band width each side, fixed, so the box keeps its place and width and only the dot moves. A value off scale clamps to the edge with an arrowhead. Unknown means no dot and a hatched track. RH has no band: its bar reads on 0–100% with the value and no band box |
| `Meter` | A 6 px `--field` track with a 1 px `--line` edge. The fill uses the domain token: `--eq-water` for drip minutes and gallons, `--eq-mister` for mist minutes, `--eq-fert` for feeds, `--muted` for anything else. Planned-remaining minutes are a dashed outline in the same token; the cap is a 2 px `--ink` tick. At or over cap, the tick turns `--fault`, the fill stops at 100% and the words read "cap reached" or "+12 min over" |
| `Sparkline` | Line: 1.5 px in the metric token (`--c-temp`, `--c-vpd`, `--c-rh`) over a `--c-band-wash` band when known; gaps break; the last point is a 3 px dot. Bars: 2–4 px bars in the domain token or `--muted`. 28 px tall in tiles, 20 px in rows |
| `Delta` | "▲ 1.3 pts vs previous 30 d" in ink. The arrow and the words carry direction; never green or red |
| `StatusStrip` | Day cells at least 8 px wide with 2 px gaps. `data-level` 0–3 maps to `--tint-ok`, `--tint-warn`, `--amber` and `--fault` (quiet when good, loud when bad). No data is a `--line` diagonal hatch; a partial day has a dashed `--muted` outline. Each cell is a link or button with an `aria-label` ("Oct 28: temperature in band 82%"), with a legend and an equivalent table. Class prefix `strip-`, never `.calendar` |
| `TimeTrack` | HTML rows (28 px desktop, 24 px phone) over a time scale from `lib/time-scale.ts`: local ticks, DST-correct, every 3 h on desktop and 6 h on phones. **Spans:** done is a solid fill in the cause or equipment token; planned is a 1.5 px dashed outline on white; skipped or blocked is a `--line` hatch with a `--muted` outline; unknown is a `--muted` hatch; at least 6 px wide; labels sit outside when there is no room. **Background:** night (before sunrise, after sunset) is a `--field` wash. **Now** is a 1.5 px `--ink` rule. **Markers** (Iris, plan) are a 2 px dashed `--ink` tick with a label. A span with detail is a `<button>` named in words ("L01 Wall drip, Tue 07:31, 12 min, done, ok"). The rows are a `<ul>`, so the track reads as a list |

## 5. Screens

Each screen keeps its reads, polls, write hooks and honesty rules from screens §2–§10. Only what is listed here
changes. The data hooks named under "Hooks kept" survive the rebuild. Specs move off class selectors onto them.

### 5.1 Now (`/`)

| | |
|---|---|
| Purpose | Five seconds to know: is everything OK, what is running, is water fine, what needs someone. The caretaker's three on-site actions sit here |
| Primary task | Glance, then acknowledge, stop or burst |
| Above the fold | **360 × 740:** the answer, the hero temperature, "Updated N s ago", the three band bars, the status line and (operators) Stop all water. **1440 × 900:** the hero, quick actions, Needs someone and Water today |
| Moves in | Bursts (from Equipment, D-UX-05); occupancy (into the status line); plan transitions and next runs (into "Running and next") |
| Removed | The eight hero tiles: VPD and RH become band bars; Mode, Iris, Outdoor and Data become the status line; Water and Alerts were duplicates. The separate Running and Coming up panels are merged. The repeated counts and "next run" lines go |
| New | Time in band today against yesterday (from API-04 `daily.temp_in_band_pct` and `daily.vpd_in_band_pct`, read with the three climate keys' min and max over today in one 60 s read, or two if the API keeps daily keys apart); quick actions; the "Running and next" track (API-05 for the last 6 h, API-IRR-02 and API-07 for the next 12 h, solar from API-IRR-02); a band bar and 24 h VPD sparkline per zone; mister faults and the "N settings not confirmed" link in Needs someone |
| Hooks kept | `.hero-temp`, `#data`, `[data-tile=vpd\|rh\|mode\|iris\|outdoor\|data]` (on the band bars and status chips), `[data-chip]` (running, burst, occupancy), `.now-water`, `[data-water]`, `#needs-someone`, `.panel-empty`, `#coming-up` with `[data-coming]` (on "Running and next"), `#zones [data-zone]`, `[data-zone-tag]` |

```text
1440
┌▓ ink hero ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓┐
│ GREENHOUSE · LONGMONT                      TEMP  ├───[▓▓▓▓▓▓▓●▓▓▓]────┤  64.8 °F   band 62.0–70.0          │
│ 1 thing needs someone                      VPD   ├─[▓▓▓▓●▓▓▓▓▓]───────┤  0.62 kPa  band 0.45–0.80          │
│ 64.8 °F                                    RH    ├──────────●─────────┤  71.0%     no band                 │
│ in band · ● Updated 20 s ago               IN BAND TODAY  temperature 96% · VPD 88%  (yesterday 94% · 91%) │
│ Venting to cool · Iris OK, SUNRISE plan to 17:30 · Outdoor 41.2 °F · Someone inside: misters wait (1 min)  │
│ > All readings · sources, times, mode code, settings pushed 4 min ago                                      │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
[■ Stop all water]   [Fans 15 min]   [Humidify 5 min]   [Vent bypass 10 min]                    operators only
┌ NEEDS SOMEONE ────────────────────────────────────┐ ┌ WATER TODAY ─────────────────────────────────────────┐
│▌WARNING  Fan 2 relay stuck: on with no command    │ │ Wall    [▓▓▓░░░░░░░|]  12 / 60 min  last 07:31, 12 min │
│          fan2 · since 06:02 (40 min) · x3 since Mon│ │ Center  [▓▓▓▓▓░░░░░|]  46 / 120     misting now (VPD)  │
│          [Acknowledge]                  Details > │ │ South   [░░░░░░░░░░|]   0 / 45      3 days ago         │
│ 2 settings not confirmed >                        │ │ West    [░░░░░░░░░░|]   0 / 45      9 days ago         │
│                                     All alerts >  │ │ SAF-4   [▓░░░░░░░░░|]  21 / 360 gal · no flow          │
└───────────────────────────────────────────────────┘ │                                           Water >     │
                                                      └───────────────────────────────────────────────────────┘
┌ RUNNING AND NEXT ─ last 6 h, next 12 h ───────────────────────────────────────────────────────────────────┐
│             04:00     06:00     08:00 |   10:00     12:00     14:00     16:00     18:00     20:00          │
│ Heat        ███ ██ ██  █          |                                                                       │
│ Vent, fans          ▒▒▒▒▒▒▒▒▒▒▒▒▒▒|                                                                       │
│ Fog, mist   ▏ ▏▏  ▏ ▏▏ ▏▏ ▏▏▏ ▏▏▏▏|               [13:00 L04 4 x 45 s]                                    │
│ Drips                 ▌07:31      |                                       [16:00 L01 10 min]              │
│ Iris, plan  ^SUNRISE              |    ^SOLAR_MAX    ^13:00 engage 1.60 -> 1.70 (AI)                      │
│ ░░ night ░░ sunrise 06:31                                                      sunset 16:52 ░░ night ░░  │
│ Later: L02 Wall feed Sun 09:00 · L07 South mister: no schedule                                            │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌ ZONES ────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ North           │ Center   HOUSE  │ South    PEER   │ East            │ West                               │
│ 65.1 °F 0.60    │ 64.8 °F 0.62    │ 65.4 °F 0.66    │ 64.0 °F 0.58    │ 65.9 °F 0.71                       │
│ [▓▓●▓] ~~~~~~~  │ [▓▓●▓] ~~~~~~~  │ [▓▓▓●] ~~~~~~~  │ [▓●▓▓] ~~~~~~~  │ [▓▓▓●] ~~~~~~~                     │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

```text
360 × 740, operator
┌────────────────────────────────────┐
│ [V]erdify · DASHBOARD [OPERATOR v] │ 52
│▓ GREENHOUSE · LONGMONT            ▓│
│▓ All OK                           ▓│ h1, the answer
│▓ 64.8 °F  in band                 ▓│ .hero-temp
│▓ ● Updated 20 s ago               ▓│
│▓ TEMP ├─[▓▓▓●▓▓]──┤ 64.8 °F        ▓│
│▓ VPD  ├[▓▓●▓▓▓]───┤ 0.62 kPa       ▓│
│▓ RH   ├─────●─────┤ 71.0%          ▓│
│▓ In band today: temp 96% · VPD 88%▓│
│▓ Venting to cool · Iris OK · 41 °F▓│
│▓ Nobody seen: misters may run     ▓│
│▓ > All readings                   ▓│ hero about 340 px
│ [■ Stop all water                ] │ 48, ends near y = 470
│ [Fans 15 min][Humidify][Vent 10 m] │ 48
│ NEEDS SOMEONE                      │
│ No open alerts · 3 resolved today >│
├──────┬──────┬──────┬────────┬──────┤
│ Now  │Water │Graphs│Activity│Control│ 56
└──────┴──────┴──────┴────────┴──────┘
 below: Water today (zone meters) > Running now and Next 12 h (two lists) > Zones (2-up tiles)
```

Quick actions show only for operators. Before the irrigation firmware the burst buttons are not rendered, and the row
holds Stop all water and the existing "Bursts arrive with the irrigation firmware" line. On phones, "Running and next"
is two lists: "Running now" (`[data-chip]` rows with "since HH:MM" and the cause) and "Next 12 h" (`[data-coming]` rows
for runs, plan transitions, the Iris trigger, sunrise and sunset). CSS chooses between the track and the lists, and
both render, so hydration never reflows them.

### 5.2 Water → Today (`/water`)

| | |
|---|---|
| Purpose | What can water, what is watering, what has watered today and what comes next, zone by zone |
| Primary task | Check a zone, then run or stop a line |
| Above the fold | **Phone:** the header with its sync line, the sticky row of tabs and "■ Stop all", the Site today card, and Wall with L01. **Desktop:** the header, Wall and Center, and Site today in the aside |
| Layout | `l-split`. The main column holds zone cards in fixed order: Wall (L01, L02 with L03 as its sub-row), Center (L04, L05, L06), South (L07, L08), West (L09, L10), then Fog (L11, climate only). The aside holds Site today, sticky on desktop. The order never changes with state, which keeps spatial memory; the header status line names whatever is running, with links to its row |
| Zone card head | The zone name and kind, a minutes-against-cap `Meter` and the last watered time. Mister zones add the "VPD control" and "Schedule" switches with the VPD source ("house average", "peer probes (address 4 out)") |
| Line row | `ObjectRow`: "L01 Wall drip", CLEAN or FERT, the state sentence from `irr_state` ("Idle · last 07:31, 12 min · ok", "Paused until Sat 09:00 · VPD misting continues", "Faulted: no misting until midnight"), a today strip (`TimeTrack`, one row 00–24 h: runs done, planned and skipped, plus the now rule) with "next 16:00 · 10 min", one primary action, Stop (D-UX-06) and the ⋯ menu (Enabled switch with its check sheet, Options…, Pause until…, "Activity for L01 ›", "Runs of L01 ›", "Graphs ›") |
| Moves | The limits strip splits: per-zone caps go to the zone heads, and SAF-4, the soft budget, flow, occupancy and fallback go to Site today. The mister association goes to the zone heads, the program banner to the header status line, and Pause lines… to the header actions |
| Removed | The separate "Limits today", "Mister zones" and "Lines" sections; the roughly ten lines of text per card; four of the up to seven inline controls (into the menu); "History ›"; running-first sorting on phones |
| New | Zone cards with cap meters in their heads; a today strip per line; Site today; the state-aware Stop; the row menu |
| Hooks kept | `[data-line]` with `data-state` on each row's `article` (named by its h3, "L01 Wall drip"), `[data-program]`, `[data-limit]`, the button names `Run…`, `Run 10 min`, `Stop`, `Stop all water` and "■ Stop all", and `.mister-source` text |

```text
1440 (page-wide off)
WATER
Water today                                                               [Pause lines…]  [■ Stop all water]
Program rev 42 on controller · confirmed 2 min ago · L04 Center mister running (VPD)
[Today] [Schedules] [History]
┌ WALL · drip ────────────────── [▓▓▓░░░░░░|] 12 / 60 min · last 07:31 ┐ ┌ SITE TODAY ─────────────────────┐
│ L01 Wall drip     CLEAN  Idle · last 07:31, 12 min · ok              │ │ SAF-4   [▓░░░░░░░|] 21 / 360 gal│
│   ├──────█│────────────[ ]────────┤ next 16:00 · 10 min             │ │ Soft budget [▓▓░░|] 40 / 120 gal│
│                                      [Run…]  [Stop]  [⋯]             │ │ Flow: none with valves closed   │
│ L02 Wall feed     FERT   Off · no check recorded           [Stop][⋯] │ │ Occupancy: nobody seen (05:10)  │
│   └ L03 fert master: opens with a feed                              │ │ Fallback misting: off           │
└──────────────────────────────────────────────────────────────────────┘ └─────────────────────────────────┘
┌ CENTER · misters and drips ───── [▓▓▓▓▓░░░|] 46 / 120 min · now ─────┐
│ VPD control [ON]  Schedule [OFF]  · VPD from the house average        │
│▌L04 Center mister CLEAN  ● Running · VPD · since 06:58      [■ Stop][⋯]│  running: rule in --eq-mister
│   ├▏▏ ▏▏▏ ▏ ▏▏▏▏│───────────[ ]──────┤ next 13:00 · 4 x 45 s        │
│ L05 Center drip   Reserved · disabled                        [⋯]      │
│ L06 Center feed   Reserved · disabled                        [⋯]      │
└───────────────────────────────────────────────────────────────────────┘
 SOUTH · WEST · FOG (climate only, read-only)
```

```text
360
┌────────────────────────────────────┐
│ WATER                              │
│ Water today                        │
│ Rev 42 on controller · 2 min ago   │
│ [Today][Schedules][History][■ Stop all] sticky under the header
│ ┌ SITE TODAY ─────────────────────┐│
│ │ SAF-4 [▓░░░░░|] 21 / 360 gal    ││
│ │ No flow with valves closed      ││
│ │ Someone inside: misters wait    ││
│ └─────────────────────────────────┘│
│ ┌ WALL ───── [▓▓░░░|] 12 / 60 min ┐│
│ │ L01 Wall drip · CLEAN           ││
│ │ Idle · last 07:31, 12 min · ok  ││
│ │ ├───█│──────[ ]───┤ next 16:00  ││
│ │ [Run…                    ] [⋯]  ││ Run full width (no Stop while idle, D-UX-06)
│ │ L02 Wall feed · FERT · Off  [⋯] ││
│ └─────────────────────────────────┘│
```

### 5.3 Water → Schedules (`/water/schedules`)

| | |
|---|---|
| Purpose | What runs when, how long, how close together and against which cap; which rules produce it; change them |
| Primary task | Check a day's runs, then create or edit a schedule |
| Above the fold | **Desktop:** the week strip, the whole day timeline and the head of the rules list. **Phone:** the week strip and today's agenda |
| Moves | "Next 7 days" and "Last 7 days" merge into one week strip with past and future side by side. The disabled-schedule footnote moves under each line in the rules list. The one-sentence key becomes a Key popover |
| Removed | The text-only week table as the main view; the separate past mode; the phone's horizontal chip day switcher |
| New | The day timeline with night shading, cap meters, VPD context and pause bands; the rules list with "changed by"; create from an empty slot; the block popover; the agenda; editor preview words before Save |
| Hooks kept | `.week-table` with `tr[data-line] td[data-day]` (on the week matrix), `.week-days` button names, "+ New schedule", "Pause <line> until after this run", `[data-write-control]` on link-styled writes |

The design is in §6.

### 5.4 Water → History (`/water/history`)

| | |
|---|---|
| Purpose | Did each zone get the water intended over a period, and what ran, why, and how it ended |
| Primary task | Compare minutes or gallons per zone across a period; find one run |
| Above the fold | The header with the period summary, the range chips, the KPI tiles and the start of the minutes chart |
| Layout | One page range (`range=7d\|30d\|90d\|365d`) with Group by (zone, line, cause) and Measure (minutes, gallons). Then KPI tiles (`l-cols-4`): watering time, gallons, runs, and missed, short or cap, each with a delta against the previous equal period. Then minutes or gallons by group (`MinutesByZone`, from API-IRR-10 up to 400 days). Then Daily water use (API-21, which serves 30 or 90 days: it shows 30 for ranges up to 30 days and 90 above, and says so in its section meta). Then Runs |
| Runs | A `Table` with a sticky head; filters for line, cause, outcome and dates applied on change (no Apply button anywhere); 31 days per page with "Load older"; "CSV of these runs"; each row's "Graphs ›" |
| Moves | The per-chart day chips (7/30 d and 30/90 d) become one page range; charts that cannot follow it say what they show |
| Removed | The eyebrow-styled section headings; the mixed filter models (every filter now applies on change) |
| New | KPIs with deltas; the gallons measure; 90 and 365 days; CSV for minutes, water use and runs |
| Hooks kept | `.minutes-row`, `.minutes-today`, `.plot-water-use canvas`, `.water-summary`, the `/Runs, newest first/` table, the "Daily water use" region and the "Water use days" and "Split water use by" lists |

```text
1440                                                                 360
WATER                                                                 WATER
Water history                                    [■ Stop all water]   Water history
Last 7 days: 6 h 12 min watering · 412 gal · 21 runs, 1 missed        7 d: 6 h 12 min · 412 gal · 1 missed
[Today] [Schedules] [History]                                         [Today][Schedules][History][■ Stop all]
[7 d][30 d][90 d][365 d]   Group by [zone v]   Measure [minutes v]    [7 d][30 d][90 d][365 d]
┌ WATERING ──┐┌ GALLONS ──┐┌ RUNS ──────┐┌ MISSED, SHORT, CAP ┐       [zone v] [minutes v]
│ 6 h 12 min ││ 412 gal   ││ 21         ││ 1 missed           │       ┌ WATERING ┐┌ GALLONS ┐
│ ▲ 18% vs   ││ ▲ 9%      ││ ▼ 2        ││ Oct 31 L09: no     │       │ 6 h 12 m ││ 412 gal │
│ prior 7 d  ││           ││            ││ clock              │       └──────────┘└─────────┘
└────────────┘└───────────┘└────────────┘└────────────────────┘       ┌ RUNS ────┐┌ MISSED ─┐
┌ WATERING MINUTES BY ZONE · 7 DAYS ─────────────────────── CSV ┐       minutes by zone (rows, 84 px)
│ Wall    ▁ ▃ ▃ ▅ ▃ ▃ ▆:   today 10 min · cap 60                │       daily water use
│ Center  ▆ ▇ ▅ ▆ ▇ ▆ ▃:   today 46 min · cap 120               │       Runs as stacked rows
└──────────────────────────────────────────────────────────────┘
┌ DAILY WATER USE · 30 DAYS (the shortest this chart serves) ───┐
┌ RUNS · newest first · [Line v][Cause v][Outcome v] From To · CSV ┐
```

### 5.5 Graphs → Climate (`/graphs`)

| | |
|---|---|
| Purpose | What happened and what is planned, over any range, with cause and effect in one view |
| Primary task | Read the climate against the band over a period and explain a change |
| Above the fold | **Desktop:** the story line, the toolbar, the temperature and VPD panels. **Phone:** the story line, the range chips, and the temperature panel with its readout |
| Story line | The page status line, generated from the same reads: "Last 24 h: in band 96% of points · 2 excursions: too dry 13:10–13:40 (peak 1.31 kPa), too cold 05:50–06:05". It lists at most three excursions, ranked by duration times depth. Each one links to its window ±30 min |
| Toolbar | Range chips (Today, 24 h, 72 h, 7 d, 30 d, 90 d), ‹ › to step the window by its own length, Zoom out (×2) and Custom… (From and To days, as today). Then Preset (Climate, Water and soil, Light, Controller), which sets `m=`. Then Layers (outdoor, forecast, events by source, min–max envelope, compare: none, yesterday or last week), + Metric, + Setting, and Export (CSV of this window, data table) |
| Chart | One `ChartStack` (§7.1): Temperature, VPD and RH panels with picked-metric panels, the event rail on top, the equipment lanes below (open on desktop, a collapsed disclosure on phones) and one bottom axis |
| Moves | The range chips, custom range, pickers and setting chips into one toolbar; the legend into the panel header rows; the resolution line from the page head to the stack's foot; the scorecard link into the Statistics tab |
| Removed | Per-panel legends, summary sentences, tools and "Data table" disclosures; the per-chart tooltips; the 150 px panels; the separate "On charts" chip row (its items move into Layers and the setting chips) |
| New | The story line; presets; Today and 90 d; window steps and Zoom out; the Layers menu with excursion shading, the min–max envelope, compare and the event rail |
| Hooks kept | The list names "Range", "Settings drawn as steps", "Metrics on the charts" and "Overlays"; the link names "CSV of this window" and "CSV of this table"; `.resolution-line` (now in the stack's foot); `.chart-message`; `[data-equipment]` lane rows. `.linked-panels figcaption` and `.plot-small` are replaced, and graphs.spec moves to the panel header rows (`[data-panel=temperature]`) |

```text
1440 (page-wide)
GRAPHS
Climate                                                                                          [Export v]
Last 24 h: in band 96% of points · 2 excursions: too dry 13:10–13:40 (peak 1.31 kPa), too cold 05:50–06:05
[Climate] [Zones] [Statistics]
[Today][24 h][72 h][7 d][30 d][90 d][Custom…]  [<][>] [Zoom out]     Preset [Climate v]  [Layers v] [+ Metric] [+ Setting]
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ events   ◆        ▲                ▮  ▮             ◆AI                    |                                  │ 20 px rail
│ TEMPERATURE °F   in band 98% · low 61.2 (05:10) · mean 66.0 · high 71.9 (14:22)          ● Indoor ─ Outdoor  │ header row
│  72 ┤▒▒▒▒▒▒▒▒/‾‾‾‾‾‾\▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒|- - plan - - - - - - -           │ 220 px
│  62 ┤▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒|                                  │
│ VPD kPa          in band 91% · low 0.41 · mean 0.66 · high 1.31 (13:22)                                       │
│ 1.2 ┤                 ┌─▲ excursion shaded --tint-warn ─┐                                                    │ 200 px
│ RH %             no band · low 48.1 · mean 63.0 · high 82.4                                                  │ 160 px
│ > Equipment  Heat 1 ▇ ▇   Fan 1 ▇▇▇   Vent ▇▇▇▇   L04 ▏▏▏▏   (20 px rows)                                    │
│        00:00          03:00          06:00          09:00          12:00          15:00     Now               │ one axis
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
One point per 5 min · times America/Denver · CSV of this window · Data table >
 while the cursor is on the plot, every header row reads the cursor instead: "14:22  71.9 °F · band 62.0–70.0 · above"
```

```text
360
GRAPHS
Climate
24 h · in band 96% · 2 excursions >
[Climate][Zones][Statistics]
[Today][24 h][72 h][7 d][30 d][Custom]
[<][>]  [Layers]  [+ Metric]
┌──────────────────────────────────┐
│ TEMPERATURE °F  64.8 now         │  readout row: cursor values while scrubbing
│ (plot 160 px)                    │
│ VPD kPa  0.62 now                │
│ (plot 150 px)                    │
│ RH %  71.0 now                   │
│ (plot 120 px)                    │
│ 00:00   06:00   12:00   Now      │
└──────────────────────────────────┘
> Equipment lanes (collapsed)
```

### 5.6 Graphs → Zones (`/graphs/zones`)

| | |
|---|---|
| Purpose | Compare the microclimates and see which probe each number comes from |
| Primary task | Spot the zone that drifts from its band |
| Layout | A `ChartGrid` of small multiples: rows are metrics (temperature and VPD by default; RH on `metric=`), columns are zones (North, Center, South, East, West). The y axis is uniform per row, each cell has its zone's band wash, the cursor is shared and plots are 120 px. Each column head shows the zone name, tag (MEASURED, PEER or HOUSE as text, amber when PEER), the current value and a link to focus |
| Focused (`?zone=south`) | h1 "South zone" and the eyebrow "GRAPHS › ZONES": a full `ChartStack` for the zone with its band and the controller's dotted target, as today |
| Phone | Metric chips (one metric at a time) and five stacked 110 px plots, each headed by the zone, tag and value |
| Moves | The one-zone page becomes the drill-down from the grid; Now's zone tiles still deep-link to it |
| Removed | The "‹ Graphs" back link; one zone at a time as the only view |
| New | Small multiples on shared scales with one cursor; the Zones tab, so the view is reachable from Graphs |
| Hooks kept | The h1s "South zone" and "West zone", the list "Zone" and `[data-zone]` |

```text
1440 (page-wide)
GRAPHS
Zones
Last 24 h · each zone against its band · south from peer probes (address 4 out)
[Climate] [Zones] [Statistics]
[24 h][72 h][7 d]   Metrics [Temperature][VPD][RH]
            North >          Center > HOUSE    South > PEER     East >           West >
TEMP °F   ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐
          │▒▒/‾‾\▒▒▒▒▒▒│   │▒▒/‾‾\▒▒▒▒▒▒│   │▒/‾‾‾\▒▒▒▒▒▒│   │▒▒/‾\▒▒▒▒▒▒▒│   │▒/‾‾‾‾\▒▒▒▒▒│   shared y
          └────────────┘   └────────────┘   └────────────┘   └────────────┘   └────────────┘
          65.1 now          64.8             65.4             64.0             65.9
VPD kPa   ┌────────────┐   ...
```

### 5.7 Graphs → Statistics (`/graphs/statistics`)

| | |
|---|---|
| Purpose | Is the experiment improving week to week? Time in band, water, Iris and alerts per day |
| Primary task | Read the trend, find a bad day and open it |
| Above the fold | The window chips, the four KPI tiles and the day-by-day strip |
| Layout | `days=30\|90\|365` or a custom range. Then four KPI tiles: temperature in band, VPD in band, water (gal per day and total) and Iris (required plans written and missed). Each has a delta against the previous equal window and a sparkline. Then "Day by day": `StatusStrip` rows for temperature in band, VPD in band, Iris missed and alerts raised, plus a gallons bar row, with every cell opening that day in Climate. Then "Typical day" (§7.2) and "Daily range" side by side (`l-cols-2`). Then "Time in band per day" (two bar rows with a dotted 90% target). Then "Data": the former scorecard table for the window, with day/night time in band for the last 3 days only (Q-12) and "CSV of this table" |
| Levels | **Time in band:** 95% and up is 0; 85–95 is 1; 70–85 is 2; under 70 is 3. **Iris missed:** 0 is 0; 1 is 2; 2 or more is 3. **Alerts raised** (critical and warning): 0 is 0; 1–2 is 1; 3–9 is 2; 10 or more is 3 (the EEMUA 191 flood) |
| Moves | The scorecard's number table into the "Data" disclosure; its CSV with it |
| Removed | The fixed 30-row, 15-column table as the whole page; the "Scorecard ›" link hanging off a chip row |
| New | 90- and 365-day windows; KPI tiles with deltas; the day-by-day strip; the typical day; the daily range; per-day time-in-band bars; a drill-down from every day into Climate |
| Hooks kept | The h1 becomes "Statistics" (graphs.spec updates). `scorecard-table` keeps its table hooks inside "Data" |

```text
1440 (page-wide)
GRAPHS
Statistics
Last 30 days · whole-day time in band; the day/night split covers the last 3 days only
[Climate] [Zones] [Statistics]
[30 d] [90 d] [365 d] [Custom…]
┌ TEMPERATURE IN BAND ┐ ┌ VPD IN BAND ────────┐ ┌ WATER ──────────────┐ ┌ IRIS ───────────────────┐
│ 94.2%               │ │ 88.0%               │ │ 14.2 gal/day        │ │ 41 of 42 required plans │
│ ▲ 1.3 pts vs prior  │ │ ▼ 2.1 pts           │ │ 426 gal · ▲ 8%      │ │ 1 missed (Oct 28)       │
│ ▁▂▃▅▆▇▇▆▇▇          │ │ ▃▃▂▅▆▆▇▅▆▇          │ │ ▂▃▂▅▃▂▃▄▃▂          │ │ strip ▢▢▢▢▢▢■▢▢▢        │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘ └─────────────────────────┘
┌ DAY BY DAY ─ Oct 6 ........................................................... Nov 4 ┐
│ Temperature in band  ▢▢▢▢▣▢▢▢▢▢▣▣▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢◫                                  │
│ VPD in band          ▢▢▣▣▢▢▢▢▢▢▢▣▢▢▢▢▢▢▢▢▢▣▢▢▢▢▢▢▢◫                                  │
│ Iris missed          ▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢■▢▢▢▢▢▢▢                                  │
│ Alerts raised        ▢▢▣▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢                                  │
│ Gallons              ▁▂▁▃▂▂▁▂▃▂▂▁▂▂▃▂▁▂▂▂▃▂▂▁▂▃▂▂▂▁                                  │
│ ▢ good  ▣ slight  ■ moderate  █ severe  ◫ today, partial  ⟋ no data                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌ TYPICAL DAY · LAST 30 DAYS ─ by hour vs band ┐ ┌ DAILY RANGE · TEMPERATURE ─ min, mean, max ┐
└───────────────────────────────────────────────┘ └────────────────────────────────────────────┘
┌ TIME IN BAND PER DAY ─ temperature, VPD; dotted target 90% ─────────────────────────────────┐
> Data · 30 rows · CSV of this table
```

On phones the tiles go 2-up, and the strip shows 30 days at once (cells at least 8 px). Longer windows scroll inside
the strip's region, which opens at the newest day. The charts stack.

### 5.8 Activity → Alerts (`/activity`)

| | |
|---|---|
| Purpose | What needs someone now |
| Primary task | Read an alert, act on it, acknowledge it |
| Above the fold | The status line ("2 open · 1 acknowledged, still open · 3 resolved in 24 h") and the "Needs action" rows |
| Layout | Three groups. "Needs action" holds unacknowledged alerts by severity, then age. "Acknowledged, still open (N)" and "Resolved in the last 24 h (N)" are collapsed. Then "Recent activity": the 5 newest log entries and "Open the log ›" (API-17) |
| Alert row | `ObjectRow` with a severity rule: the severity word, plain title, zone or line, age, and a repeat count with first and last seen ("x37 since Mon · last 06:02"). Inline Acknowledge and "Details ›". The detail expands in place, addressable as `?alert=`, holding the facts, related line or equipment state, runbook, note field with Acknowledge and Resolve, "Open in Graphs ±2 h", and Stop all water or Clear fault where the alert calls for it |
| Moves | The activity log to its own tab (§5.9) |
| Removed | The page-long two-panel layout; the log on this page |
| New | The three groups; repeat counts with first and last seen; Recent activity |
| Hooks kept | `.alert-facts`, `.alert-detail`, `.runbook`, `details.alert-group > summary`, `ol.alert-list > li` and the button names |

```text
1440                                                                360
ACTIVITY                                                            ACTIVITY
Alerts                                                              Alerts
2 open · 1 acknowledged · 3 resolved in 24 h                        2 open · 1 ack · 3 resolved
[Alerts] [Log]                                                      [Alerts][Log]
┌ NEEDS ACTION · 2 ──────────────────────────────────────────────┐  ▌CRITICAL · 03:02 (4 min)
│▌CRITICAL  Water flowing with every Verdify valve closed · 4 min │   Water flowing with every
│           3 gal in 10 min · a leak, or the hose, sink or hydro  │   Verdify valve closed
│           If no one is using water, close the spigot.           │   3 gal in 10 min
│           [Acknowledge]                              Details >  │   [Acknowledge            ]
│▌WARNING   Fan 2 relay stuck · fan2 · 40 min · x3 since Mon      │   Details >
│           [Acknowledge]                              Details >  │  ▌WARNING · 06:02 (40 min) …
└─────────────────────────────────────────────────────────────────┘  > Acknowledged, still open (1)
> Acknowledged, still open (1)                                      > Resolved in 24 h (3)
> Resolved in the last 24 h (3)                                     Recent activity …
┌ RECENT ACTIVITY ─────────────────────────────── Open the log > ┐
```

### 5.9 Activity → Log (`/activity/log`)

| | |
|---|---|
| Purpose | Who changed what, when and why |
| Primary task | Find a change by actor, kind or target |
| Layout | Facet chips: Who (People `source=dashboard`, Iris, Agents, System) and What (Settings, Water, Alerts, Plan, Equipment), mapped onto the existing `source` and `action` parameters. A Target field and From and To dates, all applied on change, all in the URL. "CSV of these entries" covers the loaded pages. Entries sit under sticky day headers (Today, Yesterday, Mon 3 Nov) |
| Entry | One line: time · actor (with AI for Iris and agents) · object · old → new · manual · "reason" · outcome pill. An Iris plan collapses to "iris AI · SUNRISE plan · 9 changes" and expands to its changes. Every automatic entry names its cause ("Forecast rule · system", "Schedule 'Wall MWF'") |
| Moves | From the lower half of `/activity` to `/activity/log` |
| Removed | Target's separate Apply button; multi-line entries |
| New | Who and What facet chips; sticky day headers; one-line entries; CSV of the loaded entries |
| Hooks kept | `li.event`, `.event-reason`, `.event-by [data-badge=ai]`, the search landmark "Activity log filters" and the region "Activity log" |

```text
1440                                                                         360
ACTIVITY                                                                     ACTIVITY
Activity log                                                                 Activity log
Every write by people, Iris, agents and the system                           [Alerts][Log]
[Alerts] [Log]                                                               Who [All v]  What [All v]
Who [All][People][Iris][Agents][System]  What [All][Settings][Water][Alerts]  Target [L01 or key]
Target [L01 or key]  From [    ] To [    ]                CSV of these entries ── TODAY ──
── TODAY ─────────────────────────────────────────────────────────────────── 06:47 jason
06:47  jason        VPD Mister Engage 1.60 -> 1.55 kPa · manual · "Vandas"     VPD Mister Engage
06:02  iris AI      SUNRISE plan · 9 changes                              >   1.60 -> 1.55 kPa · manual
03:58  system       Forecast rule · fog RH ceiling 88 -> 85%     CONFIRMED     06:02 iris AI · SUNRISE · 9 >
── YESTERDAY ───────────────────────────────────────────────────────────────
```

### 5.10 Control → Settings (`/settings`)

| | |
|---|---|
| Purpose | Every setting: what the device reports, what is changing, what Iris plans next. Change any of them |
| Primary task | Find a setting and change it, or check that a change reached the controller |
| Above the fold | The header (search in the actions), the status line ("112 settings · 2 requested · 0 not confirmed · 5 manual"), the rail and the first group |
| Layout | `l-rail`. The rail holds Show (All, Changed in the last 24 h, Waiting for controller, Not applied, Manual (Iris skips), Set by Iris or an agent, Read-only, each with a count and a one-line definition) and Groups (anchors with counts), then the window switch (24 h or 7 d), "Show retired" (retired rows are hidden by default; a search still finds them), the glossary link and a legend that defines every filter, state and read-only kind (#1333; wording pending Jason's confirmation). "Changed in the last …" means any write in force in the window, an Iris plan's included; if API-09 does not serve the write time, that filter is left out. Each row shows its one-line summary under the label, and the drawer what the setting changes and what raising and lowering it does (API-09 `help`, #1332). The catalog stays a `<table>` with one `tbody` per group and a sticky group head |
| Row | Two lines. **Line 1:** label and key (mono, muted), the device value at `--fs-lg` with its unit, a `BandBar`-style bounds bar (min–max track, value dot, and the next planned value as a hollow mark), the one pill, and Edit. **Line 2** (`.meta`): who wrote the value in force ("jason · manual · 'Vandas drying' · 06:47", or "Iris changed this at 07:02 · AI"), the next plan ("next 1.70 at 13:00 · AI" or "superseded until Iris's next plan") and, while pending, the request ("→ 1.55 kPa REQUESTED · 40 s · Sent 06:47; no matching readback yet") |
| Drawer | The same `EditDrawer` and fields. **Top:** the device value, bounds bar, request line and plan line. **Then:** the field, band preview, Manual, Reason, then Release to auto, Cancel and Save. **Then:** "Change history" (chart and entries "1.60 → 1.55 · jason · manual · 'Vandas drying' · Confirmed 06:48 [Revert to this]") and "Activity for this setting ›". The drawer is a component in `components/control/` so Plan and Equipment open it in place |
| Phone | Search, then the Show select and the "Jump to group" select, then stacked rows: label, device value and unit, one pill, one meta line. The Edit link covers the row |
| Moves | The filter chips into the rail; the "Next (plan)" and "Allowed · step" columns into the meta line and the bounds bar; the drawer into `components/control/` |
| Removed | Steady-state CONFIRMED pills; the five-column table look; the "Catalog" card around the table |
| New | The rail with counts; Recently changed; bounds bars with the planned mark; two-line rows; phone selects |
| Hooks kept | `tr.setting-row[data-param]`, `tbody[data-group]`, `.setting-value`, `.setting-device`, `.setting-writer`, `.setting-request`, `.setting-next`, `.setting-label`, `.setting-edit`, `.state-reason`, `.drawer-history`, `.drawer-revert`, `.band-preview`, `.field-error`, "Edit <label>", "Changed by an agent N", "Manual: Iris skips this" and no "Send again" |

```text
1440
CONTROL
Settings                                                                   [Search settings: name or key      ]
112 settings · 2 requested · 0 not confirmed · 5 manual
[Settings] [Plan] [Equipment]
┌ SHOW ────────────────────┐ ┌ PLANNER POLICY · 22 ─────────────────────────────────────────────────────────────┐
│ All                  112 │ │ VPD Mister Engage   mister_engage_kpa                                              │
│ Recently changed       3 │ │   1.60 kPa    [0.50 ──●────○─────── 2.50]   REQUESTED                    [Edit]    │
│ Requested              2 │ │   jason · manual · "Vandas drying" · 06:47 · -> 1.55 · 40 s · next 1.70 at 13:00 AI │
│ Not confirmed          0 │ ├────────────────────────────────────────────────────────────────────────────────────┤
│ Manual                 5 │ │ Fog RH Ceiling   fog_rh_ceiling_pct                                                │
│ Changed by an agent    3 │ │   88%         [50 ───────────●── 98]        MANUAL                       [Edit]    │
│ Read-only             14 │ │   steward · agent · 2 days ago                                                     │
│ GROUPS                   │ └────────────────────────────────────────────────────────────────────────────────────┘
│ Band in force          8 │ ┌ BAND IN FORCE · 8 · derived from the crop band anchors ────────────────────────────┐
│ Planner policy        22 │
│ Site water caps        3 │
└──────────────────────────┘
```

```text
360
CONTROL
Settings
112 settings · 2 requested
[Settings][Plan][Equipment]
[Search settings          ]
Show [All v]   Group [Jump to v]
┌ VPD Mister Engage ──────────────┐
│ 1.60 kPa          REQUESTED     │
│ -> 1.55 · 40 s · jason · manual │
└─────────────────────────────────┘
```

### 5.11 Control → Plan (`/plan`)

| | |
|---|---|
| Purpose | What Iris will tell the controller, whether Iris is healthy, and how to override it |
| Primary task | Read the plan, then take a lane's setting manual in the drawer, in place |
| Above the fold | The status line ("SUNRISE plan · written 07:02 · valid to 17:30, 6 h 12 min left · AI"), Plan in force and Planner health |
| Layout | Plan in force and Planner health (`l-cols-2`), then Planned settings (full width). The header actions hold "Find a setting" (search to `/settings?q=`) |
| Plan in force | The trigger, plan id, AI mark, a validity bar (written → valid until, now marked), the hypothesis (AI) and a disclosure for conditions, stress windows and rationale. With no plan in force: "Band and defaults apply since 05:40" |
| Planner health | The last 36 h of triggers as a row of status marks (ok, late, failed, missed in words on hover and in the list), the next trigger, and "Last 14 days: 41 of 42 required plans" with a `StatusStrip` from `daily.iris_required_written` and `daily.iris_required_missed`. The ledger list sits in a disclosure |
| Planned settings | `PlanLanes`, unchanged in drawing, with 24 h and 72 h. A lane label opens `/plan?edit=<param>`. "Open" goes away |
| Moves | Taking a planned setting manual happens here, in the drawer, instead of on Settings |
| Removed | The "Open" link per lane; the 36-hour list as the main health view (it sits in a disclosure) |
| New | The validity bar with time left; trigger marks; the 14-day reliability strip; "Find a setting" |
| Hooks kept | The regions "Plan in force", "Planner health" and "Planned settings" (stacked at 390 px), `.lanes-plan`, `.lanes-canvas` with its 6rem label offset, `.plan-facts`, `.plan-hypothesis`, `.plan-none`, `.plan-manual`, `.plan-status`, `.plan-device`, `.trigger-*` and `[data-trigger][data-outcome]` |

```text
1440                                                                       360
CONTROL                                                [Find a setting]    CONTROL
Plan                                                                       Plan
SUNRISE plan · written 07:02 · valid to 17:30 (6 h 12 min left) · AI      SUNRISE · to 17:30 · AI
[Settings] [Plan] [Equipment]                                              [Settings][Plan][Equipment]
┌ PLAN IN FORCE ─────────────────────────┐ ┌ PLANNER HEALTH ────────────┐  ┌ PLAN IN FORCE ─────────┐
│ SUNRISE · iris-…-0702 · AI              │ │ 36 h  ok ok ok late ok ok  │  │ [▓▓▓▓▓▓░░░░] to 17:30  │
│ [▓▓▓▓▓▓▓▓▓▓░░░░░░░░] 07:02 -> 17:30     │ │ next SOLAR_MAX 12:14       │  │ "Hold VPD near …" (AI) │
│ "Hold VPD near 0.9 kPa through midday"  │ │ 14 days: 41 of 42 ▢▢▢■▢▢▢  │  └────────────────────────┘
│ > Conditions, stress windows, rationale │ │ > Trigger ledger           │  ┌ PLANNER HEALTH ────────┐
└─────────────────────────────────────────┘ └────────────────────────────┘  ┌ PLANNED SETTINGS ──────┐
┌ PLANNED SETTINGS · NEXT 24 H ──────────────────────────── [24 h][72 h] ┐
│ (PlanLanes; each lane label opens the setting drawer here)            │
```

### 5.12 Control → Equipment (`/equipment`)

| | |
|---|---|
| Purpose | What each actuator and the controller is doing, why, and how much it has run |
| Primary task | Check an actuator's state and why; work the lights, a burst or a restart |
| Above the fold | The status line ("Controller up 3 d 4 h · firmware 2026.11.3 · Wi-Fi −61 dBm · updated 12 s ago"), the board and the Controller card |
| Layout | Two columns from 861 px. **Left:** Climate (fan 1, fan 2, vent, heat 1, heat 2), Lights (main and grow, with the Lutron readback and the controller's intent, plus the light controls) and Flags (occupancy, leak, water flowing). **Right:** Water relays (L01–L11) and the Controller card. Then, full width: Bursts (the shared quick-action component), Why (mode, fog block, moisture block reasons), Last 24 h lanes (24 h, 72 h) and "Daily runtime · 14 days" (one `Sparkline` bar row per heat 1, heat 2, fan 1, fan 2, vent and fog, with the daily average, from API-05). More actions holds Restart controller |
| Board row | Name, then the state word with a dot (ON, OFF or UNKNOWN; only UNKNOWN is hatched or muted), then since, runtime today and cycles |
| Controller card | API-02 `controller`: firmware version, uptime and last reset reason, free heap with a `Meter` against the 32 kB floor (fault below it), largest block, RSSI in dBm, and last seen |
| Moves | The burst controls are shared with Now's quick actions (one component); the setting drawer opens here for the light switches |
| Removed | The unbalanced `.grid-3` (5, 11 and 2 rows); the long lede |
| New | The Controller card (API-02 `controller`, served but never shown until now); daily runtime for 14 days; the controller status line |
| Hooks kept | `.equipment-row[data-equipment]`, `.equipment-state`, `.equipment-facts`, `.equipment-board`, `.why-row[data-reason]`, `.why-words`, `.burst-panel [data-chip=burst]`, `.burst-more summary`, `.light-actions`, `[data-differ]`, `[data-auto]`, `[data-arrives]` and `[data-bursts=none]`; the h1 becomes "Equipment" |

```text
1440                                                                      360
CONTROL                                                [Find a setting]   CONTROL
Equipment                                                                 Equipment
Controller up 3 d 4 h · firmware 2026.11.3 · −61 dBm · 12 s ago           Up 3 d 4 h · −61 dBm · 12 s ago
[Settings] [Plan] [Equipment]                                             [Settings][Plan][Equipment]
┌ CLIMATE ───────────────────────────────┐ ┌ WATER RELAYS ─────────────┐ ┌ CLIMATE ─────────────────┐
│ Fan 1     ● ON  since 06:58 · 2 h 10 · 4│ │ L01 Wall drip  OFF · 12 · 1│ │ Fan 1  ● ON since 06:58  │
│ Vent      ● OPEN since 06:58 · 3 h · 2  │ │ L04 Center ● ON since 09:58│ │   2 h 10 min · 4 cycles  │
│ Heat 1    OFF · 3 h 05 min · 9          │ │ ...                        │ │ ...                      │
├ LIGHTS ────────────────────────────────┤ └────────────────────────────┘ ┌ CONTROLLER ──────────────┐
│ Main  Lutron ON · controller ON  [Turn off] [Back to automatic]     │   ┌ WATER RELAYS ────────────┐
├ FLAGS ─────────────────────────────────┤ ┌ CONTROLLER ───────────────┐ ┌ BURSTS ─ WHY ─ LANES ────┐
│ Occupancy: someone inside (4 min ago)  │ │ heap [▓▓▓▓░|] 41 kB/32 kB  │
└─────────────────────────────────────────┘ └────────────────────────────┘
 BURSTS · WHY · LAST 24 H (lanes) · DAILY RUNTIME 14 DAYS · More actions v
```

### 5.13 Sign-in, errors and 404

These are unchanged: the plain header and one card, with no nav, role pill or banners (`e2e/layout.spec.ts` pins
this). `/more` now lands on "Page not found" with "Return to Now".

### 5.14 Drawers, dialogs, popovers and toasts

- **The edit drawer** keeps its behaviour and sizes: 28rem on the right from 861 px, a full-screen sheet with pinned
  actions at 860 px and below. From 1200 px the page narrows beside it while the schedule editor is open, as today.
- **Popover** (new, `components/popover.tsx`): anchored from 861 px; at 860 px and below a bottom sheet in a native
  `<dialog>`. Focus moves in and returns to the opener. Escape closes it.
- **The confirm dialog** stays for water runs only.
- **Toasts** are unchanged.

## 6. The schedule interface

### 6.1 Chosen layout

Schedules has three views of the occurrences and one list of the rules, each answering one question.

- **Day timeline** (the default on desktop): when does each line run today, for how long, how close together, and
  what did VPD misting do?
- **Week:** the strip on top of every view, plus the matrix view on desktop. Which days run, what happened on past
  days, and how full is each day against its caps?
- **Agenda** (the default on phones; a toggle on desktop, where it is also the timeline's equivalent for keyboard and
  screen-reader users): what runs next, in order?
- **Rules list** (always below the view): which schedules exist, how they are set, who changed them last, and edit.

Desktop toggles between Day, Week and Agenda; phones between Agenda and Day.

**The next run** reads the same way everywhere:

- in the Schedules status line ("next: L01 Wall drip 16:00");
- as the first dashed block after the now rule on the timeline;
- as the agenda row marked NEXT;
- as each line's "next 16:00 · 10 min" on Water → Today;
- in "Running and next" on Now.

Why this layout:

- Consumer irrigation apps pair a rule list with a month calendar (Rachio, B-hyve). They show which days, but not
  durations, spacing or overlaps.
- Commercial and thermostat products add a time-positioned day view, which shows those honestly: Rain Bird IQ4's
  projected-flow report and Dryrun, ecobee's zoomable day.
- Verdify's real constraints are time constraints:
  - the 30-minute spacing per line;
  - one drip manifold (L01, L02, L05, L06) and one mister manifold (L04, L07–L10);
  - the daily caps;
  - sunrise-relative starts;
  - VPD misting that alternates with scheduled pulses.
- Rachio users asked for past and future in one calendar, with each past day showing what ran or was skipped. The
  week strip and matrix do that.
- Separating the rules from their occurrences follows every irrigation product surveyed and fixes the finding that a
  schedule existed on screen only as its occurrences.

Rows on the timeline group by **manifold**, because one manifold runs one line at a time, so its rows must sit
together. Today groups by **zone**, because plants live in zones. Each row's label names its zone ("L04 Center
mister").

### 6.2 Day timeline

```text
1440 (page-wide)
WATER
Schedules                                                                 [+ New schedule]  [■ Stop all water]
Program rev 42 on controller · confirmed 2 min ago · next: L01 Wall drip 16:00
[Today] [Schedules] [History]
 < week >  Mon 3     Tue 4 today   Wed 5     Thu 6     Fri 7     Sat 8     Sun 9        [Day] [Week] [Agenda]
           5 ran ok  2 ok, 3 next  6 next    3 next    6 next    2 next    5 next                 week strip
           ▢         ◫ 22/60 min   ▢         ▢         ▢         ▢         ▢
┌ TUESDAY 4 NOVEMBER · TODAY ─────────────── sunrise 06:31 · solar noon 11:42 · sunset 16:52 ─── [<] Today [>] ┐
│                      00    03    06    09    12    15    18    21    24       today / cap                    │
│ DRIP MANIFOLD                                                                                               │
│ L01 Wall drip        ░░░░░░░░░░░█▌ 07:31 ok   [16:00 10 min]  ░░░░░░░░       [▓▓▓░|] 22 / 60 min       [+]   │
│ L02 Wall feed        ░░░░░░░░░░░              (Sundays)        ░░░░░░░░        0 min                      [+]   │
│ L05 Center drip      Reserved · disabled                                                                    │
│ MISTER MANIFOLD                                                                                             │
│ L04 Center mister    ░░░▏▏ ▏▏▏ ▏ VPD ▏▏ ▏ [13:00 4x45 s]   ░░░░░░░░       [▓▓▓▓░|] 46 / 120 min      [+]   │
│ L07 South mister     ░░░░░░░░ ⟋⟋⟋⟋ paused until Sat 09:00 · jason ⟋⟋⟋⟋⟋⟋⟋⟋⟋    0 / 45 min                [+]   │
│ L09 West mister      ░░░░░░░░░░░             [15:00 6x30 s skipped: RH 92%] ░░  3 / 45 min                 [+]   │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 Key (popover): solid = ran · dashed = planned · hatched = will not run or skipped · thin ticks = VPD misting · ░ night
┌ SCHEDULES BY LINE · 14 of 24 [▓▓▓▓▓▓░░░░] ──────────────────────────────────────────────────────────────────┐
│ L01 Wall drip                                                                           [+ Schedule]        │
│   Wall MWF        Mon Wed Fri · sunrise + 60 (07:31 today) · 12 min                       [ON]  [Edit] [⋯] │
│                   MANUAL · changed by iris (AI) 2 days ago · "forecast 92 °F" · CONFIRMED                    │
│   Evening top-up  Daily · 16:00 · 10 min · Iris may adjust                                [ON]  [Edit] [⋯] │
│ L04 Center mister                                                                       [+ Schedule]        │
│   Midday mist     Daily · 13:00 · 4 x 45 s, 60 s gap                                      [ON]  [Edit] [⋯] │
│   Fallback (3)    only if VPD sensing is degraded · solar noon −120, 0, +120 · 4 x 45 s        [Edit]       │
│   Dawn drench     Mon Thu · sunrise + 15 · 8 x 60 s · disabled                            [OFF] [Edit] [⋯] │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Window.** One local day, 00:00–24:00 (25 h on the fall-back day, from `lib/time-scale.ts`), chosen by `day=`.
  Today is the default. ‹ › step one day, across the 7 days before today and the 6 after it, which is what
  API-IRR-02 serves; the buttons stop at those ends.
- **Occurrences** come from API-IRR-02: the default read for today onward and `past_days=7` for the week before.
  - A block starts at `planned_start` and is as long as its schedule's `on_s`, `count` and `gap_s`, at least 6 px.
  - A mist window draws its pulses as ticks inside the block when they fit, else one block labelled "4 × 45 s".
  - A fertilizer run draws its feed on its own row, and its prewet and flush on the clean partner's row (L01 for
    L02) in `--eq-fog`, right before and after the feed.
  - Colour follows the cause: schedule in `--eq-water`, feed in `--eq-fert`, prewet and flush in `--eq-fog`,
    fallback in `--eq-heat1`, as `charts/causes.ts` maps them.
- **Status** sits on the block as a fill and a word:
  - done: solid, with the outcome word (ok, short, stopped, preempted or cap) as its label;
  - planned: a dashed outline;
  - running: solid to now, dashed after;
  - skipped or blocked: hatched, with the API's `reason` ("skipped · line paused by jason", "RH 92%");
  - missed: a `--fault` outline and "missed · no valid clock";
  - unknown outcome: hatched `--muted` and "unknown" (a gap, never missed).
- **Context:**
  - Night shading comes from `agenda.solar`.
  - The now rule shows on today.
  - Past VPD misting on mister rows shows as thin `--eq-mister` ticks along the row's lower third, from API-05
    intervals for that day (days up to 31 back).
  - A pause shows as a hatched band across its span, with "paused until Sat 09:00 · jason" at its start.
  - A disabled line's row is muted and reads "Disabled: its 3 runs this week will not run".
  - A mister with Schedule off reads "Schedule off for this zone".
- **Caps:** each row ends with a `Meter`: minutes done today (`lines[].today.minutes`) plus planned minutes against
  `daily_cap_min`. A planned block that would pass the cap gets an amber outline and "ends at the daily cap" (the
  API's occurrence note).
- **Interaction.**
  - A block is a button. It opens the popover (a sheet on phones) shown below.
  - An empty part of a row is clickable for operators. It opens the editor prefilled with that line, the selected
    day's weekday and the clicked time rounded to 15 minutes. The keyboard path is the row's [+] button, named "New
    schedule on L01 Wall drip".

```text
┌ L01 Wall drip · Wall MWF ─────────────────────────────┐
│ Tue 4 Nov · 07:31 (sunrise + 60) · 12 min             │
│ Done · ok · 12.0 min · 9 gal                          │
│ Rule: Mon Wed Fri · sunrise + 60 · 12 min · MANUAL    │
│ Next: Wed 07:32 · Fri 07:34 · Mon 07:36               │
│ [Edit schedule]  [Pause L01 until after this run]     │  operators; Pause only on a planned one
│ Runs of L01 >   Graphs around this run >              │
└───────────────────────────────────────────────────────┘
```

### 6.3 Week strip and week matrix

- **The week strip** sits on top of every Schedules view. It shows Monday to Sunday of the selected week; ‹ › move
  by a week, and past and future days sit side by side. Each day cell gives the date and a summary:
  - past days: "5 ran ok" or "1 missed", with a `StatusStrip` level (0 when every outcome was ok, 1 short or cap,
    3 missed);
  - today: "2 ok, 3 next", with the partial outline;
  - future days: "6 next".

  The total against the sum of the caps shows on hover and in the cell's accessible name. Selecting a day sets
  `day=` for the timeline and the agenda.
- **The week matrix** (`view=week`, desktop) has rows for the lines (by manifold, as in the timeline) and columns for
  the seven days. Each cell lists one mark per occurrence (solid done, outline planned, hatched skipped, fault
  outline missed) with the minutes total against the cap ("22 / 60"). An over-cap total reads amber with "cap". A
  cell opens that day's timeline. The one-sentence key (`components/schedule-week.tsx:202`) becomes the Key popover.
  The matrix sits in an `.l-scroll-x` region below 1100 px.

### 6.4 Rules list

- **Grouping.** It is grouped by line, in L01–L10 order. Disabled schedules stay under their line, muted; there is no
  footnote. The section head shows "14 of 24 schedules" with a `Meter` against `max_schedules`.
- **A rule row reads:**
  - the label;
  - the days (with "Daily" and "Weekdays" for the obvious sets);
  - the start in words with today's resolved time;
  - the amount ("12 min", "4 × 45 s, 60 s gap", "feed 5 min · prewet 2 min · flush 4 min on L01");
  - MANUAL as a pill, or "Iris may adjust";
  - FERT;
  - the degraded-only note;
  - the sync pill (REQUESTED or NOT CONFIRMED, and CONFIRMED within 10 min);
  - "changed by `updated_by` `updated_at`" with its reason.
- **Controls:** the enabled switch, Edit, and ⋯. The menu holds Duplicate, which opens the editor prefilled as a
  new schedule, and Delete, which behaves as the editor's Delete does today.
- **Per line:** "+ Schedule" prefills the line.

### 6.5 Create and edit

- **Entry points:** "+ New schedule" (page header), "+ Schedule" (per line), a click on an empty slot, and Edit (rule
  row or popover). They open the existing `ScheduleEditor` drawer (`?schedule=new|<slot>`). There is no drag (D-UX-12).
- **Field order:**
  - Line, Label;
  - Days (weekday chips with "Daily" and "Weekdays" shortcuts);
  - Start (`TimeField`, with today's resolved clock time beside it);
  - Amount fields for the line's kind, with bounds from `agenda.bounds`;
  - Degraded-only (misters);
  - the one-time Check, when the line needs it;
  - Manual (ticked), Reason;
  - Save.
- **The live preview runs before Save.** The draft draws as dashed PLANNED blocks on the timeline of the next day it
  runs. The editor's preview (`lib/schedule-preview.ts`) lists the day totals against the cap, "N of 24 schedules"
  and the refusals the API would give, in words, as you type:
  - "Starts 20 min after Wall MWF on L01: schedules on one line need 30 min" (`too_close`);
  - "Wed would reach 72 of 60 min: runs past the cap end at the cap" (cap);
  - "This would be the 25th schedule; the controller holds 24" (`too_many_schedules`);
  - "Shares the drip manifold with L02's 07:30 feed: it waits until 07:43" (informational).

  Save stays enabled. The server decides, and a 422 keeps the drawer open with the message beside its field.

### 6.6 Skips, pauses, caps and conflicts

| Case | Timeline and matrix | Agenda | Rules list |
|---|---|---|---|
| Skipped (rain pause, RH, dew, occupancy, L03 open) | Hatched block with the reason word | Hatched row, "skipped · RH 92%" | — |
| Paused line | Hatched band over the pause and "paused until … · who" | "Paused until Sat 09:00 by jason" above that line's rows | The row notes the pause |
| Disabled line or schedule | Muted row or hatched block, "will not run" | Hatched, "line disabled" | A muted row, switch OFF |
| Cap | The row meter; an amber outline and "ends at the cap" on the block that crosses it | "ends at the daily cap" | — |
| Missed | A `--fault` outline and "missed · reason" | A fault-coloured word | — |
| Draft conflict | Fault-outlined draft block plus words in the editor preview | — | — |
| Unknown outcome | `--muted` hatch, "unknown" | "outcome unknown (ingestor was down)" | — |

### 6.7 Phones

```text
360
WATER
Schedules
Rev 42 confirmed · next L01 Wall drip 16:00
[Today][Schedules][History][■ Stop all]   sticky
[Agenda][Day]                [+ New]
 < Mon3 Tue4 Wed5 Thu6 Fri7 Sat8 Sun9 >   week strip, 7 cells of 44 px
── TODAY · TUE 4 NOV ───────────────
07:31  L01 Wall drip · 12 min
       Wall MWF · ok · 9 gal
13:00  L04 Center mister · 4 x 45 s     NEXT
       Midday mist · Iris may adjust
15:00  L09 West mister · 6 x 30 s
       skipped · RH 92% (hatched)
16:00  L01 Wall drip · 10 min
── WED 5 NOV ───────────────────────
07:32  L01 Wall drip · 12 min
── SCHEDULES BY LINE ───────────────
> L01 Wall drip (2)
> L04 Center mister (5)
```

- The agenda starts at local midnight of the selected day and runs 3 days. "Earlier today" collapses once it is past
  noon. The next planned occurrence carries NEXT.
- A row opens the popover as a sheet.
- The day timeline (`view=day`) fits 24 h across the width, with 6-hour ticks, labels above each track (a 20 px label
  line plus a 24 px track) and the cap meter under the label. It never scrolls sideways.
- The week matrix is not offered below 861 px.
- Rules sit in per-line disclosures under the agenda.
- The editor is the full-screen sheet with pinned actions.

## 7. Graphs and statistics

### 7.1 Chart framework changes (`dashboard/app/charts/`)

**Composition: `ChartStack`.** It replaces `LinkedPanels` and holds one card of panels on one x scale.

- Only the bottom panel draws the time axis: 36 px at 1440, 28 px at 860 and below.
- Panel heights are 220/200/160 px for temperature, VPD and RH (160/150/120 at 860 and below). Each added panel is
  160 px (120 px on phones).
- Every panel has an `id` (`temperature`, `vpd`, `rh`, or a metric key) for anchors.
- A 1px `--line` divider separates panels. The chrome between panels is the header row alone.

**Panel header rows** carry, per panel:

- the name and unit;
- the window statistics: in band % of points (only when a band is drawn), low with its time, mean, high with its
  time, and gaps;
- series swatches that toggle (`aria-pressed`) and replace the separate legend.

While the cursor is on any panel, every header row shows the values at the cursor instead: the value, the band and
"above", "below" or "in". One time chip rides the cursor line at the top of the stack. These replace the per-chart
tooltips (`charts/tooltip.ts:22-33`). Lanes keep their own tooltip, for the hovered row only.

**Range.** `RangeToolbar` replaces `RangeChips`:

- presets from the page (Graphs: Today, 24 h, 72 h, 7 d, 30 d, 90 d);
- ‹ › to step by the window's length;
- Zoom out (×2, centred);
- Custom… (From and To days).

Drag-to-zoom on fine pointers and "Reset zoom" stay. The future pad matches the window's length, at most 72 h, so the
default 72 h opens with 72 h of forecast ahead; Today ends at the next midnight (decisions 2026-11-05, replacing D-UX-13;
in `charts/range-toolbar.tsx` and `lib/graph-url.ts`).

**Band.** The ribbon (`--c-band-wash` with `--c-band-edge` edges) stays. New: **excursion shading**. Wherever the
measured series leaves the band, the area between the line and the nearest band edge fills with `--tint-warn`, and
the line keeps its colour. Drawn in `drawClear` after the band, so the line stays on top.

**Min–max envelope.** For buckets of 30 min and more (7 d and longer), each bucket's API-04 `min`–`max` draws as a
2 px vertical bar in `--ink-muted` under the mean line. It is on by default at those buckets and toggles off with
`env=0`.

**Event annotations.** A 20 px rail across the top of the stack, which `events=` toggles by source:

| Event | Mark | Token |
|---|---|---|
| Setting write by a person | Diamond | `--forest` |
| Iris | Diamond, "AI" in its label | `--ink` |
| Another agent | Diamond | `--eq-fan2` |
| System (forecast rule) | Hollow diamond | `--muted` |
| Alert raised | Triangle | By severity: `--fault`, `--warn-text`, `--muted` |
| Alert resolved | Hollow triangle | By severity |
| Water run | 3 px bar | Its cause token |

- Writes come from API-17. Alerts come from API-14's open and recently resolved alerts, the window API-14 serves
  today. Runs come from API-IRR-11 for windows up to 31 days; for longer windows the runs layer is off with a note.
- Marks closer than 8 px cluster into one mark with a count.
- Hovering or focusing a mark draws a hairline through every panel and opens a list of its events, each linking to
  its Activity entry.
- Keyboard and screen readers get the same list as an "Events in this window (N)" disclosure under the stack.

**Compare.** `cmp=day|week` draws the primary series of each panel for the previous day or the same window a week
earlier, time-shifted, as a 1.25 px dashed `--c-bound` line labelled "yesterday" or "last week". The header row adds
"mean 66.0 °F · ▲ 0.8 vs yesterday". It is off by default and takes one extra API-04 read per panel.

**Small multiples: `ChartGrid`.** One sync key and a uniform y scale per row. Only the first column draws a y axis
and only the bottom row an x axis. Panel heights come from props (120 px on Zones).

**Lanes.** Unchanged in drawing. Inside a stack they sit in a `<details>`: open from 861 px, closed below. They gain
"CSV of these lanes" from their intervals.

**Bars.** `MinutesByZone` gains `measure: "minutes" | "gal"`, accepts any window up to 400 days, and offers "CSV of
this chart". `DailyWaterUse` offers its CSV.

**Phones.** No floating tooltip. A tap or drag on a plot moves the cursor, and the header rows show its values (the
pinned readout). There is no zoom on touch (as today).

**Colour roles.** No new tokens:

| Role | Token |
|---|---|
| Series | `--c-temp`, `--c-vpd`, `--c-rh`, `--c-outdoor` |
| Band | `--c-band-wash`, `--c-band-edge` |
| Excursion | `--tint-warn` |
| Envelope | `--ink-muted` |
| Compare | `--c-bound`, dashed |
| Setting steps | `--c-setpoint`, `--c-requested`, planned |
| Equipment and causes | `--eq-*` |
| Events | As in the table above |
| Grid | `--c-grid` |

Labels on plots keep the 4.5:1 rule (design system §5.5).

**Units and time.** The unit sits in the panel header and on readouts, never on every tick. Axes keep their local
time with the offset at 1440 and time only at 860 and below, as built.

### 7.2 Statistics

- **Daily summaries.** API-04 daily buckets give every metric's min, mean and max per day for up to 400 days.
  Statistics charts them for climate. With D-UX-04, Climate's metric picker lists the `daily.*` keys too; a panel of
  daily keys draws one point per day.
- **Time in band.** Whole-day temperature and VPD from `daily.temp_in_band_pct` and `daily.vpd_in_band_pct` for every
  day. The day/night split covers the last 3 days only, in the table (Q-12). Statistics shows the KPIs, the strip
  rows and the per-day bars; Now shows today against yesterday.
- **Typical day.** For the last 14 or 30 days (`typical=`), from hourly API-04 buckets: the median by hour of day as
  a 2 px series-colour line, the 25th and 75th percentiles as 1px dashed lines in the same colour, and the band
  ribbon of the window's last day behind them. One chart each for temperature and VPD, 240 px. This is the AGP
  report's modal day, and it shows recurring night drift at a glance.
- **Daily range.** For each day, a min–max bar in `--ink-muted` with a mean tick in `--c-temp`, which shows the day
  and night swing (DIF). 200 px.
- **Water by zone** belongs to Water → History: minutes or gallons by zone, line or cause, with KPI deltas.
  Statistics carries only the gallons-per-day row and the Water tile, which links there.
- **Comparisons.**
  - KPI deltas against the previous equal window (Statistics and Water history).
  - Today against yesterday for time in band (Now).
  - Yesterday or last week as an overlay (Climate).
  - Deltas are words and arrows in ink, never green or red.
- **Equipment duty.** Daily runtime for the last 14 days from API-05, on Control → Equipment.

### 7.3 Charts by screen

| Screen | Charts and visuals | Engine |
|---|---|---|
| Now | Band bars (hero, zones), zone sparklines, meters, the "Running and next" track | SVG and HTML (no uPlot on Now) |
| Water → Today | Zone meters, per-line today strips, site meters | SVG and HTML |
| Water → Schedules | Day timeline, week strip, week matrix, cap meters | HTML (`TimeTrack`, `StatusStrip`) |
| Water → History | KPI tiles with sparklines; minutes or gallons by group; daily water use | SVG plus uPlot bars |
| Graphs → Climate | `ChartStack` with band, excursions, envelope, steps, forecast, compare, event rail and lanes | uPlot |
| Graphs → Zones | `ChartGrid` small multiples; a focused `ChartStack` | uPlot |
| Graphs → Statistics | KPI tiles, `StatusStrip` rows, gallons bars, typical day, daily range, time-in-band bars | SVG, HTML, uPlot |
| Activity | None (the log stays text) | — |
| Control → Settings | Row bounds bars; the drawer's change history chart and band preview | SVG plus uPlot (drawer) |
| Control → Plan | Validity bar, trigger marks, reliability strip, `PlanLanes` | HTML plus uPlot |
| Control → Equipment | Heap meter, board, lanes, daily runtime bars | SVG plus uPlot |

## 8. Accessibility and responsive rules that must hold

- **WCAG 2.2 AA everywhere.** axe (`wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`) reports zero violations at 320, 768
  and 1440 px on every route in `e2e/routes.ts`, on both setting drawers, the schedule editor, an open popover and
  every SCR-09 card.
- **Contrast.** Text is at least 4.5:1 (ink 16.19, muted 5.41, forest 8.20, fault 6.54, `--warn-text` on
  `--tint-warn` 7.81). Graphics that carry meaning are at least 3:1 against what they sit on:
  - band edges (3.08), meters and series (all at least 3.19);
  - the hero band bar (5.3 on ink) and the value dot with its ink ring;
  - focus rings (forest 8.20, mint on ink).

  Tints (`--tint-ok`, `--tint-warn`, `--c-band-wash`) never carry meaning alone. Each sits beside words, an outline
  or an equivalent table.
- **Colour is never alone.** Severity has its word, outcomes have words and fills (solid, dashed, hatched), unknown is
  hatched, and pills carry text.
- **Targets.** Every button, link, chip, tab, menu item, switch and summary is at least 44 px at 860 px and below and
  at least 24 px elsewhere (SC 2.5.8), with 44 px kept wherever the design system already sets it. Checkboxes are 24
  px. Timeline blocks narrower than 24 px get an invisible 24 px hit area that never overlaps a neighbour's; where it
  would, the block is reachable from the agenda and the row list instead.
- **Keyboard.**
  - Every action is reachable in a logical order: header, banners, page header, tabs, content.
  - The focus ring is never removed.
  - Drawers, dialogs and sheets trap focus and return it to the opener.
  - Popovers and menus close on Escape.
  - Timeline blocks, strip cells and event marks are buttons or links.
  - Charts keep their data tables, which are the keyboard equivalent; a keyboard chart cursor stays Later.
- **Screen readers.**
  - Visuals are `aria-hidden` beside equivalent text, or `role="img"` with their numbers.
  - Charts keep `role="img"` with a label naming what is plotted and that a table follows.
  - Tabs are a labelled `nav` with `aria-current="page"`. Filter chips use `aria-current="true"`; toggles use
    `aria-pressed`.
  - Live regions are toasts only. Ticking ages stay silent.
- **Reduced motion.** There is no animation anywhere. Hover colour transitions of 120 ms or less are allowed, and
  under `prefers-reduced-motion: reduce` there is none (`e2e/layout.spec.ts:44-64`).
- **No horizontal page scroll** at 360 × 740 and 390 × 844 on every route, drawer and card page, on WebKit too. Wide
  content scrolls inside its own labelled `.l-scroll-x` region.
- **Above the fold, measured against the bottom bar:**
  - On Now at 360 × 740: the answer, `.hero-temp` and "Updated N s ago".
  - On Now at 390 × 844: Stop all water.
  - On Water at 390 × 844: "■ Stop all" stays reachable while scrolling (sticky).
- **Zoom and reflow.** At 400% zoom (320 CSS px) the pages reflow to one column with no loss (SC 1.4.10). Text
  spacing overrides do not clip (SC 1.4.12).
- **Honesty under failure.**
  - With the API down: no green, no verdicts, and no "All OK", "No open alerts", "CONFIRMED" or "in band"
    (`e2e/states.spec.ts`).
  - Static labels that contain those words, such as "In band today", render only with data.

## 9. Constraints from the code that every package honours

1. **e2e hooks.**
   - Keep every `data-*` hook: `data-badge`, `data-banner`, `data-chip`, `data-tile`, `data-line`, `data-day`,
     `data-equipment`, `data-param`, `data-group`, `data-state`, `data-program`, `data-zone`, `data-coming`,
     `data-water`, `data-trigger`, `data-outcome`, `data-reason`, `data-write-control`, `data-limit`, `data-arrives`
     and `data-bursts`.
   - Keep the hooks listed per screen in §5.
   - A changed class, h1 or accessible name updates its spec in the same PR. Specs move from class selectors to roles,
     names and `data-*` hooks.
   - The navigation names "Sections" and "Sections, phone" stay.
2. **Role gating.**
   - Write controls render only through `OperatorOnly` or `Button write` (`lib/session.tsx:28-30`,
     `components/button.tsx:22-31`). A link-styled write carries `data-write-control`.
   - A viewer-visible control must not have a name starting with a `WRITE_CONTROLS` word (`e2e/support.ts:184-191`).
     "Runs of L01" is fine; "Run history", "Edit view" or "Stop chart" would fail.
   - `WRITE_ROUTES` stays `/`, the three Water views, `/settings`, `/activity` and `/equipment`. `/activity/log` and
     the Graphs views write nothing; `/plan` writes only through a drawer opened from a lane.
3. **Writes.**
   - `lib/writes.ts`, `lib/queries.ts` and the requested → confirmed components keep their behaviour: one
     `Idempotency-Key` per write, 5 s settling polls, no optimistic device state and no "Send again".
   - With writes off, controls are disabled except Stop and Stop all water.
   - Manual defaults stay ticked.
4. **Honesty.**
   - New visuals go neutral when `useApiDown()` is true.
   - Unknown is never drawn as off or 0. Gaps stay gaps.
   - Words that assert health render only with fresh data.
5. **CSP** (`security-headers.mjs:11`).
   - No `style` attributes and no `style={` in source. Positions use SVG attributes or CSSOM custom properties.
   - No `data:` URIs: icons are inline `<svg>` elements.
   - Fonts are self-hosted. There are no requests off localhost in e2e.
   - Colours come from tokens only: no hex, `rgb(` or `hsl(` in `app/styles/*.css` (white excepted) or in
     `app/components/**` and `app/charts/**` source.
6. **SSR.**
   - No greenhouse data in server HTML (`root.test.tsx:37`): the server renders the shell, headers, tabs and
     skeletons at their reserved heights.
   - Responsive layout is CSS. Where phone and desktop differ in structure (Now's track or lists, Schedules' agenda or
     day), both render and CSS shows one. `usePhone()` is for behaviour only.
7. **Charts.**
   - uPlot is imported only lazily in `charts/uplot-chart.tsx`.
   - A route that imports `../charts/` exports `links` with `chartLinks()`. `root.tsx` never imports charts.
   - Chart source has no colour literals, `rgba(`, style attributes, `innerHTML` or DLI (`charts/chunks.test.ts`).
   - `components/data/*` must not import `charts/`.
8. **Stylesheets** (`app/styles.test.ts`).
   - Weather's `:root` stays byte for byte, and the §2.2 block stays verbatim and last in `styles.css`. `styles.css`
     keeps exactly two `:root{` blocks and the light theme only.
   - No class name begins with `.calendar`, `.day-grid`, `.preview`, `.rain-`, `.editor-`, `.still`, `.keo` or the
     other excluded weather prefixes.
   - Section files load after `styles.css` and never redefine `:focus-visible` or the input borders.
9. **Breakpoint.** 860 px stays the navigation and chart-axis breakpoint (`lib/narrow.ts`,
   `charts/time-axis.ts:9`), and its JS copies collapse into `lib/narrow.ts`.
10. **Words.** All copy for codes goes through `lib/words.ts`. Unknown codes show raw.
11. **Where tests run.** The vitest and Playwright suites run in the in-cluster CI (the commit status
    `Verdify Platform / Argo PR CI`), not on the Mac. Batch changes per PR; CI rounds are slow.

## 10. Implementation plan

### 10.1 Rules for every package

- **Ownership.** A package edits only the files it owns. It may import anything. It may create new files only in its
  own folders and with its own prefix: new `app/charts/` files are WP0's, except `charts/stats-*`, which belong to WP2.
- **Unit tests** move with their files: a `*.test.ts(x)` belongs to its subject's owner.
- **The frozen data and write layer:** `lib/{queries,writes,session,shell,operator-api,words,format,vocab,use-current,
  polling,now-answer,lines,page-context,chunk-reload,test-fixture-api}` and `generated/*`. It changes only by
  append, through WP0's owner.
- **Fixtures.**
  - The shared fixture files (`e2e/fixtures/{index,world,operations}.mjs`, `e2e/fixture-api.mjs`) are WP0's.
  - Each package adds named fixtures only in its own `e2e/fixtures/<package>.mjs`, which WP0 registers once. The
    fixtures are built from `world.mjs`'s exported helpers.
- **Styles.** Each package has its own stylesheet section, as its own file under `app/styles/`, loaded by
  `root.tsx` in a fixed order: `styles.css`, `tokens.css`, `primitives.css`, `shell.css`, `now-activity.css`,
  `graphs.css`, `water.css`, `control.css`.
  - New classes use the section prefix: `now-`, `act-`, `gr-`, `wat-`, `sch-` or `ctl-`.
  - A package may delete legacy rules in its own file once nothing uses them.
- **The cross-cutting specs** (`a11y`, `layout`, `states`, `roles`, `guard`) are WP0's. Every package keeps them
  green.
- **Shipping.** Each PR leaves every screen working, so packages can ship one at a time through the dashboard's own
  pin PR. "Done" means the `verdify-dashboard` Argo app is Synced and Healthy at a revision containing the change,
  with a live probe of the changed screen.

### 10.2 WP0 Foundation

**Scope.** The section model and routes, the shell and navigation, the page and section headers, layout primitives,
the card system, micro-visualizations, the chart framework, the stylesheet split, tokens, guards, and the test and
fixture scaffolding. WP0 lands as three PRs, in order:

1. **WP0.1 (mechanical, no visual change):**
   - split the screen CSS out of `styles.css` into the section files, unchanged;
   - check that no moved rule sets a property a §2.2 rule sets on the same element;
   - add `tokens.css`, extend the colour guard to `app/styles/*.css` and `components/**`, and add a guard that
     `components/data/*` imports no chart code;
   - split `e2e/equipment-water-depth.spec.ts` into `water-depth.spec.ts` and `equipment-depth.spec.ts`;
   - add the per-package fixture files;
   - collapse the three phone-breakpoint constants into `lib/narrow.ts`.
2. **WP0.2 (IA and shell):**
   - `lib/sections.ts`, the final `routes.ts`, the new nav and phone bar with icons, the account menu, the fault row
     in the header, the count pill;
   - `PageHeader` and `SectionTabs` swapped into every route with §2.1's titles;
   - `/activity/log` (the log moved out, unchanged);
   - `/graphs/statistics` (the scorecard moved, unchanged) with the `/graphs/scorecard` redirect;
   - the `/activity` log-parameter redirect and the removal of `/more`;
   - spec updates for nav, h1s and More.
3. **WP0.3 (primitives and charts):**
   - Card and Panel heads; `MetricTile`, `ObjectRow`, `ListCard`, `DataList`, `Menu`, `Popover` and `StopAllWater`;
   - `BandBar`, `Meter`, `Sparkline`, `Delta`, `StatusStrip`, `TimeTrack` and `lib/time-scale.ts`;
   - one `Chips` with nav, filter and toggle kinds;
   - the `.meta` class and `Ago` consolidated into `freshness.tsx`;
   - `ChartStack`, `ChartGrid`, `RangeToolbar`, the event rail, excursions, envelope and compare;
   - lanes in a stack, `MinutesByZone` measure and window, CSV for lanes, minutes and water use, one tooltip per
     synced group.

   Screens adopt these in their packages. WP0 changes no screen body beyond the header swap.

**Owns.**

- **Routes and root:** `dashboard/app/routes.ts`, `routes.test.ts`, `root.tsx`, `root.test.tsx`;
  `dashboard/app/routes/more.tsx` (deleted), `routes/not-found.tsx`, `routes/auth.tsx`.
- **Shell:** `dashboard/app/components/app-shell.tsx` (+ test), `site-header.tsx`, `banner.tsx` (+ test); new
  `account-menu.tsx`, `icons.tsx`, `stop-all-water.tsx`, `menu.tsx`, `popover.tsx`.
- **Generic components:** `dashboard/app/components/{panel,card,common,kv,details,chips,table,freshness,button,
  badge,toast,state-reason-line,test-render}.tsx` with their tests, `components/forms/*`, and new
  `components/layout/*` and `components/data/*`.
- **Libraries:** `dashboard/app/lib/narrow.ts`; new `lib/sections.ts` and `lib/time-scale.ts`; the frozen layer
  (append only).
- **Charts:** `dashboard/app/charts/*`: every existing file, `charts.css`, `fixtures/*`, and the new
  `chart-stack.tsx`, `chart-grid.tsx`, `range-toolbar.tsx` and `events.ts`.
- **Styles:** `dashboard/app/styles.css`, `styles.test.ts`, `components/badge-contrast.test.ts`,
  `charts/chunks.test.ts`; new `dashboard/app/styles/{tokens,primitives,shell}.css` and
  `styles/sections.test.ts`; the four package files at creation only.
- **e2e:** `dashboard/e2e/{support.ts,routes.ts,a11y.spec.ts,layout.spec.ts,states.spec.ts,roles.spec.ts,
  guard.spec.ts,fixture-api.mjs,fixture-ingress.mjs,verify-browser-tools.mjs}`,
  `e2e/fixtures/{index,world,operations}.mjs`, `dashboard/playwright.config.ts`.
- **Docs:** `dashboard/VENDORED-FROM-WEATHER.md`; `docs/design/dashboard/design-system.md` §2 and §3.
- **Before WP1–WP4 start,** WP0 may make mechanical, behaviour-free edits anywhere: the CSS moves, the header swap,
  the route moves and spec selector updates.

**CSS sections.** `/* Section: Tokens */` (`styles/tokens.css`), `/* Section: Primitives (WP0) */`
(`styles/primitives.css`) and `/* Section: Shell (WP0) */` (`styles/shell.css`).

**Acceptance.**

1. **Navigation.**
   - At 1440, `navigation "Sections"` lists Now, Water, Graphs, Activity, Control.
   - At 360 and 390, `navigation "Sections, phone"` lists the same five in the same order, each at least 44 px with an
     icon. `layout.spec.ts` pins `[/^Now/, /^Water/, /^Graphs/, /^Activity/, /^Control/]`.
   - Control links to `/settings`. `/plan`, `/settings` and `/equipment` mark Control current.
   - `/more` answers 404 and nothing links to it.
2. **Headers.** Every route renders `PageHeader` with §2.1's eyebrow, h1 and `<title>`. Tabbed sections render their
   tabs with `aria-current="page"`.
3. **Account menu.** `.role-pill` reads OPERATOR or VIEWER at 360 and 1440. Sign out works from the account menu.
4. **Banners.**
   - The fault banner is inside the sticky header and stays visible after scrolling 2,000 px.
   - Warn banners are in flow. `[data-banner]` is unchanged.
   - The count pill shows API-02 `alerts.open`, and its colour follows the worst severity.
5. **Redirects.**
   - `/graphs/scorecard?x=1` redirects to `/graphs/statistics?x=1`.
   - `/activity?target=L01` lands on `/activity/log?target=L01`.
   - `/activity?alert=7` stays on Alerts.
6. **Styles.**
   - `styles.css` keeps its two `:root` blocks and the §2.2 block last.
   - `tokens.css` defines §3.3's tokens once.
   - No section file has a colour literal but white.
   - No component or chart source has a hex, `rgb(` or `style={`.
7. **Primitive tests.** Each primitive has unit tests for ready, loading, empty, stale, unknown, error and API-down
   (neutral, no green classes), and renders no `style` attribute.
8. **Chart tests,** on the uPlot mock:
   - `ChartStack` draws one x axis, one cursor time chip and header readouts.
   - Excursion, envelope, compare and event layers paint only when asked.
   - Lanes, minutes and water use export CSV.
9. **Suites.** The full vitest and Playwright suites pass in the in-cluster CI. axe is clean at 320, 768 and 1440 on
   every route. There is no horizontal scroll at 360 or 390.

**Tests and specs to update.** `layout.spec.ts` (tab pin), `a11y.spec.ts` and `states.spec.ts` (new routes come in
through `ROUTES`), `roles.spec.ts`, `support.ts` (the bottom-bar helper is unchanged; add `accountMenu()`),
`routes.ts` (add `/activity/log` and `/graphs/statistics`, drop `/more`), `app-shell.test.tsx`, `root.test.tsx`,
`routes.test.ts`, `styles.test.ts`, `badge-contrast.test.ts` and `chunks.test.ts`. Also the h1 and More pins in
`graphs.spec.ts`, `activity.spec.ts`, `equipment.spec.ts`, `caretaker-visit.spec.ts` and `now.spec.ts`, as a
mechanical update.

### 10.3 WP1 Now and Activity

**Scope.**

- **Now** (§5.1):
  - the hero with band bars, today's range, time in band against yesterday and the status line;
  - quick actions;
  - Needs someone with alerts, mister faults and the not-confirmed link;
  - Water today with zone meters;
  - "Running and next" (track and lists);
  - the zone strip;
  - de-duplication.
- **Activity Alerts and Log** (§5.8, §5.9): groups, repeat counts, details in place, Recent activity, facets, day
  headers, one-line entries, Iris plan collapse and CSV.
- **The caretaker visit** on the new paths.

**Owns.**

- **Routes:** `dashboard/app/routes/now.tsx`, `routes/activity.tsx`, `routes/activity-log.tsx`, with their tests.
- **Components:** `dashboard/app/components/now/*`, `components/{coming-up,zone-tiles,hero,stat-tile,alert-row,
  event-row,burst-panel}.tsx` with tests, and new `components/activity/*`.
- **Libraries:** `dashboard/app/lib/activity.ts`, `lib/last-water.ts`, with tests.
- **Styles:** `dashboard/app/styles/now-activity.css`.
- **e2e:** `dashboard/e2e/now.spec.ts`, `activity.spec.ts`, `caretaker-visit.spec.ts`, `e2e/fixtures/now-activity.mjs`.

**CSS section.** `/* Section: Now and Activity (WP1) */` in `app/styles/now-activity.css`.

**Uses but does not own:** `StopAllWater` (WP0), `ClearFaultButton` and `lib/water.ts` (WP3) and
`lib/zone-source.ts` (WP2); it links into Settings' filters (WP4). Equipment (WP4) uses WP1's `burst-panel.tsx`.

**Acceptance.**

1. **Above the fold.**
   - At 360 × 740 under `in-band`: the h1 "All OK", `.hero-temp`, "Updated 20 s ago" and the three band bars sit
     above the bottom bar.
   - At 390 × 844, Stop all water does too.
2. **Band bars.** Each draws one track, the band box and the value; today's low–high is in its name only. Out of band
   shows a verdict word; in band shows none. RH reads "no band". With the API down, the bars and "In band today" do not render.
3. **Quick actions.**
   - Operators get Stop all water and the three burst buttons, which send one API-IRR-12 each.
   - Viewers get no quick actions row.
   - Before the engine, operators get Stop all water and the "Bursts arrive with the irrigation firmware" line.
4. **De-duplication.**
   - The open-alert count appears in the page body once: in the answer, or in the Needs someone title.
   - There is one Stop all water button.
   - No next-run sentence appears outside "Running and next".
5. **Running and next.** It shows the last 6 h and next 12 h on desktop and two lists on phones, with `[data-chip]`
   and `[data-coming]`. A line with no run in 12 h appears under "Later".
6. **Zones.** Each zone tile has a band bar, a 24 h VPD sparkline and its tag as text, and opens
   `/graphs/zones?zone=`.
7. **Alerts.**
   - `/activity` shows Needs action, Acknowledged and Resolved (24 h) groups, with repeat counts.
   - Acknowledge is one request with an optional note.
   - `?alert=` expands in place.
   - Fence breach shows Stop all water; the leak alert shows the spigot words and no Stop.
8. **Log.**
   - `/activity/log` facets apply on change and live in the URL. The day headers are sticky.
   - An Iris plan reads as one entry with N changes.
   - "CSV of these entries" downloads the loaded rows.
9. **Caretaker visit.** J9 at 360 × 740 runs Now → Fans 15 min → Water → L01 Run… → Stop → Activity → Acknowledge
   with a note, one request each. The viewer variant shows no control.

**Specs and tests to update.** `now.spec.ts`, `activity.spec.ts` and `caretaker-visit.spec.ts`, plus every unit test
of the owned files: `now.test.tsx`, `activity.test.tsx`, `components/now/*.test.tsx`, `alert-row.test.tsx`,
`event-row.test.tsx`, `burst-panel.test.tsx`, `coming-up.test.tsx`, `zone-tiles.test.tsx`, `hero.test.tsx`,
`stat-tile.test.tsx`, `lib/activity.test.ts` and `lib/last-water.test.ts`.

### 10.4 WP2 Graphs

**Scope.**

- **Climate** (§5.5): the story line, toolbar, presets, Layers, Export, adoption of `ChartStack`, the event-data
  reads and the URL model (Today, 90 d, step, zoom out, `events`, `cmp`, `env`, `preset`, the future pad and
  `daily.*` allowed).
- **Zones** (§5.6): `ChartGrid` small multiples and the focused zone.
- **Statistics** (§5.7, §7.2): KPIs, strip rows, typical day, daily range, time-in-band bars and the table with CSV.

**Owns.**

- **Routes:** `dashboard/app/routes/graphs.tsx`, `graphs.zones.tsx`, `graphs-statistics.tsx`, `graphs-scorecard.tsx`
  (the redirect), with their tests.
- **Components:** `dashboard/app/components/graphs/*` with tests.
- **Libraries:** `dashboard/app/lib/{graph-url,graph-reads,graph-overlay,graph-link,zone-source}.ts` with tests, and
  new `lib/graph-story.ts`.
- **Charts:** new `dashboard/app/charts/stats-*.tsx` with tests.
- **Styles:** `dashboard/app/styles/graphs.css`.
- **e2e:** `dashboard/e2e/graphs.spec.ts`, `e2e/fixtures/graphs.mjs`.

**CSS section.** `/* Section: Graphs (WP2) */` in `app/styles/graphs.css`.

**Acceptance.**

1. **Plot space.** At 1440 under the default 24 h view, the temperature plot area is at least 180 px tall and the
   stack draws exactly one time axis. At 390 the temperature plot is at least 130 px.
2. **Cursor.** Hovering one panel updates every panel's header row to the cursor values and shows one time chip.
   There is no floating per-panel tooltip.
3. **Story line.** It states in-band % and up to three excursions. An excursion link sets `from` and `to` around it.
4. **Range.**
   - Today ends at the next local midnight.
   - The 24 h future pad is 6 h.
   - ‹ › shift by the window's length, and Zoom out doubles it, centred.
   - All of these are in the URL.
5. **Layers.**
   - Excursions shade outside the band.
   - The envelope shows from 7 d and `env=0` hides it.
   - `cmp=day` draws yesterday dashed with a delta in each header.
   - The event rail toggles by source, and its marks link to Activity. The event list disclosure holds every mark.
6. **Zones.** `/graphs/zones` shows the 5 × 2 grid with shared cursor and uniform y per row. `?zone=south` keeps the
   h1 "South zone" and the dotted controller target.
7. **Statistics.**
   - `days=30|90|365` work, with four KPIs showing deltas.
   - Strip cells open `/graphs?from=<day>&to=<day>`.
   - Typical day uses the last 14 or 30 days hourly.
   - The table keeps the day/night split for 3 days only, and "CSV of this table" downloads exactly the rows shown.
8. **Picker.** `daily.*` keys appear in the metric picker and plot one point per day.
9. **Units and words.** Every number carries its unit in the header or readout. Times show the offset in the fall-back
   hour.

**Specs and tests to update.** `graphs.spec.ts` (h1s "Climate", "Statistics" and "South zone"; header-row hooks in
place of `.linked-panels figcaption` and `.plot-small`; the range list), plus `graphs.test.tsx`,
`graphs.zones.test.tsx`, `graphs-scorecard.test.tsx` (it becomes the statistics test),
`components/graphs/*.test.tsx`, `lib/graph-url.test.ts`, `lib/graph-overlay.test.ts`, `lib/graph-link.test.ts`,
`lib/zone-source.test.ts` and the new `lib/graph-story.test.ts`.

### 10.5 WP3 Water

**Scope.**

- **The Water layout:** the header status line from the program state, the actions (Pause lines…, Stop all water) and
  the sticky phone row.
- **Today** (§5.2): zone cards, line rows with state-driven actions, a Stop only while running, the menu, today strips and Site
  today.
- **Schedules** (§6): the day timeline, week strip, week matrix, agenda, rules list, popover, create from slot, and the
  editor's layout and preview words.
- **History** (§5.4): the page range, KPIs, measure, CSV and the runs table.

**Owns.**

- **Routes:** `dashboard/app/routes/water.tsx`, `water-board.tsx`, `water-schedules.tsx`, `water-history.tsx`,
  `water.test.tsx`, `water-line-controls.test.tsx`.
- **Components:** `dashboard/app/components/{line-card,mister-association,line-enable,line-options,check-sheet,
  run-dialog,stop-button,clear-fault-button,pause-lines,schedule-week,schedule-editor}.tsx` with tests, and new
  `components/water/*` and `components/schedule/*`.
- **Libraries:** `dashboard/app/lib/{water,line-checks,schedule-preview}.ts` with tests.
- **Styles:** `dashboard/app/styles/water.css`.
- **e2e:** `dashboard/e2e/water.spec.ts`, `water-depth.spec.ts`, new `water-schedules.spec.ts`,
  `e2e/fixtures/water.mjs`.

**CSS section.** `/* Section: Water (WP3) */` in `app/styles/water.css`, with the prefixes `wat-` and `sch-`.

**Acceptance.**

1. **Today layout.**
   - Lines appear inside zone cards in L01–L11 order. Each line row is an `article` named by "L0n …" with
     `data-line` and `data-state`.
   - Mister zone heads hold the VPD control and Schedule switches with the VPD source.
   - The program state is the header status line, with `[data-program]`.
2. **Stop and Run.**
   - Stop shows on every non-fog line for operators. It is filled (`.btn-stop`) only while that line is running or
     unknown, and an outline otherwise. It is one tap and never disabled by the writes flag.
   - The run dialog flow and "Run 10 min" are unchanged.
3. **Row controls.**
   - A line row shows at most two buttons plus ⋯.
   - The enable switch, Options, Pause, "Activity for L01 ›", "Runs of L01 ›" and Graphs are in ⋯. The switch keeps
     its check sheet.
4. **Today strip.** It shows done, planned and skipped runs for today with the now rule, and "next HH:MM · amount".
5. **Schedules, day view.**
   - It shows today by default. ‹ › step days.
   - Blocks are as long as their schedules, grouped by manifold, with night shading, past VPD ticks on mister rows, a
     pause band and a cap meter per row.
   - A block opens its popover with Edit and "Pause L0n until after this run" (operators).
   - An empty-slot click and [+] open the editor prefilled.
6. **Week.** The strip shows the selected week with past outcomes and future counts. `view=week` shows the matrix with
   per-cell totals against the cap. Cells open the day.
7. **Phones.** The agenda is the default: by day, the next row marked NEXT, skipped rows hatched with their reason.
   `view=day` fits 360 px with no sideways scroll.
8. **Rules list.**
   - Rules are grouped by line, disabled ones muted under their line.
   - Each shows `updated_by` and `updated_at`, its sync pill and its enabled switch.
   - The head shows "N of 24" with a meter.
9. **Editor preview.** It shows `too_close`, cap, the 24-schedule limit and manifold waits in words before Save. The
   draft draws dashed on the timeline.
10. **History.**
    - One page range covers 7, 30, 90 and 365 days.
    - KPIs show deltas. Minutes and gallons toggle.
    - Daily water use states its own window.
    - Runs, minutes and water use each export CSV.

**Specs and tests to update.**

- **e2e:** `water.spec.ts` (Stop all, Run, program banner moved to the header, line-row hooks), `water-depth.spec.ts`
  and the new `water-schedules.spec.ts`. The latter covers `.week-table` (the week matrix keeps `tr[data-line]
  td[data-day]` and `.week-days` names) and the 390 px day view with one day visible.
- **Unit tests:** `water.test.tsx`, `water-line-controls.test.tsx`, `line-card.test.tsx`,
  `mister-association.test.tsx`, `line-enable.test.tsx`, `line-options.test.tsx`, `check-sheet.test.tsx`,
  `run-dialog.test.tsx`, `stop-button.test.tsx`, `clear-fault-button.test.tsx`, `pause-lines.test.tsx`,
  `schedule-week.test.tsx`, `schedule-editor.test.tsx`, `lib/water.test.ts`, `lib/line-checks.test.ts` and
  `lib/schedule-preview.test.ts`.

### 10.6 WP4 Control

**Scope.**

- The Control header with "Find a setting" on Plan and Equipment.
- **Settings** (§5.10): the rail with Show and Groups, the two-line rows with bounds bars, the one-pill rule, phone
  selects, and the drawer layout extracted to `components/control/` and opened in place on Plan and Equipment.
- **Plan** (§5.11): the validity bar, trigger marks, the 14-day reliability strip, and lanes opening the drawer in
  place.
- **Equipment** (§5.12): the two-column board, Controller card, Bursts placement, Why, lanes and daily runtime.

**Owns.**

- **Routes:** `dashboard/app/routes/settings.tsx`, `plan.tsx`, `equipment.tsx`, `settings.test.tsx`, `plan.test.tsx`,
  `plan-layout.test.ts`, `equipment.test.tsx`, `equipment-depth.test.tsx`.
- **Components:** `dashboard/app/components/{setting-row,setting-history,band-preview,plan-card,planner-health}.tsx`
  with tests, `components/equipment/*`, and new `components/control/*`.
- **Libraries:** `dashboard/app/lib/{revert,setting-values}.ts` with tests.
- **Styles:** `dashboard/app/styles/control.css`.
- **e2e:** `dashboard/e2e/settings.spec.ts`, `plan.spec.ts`, `equipment.spec.ts`, `equipment-depth.spec.ts`,
  `e2e/fixtures/control.mjs`.

**CSS section.** `/* Section: Control (WP4) */` in `app/styles/control.css`, with the prefix `ctl-`.

**Acceptance.**

1. **Rail.**
   - The Show filters and group anchors carry counts. "Recently changed" appears only when API-09 serves the write
     time.
   - Filters live in the URL (`filter=`) and keep `?edit=`.
2. **Rows.**
   - Each row shows the device value large, the bounds bar with the planned mark, one pill by §4.4's priority, and one
     meta line.
   - CONFIRMED shows only within 10 minutes of the newest write.
   - REQUESTED rows still poll every 5 s and show the reason after 60 s. There is no "Send again".
3. **Drawer.**
   - `/plan?edit=mister_engage_kpa` and `/equipment?edit=sw_gl_main_auto_mode` open the setting drawer in place.
     Closing returns focus to the opener.
   - Plan's "Open" link is gone, and the lane label opens the drawer.
   - Save, Release, Revert and the band preview behave as before (`SETTING_DRAWERS` stay clean in axe and layout).
4. **Plan.** The plan card shows the validity bar and time left. Planner health shows the 36 h marks, the next trigger
   and the 14-day strip from `daily.iris_required_*`.
5. **Equipment.**
   - The h1 is "Equipment" and the board has two balanced columns.
   - The Controller card shows firmware, uptime, reset reason, heap against the 32 kB floor, largest block and RSSI,
     each with its age.
   - Bursts and lights keep their 44 px targets at 390.
   - "Daily runtime · 14 days" shows one bar row per heat 1, heat 2, fan 1, fan 2, vent and fog.
6. **Phones.** At 360, Settings uses the selects, rows stack, and nothing scrolls sideways (the drawer included).

**Specs and tests to update.**

- **e2e:** `settings.spec.ts` (pills and row anatomy; the drawer hooks are unchanged), `plan.spec.ts` (Open becomes
  the lane label; regions unchanged), `equipment.spec.ts` (h1, board) and `equipment-depth.spec.ts`.
- **Unit tests:** `settings.test.tsx`, `plan.test.tsx`, `plan-layout.test.ts`, `equipment.test.tsx`,
  `equipment-depth.test.tsx`, `setting-row.test.tsx`, `setting-history.test.tsx`, `band-preview.test.tsx` and
  `lib/revert.test.ts`.

### 10.7 WP5 Close-out

**Scope.**

- Update [screens](screens.md) (§1 and every SCR section) and [design system](design-system.md) §4–§7 to the built
  state.
- Update [product](product.md) journey steps that name screens: J2's "Open" becomes the lane label; J5's paths; J9's
  "More → Equipment" becomes Now's quick actions.
- Set this file's status to Built.
- Delete code that is no longer used:
  - `charts/linked-panels.tsx`, `charts/range-chips.tsx` and `charts/legend.tsx` if nothing imports them;
  - `components/hero.tsx` and `components/stat-tile.tsx` if unused;
  - the legacy rules left in the section files that no longer match anything.
- Run the final axe, layout and live walk (§11).

**Owns.** `docs/design/dashboard/screens.md`, `design-system.md` (§4–§7), `product.md` (journey steps) and
`redesign.md`. After WP1–WP4 merge, it may make deletion-only edits to their files, with each change listed in the PR.

**Acceptance.** The docs match the build. Nothing removed is still imported. The full suites are green in the
in-cluster CI. The live checklist in §11 passes on dashboard.verdify.ai.

### 10.8 Sequence

1. **WP0** lands first: WP0.1, then WP0.2, then WP0.3. Each must be green in CI.
2. **WP1, WP2, WP3 and WP4** start once WP0.3 merges and run in parallel, one to three PRs each. A primitive fix they
   need goes to WP0's owner as a small PR.
3. **WP5** follows the last of the four.

Each PR ships through the dashboard's own pin PR and is verified live on the screens it changed.

## 11. Acceptance checklist

Walk this at 1440 × 900 and at 360 × 740, once as an operator and once as a viewer, on dashboard.verdify.ai after the
pin PR is live.

**Shell**

- [ ] The header shows Now, Water, Graphs, Activity, Control. The current section has the forest underline. Nothing
  scrolls sideways at any width.
- [ ] The phone bar shows the same five, each with an icon over its label. There is no More, and `/more` says Page
  not found.
- [ ] The Activity count equals the count in Alerts and on Now. It is red with a critical alert open and amber with
  only warnings.
- [ ] The account button shows OPERATOR or VIEWER. It opens to my name, my role and Sign out, which works.
- [ ] With the controller not reporting, the fault row stays in the header while I scroll a long page.
- [ ] Every page title says section and view, and Back closes an open drawer before it leaves a page.

**Now**

- [ ] On the phone, without scrolling: the answer, the temperature, "Updated … ago", three band bars and Stop all water.
- [ ] Each band bar shows one track, the band box and the current value (today's low and high in its name only). Out
  of band adds a word; in band adds nothing.
- [ ] "In band today" shows temperature and VPD with yesterday's beside them.
- [ ] As an operator I can start Fans 15 min from Now. As a viewer I see no quick actions.
- [ ] The alert count appears once in the page body. The next run appears only in "Running and next".
- [ ] "Running and next" shows the last 6 hours of heat, vents, fog and mist and the next 12 hours of runs and plan
  changes, with sunrise and sunset. On the phone it is two lists.
- [ ] Water today shows each zone's minutes against its cap and when it was last watered, plus SAF-4 gallons.
- [ ] A zone tile opens that zone in Graphs.

**Water → Today**

- [ ] Lines sit in their zones. The center zone's head holds VPD control and Schedule with the VPD source.
- [ ] Only a running line (or one whose relay reads unknown) has a Stop, filled red; idle lines have none, and Stop all water stops everything (D-UX-06, 2026-11-06).
- [ ] Each line shows one state sentence, a today strip and its next run. Everything else is under ⋯.
- [ ] SAF-4, the soft budget, flow and occupancy are in Site today. On the phone, "■ Stop all" stays reachable while
  scrolling.

**Water → Schedules**

- [ ] The day timeline shows each run at its time and length, grouped by manifold, with night shaded and the now line.
- [ ] Past VPD misting shows as ticks on the mister rows. A paused line shows its pause band and who paused it.
- [ ] Each row ends with minutes against the cap. A run that would hit the cap is marked.
- [ ] Clicking a run shows its rule, its outcome or next times, Edit, and Pause until after this run.
- [ ] Clicking an empty time on a line opens a new schedule prefilled with that line, day and time.
- [ ] The week strip shows past days' outcomes and future counts. Week shows the line-by-day matrix with totals
  against the caps.
- [ ] The rules list shows every schedule under its line, including disabled ones, with who changed it last.
- [ ] While I type in the editor, too-close, cap and 24-schedule problems show in words, and the draft appears on the
  timeline.
- [ ] On the phone the agenda lists today and the next days with NEXT and skip reasons, and Day fits the screen.

**Water → History**

- [ ] One range (7, 30, 90, 365 days) drives the page. The KPIs show change against the previous period.
- [ ] Minutes and gallons switch on the zone chart. Runs filter on change and download as CSV.

**Graphs → Climate**

- [ ] The plots are tall, and the stack has one time axis at the bottom.
- [ ] Moving the cursor updates every panel's header with the values at that time. There is one time label and no
  stack of tooltips.
- [ ] The story line states time in band and the excursions. An excursion link zooms to it.
- [ ] Today, ‹ ›, Zoom out and Custom work. The 24 h view has only 6 h of future.
- [ ] Out-of-band stretches are shaded. At 7 d the min–max envelope shows.
- [ ] Event marks show my changes, Iris's plans, alerts and runs. A mark opens its Activity entry.
- [ ] Compare with yesterday draws a dashed line and a delta.

**Graphs → Zones and Statistics**

- [ ] Zones shows every zone side by side on the same scale with one cursor. A zone opens its full charts.
- [ ] Statistics shows 30, 90 and 365 days, four KPIs with deltas, and a day-by-day strip. A day opens Climate for that
  day.
- [ ] The typical day and daily range charts render, and the table downloads as CSV.

**Activity**

- [ ] Alerts shows Needs action first, with repeat counts, and Acknowledge works in one tap with an optional note.
- [ ] An alert opens in place with its runbook and "Open in Graphs ±2 h".
- [ ] Log filters by who and what on change, groups by day, collapses Iris plans, and downloads CSV.

**Control**

- [ ] Control opens Settings. The rail filters to Requested, Manual and the others, with counts.
- [ ] Each setting row shows the device value large, its range bar and at most one pill. Old CONFIRMED pills are gone.
- [ ] A Plan lane opens its setting drawer on the Plan page, and the change reads REQUESTED, then CONFIRMED.
- [ ] Equipment shows the controller's firmware, uptime, heap against its floor and Wi-Fi. The board's two columns are
  balanced.
- [ ] Daily runtime shows 14 days per heater, fan, vent and fog.

**Everywhere**

- [ ] With the API stopped, nothing is green, no page says "All OK", "in band" or "CONFIRMED", and every panel says
  why.
- [ ] A viewer sees every page with no write control.
- [ ] axe is clean at 320, 768 and 1440. With reduced motion nothing moves. Every target on the phone is at least 44 px.

## References

- ISA-101 high-performance HMI (colour for abnormal, overview to detail):
  https://blog.isa.org/modern-dcs-graphics-level-1-overview-displays
- NN/g, "Hamburger menus and hidden navigation": https://www.nngroup.com/articles/hamburger-menus/
- Apple HIG, tab bars (avoid overflow tabs; badges for critical information):
  https://developer.apple.com/design/human-interface-guidelines/tab-bars
- Grafana, shared crosshair and tooltip:
  https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/modify-dashboard-settings/
- Grafana, annotations: https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/annotate-visualizations/
- Stripe, chart layout (headline metric above a fixed-height chart):
  https://docs.stripe.com/stripe-apps/patterns/chart-layout
- Home Assistant, Sections view: https://www.home-assistant.io/dashboards/sections/
- Rachio community, "Calendar should show past days as well as future":
  https://community.rachio.com/t/calendar-should-show-past-days-as-well-as-future/22333
- Rain Bird IQ4 (projected flow, Dryrun):
  https://www.rainbird.com/sites/default/files/media/documents/2022-09/d417773-0120-iq4-techspec-english_090222.pdf
- ecobee schedule redesign: https://www.ecobee.com/citizen/a-smarter-simpler-way-to-manage-comfort-at-home/
- Ambulatory Glucose Profile report (time in range, modal day):
  https://www.healthpartners.com/institute/wp-content/uploads/2025/05/Ambulatory-Glucose-Profile-Report-Overview.pdf
- Stephen Few, bullet graph design specification:
  https://www.telerik.com/docs/default-source/blogs-docs/bullet_graph_design_spec-pdf
- EEMUA 191 alarm rates:
  https://www.icheme.org/media/17392/lc-0151_21-lead-process-safety-metrics-alarm-rationalisation-final.pdf
