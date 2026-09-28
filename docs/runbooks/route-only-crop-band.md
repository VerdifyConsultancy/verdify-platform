# Prospective route-only crop-band observation

`research/planner-efficacy/route_only_crop_band.py` computes a bounded daily
comparison from the winter observer's immutable 06:00–24:00 Denver day artifact,
its preregistered route-only panel source, and its prospective frozen crop-target
source. It reuses `fixed_panel.py` to give each north/east/west database column
one vote per complete minute and requires 12 of 15 six-field database minutes
per eligible bin. Every one of the 72 bins remains in the denominator or carries
an unavailable reason in the underlying calculation. It never uses controller
credit, a changing house average, a current crop resolver, or an inferred target.

After a completed day has been captured, run the offline producer with the exact
archived bytes:

```sh
python research/planner-efficacy/route_only_crop_band.py \
  --instance "$ARCHIVE/instance.json" \
  --day "$ARCHIVE/days/YYYY-MM-DD.json" \
  --panel-source "$ARCHIVE/sources/panel-source" \
  --target-source "$ARCHIVE/sources/crop-target-source" \
  --output "$ARCHIVE/route-only/YYYY-MM-DD.json"
```

The output is a canonical, exclusive-created `RouteOnlyCropBandDiagnostic`.
Keep the day, instance, panel, and target bytes: their hashes in the diagnostic
identify inputs but do not independently authenticate them. The producer checks
registration hashes, the positive frozen target revision and all 4,320 target
bins, completed day/window and archive identity, exact three route fields, and
source-snapshot collection timing. Missing history fails closed. A route-only
source deliberately has no physical serial. Database flush timestamps cannot
prove individual probe callback freshness or stable physical installation.
The frozen target is a panel-mean reference; actual crop placement and on-chip
consumption remain unverified. This observation is never a physical-efficacy,
experiment, or causal result.

The API/MCP schema has a separate `route_only_crop_band_evidence` field. Its
bounded one-day reader returns `unavailable` when the publication function or
day row is absent, and rejects a malformed newest row without falling back.
The existing `physical_crop_band_evidence` remains unavailable. The site
labels a valid route result as observational and renders no route result when
the scorecard day differs. The earliest winter archive day is 2026-11-02, so
no qualifying prospective result exists today.

## Publication qualification

Migration 257 is currently an unsealed, fail-closed source draft. Its
append-only table stores only the summary diagnostic, artifact hash, frozen
target revision, and three route contributor revisions. The guarded owner
insert requires a complete prospective 06:00–24:00 Denver window and matching
pre-window lineage. It never grants runtime roles publication writes or raw
lineage reads; only the API runtime can execute the one-day read projection.
The latest publication alone is considered. The ingestor has no read or write
grant on this contract.

An isolated, network-denied logical restore of a migration-255 backup proved
the SQL shape, missing-day behavior, owner future-day rejection, and API versus
ingestor grants in a rollback transaction. It cannot provide production
successor hashes: the ordinary-login catalog digest includes numeric role OIDs
and object ACLs, which a logical restore does not preserve. The read-only live
catalog projection reproduced both sealed 255 hashes, but OID normalization
still left multiple ACL and function differences on the clone. Qualify the
exact 256 predecessor and 257 successor against the live catalog in a bounded
rollback-only transaction after the device writer window; advance the two
receipt rows and migration ledger atomically in the final forward migration.
Do not substitute clone hashes. Only publish a completed prospective day whose
retained source artifacts and fixed-panel result reproduce exactly. Do not
modify migration 255 or fabricate a September historical result.
