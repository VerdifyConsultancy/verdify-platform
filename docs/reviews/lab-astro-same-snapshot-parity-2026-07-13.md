# Lab Astro Same-Snapshot Parity Review — 2026-07-13

Issue: #479. This review records the immutable input, the nine unavailable
historical references, and their explicit disposition. It does not authorize a
stage activation, credential/resource binding, production sync, DNS change, or
Quartz retirement.

## Frozen comparison input

- Snapshot files: 429
- Attestation SHA-256:
  `a5ed1cc899094dc061c01904d93e7034618d9eb22cf14488d04298da635a4e6d`
- Sanitized content-manifest SHA-256:
  `2dbcb7256f475be6bd620427101900c53814fb065a815e0129b19451d7467d86`
- Evidence status: `provisional-only`; `approvalEligible: false`
- Same origin for both builds: `https://lab.verdify.ai`

The schema-v2 parity manifests bind both byte digests above and the comparator
revalidates the declared snapshot root. A declared identity mismatch is an
unwaivable failure; the recorded build procedure supplies the same content tree
to both generators. The current attestation remains diagnostic evidence only;
strict approval remains blocked until a trusted immutable attestation resolver
is implemented and an approved snapshot is supplied.

## Code-level diagnostic result

Both generators read the frozen 429-file content tree above and used
`https://lab.verdify.ai` as their origin. The schema-v2 comparison contains
240/240 canonical routes and 84/84 aliases. It reports 442 findings, all in the
remaining source or materialization boundaries:

- 143 `baseline-grafana-fallback-missing`, 143
  `baseline-grafana-source-conflict`, and 143
  `candidate-grafana-fallback-missing` findings: 429 total, gated on #476/#480.
- Four `asset-missing` plus four `baseline-asset-missing` findings and the
  matching grouped media findings for the four historical images below.
- One grouped link finding containing the five unavailable daily-plan routes
  below.
- One grouped link finding plus one grouped media finding for the two current
  camera occurrences, gated on #476/#480.

There are no remaining title-parser, feed, sitemap, robots, 404, fragment-target,
decorative-anchor, or crop sibling-link findings. The comparison is correctly
`compatible: false`, has no applied exceptions, and is
`approval-blocked-provisional-evidence`.

The final diagnostic artifacts are byte-identified as follows (the large JSON
documents are reproducible evidence and are not committed):

- Quartz schema-v2 manifest:
  `sha256:2bdeca50712e5fd0e45f5b42b2bae577e2b8f8a79388096e41b32494356c4ece`
- Astro schema-v2 manifest:
  `sha256:f8ffd0a0a5db39934385c6026b267f1a813ff5c45165d02c754277b36c565cda`
- Comparison report:
  `sha256:e9fa0c5056fae199d0d3ac7733f9c8eb8a200034da677a9857515e6e030a2e8e`

## Historical-reference dispositions

The offline source check found 16 errors. Exactly nine are the historical
references below; the other seven are separately visible duplicate-panel,
duplicate-alias, or freshness findings and are not reclassified here.

| Source page | Unavailable target | Frozen evidence | Disposition |
|---|---|---|---|
| `data/plans/index.md` | `/plans/2026-06-12` | Target absent from the 429-file manifest | Keep the daily-summary row, render the date as unlinked with `planner-cycle page unavailable`; do not invent a plan page |
| `data/plans/index.md` | `/plans/2026-06-11` | Target absent from the 429-file manifest | Same disposition |
| `data/plans/index.md` | `/plans/2026-06-10` | Target absent from the 429-file manifest | Same disposition |
| `data/plans/index.md` | `/plans/2026-06-09` | Target absent from the 429-file manifest | Same disposition |
| `data/plans/index.md` | `/plans/2026-06-08` | Target absent from the 429-file manifest | Same disposition |
| `greenhouse/crops/lettuce.md` | `/static/vision/lettuce-475.jpg` | Asset absent from the 429-file manifest | Preserve timestamp/camera/zone/health/notes and render an explicit historical-image-unavailable state; do not create substitute bytes |
| `greenhouse/crops/peppers.md` | `/static/vision/peppers-487.jpg` | Asset absent from the 429-file manifest | Same disposition |
| `greenhouse/crops/peppers.md` | `/static/vision/peppers-484.jpg` | Asset absent from the 429-file manifest | Same disposition |
| `greenhouse/crops/peppers.md` | `/static/vision/peppers-482.jpg` | Asset absent from the 429-file manifest | Same disposition |

The plan-index generator now links a row only when its actual daily Markdown
page exists. A zero-plan row remains linkable when a genuine generated page is
present. The crop-profile generator now publishes only an original image or a
retained same-ID public copy; otherwise it keeps the observation metadata and
states that the image is unavailable. Tests cover both rules and prohibit a
replacement image under a historical URL.

## Reproduction

```bash
python scripts/site-doctor.py \
  --vault-root site-astro/.snapshot/content \
  --skip-grafana --skip-public --skip-site-container --skip-launch-lint
```

The framework comparison is reproduced by building Quartz from
`.snapshot/content`, building Astro with `LAB_SNAPSHOT=.snapshot`, and then
running both schema-v2 manifest commands plus `compare --allow-provisional`
with `--snapshot-root .snapshot`. Astro's production build intentionally exits
at its final release verifier because this snapshot is provisional; the static
output is complete before that fail-closed approval boundary.

On the frozen snapshot this reports the four `missing-image` and five
`missing-internal-link` rows above. A new sanitized snapshot produced after the
generator fixes must contain none of those dangling `src`/`href` values. Both
frameworks must then be rebuilt from that exact new snapshot and its identity
recorded here before the nine dispositions can be considered reflected in the
immutable input.

## Remaining parity boundaries

- The repaired deterministic Quartz/Astro feeds and sitemaps, document-title
  parsing, rendered-text normalization, 404 preservation, robots policy, and
  decorative heading-anchor treatment are code-level Phase 4d work.
- Selected same-origin evidence is still absent for 143 graph and two current
  camera occurrences. That is the gated #476/#480 release-materialization
  boundary; this review does not weaken or waive it.
- Final #479 evidence must run without `--allow-provisional`, report zero
  unexplained semantic findings, and use an approved immutable snapshot.
