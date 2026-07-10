# Production DB credential rotation preflight — 2026-07-10

Scope: issue #438 and `g-prod-db-credential-rotation-20260709`. This record contains identities, paths, counts, and booleans only. No credential value, reversible fingerprint, or decrypted Secret payload is present.

## Authorization

- Gate decision: `rotate-now`, approved by Jason Vallery at 2026-07-10T12:21:20Z.
- Durable authorization commit: `dca668fc6d5a68e14843510e32a26d4d4d673522`.
- Non-waived controls: caller completeness, successful backup, production SOPS authority, suspended scheduled consumers, URI-safe replacement, single-writer-first restart, redacted new-valid/old-invalid proof, and rollback readiness.

## Encrypted authority

- Source: `jvallery/agents` `platform/gitops/secrets-ksops/verdify-prod/secret-verdify-app-secrets.enc.yaml`.
- Production identity: `Secret/verdify-app-secrets` in `verdify-prod`.
- Pre-rotation Secret resourceVersion: `6118955`; UID `2b9968c1-d5fd-4539-85ab-ad1ba665752a`.
- Key names: `ESP32_API_KEY`, `MQTT_PASS`, `MQTT_USER`, `OPENAI_API_KEY`, `POSTGRES_PASSWORD`, `VERDIFY_WRITE_API_KEY`.
- In-cluster `argocd/sops-age` identity loaded into a mode-0600 temporary operator file; SOPS decrypt succeeded.
- Encrypted source versus live Secret parity before staging: `true` for all six keys.
- Staged replacement PR: `jvallery/agents#2904`, head `e6e8f69d92349d1577ada6feb3af6f6a0722ea9a`.
- `replacement_uri_safe: true`; non-password plaintext values unchanged: `true`; metadata/key set unchanged: `true`.

## Backup and rollback baseline

- Backup Job: `verdify-db-backup-29727857`; UID `46a5309a-bc99-4aee-8cf6-8a812771b7a4`.
- Started `2026-07-10T08:17:00Z`; completed `2026-07-10T08:17:45Z`; succeeded `1`; failed `0`.
- Output: `/backups/verdify-20260710T081705Z.dump`, reported size `178.6M`.
- Persistent location: PVC `verdify-prod/verdify-db-dumps`, PV `verdify-db-dumps-prod`, NFS CSI handle `192.168.30.126#volume2/verdify#db-dumps/prod#verdify-db-dumps-prod#`.
- Prior encrypted SOPS revision and current workload digests remain available for rollback; old credential access is retained only through encrypted pre-rotation Git history for the bounded negative proof.

## Refreshed live callers

- StatefulSet: `verdify-db`.
- Deployments: `verdify-ingestor`, `verdify-api`, `verdify-mcp`, `verdify-planner`, `verdify-setpoint-server`, `verdify-grafana`.
- CronJobs: `verdify-band-curve-refresh`, `verdify-db-backup`, `verdify-ha-gap-backfill`, `verdify-lab-publisher`, `verdify-vision`.
- Future Job template: `verdify-migrate`.
- No additional live workload template referenced `verdify-app-secrets.POSTGRES_PASSWORD` at the 2026-07-10 refresh.

## Scheduled-consumer fence

- All five credential-consuming CronJobs were patched `spec.suspend=true`.
- The sole active Job was `verdify-vision-29727180`, stuck since 2026-07-09 with `ImagePullBackOff`; after suspension it was deleted under the authorized runbook.
- Active Jobs after fencing: `0`.

## Production baseline

- Argo app `verdify-prod-dark`: `Healthy`, `OutOfSync`, desired revision `c9cb0ceddc13671c7cd8da103016e8846efacf44` at capture. No Argo sync was performed.
- `verdify-ingestor`: one desired/ready/available replica, `Recreate`, pod `verdify-ingestor-84b785cc55-pgq5r` Ready.
- Writer Lease holder matched that sole ingestor pod and renewed during preflight.
- Database authenticated locally as role/database `verdify`.
- Public probes returned HTTP 200 for `api.verdify.ai/health`, `lab.verdify.ai`, and `graphs.verdify.ai`.
- Alert baseline: two critical and 28 warning rows open/acknowledged and unresolved. Existing planner alerts are release/OTA blockers but are not caused by credential authentication.

## Stop point and next transition

The live Secret and database role remain unchanged at this preflight checkpoint. Continue only after PR #2904 has green checks and an independent exact-head security approval. Then merge the ciphertext authority, deliver only the Secret, rotate the existing role through the secure interactive path, recreate the one writer first, roll each stateless consumer serially, prove new-valid and old-invalid, and resume controlled CronJob proofs.
