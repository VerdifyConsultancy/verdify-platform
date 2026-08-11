# Lab Astro Migration — Consolidated Program Tracker

Last updated: 2026-07-14 09:55 UTC. Phases 1-3 are PASSED; Phase 4 is
in progress. The static Astro canary is healthy, but occurrence activation
Pass 1 is **not repo-ready**: image packaging/pins and acceptance tooling are
complete, while four validated source lanes remain off main, one protected
store is externally delivered, and one protected reporting resource is
executor-delivered under validation. The current work register below is the
authoritative execution view.

Owner: platform agent (Claude outer loop plans/verifies; Codex executes on
xhigh). Direct-execution safeguards (prod sync, DNS/edge, Quartz retirement, credential
work). Epic: #351 (L9, G3). This file is the single source of truth for the
Quartz→Astro migration of lab.verdify.ai; update it whenever a phase gate
changes state.

## Ground truth (live and Git checked 2026-07-14 ~09:41 UTC)

| Surface | State |
|---|---|
| `lab.verdify.ai` (prod) | Quartz. `verdify-lab` Deployment in `verdify-prod` (ghcr image — pre-ADR-0021 holdover), `verdify-lab-publisher` CronJob republished every 10 min (mutable, shared RWO PVC, both replicas node-pinned). Healthy and fresh; had a 5-job BackoffLimitExceeded streak before recovering. |
| `lab-stage.verdify.ai` (canary) | Astro static Deployment `verdify-lab-astro-stage` in `verdify-platform`: 2/2 Ready, zero restarts, distinct nodes, exact image and pod image IDs `verdify-lab-astro@sha256:878c522740a44df44369dae1154b162b485a29d4b4b45d9ad48e20a44f22d56b` (#530). T0 04:06:45Z and T+10 04:17:47Z passed with identical stable evidence, including scroll, Pagefind, media, mobile navigation, and all 145 DOM occurrences. The output still has zero materialized occurrence blobs and no selected occurrence release. |
| `main` desired state | #525, #528, and #532 are merged. The overlay preserves static `878c522…d56b` and pins dormant release-agent `b9df7c23…c861`, release-nginx `88ba3cb8…8cb1`, and offline-contract occurrence-exporter `a809d11c…de4a`. The exporter is built and exact-source-probed, but it is not yet a runnable producer. |
| Live dormant runtime | `verdify-lab-release-runtime` exists at `replicas: 0`, is unrouted, and still shows the older live agent/nginx desired images because #532 has not been stage-synced. No runtime pod, endpoint, credential, or store is active. |
| In-cluster CI | Canonical exporter workflow `verdify-exporter-probe-projected-token-ql5cw` succeeded on node4 at 09:04:38Z after agents#3006 fixed the Argo projected-token boundary; agents#3002 is closed. Exact PR workflows for #532 (`verdify-platform-pr-ci-tljsz`) and #528 (`verdify-platform-pr-ci-74x44`) succeeded. The 13/13 browser suite includes the #504 scrollability regression. |

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
  58 release/unit + 7 parity tests, 13/13 fixture and 13/13 real-content
  browser gates, real build 152 sources → 323 routes, repo `make ci` green,
  in-cluster PR validation green.
- Route/alias parity: 240/240 canonical routes, 84/84 aliases, 15,450
  same-origin references verified.

## The nine surfaces (program decomposition)

Child issues (persisted 2026-07-13 after planning): 1→#474, 2→#475, 3→#476,
4→#477, 5→#478, 6→#479, 7→#480, 8→#481, 9→#482 (safety-checked cutover).

| # | Surface | Position | Remaining gap |
|---|---|---|---|
| 1 | Shared design contract | 1.1.0 merged and accepted on stage | Production remains Quartz until Phase 5 |
| 2 | Search/CSP/camera/contact | Pagefind/search, strict CSP, and contact behavior accepted on stage; privacy-validated bounded offline producers exist for both camera occurrences | No selected occurrence release, materialized same-origin fallback, freshness/LKG proof, or live producer; Cloudflare analytics remains diagnostic-only under the strict CSP |
| 3 | Graph fallbacks | 143/143 occurrences discovered and DOM-reconciled; complete manifest/feed-bound offline producer merged; interactive links preserved | No reporting-feed activation, occurrence-store selection/materialization, freshness/LKG proof, or live immutable fallback image |
| 4 | Media & lightbox | Responsive images, intrinsic sizing, and lightbox accepted on stage | Production remains Quartz until Phase 5 |
| 5 | Planner/evidence templates | Accepted on stage against the full frozen snapshot | Event-driven content refresh remains Phase 4b |
| 6 | Semantic parity | Same-snapshot schema-v2 diagnostic implemented; 240/240 canonical routes and 84/84 aliases compare from one frozen input, with SVG-title, feed/sitemap, heading-fragment, robots, and ambiguous sibling-link drift cleared | The diagnostic has 442 fully accounted findings: 429 gated graph-fallback findings, nine unavailable historical source references pending a regenerated snapshot, and two gated current-camera occurrences; final proof also requires an validation-eligible immutable snapshot |
| 7 | Immutable publishing | Local-filesystem CAS/release/cache engine, S3 conditional stores, typed occurrence store, closed caller, runtime injection (#525), exact runtime/exporter pins (#532), and live acceptance tooling (#528) are on main; the runtime remains disconnected at `replicas: 0` | #533/#537/#540/#542 own protected storage, deterministic packs, distributed coordination/GC, and executable publisher/runtime; no runtime pod is scheduled or routed |
| 8 | Quality gates | Strict 13/13 real-content gates green; exact tested static build passed live T0 and T+10 acceptance; #528 closes selected-release identity/path/completeness checks | Repeat the same gates for #541 Pass 1/Pass 2; no production acceptance yet |
| 9 | Production cutover | Fail-closed canary scaffold merged via #471 and remains inactive/source-only | Not built/pinned/deployed; Quartz remains authoritative; exact-revision actuator and rollback required |

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
VALIDATION: **PASSED 2026-07-13 ~06:55Z.** Stage serves `ee36941f` (Pagefind
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
#5 fields, native parent/blocker links, and explicit direct-execution safeguards; completed
stage surfaces #474/#477/#478/#481 are closed with durable evidence. Technical
issue decomposition and execution evidence are retained in issues #474-#482,
this plan, and the branch dispositions below. The doc-debt slice
from the 2026-07-13 staleness scan is complete in this Phase 3 change:
`docs/runbooks/k3s-operations.md` §2/§5.3 records the Zot/Kaniko
pipeline and current Lab blocker; `docs/agents/web.md` records Astro ownership,
deploy, acceptance, and stage state; and `LANES.md` carries a Lab-lane
staleness banner without rewriting its non-Lab historical plan.
VALIDATION: **PASSED 2026-07-13 ~07:54Z.** Board and tracker agreed, the issue
decomposition was complete, and every orphan branch had a recorded disposition.

Phase 4 — **Feature completion (owner: codex, critical-path order below).**
1. **4c Graphs/camera producer first:** build the isolated reporting/export
   tier, occurrence release producer, privacy-preserving camera sanitizer,
   allowlisting, freshness alerts, and LKG proof for all 143 graph plus two
   camera occurrences. The reporting tier must be anonymous-disabled and use
   a least-privilege read credential against a one-way, read-only public
   reporting feed—not the Track A Grafana datasource or controller database
   credential. The feed exposes only allowlisted public time-series/views and
   carries a source watermark: p95 lag at most 15 minutes and an alert at more
   than 30 minutes. Provisioning/changing that feed, tier, or credential is
   safety-checked; code and credential-name-only manifests may land first. The
   camera sanitizer separately performs GET-only reads from the exact validated
   public-edge allowlist
   `https://api.verdify.ai/api/v1/public/cameras/{greenhouse_1|greenhouse_2}/latest.jpg?h=1080`,
   with no Authorization header or cookie. It rejects redirects, strips
   metadata by bounded decode/re-encode, and can egress only to that origin and
   the occurrence store. Only the API owns its internal camera handoff; the
   sanitizer has no device-VLAN, Frigate/go2rtc, controller, DB, or general API
   access. Any future camera credential or source/allowlist change is also
   safety-checked.
   The inert trust-boundary slice (#483), two-camera offline producer (#487),
   and complete 143-graph offline producer (#492) are merged. They do not
   create a workload, activate a tier, read a credential, make a default live
   request, or mutate stage/prod. Issue #476 remains In Progress for the
   operator-owned reporting feed, occurrence-store delivery/selection,
   freshness alerts, LKG behavior, and joint live proof. Inert Phase 4b
   implementation may proceed, but live reporting or occurrence activation
   remains separately safety-checked.
   The current exact-source static/agent/nginx/exporter chain is built and
   pinned through #532. Canonical workflow
   `verdify-exporter-probe-projected-token-ql5cw` proved the exporter image
   contains the exact source and 143+2 offline contract. This proves packaging,
   not production: the exporter still has no runnable producer binding, and no
   reporting feed, endpoint, credential, producer request, route, or runtime
   activation occurred. #534/#535 own the reporting resource and executable.
2. **4a Release runtime:** `d91737d` landed via #473 with init hydration,
   atomic runtime, readiness, metrics, and tests. The follow-through makes the
   candidate visible in the Lab stage GitOps source only as a truly disconnected
   dormant workload: `replicas: 0`, exact source-bound agent/site image
   digests, no route, no object-store/AWS environment, and deny-all egress.
   Merging that source is not a live rollout: the Lab stage ArgoCD app remains
   manual-sync, and an operator sync is a separately recorded boundary. A later
   activation must apply the validated #532 runtime pins, add the #542
   store/egress/runtime contract, explicitly raise replicas, and pass #541
   stage acceptance.
3. **4b S3 backend and event wiring:** the inactive full-site S3
   conditional-store foundation merged through #496, #502 carries the distinct
   typed occurrence store, and #507 carries the closed 143+2 caller. The
   concrete source-only operation adapter maps that caller to explicit store
   APIs; its CLI requires the literal `execute` command, canonical validated
   policy, and an explicit store location before store initialization. No
   workload selects it and it carries no endpoint, credential, route, or
   activation. The source-only #501 binding-name contract fixes the
   future non-secret metadata ConfigMap and separate reader/writer Secret names,
   validates canonical key-name inventories without reading values, and makes no
   existence, endpoint, deployment, or authority claim. The source-only 4c
   caller and adapter supply a closed store-operation contract without endpoint,
   value, credential, deployment, or activation wiring. PR #525 merged the
   runtime prerequisite:
   fixed `https://s3-hdd.vallery.net`/`garage` client configuration; an explicit
   four-key S3 environment only; no environment read for local stores; code-level
   reader/writer enforcement; no-store `prepare`; reader `status`/`bundle`/
   `hydrate`; writer `publish`/`rollback`; and an exact four-key reconciler child
   allowlist. Its construction-only stage publisher factory requires explicit
   shared-S3 occurrence/site stores plus caller-supplied build, verifier, and
   checkpoint operations, with no operation defaults. It adds no Kubernetes
   manifest or Secret values, workload selection, endpoint probe, network call,
   replica, egress, route, sync, activation, distributed lease, retention/GC,
   or credential provisioning. #533 owns protected store delivery, #537 the
   deterministic packed format and budget proof, #540 distributed
   coordination/retention/GC and real-endpoint semantics, and #542 the
   executable publisher/two-pod runtime. S7 closes only after #541 proves the
   joint 143+2 rollout.
4. **4d Exact parity:** recreate the SVG-title comparator fix (+8 tests),
   rebuild Quartz+Astro from the same immutable snapshot, burn down real
   findings, and disposition the nine unavailable historical references. The
   exact frozen-input evidence and per-reference dispositions are recorded in
   `docs/reviews/lab-astro-same-snapshot-parity-2026-07-13.md`. Its
   code-level diagnostic now has 240/240 routes and 84/84 aliases with 442
   fully accounted findings: 429 graph-fallback findings, the nine frozen
   historical references, and two current-camera occurrences. Final live proof
   depends on 4c+4b materializing the same-snapshot fallbacks, a regenerated
   source snapshot reflecting the historical-reference dispositions, and an
   validation-eligible immutable attestation.
   Issue #479 is open for this final proof; the earlier automatic close
   did not satisfy it.

VALIDATION per implementation slice: committed change + `make ci` green +
in-cluster build green. That validation does not close S3/4c: S3 remains open
until the joint S3+S7 stage rollout serves and reconciles 143/143 graph and 2/2 validated camera
fallbacks at T0 and T+10, with no pending/invalid occurrences and with LKG and
freshness recovery proven. No reporting-tier activation, credential work,
production sync, DNS, or Quartz change occurs without its recorded direct-execution safeguard.

Phase 5 — **Production cutover (safety-checked).** The canary scaffold landed via
#471 but remains unbuilt, unpinned, undeployed, and unrouted. Build/pin/deploy
the dark canary only after its technical preconditions pass, run full acceptance
against prod, then perform the route cutover, Quartz retirement, publisher
decommission, and GHCR image retirement (ADR-0021 cleanup).
SAFEGUARD: use an explicit exact-target execution request; rollback is the route
flip back to Quartz, which stays warm until post-cutover verification passes.

## Branch dispositions (2026-07-13, per-branch triage vs origin/main)

LANDED — salvaged work:

- `coordinator/lab-production-canary-v2` (da26d54) — the fail-closed Astro
  production canary scaffold, merged onto main via PR #471 as `0285196` after
  in-cluster PR CI. It remains dormant Phase 5 input and is not built, pinned,
  deployed, or routed. It supersedes the unpushed local-main
  commit 03cff94 (verified patch-identical) and the canary copies buried in
  `web/lab-production-candidate`, `coordinator/lab-s3-release`, and
  `coordinator/lab-goal-completion`.
- `coordinator/lab-release-runtime` — ONLY home of d91737d, the immutable
  release-cache runtime (2-pod read-only cache candidate, init hydration +
  sidecar reconciliation, atomic nginx current/tree, readiness/metrics, 61
  unit tests). Only d91737d was cherry-picked onto main via PR #473 as
  `71530e1` after in-cluster PR CI; the five superseded sibling commits were
  omitted.
- `security/grafana-supported-images` — unrelated to lab but valuable:
  upgrades EOL grafana-oss 11.6.0 / renderer 3.12.6 to supported digest-pinned
  releases + Secret-sourced renderer token. Merged via PR #472 as `37561db`
  after in-cluster PR CI; prod Grafana Secret delivery and sync stay gated.
- `coordinator/lab-goal-completion` — one salvage beyond the canary: PR #462's
  merge dropped a release-store section from
  `docs/site-publishing-pipeline.md`; the exact section was restored via PR
  #470 as `e34cf1d` after in-cluster PR CI, and the source branch is deleted.

CLOSED REVIEW VEHICLE:

- `web/public-output-hls-ts` = PR #458 — **MERGED 2026-07-13 ~11:15Z** after
  outer-loop (Claude) review, verdict MERGE-AFTER-FIXES: HIGH fixes applied
  (drift guards repinned to the new contracts; guard-timeout clamp raised to
  30-600s/default 300s after the measured 125s tree exceeded the old 120s
  ceiling), and the historically-red in-cluster validator was root-caused
  with live pod logs to TWO environment mismatches — validate runs as root
  (CAP_DAC_OVERRIDE defeats chmod-0 "unreadable" fixtures) and under
  emissary umask 0000 (0777 fixture dirs correctly rejected by the
  fail-closed promoter) — both fixed test-only. First-ever green in-cluster
  validate (`verdify-platform-pr-ci-dgmff`), merged at head 6ff5b19 + branch
  deleted. MEDIUM/LOW review findings remain tracked as PR comments. The two
  subsumed branches (`web/public-output-guard`,
  `web/site-unification-integration`) are confirmed absent locally and on
  `origin`. Residue: two
  inert debug Workflows (`…debug458`, `…debug458b`) in agent-fleet-ci need a
  delete-rights cleanup.

DELETE — verified superseded (content demonstrably on main via #461/#462):

- **Deleted locally 2026-07-13 after remote-absence and salvage verification:**
  `web/lab-astro-stage`, `web/lab-parity-final` (landed verbatim as 87eb007),
  `web/lab-parity-completion`, `web/lab-design-completion` (tree-identical to
  #461 squash 7020834), `web/lab-runtime-completion`,
  `coordinator/lab-publishing-completion` (byte-identical release machinery),
  `web/lab-production-candidate` (canary superseded by canary-v2),
  `coordinator/lab-s3-release` (local-filesystem release/CAS content already on main; canary
  superseded), and `coordinator/lab-goal-completion` after PR #470 preserved its
  doc patch. These are the nine authorized deletions.
- **Confirmed absent:** `web/public-output-guard` +
  `web/site-unification-integration`, subsumed by merged PR #458.
- Local `main` in the shared root checkout: do NOT push, reset, rewrite, or
  otherwise disturb it; its extra commit is canary-v2 content and may coexist
  with unrelated operator state. Continue all program work from fresh
  worktrees rooted at `origin/main`. Retire the old local branch only through a
  separately validated, explicitly authorized non-destructive disposition once
  no worktree uses it.

CORRECTION (2026-07-13 ~10:05Z, codex direct audit supersedes the branch
triage's claim): main contains an abstract release store plus LOCAL
FILESYSTEM implementations only — there is NO S3 backend, no S3 occurrence
persistence, and no AWS SDK client on main. The S3/CAS primitives seen in
triage live on the (now-deleted) local branches' history and were never
merged. Phase 4b therefore includes IMPLEMENTING the S3 backend and its
conditional-write contract before any CLI/event wiring; endpoint and
credential checks stay name-only and activation-gated.

RESOLUTION (2026-07-13 ~14:56Z): #496 merged the inactive S3 full-site
conditional-store foundation and exact SDK dependency. Occurrence persistence,
CLI/caller selection, distributed coordination, retention/GC, endpoint proof,
credentials, and activation remain open and separately gated.

FOLLOW-THROUGH (source lifecycle): #502 added the typed S3 occurrence store and
#507 added the injected closed 143+2 caller. This change adds the concrete
operation adapter plus explicit execute CLI, with offline local/injected-S3
proof. These source artifacts do not bind a resource, endpoint, credential,
workload, route, replica, or environment; real-endpoint proof, a distributed
lease, bounded GC, event delivery, and any stage selection remain open gates.

PHASE 4B PREREQUISITE (PR #525, 2026-07-14): the runtime S3 injection fixes the
endpoint/region and explicit four-key client environment, enforces reader and
writer roles through the built-site CLI/reconciler, and provides a
construction-only shared-S3 stage runtime with explicit build, verifier, and
checkpoint dependencies. Its source gate does not prove an endpoint, build or
pin the next images, perform a network or cluster action, provision a
credential, or authorize activation.

## Current Phase 4 work register

The exact stage-only #476/#480 activation scope was recorded on 2026-07-14.
That validation does not convert incomplete source or protected delivery into a
passed gate. Every independently executable unit now has one issue with
What/Why/How/Test/Success and complete Project #5 fields:

| Issue | Work item | Current state | Blocks / evidence |
|---|---|---|---|
| #533 | Dedicated private stage store and separate reader/writer delivery | Backlog; protected operator delivery | Cross-linked to `jvallery/storage-infra#53`; blocks #540/#541; #537/#540 own capacity and endpoint integration |
| #534 | Anonymous-disabled reporting tier, one-way read-only feed, and separate credential | Backlog; `verdify-platform` executor under validation | Independent reporting-resource delivery; blocks #541 live proof, not inert #535 source |
| #535 | Executable 143+2 producer and dormant reporting GitOps | In Progress; validated partial substrate `e79a978`, no PR | Missing the executable, final exporter binding, required ConfigMaps, and camera/store egress; no source-start blocker |
| #537 | Deterministic occurrence/site packs and validated-budget proof | In Progress; validated source commit `f6348bf`, no PR | Reconstruct that commit only onto current main; never merge its inherited #540 ancestry; blocks #540/#542/#541 |
| #540 | Distributed S3 lease/fence, retention/GC, accounting, real-endpoint proof | In Progress; validated source commits through `d3ccea9`, no PR | Reconstruct on merged #537; blocked by #533/#537; blocks #542/#541 |
| #542 | Executable packed publisher and dormant two-pod runtime | In Progress; validated local head `448d556`, no PR | Blocked by #535/#537/#540; blocks #541 |
| #541 | Two-pass stage activation, 143+2/both-cache proof, route switch, T0/T+10, and 24h/96-sample soak | Backlog; no activation started | Blocked by #533/#534/#535/#537/#540/#542 |
| #536 | Public-output/media-contract follow-ups from #458 | Ready; non-critical path | Native child of #475 |

The local `phase4c/deterministic-release-packs@f6348bf` ref is a mixed review
vehicle, not a safe PR head: its ancestry contains the #540 source commits
`a288c40`, `9faead5`, `0b9473f`, `bae3bde`, `b632a3d`, `8228a94`, and
`d3ccea9`, followed by the single #537 commit `f6348bf`. Preserve the validated
dependency order by reconstructing/cherry-picking only `f6348bf` onto current
main for #537, keeping current main's live-acceptance package entry and
resolving only the packed-release additions in the publishing doc/package
manifest. Review and land #537 first. Then replay the seven listed #540
non-merge commits explicitly and in order on that merged base; exclude
historical merge commits `fd3b6ed` and `51e1828`, whose main-side content is
already present. Do not open or merge the mixed branch wholesale or use a
contiguous range that captures those merges. The implementation modules do not
overlap; rerun each issue's full tests after reconstruction before opening
either PR.

Completed prerequisites are #525 (runtime S3 injection), #527/#531 (exact
exporter packaging), #532 (dormant runtime/exporter pins), #528 (bounded live
143+2 verifier), agents#3001 (CronJob kind permission), and agents#3006
(canonical projected-token probe fix). agents#3002 is resolved and closed; no
node-placement workaround remains.

Pass 1 may begin only after the six blockers on #541 are green, validated source
and exact digest pins are on main, protected resources are delivered by name,
and real-endpoint lease/GC tests pass. For **each** pass, freeze and record one
immutable Platform commit and exact pin-set, the complete rendered resource
inventory/hash, the previous known-good Platform rollback revision and manual
rollback trigger, and the validated `jvallery/agents` actuator commit. Before
reconciliation, prove the live fleet AppProject admits every expected kind,
including `CronJob`. The fleet-owned Application must target only the frozen
commit, reach `Synced` + `Healthy`, and contain no resource outside the validated
inventory. After T+10, a separate validated fleet PR restores
`targetRevision: main`, autosync disabled, `prune:false`, and `selfHeal:false`;
this repeats the proven agents#2998/#2999 pattern and never syncs moving `main`
directly. Pass 1 activates the producer and two-pod runtime **unrouted**. Pass
2 changes the stage route only after exact 143+2 and both-cache convergence.
Production, DNS/edge, Quartz, devices, and Track A credentials remain out of
scope.

## WWW / Lab stack interface

The original unification goal is one presentation/build model, not one repo or
failure domain. The shared Verdify shell is merged in
`VerdifyConsultancy/verdify-www#33` and consumed by Lab surface #474.
`jvallery/agents#2907` is complete: WWW now builds in-cluster, publishes to
Zot, and reconciles through ArgoCD. The cross-program umbrella is
`jvallery/agents#2930`; this tracker and #351 own Lab execution. WWW and Lab
remain separate release artifacts, deployments, and repositories while sharing
Astro, the versioned shell, fonts/tokens/navigation, and the in-cluster
Kaniko/Zot/ArgoCD delivery model.

## Execution cadence

- Work the phase order above, land each change on `main`, and require `make ci`
  plus the relevant live probe before advancing.
- For production sync, DNS, device, or credential actions, use an explicit
  exact-target request, run the documented preflight, and stage rollback.
- Issues #474-#482 and #533-#542 retain the technical decomposition and current
  implementation facts; this plan and the issue records are authoritative.

## KPIs / done bar

- For each Lab-relevant main revision, in-cluster CI opens or updates its exact
  digest pin PR within 24 hours. Time from validated pin merge to stage sync is
  recorded separately as operator-dependent; every authorized stage rollout
  still receives T0/T+10 acceptance (pipeline health).
- 5/5 stage defects cleared; full acceptance green against deployed build.
- 143/143 graph and 2/2 privacy-validated camera occurrences with secure
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
  numeric budget requires explicit recorded task scope recorded on S7 before
  activation; no unbounded object-retention policy is accepted.
- Production on Astro, Quartz retired — with Jason APPLY recorded.
