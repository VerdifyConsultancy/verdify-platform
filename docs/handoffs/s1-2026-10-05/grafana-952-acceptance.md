# Grafana monitoring identity — #952 accepted October 5, 2026

All three issue criteria are proven by the [sanitized acceptance receipt](evidence/grafana-monitoring-identity-acceptance-20261005.json). The original dedicated service account 2, token 1, org 1 and Viewer membership survive a separately scoped pod replacement. Account metadata identifies `sa-endpoint-monitor`, with service-account status and no Grafana admin privilege. The existing credential was used without rotation.

The product source is `c8fc15e6a81d29b16e23d6db078893160704b4ea`; the promoted production render and full-sync revision is `b5ffc37e483437615476cba49723a47d0eb2823f`. Argo application `verdify-prod-dark` was Synced and Healthy after replacement. Owned sources are `deploy/k8s/components/grafana/grafana.yaml` and `grafana-data.yaml`: replicas 1, Recreate, a 120-second readiness window and persistent `/var/lib/grafana`. Claim `verdify-grafana-data`, UID `acad919d-b6d2-4824-b7c7-a2b2ab460103`, remained Bound to `pvc-acad919d-b6d2-4824-b7c7-a2b2ab460103` throughout. The receipt records both actual Grafana and renderer image digests.

| Observation (UTC) | Protected `/api/user` | Anonymous `/api/user` | Authenticated `/api/admin/settings` | Local / external auth evidence | Related firing alerts |
|---|---:|---:|---:|---|---:|
| Before repair, 21:31:45 | 401 | 401 | 403 | 0 / 0 | 4 |
| Repaired, before separate replacement, 21:59:23 | 200 | 401 | 403 | 1 / 1 | 0 |
| After separate replacement, 22:09:52 | 200 | 401 | 403 | 1 / 1 | 0 |

The replacement used the exact Deployment UID `87d1186a-2baf-4922-86c9-793f2e71673c`, current resourceVersion and singleton preconditions. Scale-down occurred at 22:06:36.169633 UTC; old pod `a9fc655d-00c7-4359-8b8a-e79e581f9f1c` was fully deleted at 22:06:38.478834 UTC before replicas were restored to 1 at 22:06:38.602152 UTC. New pod `e6dcd55b-e7fd-4d52-974d-824d3e38b6f0` became Ready and available after the configured readiness window. Deployment spec, claim, image digests and original identity remained unchanged. Ingestor UID `ae448e3c-f615-4896-929c-385c15a9c3b4` was unchanged by this replacement.

Fresh post-replacement probe sample times were 22:09:50.932 UTC at `local` and 22:09:52.247 UTC at `external-cloudflare`. Each vantage reported authenticated 200, anonymous 401 and authenticated evidence success 1. Direct protected API readback identified the original login, org 1 and `isGrafanaAdmin=false`; persisted metadata separately confirmed actual account 2, token 1 and Viewer membership. UI availability alone is not the acceptance proof.

## Recovery

Retain the exact Grafana PVC and PV through any rollback. Never prune/delete the claim or replace the persistent mount with `emptyDir`. If an image/config reversion becomes necessary, revert those owned sources while retaining replicas 1 and Recreate, then coordinate an exact-revision, non-pruning Argo sync and repeat protected/negative and both-vantage probes. For replacement recovery, inspect the same Deployment and restore replicas to 1; do not start a duplicate replacement. Keep the original monitoring credential.

## First release and remaining sprint work

The [first-release receipt](evidence/verified-first-release-receipt.json), [build provenance](evidence/final-c8-build-provenance.json), [promotion](evidence/final-c8-promotion-receipt.json) and [publisher pin](evidence/final-c8-publisher-pin-receipt.json) bind the c8 source to the promoted revision. The full, non-pruning sync completed 127 resources and six actual fresh hooks at 22:03:40 UTC; migrations 271–273 match their source hashes. Nine smoke checks passed and a 300-second ingestor observation found zero schema-validation errors. The [independent fleet socket receipt](evidence/proxmox-fleet-esp32-established-readonly-receipt.json) found exactly one established ESP32 connection across eight hosts and their discovered network namespaces.

This completes #952, not the sprint. Initial c8 callback gaps were delayed, not permanent: periodic callbacks yielded 12 immediate plus 9 staged genuine transition rows, all 21 terminal complete. The successor callback fix accelerates initial replay correctness and remains separately subject to build, promotion and live acceptance. No two-hour policy credit is claimed before its upcoming restart. The natural 00:00 UTC vision run and evening brief remain required for #951 and #950; manual recovery evidence does not substitute for them.
