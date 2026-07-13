# Lab Astro Migration — Consolidated Program Tracker

Last updated: 2026-07-13 (Phase 3 formalization; Phase 2 gate passed).
Owner: platform agent (Claude outer loop plans/verifies; Codex executes on
xhigh). Human gate: Jason (prod sync, DNS/edge, Quartz retirement, credential
work). Epic: #351 (L9, G3). This file is the single source of truth for the
Quartz→Astro migration of lab.verdify.ai; update it whenever a phase gate
changes state.

## Ground truth (verified live 2026-07-13 ~07:04 UTC)

| Surface | State |
|---|---|
| `lab.verdify.ai` (prod) | Quartz. `verdify-lab` Deployment in `verdify-prod` (ghcr image — pre-ADR-0021 holdover), `verdify-lab-publisher` CronJob republished every 10 min (mutable, shared RWO PVC, both replicas node-pinned). Healthy and fresh; had a 5-job BackoffLimitExceeded streak before recovering. |
| `lab-stage.verdify.ai` (canary) | Astro. `verdify-lab-astro-stage` in ns `verdify-platform`, 2/2 Ready with zero restarts on distinct nodes, exact image and pod image IDs `verdify-lab-astro@sha256:ee36941f20028fcfe06f12bf253e7139c00e3d5de1949eb8b12bb1d4ebe60b99` (pin PR #468), shell contract **1.1.0**, content frozen at the 2026-07-12 snapshot. Live T0/T+10 acceptance passed and the ArgoCD app returned to manual-sync. |
| `main` | Astro implementation through `38ccd5e` is deployed on stage: PRs #459/#461/#462 plus Pagefind fix #466, quality-gate retry #467, and verdify-www#33 shell 1.1.0. Phase 4 release/store, graph/camera fallback, exact-parity, and production-canary work remain. |
| In-cluster CI | **Green for lab-astro.** `verdify-platform-ci-phase1-38ccd5e` built exact revision `38ccd5e`, passed all 12 browser quality tests plus build/verify/probe/pin, and published the accepted zot digest above. Playwright fixes #463/#464 and agents#2969/#2970 closed the missing-browser, pin-idempotency, and deterministic CPU-allocation failures without relaxing the strict quality budgets. |

## Completed (do not re-plan)

- Shared design shell 1.1.0 (verdify-www#33): typography/tokens/header/footer/
  nav/breadcrumbs, checksum-bound contract.
- Astro stage infra (#459/#460): isolated canary at lab-stage.verdify.ai —
  2/2 replicas anti-affine, no PVC, deny-all egress, non-root read-only,
  PDB, global noindex. 404 problem resolved.
- Main Astro implementation (#461/#462): shell 1.1.0 integration, light Lab
  layout, responsive/mobile nav, Pagefind + corrected CSP, fail-closed
  same-origin camera, contact form, galleries/lightbox/srcset with intrinsic
  dims, planner/forecast/archive/evidence/contact templates, route/alias/RSS/
  social preservation, authored-content parity comparator, immutable local
  release manifests + rollback + two-generation cache, quality gates. Proof:
  58 release/unit + 7 parity tests, 12/12 fixture and 12/12 real-content
  browser gates, real build 152 sources → 323 routes, repo `make ci` green,
  in-cluster PR validation green.
- Route/alias parity: 240/240 canonical routes, 84/84 aliases, 15,450
  same-origin references verified.

## The nine surfaces (program decomposition)

| # | Surface | Position | Remaining gap |
|---|---|---|---|
| 1 | Shared design contract | 1.1.0 merged and accepted on stage | Production remains Quartz until Phase 5 |
| 2 | Search/CSP/camera/contact | Pagefind/search, strict CSP, and contact behavior accepted on stage | 2 camera occurrences have 0 verified same-origin fallbacks; carried to 4c. Cloudflare analytics remains diagnostic-only under the strict CSP |
| 3 | Graph fallbacks | 143/143 occurrences discovered and DOM-reconciled; interactive links preserved | No exporter, reporting tier, selected fallback release, materialized blob, or live immutable fallback image |
| 4 | Media & lightbox | Responsive images, intrinsic sizing, and lightbox accepted on stage | Production remains Quartz until Phase 5 |
| 5 | Planner/evidence templates | Accepted on stage against the full frozen snapshot | Event-driven content refresh remains Phase 4b |
| 6 | Semantic parity | Routes/aliases complete; comparator improved | Exact same-snapshot parity not green: ~240 false findings from SVG-`<title>` parser bug (fix exists uncommitted on a dead worktree — must be recreated), 9 unavailable historical refs (5 daily-plan routes, 4 images), and Quartz-vs-Astro must build from the SAME immutable snapshot |
| 7 | Immutable publishing | Local CAS/release/cache engine merged | No S3 adapter wiring (CLI s3:// unwired, no endpoint/credential probe, no event producer/release-agent workload); release runtime on unmerged branch `coordinator/lab-release-runtime` (d91737d: hydration init/sidecar, atomic nginx current/tree, readiness/metrics, 61 tests passed) |
| 8 | Quality gates | Strict real-content gates green; exact tested build passed live T0 and T+10 acceptance | Keep the same budgets for every Phase 4 rollout; no production acceptance yet |
| 9 | Production cutover | Fail-closed canary scaffold on `coordinator/lab-production-canary-v2` | Not on main/built/pinned/deployed; Quartz remains authoritative; Jason APPLY required |

## Execution phases and gates

Phase 1 — **Unblock the pipeline (P0, owner: codex) — PASSED.**
**Historical entry state and root cause:** confirmed by local repro and
2026-07-13 forensics, the
`build-lab-astro` test initContainer (`node:22-bookworm` in WorkflowTemplate
`verdify-platform-ci` → `repo-build/build`, defined in jvallery/agents
`platform/kubernetes/ci/agent-fleet-ci/workflows/`) never installs Playwright
browsers; PR #461 added `test:quality:built` (Playwright `@quality`) to
`npm test`, so every stage/build push since `7020834` fails at
`chromium.launch()` ("Executable doesn't exist … run npx playwright
install"). PR CI stays green because it runs zero site-astro steps (parity
gap), and the authoring pod had a pre-seeded `~/.cache/ms-playwright`.
The fix options were: (a) repo-local — make site-astro's test chain provision its
browser (e.g. `npx playwright install --with-deps chromium` before the
quality gate, or gate on browser availability); (b) control-plane — patch the
WFT test command or bake a pinned playwright test image (jvallery/agents PR).
The recovery plan was to re-trigger CI for main HEAD and let the chain open the
`ci/lab-stage-pin-*` PR (pin target:
`deploy/k8s/overlays/lab-stage/kustomization.yaml`, then at `2c03489c`),
gate-review, roll stage.
Pipeline debt recorded at entry in the same sweep (file follow-ups, coordinate for
jvallery/agents changes): `open-lab-stage-pin-pr` is non-idempotent
(non-force push + late curl/jq install — stranded PR-less branch
`ci/lab-stage-pin-aa99b5a9a0be` holds digest `df8a3279` for aa99b5a; delete
after a fresh pin lands); podGC `OnPodCompletion` destroys failure logs;
`pin-digests` "main: Error" races in older runs; and `build-api` Kaniko
failure on run `c7b2b` (rev `c1a7fff`) is SEPARATE and currently blocks prod
digest pins for the firmware-OTA commit — diagnose alongside.
GATE: **PASSED 2026-07-13 ~05:35Z.** Fix chain: PR #463 (Playwright browser
provisioning) + PR #464 (exported Playwright CLI resolution) + jvallery/
agents#2967 (podGC OnPodSuccess — failed CI pods now keep logs 48h); run
`verdify-platform-ci-mcdlh` went green through build/verify/probe; pin step
failed late as predicted (branch-without-PR trap) and was hand-opened/merged
as PR #465. Stage `verdify-lab-astro-stage` serves
`fb72e1bad7c6…` 2/2 ready; outer-loop live probes confirm shell 1.1.0
markers, `wasm-unsafe-eval` CSP, lab-nav toggle, lightbox markup, intrinsic
dims + srcset, /pagefind assets 200.

Phase 2 — **Stage convergence verification (owner: codex, verified by outer
loop) — PASSED.** **Historical entry checklist and pre-rollout evidence:**
confirm on live stage that Pagefind search works (the 2026-07-13 probe
confirmed all /pagefind assets serve 200 with 321 pages indexed — ONLY the
missing `wasm-unsafe-eval` in script-src blocks it), imgs have intrinsic
dims + srcset, lightbox works, mobile Lab-index toggle present (the shell's
own hamburger already works — only the sidebar toggle is missing), shell
1.1.0. CAMERA CORRECTION vs the 03:26 audit: the claimed CORS/private-address
failure was REFUTED live — current stage cameras hotlink
`api.verdify.ai/.../latest.jpg` via plain `<img>` and work through the public
Cloudflare edge (the "private address" was this pod's split-horizon DNS).
The REAL camera item: origin/main's new CSP drops api.verdify.ai from
img-src in favor of compiled same-origin snapshots
(`static/cameras/<id>/latest.jpg`) — verify the snapshot path actually
populates post-deploy, or camera images regress. Decide
Cloudflare-analytics-vs-CSP policy explicitly.
GATE: **PASSED 2026-07-13 ~06:55Z.** Stage serves `ee36941f` (Pagefind
noWorker fix #466 + retry #467, pin #468; a stale-Cloudflare-cache worker-CSP
interaction found and designed around). Full acceptance on the deployed
build: search/lightbox/images/mobile-nav pass; 323 routes + 145 graph/camera
DOM occurrences reconcile; T+10 durability pass green; manual-sync posture
restored (agents#2972) after temporary exact-revision autosync. CI hardening
landed en route: agents#2969 (pin idempotency), agents#2970 (test-container
CPU 2/3 — fixed the deterministic home long-task budget miss). Camera: 2
occurrences, 0 verified same-origin fallbacks — carried to Phase 4c
(occurrence exporter/store), NOT claimed as parity. Cloudflare-analytics CSP
blocks: diagnostic-only; strict CSP retained.

Phase 3 — **Program formalization (owner: codex).** #351 → In Progress/P1/XL;
create the nine surface child issues (What/Why/How/Test/Success + Project #5
fields); salvage/park branch work per dispositions below. The doc-debt slice
from the 2026-07-13 staleness scan is complete in this Phase 3 change:
`docs/handoff/k3s-agent-handoff.md` §2/§5.3 now records the zot/Kaniko
pipeline and current Lab blocker; `docs/agents/web.md` records Astro ownership,
deploy, acceptance, and stage state; and `LANES.md` carries a Lab-lane
staleness banner without rewriting its non-Lab historical plan.
GATE: board reflects this tracker; no orphan branches unaccounted.

Phase 4 — **Feature completion (owner: codex, sequenced).**
4a Release runtime: recreate/rebase `coordinator/lab-release-runtime` onto
   main, PR with tests.
4b S3 backend: wire CLI/occurrence persistence, real-endpoint conditional-write
   proof (credential presence by name only — coordination row exists),
   event-driven publishing replacing the frozen snapshot.
4c Graphs: isolated reporting tier (anonymous-disabled, least-privilege
   read credential — NOT the Track A primary datasource), exporter over all
   143 occurrences, allowlisting, freshness alerts, LKG proof.
4d Exact parity: recreate the SVG-title comparator fix (+8 tests), rebuild
   Quartz+Astro from the same immutable snapshot, burn down real findings;
   disposition the 9 unavailable historical references.
GATE per item: PR merged + `make ci` green + in-cluster build green.

Phase 5 — **Production cutover (Jason-gated).** Land canary scaffold
(`da26d54`) on main once Phase 2 is green, build/pin/deploy the dark canary,
full acceptance vs prod, then Jason APPLY: route cutover, Quartz retirement,
publisher decommission, ghcr image retirement (ADR-0021 cleanup).
GATE: Jason explicit approval; rollback = route flip back to Quartz (kept
warm until sign-off).

## Branch dispositions (2026-07-13, per-branch triage vs origin/main)

KEEP — real unmerged work:

- `coordinator/lab-production-canary-v2` (da26d54) — the fail-closed Astro
  production canary scaffold, already rebased onto post-#462 main. Phase 5
  input; open PR once Phase 2 is green. Supersedes the unpushed local-main
  commit 03cff94 (verified patch-identical) and the canary copies buried in
  `web/lab-production-candidate`, `coordinator/lab-s3-release`, and
  `coordinator/lab-goal-completion`.
- `coordinator/lab-release-runtime` — ONLY home of d91737d, the immutable
  release-cache runtime (2-pod read-only cache candidate, init hydration +
  sidecar reconciliation, atomic nginx current/tree, readiness/metrics, 61
  unit tests). Phase 4a: cherry-pick d91737d onto current main (drop the five
  superseded sibling commits), land via PR.
- `security/grafana-supported-images` — unrelated to lab but valuable:
  upgrades EOL grafana-oss 11.6.0 / renderer 3.12.6 to supported digest-pinned
  releases + Secret-sourced renderer token. Open PR on its own lane (prod
  grafana sync stays gated).
- `coordinator/lab-goal-completion` — one salvage beyond the canary: PR #462's
  merge dropped a release-store section from
  `docs/site-publishing-pipeline.md`; restore via small docs PR. Then delete.

OPEN PR — already the vehicle:

- `web/public-output-hls-ts` = PR #458 (fail-closed public-output guard stack
  + HLS/MP4 media validation, `verdify_public/` package, ~9k lines). It
  subsumes `web/public-output-guard` and `web/site-unification-integration`
  (same tip commit f17e30f). Drive #458 to review/merge; then delete the two
  subsumed branches.

DELETE — verified superseded (content demonstrably on main via #461/#462):

- `web/lab-astro-stage`, `web/lab-parity-final` (landed verbatim as 87eb007),
  `web/lab-parity-completion`, `web/lab-design-completion` (tree-identical to
  #461 squash 7020834), `web/lab-runtime-completion`,
  `coordinator/lab-publishing-completion` (byte-identical release machinery),
  `web/lab-production-candidate` (canary superseded by canary-v2),
  `coordinator/lab-s3-release` (S3/CAS content already on main; canary
  superseded), `web/public-output-guard` + `web/site-unification-integration`
  (subsumed by PR #458 — delete after it merges).
- Local `main` in the pod checkout: do NOT push; its extra commit is
  canary-v2 content. Reset to origin/main at next attended opportunity.

Finding that improves on the 03:26 audit: the S3 CAS primitives (immutable
puts, ETag CAS, pagination, distributed lease, safe GC) are ALREADY ON MAIN —
what remains for Phase 4b is wiring (CLI s3:// construction, occurrence
persistence caller, real endpoint/credential probe, publisher/event
workload), not the backend itself.

## Operating cadence

- Outer loop (Claude): 5-minute drive loop — check codex progress, unblock,
  verify each gate with live probes, keep this tracker + #351 current,
  surface Jason-gated asks in `COORDINATION_REQUESTS.md`.
- Executor (codex, xhigh): works the phase order above; every change lands
  via `main` with `make ci` green; no prod sync/DNS/device/credential action
  without Jason.
- Consensus ceremony: the full consensus-review ratification codex queued is
  deferred until after Phase 1-2 (delivery bottleneck first); run it before
  Phase 5 issue finalization.

## KPIs / done bar

- Stage serves latest main digest ≤24h after merge (pipeline health).
- 5/5 stage defects cleared; full acceptance green against deployed build.
- 143/143 graph occurrences with secure immutable fallback live.
- Exact same-snapshot parity findings = 0 (after parser fix + snapshot-locked
  rebuild), 9 historical refs dispositioned.
- S3-backed event-driven publish replaces the 10-min mutable publisher.
- Production on Astro, Quartz retired — with Jason APPLY recorded.
