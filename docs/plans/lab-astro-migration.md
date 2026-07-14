# Lab Astro Migration — Consolidated Program Tracker

Last updated: 2026-07-14. The latest recorded stage rollout serves `814eafff`
(P0 scroll-fix PR #504/pin #505 — user-reported viewport scroll-lock from body-level
`overflow:clip`, fixed + regression-tested; roll also shipped #487/#496/#501/
#502 content. Phases 1-3 PASSED; Phase 4 in flight.)
build/probe/pin gates passed; Phase 4b S3 foundation merged; occurrence
adapter is carried by #502; the source-only binding-name readiness contract is
carried by #501. The occurrence-export caller is merged through #507. The
concrete operation adapter and explicit execute CLI in this source tree remain
caller-disabled without an approved policy and explicit store location; they
make no endpoint, value, credential, deployment, or activation claim.
Phase 4b PR #525 carries strict runtime S3 dependency injection. It is a
source-only prerequisite and does not change the live stage or satisfy a
build, pin, rollout, or activation gate.
The source tree also defines an offline-default `occurrence-exporter` image target;
it has no renderer/runtime binding, workload, digest, or pin and is not part of
the latest completed image chain.
Owner: platform agent (Claude outer loop plans/verifies; Codex executes on
xhigh). Human gate: Jason (prod sync, DNS/edge, Quartz retirement, credential
work). Epic: #351 (L9, G3). This file is the single source of truth for the
Quartz→Astro migration of lab.verdify.ai; update it whenever a phase gate
changes state.

## Ground truth (stage verified live ~07:04 UTC; repo/CI refreshed ~16:15 UTC)

| Surface | State |
|---|---|
| `lab.verdify.ai` (prod) | Quartz. `verdify-lab` Deployment in `verdify-prod` (ghcr image — pre-ADR-0021 holdover), `verdify-lab-publisher` CronJob republished every 10 min (mutable, shared RWO PVC, both replicas node-pinned). Healthy and fresh; had a 5-job BackoffLimitExceeded streak before recovering. |
| `lab-stage.verdify.ai` (canary) | Astro. `verdify-lab-astro-stage` in ns `verdify-platform`, 2/2 Ready with zero restarts on distinct nodes, exact image and pod image IDs `verdify-lab-astro@sha256:ee36941f20028fcfe06f12bf253e7139c00e3d5de1949eb8b12bb1d4ebe60b99` (pin PR #468), shell contract **1.1.0**, content frozen at the 2026-07-12 snapshot. Live T0/T+10 acceptance passed and the ArgoCD app returned to manual-sync. |
| `main` | Astro source includes the accepted static implementation, dormant release/cache runtime, offline camera producer (#487), complete offline 143-graph producer (#492), inactive S3 conditional-store foundation (#496), typed occurrence store (#502), and the closed 143+2 caller (#507). The concrete store-operation adapter and execute CLI in this source tree require an explicit store plus canonical Jason-approved policy before initialization. Runtime pin #500 binds the dormant agent/nginx pair to the latest completed relevant source chain; later source changes are not automatically staged. A fourth source-only `occurrence-exporter` Docker target packages the producer contracts behind an offline verifier, but has no renderer/runtime binding, workload, built digest, or pin. The source-only name-readiness contract is carried by #501, not yet a deployed binding. The live stage remains the static image above; endpoint binding, event delivery, exact parity, and production cutover remain. |
| Phase 4b PR #525 | Strict source-only S3 injection fixes endpoint `https://s3-hdd.vallery.net` and region `garage`, enforces reader/writer roles, and adds explicit CLI/reconciler and construction-only stage-publisher bindings. It selects no workload and authorizes no activation. |
| In-cluster CI | **Green for the latest completed Lab source/pin chain.** Workflow `verdify-platform-ci-crv96` completed 17/17 Lab Pod gates for `6729cc2...`: 12 browser quality tests, static/agent/nginx builds, metadata hydration, exact static probe, and paired runtime probe. Pin #500 passed exact-head PR CI/render/kubeconform and its digest-only follow-up workflow passed. Later exact-main workflows do not authorize stage sync or runtime activation. Earlier Playwright fixes #463/#464 and agents#2969/#2970 remain enforced without relaxing quality budgets. |

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
| 2 | Search/CSP/camera/contact | Pagefind/search, strict CSP, and contact behavior accepted on stage; privacy-approved bounded offline producers exist for both camera occurrences | No selected occurrence release, materialized same-origin fallback, freshness/LKG proof, or live producer; Cloudflare analytics remains diagnostic-only under the strict CSP |
| 3 | Graph fallbacks | 143/143 occurrences discovered and DOM-reconciled; complete manifest/feed-bound offline producer merged; interactive links preserved | No reporting-feed activation, occurrence-store selection/materialization, freshness/LKG proof, or live immutable fallback image |
| 4 | Media & lightbox | Responsive images, intrinsic sizing, and lightbox accepted on stage | Production remains Quartz until Phase 5 |
| 5 | Planner/evidence templates | Accepted on stage against the full frozen snapshot | Event-driven content refresh remains Phase 4b |
| 6 | Semantic parity | Same-snapshot schema-v2 diagnostic implemented; 240/240 canonical routes and 84/84 aliases compare from one frozen input, with SVG-title, feed/sitemap, heading-fragment, robots, and ambiguous sibling-link drift cleared | The diagnostic has 442 fully accounted findings: 429 gated graph-fallback findings, nine unavailable historical source references pending a regenerated snapshot, and two gated current-camera occurrences; final proof also requires an approval-eligible immutable snapshot |
| 7 | Immutable publishing | Local-filesystem CAS/release/cache engine, inactive S3 conditional-store foundation (#496), typed occurrence store (#502), closed caller (#507), and source-only concrete operation adapter/explicit CLI are present; 4a runtime is source-visible with exact source-bound images as a disconnected `replicas: 0` workload; the source-only #501 readiness contract fixes future reader/writer Secret resource names plus non-secret metadata ConfigMap/key names without inspecting values | Actual resource/value binding, real-endpoint proof, distributed coordination, retention/GC, event producer, and stage activation remain; no runtime pod is scheduled or routed |
| 8 | Quality gates | Strict real-content gates green; exact tested build passed live T0 and T+10 acceptance | Keep the same budgets for every Phase 4 rollout; no production acceptance yet |
| 9 | Production cutover | Fail-closed canary scaffold merged via #471 and remains inactive/source-only | Not built/pinned/deployed; Quartz remains authoritative; Jason APPLY required |

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
   The inert trust-boundary slice (#483), two-camera offline producer (#487),
   and complete 143-graph offline producer (#492) are merged. They do not
   create a workload, activate a tier, read a credential, make a default live
   request, or mutate stage/prod. Issue #476 remains In Progress for the
   operator-owned reporting feed, occurrence-store delivery/selection,
   freshness alerts, LKG behavior, and joint live proof. Inert Phase 4b
   implementation may proceed, but live reporting or occurrence activation
   remains separately Jason-gated.
   Their exact-source static/agent/nginx build, metadata, static-image, paired
   runtime, and latest completed dormant digest-pin chain passed via
   `verdify-platform-ci-crv96` and #500. This proves the packaged source pair
   only; no stage sync,
   reporting feed, producer request, route, or runtime activation occurred.
   The source tree now also defines an `occurrence-exporter` target derived from
   the locked dependencies image. It copies only two additional project-source
   files: an offline verifier and the shared contract module consumed by the
   real graph, camera, and joint producers. It runs as `101:101`, grants no
   release/device/live authority, and defaults to
   an offline contract verifier. This is packaging source only: no runnable
   renderer, runtime binding, workload, overlay image sentinel, built digest,
   pin, endpoint, credential, policy, reporting feed, or activation exists for
   it yet. Required build revision/release-time arguments are validated and
   bound into OCI labels plus canonical verifier-emitted image metadata.
2. **4a Release runtime:** `d91737d` landed via #473 with init hydration,
   atomic runtime, readiness, metrics, and tests. The follow-through makes the
   candidate visible in the Lab stage GitOps source only as a truly disconnected
   dormant workload: `replicas: 0`, exact source-bound agent/site image
   digests, no route, no object-store/AWS environment, and deny-all egress.
   Merging that source is not a live rollout: the Lab stage ArgoCD app remains
   manual-sync, and an operator sync is a separately recorded boundary. A later
   activation must pin both runtime images, add the reviewed 4b store/egress
   contract, explicitly raise replicas, and pass stage acceptance.
3. **4b S3 backend and event wiring:** the inactive full-site S3
   conditional-store foundation merged through #496, #502 carries the distinct
   typed occurrence store, and #507 carries the closed 143+2 caller. The
   concrete source-only operation adapter maps that caller to explicit store
   APIs; its CLI requires the literal `execute` command, canonical approved
   policy, and an explicit store location before store initialization. No
   workload selects it and it carries no endpoint, credential, route, or
   activation. The source-only #501 binding-name contract fixes the
   future non-secret metadata ConfigMap and separate reader/writer Secret names,
   validates canonical key-name inventories without reading values, and makes no
   existence, endpoint, deployment, or authority claim. The source-only 4c
   caller and adapter supply a closed store-operation contract without endpoint,
   value, credential, deployment, or activation wiring. PR #525 carries the
   next runtime prerequisite:
   fixed `https://s3-hdd.vallery.net`/`garage` client configuration; an explicit
   four-key S3 environment only; no environment read for local stores; code-level
   reader/writer enforcement; no-store `prepare`; reader `status`/`bundle`/
   `hydrate`; writer `publish`/`rollback`; and an exact four-key reconciler child
   allowlist. Its construction-only stage publisher factory requires explicit
   shared-S3 occurrence/site stores plus caller-supplied build, verifier, and
   checkpoint operations, with no operation defaults. It adds no Kubernetes
   manifest or Secret values, workload selection, endpoint probe, network call,
   replica, egress, route, sync, activation, distributed lease, retention/GC,
   or credential provisioning. After this prerequisite passes its own gate,
   next prove real-endpoint conditional writes and a distributed lease
   (credential presence by name only), event-driven publishing, bounded
   retention/GC, and resource metrics. S3 occurrence wiring and S7 closure are
   hard-gated on the 4c producer contract.
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
   approval-eligible immutable attestation.
   Issue #479 is open for this final gated proof; the earlier automatic close
   did not satisfy it.

GATE per implementation slice: PR merged + `make ci` green + in-cluster build
green. That gate does not close S3/4c: S3 remains open until the joint S3+S7
stage rollout serves and reconciles 143/143 graph and 2/2 approved camera
fallbacks at T0 and T+10, with no pending/invalid occurrences and with LKG and
freshness recovery proven. No reporting-tier activation, credential work,
production sync, DNS, or Quartz change occurs without its recorded human gate.

Phase 5 — **Production cutover (Jason-gated).** The canary scaffold landed via
#471 but remains unbuilt, unpinned, undeployed, and unrouted. Build/pin/deploy
the dark canary only after its gates, run full acceptance vs prod, then Jason
APPLY: route cutover, Quartz retirement, publisher decommission, ghcr image
retirement (ADR-0021 cleanup).
GATE: Jason explicit approval; rollback = route flip back to Quartz (kept
warm until sign-off).

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
  separately reviewed, explicitly authorized non-destructive disposition once
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

## Activation status (2026-07-14 ~11:30Z)

Jason approved the lab-stage occurrence activation 2026-07-14. Pass 1
repo/pipeline prerequisites are COMPLETE (exporter contract image, runtime
pins, publisher, AppProject CronJob permission, acceptance tooling, probe
deadline/scheduling hardening). Remaining blockers are EXTERNAL:
`jvallery/storage-infra#53` (private `verdify-lab-occurrences` bucket +
scoped reader/writer identities + contracted Secret delivery) and the
anonymous-disabled reporting tier read-only credential. After delivery:
~60-90 min two-pass activation (producer/runtime unrouted → 143+2 both-cache
convergence → reviewed route switch) then the 24h/96-sample freshness soak.
Known infra risk: multi-node Init/sandbox wedge in agent-fleet-ci (RCA
`jvallery/agents#3002`) — prod and stage serving are unaffected.

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
