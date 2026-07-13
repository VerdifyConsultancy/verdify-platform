# Verdify Site Publishing Pipeline

This is the operator trace for publishing `lab.verdify.ai` from curated
Markdown/static content into the k3s-served Quartz site.

Production still uses the Quartz path documented below. The isolated Astro
candidate at `lab-stage.verdify.ai` is a separate, provisional image and must not
be treated as production authority or as permission to retire this path.

Status 2026-07-13: the accepted static Astro stage runs digest `ee36941f…`
(shell 1.1.0) after its in-cluster gates and T0/T+10 checks passed. The local
release/cache runtime is now represented in the Lab stage GitOps source only as
a disconnected dormant workload: `replicas: 0`, zero-digest agent/site image
sentinels, no route, no object-store/AWS environment, and no egress. It has not
been activated or synced by this source change. Program tracker:
`docs/plans/lab-astro-migration.md`.

## Astro specialist-occurrence release contract (source-only)

The Astro candidate owns an offline specialist-evidence release interface under
`site-astro/scripts/lib/occurrence-release.mjs`. It is intentionally separate
from the current Quartz publisher and does not query the database, Grafana,
camera API, S3, or any other network service. A future bounded exporter supplies
one canonical local request containing only event metadata, approved occurrence
metadata, and local sanitized candidate paths.

The contract provides:

- opaque, stable occurrence IDs for normalized Grafana and current-camera
  occurrences;
- same-origin content-addressed PNG fallbacks validated by PNG structure and
  checksums, bounded decompression, complete scanline reconstruction, dimensions,
  encoded SHA-256, and decoded-pixel SHA-256;
- last-known-good retention when a render, capture, policy check, or decode fails;
- one atomic `selection.json` containing `current` and `previous` release-manifest
  digests, a monotonic generation, selection time, and reason;
- a separate current/previous CAS selector for each current-camera occurrence;
- compare-before-promote semantics keyed by the complete selection digest;
- durable pre-selection event intents and idempotent triggers keyed by event ID,
  payload digest, and event-envelope digest;
- planner completion freshness evaluation at a five-minute target and a
  fifteen-minute alert threshold; and
- local pointer-only rollback to `previous` without regenerating evidence.

The Astro compiler always emits `occurrence-manifest.json`. With no selected
specialist store, every occurrence is explicit pending evidence. When
`LAB_OCCURRENCE_STORE` names an already-verified read-only store, the compiler
revalidates the selected manifest and decoded blobs, copies only the referenced
content-addressed images, emits local fallbacks, and keeps an independently
usable graph link. Current-camera public manifests include opaque occurrence,
exact-policy, and approved-request provenance digests, never the upstream URL.

The CLI is deliberately local and credential-free:

```bash
cd site-astro
node scripts/manage-occurrence-release.mjs publish --request /path/to/canonical-request.json
node scripts/manage-occurrence-release.mjs status --store /path/to/store
node scripts/manage-occurrence-release.mjs rollback \
  --store /path/to/store --expected <selection-sha256> --at <UTC-instant>
node scripts/manage-occurrence-release.mjs publish-media --request /path/to/media-request.json
node scripts/manage-occurrence-release.mjs media-status --store /path/to/store --occurrence <opaque-id>
node scripts/manage-occurrence-release.mjs rollback-media \
  --store /path/to/store --occurrence <opaque-id> --expected <selection-sha256> --at <UTC-instant>
```

The compiler's canonical `media/*.request.json` files are the closed v3 input to
`publish-media`; they require both `policySha256` and
`requestProvenanceSha256`. Its canonical `release.request.json` is the closed v2
input to `publish` and requires `policySha256`. Unknown fields—including source
URLs—and missing identity fields are rejected before store mutation.

Phase 4c adds a closed producer boundary without activating a producer:

```bash
cd site-astro
node scripts/prepare-occurrence-export.mjs validate \
  --manifest /path/to/occurrence-manifest.json \
  --policy config/lab-stage-occurrence-export-policy.json \
  --batch /path/to/canonical-export-batch.json \
  --source /path/to/sanitized-candidates
```

The checked-in policy is blocked and byte-binds the accepted stage occurrence
manifest: 143 graph fingerprints plus two opaque camera fingerprints. Every batch
also binds the exact canonical policy SHA-256. The compiler requires a named
operator-owned, one-way, read-only public reporting feed and rejects reuse of
anonymous `graphs.verdify.ai` or the Track A primary role. A trusted processing
instant limits delivery delay to 300 seconds and future skew to 60 seconds. Its
end-to-end source watermark has a p95 target of 900 seconds; greater than 1800
seconds is alert state and cannot prepare new release requests, so selected LKG
bytes stay in place.

The camera source contract is GET-only and exact:
`https://api.verdify.ai/api/v1/public/cameras/{greenhouse_1|greenhouse_2}/latest.jpg?h=1080`.
Redirects, cookies, auth, direct device/VLAN, Frigate, go2rtc, database, and
control access are forbidden. A domain-separated SHA-256 binds each opaque
occurrence to GET, its exact URL, and those redirect/auth/cookie rules in the
reviewed policy, batch, sanitized candidate, private generation, selection, and
reconciliation request. A generation is eligible for LKG only while its exact policy
and request digests match, so a same-version policy or URL mutation cannot retain it.
Only the opaque digests—not the URL—enter public release output. The future producer must decode each JPEG and
re-encode a metadata-free RGB/RGBA PNG named by its SHA-256. The offline compiler
then revalidates PNG structure, CRC, bounded inflate, decoded pixels, MIME,
dimensions, byte count, bounded PNG chunk cardinality, content-addressed name,
exact occurrence allowlist, and camera opacity before emitting canonical
per-camera then reconciliation requests. Event, publication, capture, verification,
selection, freshness, and rollback instants must round-trip as canonical UTC with
either whole seconds or exactly three milliseconds; impossible dates are rejected.

`deploy/k8s/components/lab-occurrence-reporting-boundary/` records the future
boundary but is deliberately inert: no overlay reference, workload, Secret,
Service, or Ingress, and deny-all egress. Request preparation also refuses the
blocked policy. A separate Jason-gated change must approve and provide the
reporting tier/feed, reporting-only credential, occurrence-store route, and
egress restricted to that store plus `api.verdify.ai:443`.

This implementation does not grant export authority or prove live freshness.
Before stage can select a real occurrence release, separate work must provide a
policy-approved reporting source and camera sanitizer that cannot reach the Track
A primary, plus durable delivery, alerts, outage probes, and the GitOps image/pin
rollout. Static nginx does not yet resolve the stable current-media targets. A
source-only merge needs no service restart. Any stage rollout needs the
normal stage acceptance and delayed durability probes; production sync, public
cutover, and Quartz retirement remain human-gated.

The specialist-occurrence fixture is separate from the complete built-tree release
store below. It retains ten occurrence manifests and two selected media generations,
but does not claim a deployed object-store adapter or distributed lease.

## Astro built-tree release and serving cache (local backend)

`site-astro/scripts/lib/site-release-store.mjs` treats one complete Astro `dist/`
tree as the release unit. It inventories a closed, sorted, case-fold-collision-free
tree; rejects symlinks, hard links, special files, excessive depth/count/size, and a
missing `index.html`; and imports every file as a SHA-256-addressed blob. The canonical
release manifest binds the exact source-snapshot manifest digest, publication-policy
version, builder commit, planner/event envelope and payload, timestamps/freshness,
and every output path, media type, byte count, and digest. A policy change therefore
cannot silently reuse the prior release identity. Specialist last-known-good evidence
also retains the policy version that approved those bytes when carried forward; it is
not relabeled as verified under a later policy.

Publication writes an immutable event intent before changing selection. Event ID,
envelope digest, payload digest, intended release, and expected selection digest are
bound together, so a retry after process failure completes the same operation without
forking it. One atomic canonical selector carries current and previous releases plus
a monotonic generation. Every update after the first requires the full selector SHA
precondition. Identical content is change-gated, and rollback only swaps current and
previous; neither operation rebuilds the site.

The exported `SiteReleaseStore` class documents the backend operation surface; the
credential-free `LocalSiteReleaseStore` implements it. The local backend has a
recoverable, same-host PID/nonce lease. A live or foreign-host owner excludes
concurrent publishers; a demonstrably dead same-host lease is atomically moved to a
nonce tombstone before reacquisition. Well-formed interrupted candidates, including
the second link left after immutable publication, are repaired under that lease. This
is explicitly not a distributed lease. Each publication retains at most ten manifests,
then evicts optional oldest releases until reachability-accounted bytes fit the 10 GiB
cap. Event tombstones remain so an evicted event ID cannot be reinterpreted as new work.

`site-astro/scripts/lib/site-release-cache.mjs` is the pod/local serving adapter. It
rehashes every source blob and every completed tree before installing a unique physical
generation, then atomically replaces a relative `current` symlink. A separate
same-host lease serializes the complete hydrate/swap/prune transaction. It preserves
the prior complete generation through `previous`, prunes older cache generations, and
leaves the served symlink untouched if hydration fails. A corrupt current store release
falls back to the verified previous release. An independently created, byte-verified
baked bundle is the cold-start known-good when the store is unavailable. Status reports
readiness, source/degraded fallback state, release identity, and planner freshness using
the five-minute target and fifteen-minute alert thresholds.

The credential-free CLI is:

```bash
cd site-astro
node scripts/manage-site-release.mjs prepare --build dist --snapshot <sha256> --policy <version> --commit <commit>
node scripts/manage-site-release.mjs publish --request /path/to/canonical-request.json
node scripts/manage-site-release.mjs status --store /path/to/store --at <UTC-instant>
node scripts/manage-site-release.mjs rollback --store /path/to/store --expected <selection-sha256> --at <UTC-instant>
node scripts/manage-site-release.mjs bundle --store /path/to/store --release <release-sha256> --destination /image/known-good
node scripts/manage-site-release.mjs hydrate --store /path/to/store --cache /srv/lab-cache --baked /image/known-good
```

The focused tests inject failures after blob import, manifest publication, event-intent
publication, and immediately before selection; exercise concurrent and dead-owner
leases; prove retry and rollback; enforce retention/reachability; corrupt selected
bytes; boot from baked fallback; and run all CLI operations end to end.

The complete local filesystem primitive is present, and its candidate manifest is
included by the Lab stage overlay only at `replicas: 0`. The overlay deliberately
retains separate zero-digest sentinels for the release agent and nginx site images;
the candidate has no route, object-store/AWS environment, credential reference, or
egress. Merging this source does not alter the live cluster because the Lab stage
ArgoCD app remains manual-sync. An operator sync is separately recorded and still
cannot schedule the dormant zero-replica workload. Object storage needs an
implementation of the exposed store semantics plus a real distributed
lease/conditional-write authority; activation additionally needs reviewed exact
image pins, store/egress wiring, an explicit replicas change, and live freshness
alert routing. Those are deployment/data-authority concerns, not hidden claims of
the local backend.

## Source of Truth

As of 2026-06-14, durable lab content/public/state lives in S3-compatible object
storage. The bucket is provided by Secret `verdify-lab-publisher-s3`; the
default prefix is `lab`.

```text
s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/content/    # Markdown + static source tree
s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/public/     # generated Quartz public tree
s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/state/      # publish/build logs and context
s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/manifests/  # per-tree content-hash manifests (delta-sync bookkeeping)
```

The in-cluster publisher syncs content to the `verdify-lab-site-cache` PVC at
`/work/content`, runs the existing generators, builds Quartz into `/work/public`,
and syncs content/public/state back to S3. The PVC is a k3s cache and live serve
surface, not durable source of truth.

Legacy generator paths such as `/srv/verdify/verdify-site/content` and
`/mnt/iris/verdify-vault/website` are compatibility symlinks inside the
publisher container.

Generated pages, such as `/data/forecast`, `/data/plans`, `/plans/index`,
`/plans/YYYY-MM-DD`, `/reference/lessons`, crop profiles, zone pages, equipment
blocks, and public sample datasets are written into the same website tree by
generator scripts. Do not hand-edit generated blocks or pages unless you expect
the generator to overwrite them later.

Production refreshes use one entry point inside the publisher image:

```bash
lab-publish-k3s
```

`lab-publish-k3s` wraps `scripts/publish-site-content.sh`. It regenerates the
daily plan, forecast page, plan indexes, lessons, Baseline vs Iris, equipment
blocks, zone pages, crop profiles, public sample CSVs, and planner static
context before rebuilding the site and uploading the result to S3.

Some public routes are aliases because the nav and story pages link to the
`/data/...` route while older URLs still exist:

```text
/evidence/baseline-vs-iris      -> data/baseline-vs-iris frontmatter alias
/forecast                       -> data/forecast frontmatter alias
/plans and /plans/              -> plans/index.md noindex compatibility stub
```

`make site-doctor` checks forecast freshness from `last_updated`, verifies that
the canonical plan index lists the newest `plans/YYYY-MM-DD.md` page first,
verifies the `/plans/` stub does not duplicate the archive table, rejects
duplicate route owners, and rejects retired source paths such as the old
`intelligence/`, `slack/`, `/forecast/index.md`, and duplicate top-level article
copies. A stale generated route is a release-blocking site-doctor error, not a
visual cleanup task.

## Publish Flow

```text
Curated website content
  -> S3 content prefix
  -> verdify-lab-publisher CronJob in k3s
  -> scripts/publish-site-content.sh for generated refreshes
  -> scripts/rebuild-site.sh
  -> npx quartz build --output /work/builds/public.*
  -> rsync staged output into /work/public
  -> delta-sync /work/content, /work/public, /work/state back to S3 (content-hash gated)
  -> verdify-lab nginx reads /work/public through the lab cache PVC
  -> Traefik / Cloudflare / lab.verdify.ai
```

## Low-Downtime Publish

Quartz clears its output directory before emitting a new site. Building directly
into the live `public/` directory creates a short window where nginx can serve
404s for normal pages. Verdify now avoids that by building into a temporary
staging directory under:

```text
/work/builds/public.*
```

Only after Quartz succeeds and `index.html` exists does the rebuild script sync
the staged output into the live public directory:

```text
/work/public
```

The sync uses delayed deletes, so existing pages stay available while new files
copy into place. The `verdify-lab` nginx container serves the PVC read-only and
does not need S3 credentials.

## Delta Uploads to S3 (content-hash, change-gated)

The S3 `public/` tree is a **durable mirror only** — nginx serves the PVC, and
the publisher only ever downloads `content/` (never `public/`) to hydrate a cold
PVC. That mirror used to be written with `aws s3 sync … --delete`, which decides
what to upload by comparing **size + mtime**. Because the Quartz rebuild
regenerates the whole `public/` tree every run, every file got a fresh mtime and
the full ~400 MiB site re-uploaded to the (HDD-backed) endpoint every 10 minutes
even when the rendered bytes were identical — pure write pressure on a saturated
endpoint.

`lab-publish-k3s` now uploads through `scripts/s3-delta-sync.py`, which drives
uploads off a per-file **SHA-256 manifest** instead of mtime:

- Walks the local tree → `{relpath: sha256}` and compares against the manifest of
  what was last uploaded (PVC cache `/work/manifests/<tree>.json`, else the S3
  copy under the `manifests/` prefix, else cold).
- **Change gate:** if nothing differs, it uploads **zero** objects (the common
  every-10-minutes no-op case).
- Otherwise it uploads **only** the changed/new files (one
  `aws s3 cp --recursive` over a hardlink staging tree of just those files) and,
  with `--delete`, prunes keys whose local file vanished.
- Persists the new manifest to the PVC and S3 so the next run is a true delta. A
  rescheduled (wiped) PVC falls back to the S3 manifest, so it still deltas
  instead of re-uploading the whole tree.

The manifests live under their own `manifests/` prefix (outside
content/public/state) so they never feed back into the walk. Steady-state runs
now move only the handful of HTML pages whose content actually changed (e.g. a
regenerated `generated at` timestamp) instead of the entire tree.

## Change Detection

The k3s CronJob runs every 10 minutes:

```bash
kubectl -n verdify-prod get cronjob verdify-lab-publisher
```

State files:

```text
/work/state/site-build-last-run  # last successful build marker
/work/state/site-build.log       # Quartz build log
/work/state/publish.log          # generator publish log
/work/builds/                    # temporary staged build output
```

The CronJob has `concurrencyPolicy: Forbid`; if a build is still running, the
next scheduled run is skipped by Kubernetes. The shell scripts also use flock
locks under `/work/locks`.

Manual jobs created with `kubectl create job --from=cronjob/...` are separate
Jobs, so they can overlap a scheduled run. For a clean manual proof, first
confirm no publisher pod is active or temporarily suspend the CronJob. In k3s,
`lab-publish-k3s` sets `VERDIFY_PUBLISH_LOCKED_RC=75`; if the publish lock is
held, the wrapper exits before syncing any cache content back to S3.

## Normal Checks

Use this first when the public site is stale:

```bash
kubectl -n verdify-prod get cronjob/job/pod -l app.kubernetes.io/component=lab-publisher
```

Then check the latest publisher pod logs:

```bash
POD=$(kubectl -n verdify-prod get pod -l app.kubernetes.io/component=lab-publisher \
  --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}')
kubectl -n verdify-prod logs "$POD"
```

Run an immediate one-shot publish from the CronJob template:

```bash
kubectl -n verdify-prod create job --from=cronjob/verdify-lab-publisher \
  "verdify-lab-publisher-manual-$(date +%Y%m%d%H%M%S)"
```

The S3 Secret must provide:

```text
LAB_S3_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION
LAB_S3_ENDPOINT_URL  # optional, for non-AWS S3-compatible stores
```

For the current local S3-compatible object store, use bucket
`verdify-platform`, signing region `garage`, and endpoint
`https://s3-hdd.vallery.net`. Prod uses `LAB_S3_PREFIX=lab`; dev patches the same
ConfigMap to `lab-dev` so dev cannot overwrite public prod output. Use the
Verdify-scoped key, not another app's S3 credentials.

Validate the built site:

```bash
make site-doctor
```

For generated planning and forecast pages, also confirm the nav-facing routes:

```bash
curl -fsSL https://lab.verdify.ai/data/forecast/ | rg '[0-9]{2}-[0-9]{2} [0-9]{2}:00'
TZ=America/Denver curl -fsSL https://lab.verdify.ai/plans/"$(date +%Y-%m-%d)"
```

The publisher pins `LAB_LOCAL_TIMEZONE`/`TZ` to `America/Denver` because public
daily plan pages are greenhouse-local records. Scheduled k3s runs also remove
auto-generated `/plans/YYYY-MM-DD.md` pages later than the local publish date so
a UTC rollover cannot publish tomorrow's empty plan stub.

## Debugging Content Edits

If a hand-authored edit does not show up, first confirm it reached the S3 content
prefix:

```bash
aws s3 ls "s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/content/"
```

If the text exists in S3 but not in `/work/public`, the issue is Quartz
build/publish. Check the latest publisher pod logs and create a one-shot job from
the CronJob template.

If the generated HTML is correct in the PVC but `lab.verdify.ai` is stale, the
issue is serving/cache. Check:

```bash
curl -I https://lab.verdify.ai/
curl -I https://lab.verdify.ai/static/contentIndex.json
```

Expected freshness headers for HTML, Quartz extensionless routes, JSON indexes,
CSS, and JS are `Cache-Control: no-cache, no-store, must-revalidate`. These are
served by the `verdify-lab-nginx-config` ConfigMap mounted into the
`verdify-lab` pod. From the in-cluster/LAN path, responses should show nginx
headers; if a WAN path shows Cloudflare cache headers, investigate the
Cloudflare tunnel/rules before changing the publisher.

Only restart `verdify-lab` if nginx is serving errors while
`/usr/share/nginx/html/index.html` exists in the lab pod.
