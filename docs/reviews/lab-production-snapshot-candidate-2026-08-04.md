# Verdify Lab — Candidate Production Snapshot & Sanitization Report (2026-08-04)

Issue: #570. **Status: CANDIDATE. NOT APPROVED. NOT CAPTURED.**

This document is the decision package for the first approval-eligible immutable
Lab production snapshot. It records the authoritative source, a real inventory
of that source taken today, the sanitization plan, and the privacy / identity /
content-rights / occurrence-selection reasoning #570 requires.

It does **not** approve anything. Jason is the recorded approval authority for
the snapshot. Nothing in this document, and no entry in
`site-astro/scripts/lib/production-approval.mjs`, becomes trusted until he
records an explicit approval and a separate reviewed PR adds the matching
registry entry.

The snapshot **bytes have deliberately not been captured**. Section 7 states
why, and section 8 states exactly what is required to capture them.

## 1. Authoritative source

The legitimate current authoritative source is the Lab content prefix that
publishes `lab.verdify.ai`:

```
s3://$LAB_S3_BUCKET/lab/content/
```

It is synced by the `verdify-lab-publisher` CronJob (ns `verdify-prod`, every
10 minutes, `scripts/lab-publish-k3s.sh`) and mirrored onto the
`verdify-lab-site-cache` PVC at `/work/publisher/content`. That tree — not the
built Quartz output, not the retired `/lab-cache/public` directory, and not the
existing GitHub release asset — is what a new capture must be taken from.

The frozen 2026-07-12 capture (`lab-stage-snapshot-20260712t1620z`, 429 files,
attestation `a5ed1cc8…`) is the **legacy provisional** input. #570 forbids
relabelling it, and the shipped code makes relabelling impossible. It is
superseded here, not reused.

## 2. Inventory of the live source (real evidence)

Taken read-only from the running `verdify-lab` nginx pod, which mounts the cache
PVC read-only. Metadata only; no content was copied off-cluster.

```bash
ssh jason@192.168.30.31 'sudo k3s kubectl -n verdify-prod exec <verdify-lab-pod> -c lab-site -- \
  sh -c "cd /lab-cache/publisher/content && find . -type f -exec stat -c \"%Y|%s|%n\" {} + ; du -sb ."'
```

Snapshot 2026-08-04T15:57Z, re-observed identical at 2026-08-04T16:04Z:
**471 files, 416,116,282 bytes (~396.7 MiB), 22 directories, 0 symlinks.** The
publisher's own `manifests/content.json` independently reports 471 entries.

| Top-level | Files | Bytes |
|---|---:|---:|
| `static/` | 292 | 405,087,464 |
| `plans/` | 130 | 9,305,269 |
| `start/` | 9 | 1,105,815 |
| `greenhouse/` | 24 | 203,171 |
| `reference/` | 8 | 194,454 |
| `data/` | 6 | 107,897 |
| root (`index.md`, `robots.txt`) | 2 | 13,908 |

By extension: `.ts` 171 / 267.8 MB, `.md` 175 / 9.9 MB, `.csv` 64 / 14.0 MB,
`.jpg` 38 / 20.8 MB, `.mp4` 2 / 102.0 MB, `.png` 5 / 1.1 MB, `.m3u8` 4,
`.json` 2, `.js` 3, `.txt` 3, `.css` 2, `.svg` 1, `.jpeg` 1.

`static/video/` is **179 files / 370,069,481 bytes**, every one with mtime
2026-06-29 — bit-frozen, and exactly the `hlsFilesPreserved: 179` the 2026-07-12
attestation records. `static/vision/` is 2 files / 429,290 bytes.

**Delta vs the 429-file frozen capture: +42 files, none in `static/video/`.**
The growth is two daily-accreting generated series — dated `plans/*.md`
(2026-03-24 → 2026-08-04) and dated hourly-performance CSVs under
`static/data/hourly-performance/` (20260518 → 20260804), roughly two files per
day. The source has no new *material class*, only more of the same series. Note
that this count drifts by design: it is an inventory, not a durability claim.

## 3. What the sanitization must do

The 2026-07-12 capture changed 8 of 429 files: 3 text redactions, 3
invalid-value repairs, 3 PNG re-encodes (one file carried two classes), and
preserved 179 HLS files byte-for-byte. The same four transformation classes
apply. The zero-finding gate is `scripts/check-public-output.py` (schema v2,
`--json-report` → `evidence/public-output-guard.json`).

Automated content scan of the live tree (counts only; no values read out) found
**zero** hits across all `.md`/`.csv`/`.json`/`.txt`/`.js`/`.css` for:
email-like strings, phone-like strings, AWS `AKIA` key ids, PEM private-key
headers, RFC1918 addresses, `.local`/`.internal`/`svc.cluster.local` hostnames,
street-address-like strings, latitude/longitude pairs, and
`bearer`/`api_key`/`password`/`access_token` keywords. Zero markdown files
mention the operator given names. There is no `private/`, `secret/`,
`credential/`, `.env`, `.bak`, `.orig`, or draft residue in the tree.

That is the good news: the tree is already close to publishable, which is why
the previous capture only needed 8 changes. The remaining questions are not
keyword-detectable.

### 3.1 Include

- All `.md` route content under `plans/`, `greenhouse/`, `reference/`,
  `start/`, `data/`, and the root `index.md`. All of it is served publicly at
  `lab.verdify.ai` today.
- The 179 `static/video/` HLS files, preserved byte-for-byte (no re-encode —
  re-encoding would break segment digests and the `hlsFilesPreserved` count).
- The hourly-performance CSVs and the two `.json` indexes.
- `robots.txt`, replaced by the compiler with the canonical production policy.

### 3.2 Redact, re-encode, or exclude — and why

| Item | Class | Evidence | Proposed disposition |
|---|---|---|---|
| `static/photos/jason-and-james.jpeg` | **identity** — photograph of two identifiable individuals, both given names in the filename | live probe: HTTP 200 on `lab.verdify.ai` today | **Jason's call (D1).** Already public on a *mutable* site; a release asset is a *permanent, non-retractable* publication. Options: keep, keep with an opaque filename, or exclude. |
| `static/photos/exterior-wide-property-solar.jpg`, `exterior-night-snow.jpg`, `exterior-evening-patio-wide.jpg` (+ `full/` variants) | **premises / location disclosure** — exterior views of a private residence | live probe: HTTP 200 | **Jason's call (D2).** The greenhouse is at a private address in Longmont, CO. Exterior wide shots plus published local-time series narrow location materially. |
| `static/photos/homelab-cortex-server-rack.jpg` | **infrastructure disclosure** | live probe: HTTP 200 | **Jason's call (D2).** Rack photography can expose hardware, labels, and cabling detail not visible in text. |
| All 5 `.png` (3 under `start/slack-ops/`, 2 icons) | **metadata** | the 3 `start/slack-ops/` PNGs are the plausible re-encode targets of the previous capture (`pngReencodeFiles: 3`) | Re-encode to strip ancillary chunks. The repo already holds the strict precedent for this in `scripts/lib/png-validation.mjs`, which rejects ancillary metadata on occurrence blobs. |
| `static/vision/lettuce-477.jpg`, `lettuce-479.jpg` | **camera-derived imagery** | live probe: HTTP 200; `robots.txt` `Disallow: /static/vision/` | Include only if D3 says the vision family stays published. They are served-but-not-indexed today. |
| 38 `.jpg` + 1 `.jpeg` generally | **content rights** | — | **Jason's call (D4).** Confirm every image is first-party. Any third-party or licensed image must be excluded from a permanent public asset. |
| `raw-planner-lessons.md`, `raw-ai-tunables.md`, `planner-static-context.md`, `publish.log` | **raw pre-reduction material** | on the PVC under `state/`, **outside** `content/`; 66 KB→50 KB and 333 KB→80 KB reductions vs their published counterparts | Structurally excluded — a capture of `content/` cannot reach them. Recorded here so the exclusion is deliberate, not accidental. |
| `orchid-463.jpg`, `orchid-471.jpg`, `orchid-477.jpg` | **residue** | present under the retired `/lab-cache/public` directory; absent from `content/`, from the live served tree, and from both manifests; 404 on the live site | Structurally excluded. **Open item:** whether the S3 bucket still retains these pruned objects is unverified. |

### 3.3 Stale policy to fix in the same capture

`robots.txt` carries `Disallow: /greenhouse/lessons/raw`. That path **does not
exist** — not in `content/`, not in the built tree, not in either manifest, and
it 404s live. The rule is stale or pre-emptive. A production policy that
disallows a nonexistent path is misleading evidence; either remove the rule or
make the path real. The production `robots.txt` is asserted byte-for-byte by
`verify-production-output.mjs`, so this must be settled before capture.

## 4. Privacy and identity reasoning

Everything in `content/` is already publicly served at `lab.verdify.ai`. The
decision is therefore **not** "is this public?" but "should this become a
permanent, content-addressed, non-retractable public artifact?" A GitHub release
asset can be downloaded and mirrored the moment it is published; withdrawing it
later does not withdraw copies. A site page can be edited or removed.

That asymmetry is the whole of D1 and D2. The text corpus scans clean. The
residual identity and location exposure is entirely in the photographic assets,
and it is a judgement call, not a detection problem — which is exactly why #570
routes it to a human approver rather than to a guard.

## 5. Content-rights reasoning

The corpus is machine-generated greenhouse telemetry, planner output, and
first-party photography, published under the Verdify Lab site. The rights
question is confined to the 39 photographic assets and the 2 `.mp4` / 179 HLS
media files. The HLS ladder is a first-party greenhouse tour (frozen 2026-06-29).
D4 asks Jason to confirm there is no third-party or licensed imagery in the set.

## 6. Occurrence-selection reasoning

The production verifier's `verifySelectedEvidence` requires that **every**
discovered occurrence carry a selected, materialized, content-addressed
fallback. The frozen capture discovers **143 Grafana graph occurrences + 2
current-camera occurrences**; none currently has a selected release. The
occurrence set is a property of the snapshot's markdown, so a new capture will
discover its own count, which must be re-measured, not assumed.

The approval record therefore binds `occurrenceSelectionPolicySha256` — the
SHA-256 of the exact canonical export policy that produced the selected release.
That makes the approval cover *which* occurrences were selected and under what
policy, not just the content bytes. Selecting occurrences at all is the
#533/#534/#535/#540/#542/#541 track and is **not** delivered by #570.

## 7. Why the bytes were not captured

1. **The sanitizer is not in this repository.** Only the detector
   (`scripts/check-public-output.py`) is. The 8 transformations of the
   2026-07-12 capture were produced out-of-tree. Reconstructing them means
   choosing a redaction policy — which is D1–D4, a decision, not an
   implementation.
2. **Publishing the asset is outward-facing and effectively irreversible.**
   ~397 MiB derived from a private greenhouse/vault pipeline, published as a
   public GitHub release asset. That is a confirm-first action.
3. **A capture today would not turn the build green and would need redoing.**
   Section 9 shows the approval is one of three independent gates. Capturing
   before the occurrence track lands produces a large permanent public artifact
   that unblocks nothing and is likely superseded.

## 8. The approval record Jason would sign

The registry entry is the approval. Its shape is fixed by
`site-astro/scripts/lib/production-approval.mjs`; the digest fields are produced
by the capture:

```json
{
  "contract": "verdify.lab-production-snapshot-approval",
  "schemaVersion": 1,
  "approvalId": "lab-production-snapshot-<YYYYMMDD>t<HHMM>z",
  "snapshotAttestationSha256": "<from capture>",
  "sanitizedManifestSha256": "<from capture>",
  "sourceManifestSha256": "<from capture>",
  "sanitizedFileCount": "<from capture>",
  "sourceFileCount": "<from capture>",
  "policyVersion": "verdify-public-output-production-v1",
  "guardReportSha256": "<from capture, zero-finding v2>",
  "sourceOrigin": "s3://<LAB_S3_BUCKET>/lab/content",
  "sourceCapturedAt": "<UTC instant of the capture>",
  "occurrenceSelectionPolicySha256": "<reviewed export policy>",
  "approver": "jvallery",
  "approvalRecordUrl": "https://github.com/VerdifyConsultancy/verdify-platform/issues/570#issuecomment-<id>",
  "approvedAt": "<UTC instant of the recorded decision>",
  "releaseTag": "lab-production-snapshot-<YYYYMMDD>t<HHMM>z",
  "assetSha256": "<published release asset digest>",
  "approvalSha256": "<sha256 of the canonical approval.json bytes>"
}
```

### Decisions only Jason can make

- **D1 — identity.** `static/photos/jason-and-james.jpeg`: keep as-is, keep
  under an opaque filename, or exclude from the permanent asset?
- **D2 — premises and infrastructure.** Keep, downscale/crop, or exclude the
  three exterior property photographs and the server-rack photograph?
- **D3 — camera-derived imagery.** Does the `static/vision/` family stay in a
  permanent public artifact, given it is `Disallow`ed but served today?
- **D4 — content rights.** Confirm all 39 photographic assets and the
  greenhouse tour media are first-party and clear for permanent publication.
- **D5 — robots policy.** Remove the stale `Disallow: /greenhouse/lessons/raw`
  rule, or create the path? (`verify-production-output.mjs` asserts the exact
  bytes.)
- **D6 — the production sanitization policy version.** Ratify
  `verdify-public-output-production-v1` as the label for the D1–D5 outcome. The
  code refuses a stage policy version in a production approval.
- **D7 — S3 residue.** Is the bucket to be swept for pruned objects (notably
  the three orchid vision images) before a permanent capture?
- **D8 — capture location.** Authorize the capture to run **in-cluster in the
  owning repo pod** (recommended: it already has the source, keeps ~397 MiB of
  unsanitized content off any laptop, and satisfies #570's "from the exact
  owning repo pod") rather than on an operator machine.

## 9. What this unblocks — and what it does not

Merging the approval contract and, later, an approved snapshot clears **one** of
the three independent gates in `verify-production-output.mjs`:

1. `approvalEligible === true` and `localEvidenceStatus !== "provisional-only"`
   — **closed by #570 + a registered approval.**
2. `unavailableReferenceCount === 0` — the frozen capture has **nine**
   unavailable historical references (five `/plans/2026-06-08..12` routes, four
   `/static/vision/{lettuce-475,peppers-482,peppers-484,peppers-487}.jpg`
   assets; `docs/reviews/lab-astro-same-snapshot-parity-2026-07-13.md`). Live
   probes confirm all nine targets are still absent today. The generator fixes
   are merged, so a **fresh** capture should stop emitting the dangling
   references — this must be measured on the new capture, not assumed.
3. `verifySelectedEvidence` — every discovered occurrence needs a selected
   immutable release with a materialized same-origin fallback. The occurrence
   store is empty and the executable producer/publisher path
   (#535/#540/#542/#541) has not landed. **Not addressed by #570.**

Gate 3 is the binding constraint. An approved snapshot alone does not produce a
green `verdify-lab` build.
