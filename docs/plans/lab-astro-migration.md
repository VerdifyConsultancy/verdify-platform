# Lab Astro Migration — Consolidated Program Tracker

Last updated: 2026-07-13 (Phase 3 gate passed; Phase 4c PR #483 open).
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

Child issues (persisted 2026-07-13 post-consensus): 1→#474, 2→#475, 3→#476,
4→#477, 5→#478, 6→#479, 7→#480, 8→#481, 9→#482 (Jason-gated cutover).

| # | Surface | Position | Remaining gap |
|---|---|---|---|
| 1 | Shared design contract | 1.1.0 merged and accepted on stage | Production remains Quartz until Phase 5 |
| 2 | Search/CSP/camera/contact | Pagefind/search, strict CSP, and contact behavior accepted on stage | 2 camera occurrences have 0 verified same-origin fallbacks; carried to 4c. Cloudflare analytics remains diagnostic-only under the strict CSP |
| 3 | Graph fallbacks | 143/143 occurrences discovered and DOM-reconciled; interactive links preserved | No exporter, reporting tier, selected fallback release, materialized blob, or live immutable fallback image |
| 4 | Media & lightbox | Responsive images, intrinsic sizing, and lightbox accepted on stage | Production remains Quartz until Phase 5 |
| 5 | Planner/evidence templates | Accepted on stage against the full frozen snapshot | Event-driven content refresh remains Phase 4b |
| 6 | Semantic parity | Routes/aliases complete; comparator improved | Exact same-snapshot parity not green: ~240 false findings from SVG-`<title>` parser bug (fix exists uncommitted on a dead worktree — must be recreated), 9 unavailable historical refs (5 daily-plan routes, 4 images), and Quartz-vs-Astro must build from the SAME immutable snapshot |
| 7 | Immutable publishing | Local CAS/release/cache engine merged; isolated 4a runtime cherry-pick is PR #473 | No S3 adapter wiring (CLI s3:// unwired, no endpoint/credential probe, no event producer/release-agent workload) |
| 8 | Quality gates | Strict real-content gates green; exact tested build passed live T0 and T+10 acceptance | Keep the same budgets for every Phase 4 rollout; no production acceptance yet |
| 9 | Production cutover | Fail-closed canary scaffold is preserved in open PR #471 | Not on main/built/pinned/deployed; Quartz remains authoritative; Jason APPLY required |

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

Phase 3 — **Program formalization (owner: codex) — PASSED.** #351 is In
Progress/P1/XL. Issues #474-#482 carry What/Why/How/Test/Success, all Project
#5 fields, native parent/blocker links, and explicit human gates; completed
stage surfaces #474/#477/#478/#481 are closed with durable evidence. The
cross-model consensus report and append-only ledger are under
`.agent-workflow/consensus/`. Branch work is salvaged/parked per the
dispositions below. The doc-debt slice
from the 2026-07-13 staleness scan is complete in this Phase 3 change:
`docs/handoff/k3s-agent-handoff.md` §2/§5.3 now records the zot/Kaniko
pipeline and current Lab blocker; `docs/agents/web.md` records Astro ownership,
deploy, acceptance, and stage state; and `LANES.md` carries a Lab-lane
staleness banner without rewriting its non-Lab historical plan.
GATE: **PASSED 2026-07-13 ~07:54Z.** Board and tracker agree; two consecutive
unchanged `no-changes` turns from Claude and Codex, all six seat approvals,
and YES votes for all nine issues are recorded; no authorized orphan branch is
unaccounted.

Phase 4 — **Feature completion (owner: codex, critical-path order below).**
1. **4c Graphs/camera producer first:** build the isolated reporting/export
   tier, occurrence release producer, privacy-approved camera sanitizer,
   allowlisting, freshness alerts, and LKG proof for all 143 graph plus two
   camera occurrences. The reporting tier must be anonymous-disabled and use
   a least-privilege read credential against a one-way, read-only public
   reporting feed—not the Track A Grafana datasource or controller database
   credential. The feed exposes only allowlisted public time-series/views and
   carries a source watermark: p95 lag at most 15 minutes and an alert at more
   than 30 minutes. Provisioning/changing that feed, tier, or credential is
   Jason-gated; code and credential-name-only manifests may land first. The
   camera sanitizer separately performs GET-only reads from the exact approved
   public-edge allowlist
   `https://api.verdify.ai/api/v1/public/cameras/{greenhouse_1|greenhouse_2}/latest.jpg?h=1080`,
   with no Authorization header or cookie. It rejects redirects, strips
   metadata by bounded decode/re-encode, and can egress only to that origin and
   the occurrence store. Only the API owns its internal camera handoff; the
   sanitizer has no device-VLAN, Frigate/go2rtc, controller, DB, or general API
   access. Any future camera credential or source/allowlist change is also
   Jason-gated.
   The first inert, offline trust-boundary slice is open as PR #483 and refs
   #476. It does not create a workload, activate a tier, read a credential, or
   mutate stage/prod; #476 remains open for the operator-owned reporting feed,
   renderer/re-encoder, occurrence-store delivery, alerts, and joint live proof.
2. **4a Release runtime:** cherry-pick only `d91737d` onto main and land its
   init hydration, atomic runtime, readiness, and metrics with tests. This can
   proceed in parallel with the gated 4c reporting-tier activation.
3. **4b S3/event wiring:** wire the CLI, the 4c occurrence persistence caller,
   real-endpoint conditional-write proof (credential presence by name only),
   event-driven publishing, bounded retention/GC, and resource metrics. S3
   occurrence wiring and S7 closure are hard-gated on the 4c producer contract.
4. **4d Exact parity:** recreate the SVG-title comparator fix (+8 tests),
   rebuild Quartz+Astro from the same immutable snapshot, burn down real
   findings, and disposition the nine unavailable historical references. Its
   final live proof depends on 4c+4b materializing the same-snapshot fallbacks.

GATE per implementation slice: PR merged + `make ci` green + in-cluster build
green. That gate does not close S3/4c: S3 remains open until the joint S3+S7
stage rollout serves and reconciles 143/143 graph and 2/2 approved camera
fallbacks at T0 and T+10, with no pending/invalid occurrences and with LKG and
freshness recovery proven. No reporting-tier activation, credential work,
production sync, DNS, or Quartz change occurs without its recorded human gate.

Phase 5 — **Production cutover (Jason-gated).** Land canary scaffold
(`da26d54`) on main once Phase 2 is green, build/pin/deploy the dark canary,
full acceptance vs prod, then Jason APPLY: route cutover, Quartz retirement,
publisher decommission, ghcr image retirement (ADR-0021 cleanup).
GATE: Jason explicit approval; rollback = route flip back to Quartz (kept
warm until sign-off).

## Branch dispositions (2026-07-13, per-branch triage vs origin/main)

KEEP — real unmerged work:

- `coordinator/lab-production-canary-v2` (da26d54) — the fail-closed Astro
  production canary scaffold, salvaged onto current main as PR #471 (head
  27822e1; in-cluster PR CI green). Phase 5 input; leave open/unmerged until its
  gates. It supersedes the unpushed local-main
  commit 03cff94 (verified patch-identical) and the canary copies buried in
  `web/lab-production-candidate`, `coordinator/lab-s3-release`, and
  `coordinator/lab-goal-completion`.
- `coordinator/lab-release-runtime` — ONLY home of d91737d, the immutable
  release-cache runtime (2-pod read-only cache candidate, init hydration +
  sidecar reconciliation, atomic nginx current/tree, readiness/metrics, 61
  unit tests). Only d91737d was cherry-picked onto current main as PR #473
  (head 29edeb9; in-cluster PR CI green); the five superseded sibling commits
  were omitted.
- `security/grafana-supported-images` — unrelated to lab but valuable:
  upgrades EOL grafana-oss 11.6.0 / renderer 3.12.6 to supported digest-pinned
  releases + Secret-sourced renderer token. Salvaged as PR #472 (head 38536ec;
  in-cluster PR CI green); prod Grafana Secret delivery and sync stay gated.
- `coordinator/lab-goal-completion` — one salvage beyond the canary: PR #462's
  merge dropped a release-store section from
  `docs/site-publishing-pipeline.md`; the exact section is restored in PR #470
  (head 8846b00; in-cluster PR CI green), and the source branch is deleted.

OPEN PR — already the vehicle:

- `web/public-output-hls-ts` = PR #458 (fail-closed public-output guard stack
  + HLS/MP4 media validation, `verdify_public/` package, ~9k lines). It
  subsumes `web/public-output-guard` and `web/site-unification-integration`
  (same tip commit f17e30f). Current main was merged into its head as 9cb8bcb;
  the full local gate is green, but its clean in-cluster PR validator failed
  without retained pod logs and is under exact-environment reproduction. Do not
  request review or delete the two subsumed branches until that red gate is
  resolved.

DELETE — verified superseded (content demonstrably on main via #461/#462):

- **Deleted locally 2026-07-13 after remote-absence and salvage verification:**
  `web/lab-astro-stage`, `web/lab-parity-final` (landed verbatim as 87eb007),
  `web/lab-parity-completion`, `web/lab-design-completion` (tree-identical to
  #461 squash 7020834), `web/lab-runtime-completion`,
  `coordinator/lab-publishing-completion` (byte-identical release machinery),
  `web/lab-production-candidate` (canary superseded by canary-v2),
  `coordinator/lab-s3-release` (S3/CAS content already on main; canary
  superseded), and `coordinator/lab-goal-completion` after PR #470 preserved its
  doc patch. These are the nine authorized deletions.
- **Deferred:** `web/public-output-guard` +
  `web/site-unification-integration` are subsumed by PR #458 but stay until that
  PR is green and merged.
- Local `main` in the shared root checkout: do NOT push, reset, rewrite, or
  otherwise disturb it; its extra commit is canary-v2 content and may coexist
  with unrelated operator state. Continue all program work from fresh
  worktrees rooted at `origin/main`. Retire the old local branch only through a
  separately reviewed, explicitly authorized non-destructive disposition once
  no worktree uses it.

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
- Consensus ceremony: completed in Phase 3 at frozen commit `1e7dc6c`; the
  report and ledger are `.agent-workflow/consensus/lab-astro-migration.*`.
  Issues #474-#482 were persisted only after the full gate passed.

## KPIs / done bar

- For each Lab-relevant main revision, in-cluster CI opens or updates its exact
  digest pin PR within 24 hours. Time from reviewed pin merge to stage sync is
  recorded separately as operator-dependent; every authorized stage rollout
  still receives T0/T+10 acceptance (pipeline health).
- 5/5 stage defects cleared; full acceptance green against deployed build.
- 143/143 graph and 2/2 privacy-approved camera occurrences with secure
  immutable fallback live at T0 and T+10; S3 stays open until this joint S3+S7
  proof passes.
- The isolated public reporting feed has p95 source-watermark lag at most 15
  minutes over a rolling 24-hour window of at least 96 fifteen-minute samples
  from `verdify_lab_reporting_source_lag_seconds`. Lag above 30 minutes for two
  consecutive five-minute evaluations alerts; two evaluations below 15 minutes
  recover it. Alerted output retains LKG without being labelled fresh. The feed
  has no write path or controller credential into Track A.
- Exact same-snapshot parity findings = 0 (after parser fix + snapshot-locked
  rebuild), 9 historical refs dispositioned.
- S3-backed event-driven publish replaces the 10-min mutable publisher.
- Occurrence/release storage has measured bytes written/retained/deleted and
  request/egress counts; safe CAS-aware GC retains the current plus rollback
  generation and removes unreferenced immutable objects after a documented
  48-hour recovery window. Default pre-activation hard budgets are 10 GiB
  retained, 5 GiB written/day, 10 GiB egress/day, and 25,000 object requests/
  day; 80% alerts and 100% blocks publication while retaining LKG. A different
  numeric budget requires explicit Jason approval recorded on S7 before
  activation; no unbounded object-retention policy is accepted.
- Production on Astro, Quartz retired — with Jason APPLY recorded.
