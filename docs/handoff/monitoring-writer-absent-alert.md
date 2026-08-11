# Handover → monitoring-stack agent: out-of-band writer-absent + telemetry-stall alerts

> **STATUS UPDATE 2026-07-11:** after a SECOND silent multi-hour outage
> (2026-07-11 01:59–09:08 UTC DB/Longhorn outage, zero alerts), the gap is now
> closed **in-lane** by `deploy/k8s/overlays/prod/writer-watchdog.yaml` — a
> CronJob independent of the ingestor that checks the
> `verdify-ingestor-writer` Lease age (>180 s → CRITICAL `writer_absent`) and
> climate freshness (>600 s → CRITICAL `telemetry_stall`), paging via direct
> `alert_log` rows + k8s Events when the DB is down. The PrometheusRule below
> is STILL WANTED as the fully out-of-cluster backstop (this repo has no RBAC
> in `observability`), and Slack wiring waits on the token-secret sealing.

**From:** `verdify-platform` (L1 audit P0) · **Date:** 2026-06-17 · **Tracker:** `jvallery/agents` monitoring-stack; cross-ref `VerdifyConsultancy/verdify-platform#343`, GitHub issues.

## Why this is needed (the P0 gap)

The Verdify greenhouse's alert engine (`sensor_offline`, `setpoint_unconfirmed`,
`band_drift`, etc.) runs **inside the ingestor process** (`ingestor/tasks/alerts.py`,
`confirmation.py`), on the 300 s task loop. So when the **ingestor itself is down**, the
monitor that would notice is down too — **a dead writer self-silences.** There is no
in-repo, out-of-band alert for "the sole ESP32 writer is gone."

**This was not theoretical:** on 2026-06-17 a storage incident (dead Synology `/volume1`
SSD tier + a stuck RWO PVC) stranded the sole writer for **~1 hour** during a deploy. The
greenhouse stayed safe (the ESP32 runs its band autonomously on-chip), but telemetry
capture, setpoint dispatch, and **all alerting** were dark the entire time — and nothing
paged. A writer-absent alert off the out-of-band exporter would have fired in ~3 min.

## The signal already exists — it just isn't alerted on

`verdify-writer-exporter` is a **DaemonSet in ns `observability`** (out-of-band, independent
of the ingestor) that exports the ESP32 writer-connection gauge:

```
verdify_esp32_writer_estab        # gauge, per node/instance
sum(verdify_esp32_writer_estab)   # cluster-wide count of established ESP32 writer connections
```

- `sum == 1` → healthy (exactly one writer, the single-writer invariant).
- `sum == 0` → **writer ABSENT** (no process holds the ESP32 connection). ← the unalerted gap.
- `sum >= 2` → **split-brain** (two writers — device thrash risk). ← believed already covered
  by the `#241` split-brain alarm; **please confirm** and keep.

Prometheus in `observability` already scrapes this exporter (verified: the metric is queryable
via the in-cluster Prometheus). No new exporter is needed for the writer-absent alert.

## Asks

### 1. Writer-absent alert (NEW — the P0)
A `PrometheusRule` on `sum(verdify_esp32_writer_estab) == 0`, Slack-routed to `#greenhouse`,
independent of the ingestor. Suggested rule:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: verdify-writer-presence
  namespace: observability           # wherever the writer-exporter rules live
  labels:
    # match your Prometheus ruleSelector (this repo ships NO PrometheusRules; they live here)
    release: <kube-prometheus-stack release>
spec:
  groups:
    - name: verdify-writer
      rules:
        - alert: VerdifyEsp32WriterAbsent
          expr: sum(verdify_esp32_writer_estab) == 0
          for: 3m                     # tolerate a normal Recreate roll; page if it persists
          labels:
            severity: critical
            domain: verdify-greenhouse
          annotations:
            summary: "No ESP32 writer — the sole greenhouse writer is down"
            description: >-
              sum(verdify_esp32_writer_estab)=0 for 3m. Telemetry capture, setpoint
              dispatch, and the in-ingestor alert engine are ALL down. The ESP32 keeps
              running its last NVS band autonomously (no immediate plant risk), but
              nothing is being recorded or tuned. Runbook below.
        - alert: VerdifyEsp32WriterSplitBrain      # keep/confirm vs #241
          expr: sum(verdify_esp32_writer_estab) >= 2
          for: 1m
          labels: { severity: critical, domain: verdify-greenhouse }
          annotations:
            summary: "Two ESP32 writers — split-brain device thrash risk"
```

`for: 3m` is deliberate: a normal ingestor Recreate (single-writer rollout) has a brief
zero-writer window; 3 min avoids paging on routine deploys while catching a real outage.

### 2. Telemetry-stall alert (complements #1 — "connected but not writing")
The writer can hold the ESP32 connection (`sum==1`) yet stop persisting rows (wedged event
loop, dead DB conn). Catch this with **climate-row freshness**, which must come from a source
**outside the ingestor**. Two options (your call — pick the one that fits the stack):

- **(a) Scrape the API health endpoint.** `api.verdify.ai` `/health` already computes
  `SELECT extract(epoch FROM now()-max(ts)) FROM climate` (stale at >300 s). If it exposes
  a metric (or add a tiny blackbox/json scrape), alert on staleness.
- **(b) postgres_exporter custom query** against `verdify-db` (read-only):
  `SELECT extract(epoch FROM now()-max(ts)) AS verdify_climate_age_seconds FROM climate`,
  then `alert: VerdifyTelemetryStall  expr: verdify_climate_age_seconds > 600  for: 5m`.

Either way: `severity: critical`, route to `#greenhouse`. Threshold ~600 s (climate writes
every ~5–60 s normally; >10 min = genuinely stalled).

### 3. Alertmanager routing
Route `domain: verdify-greenhouse` (or your equivalent label) to the `#greenhouse` Slack
receiver, same path as the existing Verdify domain alerts. No inhibition vs the in-ingestor
alerts (these are the out-of-band backstop and must fire even when the ingestor is silent).

## Runbook (attach to the alert)
When `VerdifyEsp32WriterAbsent` fires:
1. **Greenhouse is not in immediate danger** — the ESP32 enforces its last NVS band on-chip
   without the writer. Don't panic-OTA.
2. Check the ingestor: `kubectl -n verdify-prod get pods -l app.kubernetes.io/component=ingestor -o wide`.
   Look for `Pending` (storage/scheduling), `CrashLoopBackOff`, or `Init` (PVC mount).
3. Storage is the usual cause (see the 2026-06-17 incident): `kubectl -n verdify-prod describe pod <ingestor>`
   → `FailedMount`/`ProvisioningFailed` = Synology iSCSI issue → escalate to the storage lane.
4. Confirm single-writer after recovery: `sum(verdify_esp32_writer_estab)` returns to `1`.

## Notes / boundaries
- This repo (`verdify-platform`) ships **no PrometheusRules** — Verdify alerting is either the
  in-ingestor engine or lives in monitoring-stack. The writer-presence rule belongs **here**
  (monitoring-stack), reading the `observability`-namespace exporter.
- Keep these alerts **out-of-band** (do not co-locate in the ingestor) — that independence is
  the entire point.
- Cross-ref: `docs/reviews/lane1-architecture-audit-2026-06-16.md` §8 (P0 #2), and the
  `verdify-platform` GitHub issues monitoring-stack row.
