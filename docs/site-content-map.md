# Verdify Site Content Map

This is the working contract for `lab.verdify.ai` content. The content source tree is `/mnt/iris/verdify-vault/website`; the repo-owned Quartz source tree is `site/`; Quartz builds into `/srv/verdify/verdify-site/public`.

Current active source size after the 2026-05-23 audit: 106 Markdown pages. Legacy `/greenhouse/lessons` and `/intelligence/lessons` are aliases for the generated `/reference/lessons` page. Legacy `/data/economics`, `/evidence/economics`, and `/economics` resolve to `/start/resource-use` after the resource/cost merge. Legacy `/evidence/baseline-vs-iris` resolves to `/data/baseline-vs-iris`. Legacy `/reference/context-window`, `/intelligence/context-window`, `/reference/intelligence`, and `/intelligence` resolve to `/reference/planning-loop` after the AI reference merge. Legacy `/greenhouse/growing`, `/crops`, and `/growing` resolve to `/greenhouse/crops` after the crop/growing merge. Legacy `/reference/build-notes` and `/intelligence/build-notes` resolve to `/reference/architecture` after the architecture/build-notes merge. Legacy `/reference/faq`, `/intelligence/faq`, and `/faq` resolve to `/start/ai-greenhouse` after the FAQ merge. Legacy `/greenhouse/cameras` and `/cameras` resolve to `/greenhouse` after the camera-context merge. Legacy `/reference/firmware-change-protocol`, `/intelligence/firmware-change-protocol`, `/firmware-change-protocol`, and `/fcp` resolve to `/reference/safety` after the firmware-protocol merge. Legacy `/greenhouse/operations`, `/greenhouse/how-the-greenhouse-operates`, `/greenhouse/how-it-works`, `/start/slack-ops`, `/slack`, and `/data/slack-ops` resolve to `/data/operations` after the operations merge. Legacy `/reference/known-limits`, `/forecast`, and `/plans` resolve to `/start/climate`, `/data/forecast`, and the noindex plan compatibility stub respectively; daily plan records remain at `/plans/YYYY-MM-DD`.

## Editorial Principle

The site story is: Verdify is a public AI-assisted greenhouse control loop in Longmont's high-elevation dry climate. Every page should support the same narrative: crop profiles define targets, Iris writes bounded tactical plans, the ESP32 owns deterministic relay control every 5 seconds, telemetry judges outcomes, and public scorecards make failures and lessons inspectable. Avoid framing Verdify as off-grid, fully autonomous, or self-improving unless the page immediately grounds the claim in the validated lesson lifecycle.

## Route Families

| Route | Role | Source type | Primary data/source | Graph layer | Update cadence |
|---|---|---|---|---|---|
| `/` | Public narrative entry point | Hand-authored | Current positioning, live overview panels | `site-home` | Manual |
| `/start/about` | Project context | Hand-authored | Greenhouse/project description | None | Manual |
| `/start/ai-greenhouse` | Primary AI greenhouse story | Hand-authored | Plain-English route into AI planning, safety, and proof pages | None | Manual |
| `/start/climate` | Climate system overview | Hand-authored | Greenhouse physics + live climate panels | `site-climate` | Manual copy, live graphs |
| `/start/evidence` | Evidence route map | Hand-authored | Canonical proof pages, generated archives, APIs, and public-safe exports | None | Manual |
| `/start/resource-use` | Resource use and cost proof | Hand-authored | Water, energy, gas, public rate assumptions, cost rollups, seasonal shape, lighting/resource timing, and solar alignment | `site-home`, `site-evidence-economics` | Manual copy, live graphs |
| `/start/contact` | Contact page | Hand-authored | Public inquiry form | None | Manual |
| `/data/operations` | Live operations, operating policy, and operator surface | Hand-authored | System health, active plan, alerts, controller state, lighting/wetting/irrigation/fertigation policy, and Slack operator briefs | `site-evidence-operations`, `greenhouse-equipment`, `site-climate` | Manual copy, live graphs |
| `/data/planning-quality` | Planner quality proof | Hand-authored | Planner score, compliance, lessons, forecast accuracy | `site-evidence-planning-quality` | Manual copy, live graphs |
| `/data/baseline-vs-iris` | Baseline comparison | Generated | `daily_summary`, `plan_journal`, `v_planner_performance` | None | Generated |
| `/data/plans` | Canonical daily plan archive index | Generated index | `daily_summary`, `plan_journal`, setpoint context | None | Generated |
| `/data/forecast` | Forecast page | Generated | `weather_forecast`, `fn_forecast_correction`, `forecast_deviation_log` | None | Every 30 minutes |
| `/data/hourly-performance` | Hourly performance CSV export | Generated | Trailing 30-day hourly climate and equipment-utilization CSVs | None | Generated daily |
| `/greenhouse` | Physical greenhouse overview and visual context | Hand-authored | Structure, zones, camera snapshots, equipment, crops | None | Manual |
| `/greenhouse/structure` | Physical shell reference | Hand-authored | Dimensions, glazing, light transmission | `site-climate-lighting` | Manual copy, live graphs |
| `/greenhouse/equipment` | Equipment inventory | Partially generated | `v_equipment_relay_map`, `equipment` | `site-greenhouse-equipment` | Generated sections |
| `/greenhouse/crops` | Crop/growing overview and crop-profile index | Hand-authored | Crop placement, live crop panels, active-control status, crop targets, and stress context | `site-greenhouse-crops` | Manual copy, live graphs |
| `/greenhouse/hydroponics` | Hydroponic system reference | Hand-authored | NFT system sensors and chemistry | `greenhouse-hydroponics` | Manual copy, live graphs |
| `/greenhouse/lighting` | Lighting/DLI reference | Hand-authored | DLI, grow-light behavior, solar context | `site-climate-lighting` | Manual copy, live graphs |
| `/greenhouse/soil` | Soil/substrate reference | Hand-authored | Root-zone probes and crop-steering gaps | None | Manual |
| `/greenhouse/crops/*` | Individual crop profiles | Hybrid: hand-authored narrative plus generated reference blocks | `crop_catalog`, crop profile views, current positions | `site-greenhouse-crops` | Generated blocks |
| `/greenhouse/zones/*` | Zone profiles | Hybrid: hand-authored narrative plus generated reference blocks, except `center.md` | `v_zone_full`, topology/position views | `site-greenhouse-zones`, crop panels | Generated blocks |
| `/reference/planning-loop` | Planning workflow, context, prompt, and live planner-state deep dive | Hand-authored | Planner health, active plan, context window, prompt family, MCP write path, dispatch, journaling, and learning loop | `site-intelligence`, `site-intelligence-planning` | Manual copy, live graphs |
| `/reference/planner-contract` | Planner trigger and publishing contract | Hand-authored | Trigger schedule, payload shape, accepted outputs, midnight review, automatic site publishing, and reliability checks | None | Manual |
| `/reference/ai-tunables` | AI tunable traceability reference | Generated | `tunable_registry`, MCP contracts, `entity_map`, firmware source, setpoint audit tables, plan rationales | None | Generated |
| `/reference/lessons` | Planner lesson library | Generated | `planner_lessons` | None | Generated |
| `/reference/data-model` | Data model reference | Hand-authored | Tables, views, functions, compression, retention, and evidence exports | None | Manual |
| `/reference/architecture` | System architecture and public-safe reference implementation | Hand-authored | Component map, data flow, reference hardware/runtime shape, database overview, example plan/scorecard JSON, and facts owned elsewhere | None | Manual copy |
| `/reference/safety` | Safety architecture and firmware change protocol | Hand-authored | ESP32/dispatcher/LLM control split, firmware review gates, OTA preflight, rollback, and command surface | None | Manual |
| `/reference/known-limits` | Known issues | Alias | Alias on `/start/climate` after the known-limits merge | None | Manual |
| `/reference/related-work` | Related work comparison | Hand-authored | Maker, research, and commercial greenhouse-control context | None | Manual |

## Current Source Inventory

This inventory makes the active source set explicit. At the 2026-05-23 audit, `make site-doctor` reports 106 Markdown sources; daily plan records run from 2026-03-24 through 2026-05-23.

| Source group | Active sources | Content owner |
|---|---|---|
| Root | `index.md` | Public narrative entry point and first proof links |
| Start | `start/about.md`, `start/ai-greenhouse.md`, `start/climate.md`, `start/contact.md`, `start/evidence.md`, `start/resource-use.md` | Project context, AI story plus FAQ, climate overview, contact, evidence map, and resource/cost proof |
| Data | `data/baseline-vs-iris.md`, `data/forecast/index.md`, `data/hourly-performance.md`, `data/operations.md`, `data/planning-quality.md`, `data/plans/index.md` | Generated proof pages, live operations, planner quality, public exports, and canonical plan index |
| Greenhouse overview and subsystems | `greenhouse/index.md`, `greenhouse/equipment.md`, `greenhouse/hydroponics.md`, `greenhouse/lighting.md`, `greenhouse/soil.md`, `greenhouse/structure.md` | Physical tour plus camera context, equipment inventory, hydroponics, lighting/DLI, soil probes, and structure |
| Crop profiles | `greenhouse/crops/index.md`, `greenhouse/crops/basil.md`, `greenhouse/crops/canna.md`, `greenhouse/crops/cucumbers.md`, `greenhouse/crops/herbs.md`, `greenhouse/crops/lettuce.md`, `greenhouse/crops/orchid.md`, `greenhouse/crops/peppers.md`, `greenhouse/crops/strawberries.md`, `greenhouse/crops/tomatoes.md` | Crop overview plus generated crop-specific target, placement, and status references |
| Zone profiles | `greenhouse/zones/index.md`, `greenhouse/zones/center.md`, `greenhouse/zones/east.md`, `greenhouse/zones/north.md`, `greenhouse/zones/south.md`, `greenhouse/zones/west.md` | Zone overview plus generated microclimate, equipment, water, sensor, and planting references |
| Reference | `reference/ai-tunables.md`, `reference/architecture.md`, `reference/data-model.md`, `reference/lessons.md`, `reference/planner-contract.md`, `reference/planning-loop.md`, `reference/related-work.md`, `reference/safety.md` | Tunable registry, implementation architecture, schema map, lessons, planner contract, planning loop, related work, and safety protocol |
| Compatibility generated index | `plans/index.md` | Noindex alias stub for `/plans` while dated child records remain under `plans/` |
| Daily plan records | `plans/2026-03-24.md` through `plans/2026-05-23.md` at audit time | Generated daily plan records and outcomes linked from `/data/plans` |

## Navigation Contract

The public navigation is a reader path, not a sitemap. Every publishable Markdown source in `/mnt/iris/verdify-vault/website` must be discoverable from the built `site-nav`, but generated records and reference children should usually be reached through their hub pages rather than listed directly in the sidebar. Quartz tag pages, `404`, and the `/plans` noindex compatibility stub are excluded from the discoverability requirement.

`make site-doctor` compares the source route inventory against links rendered in the built `site-nav` plus the first-hop links on those nav pages. It fails when a source route is not discoverable or when the nav contains a stale internal route.

The current navigation hierarchy is:

- Overview: Home, Verdify.ai business site, AI Greenhouse, Evidence Index, About, and Contact.
- Live Evidence: Operations, Climate, Planning Quality, Baseline vs Iris, Resource Use, and Forecast.
- Planner: latest generated daily plan, Planning Archive, Planning Loop, Planner Contract, AI Tunables, and Lessons.
- Greenhouse: greenhouse tour, equipment, structure, lighting, hydroponics, soil sensors, crop hub, and zone hub.
- Reference: safety, data model, architecture, known limits, related work, and the GitHub repository.

These routes stay public but should not appear directly in global nav:

- `/plans/index.md`, because it is a noindex compatibility stub for `/plans`.
- `/plans/YYYY-MM-DD`, because dated plan records are generated lab notebook entries owned by `/data/plans`.
- Individual crop pages, because they are children of `/greenhouse/crops`.
- Individual zone pages, because they are children of `/greenhouse/zones`.
- Utility export pages such as `/data/hourly-performance`, because they are data-download details linked from the evidence index.

## Source Rules

- `/data/plans` is the canonical public daily-plan index. The linked daily records live at `/plans/YYYY-MM-DD`, and `scripts/generate-daily-plan.py --backfill` must be able to regenerate every linked day page from DB state. `/plans` and `/plans/` remain a generated noindex compatibility stub because the daily-plan folder must keep serving child records without falling through to a Quartz folder listing.
- `/data/forecast` is the canonical generated forecast page. `/forecast` is a frontmatter alias on `/data/forecast`, not a second generated source page.
- Former `/evidence/plans` content is archived outside the active website tree at `/mnt/iris/verdify-vault/archive/website-legacy-2026-04-28/evidence/plans`. Do not restore it unless there is a deliberate redirect/history decision.
- Former `/greenhouse/lessons` and `/intelligence/lessons` narrative content is archived outside the active website tree at `/mnt/iris/verdify-vault/archive/website-simplification-2026-04-28/intelligence/lessons.md`. Use generated `/reference/lessons` as the source of truth; legacy routes remain aliases.
- Generated pages must carry an explicit source marker near the top. `make site-doctor` checks this for known generated routes.
- `make site-doctor` also flags Unicode replacement characters; these indicate source corruption, not just browser rendering issues.
- Every active source route must be discoverable from the public navigation. `make site-doctor` emits `nav-route-missing` when a source route is absent from both the built `site-nav` and the first-hop links on nav pages, and `nav-route-stale` when an internal nav link has no matching source page.
- Hand-authored pages may embed live Grafana panels, but the prose must describe the live panel that is actually embedded. Use `scripts/site-doctor.py --semantic-report /tmp/verdify-site-semantic.md` before large copy passes.
- `make site-doctor` fails when the same Grafana dashboard/panel ID appears on more than one non-plan page, when long public prose is repeated across pages, or when exact structure/safety facts appear outside their canonical owner pages.
- Dated "static snapshot" blocks are crawler receipts, not evergreen facts. `make site-doctor` fails when snapshot blocks such as "Static public API snapshot" or "Snapshot from the live database" age past one week.
- Public images live under `/mnt/iris/verdify-vault/website/static/photos`. Use `docs/site-image-audit.md` as the working image catalog. Do not publish old backups, camera snapshots, contact sheets, or generic plant photos as crop-specific proof.
- Do not use ASCII diagrams or raw Mermaid blocks on public pages. Use the repo-owned Quartz classes in `site/quartz/styles/custom.scss` instead: `flow-steps`, `system-map`, `metric-grid`, `timeline-row`, `score-split`, `floor-plan`, and `data-table`.
- Crop, zone, and equipment pages are hybrid pages. Their narrative sections are curated by hand, while explicit `data-auto-render` blocks are regenerated from DB-style source data. `scripts/render-crop-profiles.py`, `scripts/render-zone-pages.py`, and `scripts/render-equipment-page.py` update only marked blocks by default; use `--replace-page` only when the intent is to replace curated prose.
- Generated/reference blocks should render as `metric-grid`, `metric-card`, or `data-table` HTML components rather than raw Markdown tables. Forecast, daily plan, plans index, crop, and zone generators now emit these components directly.
- Crop pages include a generated `Latest Vision` block when `image_observations` has crop-linked camera analysis. The renderer copies selected snapshots into `/static/vision/` for public thumbnails.
- `docs/site-image-manifest.json` is the machine-readable public photo manifest. `make site-doctor` checks `/static/photos/` references against that manifest and fails on unapproved or out-of-place photo use.
- Do not edit `/srv/verdify/verdify-site/public`; it is build output.
- Do not edit `/srv/verdify/verdify-site/quartz` for normal CSS/component work; it is synced from repo-owned `site/quartz` during rebuild.
- Quartz emits most leaf pages as `route.html`, not `route/index.html`. `site/nginx.conf` must redirect slash-form leaf URLs such as `/greenhouse/zones/east/` to slashless `/greenhouse/zones/east`; serving `route.html` directly at the slash URL breaks Quartz's relative CSS/JS asset paths. Directory index routes such as `/greenhouse/` still serve `index.html`.

## Current Notes

- Cooling equipment proof is live on `site-climate-cooling` panel IDs `938` and `939`.
- Soil-moisture-vs-VPD proof is live on `site-climate-water` panel ID `218`.
- Public navigation is intentionally simplified into reader paths. Generated daily plans, crop profiles, zone profiles, compatibility stubs, and utility downloads stay public through their hub pages instead of appearing directly in global nav.
- The 2026-04-28 simplification pass rewrote `/`, `/evidence`, `/intelligence`, `/greenhouse`, `/greenhouse/growing`, and `/intelligence/broken`; fixed replacement-character corruption in active source; and added a redirect at `/intelligence/lessons/`.
- The 2026-04-28 generated-block cleanup converted active crop, zone, and equipment auto-render blocks from Markdown tables into web components. It also fixed truncated position schemes caused by literal pipe characters in shelf notation and added `site-doctor` guards for self-aliases, raw Mermaid blocks, box-drawing ASCII diagrams, stale Grafana panel IDs, and image placement.
- The unused live Grafana dashboard `site-evidence-compliance` was exported to `/mnt/iris/verdify/grafana/dashboards/archive/2026-04-28/site-evidence-compliance.json`, removed from provisioning, and no longer appears in Grafana search after the 2026-04-27/28 restart.
- The site now has source-level internal link validation, but it intentionally treats `section/page` and `/section/page` as valid root routes because existing content uses both conventions.
