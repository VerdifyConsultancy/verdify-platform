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

The API/MCP schema has a separate `route_only_crop_band_evidence` field, default
`unavailable`; the existing `physical_crop_band_evidence` remains unavailable.
No production reader or diagnostic row is installed yet. The earliest winter
archive day is 2026-11-02, so no qualifying prospective result exists today.

## Publication qualification

Before adding a live reader, restore an exact migration-255 production backup to
an isolated, network-denied PostgreSQL clone. On that clone, test a forward-only
publication migration that stores immutable day/instance/source hashes and the
typed diagnostic, grants only a one-day read projection to the API runtime, and
leaves ingestor and ordinary runtime roles without raw lineage-table access or
publication writes. Select the newest revision before validation; never fall
back to an older valid row. Run positive and negative ordinary-login tests,
derive and pin both new boundary digests from the reviewed catalog, and verify
the owning migration runner stamps its ledger in the same transaction. Then add
the bounded API/MCP adapter with validation and timeout handling, and render an
explicitly **observational** site block. Only publish a completed prospective
day whose retained source artifacts and fixed-panel result reproduce exactly.
Do not modify migration 255 or fabricate a September historical result.
