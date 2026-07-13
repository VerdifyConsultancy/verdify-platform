# Lab immutable-release runtime candidate

This directory is a disconnected, read-only proof target. It is intentionally
absent from every active overlay and creates no Ingress, IngressRoute, DNS,
Secret, PVC, publishing writer, database reader, or device path.

Each of the two hostname-spread pods owns an `emptyDir` cache. The init
container verifies and hydrates the image-baked known-good release before nginx
starts. The sidecar polls the site-release CLI for the selector's immutable
SHA-256 identity, verifies a changed release into a new physical generation,
then atomically changes the relative `current` symlink. Nginx serves
`current/tree`; a failed observation preserves that complete generation.

The checked-in workload is deliberately disabled at the storage layer:
`LAB_RELEASE_STORE` names an absent local directory, so cold start uses only the
digest-bound baked bundle. Enabling object storage is a separate integration:

1. build and pin the `agent` and `site` targets from
   `site-astro/Dockerfile.release-runtime` through the fleet Kaniko/ZOT path;
2. land the object-store implementation behind `manage-site-release.mjs`;
3. deliver a read-only, prefix-scoped credential outside this candidate and
   patch `LAB_RELEASE_STORE` to its credential-free `s3://bucket/prefix` URI;
4. verify the endpoint remains `s3-hdd.vallery.net` (the policy permits only
   its current `192.168.7.10/32` edge address on TCP 443 plus cluster DNS); and
5. run disconnected canary probes before any separately authorized route or
   cutover change.

The runtime publishes `/healthz`, `/readyz`, `/metrics`, and
`/.well-known/verdify-release.json` through the internal ClusterIP service.
