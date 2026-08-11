# Verdify database credential caller matrix

Snapshot: 2026-07-09T22:45:00Z

Scope: issue #438, `verdify-app-secrets.POSTGRES_PASSWORD`, and the five standalone clients changed by the security-hygiene lane.

Evidence policy: names, references, commands, and boolean verdicts only; no credential values.

## Verdict

Removing the committed fallbacks and `/srv/verdify/.env` parsing does not break a current live caller.

- `daily-summary-snapshot.py` has no current scheduler. Its retired Iris VM user cron is gone; `verdify-ingestor` now writes live daily summaries in process.
- The equipment, zone, and crop renderers run through the live `verdify-lab-publisher` CronJob. The pod injects `POSTGRES_PASSWORD` from `verdify-app-secrets`, and `lab-publish-k3s.sh` composes and exports `VERDIFY_DSN` before calling `publish-site-content.sh`.
- `vault-operations-writer.py` has no wrapper, Make target, CronJob, systemd unit, or live pod caller. It is manual-only and now fails closed unless an authenticated DSN/password is explicitly injected.
- Direct workstation and repo-pod invocations intentionally have no implicit write credential. They must use the authenticated operator tunnel plus an injected credential, or the read-only `AGENT_RO_DSN` where the operation is read-only.

## Five-client matrix

| Client | Current caller | Injection | Rerun and validation | Rollback or accepted gap |
|---|---|---|---|---|
| `scripts/daily-summary-snapshot.py` | No live scheduler; historical VM cron only. Live equivalent is the ingestor `daily_summary_live` task and midnight accumulator writer. | Direct invocation must provide `VERDIFY_DSN` or `POSTGRES_PASSWORD`; the script writes `daily_summary`, so a read-only role is insufficient. | No restart. If an intentional backfill is needed, establish the authenticated DB tunnel, inject the credential without logging it, run one explicit date, and verify the row plus ingestor logs. | Revert the source commit. Durable follow-up is either retire/document the script as manual backfill or author a real Secret-injected CronJob; stale cron docs are not runtime authority. |
| `scripts/render-equipment-page.py` | `verdify-lab-publisher` CronJob every ten minutes via `lab-publish-k3s.sh` -> `publish-site-content.sh`. | Secret key -> `POSTGRES_PASSWORD`/`DB_PASS`; wrapper -> `VERDIFY_DSN`. | Publish and hand-pin the new lab-publisher digest, sync through the GitOps release path, then prove one scheduled/one-shot Job. A dry run may target the current content file. | Re-pin the prior lab-publisher digest and sync. Direct default `/mnt/iris` paths are container compatibility paths, not workstation/repo-pod paths. |
| `scripts/render-zone-pages.py` | Same live lab-publisher chain. | Same Secret-to-DSN chain. | Same image/sync proof; dry run against the current zones directory. | Same prior-digest rollback; explicit output path required outside the publisher container. |
| `scripts/render-crop-profiles.py` | Same live lab-publisher chain. | Same Secret-to-DSN chain. | Same image/sync proof; dry run against the current crops directory and optional consistency check. | Same prior-digest rollback; explicit output path required outside the publisher container. |
| `scripts/vault-operations-writer.py` | Manual CLI only; no live scheduler or wrapper. | Require `VERDIFY_DSN`/`POSTGRES_PASSWORD`. Prefer the read-only agent DSN for the renderer's SELECT-only path. | No restart. Run `--dry-run` against the current laptop vault output path, then inspect the diff. | Revert the source commit. The historical `/mnt/iris/verdify-vault/operations` default is absent on current laptop/repo-pod surfaces. |

`publish-site-content.sh` retries a failed renderer three times, continues the remaining generators, then exits nonzero. Missing injection therefore becomes a visible failed Job rather than a silent partial success. Recent publisher failures were unrelated to these renderers or database authentication.

## Live shared-secret consumers

The following live workload templates reference `verdify-app-secrets.POSTGRES_PASSWORD`. Existing completed Jobs are instances of the listed CronJobs and do not add a distinct rotation owner.

| Workload | Environment names | Rotation action | Validation |
|---|---|---|---|
| `StatefulSet/verdify-db` | `POSTGRES_PASSWORD` | Do not restart for the password change. Change the existing `verdify` role password through a secure interactive database session; the updated Secret protects future pod starts. | DB pod stays Ready; local and service connections succeed with the new value. |
| `Deployment/verdify-ingestor` | `DB_PASSWORD`, `PGPASSWORD`, `POSTGRES_PASSWORD` | Recreate first after the role change using the existing `Recreate` strategy, preserving the single-writer invariant. | One Ready replica, Lease/single-writer proof, ESPHome connected, database task logs healthy, no new critical alert. |
| `Deployment/verdify-api` | `DB_PASS`, `POSTGRES_PASSWORD` | Roll after Secret update and role change. | Health/status endpoints and one read query return successfully. |
| `Deployment/verdify-mcp` | `DB_PASS`, `POSTGRES_PASSWORD` | Roll after Secret update and role change. | MCP health and a read-only tool call succeed. |
| `Deployment/verdify-planner` | `POSTGRES_PASSWORD` | Roll after Secret update and role change. | Planner DB preflight and tool-level health succeed; this alone does not clear issue #427. |
| `Deployment/verdify-setpoint-server` | `POSTGRES_PASSWORD` | Roll after Secret update and role change. | Health endpoint and read-only setpoint fetch succeed; do not create a second writer. |
| `Deployment/verdify-grafana` | `POSTGRES_PASSWORD` | Roll after Secret update and role change. | Datasource health and a known dashboard query succeed. |
| `CronJob/verdify-band-curve-refresh` | `PGPASSWORD` | Suspend before rotation; resume after deployment checks and run one controlled Job. | Job succeeds and materialized-view freshness advances. |
| `CronJob/verdify-db-backup` | `PGPASSWORD` | Suspend before rotation; resume and run one controlled backup. | Job succeeds, produces a nonempty dump, and the restore-validation policy remains satisfied. |
| `CronJob/verdify-ha-gap-backfill` | `POSTGRES_PASSWORD` | Suspend before rotation; resume after core services. | Controlled Job authenticates and completes within its bounded apply policy. |
| `CronJob/verdify-lab-publisher` | `DB_PASS`, `POSTGRES_PASSWORD` | Suspend before rotation; resume after the fail-closed image is pinned. | One scheduled/one-shot publish completes, including all three renderers. |
| `CronJob/verdify-vision` | `POSTGRES_PASSWORD` | Suspend before rotation; resume only after its independent image-pull defect is resolved or explicitly accept that it remains broken for that unrelated reason. | Authentication failure must not replace the known image-pull classification. |
| `Job/verdify-migrate` | `DB_PASS`, `POSTGRES_PASSWORD` | No standing pod to restart; every future migration Job must be created after the Secret update. | Next authorized migration Job authenticates before applying any migration. |

The live database role `verdify` is a login superuser and owns 4,791 relations. Creating a replacement role is not a safe shortcut; the rotation runbook changes the existing role in place.

## Completeness and safe probes

- Repository references were searched by exact filename across scripts, workflows, manifests, docs, and tests.
- Live Deployments, StatefulSets, CronJobs, and Jobs were enumerated by `secretKeyRef.name == verdify-app-secrets` and key `POSTGRES_PASSWORD`.
- Live `verdify-prod` has no `daily-summary-snapshot` CronJob.
- Recent ingestor logs show successful live daily-summary updates.
- Recent retained lab-publisher Jobs execute the three renderers with the Secret-to-DSN chain.
- Secret values were never read or emitted; only Secret/key and workload names were inspected.

Re-run the workload inventory before rotation because Jobs and deployment topology are time-sensitive.
