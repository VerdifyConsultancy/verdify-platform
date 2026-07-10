# Verdify application database credential rotation runbook

Status: **COMPLETE**. Gate `g-prod-db-credential-rotation-20260709` was
approved as `rotate-now` and the runbook completed on 2026-07-10.

Owner: Jason Vallery, with the controller executing only after an explicit `rotate-now` decision.

Scope: rotate only `verdify-app-secrets.POSTGRES_PASSWORD` and the existing PostgreSQL `verdify` role; preserve every unrelated key.

Execution evidence: `.verdify/sprints/software-recovery-2026-07-09/lanes/security-hygiene/rotation-closeout-2026-07-10.md`
at controller evidence commit `ecb987f`. The replacement passed new-valid and
old-invalid proof, all healthy consumers accepted it, and rollback was not used.
This status records the completed execution; the hard stops and sequence below
remain the authoritative procedure for any future rotation.

## Hard stops

Do not begin until all are true:

1. Jason explicitly resolves the gate with `rotate-now`.
2. The caller matrix has been refreshed from live workload templates.
3. The current database backup has succeeded and its rollback location is recorded without credential material.
4. No CronJob using the password is running; the five listed CronJobs are suspended through an authorized change.
5. The operator has a secure local shell with tracing/history disabled, `umask 077`, the fleet SOPS age key, the current credential retained only for rollback/negative proof, and the replacement held only in an approved credential manager or protected process memory.
6. The fleet secret authority is verified before use. At the 2026-07-09
   preflight, the available registry/skeleton still named retired
   `verdify-staging`; that historical blocker was corrected by the reviewed
   production SOPS authority merged in `jvallery/agents#2904`. For every future
   execution, a reviewed production-targeted artifact for
   `verdify-prod/verdify-app-secrets` must be the source of truth. Never apply a
   staging or otherwise non-production artifact to production.
7. The replacement satisfies the temporary URI-compatibility contract: exactly 64 lowercase hexadecimal characters generated from 32 cryptographically random bytes. Validate the shape in the secure process and record only `replacement_uri_safe: true`; never record the value. This preserves 256 bits of entropy while using only URI-unreserved characters. Until every consumer percent-encodes credentials, any other alphabet or length is a hard stop because characters such as `@`, `/`, `#`, and `?` can change PostgreSQL URI parsing. Do not use ordinary base64 output for this rotation.

Do not print, echo, paste into a PR, pass as a command-line argument, enable shell tracing, or write either credential to a normal file.

## Rotation sequence

1. **Capture non-secret baseline.** Record current Git/Argo revision, Secret resource version, DB pod identity, Ready replicas, active Jobs, alert counts, endpoint health, and caller inventory. Confirm the ingestor uses one replica and `Recreate` strategy.
2. **Prepare rollback.** Retain the prior encrypted SOPS revision and a secure operator-only copy of the old credential until all new-valid/old-invalid proofs pass. Record the prior deployment digests.
3. **Suspend scheduled consumers.** Suspend band-curve refresh, DB backup, HA gap backfill, lab publisher, and vision. Wait for already-started Jobs to finish or terminate them only under the protected change procedure.
4. **Seal the replacement.** Generate the replacement from 32 cryptographically random bytes as 64 lowercase hexadecimal characters, validate the format without emitting it, and store it only through the approved secure path. Edit the approved production SOPS artifact in place with `sops`, changing only `POSTGRES_PASSWORD`. Preserve `VERDIFY_WRITE_API_KEY`, `ESP32_API_KEY`, `MQTT_USER`, `MQTT_PASS`, and any later-added keys byte-for-byte. Commit/review the ciphertext and metadata change without decrypted output.
5. **Deliver the Secret.** Use the fleet secret-delivery path to update `verdify-prod/verdify-app-secrets`. Verify only resource version and key-name parity. Existing pods retain their old environment until recreated.
6. **Rotate the existing DB role in place.** In a secure interactive `psql` session on the DB pod, use the password meta-command for role `verdify` so the replacement is not placed in shell history or a logged SQL file. Do not create a substitute role: `verdify` owns thousands of relations and is currently superuser.
7. **Restore the device writer first.** Recreate `verdify-ingestor` using its one-replica `Recreate` strategy. Stop if a second writer appears, the Lease is ambiguous, database tasks fail, ESPHome does not reconnect, or a new critical alert opens.
8. **Roll stateless consumers one at a time.** Recreate API, MCP, planner, setpoint-server, then Grafana. After each rollout, require Ready state and its matrix validation before continuing.
9. **Validate future migration auth.** Do not run a migration solely for credential proof. Confirm the next authorized migrate Job template references the new Secret resource; its normal preflight will provide the execution proof.
10. **Resume scheduled consumers deliberately.** Resume and run controlled proofs for band refresh, DB backup, HA gap backfill, and lab publisher. Resume vision only with its independent image-pull disposition recorded.
11. **Prove new-valid.** From an approved secure operator path, test a new database connection and every consumer outcome. Store booleans/statuses only. Application health without an authenticated query is insufficient where a targeted read can be made safely.
12. **Prove old-invalid.** Using the retained old credential only in the secure shell environment, attempt a bounded database login and discard all command output. Record only `old_credential_rejected: true`. Any successful old login is a hard failure.
13. **Close out.** Confirm all intended workloads are Ready, no auth failures are rising, ingestor remains the only writer, scheduled proofs passed, the current Secret/SOPS metadata point at production, and rollback material is dispositioned. Update issue #438 and close the gate with immutable refs and boolean evidence.

## Rollback

If any core consumer cannot authenticate or greenhouse control degrades:

1. Stop further rollouts and keep CronJobs suspended.
2. Through the same secure interactive path, restore the old password on the existing `verdify` role.
3. Reapply the prior encrypted production Secret revision through secret delivery.
4. Recreate only workloads already moved to the replacement, starting with ingestor and preserving its single-writer invariant.
5. Re-run old-valid health checks, record the rollback verdict, and leave the gate open.

Do not roll back unrelated keys, firmware, device credentials, database schema, or application digests unless evidence ties them to the incident.

## Redacted evidence record

The closeout must contain only:

- authorization/gate reference;
- encrypted-source commit and Secret resource-version refs;
- DB and workload identities/digests;
- per-consumer Ready/authenticated-health booleans;
- controlled CronJob results;
- `replacement_uri_safe: true|false`;
- `new_credential_valid: true|false`;
- `old_credential_rejected: true|false`;
- rollback used/not-used and disposition;
- timestamps, alerts, and issue/PR/check refs.

Any raw or reversible credential material invalidates the evidence and requires immediate incident handling.
