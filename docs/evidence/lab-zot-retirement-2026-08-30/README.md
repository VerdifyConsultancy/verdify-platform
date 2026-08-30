# Verdify Lab Zot retirement evidence — 2026-08-30

`manifest-inventory.tar.gz` is the checksum-sealed, credential-free pre-delete
inventory for the four retired Lab image repositories:

- `verdifyconsultancy/verdify-lab-astro`
- `verdifyconsultancy/verdify-lab-release-agent`
- `verdifyconsultancy/verdify-lab-release-nginx`
- `verdifyconsultancy/verdify-lab-occurrence-exporter`

The archive contains tag-to-digest ledgers, unique digest lists, raw OCI
manifests, and an internal SHA-256 checksum list captured before all 536 tagged
manifests were deleted. It contains no registry credential or Secret value.

The archive is audit and reconstruction evidence, not a standalone image
backup: image blobs are not included. Git source and build history remain the
authoritative rebuild path. Post-delete verification proved zero tags for all
four repositories and HTTP 404 for every formerly deployed digest. Zot 2.1.x
continues to show the four empty repository-name tombstones in `_catalog`.

Related delivery records:

- Verdify Platform PRs #763, #764, and #765
- jvallery/agents PRs #4133 and #4135
- jvallery/storage-infra PR #440

