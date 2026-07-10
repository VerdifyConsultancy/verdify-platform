# Production database credential rotation closeout — 2026-07-10

This artifact intentionally contains only boolean results, counts, resource
metadata, and immutable Git references. It contains no credential value,
reversible derivative, decrypted secret, or key material.

## Authority and gate

- Human authorization gate: `g-prod-db-credential-rotation-20260709`, approved
  `rotate-now` at `2026-07-10T12:21:20Z`.
- Pre-rotation encrypted authority:
  `jvallery/agents@3d342882b794fd7e089b4cdadb85571510e4f4a2`.
- Rotated encrypted authority PR: `jvallery/agents#2904`.
- Merged encrypted authority:
  `jvallery/agents@deae5f50848597fb90057b2914c105113a23e3c0`.
- Independent PR critic: approved the exact encrypted head after confirming that
  only the decrypted database password changed and no plaintext entered Git,
  the PR, or Actions output.

## Rotation result

| Proof | Result |
|---|---|
| `replacement_uri_safe` | `true` |
| merged encrypted authority matches live Secret | `true` |
| non-password live Secret keys unchanged | `true` |
| new credential authenticates directly over TCP | `true` |
| old credential is rejected directly over TCP | `true` |
| rollback used | `false` |
| live Secret resourceVersion | `67776214` |

The existing `verdify` role was changed in place through stdin-only `psql`
handling. The database StatefulSet was not restarted.

## Long-running consumer acceptance

| Consumer | Ready | DB/read proof | Auth errors | Notes |
|---|---:|---:|---:|---|
| ingestor | `1/1` | DB pool and ESP32 reconnect | `0` | Lease holder matches the new sole pod |
| API | `2/2` | public `/health` 200 | `0` | serial rollout complete |
| MCP | `2/2` | `SELECT 1` | `0` | local HTTP service reachable |
| planner | `2/2` | `SELECT 1` on both pods and `/health` 200 | `0` | existing planner alerts intentionally remain |
| setpoint-server | `1/1` | DB pool and direct `SELECT 1` | `0` | independent `/setpoints` backend bug noted below |
| Grafana | `1/1` | live TimescaleDB datasource query | `0` | public `/api/health` 200 |

The ingestor Lease renews under the replacement pod identity and no second
device writer exists.

## Scheduled consumer acceptance

| CronJob | Controlled Job | Result | Final schedule state |
|---|---|---|---|
| band-curve refresh | `verdify-band-curve-refresh-rotation-20260710` | materialized view refreshed | resumed |
| database backup | `verdify-db-backup-rotation-20260710` | `179.1M` dump, first attempt | resumed |
| HA gap backfill | `verdify-ha-gap-backfill-rotation-20260710` | completed with no auth failure | resumed |
| lab publisher | `verdify-lab-publisher-rotation-20260710` | 322 pages and object-store delta sync | resumed |
| Vision | none | not run | suspended |

Vision remains suspended because its prior ImagePullBackOff is independent of
the rotation. The failed old Vision Job was removed before the rotation and was
not recreated.

## Residuals and cleanup

- Across all six restarted deployments, the post-cutover database-auth error
  scan returned `0`.
- API, lab, and graphs public health endpoints returned HTTP `200`.
- The setpoint-server `/setpoints` endpoint still selects the retired Docker
  psql backend inside k3s and returns 500, while its async DB pool, health probe,
  and direct database read pass. This is a separate software issue, not a
  credential-rotation failure.
- The temporary SOPS age-key copy at the laptop operator boundary was removed
  with secure deletion after acceptance completed.
