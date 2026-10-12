# Verdify runtime inventory — 2026-10-12

Read-only snapshot collected around 17:38–17:50 UTC. No service, device, database, GitOps, or credential mutation performed.

## Result

Kubernetes availability is green while the ordinary planner-to-controller delivery path is held. All 15 Deployments and the legacy DB StatefulSet have their desired replicas Ready. Argo reports Synced/Healthy at `b5bdb3c65b243c230b7864fa81b95f926b2e68ff`. Its last operation was a selective seven-CronJob `ci-velocity-cron` sync at 00:30 UTC; the last recorded full sync was October 11 at 14:39 UTC, revision `9f799919fb23404a7f71cf02668b8f4a0091fc5d`.

## Highest priority

- The retained writer run `76d18133852b490680bce2639e6d8397` is halted (`unapproved candidate appeared`). It belongs to the previous pod/source/session, generation3, and its approval expired October7 00:13 UTC. The active pod uses source `f48c6bf0257b25ed2820689154ca3502507129c6`, generation1. Current preview has17 desired changes. All12 original stage requests are actually confirmed in the DB, with no later non-ESP32 requests for those fields. No recovery manifest exists.
- The sampled ingestor log has21 `blocked_stage` events and313 individual cfg-readback drift messages over109 minutes. No plan-sourced write has been confirmed since October6 23:43 UTC, despite39 Iris plans created in the rolling seven-day window. Current effective journal plan is `iris-20261012-0710`. The private planner worker is healthy but explicitly non-authoritative, and its graph-run table has no rows in the last7days.
- Climate is fresh (about52seconds at capture), yet30 fields are entirely null across1,390 rows/24h: south temperature/RH/VPD, hydro metrics, PAR/DLI, runoff and leaf instrumentation among them. South soil probe1 has895 zero readings then no update after15:44 UTC; soil probe2 and west are absent. `v_dli_current` says unavailable/source_reading_missing. Six priority1 instrumentation requirements are blocked.
-40 unresolved application alerts are warnings, including sensor/soil offline and heap pressure; no unresolved high/critical row. This does not establish plant safety. API `/health` says `ok` while reporting0% current-day compliance and planner_score83.2. Recent complete days show only9.6–17.4% compliance and12.1–13.5 cold-stress hours/day; distinguish deficient sensing/target policy from confirmed physical failure.

## Deployment and operations

- Sole ingestor replicas1/Recreate, fresh `verdify-ingestor-writer` lease held by that pod. No second client was opened. Independent device socket census was not run.
- API2/2 and MCP2/2 are ready. API image source is `325558d4d8f730b0c42afff0de87e08eda3b5aa1`. MCP readyz reports required tools present, DB ok, auth enforce, no auth misconfiguration. Both current release digests match repository release pins. All exact workload digests are in runtime-summary.json.
- Firmware telemetry reports `2026.10.2.0637.620a218a-wifi-bound`, uptime~10.16days, active_probe_count4, probe_healthOK. Runtime DB remains `verdify-db-0` on node7; migration ledger283 rows, max sequence275, last migration October6 09:58 UTC.
- All10 CronJobs inventoried; firmware builder intentionally suspended. Vision's three latest retained scheduled jobs captured2/2 cameras and completed analysis; each camera has29 observations over7days. Prior October5 failed jobs are retained history.
- Lab publisher latest completed job succeeds with content delta uploads and~339s total runtime. Five October11 DeadlineExceeded jobs remain retained. API/Grafana/lab endpoints are available internally; rendered user/physical acceptance was not performed.
- Backup job October12 08:17 UTC succeeded on attempt1 and logged paired399.9MiB dump/roles/checksum files. Multiple old September26–28 restore probes remain Failed; no new restore qualification was run. Backup exporter has periodic readiness timeout/BrokenPipe errors.
- Ingestor CPU/GPU telemetry still queries unreachable historical VM addresses every5minutes; recorded152 no-route warnings in the bounded log. Hermes restarted twice; last exit Error October11 22:15 UTC. It now serves, but logs show skill-edit errors and no allowlists warning.
- Experiment v2 remains draft/shadow/closed/component disabled, pinned to old firmware identity in its record; zero current proof receipts/work/epochs. No GateP work executed.

## Evidence limits

Requested logs since24h with tail1500; high-volume API/DB/ingestor logs cover only minutes to hours. No full estate historical log backend query or physical inspection. Registry OCI label readback unavailable because the Mac Docker credential helper is missing; anonymous origin requests require authentication. No Secret data was accessed. `v_data_trust_ledger` timed out at8seconds and was not retried heavily. All evidence is sanitized; JSON summaries preserve exact identifiers, times, images, and query outputs.

## Rehearsal inventory

All-namespace CNPG scan finds no Verdify greenhouse/rehearsal Cluster object. `verdify-db-rehearsal` remains Active with6 completed/failed pods,80 retained jobs, one scaled-zero `verdify-s2-grafana-proof` Deployment and one2GiB spool PVC. No running rehearsal pod or DB StatefulSet exists. This is retained qualification debris, not a currently hibernating CNPG cluster. Auth restore drills are separately hibernated and are unrelated.
