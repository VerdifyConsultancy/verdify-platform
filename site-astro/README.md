# Verdify Lab Astro stage

This is an isolated, static Astro candidate for `lab-stage.verdify.ai`. It does
not replace Quartz or alter `lab.verdify.ai`. The builder consumes one local
sanitized snapshot, verifies its closed `attestation.json` and
`manifests/content.json`, compiles all Markdown and assets without database,
Grafana, S3, or HTTP access, and emits a noindex stage tree.

The implementation deliberately uses custom Astro layouts plus Pagefind. No
Starlight claim is made: there is not yet an active immutable corpus on which
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
geometry, and native-dialog lightbox focus restoration. The standard quality
gate uses pinned Chromium; the focused media-autoload gate also uses pinned
WebKit so Chromium and Safari-class loading behavior cannot drift. The npm
commands install only their required pinned browsers and OS dependencies when
they are absent. For a focused rebuild and quality run, use:

```bash
npm run test:quality
```

To run only the offline Quartz graph/camera loader contract in both browsers:

```bash
npm run test:media:cross-browser
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

The current frozen snapshot is a legacy content-hash capture, not an active
immutable snapshot. It carries the `verdify.lab-stage-sanitized-snapshot`
contract, whose verifier hard-rejects `activationEligible !== false`, so
`static-build.json` and `route-manifest.json` report
`localEvidenceStatus: provisional-only` and `activationEligible: false` for it and
always will. It cannot be relabelled: changing a byte of its attestation changes
the attestation digest its own release descriptor pins.

## Active immutable production snapshots

`verify-production-output.mjs` requires `activationEligible === true` and a
non-provisional evidence status. `scripts/lib/production-activation.mjs` is the
only producer of that verdict, and it is deliberately narrow:

- A snapshot becomes activation-eligible **only** by matching an entry in the
  frozen `PRODUCTION_ACTIVATION_REGISTRY` constant. That registry is compiled into
  the build. It is never read from the environment, a CLI flag, a build
  argument, the snapshot payload, or an object store. Adding an entry is a
  explicit source change on `main`; the immutable record is the activation
  mechanism.
- The registry ships **empty**. Nothing is active by default.
- An active snapshot carries a second closed contract:
  `verdify.lab-production-sanitized-snapshot` in `attestation.json` plus a
  canonical `activation.json` (`verdify.lab-production-snapshot-activation` v1).
  The activation's SHA-256 must equal the registered `activationSha256`, every field
  must equal the registry entry, and the sanitized content-manifest digest,
  source-capture manifest digest, file counts, guard-report digest, sanitization
  policy version, and the snapshot's own attestation digest must all be
  reproduced from the snapshot on disk. An activation cannot be replayed onto a
  different capture, and one changed content byte invalidates it.
- The record names its provenance in the open: the authoritative source URI and
  capture instant, the activationActor (from a fixed authority list), a permalink to
  the source-controlled activation record, the activation instant, the
  immutable release tag, and the release asset digest.
- The production release descriptor
  (`verdify.lab-production-snapshot-release` v1) adds `activationSha256` and
  `activationId` and carries **no** hard-coded content pins — every pin must match
  the registered activation, so the bounded hydrator refuses to download an
  unactive production asset at all.
- Fixtures are excluded structurally: the fixture branch is mutually exclusive
  and the fixture payload layout forbids `activation.json`.

`verify-production-output.mjs` and the stage verifiers are unchanged by this
contract.

## Specialist evidence releases

The Astro compiler now emits `occurrence-manifest.json` for every discovered
Grafana and current-camera occurrence. Grafana records preserve the normalized
dashboard UID, panel, query multiplicity, variables, time range, semantic role,
cadence, and an opaque occurrence ID. Camera records expose only an opaque
occurrence ID, semantic role, stable same-origin target, cadence, and opaque
occurrence, exact-policy, and active-request digests. The upstream camera URL is
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
`LAB_OCCURRENCE_STORE` to that read-only store and set `LAB_OCCURRENCE_POLICY` to the
canonical JSON file for the exact active export policy that produced the selected
release. The store is fail-closed without that policy, and a selected release cannot
use a policy that remains blocked. The compiler revalidates the selected manifest and
decoded blobs, then binds the release to the snapshot-manifest SHA-256,
the export-policy version, and the SHA-256 of the complete canonical policy bytes. The
snapshot sanitization-policy version is a separate contract and is not used for this
binding. The policy's source-occurrence digest is checked against a stable discovery
projection with the top-level selection and every per-occurrence selection set to null,
so decorating served evidence cannot change the active discovery identity. A selected
build is accepted only with fallbacks for every discovered graph and current-camera
occurrence. Before copying any blob, the compiler also rechecks each selected discovery
fingerprint against the exact policy allowlist, each camera request-provenance digest,
and the graph/current-media MIME, encoded-byte, width, and height bounds. The compiler
copies only referenced content-addressed images into the static release, renders them as
the inline same-origin fallbacks, and preserves the separate interactive evidence link.
`static-build.json` uses the repository-wide
`sha256:<64-hex>` digest form for `selectedOccurrenceManifestSha256`; the v1
`occurrence-manifest.json` contract retains the raw lowercase 64-hex digest in
`selectedManifestSha256`. The production verifier cross-checks the two representations.
An absent `LAB_OCCURRENCE_STORE` remains explicit pending evidence, does not require a
policy, and never causes a network render during the build.

This is release tooling and fixture proof, not live export authority. The current
stage image was built before these contracts and still has no selected occurrence
release. A future stage rollout requires a new fleet-origin image/pin and the normal
stage GitOps durability probe. Production Lab cutover, a reporting-store grant,
camera sanitizer authority, public routing, and Quartz retirement remain separately
gated. In particular, nginx does not yet resolve `/evidence/current/<id>` or watch
these local selectors: a deployed no-build pointer update/rollback still requires the
future runtime/object-store adapter and end-to-end proof. No service restart is
required by this source-only change.

After a separate stage rollout has selected and materialized an occurrence
release, run the GET-only live acceptance check at T0 and T+10:

```bash
npm run verify:live:occurrences -- --origin https://lab-stage.verdify.ai
```

To verify one unrouted release-runtime pod through a local port-forward while
retaining the public attestation identity, supply a credential-free transport
origin:

```bash
npm run verify:live:occurrences -- \
  --origin https://lab-stage.verdify.ai \
  --transport-origin http://127.0.0.1:18080
```

The verifier follows no redirects and fetches only the supplied origin's build and
occurrence manifests plus their canonical same-origin content-addressed PNG paths. It
requires all 143 graph and both camera fallbacks, reconciles the raw/prefixed selected
release identities, and checks immutable cache headers, exact byte counts, and byte
digests. The optional transport changes only the connection origin: the documents and
asset paths must still attest exactly `https://lab-stage.verdify.ai`. Explicit
transport is limited to canonical literal loopback hosts in `127.0.0.0/8` or `[::1]`
for a local per-pod port-forward. DNS names, other IP addresses, credentials, and any
path, query, or fragment are rejected. Requests follow no redirects, omit credentials
and referrers, use only the connection's Host, and do not send an Origin header. It is
acceptance tooling only: it does not render, publish, mutate a store, read a credential,
or activate a workload.

### Phase 4c producer boundary (inactive)

`scripts/prepare-occurrence-export.mjs` now closes the offline handoff that a
future specialist producer must satisfy. The validated, byte-bound policy at
`config/lab-stage-occurrence-export-policy.json` contains exactly 143 graph and
two opaque camera occurrence fingerprints from the accepted stage snapshot. A
producer batch must name the operator-owned, one-way, read-only public reporting
feed contract, bind the exact canonical policy SHA-256, carry its source watermark,
and include every active occurrence exactly once. The trusted compiler clock
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
store mutation. The active upstream handoff is
GET-only to the two exact
`api.verdify.ai/api/v1/public/cameras/.../latest.jpg?h=1080` paths; redirects,
cookies, authentication, device/VLAN access, Frigate, and go2rtc are forbidden,
and JPEG input must be decoded and cleanly re-encoded without metadata before it
enters the candidate directory.

The checked-in policy has `activation.state=blocked`. Validation is available,
but request preparation refuses it. The matching Kubernetes Component under
`deploy/k8s/components/lab-occurrence-reporting-boundary/` is referenced by no
overlay, defines no workload or Secret, and is deny-all. A separate safety-checked
change must create and validate the isolated reporting feed/tier, its
least-privilege credential, occurrence-store access, and egress limited to that
store plus `api.verdify.ai:443`. Existing anonymous `graphs.verdify.ai` and the
Track A primary database role are explicitly ineligible.

The inert graph-producer library adds one pure planner that verifies the exact
occurrence-manifest bytes against that policy and emits all 143 render targets in
manifest order without an endpoint. Its producer has no default renderer or network,
service, credential, database, Kubernetes, Grafana, or object-store client: an
active policy and an explicitly injected renderer are required before any call.
The producer also requires the full closed reporting-feed envelope and derives its
canonical digest itself. Only that digest enters the v3 plan and all 143 requests;
the feed identity, watermark, endpoint, and credential details do not. The injected
renderer must declare the same digest through the exact abort-cooperative v3 contract
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

The source-only graph batch assembler now completes that offline 143+2 handoff. It
accepts the full reporting-feed envelope, an explicit dedicated datasource identity,
the injected v3 renderer, and aggregate/per-camera selector preconditions. The raw
datasource identity is never written to a plan, result, batch, log, or digest evidence;
only its domain-separated SHA-256 crosses the render boundary. Every request declares
whether it uses the reporting-tier default or needs the dedicated legacy-dashboard
override. The latter is closed to exactly `greenhouse-weather`,
`greenhouse-equipment`, `greenhouse-hydroponics`, `greenhouse-lighting`, and
`greenhouse-soil` (40 occurrences in the accepted 143-occurrence snapshot). Reusing
the legacy `verdify-tsdb` or `P44368ADAD746BC27` identity, an anonymous source, or a
URL-shaped identity fails before a renderer call.

After rendering, the assembler emits two separately digested canonical documents:
the existing URL-free v3 graph result and the complete v2 occurrence-export batch.
The latter carries the opaque reporting-feed watermark, all 143 graph records, both
camera records in manifest order, the aggregate selection precondition, and both
per-camera selection preconditions. Mixed graph failures remain complete and retain
their classified null records for downstream LKG handling. No HTTP adapter is
provided: renderer transport/configuration remains injected, with no environment,
endpoint, credential, store, workload, route, replica, or activation binding in this
source slice. The checked policy remains blocked, so these contracts cannot make a
live render or publish until the existing execution safeguards are separately satisfied.

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

The release-store operation surface now has both the existing local implementation and
an inactive `S3SiteReleaseStore` foundation. Store locations are parsed strictly as a
local path or `s3://bucket/non-empty-prefix`. The S3 adapter uses absent-only writes for
immutable objects, entity-tag compare-and-swap for the selector, bounded streamed
reads, and bounded paginated listing. Its client is dependency-injected and covered by
offline fakes; no endpoint or credential is configured or contacted by the tests.

Specialist evidence has a separate inactive `OccurrenceReleaseStore` surface. Its
local adapter preserves the existing aggregate/per-camera layout, while the injected
S3 adapter binds every key beneath the typed `occurrence-releases/v1` namespace. Both
selector families keep caller SHA-256 preconditions; S3 compare-and-swap uses the
entity tag from the immediately preceding read. Manifests, media generations, event
intents, and validated PNG blobs are absent-only and collision checked; event intents
must carry the adapter's canonical store-identity digest. Bounded blob reads can
materialize exact bytes for a compiler, but the occurrence CLI still accepts local
stores only and no event producer is wired to mutate S3.

Occurrence blob materialization is monotonic and delete-free at its public destination.
Every selected blob is first written, synced, closed, and fully PNG/digest/size validated
inside one private same-filesystem staging directory; no destination is committed until
all source blobs pass. Commit uses an absent-only hard link. An existing destination is
accepted only when bounded validation proves the exact expected content, while a conflict
is left untouched. A later commit or directory-sync failure may therefore leave only exact
content-addressed outputs; retry converges by accepting those outputs and completing the
remainder. Cleanup removes private staging only and never rollback-deletes a destination.

The separate source-only occurrence-store binding-readiness contract carried by #501
fixes the five future environment key names and three resource names without reading
any value. It assigns non-secret location/endpoint/region key names to ConfigMap
`verdify-lab-occurrence-store-metadata`, and access-key key names to distinct
`verdify-lab-occurrence-store-reader` and
`verdify-lab-occurrence-store-writer` Secrets. It explicitly rejects reuse of the
legacy Quartz publisher Secret. The offline CLI consumes a canonical JSON name
inventory rather than `process.env`; strict `{kind,bucket,prefix}` metadata is
validation-only and is omitted from its names/status-only output:

```bash
npm run object-store:binding-readiness -- --inventory /path/to/name-inventory.json
```

This is not a Kubernetes existence check, value check, endpoint probe, credential
grant, or runtime binding.

The runtime candidate is present in Lab stage GitOps source only as a dormant
`replicas: 0` workload. Its paired agent/site images are pinned to exact zot digests
after their source-bound container probe, but it still has no route, object-store/AWS
environment, application/object-store credential, or egress. Its standard zot registry
`imagePullSecret` is only for image retrieval. The stage app remains manual-sync;
merging source is not an operator sync, and even a sync cannot schedule this
zero-replica candidate.

The CLI and cache hydrator remain local-only. A source-only S3 coordinator now
implements same-bucket/non-overlapping-prefix enforcement, pre-I/O inventory
reservations, distributed fencing, 14-day idempotency records, 48-hour reachability
GC, and daily resource accounting. Its closed site-publisher checkpoint contract must
still be imported by the executable publisher writer when that branch is refreshed.
The operator proof now also verifies that a second absent-only write cannot replace
an existing object, but endpoint correctness is not capacity authorization. The
current per-file 143+2 format is machine-readably activation-blocked: at 96 full
publications/day its strict 48-hour occurrence payload alone reaches 28,564 objects,
above the 25,000 complete-inventory cap, and its canonical reads exceed the daily
request budget. Deterministic occurrence/site packs and selected-root inventory must
land before this gate can open; the 96-sample reporting freshness KPI does not imply
96 full object publications. Object-store CLI wiring and bounded site cache
materialization, the event agent, real-endpoint proof, actual resource/value binding,
store/egress wiring, and alert routing also remain required. The #501 slice fixes
names only; none of this source configures an endpoint or credential, deploys a
binding, activates a writer, or alters production.

The dormant coordinator also still accepts the publisher payload estimate/result
from its injected caller. Its own reservation and fence overhead is always added,
but activation additionally requires the executable writer to derive the full
payload envelope, import the closed checkpoint contract, and enforce the fence at
the actual mutation boundary.

The source tree now contains the first deterministic packed-layout prerequisite.
`scripts/lib/deterministic-release-pack.mjs` uses fixed `VLABPACK`/v1 framing, one
canonical sorted JSON index, exact uncompressed byte frames, and no time, ownership,
mode, or link metadata. It rejects malformed framing, unsafe/duplicate/colliding
paths, links, digest mismatches, and every configured size/count overrun before
materializing a newly created tree. `packed-release-selected-root.mjs` makes one
current/rollback root select a complete occurrence-pack plus site-pack pair, never
one side independently.

`packed-release-capacity.mjs` deterministically simulates 16 days and 1,536 events.
With the documented retention and attempt envelopes it proves 4,328 retained objects
and 17,672 requests/day. Real pack and control-object byte sizes remain mandatory
inputs to the 10-GiB retained, 5-GiB/day write, and 10-GiB/day egress gates. Tests also
hydrate an occurrence pack back to all 143 graph and two camera
`/evidence/blobs/sha256/<digest>.png` files. This is source-only format and capacity
proof: it does not wire S3, mutate a selector, start a publisher, deploy a workload,
or establish live two-cache convergence.

The stage vendors the validated offline parity comparator at
`scripts/site-build-parity.py` (SHA-256
`9ffa966a8d36a1a98a56b941588a340f2e23cc7ee38389e1056717204fadf92c`):

```bash
LAB_SNAPSHOT=.snapshot npm run parity:manifest
LAB_SNAPSHOT=.snapshot \
QUARTZ_MANIFEST=/tmp/verdify-lab-quartz-baseline-20260712t1620z.json \
npm run parity:compare:provisional
```

The baseline itself has known integrity blockers. A successful structural
diagnostic is not a baseline, canary, cutover, or production activation.

For container-only fixture diagnostics, select the non-default target
explicitly: `docker build --target fixture-runtime -f Dockerfile .`. That image
is not release input.

## Shared-shell boundary

The build vendors the validated `@verdify/site-shell` 1.1.0 release from WWW
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
npx playwright install --with-deps chromium
npm run test:browser
```

The cross-browser media gate prepares only Chromium and WebKit with the same
`--with-deps` flow; it never installs Firefox:

```bash
npm run test:media:cross-browser
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

- The sanitized legacy capture remains provisional-only; the future active
  immutable filesystem/object-store evidence attestation is not implemented.
- Digest-verified local Grafana fallback images remain absent from the frozen
  snapshot. The compiler and specialist-release store now enforce decoded,
  content-addressed current/previous selection and last-known-good retention, but
  a policy-active exporter/reporting source has not populated a selected release.
- Current-camera occurrences now have an opaque same-origin manifest contract, but
  the camera sanitizer and independently served occurrence pointer are not deployed.
- Planner event idempotency, freshness, conditional promotion, local pointer rollback,
  verified cache hydration, and baked outage fallback are implemented and tested; no
  bounded event delivery authority, freshness histogram, or firing alert is connected
  to stage yet.
- The complete built-tree local store implements reachability GC, maximum-ten
  retention, and the ratified 10 GiB cap. The inactive S3 foundation implements
  bounded conditional store primitives and an offline-tested distributed coordinator,
  but executable publisher integration, CLI/caller wiring, and acknowledged
  real-endpoint proof remain.
- The built-tree publisher lease recovers a provably dead same-host PID and excludes a
  live local owner. The S3 coordinator provides the source-only cross-pod fence; no
  active workload invokes it yet.
- The frozen Quartz baseline has known HLS, alias, feed/sitemap, missing-asset,
  and fallback findings and is not clean activation evidence.
- The WWW shell release is source-only until its producer change is merged; the Lab
  pins its exact validated bytes and does not claim producer merge or cutover.
- The zero-finding public-data guard report is mandatory snapshot input, but a
  public canary still requires the separate activation safeguards.
