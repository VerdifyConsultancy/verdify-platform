# Verdify Lab Astro stage

This is an isolated, static Astro candidate for `lab-stage.verdify.ai`. It does
not replace Quartz or alter `lab.verdify.ai`. The builder consumes one local
sanitized snapshot, verifies its closed `attestation.json` and
`manifests/content.json`, compiles all Markdown and assets without database,
Grafana, S3, or HTTP access, and emits a noindex stage tree.

The implementation deliberately uses custom Astro layouts plus Pagefind. No
Starlight claim is made: there is not yet an approved immutable corpus on which
Starlight can demonstrate the required Quartz/Obsidian/raw-HTML route contract.

## Local build

The checked-in fixture exercises root, leaf, folder, alias, planner, table,
download, media, and Grafana occurrence semantics. It also carries synthetic,
non-production representatives for the home, planner, forecast, archive,
evidence, contact, and media/lightbox page families used by browser quality
gates:

```bash
npm ci
npm test
```

`npm test` includes the desktop/mobile browser quality gate. It runs the
representative routes at the Marketing contract's 390px and 1440px widths,
checks WCAG A/AA rules with Axe, horizontal overflow, default-light and
explicit-dark surface rules, console/request failures, bounded DOM/asset/JS
budgets, FCP/LCP/CLS/long-task budgets, semantic page enhancements, skip-link
and mobile-navigation focus, form boundaries, reduced motion, responsive image
geometry, and native-dialog lightbox focus restoration. Chromium must be
installed once in a fresh tool image with `npx playwright install chromium`.
For a focused rebuild and quality run, use:

```bash
npm run test:quality
```

The pinned browser fixture mirrors the immutable page-primitives visual
contract published by the Marketing shell release. It is a regression oracle,
not an alternative source of design tokens; browser assertions read the
computed `--color-*` values from the installed shared shell.

The fixture path is synthetic-only and cannot satisfy real snapshot mode. A
real stage candidate is assembled outside Git as `.snapshot/` and must carry
the canonical closed `verdify.lab-stage-sanitized-snapshot` v1 attestation.
That attestation binds the original 429-file manifest, the sanitized 429-file
manifest, the zero-finding public-output guard report, bounded transformation
counts, and byte-preserved HLS inventory. The default Docker target hard-codes
`.snapshot`; a missing, raw, or fixture snapshot fails before Astro runs.

The checked-in release descriptor under `vendor/snapshot/` is the only network
authority for pre-Kaniko hydration. Its GitHub release URL, byte count, asset
digest, and attestation digest are fully pinned; after that exact public asset
has been published, hydrate into an absent destination with:

```bash
npm run snapshot:hydrate
```

The hydrator has no token or URL override. It follows only bounded HTTPS GitHub
release redirects, streams to an exact byte cap while hashing, verifies the tar
before extraction, accepts only regular files/directories in the closed
content/manifest/attestation/evidence layout, rejects traversal and collisions,
cross-checks the attestation, content manifest, and zero-finding guard evidence,
then atomically selects `.snapshot`. Docker receives that local directory and
builds offline.

The same release bindings can be checked on an already-local candidate without
network or mutation:

```bash
npm run snapshot:verify -- \
  --snapshot /tmp/verdify-lab-snapshot-sanitized-20260712t1620z
```

To exercise the real compiler locally after the private release asset has been
fetched, hash-verified, and placed outside Git:

```bash
LAB_SNAPSHOT=/tmp/verdify-lab-sanitized-snapshot-20260712t1620z \
SITE_ORIGIN=https://lab-stage.verdify.ai \
npm run build
```

The current frozen snapshot is a legacy content-hash capture, not the future
immutable snapshot attestation. `static-build.json` and `route-manifest.json`
therefore always report `localEvidenceStatus: provisional-only` and
`approvalEligible: false`.

## Specialist evidence releases

The Astro compiler now emits `occurrence-manifest.json` for every discovered
Grafana and current-camera occurrence. Grafana records preserve the normalized
dashboard UID, panel, query multiplicity, variables, time range, semantic role,
cadence, and an opaque occurrence ID. Camera records expose only an opaque
occurrence ID, semantic role, stable same-origin target, cadence, and opaque
occurrence, exact-policy, and approved-request digests. The upstream camera URL is
never copied into the public manifest.

`scripts/manage-occurrence-release.mjs` implements the offline specialist-release
contract. Its input is a canonical, bounded request containing one idempotent
`verdify.lab-release-trigger` and local candidate paths. It has no HTTP, database,
Grafana, object-store, or credential client. PNG candidates are accepted only after
chunk checksums, bounded inflate, scanline-filter reconstruction, dimensions, encoded
digest, and decoded-pixel digest all validate; ancillary metadata is rejected.
Failed renders/captures retain the prior verified fallback; corrupt candidates
never replace last-known-good bytes.

Camera capture writes candidates only beneath an output root that already exists
as a canonical real directory. Create that directory before running
`npm run camera:export`; a missing or linked output root fails before the camera
request begins, and linked candidate subdirectories fail before any candidate
write.

The local store uses content-addressed image blobs and release manifests. One
canonical release `selection.json` atomically selects both `current` and `previous`.
Each current-camera occurrence has a separate two-generation CAS selector so camera
events never mutate the graph/page release. Every selector carries a generation and
selection-digest precondition. Private generations bind the exact canonical policy
SHA-256 and expected camera-request provenance SHA-256. Reconciliation selects LKG
only when both still match; changing a camera URL or any policy byte, even under the
same policy-version label, cannot relabel the old generation. Event intents are
durable before selection, retries are idempotent, event IDs bind both payload and
envelope, and old-event tombstones survive the ten-manifest retention window. All
release instants use strict canonical UTC second-or-millisecond form and reject
calendar normalization. Local rollback swaps current/previous without regenerating
evidence. Planner triggers carry a five-minute
target and fifteen-minute alert contract; graph and camera records carry their
ratified cadence-based stale thresholds.

For a build that has an already-verified local occurrence store, set
`LAB_OCCURRENCE_STORE` to that read-only store. The compiler revalidates the selected
manifest and decoded blobs, copies only referenced content-addressed images into the
static release, renders them as the inline same-origin fallbacks, and preserves the
separate interactive evidence link. An absent store remains explicit pending evidence
and never causes a network render during the build.

This is release tooling and fixture proof, not live export authority. The current
stage image was built before these contracts and still has no selected occurrence
release. A future stage rollout requires a new fleet-origin image/pin and the normal
stage GitOps durability probe. Production Lab cutover, a reporting-store grant,
camera sanitizer authority, public routing, and Quartz retirement remain separately
gated. In particular, nginx does not yet resolve `/evidence/current/<id>` or watch
these local selectors: a deployed no-build pointer update/rollback still requires the
future runtime/object-store adapter and end-to-end proof. No service restart is
required by this source-only change.

### Phase 4c producer boundary (inactive)

`scripts/prepare-occurrence-export.mjs` now closes the offline handoff that a
future specialist producer must satisfy. The reviewed, byte-bound policy at
`config/lab-stage-occurrence-export-policy.json` contains exactly 143 graph and
two opaque camera occurrence fingerprints from the accepted stage snapshot. A
producer batch must name the operator-owned, one-way, read-only public reporting
feed contract, bind the exact canonical policy SHA-256, carry its source watermark,
and include every approved occurrence exactly once. The trusted compiler clock
allows at most five minutes of delivery delay and 60 seconds of future clock skew.
The target is end-to-end source-watermark p95 at or below 15 minutes; a sample
older than 30 minutes is alert state and cannot prepare a release, preserving
last-known-good evidence.

Candidate files must be metadata-free RGB/RGBA PNGs named by their actual SHA-256.
The compiler independently decodes them, verifies CRCs and scanlines, bounds total
and image-data chunk cardinality, and applies tighter MIME, byte, and dimension
bounds before it can emit canonical media-first and reconciliation publish
requests. Camera batches carry only opaque occurrence IDs plus a domain-separated
request-provenance SHA-256. That digest binds the occurrence ID, GET method, exact
URL, and the no-redirect/no-auth/no-cookie rules through the candidate, private
generation, selection, and reconciliation contracts without publishing the URL.
Both the exact policy and request digests must still match before camera LKG is
selected. Prepared `media/*.request.json` files are accepted directly by the closed
v3 `publish-media` CLI contract, and `release.request.json` by the closed v2
`publish` contract; missing identities and unknown URL-bearing fields fail before
store mutation. The approved upstream handoff is
GET-only to the two exact
`api.verdify.ai/api/v1/public/cameras/.../latest.jpg?h=1080` paths; redirects,
cookies, authentication, device/VLAN access, Frigate, and go2rtc are forbidden,
and JPEG input must be decoded and cleanly re-encoded without metadata before it
enters the candidate directory.

The checked-in policy has `activation.state=blocked`. Validation is available,
but request preparation refuses it. The matching Kubernetes Component under
`deploy/k8s/components/lab-occurrence-reporting-boundary/` is referenced by no
overlay, defines no workload or Secret, and is deny-all. A separate Jason-gated
change must approve and create the isolated reporting feed/tier, its
least-privilege credential, occurrence-store access, and egress limited to that
store plus `api.verdify.ai:443`. Existing anonymous `graphs.verdify.ai` and the
Track A primary database role are explicitly ineligible.

The inert graph-producer library adds one pure planner that verifies the exact
occurrence-manifest bytes against that policy and emits all 143 render targets in
manifest order without an endpoint. Its producer has no default renderer or network,
service, credential, database, Kubernetes, Grafana, or object-store client: an
approved policy and an explicitly injected renderer are required before any call.
The producer also requires the full closed reporting-feed envelope and derives its
canonical digest itself. Only that digest enters the v2 plan and all 143 requests;
the feed identity, watermark, endpoint, and credential details do not. The injected
renderer must declare the same digest through the exact abort-cooperative v2 contract
and settle promptly after the producer aborts it. The producer enforces the claim: a
renderer or response body that does not settle and clean up within the short bounded grace period stops
all new scheduling and fails the whole batch closed, while still returning all 143
ordered null records. At most four calls can remain unsettled, including after the
producer returns. Bounded PNG responses are decoded and
deterministically re-encoded as metadata-free RGB PNG, then published through the
same canonical content-addressed candidate store used by camera capture. Stable,
URL-free v3 results include every graph once on mixed failures and repeat only the
feed-envelope digest for the later 143+2 assembler to verify. The offline real-build
verifier uses an explicit non-live envelope solely to prove planning; it makes no
feed-existence or freshness claim. This source-only slice
does not provide or activate the reporting feed, renderer, watermark/alert path,
S3 delivery, workload, or stage rollout.

## Complete built-site releases

The full Astro output now also has a local release and cache path. Run
`scripts/manage-site-release.mjs prepare` after a successful build to obtain the
content identity and event-payload digest, then publish one canonical request. The
release manifest binds snapshot, policy, builder, event, freshness, and the complete
path/digest/size/media-type inventory. Publication uses durable event intent,
selection-digest CAS, atomic current/previous selection, a crash-recoverable same-host
lease with hard-crash candidate repair, identical-content change gating, maximum-ten
release retention, byte-aware reachability eviction under a 10 GiB retained-store cap.
Rollback swaps pointers without a
rebuild.

`manage-site-release.mjs hydrate` verifies every blob and completed output tree before
atomically moving the serving cache's `current` symlink. A same-host hydrate lease
serializes install/swap/prune; unique physical generations permit atomic self-repair
without deleting the selected path. It keeps two complete local generations and can
cold-start from a separately verified baked known-good bundle when
the store is unavailable. Its machine-readable status distinguishes current,
previous, and baked fallback, and carries the planner five-minute target/fifteen-minute
alert evaluation. `npm test` includes failure injection, concurrency, GC/retention,
corruption/fallback, rollback, baked-outage, and CLI end-to-end coverage.

This is a complete local-filesystem backend, not a deployed S3 adapter or distributed
lease. Its candidate is present in the Lab stage GitOps source only as a dormant
`replicas: 0` workload with separate zero-digest agent/site sentinels, no route, no
object-store/AWS environment, no application/object-store credential, and no
egress. Its standard zot registry `imagePullSecret` is only for image retrieval.
The stage app remains manual-sync; merging source is not an operator sync, and even
a sync cannot schedule this zero-replica candidate. Activation still needs reviewed
image pins, store/egress wiring, an explicit replicas change, cache and alert proof,
and normal stage acceptance. No code in this path queries S3, the database, Grafana,
cameras, or the network, and this change does not alter production.

The stage vendors the reviewed offline parity comparator at
`scripts/site-build-parity.py` (SHA-256
`d3f6662ac8303ae8a29020743254eb859db61693103b420b26df2c043ee659a4`):

```bash
npm run parity:manifest
QUARTZ_MANIFEST=/tmp/verdify-lab-quartz-baseline-20260712t1620z.json \
npm run parity:compare:provisional
```

The baseline itself has known integrity blockers. A successful structural
diagnostic is not a baseline, canary, cutover, or production approval.

For container-only fixture diagnostics, select the non-default target
explicitly: `docker build --target fixture-runtime -f Dockerfile .`. That image
is not release input.

## Shared-shell boundary

The build vendors the reviewed `@verdify/site-shell` 1.1.0 release from WWW
commit `7febbc479c6ed7d22f829e9c1e7109bc9bc7c6c0`. The archive, independent
release record, and four-file consumer kit are committed under `vendor/` and
`scripts/site-shell/`; every byte is independently pinned before the hardened
installer runs, and the installed tree is verified again in a separate
process. There is no runtime, CDN, registry, or package-manager fetch. The
shared Header, Footer, Breadcrumbs, full-page design contract, responsive media
and lightbox primitives, Lab lockup, design tokens, and self-hosted IBM Plex
fonts come directly from that release. Lab-owned evidence navigation,
Pagefind search, reader mode, article styles, and specialist evidence rendering
remain outside the shell boundary.

## Browser runtime contract

Pagefind runs under the stage CSP with only `'wasm-unsafe-eval'`; broad
`'unsafe-eval'` is forbidden. Fonts are emitted as same-origin files and KaTeX
CSS is linked only by pages that contain rendered math. The contact form keeps
its captured HTML and submission endpoint, while Lab-owned selectors map its
legacy Quartz variables to the shared Marketing tokens.

Camera markup never auto-loads or refreshes across origins. For each captured
public camera URL, the snapshot publisher may include an immutable
`static/cameras/<camera>/latest.jpg` file. The compiler rewrites the image and
30-second refresh to that same-origin last-known-good path. If the asset is
missing, the build renders an explicit unavailable state instead of a broken
image; `static-build.json` records occurrence and local-fallback counts. This
contract does not authorize database, device-network, or Track A access from the
site builder.

The browser regression gate exercises real Pagefind WASM under the nginx CSP,
same-origin KaTeX fonts, and computed contact-form visibility/focus styles:

```bash
npx playwright install chromium
npm run test:browser
```

## Stage image contract

The Dockerfile separates dependency assembly from the content build and pins
exact Node, Astro, Tailwind, PostCSS, Pagefind, and runtime-image versions. Its
default runtime can only be built from an uncommitted, sanitized `.snapshot/`
directory. The public, immutable snapshot release asset is fetched and verified
before Kaniko receives the context; neither the Astro compiler nor runtime
fetches it. The release pipeline pins the published zot-origin digest in Git,
and the stage runtime has no egress.

The runtime is static nginx on port 8080, globally noindex, and read-only-root
compatible. Its standard zot registry `imagePullSecret` is only for image
retrieval; it requires no application/object-store Secret, service-account
token, database, object store, Grafana, or device-network access.

## Known blockers

- The sanitized legacy capture remains provisional-only; the future approved
  immutable filesystem/object-store evidence attestation is not implemented.
- Digest-verified local Grafana fallback images remain absent from the frozen
  snapshot. The compiler and specialist-release store now enforce decoded,
  content-addressed current/previous selection and last-known-good retention, but
  a policy-approved exporter/reporting source has not populated a selected release.
- Current-camera occurrences now have an opaque same-origin manifest contract, but
  the camera sanitizer and independently served occurrence pointer are not deployed.
- Planner event idempotency, freshness, conditional promotion, local pointer rollback,
  verified cache hydration, and baked outage fallback are implemented and tested; no
  bounded event delivery authority, freshness histogram, or firing alert is connected
  to stage yet.
- The complete built-tree local store implements reachability GC, maximum-ten
  retention, and the ratified 10 GiB cap. A real object-store backend and distributed
  lease remain external deployment work; neither is claimed here.
- The built-tree publisher lease recovers a provably dead same-host PID and excludes a
  live local owner. Cross-pod publication still requires a distributed lease or
  object-store conditional-write primitive.
- The frozen Quartz baseline has known HLS, alias, feed/sitemap, missing-asset,
  and fallback findings and is not clean approval evidence.
- The WWW shell release is review-only until its producer PR is merged; the Lab
  pins its exact reviewed bytes and does not claim producer merge or cutover.
- The zero-finding public-data guard report is mandatory snapshot input, but a
  public canary still requires the separate approval gates.
