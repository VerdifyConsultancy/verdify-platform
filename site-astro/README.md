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
download, media, and Grafana occurrence semantics:

```bash
npm ci
npm test
```

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

The build vendors the reviewed `@verdify/site-shell` 1.0.0 release from WWW
commit `c9c0d56f654d6b9198352f16c620717dbee71612`. The archive, independent
release record, and four-file consumer kit are committed under `vendor/` and
`scripts/site-shell/`; every byte is independently pinned before the hardened
installer runs, and the installed tree is verified again in a separate
process. There is no runtime, CDN, registry, or package-manager fetch. The
shared Header, Footer, Breadcrumbs, Lab lockup, design tokens, and self-hosted
IBM Plex fonts come directly from that release. Lab-owned evidence navigation,
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

The runtime is static nginx on port 8080, globally noindex, read-only-root
compatible, and requires no Secret, service-account token, database, object
store, Grafana, or device-network access.

## Known blockers

- The sanitized legacy capture remains provisional-only; the future approved
  immutable filesystem/object-store evidence attestation is not implemented.
- Digest-verified local Grafana fallback images are absent from the frozen
  snapshot; stage preserves each source occurrence as non-embedded provenance
  and an explicit external link.
- The frozen Quartz baseline has known HLS, alias, feed/sitemap, missing-asset,
  and fallback findings and is not clean approval evidence.
- The WWW shell release is review-only until its producer PR is merged; the Lab
  pins its exact reviewed bytes and does not claim producer merge or cutover.
- The zero-finding public-data guard report is mandatory snapshot input, but a
  public canary still requires the separate approval gates.
