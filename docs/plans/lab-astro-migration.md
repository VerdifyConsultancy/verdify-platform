# Lab Astro Migration — Consolidated Program Tracker

Last updated: 2026-07-13 (outer-loop assessment; supersedes chat-only status).
Owner: platform agent (Claude outer loop plans/verifies; Codex executes on
xhigh). Human gate: Jason (prod sync, DNS/edge, Quartz retirement, credential
work). Epic: #351 (L9, G3). This file is the single source of truth for the
Quartz→Astro migration of lab.verdify.ai; update it whenever a phase gate
changes state.

## Ground truth (verified live 2026-07-13 ~04:30 UTC)

| Surface | State |
|---|---|
| `lab.verdify.ai` (prod) | Quartz. `verdify-lab` Deployment in `verdify-prod` (ghcr image — pre-ADR-0021 holdover), `verdify-lab-publisher` CronJob republished every 10 min (mutable, shared RWO PVC, both replicas node-pinned). Healthy and fresh; had a 5-job BackoffLimitExceeded streak before recovering. |
| `lab-stage.verdify.ai` (canary) | Astro. `verdify-lab-astro-stage` in ns `verdify-platform`, 2/2 Ready, image `verdify-lab-astro@sha256:2c03489c…` (pin PR #460, shell contract **1.0.0**), content frozen at the 2026-07-12 snapshot. |
| `main` @ `3ce6674` | Full Astro implementation merged (PRs #459/#461/#462 + verdify-www#33 shell 1.1.0): parity comparator, immutable local releases, a11y/browser/visual/perf gates. **Merged but deployed nowhere.** |
| In-cluster CI | **Red for lab-astro on main.** `verdify-platform-ci-9vzwx` (rev `3ce6674`, post-#462) failed in `build-lab-astro` at its `test` step (exit 1) even though PR CI was green; earlier run `c7b2b` (rev `c1a7fff`) failed in `build-api`. No `ci/lab-stage-pin-*` PR for the post-#462 build exists → stage cannot advance. An 8h-old errored `verdify-lab-stage-resume-pin-debug…open-lab-stage-pin-pr` pod is the failed prior pin attempt. |
| Local checkout (`/workspace/verdify-platform/repo`) | `main` ahead 1/behind 7 of origin — the extra commit `03cff94` (prod-canary scaffold) is patch-identical to `da26d54` on `coordinator/lab-production-canary-v2` (verified hunk-for-hunk); do **not** push local main. The seven /tmp worktrees died with the 03:45 UTC pod reboot; only their branches survive — no uncommitted work was lost-in-place. |

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
| 1 | Shared design contract | 1.1.0 merged & consumed by main | Stage serves 1.0.0 |
| 2 | Search/CSP/camera/contact | Fixed impl merged+tested | Deployed stage still broken (search, camera) |
| 3 | Graph fallbacks | Local occurrence/LKG validation exists | No exporter, reporting tier, fallback release, or live delivery — **143 graph occurrences, 0 live images on stage** |
| 4 | Media & lightbox | Merged+tested | Old stage image unsized/non-lightboxed |
| 5 | Planner/evidence templates | Merged, proven on full snapshot | Not deployed; stage frozen at Jul 12 |
| 6 | Semantic parity | Routes/aliases complete; comparator improved | Exact same-snapshot parity not green: ~240 false findings from SVG-`<title>` parser bug (fix exists uncommitted on a dead worktree — must be recreated), 9 unavailable historical refs (5 daily-plan routes, 4 images), and Quartz-vs-Astro must build from the SAME immutable snapshot |
| 7 | Immutable publishing | Local CAS/release/cache engine merged | No S3 adapter wiring (CLI s3:// unwired, no endpoint/credential probe, no event producer/release-agent workload); release runtime on unmerged branch `coordinator/lab-release-runtime` (d91737d: hydration init/sidecar, atomic nginx current/tree, readiness/metrics, 61 tests passed) |
| 8 | Quality gates | Real-content gates green on main | Deployed stage is not the tested build; no full acceptance on live stage |
| 9 | Production cutover | Fail-closed canary scaffold on `coordinator/lab-production-canary-v2` | Not on main/built/pinned/deployed; Quartz remains authoritative; Jason APPLY required |

## Execution phases and gates

Phase 1 — **Unblock the pipeline (P0, owner: codex).**
Diagnose `build-lab-astro` test failure on rev `3ce6674` (in-cluster differs
from PR CI: suspect real-content/egress/memory), fix on main, re-trigger the
build, land the `ci/lab-stage-pin-*` digest PR (the pin lives at
`deploy/k8s/overlays/lab-stage/kustomization.yaml`, still pointing at
`2c03489c`), gate-review, roll stage. Evidence trail: failed workflow
`verdify-platform-ci-9vzwx`; the 8h-old errored
`verdify-lab-stage-resume-pin-debug…open-lab-stage-pin-pr` pod holds the
prior pin attempt's failure logs — capture before GC. Sibling failure to
diagnose in passing: run `c7b2b` (rev `c1a7fff`) failed `build-api`.
GATE: stage serves the post-#462 digest; shell reports 1.1.0.

Phase 2 — **Stage convergence verification (owner: codex, verified by outer
loop).** Confirm on live stage: Pagefind search works (the 2026-07-13 probe
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
GATE: defects cleared on live probes + full acceptance run against the
deployed build.

Phase 3 — **Program formalization (owner: codex).** #351 → In Progress/P1/XL;
create the nine surface child issues (What/Why/How/Test/Success + Project #5
fields); salvage/park branch work per dispositions below. Doc debt from the
2026-07-13 staleness scan (root tracking docs are being corrected with this
tracker; these deeper ones belong here): `docs/handoff/k3s-agent-handoff.md`
§2/§5.3 (zot/Kaniko pipeline, lab blocker), `docs/agents/web.md` (site-astro
ownership + zot deploy path), `LANES.md` staleness banner for the lab lane.
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
