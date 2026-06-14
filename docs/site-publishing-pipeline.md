# Verdify Site Publishing Pipeline

This is the operator trace for publishing `lab.verdify.ai` from curated
Markdown/static content into the k3s-served Quartz site.

## Source of Truth

As of 2026-06-14, durable lab content/public/state lives in S3-compatible object
storage. The bucket is provided by Secret `verdify-lab-publisher-s3`; the
default prefix is `lab`.

```text
s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/content/  # Markdown + static source tree
s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/public/   # generated Quartz public tree
s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/state/    # publish/build logs and context
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
  -> sync /work/content, /work/public, /work/state back to S3
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
curl -fsSL https://lab.verdify.ai/plans/"$(date +%Y-%m-%d)"
```

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
