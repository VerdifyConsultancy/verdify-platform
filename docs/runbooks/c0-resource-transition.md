# C0 resource transition: exact 240–248 release

Campaign #775 / resource #781 / restored qualification #783 / delivery #644.
The owning C0 release branch is [#806](https://github.com/VerdifyConsultancy/verdify-platform/pull/806). Source, contract and image qualification do not imply a production sync.

| Contract version | Fixed source membership | Allowed ledger state |
|---|---|---|
| `c0-boundary-transition-241-247-v1` | 241–247 | All seven pending, or exact seven-file successor retry |
| `c0-resource-boundary-transition-240-248-v1` | 240–248 | All nine pending, or exact nine-file successor retry |

Migration 240 is part of the resource release because its two EXECUTE and two SELECT grants change **both** migration-217 protected catalog digests. Applying 240 separately leaves both ordinary-login receipts stale. The nine sources, their normal ledger stamps and both approved successor receipts therefore share one transaction. Do not run a current-main migration hook ahead of the nine-file release while 240 is pending.

The source pins include 240 SHA256 `7fc9a584a7cdc3fc2b89c0bea18c1e3ec5fdd8c9e6261ccf3c2cb908320e7999` and 248 SHA256 `45b3fb28c8e11608e14407f5b18bc15018dff54c7dc8dd7b882352d961027b56`. Neither contract JSON nor operator input can provide filenames, source SQL or alternate hashes. The old seven-file profile remains distinct and cannot admit pending 240 or 248. A partial or changed release refuses without per-file fallback.

## Target contract and rehearsal

The reviewed candidate contract is `deploy/k8s/overlays/prod/c0-boundary-contract-240-248.json`, SHA256 `90fac3dafbf927e6dfc67d8ec73e6b41c4f818f5c983216550848be8bf9dff6f`. It binds database `verdify`, PostgreSQL 16.11, the full predecessor ledger identity, and both before/after ordinary-login digests. The prod overlay generates a content-hashed PreSync ConfigMap at wave -1. The wave-0 `verdify-migrate` Job mounts its key as a regular read-only `subPath` file at `/db/c0-boundary-contract.json` and pins the exact byte hash directly in `VERDIFY_C0_BOUNDARY_CONTRACT_SHA256`. The regular-file mount is required because the contract reader rejects Kubernetes ConfigMap key symlinks.

The qualification used an isolated Longhorn snapshot clone of the sole production database, with the same database/role OIDs, PostgreSQL 16.11, TimescaleDB 2.25.2, a Unix-only socket and a deny-all NetworkPolicy. Before projection, its ledger SHA256 `cf0ee5e4b764587c539608a9b5db214be812987d82ae849fe6fb1e23f914f7c5` and both installed digests/receipts matched the live read-only predecessor. Exact 240–248 sources ran inside `BEGIN … ROLLBACK`; the independently extracted migration-217 catalog projection matched the installed digest for both logins before and after. A fresh session proved zero 240–248 stamps and unchanged predecessor receipts after rollback. The exact transaction then committed on that disposable clone; fresh-session readback verified all nine stamps, both approved successor receipts and both independently projected catalogs. The ordinary ingestor login exercised the new grants in a read-only session with no write privilege.

The latest 269.4 MB logical dump (`verdify-20260926T081706Z.dump`, SHA256 `740c195b8d597b1b7ab9c52621ed6fd2a64b2edbfcfe86860e6242b5b09f0061`) restored into a separate disposable cluster, but its generic catalog inspection refused before the rollback probe. A logical restore can change cluster role/database OIDs, so its fingerprints cannot be transplanted into this target contract. The physical snapshot clone is the exact-target proof. Preserve the logical restore failure as a DR follow-up.

The candidate migrate image is `registry.vallery.net/verdifyconsultancy/verdify-migrate@sha256:b3f5c221d09154734ad9d2f62780a3318d7b7df0b4ab3dea8a5ba911627a2ca5`, built by Workflow `agent-fleet-ci/verdify-c0-nine-migrate-ph285` from source `3f8b7eb1b3415d97ae63564739a1c95f53a1ba47`. The final GitOps render must show the same image for migration wave 0 and credential bootstrap waves 1 and 2, the contract hash and zero Secrets. The actual image entrypoint must also pass on a fresh isolated physical clone with the rendered volume/env before production sync.

## Delivery and readback

Use a full, no-prune Argo sync after reviewing every resource in the exact desired revision. Selective sync skips PreSync migrations. Wave 0 must complete the nine-file transaction and new-session readback before wave-1 ordinary-role credential attestation and wave-2 experiment-credential attestation. Only then may Sync-wave API, MCP, planner, ingestor and the other workloads reconcile. The former one-shot Gate R PostSync activation, bound to an obsolete source revision, is absent from this production render; collect fresh Gate R readiness separately after this release.

After sync, verify Argo `Synced` + `Healthy` at the intended revision, all nine exact ledger hashes, both installed digests and stored receipts under ordinary sessions, running image IDs, API/MCP/DB smoke, one real ESP32 socket and five minutes without ingestor schema-validation drops. Resource availability and scientific eligibility remain independently evaluated; a successful migration does not commission the meter or authorize physical experiments.

Native PG16/Timescale regression coverage lives in `tests/test_c0_resource_transition.py`; the Mac fallback runs static checks but skips native database cases. The production snapshot-clone rehearsal and exact-head in-cluster CI supply the release evidence for this target.
