# Lab immutable-release runtime candidate

This directory is a disconnected, read-only proof target. The Lab stage overlay
includes it only as dormant desired state with `replicas: 0` and zero-digest
image sentinels. It creates no Ingress, IngressRoute, DNS, Secret, PVC,
publishing writer, database reader, or device path. Its only Secret reference
is the standard zot registry image-pull Secret; it has no application or
object-store credential.

Each of the two hostname-spread pods owns an `emptyDir` cache. The init
container verifies and hydrates the image-baked known-good release before nginx
starts. The sidecar polls the site-release CLI for the selector's immutable
SHA-256 identity, verifies a changed release into a new physical generation,
then atomically changes the relative `current` symlink. Nginx serves
`current/tree`; a failed observation preserves that complete generation.
The workload declares the agent and nginx commands explicitly, and the fleet
image-pair probe exercises this same command contract. Runtime startup therefore
does not depend on a control-plane registry metadata lookup for a private image.

The checked-in workload is deliberately disabled at both the scheduler and
storage boundaries: `LAB_RELEASE_STORE` names an absent local directory, the
pod has no S3/AWS environment or object-store annotation, and its NetworkPolicy
allows no egress. Enabling object storage is a separate integration:

1. build and pin the `agent` and `site` targets from
   `site-astro/Dockerfile.release-runtime` through the fleet Kaniko/ZOT path;
2. land the object-store implementation behind `manage-site-release.mjs`;
3. provision a dedicated Lab Astro release bucket in Garage, with separate
   per-bucket keys for the read-only runtime and the publishing writer; key
   provisioning and activation are safety-checked, key material stays outside
   Git, and source may record only the Secret/key names;
4. introduce the separately validated least-privilege egress and Secret
   references, then patch `LAB_RELEASE_STORE` to a credential-free
   `s3://<dedicated-release-bucket>/releases` URI;
5. replace both zero image sentinels and explicitly raise the replica count; and
6. obtain the operator stage-sync gate and run disconnected canary probes before
   any separately authorized route or cutover change.

Merging this source does not change the live cluster. The Lab stage ArgoCD app
is manual-sync, and an operator sync is a separate recorded action. Even after
such a sync, this source remains unschedulable at `replicas: 0`; activation
requires another explicit activation change satisfying every step above.

The runtime publishes `/healthz`, `/readyz`, `/metrics`, and
`/.well-known/verdify-release.json` through the internal ClusterIP service.
