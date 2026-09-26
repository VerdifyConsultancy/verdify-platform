# September 4 wetting interruption — unresolved hold (#778 / #749)

The reproducible [v2 result](results-wetting-incident-2026-09-04-v2.json) establishes
recorded runtime zeros under hot/dry conditions, **not verified equipment
coverage, actual continuous off-state, or a cause**. The earlier
[v1 result](results-wetting-incident-2026-09-04.json) is retained, superseded by
the explicit equipment-coverage limitation in v2.
`physical_wetting_proof_allowed` is false. No controller setting, cap, mode,
firmware or device was changed during this investigation.

## Observed timeline

The requested interval is September 4 14:30–18:45 America/Denver, or
2026-09-04 20:30 through September 5 00:45 UTC. Adjacent hourly rows are context;
they cannot be clipped into half-hour results from the aggregate alone.

| Denver hour | House °F / VPD kPa | Fog minutes | Center mist minutes | Recorded flow minutes |
| --- | --- | --- | --- | --- |
| 14:00–15:00 | 87.593 / 2.155 | 57.056 | 16.021 | 47.113 |
| 15:00–16:00 | 98.337 / 5.004 | 0 | 0 | 0 |
| 16:00–17:00 | 93.504 / 4.282 | 0 | 0 | 0 |
| 17:00–18:00 | 90.967 / 3.828 | 0 | 0 | 0 |
| 18:00–19:00 | 83.861 / 2.303 | 36.017 | 0 | 29.061 |

Fans and vent each have 60 recorded minutes in all three zero-wetting hours.
The current hourly exporter uses `coalesce(..., 0)` for absent equipment
intervals. Climate sample counts do not establish equipment-state coverage.
Thus these zeros cannot distinguish a confirmed off-state from missing state
evidence; raw equipment rows/continuity are a necessary part of the causal join.
The v2 report explicitly sets `equipment_observation_coverage_verified: false`.
The exporter also carries the last known on-state to the export boundary without
an observation-age limit, so 60 recorded fan/vent minutes are not independent
proof of continuous physical operation either.
The five-minute VPD peak is 15:30 Denver (21:30 UTC): 100.48°F, 5.395 kPa,
94.17°F outdoors, five source samples. Both counters are constant across all
37 five-minute samples from 15:00 through 18:00 inclusive: cumulative meter
600 gallons and mister-today estimate 157.38 gallons. Those different counters
are not measured limits, and do not establish a 600-gallon budget exhaustion.

The ingestor's retained logs cover the interval: 3,595 timestamped records from
20:30:28.120995 to 00:44:54.762747 UTC. The allowlisted projection contains 120
events: 56 empty-occupancy latch/replay messages, 54 empty-occupancy push messages,
five reconnect messages, four expected disconnects and one unexpected loss.
There are no matched occupied messages. **A push log is not device confirmation.**

Reconnect messages occurred at 15:02:57, 16:57:47, 17:46:18, 18:08:18 and
18:26:13 Denver. They report gaps of 8, 30, 4, 0 and 462 seconds respectively.
These are rounded reported gaps, not reconstructed device uptime. The last two
are after the three-hour dry interval. They do not describe a continuous
three-hour transport loss, nor prove why an autonomous controller stopped wetting.

## Hypothesis disposition

| Hypothesis | Evidence and remaining uncertainty |
| --- | --- |
| Sustained occupied command | Weakened by repeated empty latch/push logs; firmware occupancy readback, inhibit state and manual events remain missing. Not ruled out. |
| Continuous transport outage | Not supported by the retained connection stream or ongoing hourly telemetry. Reboots, clock validity and generation-specific control behavior are still unverified. |
| Soft water budget | Effective consumed budget and safety-VPD inputs are missing. Current source has an emergency bypass; do not infer exhaustion from an unrelated meter total. |
| Absolute hard ceiling | Mister-today and actual consumed hard-limit/reset lineage must be joined. Defaults do not establish the deployed value. |
| Leak / irrigation / fertilizer | Raw interlock and actuator-confirmation records are missing. An hourly recorded fertilizer zero or no stdout message does not clear these guards. |
| Time validity / vent compatibility / manual intervention | Actual consumed flags, control decision records and requested→sent→confirmed transitions are missing. Vent-open time alone does not explain why wetting worked before the interruption. |

Firmware interlock messages flow through `esp32_logs` and Loki when enabled;
they are not generally mirrored in ingestor stdout. That stream's absence of
interlock messages cannot eliminate the hypotheses. The code audit records
source-file hashes, not proof that the physical device ran that exact source.

## Provenance and reproduction

Public inputs are the September 5 retained captures of:

- `https://lab.verdify.ai/static/data/hourly-performance/greenhouse-performance-hourly-30d-latest.csv`
- `https://lab.verdify.ai/static/data/verdify-sample-7d-climate.csv`

The URLs are mutable. Reproduction uses the frozen byte streams and SHA-256
identities in the result, not newly downloaded data with the same filenames.
Those original captures and the redacted log projection remain outside Git.
Only aggregate outcomes, selected transport timestamps and hashes are committed.

Log source was pod `verdify-ingestor-75cd57f455-nm6wk`, UID
`3a3ba9b1-f0bc-4848-a640-f569db0ff040`, namespace `verdify-prod`. The pod started
September 1 03:13:34 UTC and its ingestor container had zero restarts at capture.
The read-back image ID was
`registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:c83f336e03a70d2f797afbbdf7f2eea1dd2e117a8fe206770c6149e39e26c174`.
This identifies the observed log producer, not the physical controller firmware.

`wetting_incident.py` has no network, database, Kubernetes or device client.
An authorized operator supplies timestamped logs, without changing logging,
firmware or device settings:

```sh
set -o pipefail
kubectl -n verdify-prod logs verdify-ingestor-75cd57f455-nm6wk \
  -c ingestor --timestamps --since-time=2026-09-04T20:30:00Z |
  python research/planner-efficacy/wetting_incident.py project-logs \
    --output /private-evidence/incident-events.json

python research/planner-efficacy/wetting_incident.py analyze \
  --hourly /private-evidence/hourly.csv \
  --climate /private-evidence/climate-7d.csv \
  --events /private-evidence/incident-events.json \
  --output /private-evidence/incident-report.json
```

The projector normalizes timestamp offsets to UTC before selecting records.
It hashes the raw in-window stream and matched source records but never saves
free-form messages. Only recognized application-log prefixes and allowlisted
event patterns produce typed output; quoted plan narratives do not. Original
raw logs remain subject to source retention, so a later live re-query is not
guaranteed to recover them. The redacted projection is the frozen analysis input.
Existing output files are never overwritten. Earlier exploratory captures remain
preserved separately, rather than silently rewritten.

The checked-in result was reproduced byte-for-byte with the environment cleared
(`env -i`, only PATH/TZ supplied). The test suite checks offset/nanosecond handling,
redaction, narrative rejection, missing rows/values, duplicate and UTC/local
inconsistency, DST ambiguity, counter semantics and overwrite refusal. Incomplete
evidence yields an unknown interruption result, not zero-filled observations.

## Bound hold and next evidence

`wetting_incident_778_disposition` is now a mandatory Gate P readiness
prerequisite. The current proof-packet producer emits it as **incomplete**;
missing or incomplete disposition blocks every physical-proof boundary. Existing
bounded Gate R recovery remains distinct and unchanged. Positive test fixtures
are hypothetical qualified packets, not claims that this incident is resolved.
These guard/producer changes still require normal CI, publication and collector
adoption; no deployed guard update is claimed by this report.

Releasing this source-bound hold requires a reviewed incident disposition backed
by the missing joined evidence, then current #749/#641 acceptance. It cannot be
released by raising a cap, changing operating mode, relabeling this report,
discarding the prerequisite or using a collector CLI override. No physical
experiment is authorized by this campaign analysis.

Next obtain the authorized raw climate/action/interlock/equipment rows, effective
cfg limits and reset epochs, plan delivery lineage, manual/occupancy evidence and
as-of forecast vintage for this exact UTC window. Reconcile 15:00 onset and 18:00
recovery before naming a cause or implementing its bounded remedy. #778 remains
open because the full raw causal join is incomplete. This report and the explicit
hold advance #749; they do not satisfy or close physical readiness.
