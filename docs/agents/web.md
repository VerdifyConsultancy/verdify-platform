# Agent: `web`

The FastAPI crop catalog, every vault markdown writer, every page generator, the
current Quartz production site, and the Astro replacement staged for
`lab.verdify.ai`.

## Owns

- `api/main.py` + sibling modules — FastAPI endpoints (crop catalog, health, observations, zones, setpoint echo)
- `scripts/generate-*.py` and public export generators — `generate-daily-plan.py`, `generate-forecast-page.py`, `generate-lessons-page.py`, `export-hourly-performance-dataset.py`, etc.
- `scripts/vault-*.py` — `vault-daily-writer.py`, `vault-crop-writer.py`
- `site/` — full Quartz source tree, docs, package lock, build config, and nginx config
- `site-astro/` — Astro compiler/runtime, shared-shell consumer, immutable
  snapshot/occurrence/release contracts, and parity/browser quality gates
- `deploy/k8s/components/lab-astro-stage/`,
  `deploy/k8s/components/lab-release-runtime/`, future reviewed reporting
  boundary components, and `deploy/k8s/overlays/lab-stage/` — isolated static
  canary plus dormant release path and reviewed Zot digest pins
- S3-backed lab content store — source/public/state for the k3s lab publisher
  (`deploy/k8s/components/lab-site/lab-publisher.yaml`)
- Legacy `/mnt/iris/verdify-vault/` paths — compatibility paths for generators;
  do not make new production dependencies on the NAS for lab publishing.

## Does not own

- Schemas the API returns or the frontmatter models (`verdify_schemas/api.py`, `vault.py` — coordinator)
- The DB the API reads (ingestor writes, coordinator migrates)
- The planner output that feeds daily plan pages (genai)

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `ingestor` | Needs a new DB column to render in a page | Ingestor adds the write path + coordinator adds migration; web consumes next cycle |
| `genai` | New plan section, new lesson category | Genai defines shape in `plan.py` / `lessons.py`; web renderer consumes through the schema |
| `coordinator` | Adding a response model or frontmatter schema | Coordinator merges `verdify_schemas/api.py` or `vault.py` change; web endpoint gets `response_model=` wiring after |

## Gates

- Vault writer changes must produce byte-for-byte identical frontmatter on existing files (`diff` against pre-regen file) — Obsidian dataview queries depend on key names and order.
- FastAPI endpoints must have `response_model=` declared (Sprint 22 pattern). OpenAPI `/docs` populates from these; regressions here mislead downstream consumers.
- Quartz production changes must build successfully (`make site-rebuild` or
  equivalent) before publishing. Astro changes must pass
  `cd site-astro && npm ci && npm test`; the test chain includes the fixture
  build, manifest/parity checks, and desktop/mobile Playwright quality gate.
- Run `make site-doctor` after Quartz/Grafana/content changes; it validates
  generated-page markers, image refs, live Grafana iframe panel IDs, built
  output, and nginx bind-mount readability. For content audits, add
  `--semantic-report <path>` to `scripts/site-doctor.py` to write the
  iframe-to-heading-to-live-panel-title inventory.
- A real Astro candidate must consume a closed sanitized snapshot and publish
  an exact zot digest. Stage acceptance proves 2/2 Ready replicas with zero
  restarts on distinct nodes, exact pod image IDs, `/healthz`, Pagefind,
  responsive media/lightbox, mobile navigation, route/alias and occurrence
  reconciliation at T0 and T+10, then restores the manual-sync posture.
  Occurrence-count reconciliation alone is not fallback parity: selected graph
  and camera evidence must be verified separately.
- Use `docs/site-content-map.md` as the route/content contract before reorganizing pages. It defines canonical route families, source type, data source, graph layer, and known gaps.
- Site markdown edits must respect generated-page ownership. Check the generator
  list below before hand-editing pages that will be synced into the S3 content
  prefix.
- Grafana iframe edits must be checked against live Grafana dashboard panel IDs; Quartz will happily build pages with stale `panelId=` values.

## Ask coordinator before

- Changing a vault frontmatter key (breaks Obsidian dataview silently)
- Adding an API endpoint (affects external consumers incl. Cloud Run api)
- Reworking the vault directory layout (site routing depends on it)
- Changing the public route/alias contract in either Quartz or Astro
- Any production cutover, public route change, Quartz retirement, or publisher
  decommission (Jason-gated)

## Site operations reference

`lab.verdify.ai` remains the Quartz production site. Its current serving path is:

`s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/content` → `verdify-lab-publisher` CronJob → `/work/content` → `npx quartz build` → `/work/public` → S3 public/state sync → `verdify-lab` nginx reads the lab cache PVC → Traefik → `lab.verdify.ai`.

Do not edit `/work/public` or `/srv/verdify/verdify-site/public`; they are build
output. Hand-authored content should be seeded/synced to the S3 content prefix.
Repo-owned Quartz/build code lives in `site/` and `scripts/`.

The current production Quartz image is a pre-ADR-0021 GHCR holdover. GHCR
publishing and `VerdifyConsultancy/verdify-site-legacy@v4` are not valid paths
for a new release. Do not edit `/srv/verdify/verdify-site/quartz` for normal
work; treat `/srv` paths as historical/break-glass context only.

`lab-stage.verdify.ai` is the isolated Astro canary. Its build path is the exact
`verdify-platform` revision through the in-cluster `verdify-platform-ci` /
`repo-build` WorkflowTemplate: the sanitized snapshot is hydrated and verified
before Kaniko, Kaniko pushes the image to the zot origin, and a reviewed digest
pin lands in `deploy/k8s/overlays/lab-stage/kustomization.yaml`. ArgoCD serves
the static nginx image with no PVC, runtime Secret, service-account token, DB,
Grafana, object-store access, or egress.

The latest accepted static digest is
`sha256:878c522740a44df44369dae1154b162b485a29d4b4b45d9ad48e20a44f22d56b`
(#530). On 2026-07-14 it was 2/2 Ready with zero restarts on distinct nodes and
passed identical T0/T+10 evidence for Pagefind, scrollability, media/lightbox,
mobile navigation, 323 routes, and 145 DOM occurrences. See
`docs/reviews/lab-astro-stage-acceptance-2026-07-14.md`. This remains a static
checkpoint: all 143 graph plus two camera occurrences lack a selected release
and materialized fallback. #533-#542 own the remaining Phase 4 path.

Current production Quartz build/publish unit:

- `verdify-lab-publisher` CronJob runs every 10 minutes in k3s. It calls
  `scripts/lab-publish-k3s.sh`, which syncs S3 content, runs
  `scripts/publish-site-content.sh`, builds Quartz, updates the cache PVC, and
  syncs generated content/public/state back to S3.

The current Astro stage has no publisher CronJob, mutable cache PVC, or runtime
content fetch. Phase 4 replaces the frozen build input with the reviewed
event-driven release/store path before any production cutover.

The source-only occurrence execution CLI is
`site-astro/scripts/execute-occurrence-export.mjs`. It requires the literal
`execute` command, canonical policy/manifest/batch/graph-result files, a local
sanitized-candidate root, an explicit store location, and a policy whose closed
activation record carries Jason's approval before store initialization. Its
presence is not a deployment or activation claim: no Lab workload invokes it,
and no endpoint, credential, environment binding, route, or replica is supplied
by the source adapter.

The separate built-site consumer command is
`site-astro/scripts/execute-occurrence-site-publish.mjs`. It accepts only the
literal `execute` command, canonical event/producer/policy/manifest documents,
and disjoint canonical candidate/workspace roots. It checks the exact document
identities and closed Jason approval record before asking a construction-only
runtime resolver. The returned build and verifier operations must both bind the
same digest-identified `https://lab-stage.verdify.ai` global-noindex profile;
the selected build record and verifier result must attest that profile before a
site release can publish. The command deliberately has no default runtime,
store, endpoint, credential, or network client, so this source slice cannot
publish or activate anything by itself.

PR #525 merged the source-only object-store/runtime prerequisite. The S3
binding accepts only the fixed
`https://s3-hdd.vallery.net` endpoint and `garage` region, requires the explicit
four-key client allowlist (`LAB_S3_ENDPOINT_URL`, `AWS_DEFAULT_REGION`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`), and uses no ambient SDK
credential chain. Local stores do not inspect environment data. Reader and
writer roles are enforced by the store primitive: `prepare` needs no store;
`status`, `bundle`, and `hydrate` use readers; and `publish` and `rollback` use
writers. The release reconciler gives its CLI child exactly the four S3 keys,
or an empty environment for a local store.

Main now provides a construction-only stage publisher factory. Both
occurrence and built-site locations must be explicit shared-S3 locations and
must match the event identities; build, verifier, and checkpoint operations
remain explicit caller dependencies with no defaults. The factory invokes no
operation and is not selected by a workload or by the executable's default
path. Its merge added no Secret values, endpoint probe, network call, replica,
egress, route, sync, activation, distributed lease, retention/GC, or credential
provisioning. #537/#540/#542 own those remaining source and proof steps.

`site-astro/Dockerfile.occurrence-exporter` packages the source-only
`occurrence-exporter` image. It derives from the locked dependency stage and
copies only two project-source files: an offline verifier and the shared
contract module imported by the real graph, camera, and joint producers. Its
default command reports `packaged` / `runtime-unbound`
after checking the 143-graph, two-camera, and joint runner contracts. The target
runs as `101:101`, grants no release/device/live authority, and bakes no policy,
runtime configuration, object-store endpoint, credential, or reporting feed.
Its required build revision and release instant are syntax-checked, recorded in
OCI revision/created labels, and repeated in canonical metadata that the
verifier validates and emits.
It is built and pinned through #532 as `a809d11c…de4a`, and the canonical
offline exact-source probe passed. It is still not a runnable graph renderer:
there is no producer binding or active workload, route, endpoint, credential,
or reporting feed. #535 owns that transition.

`scripts/rebuild-site.sh` builds Quartz into a staged `public.*` directory,
verifies the staged `index.html`, then rsyncs the complete staged output into
the live public tree with delayed deletes. In k3s, nginx reload is skipped
because the serving pod reads the PVC and has no Docker socket.

Use `docs/site-publishing-pipeline.md` for the current S3/k3s operator flow.
`make site-publish-status` is VM-era and useful only when intentionally
debugging the retired host path.

## Generated website pages

Treat these as generated or partially generated, not ordinary prose pages:

| Page(s) | Generator | Primary source data |
|---|---|---|
| `data/forecast/index.md` | `scripts/generate-forecast-page.py` | `weather_forecast`, `fn_forecast_correction`, `forecast_deviation_log` |
| `data/hourly-performance.md` | `scripts/export-hourly-performance-dataset.py` | Trailing 30-day hourly climate and equipment runtime exports |
| `plans/YYYY-MM-DD.md` | `scripts/generate-daily-plan.py` | `daily_summary`, `plan_journal`, setpoint/scorecard context |
| `plans/index.md` | `scripts/generate-plans-index.py` | `daily_summary`, `plan_journal` |
| `reference/ai-tunables.md` | `scripts/generate-ai-tunables-page.py` | `tunable_registry`, MCP contracts, `entity_map`, firmware source, `setpoint_plan`, `setpoint_changes`, `setpoint_snapshot`, `plan_journal` |
| `reference/lessons.md` | `scripts/generate-lessons-page.py` | `planner_lessons` |
| `greenhouse/zones/*.md` | `scripts/render-zone-pages.py` | `v_zone_full`, `v_position_current`, topology tables/views |
| `greenhouse/equipment.md` | `scripts/render-equipment-page.py` | `v_equipment_relay_map`, `equipment` |
| `greenhouse/crops/*.md` | `scripts/render-crop-profiles.py` | `crop_catalog`, `v_crop_catalog_with_profiles`, `v_position_current`, `v_crop_history` |

Vault writer scripts also maintain non-website Obsidian notes:

- `scripts/vault-daily-writer.py` → `/mnt/iris/verdify-vault/daily`
- `scripts/vault-crop-writer.py` → `/mnt/iris/verdify-vault/crops`

## Grafana website layer

Site markdown embeds Grafana with `https://graphs.verdify.ai/d-solo/{dashboard_uid}/?...&panelId=N`. Site dashboard JSON is in `/mnt/iris/verdify/grafana/dashboards`, while live Grafana also stores dashboards in its DB.

Quartz transforms those embeds into browser-neutral placeholders. The
`GrafanaEmbeds` component automatically loads the corresponding
`/render/d-solo/...` PNG in every browser; interactive Grafana is an explicit
action. The public `/render/` route passes through
`verdify-grafana-render-cache`, whose one-minute cache and background refresh
prevent each Chrome/Safari page view from launching a fresh headless browser.
Keep the image URLs stable and query-complete (dashboard, panel, range, theme,
variables, and dimensions all belong in the cache identity).

Use live Grafana API from the container to inspect dashboards:

```bash
docker exec verdify-grafana curl -sS http://localhost:3000/api/search?type=dash-db
docker exec verdify-grafana curl -sS http://localhost:3000/api/dashboards/uid/site-home
```

Use `make site-doctor` as the normal post-change gate. It queries the same API and fails on missing dashboards, stale `panelId=` values, missing images, missing generated-page markers, broken build output, or an unreadable `verdify-site` bind mount. Use `scripts/site-doctor.py --semantic-report /tmp/verdify-site-semantic.md` when reviewing copy/dashboard alignment; that report maps every iframe to its nearest Markdown heading and the live Grafana panel title.

`make site-doctor` also validates internal Markdown/HTML/wiki links against the source tree. It accepts both `/section/page` and `section/page` because the current vault uses both conventions, but missing target pages are errors.

For a full dashboard/panel audit, use:

```bash
scripts/audit-grafana.py --render all --render-workers 1 --render-timeout 75 --render-retries 5 --json-report /tmp/verdify-grafana-audit.json --markdown-report docs/grafana-panel-catalog.md
```

The catalog documents every live dashboard and panel, the story each panel tells, query-derived dependencies, freshness markers, render status, and style/accuracy notes. Use `--resume-json <prior-report>` after a throttled or interrupted pass; the renderer rate-limits concurrent full audits, so serial rendering is slower but reliable.

For website-facing Grafana work, HTTP 200/PNG is not enough. A panel can still be visually broken. Use `docs/grafana-website-visual-audit.md` as the current reference: the 2026-04-28 pass rendered the 164 unique website iframe PNGs, built contact sheets, and fixed blank stats, `No data` panels from schema/time-range drift, string stat rendering, forecast-bias misuse, mister-effectiveness drift, and the DIF data-outside-range issue.

Audit snapshot from 2026-04-27/28:

- 81 website markdown files under `/mnt/iris/verdify-vault/website` after backfilling canonical `/plans/YYYY-MM-DD` pages, archiving legacy `/evidence/plans`, adding `/evidence/planning-quality`, and replacing stale `/intelligence/lessons` with a redirect.
- 265 Grafana iframes across 34 pages after simplifying `/`, strengthening `/evidence`, and adding the Planning Quality evidence page.
- 19 dashboard UIDs embedded by the site.
- 55 live Grafana dashboards after archiving unused `site-evidence-compliance` and adding `site-evidence-planning-quality`.
- Full Grafana audit generated `docs/grafana-panel-catalog.md`: 904 live panels, all 904 rendered successfully after fixing `greenhouse-energy-cost` panel 924 and adding Planning Quality.
- Website visual audit generated `docs/grafana-website-visual-audit.md`: 164 unique website iframe PNGs were rendered and visually reviewed; broken-looking website panels were fixed in the site-facing dashboard JSON and specific iframe ranges.
- Initial audit found 75 iframe embeds referenced panel IDs missing from the current live dashboards. UIDs existed; `panelId` values drifted. The stale iframe IDs were repaired on 2026-04-27/28, then a semantic pass removed obvious duplicate/misleading embeds and `make site-doctor` passed with 0 findings.
- Cooling equipment proof is now on `site-climate-cooling` panel IDs `938` and `939`; soil-moisture-vs-VPD proof is now on `site-climate-water` panel ID `218`.
- Planning quality proof is now on `site-evidence-planning-quality` panel IDs `2`, `3`, `4`, `5`, `6`, `7`, `10`, `11`, `12`, `13`, `14`, `15`, `16`, `17`, `18`, and `19`; `/evidence/planning-quality` embeds every panel.
- First site simplification pass on 2026-04-28 rewrote the public entry path (`/`, `/evidence`, `/intelligence`, `/greenhouse`), fixed active source replacement-character corruption, archived stale hand-authored `/intelligence/lessons`, and hid generated/reference routes from primary Explorer navigation.
- Remaining audit findings are cleanup/documentation work, not render blockers: 101 panels have style notes, 125 panels have accuracy notes, daily/date panels often render at midnight timestamps, and several views/functions have no direct freshness marker.
- `/plans` is the canonical daily-plan archive. Former `/evidence/plans` source pages are archived outside the active website tree at `/mnt/iris/verdify-vault/archive/website-legacy-2026-04-28`.
- `verdify-grafana` had a stale bind mount for `/etc/grafana/provisioning`; the 2026-04-27/28 Grafana restart cleared it and provisioning files are readable inside the container.

## Recent arc (pre-agent-org)

- Sprint 20-era: Site relaunch, planning page, hydroponics page
- Sprint 22: 4 vault writers migrated to `verdify_schemas` models + yaml.safe_dump; 8 API endpoints gained `response_model=`

Use GitHub issues on `VerdifyConsultancy/verdify-platform` for next work; the
old `docs/backlog/web.md` file is archived in
`/Users/jason/Orbit/context_dump/verdify-platform/`.
