# Backlog: lab.verdify.ai site refactor and data integrity - 2026-05-20

Source: live `lab.verdify.ai` content reviewed on 2026-05-20 plus the uploaded feedback transcript. Treat the feedback transcript as the source of truth for requested changes.

This backlog is split into two workstreams:

- **Codex-ready site/content refactor:** public site information architecture, copy, page consolidation, layout, and generated-site QA.
- **Data/research project:** Grafana, SQL/view, and time-series data integrity issues behind Planning Quality and Resource Use.

## Execution status

Updated 2026-05-20 after the site/content refactor, Planning Quality dashboard repair, Resource Use water-cost data fix, and the later live visual/calculation feedback pass.

Recovery note, 2026-05-22: item-level `Complete` below means the implementation
work was completed in the current lab-site effort. RM-5/RM-8 later proved the
corresponding source changes were committed through PR #81, rebuilt, reloaded,
and live-validated from tracked source; `lab.verdify.ai` DNS now resolves to the
Verdify gateway and issue #82 is closed.

| ID | Status | Evidence |
|---|---|---|
| CODEX-001 | Complete | `site/quartz/components/SiteNav.tsx` now puts `Latest Plan`, `AI Greenhouse`, `Evidence`, `Architecture`, `Resource Use`, `About`, `Contact`, and `Verdify Consulting` in Overview; `Known Limits` is removed from visible nav; `/plans/latest` aliases the newest generated daily plan; `/reference/known-limits` aliases Climate. `make site-doctor` reports 0 findings. |
| CODEX-002 | Complete | Sitewide visible prose now uses `AI planning agent`; `What Iris Adds` became `What The AI Planning Agent Adds`; generated pages were refreshed for Planning Archive, Lessons, AI Tunables, Baseline comparison, daily plans, and public sample CSVs; the old Quartz `IrisClarifier` transformer was removed. Remaining `Iris` matches are documented below as route/path, immutable ID, host/service, image filename, or validation-key exceptions. |
| CODEX-003 | Complete | Homepage no longer contains the Lutron-specific lighting copy or provenance/dispatcher/cfg-readback paragraph; homepage includes both public camera snapshots and 30-second refresh text; Greenhouse Tour now points to the homepage camera section instead of duplicating it. |
| CODEX-004 | Complete | `start/ai-greenhouse.md` now uses neutral planner terminology, has a consistent FAQ block, clarifies solar/battery versus grid/gas heat, preserves the edge-control claim boundary, and removes the Mycodo/HAGR/iGrow/Koidra/Source.ag/Blue Radix comparison section. |
| CODEX-005 | Complete | Contact page now keeps only the concise intro plus form; the fallback and project-context paragraphs were removed. |
| CODEX-006 | Complete | Operations now answers "what is the greenhouse doing right now?": component context was added for heaters, fans/vent, fogger/misters, grow lights, irrigation/fertigation, controller health, and freshness; water/resource accounting was reduced to a Resource Use pointer. |
| CODEX-007 | Complete | Climate now has a distinct control-path and microclimate job: equipment limits, zones, external weather pressure, and forecast-miss context replaced duplicate homepage-style compliance panels; the equipment table was converted to responsive `data-table` rows. |
| CODEX-008 | Complete | `reference/ai-tunables.md` is now the canonical **Planner Contract and AI Tunables** page with trigger schedule, payload/runtime contract, accepted writes, publishing behavior, routine contract fields, tunable registry, and readback evidence. Planner nav lists one merged entry; `/reference/planner-contract/` remains as a hidden moved-page compatibility URL. |
| CODEX-009 | Complete | Resource Use no longer includes the Winter Gas Constraint table; the page now presents one canonical Daily Cost By Source panel, keeps Runtime Hours By Equipment, and withholds duplicate/monthly cost panels until the underlying water-cost anomaly is corrected. |
| CODEX-010 | Complete | Known Limits is no longer a content page or nav item; old Known Limits URLs are aliases to Climate; inline Known Limits links were replaced with Climate, Resource Use, Safety, Lessons, or Planning Quality destinations. |
| CODEX-011 | Complete | Sitewide table CSS now constrains article tables, wraps table/code text, and removes nowrap header behavior so wide Markdown/generated tables do not force the main column beyond the viewport. |
| CODEX-012 | Complete | Rebuild completed and `make site-doctor` reports 103 pages, 610 internal links, 118 Grafana iframes, and 0 findings. Search gates for `Iris`, `Evidence Index`, `Known Limits`, `Lutron` on cleaned entry pages, contact fallback text, Winter Gas Constraint, and AI Greenhouse comparison names return no unexpected public-page matches. `make lint`, TypeScript, Prettier, public-site lint, targeted database regression tests, and full `make test` pass (`441 passed, 2 skipped, 1 xfailed`). |
| RP-001 | Complete | Planning Quality Grafana panels now use the local America/Denver date for `fn_planner_scorecard`; the stale 14d compliance/accuracy queries were moved to `v_forecast_plan_outcome_mart`. The regression test `test_planning_quality_panel_sources_have_current_rows` covers current local-day scorecard rows and recent 14d outcome rows. |
| RP-002 | Complete | Root cause: `v_water_meter_daily` summed rejected water-meter deltas and used a too-loose 200-gallon high-delta threshold, so interleaved counter jumps inflated January-March water cost. Migration `131-water-meter-quality-filter.sql` reclassifies `delta_gal > 25`, sums only `quality_flag='ok'` deltas, recomputes `daily_summary`, and updates `utility_cost`. Before/after: January `$4044.06 -> $3.87`, February `$1969.26 -> $4.15`, March `$660.08 -> $10.18`. Guardrail tests now assert the daily view excludes rejected deltas and monthly water cost stays plausible. |
| RP-003 | Complete | Resource Use copy and embeds now designate one canonical Daily Cost By Source graph; duplicate daily/monthly variants are removed from the public Resource Use page while dashboard definitions use the same daily_summary cost fields. |
| RP-004 | Complete | Cost bar panels in the economics dashboards now use filled bars (`drawStyle: bars`, nonzero fill opacity, zero line width, normal stacking) rather than outline-only rendering. |
| RP-005 | Complete | The Climate page information design now separates homepage, Climate, Planning Quality, and Operations jobs: Climate owns weather pressure, internal behavior, zones, equipment limits, control paths, and forecast-miss implications. |
| CODEX-013 | Complete | Lighting panels now distinguish indoor lux, outdoor lux, and solar altitude through the Grafana brand script: indoor lux has stronger sunshine-yellow fill, outdoor lux has lower fill, and solar altitude is a dashed no-fill line. Occupancy/day-night state colors were normalized, the DLI embed now uses `now-30d`, and `Lighting`, `Hydroponics`, and `Soil Sensors` are promoted into Overview nav. Render checks covered `greenhouse-lighting` panels 9, 10, and 16. |
| CODEX-014 | Complete | Resource Use now restores three individual solar-alignment panels: electric load vs solar, gas therms/min vs solar, and water GPM vs solar. The combined all-resource "Solar vs Resource Use" rollup remains absent. Render checks covered the three restored panels. |
| CODEX-015 | Complete | `website/static/verdify-architecture.svg` draws the dispatcher-to-ESP32 return path before the ingestor elements so the connector sits behind the ingestor block in the rendered Architecture diagram. Regression coverage in `tests/test_15_lab_site_followup.py` verifies the SVG order. |
| CODEX-016 | Complete | `scripts/brand-grafana-embeds.py` now encodes the public visual rules for transparent/white embeds, reduced double chrome, stronger solar/sun/lux fills with gradient fade, no extra filled-band outlines, temp/VPD band consistency, lighting-style relay state lanes, and solid gray observed outdoor VPD. Source and live brand checks pass, `make site-doctor` reports 0 findings, and key panels rendered successfully. |
| CODEX-017 | Complete | Resource Use has the six-month stacked Monthly Resource Cost by Source chart using canonical daily-summary cost fields, with overlapping segment dollar labels disabled; Runtime Hours remains visible; GPU Board Power is included on Resource Use and distinct VM/GPU colors are enforced in the dashboard. |
| CODEX-018 | Complete | Architecture no longer includes Homelab Compute and Agent Fleet, MQTT, or Not Production Safe content. GPU power evidence lives on Resource Use instead of Architecture, and live Architecture curl checks show the stale headings are gone. |
| RP-006 | Superseded | The original panel mismatch was real: one graph computed `kwh_estimated * 0.111` while stat cards used stored `daily_summary.cost_electric`. The stored canonical field is still the single panel source, but RP-008 changed that field from Shelly-derived metered kWh to runtime-modeled kWh from published watts and observed on-time. |
| RP-007 | Complete | Lux review confirmed the active readback thresholds are 40,000 lux with 8,000 lux hysteresis and recent Tempest daylight samples exceed 100,000 lux, so the "45,000 lux full sun" view was a panel/window/aggregation interpretation rather than a unit cap. Lighting copy now explains exterior threshold semantics, and lux/solar visual treatment was normalized through the brand script and render checks. |
| RP-008 | Complete | Public electric cost now uses published device wattage from `equipment_assets.wattage` multiplied by observed relay on-time, matching the gas therms model. Shelly kWh remains stored as `daily_summary.kwh_total` and exposed through `v_energy_estimate_reconciliation` as diagnostic evidence; it no longer overwrites `cost_electric` or public Resource Use panels. Follow-up performance fix: public runtime-watts panels now use `fn_runtime_power_30m()` instead of per-sample `fn_equip_at()` lookups. |

### CODEX-002 terminology exception list

The scrub intentionally preserves these `Iris`/`iris` match classes:

- Immutable plan IDs and generated historical IDs, for example `iris-20260519-0543`, `iris-oneshot-*`, and `iris-validation-*`.
- Legacy route and file names that must remain stable, especially `/data/baseline-vs-iris/` and `scripts/generate-baseline-vs-iris-page.py`.
- Host, service, and code identifiers such as `vm-docker-iris`, `hermes:iris:*`, `ingestor/iris_planner.py`, and local filesystem roots under `/mnt/iris`.
- Historical image filenames under `/start/slack-ops/iris-*.png`; alt text and captions now use `AI planning agent`.
- Developer/validation labels such as `Iris-dev` when they identify historical implementation/debug context rather than public planner branding.

## Current-state readback

This section preserves the initial live-site readback from the 2026-05-20 review. The execution-status table above is the current tracking state.

The left navigation still places **Architecture** under Reference, **Latest Plan** under Planner, and **Resource Use** under Live Evidence. **Known Limits** is still present under Reference, and **Evidence Index** is still the visible nav label. [Verdify Lab][1]

The homepage currently has exactly the two copy problems called out in the feedback: the lighting paragraph mentions the two Lutron circuits, and the following paragraph contains the "crop target provenance, dispatcher math, cfg readbacks, qualified-minutes accounting" copy that should be removed. [Verdify Lab][1]

The AI Greenhouse page is still centered on "Iris" language, has a "What Iris Adds" section, includes a Technical FAQ, includes the current solar-aligned/off-grid wording, and still has the comparison section covering Mycodo, HAGR, iGrow, Koidra, Source.ag, and Blue Radix. [Verdify Lab][2]

The Contact page already has the desired first sentence, but still includes the two paragraphs the feedback wants removed: the fallback "If the form fails..." paragraph and the "For project context..." paragraph. [Verdify Lab][3]

The Operations page currently claims ownership over water accounting alongside active relays, data freshness, active plan age, alerts, diagnostics, lighting, wetting, irrigation, and Slack briefs. That conflicts with the feedback direction to keep water status on Resource Use and make Operations more about components and live system state. [Verdify Lab][4]

The Climate page currently includes the "What The Equipment Can Change" table and live climate pressure panels. This aligns with the feedback that the page should be reworked around control paths, zones, equipment, and external weather rather than duplicating homepage-style compliance charts. [Verdify Lab][5]

The Resource Use page still contains the "Winter Gas Constraint" section with the August, January, and March table the feedback says to delete. It also owns cost, gas, electricity, water, and solar-alignment framing, so it is the right home for the water-status/cost work. [Verdify Lab][6]

The Greenhouse Tour page currently contains the **Greenhouse Cameras** section with two public camera snapshots refreshing every 30 seconds, and that section should move to the homepage near the bottom. [Verdify Lab][7]

The Planner Contract and AI Tunables pages are currently separate. Planner Contract owns triggers and publishing, while AI Tunables owns the bounded writable parameter surface. The feedback asks to merge these because the planner contract and tunables go hand in hand. [Verdify Lab][8]

The public repo exposes a `site/` static site source, plus `grafana/`, `db/`, `ingestor/`, `scripts/`, and related implementation directories. The repo README says normal validation includes `make check`, `make lint`, `make test`, and `make firmware-check`. [GitHub][9]

## Codex task set

### CODEX-001 - Rework left navigation and page labels

**Goal:** Update the public site IA to match the requested overview emphasis.

**Instructions for Codex:**

- Locate the Quartz navigation/sidebar configuration in the `site/` source.
- In **Overview**, keep `Home`, `AI Greenhouse`, and the external consulting-site link.
- Label the external `https://www.verdify.ai/` link **Verdify Consulting** and place it last in the Overview section after `Contact`.
- Rename `Evidence Index` to **Evidence** everywhere visible.
- Move **Latest Plan** into Overview.
- Move **Architecture** into Overview.
- Move **Resource Use** into Overview.
- Remove **Known Limits** from the visible nav.
- Keep existing URLs working with redirects or link aliases where appropriate.
- Run the site build and link check after changes.

**Acceptance criteria:**

- Left nav Overview shows: `Home`, `Latest Plan`, `AI Greenhouse`, `Evidence`, `Architecture`, `Resource Use`, `About`, `Contact`, `Verdify Consulting`.
- No visible nav item says `Evidence Index`.
- No visible nav item says `Known Limits`.
- Old deep links for `/reference/known-limits`, `/reference/architecture`, `/plans/latest` or equivalent generated latest-plan routing do not produce broken navigation.

### CODEX-002 - Sitewide "Iris" terminology scrub

**Goal:** Replace named-agent branding with neutral language.

**Instructions for Codex:**

- Run a full text search for `Iris`, `iris`, and `Baseline vs Iris`.
- Replace ordinary prose references with **AI planning agent**.
- Replace "Iris, the AI planner" with **the AI planning agent**.
- Replace "What Iris Adds" with **What the AI Planning Agent Adds**.
- Update alt text, cards, page excerpts, generated snippets, footer links, and metadata.
- Preserve historical plan IDs such as `iris-20260519-0543` only where they are immutable database IDs or generated historical records.
- For the Baseline page, keep the route stable, but change visible copy to **Baseline vs AI Planning Agent** unless the route title must remain for legacy reasons.

**Acceptance criteria:**

- `rg -ni "\bIris\b" site/` returns only immutable plan IDs, database keys, legacy redirect aliases, or an explicitly documented exception list.
- Live copy no longer introduces the planner as a named character.
- Safety, Slack Ops, Planning Loop, AI Greenhouse, AI Tunables, Planning Archive, Lessons, Architecture, Hydroponics, Soil, and homepage copy all use the new phrase consistently.

### CODEX-003 - Homepage cleanup and camera move

**Goal:** Clean the homepage and make it a stronger public entry point.

**Instructions for Codex:**

- On the homepage, remove the Lutron-specific wording from the lighting control loop paragraph.
- Delete the paragraph beginning with the "crop target provenance / dispatcher math / cfg readbacks / qualified minutes" concept.
- Leave "What To Look At First," "Why This Is Worth Checking," "Claim Boundary," and "Further Reading" intact except for link-label updates caused by other tasks.
- Move the complete **Greenhouse Cameras** section from the Greenhouse Tour page to the homepage near the bottom, above Further Reading or just before the footer.
- Preserve the camera privacy/safety caveat: snapshots are visual context, not a control input.
- After moving, leave either a short pointer on the Greenhouse Tour page or remove the duplicate section if the homepage becomes canonical.

**Acceptance criteria:**

- Homepage no longer says "Lutron."
- Homepage no longer contains the provenance/dispatcher/cfg readback/qualified-minutes paragraph.
- Homepage includes both public camera snapshots and the 30-second refresh note.
- Greenhouse Tour does not duplicate a full camera section unless the duplication is intentional and clearly justified.

### CODEX-004 - AI Greenhouse page rewrite and FAQ formatting

**Goal:** Make AI Greenhouse cleaner, less comparative, and more readable.

**Instructions for Codex:**

- Apply the sitewide "AI planning agent" terminology.
- Reformat the Technical FAQ as a true FAQ component or consistently styled FAQ block.
- Clarify the power/heating answer:
  - The greenhouse has solar/battery context.
  - It can operate through some electrical constraints.
  - It still needs gas heat during very cold conditions.
  - Do not overstate off-grid autonomy.
- Remove the entire comparison section currently asking how Verdify compares to Mycodo, HAGR, iGrow, commercial CEA systems, etc.
- Keep the claim boundary: no full autonomy, no yield/profit superiority claim, deterministic relay control remains at the edge.

**Acceptance criteria:**

- No comparison section remains on AI Greenhouse.
- FAQ has consistent question/answer styling and scannable spacing.
- Solar/gas wording matches the feedback and does not imply the greenhouse can safely run through all winter conditions without gas.

### CODEX-005 - Contact page cleanup

**Goal:** Keep the Contact page concise.

**Instructions for Codex:**

- Keep: "For corrections, technical questions, press, build comparisons, or collaboration, use this form. Verdify will not add you to a mailing list."
- Remove the "If the form fails..." paragraph.
- Remove the "For project context..." paragraph.
- Do not add replacement fallback copy.

**Acceptance criteria:**

- Contact page has only the concise intro plus the form.
- No "if the form fails" text remains.
- No "for project context" text remains.

### CODEX-006 - Operations page refocus

**Goal:** Make Operations the live component/system page, not the water-resource page.

**Instructions for Codex:**

- Remove water-status emphasis from Operations or convert it into a brief link to Resource Use.
- Review cards like water status, panic check, active plan, and active release.
- Keep current controller state, active relays, diagnostics, data freshness, alerts, firmware/controller state, active plan age, and live operations dashboard links.
- Add/expand component-level context for heaters, fans, vent, fogger, misters, grow lights, irrigation, controller health, and data freshness.
- Link water/resource timing to Resource Use rather than embedding water accounting on Operations.

**Acceptance criteria:**

- Operations answers "what is the greenhouse doing right now?"
- Resource Use answers "what water/electric/gas resource was spent?"
- No duplicate water accounting story is maintained in both places.

### CODEX-007 - Climate page redesign and responsive table fix

**Goal:** Turn Climate into the control-path and microclimate explanation page.

**Instructions for Codex:**

- Rework Climate around:
  - control paths,
  - zones,
  - equipment limits,
  - outside weather/forecast inputs,
  - external pressure and forecast miss context.
- Remove duplicate homepage-style panels: safe band vs reality, temperature compliance band, and VPD compliance band if they repeat the homepage story.
- Keep or improve "What The Equipment Can Change," but make it fit the center content width.
- Convert wide tables to responsive cards or compact tables.
- Apply the same responsive-table treatment sitewide.

**Acceptance criteria:**

- No horizontal scrollbar appears in the main content column at common viewport widths.
- Climate page has a clear purpose distinct from the homepage and Planning Quality.
- Equipment/control-path content is readable on mobile and desktop.

### CODEX-008 - Merge Planner Contract and AI Tunables

**Goal:** Combine contract and tunable traceability into one canonical planner control page.

**Instructions for Codex:**

- Create one canonical page, suggested title: **Planner Contract and AI Tunables**.
- Merge:
  - trigger schedule,
  - payload/runtime contract,
  - accepted writes,
  - publishing behavior,
  - tunable registry,
  - readback/landing evidence,
  - table of routine plan contract fields.
- Remove one nav entry so the Planner section does not list both old pages.
- Add redirects from both old URLs to the merged canonical page.
- Apply responsive table fixes to routine plan contract and tunable tables.

**Acceptance criteria:**

- Planner nav has one merged contract/tunables entry.
- Existing `/reference/planner-contract` and `/reference/ai-tunables` links continue working.
- The merged page explains both "what triggers a plan" and "what values the AI planning agent may write."

### CODEX-009 - Resource Use page cleanup and graph consolidation

**Goal:** Make Resource Use credible and less visually confusing.

**Instructions for Codex:**

- Delete the **Winter Gas Constraint** section and the August/January/March table.
- Consolidate daily cost charts into one canonical **Daily cost by source: electric / gas / water** graph.
- Keep **Runtime hours by equipment**.
- Remove or replace duplicate "long-range proof daily cost by source" panels if they tell the same story as the canonical daily cost graph.
- Fix bar chart rendering where bars appear as outlines instead of filled bars.
- Fix or hide monthly cost charts until the water anomaly research is complete.
- Update page copy so it does not over-explain broken or duplicate graphs.

**Acceptance criteria:**

- No Winter Gas Constraint table remains.
- One daily cost-by-source graph is canonical.
- Runtime hours by equipment remains visible.
- Monthly cost by category does not imply obviously false thousands-of-dollars water spend.

### CODEX-010 - Delete Known Limits page and repair links

**Goal:** Remove the Known Limits page from the public reading path.

**Instructions for Codex:**

- Delete the Known Limits content page or convert it into a redirect.
- Remove all visible nav references.
- Replace inline links to Known Limits with better destinations:
  - Safety Architecture for relay/firmware risk.
  - Climate for physical constraints.
  - Resource Use for cost/resource constraints.
  - Lessons for durable operational findings.
  - Planning Quality for scorecard/stress evidence.
- Ensure the homepage Further Reading list no longer links to Known Limits.

**Acceptance criteria:**

- `/reference/known-limits` does not appear in visible navigation.
- Site build has no broken internal links.
- Existing external links to Known Limits either redirect or show a minimal "moved" page.

### CODEX-011 - Sitewide table/layout QA

**Goal:** Fix the recurring horizontal-scroll complaint across the site.

**Instructions for Codex:**

- Audit every markdown table and generated table in the public site.
- Add a reusable responsive table/card style for narrow content.
- Avoid fixing only the Climate page; include Planner Contract, AI Tunables, Resource Use, Operations, Data Model, Equipment, Zones, and generated plan pages.
- Check desktop and mobile widths.

**Acceptance criteria:**

- No main-column horizontal scrollbar at 360px, 768px, 1024px, or 1440px viewport widths.
- Wide generated tables either wrap, become cards, or use a deliberate internal scroll container with visible affordance.
- Routine plan contract and equipment tables fit cleanly.

### CODEX-012 - Final generated-site QA and deployment checks

**Goal:** Catch regressions from content moves and generated pages.

**Instructions for Codex:**

- Run the site build.
- Run link checking.
- Run `rg` checks for:
  - `Iris`
  - `Evidence Index`
  - `Known Limits`
  - `Lutron`
  - `If the form fails`
  - `For project context`
- Run repo validation commands available in the project, including `make check` where practical. The repo README lists `make check`, `make lint`, `make test`, and `make firmware-check` as validation commands. [GitHub][9]
- Produce a before/after summary of changed pages.

**Acceptance criteria:**

- Build succeeds.
- Link check succeeds.
- Search checks only return documented exceptions.
- Public site has no obvious duplicated pages, broken redirects, or stale nav labels.

## Follow-up Codex task set - live visual review

These items came from the later live review on 2026-05-20 after the first site/content pass. They are open until patched, rendered, and checked against live Grafana/site output.

### CODEX-013 - Lighting page graph differentiation and nav promotion

**Goal:** Make the lighting page easier to read by giving indoor lux, outdoor lux, solar altitude, occupancy, and day/night states distinct visual treatments; move key environment pages into Overview navigation.

**Instructions for Codex:**

- Review every embedded lighting panel on `/greenhouse/lighting/`, including `greenhouse-lighting` and `site-climate-lighting` dashboard panels.
- For `Indoor Lux`, `Outdoor Lux`, and `Sun Altitude`:
  - keep sun/solar/lux in the established sunshine-yellow family where appropriate,
  - make `Sun Altitude` a dashed line only, with no fill,
  - make `Indoor Lux` visibly higher-opacity than `Outdoor Lux`,
  - avoid making all three series look like the same filled yellow shape.
- Apply the same indoor/outdoor/altitude treatment consistently to all lighting panels that compare those concepts.
- In lighting state/timeline panels:
  - map **Occupied** to blue,
  - keep **Unoccupied**/empty yellow or subdued yellow,
  - map **Night** to navy blue with high opacity,
  - keep **Day** yellow,
  - make occupied/unoccupied and day/night visually distinguishable at a glance.
- Change the DLI accumulation graph on the lighting page to show the last 30 days by default.
- Move `Lighting`, `Hydroponics`, and `Soil Sensors` from the `Greenhouse` nav group into the main `Overview` nav group.
- Run the Grafana branding check, live check, render samples, and `make site-doctor`.

**Acceptance criteria:**

- `Indoor Lux`, `Outdoor Lux`, and `Sun Altitude` no longer collapse into one indistinguishable yellow fill.
- `Sun Altitude` is a dashed line without area fill wherever it is overlaid with lux.
- Indoor lux has stronger visual weight than outdoor lux.
- Occupancy and day/night state timelines show blue occupied, yellow/unoccupied, yellow day, and navy night.
- The DLI accumulation embed on `/greenhouse/lighting/` uses a 30-day range.
- Overview nav includes `Lighting`, `Hydroponics`, and `Soil Sensors`; those links still work from their existing URLs.

### CODEX-014 - Restore individual solar-aligned Resource Use panels

**Goal:** Restore the useful per-resource solar-alignment graphs without bringing back the removed all-resources rollup panel.

**Instructions for Codex:**

- Review the Resource Use page and the `site-evidence-economics` dashboard history/current JSON.
- Restore separate panels for:
  - solar vs electricity,
  - solar vs gas,
  - solar vs water.
- Do **not** restore the single combined "Solar vs Resource Use" graph that overlays all resources together.
- Place the individual panels on `/start/resource-use/` near the solar-alignment/cost-context section.
- Style each panel to match current public Grafana theme rules:
  - transparent/white panel background,
  - no double chrome,
  - sunshine-yellow solar fill/line treatment consistent with other solar panels,
  - electric/gas/water colors consistent with the rest of Resource Use,
  - legible legends and axis units.
- Verify live render output for all three restored panels.

**Acceptance criteria:**

- Resource Use shows three individual solar-alignment panels: electricity, gas, and water.
- The all-resources combined solar rollup panel remains absent.
- The individual panels use consistent colors, fills, units, and panel chrome with the rest of the site.
- `make site-doctor` reports no broken iframe IDs or links.

### CODEX-015 - Architecture SVG connector z-order fix

**Goal:** Keep the dispatcher-to-ESP32 connector from visually crossing on top of the ingestor diagram block.

**Instructions for Codex:**

- Edit `/mnt/iris/verdify-vault/website/static/verdify-architecture.svg`.
- Locate the curved connector from `Dispatcher` back to `ESP32 Controller`.
- Reorder the SVG elements so that connector is drawn behind the ingestor block and associated labels.
- Preserve the existing visible diagram content and route/page structure.
- Render or inspect the architecture page after the edit.

**Acceptance criteria:**

- On `/reference/architecture/`, the dispatcher-to-ESP32 return path sits behind the ingestor diagram elements.
- No architecture text, labels, nodes, or other connectors disappear.
- `make site-doctor` still passes.

### CODEX-016 - Public Grafana visual standards and chrome cleanup

**Goal:** Make public Grafana embeds visually coherent across the site, without arbitrary color drift, double borders, or inconsistent state overlays.

**Instructions for Codex:**

- Audit all public Grafana embeds referenced by the site, especially Home, Resource Use, Climate, Operations, Lighting, Hydroponics, Soil, Planning Quality, and Architecture-linked evidence pages.
- Set embedded panel backgrounds to white or transparent, consistent with the surrounding site theme.
- Remove the double-chrome look by either:
  - disabling Grafana panel borders/background chrome in embeds, or
  - removing/reducing the site-side card border around embedded panels.
- Establish one public-site panel style rule for colors and apply it through `scripts/brand-grafana-embeds.py` or the dashboard JSON source rather than ad hoc manual edits.
- For all sun, solar, lux, and lighting-related graphs:
  - use the same sunshine-yellow family for solar/sun context,
  - keep the fade/gradient fill,
  - set observed and forecast solar fills to roughly 85% opacity unless a panel needs a documented exception,
  - avoid low-opacity fills that make solar visually disappear.
- Remove extra top/bottom outline lines on filled state/band overlays where the fill itself communicates the band.
- Make temperature and VPD bands consistent with the lighting-band model: same fill semantics, no unnecessary outline lines, and matching observed/forecast treatment.
- Normalize relay/on-off state overlays across all public panels to the lighting model: one partial-opacity state lane/fill instead of duplicate solid lines plus separate translucent lines.
- On the homepage VPD compliance panel, render observed outdoor VPD as a solid gray series rather than a dashed or purple series.
- Run branding checks, render representative desktop/mobile screenshots, and `make site-doctor`.

**Acceptance criteria:**

- Public Grafana panels no longer show obvious double borders/chrome.
- Solar/sun/lux fills are consistently visible and use the agreed sunshine-yellow treatment with fade and about 85% opacity.
- Temp, VPD, and lighting bands use consistent fill behavior and do not have stray outline lines.
- Relay state overlays use one consistent partial-opacity visual model across Home, Climate, Lighting, Operations, Hydroponics, Soil, and related evidence pages.
- Outdoor observed VPD on the homepage is solid gray.
- Color changes are traceable to a small documented rule set rather than one-off panel edits.

### CODEX-017 - Resource Use monthly cost and GPU panel cleanup

**Goal:** Make Resource Use show the right cost and homelab evidence without unreadable labels or misleading duplicate panels.

**Instructions for Codex:**

- Add or restore a canonical **Monthly Resource Cost by Source (6 months)** stacked bar chart on `/start/resource-use/`.
- The chart must show exactly the last six labeled calendar months and stack:
  - electric cost,
  - gas cost,
  - water cost.
- Use the same canonical cost fields chosen by `RP-006` and the existing water-cost fix; do not create a third cost definition.
- Remove per-segment dollar labels from the monthly stacked bars because they overlap and are unreadable.
- If labels are used, show only one legible total-dollar label above each monthly bar; otherwise rely on tooltip + legend.
- Keep month labels visible on the x-axis.
- Keep `Runtime hours by equipment`.
- Bring over only the useful GPU board-power-over-time graph to Resource Use as homelab utilization evidence.
- Give each VM or GPU power source a distinct color on the GPU Board Power panel.
- Do not reintroduce the combined "Solar vs Resource Use" rollup panel that overlays all resources together.
- Verify the Resource Use page on desktop and mobile for label overlap, panel chrome, and readable legends.

**Acceptance criteria:**

- Resource Use has one six-month stacked monthly cost chart with gas/electric/water cost by month.
- Monthly stacked bars do not have overlapping dollar labels; optional totals above bars are readable.
- Runtime hours remains visible.
- GPU Board Power appears on Resource Use with distinct per-VM colors.
- The combined all-resource solar rollup graph is absent.
- Resource Use chart colors and panel chrome match the public Grafana visual standards.

### CODEX-018 - Architecture page content simplification

**Goal:** Keep Architecture focused on the greenhouse control architecture, not homelab inventory, deprecated MQTT wiring, or generic warnings.

**Instructions for Codex:**

- Remove the **Homelab Compute and Agent Fleet** content from the Architecture page.
- Remove the MQTT section because it no longer reflects the active system.
- Remove the **Not Production Safe** section.
- If any homelab utilization evidence remains useful, move only the GPU watts over time panel to Resource Use under the GPU/compute evidence context.
- Keep architecture content focused on:
  - sensors and ingestion,
  - planning,
  - dispatcher,
  - ESP32/controller edge behavior,
  - database/API/site publishing path,
  - safety boundaries that are specific and still true.
- Preserve working links and redirects.
- Render the Architecture page after the cleanup and run `make site-doctor`.

**Acceptance criteria:**

- Architecture no longer contains Homelab Compute and Agent Fleet, MQTT, or Not Production Safe sections.
- GPU watts over time evidence, if retained, lives on Resource Use rather than Architecture.
- Architecture reads as a concise current-system diagram/explanation.
- No broken internal links or missing SVG assets are introduced.

## Research project - Planning Quality and Resource Cost Data Integrity

### RP-001 - Fix no-data Grafana panels on Planning Quality

**Problem statement:** The feedback reports that "Planner score today," "Compliance today," "Stress hours today," "Plan compliance last 14 days," and "Plan accuracy by day" show no data in Grafana, likely due to a time-window/query problem. The static crawler snapshot currently shows scorecard values, which suggests the API/static layer may have data while one or more live Grafana panels do not. [Verdify Lab][10]

**Research tasks:**

1. Locate the Planning Quality Grafana dashboard JSON in `grafana/`.
2. Identify the panel IDs and queries for:
   - planner score today,
   - compliance today,
   - stress hours today,
   - plan compliance last 14 days,
   - plan accuracy by day.
3. Compare each query against the static scorecard API/data source used by the public page.
4. Test whether the panels fail because of:
   - dashboard time range,
   - timezone boundary,
   - date truncation in UTC vs America/Denver,
   - missing `now()` window,
   - wrong table/view,
   - missing data after a schema/view change,
   - panel expecting a field alias that changed.
5. Patch dashboard JSON and/or SQL views.
6. Add a small regression query or smoke test that asserts the last 24h and last 14d panels return at least one row when the scorecard API has data.

**Deliverables:**

- Root-cause note.
- Dashboard/query patch.
- Validation screenshots or query outputs.
- Regression test or documented smoke check.

**Acceptance criteria:**

- All five reported panels show data for the current window.
- Query behavior is stable across local-day boundaries.
- No panel silently renders "No data" while the scorecard API has current values.

### RP-002 - Investigate and repair water-cost historical anomaly

**Problem statement:** The feedback says Monthly Cost by Category is dominated by water and implies roughly USD 4,000 water spend in December, which is known to be false. The Resource Use page currently frames electricity, gas, and water cost as operational estimates based on measured usage and static public rates, so the anomaly needs to be fixed in the underlying historical data/model rather than hidden with copy. [Verdify Lab][6]

**Research tasks:**

1. Trace the monthly water-cost calculation from Grafana panel query back to its SQL view/table.
2. Identify source columns for:
   - water gallons,
   - water GPM,
   - misting water,
   - irrigation water,
   - hydroponics/reservoir water,
   - cost-per-gallon rate.
3. Compare December, January, and February against:
   - raw flow-meter rows,
   - derived daily summaries,
   - expected physical operating windows,
   - any backfilled or synthetic rows,
   - unit conversions between gallons, liters, GPM, and gallons/minute.
4. Look specifically for:
   - double counting daily and hourly aggregates,
   - multiplying GPM by seconds/minutes twice,
   - treating cumulative counter values as deltas,
   - reset/spike handling errors,
   - null fill producing large inferred durations,
   - rate decimal errors.
5. Recompute affected historical daily/monthly water cost after root cause is fixed.
6. Add a guardrail test or SQL assertion for impossible monthly water totals.

**Deliverables:**

- Root-cause report.
- Corrected SQL/view/script.
- Backfill or reaggregation script.
- Before/after monthly cost table for December, January, February, and current month.
- Regression test for water-cost sanity.

**Acceptance criteria:**

- Monthly Cost by Category no longer shows impossible water dominance.
- December/January/February water costs are physically plausible.
- The chart remains useful without manual hiding.

### RP-003 - Standardize cost graph definitions

**Problem statement:** The feedback says there are two daily cost-by-source views telling the same story differently. The Resource Use page should have one canonical daily cost by electric/gas/water source.

**Research tasks:**

1. Inventory every Resource Use Grafana panel and generated-page chart that uses cost.
2. Assign one canonical definition:
   - electric cost,
   - gas cost,
   - water cost,
   - total cost,
   - source/time grain.
3. Remove duplicate query variants.
4. Update page copy to describe the canonical graph only.
5. Ensure long-range seasonal panels use the same source model or are clearly labeled as directional estimates.

**Acceptance criteria:**

- One daily cost-by-source graph is canonical.
- Long-range and monthly charts do not contradict daily cost.
- All panels use the same rate assumptions unless explicitly labeled otherwise.

### RP-004 - Fix outlined-bar rendering in Grafana/resource panels

**Problem statement:** The feedback reports that some bar charts render as outlines rather than filled bars, including Daily Cost by Type and Monthly Cost by Category.

**Research tasks:**

1. Inspect panel visualization settings for bar fill, stacking, draw style, opacity, and field overrides.
2. Confirm whether the issue is panel config or malformed query shape.
3. Patch affected panels.
4. Add dashboard review notes for future chart generation.

**Acceptance criteria:**

- Daily and monthly cost bars render as readable bars.
- Legend/source colors and stacking remain interpretable.
- No panel relies on outline-only bars unless intentionally designed that way.

### RP-005 - Climate page information-design pass

**Problem statement:** The feedback is not just "remove graphs"; it questions the purpose of Climate. The page should explain control paths, zones, equipment, and external weather context rather than repeating homepage-safe-band panels.

**Research tasks:**

1. Identify which climate panels are unique versus duplicated from the homepage.
2. Define a new Climate page structure:
   - external weather pressure,
   - internal temp/VPD behavior,
   - control paths by actuator type,
   - zone differences,
   - equipment limits,
   - forecast miss implications.
3. Decide which panels should live on:
   - Homepage,
   - Climate,
   - Planning Quality,
   - Operations.
4. Produce a panel migration/removal plan for Codex.

**Acceptance criteria:**

- Climate page has a distinct job.
- Duplicate graphs are removed or relocated.
- The page explains why equipment choices matter in Longmont conditions.

### RP-006 - Fix Resource Use electric-cost inconsistency

**Status:** Superseded by RP-008. The immediate mismatch was fixed by making public panels read the stored canonical `daily_summary.cost_electric` field. RP-008 then changed how that stored field is computed: runtime-modeled kWh from published watts and observed on-time is now canonical; Shelly measured kWh is diagnostic.

**Problem statement:** The Resource Use page showed inconsistent electric cost math. The 30-day average electric stat reported about USD 0.25/day, while the daily cost graph showed most electric days around USD 4-6. Initial inspection showed the stat used `daily_summary.cost_electric`, while the graph still computed `kwh_estimated * 0.111`.

**Research tasks:**

1. Inventory every Resource Use panel and `site-evidence-economics` panel that references electric cost, including stat cards, daily cost, monthly cost, utility split, and any solar-aligned panels.
2. Compare the SQL and live values for:
   - `cost_electric`,
   - `kwh_estimated`,
   - measured greenhouse kWh fields,
   - rate assumptions such as `0.111` USD/kWh,
   - `cost_total` and monthly rollups.
3. Determine whether the canonical electric source should be the stored `daily_summary.cost_electric` field or a recomputed measured-kWh expression.
4. Patch dashboard SQL, SQL views, or summary/backfill code so every Resource Use electric-cost panel uses the same canonical definition.
5. Add a regression check that prevents the electric 30-day stat and daily graph from diverging materially when they cover the same date window.
6. Update page copy only after the math is fixed; do not explain away contradictory panels.

**Deliverables:**

- Root-cause note for the mismatch.
- Patched dashboard/query/view/backfill code as needed.
- Before/after table for the last 30 completed days: date, kWh source, electric cost, gas cost, water cost, total.
- Regression test or documented smoke query.
- Rendered live Resource Use panel checks.

**Acceptance criteria:**

- 30-day electric stat, daily cost graph, monthly cost chart, and solar-aligned electric panel agree on the electric-cost source.
- No public Resource Use panel shows electric daily costs that contradict the stat-card average for the same window.
- The chosen rate/source assumptions are explicit and consistent across Resource Use.

### RP-008 - Select runtime-modeled electric cost source

**Status:** Complete for the public site and daily summary pipeline. Verdify now uses published watts per device plus observed on-time for public electric kWh/cost, the same modeling posture as gas therms from furnace runtime. The Shelly meter remains useful for reconciliation and hardware diagnostics, but it is no longer trusted as canonical greenhouse electric spend.

**Problem statement:** The prior `RP-006` fix made Resource Use panels internally consistent by using `daily_summary.cost_electric`, but the chosen source was suspect. The displayed electric cost averaged about USD 0.25/day because `daily_summary.cost_electric` was derived from `v_energy_daily.measured_kwh`, which integrates `energy.watts_total` from the Shelly Pro EM50 feed. A 2026-05-20 trace found the last 30 completed days averaged 2.30 Shelly-metered kWh/day, while `daily_summary.kwh_estimated` from equipment relay runtime averaged 24.29 kWh/day.

**Evidence captured 2026-05-20:**

- `energy.watts_total` is ingested as Shelly channel 0 power plus absolute channel 1 power.
- Home Assistant exposes `sensor.shellyproem50..._0_total_active_energy`, but no channel 1 cumulative kWh entity.
- Recent `heat1=true` and `heat2=true` samples average roughly 19 W on the Shelly heater channel, not the modeled 1500 W electric heat or gas-furnace electrical load.
- Home Assistant reported `light.greenhouse_main=on` and `light.greenhouse_grow=on` while `switch.greenhouse_main=off`, `switch.greenhouse_grow=off`, and Shelly channel 0 was near 0 W. The ingestor currently records lighting state from the `switch.*` entities, not the `light.*` entities.
- Fog/fan relay states correlate with higher Shelly watts, but the observed Shelly draw during fog is lower than the 1644 W nameplate/runtime model.
- `v_energy_estimate_reconciliation` currently coalesces `kwh_total` before `kwh_estimated`, so it reports `ok` even when runtime-modeled and Shelly-metered kWh diverge by an order of magnitude.

**Implemented tasks:**

1. Load public electric wattage from every `equipment_assets.wattage` row, with conservative code defaults for the known electric devices only as fallback.
2. Compute `daily_summary.kwh_estimated` and `cost_electric` from equipment on-minutes times published watts.
3. Stop the Shelly measured-energy refresh from overwriting `cost_electric` and `cost_total`; it now only maintains `kwh_total`, `peak_kw`, and capture metadata.
4. Recompute monthly `utility_cost` electric amount/kWh from runtime-modeled `daily_summary` values.
5. Split the model into explicit fields/views for:
   - Shelly-metered kWh,
   - runtime-modeled kWh,
   - validated billable/estimated electric kWh,
   - source/quality flag.
6. Fix `v_energy_estimate_reconciliation` so it compares runtime-modeled kWh to Shelly-metered kWh instead of hiding divergence with `COALESCE(kwh_total, kwh_estimated)`.
7. Update Resource Use copy and Grafana panel labels to say runtime-modeled electric/load instead of Shelly-metered electric.
8. Add regression checks that `cost_electric` matches `kwh_estimated * 0.111` and that reconciliation surfaces `meter_runtime_divergence`.
9. Replace slow Grafana runtime-watts queries with `fn_runtime_power_30m()`, which buckets equipment-state intervals once instead of calling `fn_equip_at()` for every sample/equipment pair.

**Deliverables:**

- Circuit/meter coverage note with channel mapping and unsupported loads.
- Source-of-truth decision for lighting runtime entities.
- Corrected SQL/view/ingestor code for measured, modeled, and canonical electric cost.
- Before/after table for the last 30 completed days: Shelly kWh, runtime kWh, selected canonical kWh, electric cost, and quality flag.
- Regression test for meter/runtime divergence and reconciliation-view honesty.
- Updated Resource Use dashboard labels after the canonical source is selected.

**Acceptance criteria:**

- Public Resource Use no longer implies that the Shelly feed is full greenhouse electric spend.
- The selected electric cost source can explain heater, fogger, fan, and lighting operation without an order-of-magnitude hidden mismatch.
- `v_energy_estimate_reconciliation` surfaces current divergence instead of reporting `ok`.
- The 30-day electric stat, daily graph, monthly graph, and solar-aligned electric panel all use runtime-modeled electric values or are explicitly labeled as meter diagnostics.

### RP-007 - Review lighting lux targets and exterior lux alignment

**Problem statement:** The live lighting panels made the lighting target look unexpectedly high, and the observed exterior/full-sun lux context appeared too low or hard to interpret. The user specifically called out a panel where full sun appears around 45,000 lux and asked for a full review of lux graphs and visualizations so solar availability, exterior Tempest lux, and lighting thresholds line up clearly.

**Research tasks:**

1. Inventory every public lux, solar, sun-altitude, DLI, lighting-threshold, and lighting-switch panel.
2. Trace each panel's data source for:
   - indoor lux,
   - outdoor/Tempest lux,
   - solar altitude,
   - solar forecast,
   - lighting target/threshold,
   - occupied/unoccupied threshold state,
   - day/night state.
3. Verify units and scaling for Tempest exterior lux, forecast solar context, indoor lux, and lighting thresholds.
4. Explain why the current target appears high, or patch the target/query/label if it is wrong.
5. Check whether exterior lux readings around 45,000 lux are plausible for the actual sensor placement, date, weather, and panel aggregation, or whether scaling/aggregation is suppressing the apparent full-sun value.
6. Align the visualization treatment with `CODEX-013` and `CODEX-016` so thresholds are readable against actual sun/solar fill.
7. Add a small smoke query or documented check comparing recent Tempest exterior lux, indoor lux, and target thresholds over the same daylight window.

**Deliverables:**

- Root-cause note for the high-looking lux target and the 45,000-lux exterior observation.
- Patched dashboard/query/labels if the issue is a calculation or unit problem.
- Screenshot or render evidence for the corrected lux panels.
- Smoke query or documented manual check for future daylight comparisons.

**Acceptance criteria:**

- Lighting panels clearly show how much sun is available relative to thresholds.
- Indoor lux, exterior Tempest lux, sun altitude, and thresholds are visually and semantically distinct.
- The lighting target is either corrected or documented as intentionally high with the relevant control rationale.
- Solar/lux visual styling matches the sitewide Grafana standards.

## Overall definition of done

The site is done when the live nav reflects the new Overview structure, visible copy no longer names Iris except for immutable historical IDs or documented legacy exceptions, the homepage is cleaner and includes cameras, Known Limits is gone, Planner Contract and AI Tunables are merged, major tables fit the center body, Planning Quality panels show data, public Grafana panels use one coherent chrome/color/band/relay style, lighting panels have consistent readable visual semantics backed by verified lux sources, Resource Use restores the useful individual solar-aligned panels without the combined rollup, Resource Use includes a readable six-month stacked monthly cost chart and distinct GPU power colors, Architecture is stripped back to current greenhouse architecture, Resource Use cost charts use one defensible electric/gas/water cost model, the electric meter/cost source is validated or explicitly labeled as partial, and Resource Use no longer implies impossible water bills.

[1]: https://lab.verdify.ai/ "Verdify: A Longmont, Colorado AI greenhouse with public telemetry - Verdify Lab"
[2]: https://lab.verdify.ai/start/ai-greenhouse "AI Greenhouse Control - Verdify Lab"
[3]: https://lab.verdify.ai/start/contact "Contact Verdify - Verdify Lab"
[4]: https://lab.verdify.ai/data/operations "Live Greenhouse Operations - Verdify Lab"
[5]: https://lab.verdify.ai/start/climate "High-Altitude Greenhouse Climate Control - Verdify Lab"
[6]: https://lab.verdify.ai/start/resource-use "Resource Use and Costs - Verdify Lab"
[7]: https://lab.verdify.ai/greenhouse "Inside the Automated Greenhouse - Verdify Lab"
[8]: https://lab.verdify.ai/reference/planner-contract "Planner Trigger and Publishing Contract - Verdify Lab"
[9]: https://github.com/jrvallery/verdify "GitHub - jrvallery/verdify: Verdify baseline scaffold"
[10]: https://lab.verdify.ai/data/planning-quality "AI Greenhouse Planning Quality - Verdify Lab"
