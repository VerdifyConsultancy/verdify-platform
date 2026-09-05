# Outdoor forecast verification contract 2 — #780

Migration 242 replaces indoor/outdoor VPD comparisons and duplicated sample
counts with versioned outdoor verification. It does not establish indoor response,
crop outcomes, causal control benefit or a new study protocol.

## Definition

The provider's [hourly parameter definitions](https://open-meteo.com/en/docs),
checked September 5, 2026, distinguish instantaneous temperature/RH/VPD from
preceding-hour mean shortwave radiation. The observation contract is:

- Instant weather: the outdoor one-minute bin beginning at the valid time. This
  is a bounded measurement approximation, not an exactly simultaneous probe.
- VPD: derive from outdoor temperature and RH within that bin, never indoor
  `vpd_avg`. Temperature uses Fahrenheit-to-Celsius conversion. Accepted sensor
  domain is -100–150°F and RH 0–100%; invalid/nonfinite inputs are unavailable.
- Solar: mean of all 60 preceding one-minute bins. A missing bin leaves hourly
  solar truth unavailable. A forecast fetched after that window began is excluded
  from solar verification. Do not fill gaps with zero or an indoor-light proxy.
- `fetched_at` records availability in Verdify, not provider issuance or model
  initialization. Those unrecorded identities cannot be reconstructed by renaming.
- Exact duplicate forecast vintages count once. Conflicting duplicate values are
  unavailable. A post-valid-time fetch cannot become a pre-valid forecast.
- Summary selection uses the latest vintage per valid time and lead bucket.
  `samples` means paired valid times, not forecast rows multiplied by telemetry.
  Solar selects independently from pre-window vintages, with its own fetch/lead
  fields; later weather vintages do not hide an eligible older solar forecast.
  Prospective solar nowcasts issued after the averaging window started are
  explicitly labeled and do not become corrected forecast priors.
- Daily error uses matched pairs; MAE is mean absolute hourly error, not absolute
  daily bias (opposite signed errors must not cancel).

The existing view column prefixes, function signature, OIDs, owners and grants are
preserved. New read-only views carry the narrower truth/metadata contract. The
existing 30-day forecast retention bounds this verification; it is not a
long-term immutable analysis store.

## Planner/public consumers

The gather script and forecast-page publisher require contract 2. Until migration
delivery they withhold the new calibration instead of treating an old function's
output as corrected truth. Prospective priors include decision/valid/available
timestamps, actual forecast lead, fetch age, matching lead bucket, paired-hour
count, observed-minute count and an availability reason. Fetch age above 120
minutes, missing calibration and conflicting vintages yield NULL corrected prior.
The old sample-count label `window_hours` is removed.

These are diagnostic candidate priors, not automatic control retuning. A
historical fixed decision must filter `fetched_at <= decision_at` **before**
latest-vintage selection. The current-time prior view is not a substitute for
frozen historical decision inputs. Do not silently change a locked study.

## Validation and release

Use the normal repository CI and:

```sh
SCORECARD_TEST_PG_BIN=/path/to/postgresql/bin python -m pytest \
  tests/test_forecast_verification_contract.py tests/test_migration_rollback_safety.py
```

The private-socket PostgreSQL fixture reproduces -8 kPa error for a perfect
outdoor forecast with indoor VPD 8 kPa, then proves zero error after migration.
It proves duplicate counts shrink from 12 to one, true lead rather than
observation age, missing RH, stale/future/conflicting vintages, instant versus
preceding-hour alignment, missing solar bins, non-cancelling MAE, ordinary
read-role access and full outer rollback with unchanged existing OIDs/ACLs.
These are synthetic regression proofs, not a production backtest.

Build affected ingestor/publisher/migrate source via Kaniko/Zot. Release compatible
consumers before the migration-image pin. Preserve prior definitions, source/pins,
fresh verified backup and the raw pre-change forecast/observation export before
the ledgered migration. The migration itself never rewrites raw data or tunes
controllers. Do not replay applied migrations or change the live cluster outside
GitOps. A post-commit rollback is a reviewed forward migration restoring captured
definitions, with old calibration explicitly withheld rather than relabeled.

Require exact-source Argo Synced+Healthy and current DB/planner/public readback.
Keep #780 open until frozen production inputs/outputs are hashed, lead-bucket
old-versus-corrected priors are reconciled, role/latency checks pass on real data
and publication is observed. The historical +1.453/+0.539 kPa comparison is a
prior capture, not a promised value for today's revised sampling/window contract.

## Frozen-input reconciliation

`research/planner-efficacy/forecast_replay.py` is an offline reconciliation tool,
not a production database client. It never discovers credentials or accepts a
DSN. First generate a bounded allowlisted export query for the historical decision
timestamps under review (at most a seven-day span):

```sh
python research/planner-efficacy/forecast_replay.py emit-sql \
  --decision 2026-09-04T12:00:00Z --decision 2026-09-05T12:00:00Z
```

Have the authorized database collector execute that query with unaligned,
tuples-only, quiet output (`psql -X -qAt -v ON_ERROR_STOP=1`) and preserve its JSON
outside Git. The query uses one repeatable-read, read-only transaction and a
120-second statement timeout. It exports explicit raw outdoor/forecast columns
and the old verifier's actual `v_climate_merged` inputs, not a fabricated proxy.
The single-house guard is required because the legacy views are unscoped. This
does not grant a repo pod new DB, exec, or cross-namespace authority. Do not
substitute the public 15-minute lifecycle or hourly sample exports: they cannot
recover valid-time one-minute truth or prove all 60 preceding solar bins.

Then, on an analysis host with PostgreSQL server binaries, run:

```sh
python research/planner-efficacy/forecast_replay.py replay \
  --input /private-evidence/forecast-input.json --pg-bin /path/to/postgresql/bin
```

Save the JSON report outside Git with the export. The runner creates and removes
only its own private-socket cluster, disables TCP, ignores inherited `PG*`
configuration, and does not use an existing database. The report includes SHA-256
input/tool/SQL identities, export metadata, row counts, old and corrected daily
and lead-bucket outputs, old/new correction functions and prospective priors at
each frozen decision. The repository SQL is unmodified; `now()` is bound to a
private stable clock and hourly `time_bucket` is emulated with UTC `date_bin`.
An outer-transaction migration rollback must restore baseline results and view
identities before the local commit/replay proceeds. This is not a production
backup restore, latency proof, or live acceptance receipt.

Review paired-hour counts, forecast availability/freshness, missing truth and
changed priors explicitly. Export bounds cannot attest that retention preserved
all requested rows. Observation event times are not observation arrival times;
late/backfilled/corrected observations remain an as-of limitation unless a
contemporaneous snapshot or arrival lineage is independently supplied. Old
conflicting-vintage tie selection is unspecified, and old/new windows and sample
denominators differ. Keep those limits in any published aggregate. Never publish
raw exports or present synthetic replay fixtures as a production backtest.
