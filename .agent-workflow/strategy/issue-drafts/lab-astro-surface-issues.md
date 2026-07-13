# Lab Astro migration — nine surface issue drafts

Parent: `#351`. Canonical plan: `docs/plans/lab-astro-migration.md`.

These drafts use stable aliases `S1`–`S9` during consensus review. After
consensus, create all nine issues, replace alias dependencies with issue
numbers, attach them as native sub-issues of `#351`, and populate Project #5.

Common fields: Sprint `S7 irrigation-lab-testing-hardening`; Epic `L9 Lab
Notebook, Website, and Publishing (#351)`; Agent Lane `verdify-platform`;
milestone `G3 — Planner, Irrigation, Lab, and Research`; labels `area:web`,
`theme:Site / RAG freshness`, `lane:L8-product`, and `wave:W3`.

## S1 — Shared marketing design contract

### Project Tracking

- Status: Done
- Priority: P1
- Effort: M
- Component: site-astro/shared-shell + VerdifyConsultancy/verdify-www
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #461, #465, #468, VerdifyConsultancy/verdify-www#33
- Dependencies: verdify-www shell 1.1.0 (satisfied); blocks S2, S4, S5, S8
- Evidence: docs/plans/lab-astro-migration.md; site-astro/vendor/site-shell/releases/verdify-site-shell-1.1.0.commit-7febbc479c6ed7d22f829e9c1e7109bc9bc7c6c0.release.json; #351 Phase 2 evidence

### What

Consume the immutable Verdify marketing shell in Lab Astro: typography,
tokens, header/footer, global navigation, breadcrumbs, and responsive behavior.

### Why

Both sites need one checksum-bound brand contract without coupling their
repositories or permitting silent drift.

### How

Vendor and verify shell 1.1.0, install only manifest-declared files, retain
Lab-specific navigation/content inside the shared frame, and fail closed on
checksum or version mismatch.

### Test

Run `npm test` and the real-content browser gate; verify desktop/mobile header,
footer, navigation, font/token markers, keyboard behavior, and artifact
checksum on live stage.

### Success

- Exact WWW commit `7febbc479c6ed7d22f829e9c1e7109bc9bc7c6c0`.
- Exact archive SHA-256 `0645773ab3a952727251840e28dc73929a3e42b904450bcc9e7d25d8b03b1c91`.
- Stage reports shell 1.1.0 with zero checksum drift.
- Required responsive and accessibility browser tests pass.
- Production adoption remains explicitly owned by S9.

## S2 — Finish search, CSP, camera, and contact surface

### Project Tracking

- Status: Backlog
- Priority: P1
- Effort: L
- Component: site-astro/search + CSP + current-media + contact
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #308, #461, #466, #468
- Dependencies: S1 satisfied; close blocked by S3 current-media output and S7 live publication
- Evidence: docs/plans/lab-astro-migration.md Phase 2; #351 acceptance evidence; scratch/lab-astro-final-t0.json and scratch/lab-astro-final-tplus10.json

### What

Complete Pagefind search, reviewed CSP, fail-closed same-origin camera
presentation, and accessible contact-form semantics.

### Why

Search and contact pass, but both camera occurrences still lack verified,
privacy-approved same-origin fallbacks; cross-origin hotlinking is intentionally
disallowed.

### How

Keep Pagefind in no-worker mode under reviewed CSP, consume S3's immutable
camera release, render a safe unavailable state when absent, and preserve
semantic/contact keyboard behavior. Keep Cloudflare analytics blocking
diagnostic-only unless separately approved.

### Test

Run manifest, parity, and browser suites plus live stage acceptance. Test
absent/stale/fresh camera states, CSP headers, Pagefind query execution,
contact focus/labels/touch targets, the native POST action, and zero
cross-origin camera/image requests. The contact form is a browser-submitted
POST to `https://api.verdify.ai/api/v1/public/contact`; the static nginx runtime
does not proxy or originate the request.

### Success

- Pagefind assets return 200 and a known query returns results.
- CSP retains `img-src 'self' data:`, `frame-src 'none'`, `object-src 'none'`,
  and no wildcard broadening.
- 2/2 camera occurrences have verified same-origin immutable fallbacks; zero
  pending and zero public hotlinks.
- Privacy exclusion policy from #308 remains enforced.
- Contact semantic-interaction browser tests pass.
- Contact retains `method="post"` and the exact approved API action; CSP
  `form-action` permits that endpoint, and browser evidence confirms submission
  is not redirected to the static Lab runtime.

## S3 — Secure graph and camera occurrence fallbacks

### Project Tracking

- Status: In Progress
- Priority: P0
- Effort: XL
- Component: site-astro occurrence store + isolated Grafana exporter + camera snapshots
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #308, #458, security/grafana-supported-images
- Dependencies: no code-start blocker; final live proof requires S7; privacy policy #308; operator-owned read-only reporting feed and credential by name only
- Human gate: Jason must approve standing up the reporting tier and issuing or
  changing its scoped read credential or reporting feed; code/evidence must
  reference only the Secret name/key, never its value
- Evidence: docs/plans/lab-astro-migration.md Phase 4c; site-astro/scripts/lib/occurrence-release.mjs; Phase 2 reports showing 143 graph and 2 camera occurrences

### What

Build the secure producer/store path for all graph and current-camera
occurrences, including immutable fallbacks, freshness state, and
last-known-good behavior.

### Why

Stage currently has 143 graph fallbacks pending and two camera occurrences
without approved local images—the largest remaining functional and security
gap.

### How

Use an isolated, anonymous-disabled Grafana reporting tier with a
least-privilege read credential distinct from Track A. Its datasource is an
operator-owned, one-way, read-only public reporting feed containing only the
allowlisted time-series/materialized views required by the 143 graph targets;
it does not reuse the Track A Grafana datasource UID, database role, controller
credential, or any write-capable connection. The feed publishes a source
watermark. Allowlist dashboard, panel, and time-range targets; render bounded
images; validate MIME, dimensions, and digest; publish content-addressed
occurrence releases; retain last-known-good on failure; and apply the camera
privacy allowlist before persistence. Run the exporter in a dedicated context
whose egress is limited to that reporting tier and the occurrence store; the
deny-all static stage runtime remains credential-free.

### Test

Exercise occurrence unit/rollback tests, adversarial URL/MIME/size inputs, CAS
races, stale/failure recovery, network and credential boundaries, and real
read-only endpoint rendering. Prove the one-way feed exposes no write/control
surface and test source-watermark lag/failure. Run live T0/T+10 reconciliation
after S7 integration.

### Success

- 143/143 graph occurrences and 2/2 approved camera occurrences have valid
  same-origin immutable fallbacks.
- Zero pending, invalid, conflicting, or credential-bearing occurrence targets.
- Interactive graph links remain restricted to approved `graphs.verdify.ai`
  targets.
- Anonymous access is disabled; reporting cannot mutate or use the Track A
  primary datasource.
- The public reporting feed's p95 source-watermark lag is at most 15 minutes;
  lag beyond 30 minutes alerts, retains LKG, and is never labelled fresh.
- Failed exports retain last-known-good; age beyond the existing 30-minute
  graph threshold fires and later recovers.
- No secret value appears in logs, releases, URLs, or evidence.
- S3 stays open until the joint S3+S7 stage rollout serves and reconciles all
  143 graph and two approved camera fallbacks at both T0 and T+10.

## S4 — Responsive media and keyboard-complete lightbox

### Project Tracking

- Status: Done
- Priority: P1
- Effort: M
- Component: site-astro media compiler + responsive images + lightbox
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #458, #461, #468
- Dependencies: S1 satisfied; #458 separately owns expanded HLS/MP4 public-output validation
- Evidence: docs/plans/lab-astro-migration.md; site-astro/tests/quality.browser.test.mjs; Phase 2 acceptance reports

### What

Preserve all authored media with intrinsic dimensions, responsive variants,
coherent gallery layout, and an accessible native lightbox.

### Why

Quartz-era images were oversized, unsized, and inconsistently presented; the
migration must improve layout without dropping media.

### How

Generate bounded responsive variants and `srcset`/`sizes`, retain aspect
ratios, group gallery media, and use a native dialog with keyboard, focus, and
reduced-motion handling.

### Test

Run output and quality browser suites on desktop/mobile; validate natural
versus declared ratios, keyboard open/Escape/close/focus return, and browser
console/network failures.

### Success

- Every responsive image has valid width, height, `srcset`, and `sizes` (stage
  baseline: 121/121).
- Natural and declared aspect ratios agree.
- Lightbox opens and closes by keyboard, restores focus, and respects reduced
  motion.
- No authored image is lost and existing performance/layout budgets remain
  green.

## S5 — Planner, archive, forecast, and evidence templates

### Project Tracking

- Status: Done
- Priority: P1
- Effort: L
- Component: site-astro templates + lab snapshot compiler
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #43, #219, #461, #468
- Dependencies: S1 satisfied; S7 owns freshness; S6 owns unavailable historical references
- Evidence: docs/plans/lab-astro-migration.md; site-astro/tests/quality.browser.test.mjs; Phase 2 route/acceptance evidence

### What

Provide dedicated Astro presentation for daily plans, planner archive,
forecast, operations/evidence, and contact content families.

### Why

These are the Lab's highest-value dynamic surfaces and must retain semantics
and interaction rather than become generic Markdown pages.

### How

Compile the reviewed snapshot into template-specific routes, accessible
tables/details/filters, evidence cards, and responsive navigation while
preserving source provenance.

### Test

Build the full snapshot; run route, output, parity, and browser tests for
daily-plan disclosure controls, archive filtering, forecast, evidence, and
contact routes.

### Success

- Every current-snapshot template family renders and is reachable.
- Daily-plan details and archive filtering work by keyboard and mobile.
- Stage baseline remains 152 sources to 323 routes without template-family loss.
- Zero template browser failures.
- Freshness/event delivery is tracked only in S7; the nine historical-reference
  dispositions remain in S6.

## S6 — Reach exact same-snapshot semantic parity

### Project Tracking

- Status: Ready
- Priority: P1
- Effort: L
- Component: site-astro/scripts/site-build-parity.py + snapshot tooling
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #458, #461, #462
- Dependencies: S1, S2, S3, S4, S5, S7; include #458 output before final locked comparison if merged
- Evidence: docs/plans/lab-astro-migration.md Phase 4d; site-astro/scripts/site-build-parity.py; site-astro/tests/test_site_build_parity_limits.py

### What

Produce a strict, framework-neutral Quartz-versus-Astro comparison from the
exact same immutable source snapshot, including routes, feeds, canonical URLs,
and social metadata.

### Why

Current findings are contaminated by an SVG `<title>` parser bug, differing
snapshots, and nine unavailable historical references, so they cannot prove
content preservation.

### How

Recreate the SVG-title fix, add at least eight targeted regression tests, build
both generators from one verified snapshot digest, compare without provisional
mode, validate RSS/XML and canonical/Open Graph/Twitter metadata, and restore or
explicitly disposition every unavailable historical reference.

### Test

Run `npm run test:parity`, full `npm test`, generate both manifests with
recorded snapshot hashes, run strict `parity:compare`, then `make ci` and the
in-cluster build. Parse every discovered RSS/feed route, compare its entry set
and absolute canonical URLs, and verify live-stage canonical, Open Graph, and
Twitter-card metadata plus referenced social images.

### Success

- Both manifests record the identical immutable snapshot digest.
- 240/240 canonical routes, 84/84 aliases, and the full same-origin reference
  graph reconcile.
- Exact unexplained semantic findings = 0; parser false positives = 0.
- Every same-snapshot RSS/feed route is valid XML and has the expected entry
  set, timestamps, and absolute canonical URLs; live-stage feed URLs return 200.
- Canonical URL, Open Graph title/type/url/image, and Twitter-card fields match
  the same-snapshot contract on every applicable route; referenced social
  images return 200 and no source social surface is silently dropped.
- All nine unavailable references are individually restored or explicitly
  reviewed/dispositioned—none silently omitted.
- Final evidence does not use `--allow-provisional`.

## S7 — Immutable S3 publishing and release runtime

### Project Tracking

- Status: In Progress
- Priority: P0
- Effort: XL
- Component: site-astro release store + S3 + release-agent + Lab runtime
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #43, #219, coordinator/lab-release-runtime@d91737d
- Dependencies: no Phase 4a start blocker; S3 exporter artifacts for full-path proof; endpoint/credential names only
- Human gate: Jason must approve issuing, scoping, changing, or rotating the S3
  credential and any real-endpoint/release-agent activation; code and inert
  Secret name/key references may land without that activation
- Evidence: docs/plans/lab-astro-migration.md Phase 4a/4b; docs/site-publishing-pipeline.md; site-astro/scripts/lib/site-release-store.mjs

### What

Land the immutable release runtime and wire existing CAS primitives to S3,
occurrence persistence, and event-driven publishing.

### Why

Stage still consumes a frozen vendored snapshot and production uses a mutable
10-minute publisher; neither provides reliable atomic promotion, rollback, or
timely content.

### How

Cherry-pick only `d91737d`; retain init hydration, sidecar reconciliation,
atomic nginx current/tree, readiness, and metrics; construct the CLI `s3://`
adapter; wire occurrence callers; prove conditional writes against the real
endpoint; and add a release-agent/event producer. Do not retire the production
Quartz publisher here. Keep real-endpoint probes and the release agent inert
until the issue-local credential/activation gate is recorded.

### Test

Run the existing 61-test runtime baseline plus full `npm test` and `make ci`;
prove concurrent CAS behavior, restart/hydration, two-pod convergence,
partial-release rejection, rollback, two-generation cache retention, and a
credential-safe real-endpoint probe. Exercise CAS-aware object GC and verify
storage/request/egress accounting without deleting the active or rollback
generation.

### Success

- Three consecutive content events reach stage in at most 10 minutes each.
- Every release and occurrence object is immutable and digest-addressed;
  pointer changes use conditional writes.
- CAS-aware GC retains the current and rollback generation, removes only
  unreferenced objects after the documented recovery window, and exports bytes
  written/retained/deleted plus request/egress metrics. The observed daily
  footprint and alerting budget are recorded before closure.
- Both stage pods converge on the exact release digest after restart; readiness
  stays false during incomplete hydration.
- No partial generation is served; rollback to the previous generation
  completes in at most five minutes.
- Frozen vendored content is no longer the live-stage publishing source.
- Secrets are referenced by name/key only; production Quartz remains untouched
  until S9.
- Credential provisioning/change and real-endpoint or release-agent activation
  have Jason's recorded approval; source-only work never reads a Secret value.

## S8 — CI quality gates and durable stage acceptance

### Project Tracking

- Status: Done
- Priority: P1
- Effort: L
- Component: site-astro tests + repo-build CI + live acceptance
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #463, #464, #465, #466, #467, #468, jvallery/agents#2967, #2969, #2970, #2971, #2972
- Dependencies: S1, S4, S5 satisfied; exact tested digest pinned by #468
- Evidence: #351 Phase 2 comment; scratch/lab-astro-phase1-log.md; scratch/lab-astro-final-t0.json and scratch/lab-astro-final-tplus10.json

### What

Make the real-content site gate reproducible in-cluster and prove the deployed
stage digest durably.

### Why

A locally green build was insufficient: missing Playwright browsers, false
scheduler headroom, lost failure pods, and pin non-idempotency previously
separated tested code from deployed code.

### How

Provision the exported Playwright browser, use truthful CPU requests on
runner-eligible nodes, retain failed pods, make pin creation retry-safe, deploy
the exact digest, and compare T0/T+10 evidence.

### Test

Run full repo/in-cluster CI, 12 quality browser tests, image/runtime digest
probes, rollout health, live acceptance, and a minimum ten-minute durability
re-probe.

### Success

- 12/12 real-content gates pass without weakening the strict `<150ms`
  long-task budget.
- Stage runs exact digest `ee36941f…60b99`, 2/2 Ready, zero restarts, distinct
  nodes.
- T0/T+10 stable evidence is identical: `ba2d4c4e…e7212`.
- Search, shell, navigation, responsive images/lightbox, routes, and occurrence
  identity reconcile.
- Graph/camera fallback completeness remains explicitly owned by S2/S3, not
  falsely claimed here.

## S9 — Jason-gated production Astro cutover and Quartz retirement

### Project Tracking

- Status: Backlog
- Priority: P1
- Effort: XL
- Component: deploy/k8s Lab prod canary + ArgoCD/edge + Quartz publisher retirement
- Sprint: S7 irrigation-lab-testing-hardening
- Milestone: G3 — Planner, Irrigation, Lab, and Research
- Epic: L9 Lab Notebook, Website, and Publishing (#351)
- Agent Lane: verdify-platform
- Parent: #351
- Related Issues/PRs: #337, #459, #460, coordinator/lab-production-canary-v2
- Dependencies: S1-S8 green; Jason explicit APPLY; network-infra route truth; no prod action before gate
- Evidence: docs/plans/lab-astro-migration.md Phase 5; production-canary branch; final parity and stage acceptance reports

### What

Land and validate a fail-closed dark Astro production canary, then—only after
Jason approval—cut traffic over and retire Quartz, the publisher, and GHCR
residue.

### Why

Quartz remains authoritative and is the final source of layout drift, mutable
publishing risk, and the last pre-ADR-0021 GHCR site image.

### How

Merge the canary scaffold without routing public traffic, build, pin, and
deploy it through GitOps only after its separate production gate; run
same-snapshot acceptance against Quartz; document rollback; request Jason
APPLY; flip the approved route; keep Quartz warm through sign-off; then
decommission it explicitly.

### Test

Verify exact canary image IDs, readiness, health, and no public exposure; run
strict parity plus full T0/T+10 acceptance; validate route, headers, and
canonical URLs after approval; exercise the documented route-flip rollback
before retirement.

### Success

- S1-S8 success criteria are green before any cutover.
- Dark canary is healthy on the reviewed digest and receives no public traffic
  before approval.
- Jason's explicit APPLY is recorded.
- Cutover has zero unexplained parity findings and no observed 5xx during the
  acceptance window.
- Route rollback is demonstrably executable in at most five minutes.
- Quartz stays warm until sign-off, then its Deployment, 10-minute publisher,
  PVC dependency, and GHCR reference are retired with evidence.
- No DNS, edge, or production sync occurs autonomously.
