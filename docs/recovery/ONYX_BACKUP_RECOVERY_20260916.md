# Verdify backup recovery — September 16, 2026

The September 15 backup failed during the original Onyx recovery window with temporary hostname-resolution failures. The current and failed Job scripts have the same SHA256, `4cb9d68767583aaffd401905403d5d2c09f10f436fa8bf1b7f5822885cb0875a`, and already include the bounded temporary-DNS retry correction. No new backup implementation change was necessary in this qualification.

A fresh Job used the deployed CronJob implementation, with a qualification deadline and post-backup archive/hash checks. It succeeded on its first dump attempt. No application, database-writer, ingestion or actuator configuration changed.

## Measured result

- Producer Job: `verdify-backup-recovery-1789536394`, UID `74f962eb-29bc-4319-8e29-3c7affa2071b`.
- Job start/completion: 2026-09-16T05:26:34Z / 2026-09-16T05:28:07Z; **93 seconds** including startup.
- Archive: `/backups/verdify-20260916T052647Z.dump`.
- Size: **280,246,363 bytes**.
- SHA256: `8fc044360621bae06deb07e7c429b8f009099c79bd711bf0a12519085dcd9348`.
- `pg_restore --list` passed. A separate read-only reader on `vm-k3s-node4` independently validated the archive and its hash.

Both independent Prometheus collectors returned fresh successful backup timestamps, and the applicable backup failure/staleness alert cleared. The successful producer and readback Jobs were removed with exact UID/resourceVersion guards after their receipts were retained. The archive remains on the existing backup PVC; no new data volume was created.

Private evidence is under `onyx-forensics/` in `verdify-backup-recovery-20260916.json` and `backup-alert-recovery-20260916.json`.

## Scope limits

This verifies fresh backup production, archive listing and independent byte integrity. It does not establish a full database restore, application transaction recovery, abrupt host loss or NAS failure tolerance. Those remain separate requirements in the Onyx recovery matrix.
