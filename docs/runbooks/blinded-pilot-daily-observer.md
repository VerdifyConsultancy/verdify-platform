# Blinded pilot daily observer preparation (#784)

Status: source preparation, not an installed scheduler, design lock, draw or
launch. The earliest retained coverage-supported warm candidate is June 1–July
30, 2027 (30 adjacent pairs, 60 Denver days). Historical June–July climate
coverage is 60/60 eligible days, 30/30 pairs and 4,309/4,320 bins; selector/as-of
weather/equipment, future authenticated targets, exact 18-hour paired covariance,
carryover and joint power remain missing. Do not replace those inputs with a
forecast, assumed power, winter packet or synthetic assigned-day outcome.

`warm-daily-observer-preparation-v1.json` records a **proposed** daily 00:45
Denver read-only operator run after the previous `[06:00,24:00)` 64,800-second
window. The first candidate due time is June 2, 2027 00:45 MDT and the last is
July 31 00:45 MDT. The Research lead owns daily integrity publication. Before
operation, bind the actual qualified design/source, observer and archive custody
in its immutable protocol. Keep the historical timed-shadow/canary/A/A waiver;
this preparation introduces no new physical or calendar gate.

## Current usable operator path

Use the existing read-only laptop DB connection contract. No new service, role,
credential grant or CronJob is installed. The current blinded-analyst view omits
unfrozen assignments; this standalone operator query needs the existing
assignment denominator access and makes no analyst-only qualification claim.

Choose a separate private **warm** archive outside web roots. Existing directories
and all original receipts are retained. Name each daily output
`receipt-<actual-UTC-capture-timestamp>.json`; the immutable payload contains the
actual database `as_of`. Never supply a fake snapshot clock, backdate a missing
run, shift the locked calendar or reuse an existing filename.

```sh
VERDIFY_DB_BACKEND=kube VERDIFY_KUBECTL='kubectl --context vallery' \
  python scripts/experiment-v2-reconcile.py \
  --experiment-id 45039c86-c1d9-52f6-a0a9-d94a17bc4b14 \
  --output /private/warm-pilot/receipt-ACTUAL-UTC-TIMESTAMP.json
```

`not_design_locked` preserves an empty draft. `reconciled` accounts for the
snapshot, including future scheduled days; neither means pilot completion.
Completed days without outcome/freeze/evidence stay missing. Every assignment
survives failures, fallback, rescue, reset evidence, zero exposure and zero/null
endpoints. A structural refusal leaves the DB and original files unchanged.
Preserve stderr and a separate source-stamped operational deviation when a run
fails. Catch-up retains actual capture time and is late when more than 24 hours
after the relevant day ends; it never replaces the day or its original receipt.

## Offline append-only custody manifest

The offline archive tool reads every `receipt-*.json` file and verifies the
existing strict receipt schema/domain hash, experiment identity and coherent
single design lock. Duplicate JSON keys are refused at every nesting depth
before schema/hash checks; a last-value parser cannot redefine receipt bytes.
It indexes duplicate files rather than dropping them,
refuses a conflicting same-time snapshot, and preserves originals on every
failure. It accesses no DB, provider, controller, reveal or network service.

```sh
python scripts/experiment-v2-reconcile-archive.py \
  --directory /private/warm-pilot \
  --experiment-id 45039c86-c1d9-52f6-a0a9-d94a17bc4b14 \
  --output /private/warm-pilot/manifest-ACTUAL-UTC-TIMESTAMP.json
```

Each new manifest is exclusive mode0600 and fsynced with its directory. Its hash
binds compact sorted JSON manifest bytes with the
`verdify-experiment-v2-reconciliation-archive-v1` + NUL domain. Retain previous
manifests, every daily receipt, failed-run artifacts and their exact source
snapshot. An index proves only integrity of the files present; it does not prove
that an omitted receipt existed, every due run occurred, backup succeeded or a
pilot completed. The full locked assignment/calendar reconciliation and declared
cadence remain the separate denominator and timeliness sources.

The CLI verifies outcome/evidence/export lineage; live safety, choice details,
maintenance, weather extremes, source/model/target changes, resource eligibility
and actual paired-backup custody need their separate source-stamped receipts.
Use existing safety/backup owner records; do not invent a success or silently
claim these sources were measured by this tool. Off-host retention of the local
warm archive remains unqualified until an actual restore readback is retained.
The existing paired database backup covers database-owned rows, not this local
receipt directory.

After the final real day, preserve the final archive index and exact frozen
export/analyzer identities. Safe stopped admission/baseline or facility closure,
one-way reveal and twice-reproduced analysis remain #785's separate work. No
archive index qualifies those steps or permits a narrower efficacy denominator.

## Separate winter packet

The registered `verdify-winter-feasibility-2026-27` stays November 2–December 31,
2026, passive and route-only, pinned to its original extractor/protocol and
source bytes. Preserve its staged/backup/restore receipt and existing 10Gi PVC.
No warm manifest command touches that directory or PVC. The successful October
1 no-due Job supplies no November outcome; first collection is November 3
00:30 MST for the November 2 observation. Do not re-register or restage it to
make this warm preparation appear executed.
