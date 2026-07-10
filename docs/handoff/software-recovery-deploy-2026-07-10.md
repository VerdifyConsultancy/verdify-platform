# Greenhouse software recovery deployment — 2026-07-10

Status at 15:48 MDT: application rollout and the single authorized firmware
OTA are complete. Immediate acceptance is green. The 2-hour and 48-hour
firmware observation gates remain open; do not promote the candidate to
`last-good.ota.bin` before the 48-hour gate.

## Released artifacts

- Canonical git state: `main` at `fffee28b0313f5cb2e031425f005bca3f0ccc5b1`
  when this closeout was written. PRs #448–#450 delivered DLI, planner, and
  firmware behavior; #451/#452 and #455–#457 promoted the runtime images.
- Firmware: `2026.7.10.1500.09ee886`, OTA accepted at approximately 15:03 MDT.
  The binary was built from repository SHA `09ee886`; the reviewed firmware
  tree landed in #450 (`0c8c301` merge lineage). Candidate SHA-256:
  `4c412460b19472c94a1dbb01fa5fb7c629aa05aa3cdde7a6ace5b1b35ecef65d`.
- API image:
  `ghcr.io/verdifyconsultancy/verdify-api@sha256:7d4610c087720989d186060b5deb1bf8f74b3d60aaf25c38280ae16e4871c97d`.
- MCP image:
  `ghcr.io/verdifyconsultancy/verdify-mcp@sha256:b6b6ea2f7827578a9b4a8331880f6c17884c28391e9d597a8513c0446a424ac6`.
- Pre-change database backup:
  `verdify-db-backup-release-20260710-1904`, object
  `/backups/verdify-20260710T190337Z.dump` (179.6 MiB).
- Applied migrations: 186 and 189–196.
- Rollback firmware remains the previous known-good binary at
  `firmware/artifacts/last-good.ota.bin`, SHA-256
  `08121f9738c686d86f5437193ca40f57ae143d499c5e4599ead6131b09221222`.

## Live acceptance evidence

- Firmware health sweep: 27 pass, 0 fail, 0 warn at 15:47 MDT; exact version
  readback, 4/4 probes, no Modbus timeouts, and no new deploy-window sensor,
  reboot, push, band, or heap alerts.
- Since first candidate telemetry: 41 diagnostic samples, zero uptime
  regressions, and maximum sample gap 65 seconds. This is materially different
  from the prior 5–6 minute reconnect concern.
- Center drip is disabled: `sw_irrig_center_enabled=0`; start hour/minute and
  every center fert/schedule value are zero.
- Center mister remains a climate actuator. It ran three times after OTA under
  `VENT_COOL_FOG_ASSIST`/`VENT_COOL_MIST_ASSIST`; center drip, all fert mister
  paths, and the fert master were false at those timestamps.
- Fertigation is constrained to the wall-drip path in firmware. The current
  plan schedules no fertigation (`irrig_wall_fert_*` readbacks are zero).
- `band_track_fraction=0`; `sw_dehum_vent_hold_enabled=1`. Night dry-out is
  enabled but still needs the overnight observation window.
- Planner health is `ok` with zero required failures. Effective control is 39
  parameters from `iris-20260710-1456` plus one current one-shot overlay from
  `iris-oneshot-20260710-1508`.
- DLI is deliberately unavailable until the broken interior sensor is
  replaced. The API exposes no exterior/fixture proxy as interior truth.
- Exactly one ingestor pod is Ready and holds `verdify-ingestor-writer`.
- API, Hermes, ingestor, MCP, and planner deployments are fully Ready.
- No unresolved critical/high alerts. PostgreSQL remains Ready at restart
  count 7; those restarts occurred during diagnosis before the query fences.
- Public evidence now fails soft: `/api/v1/public/evidence-snapshot` returned
  HTTP 200 in 13.3 seconds, slow optional metrics were null, and zero API
  queries remained active afterward.
- ArgoCD is Healthy. The only remaining OutOfSync object is the shared
  `Namespace/verdify-prod`, whose tracking labels belong to the Agent Fleet
  namespace owner; do not overwrite or sync that namespace from this app.

## Deliberately incomplete

- `CronJob/verdify-lab-publisher` is suspended in live state and Git. A guarded
  verification run proved that its internal per-step retries still create
  unacceptable production DB pressure. The job was stopped before an OOM,
  three orphaned scorecard backends were narrowly cancelled, and the DB
  recovered without another restart. GitHub issue #454 is the source of truth
  for the remaining publisher retry/concurrency fix and unsuspend acceptance.
- The firmware 2-hour observation is due after 17:03 MDT on 2026-07-10.
- The 48-hour bake is due after 15:03 MDT on 2026-07-12. Only after that gate
  passes should the candidate replace the rollback floor.
- Night dehumidification needs an overnight humidity/dew-margin review; the
  feature is enabled, but daytime acceptance cannot prove the overnight result.

## Verification commands

```bash
export VERDIFY_DB_BACKEND=kube
export EXPECTED_FW_VERSION=2026.7.10.1500.09ee886
make sensor-health SINCE='2 hours'

curl -fsS https://api.verdify.ai/api/v1/public/planner-health | jq .
curl -fsS https://api.verdify.ai/api/v1/public/evidence-snapshot | jq .

kubectl -n verdify-prod get deployment verdify-api verdify-hermes-iris \
  verdify-ingestor verdify-mcp verdify-planner
kubectl -n verdify-prod get lease verdify-ingestor-writer
kubectl -n verdify-prod get cronjob verdify-lab-publisher
```
