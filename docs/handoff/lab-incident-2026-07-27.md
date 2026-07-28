# Lab incident 2026-07-27 — artifacts and open threads

Tracking: [#551](https://github.com/VerdifyConsultancy/verdify-platform/issues/551).
Landed: [#552](https://github.com/VerdifyConsultancy/verdify-platform/pull/552).
Open: [#553](https://github.com/VerdifyConsultancy/verdify-platform/pull/553),
[jvallery/agents#3049](https://github.com/jvallery/agents/issues/3049).

## What happened

lab.verdify.ai served Quartz's stock documentation from 2026-07-26T21:43Z until
2026-07-27T~13:30Z. Two independent defects, not one:

1. **The publish path was wedged.** `scripts/check-public-output.py` scans the whole
   content corpus and gates promotion, but `scripts/publish-site-content.sh` only
   regenerates `PREV_DATE`/`DATE`. `f17e30f` (2026-07-12) added that guard *and* fixed the
   two defects it detects — crop redaction in `public_text()` and the `USD None` currency
   rendering — but production's publisher image pin never advanced past 2026-07-11. So 13
   days of content was generated pre-fix and published ungated, and the first run on the
   fixed image fail-closed on its own predecessor's output (22 findings).
2. **The baked fallback was the wrong site.** The cache PVC was recreated
   2026-07-26T09:35Z; the Deployment's init seeded the `verdify-lab` image's baked tree,
   which is Quartz's own manual. The content-policy scanner passes it — nothing in it is
   *prohibited*, it is simply not this site.

Three of the 22 findings were a live non-public-crop leak on the public site, exposed from
2026-07-12 until repair.

## Files here

| file | what it is |
| --- | --- |
| `../../scripts/repair-legacy-lab-content.py` | The one-time corpus repair actually executed against the durable S3 content store (21 files). Idempotent; a second run reports 0 changes. Kept for auditability — it mutated published data. |
| `verdify-site-legacy-lab-image-identity.patch` | Corrected `Dockerfile.k3s` for the sibling repo. **Unpushable**: `verdify-site-legacy` is archived (read-only, 403). Held here so the work is not lost. |
| `lab-rollout-baseline-20260727.txt` | Pre-rollout capture: manifests, pod UIDs, digests, node placement, PVC identity and modes, DB restarts, ingestor baseline. |

### Byte-sensitive embedded patch verification

`verdify-site-legacy-lab-image-identity.patch` is an exact `git format-patch`
artifact. Its context lines and `-- ` signature terminator contain
format-significant trailing spaces, so a whole-diff `git diff --check` reports
that one file even when the patch is intact. The exception is deliberately
narrow:

1. Run the normal whitespace gate while excluding only the byte-sensitive
   artifact; every other changed path remains covered:

   ```bash
   git diff --check origin/main...HEAD -- . \
     ':(exclude)docs/handoff/verdify-site-legacy-lab-image-identity.patch'
   ```

2. Verify the embedded patch SHA-256 is
   `6abe0f89da7761f9f111bf85b91f9640f3c894b41945963fbaf22c366c076473`.
3. Run `git apply --check` with the byte-exact artifact against archived
   `VerdifyConsultancy/verdify-site-legacy@15f947a0d86f0ecb8867ff4d89c9d3d692f420ef`
   (`v4`):

   ```bash
   shasum -a 256 docs/handoff/verdify-site-legacy-lab-image-identity.patch
   git -C /path/to/verdify-site-legacy apply --check \
     /path/to/verdify-platform/docs/handoff/verdify-site-legacy-lab-image-identity.patch
   ```

The pre-commit configuration excludes only
`docs/handoff/verdify-site-legacy-lab-image-identity.patch` from the two
whitespace-mutating hooks (`trailing-whitespace` and `end-of-file-fixer`);
future or unrelated `.patch` files remain covered. YAML/JSON, merge-conflict,
and private-key checks are not relaxed, and the patch-application proof
replaces—not removes—the integrity check for this artifact.

## Open threads

- **The `verdify-lab` fallback cannot be rebuilt.** `verdify-site-legacy` is archived and its
  `content` entry is a git symlink to `/mnt/iris/verdify-vault/website` — the decommissioned
  iris VM — so every build fell through to `cp -r docs content`. Needs a decision: unarchive
  that repo, relocate the Lab image build into this one, or drop the baked fallback. Until
  then a cold cache is **fail-closed, not self-recovering**, which `is_lab_site_tree()`
  in `scripts/prepare-lab-cache.sh` enforces deliberately.
- **The corpus guard remains a wedge risk.** Repairing 2026-07-26 and giving `cICP` a parser
  cleared *that* instance; neither removes the structural risk. The durable fixes — a pre-pin
  canary running a candidate publisher image against the whole historical corpus, and
  versioned idempotent migrations for the derived plan archive — are unimplemented.
- **Production is not yet on the v2 layout.** `main` carries it (nginx
  `root /lab-cache/publisher/public`, whole-PVC mount, node6 pin); prod still runs v1
  (`subPath: public`). The publisher CronJob is deliberately **suspended** — resuming it on
  the stale pre-guard image would re-introduce the crop leak. Needs a resource-scoped ArgoCD
  sync by an operator.
- **All `agent-fleet-ci` build steps are wedged** (three repos, `Init:0/3`, own 20Gi RWO
  Longhorn `workdir` PVC each), so no hardened publisher digest exists yet.

## Two traps worth remembering

- **Never mount the lab cache PVC with a bare `fsGroup`.** Kubelet's recursive relabel adds
  group-write and setgid, and `scripts/prepare-lab-cache-lock.py` requires `locks/` to be
  exactly `0700` and `publish-wrapper.lock` exactly `0600`. Use
  `fsGroupChangePolicy: OnRootMismatch` — verified by probe not to relabel. The *live* v1
  Deployment has no such policy, so any restart today re-breaks the modes.
- **The served directory is an inode-pinned `subPath` bind mount** under v1. Sync *into* it;
  never replace the directory, or nginx keeps serving the retired inode.
