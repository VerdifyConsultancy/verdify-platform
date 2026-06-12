# Derived history reconcile

Use `scripts/reconcile-derived-history.py` when schema or scoring logic has
evolved and Verdify needs historical derived rows recalculated from canonical
raw history.

The script is dry-run by default. It opens per-day transactions, calls the
current ingestor daily refresh implementation, reports changed columns, and
rolls back unless `--apply` is passed.

## Scope

Recomputed:

- `daily_summary` derived columns, including graded compliance v2 fields
- `daily_zone_compliance` rows written by the same daily refresh path
- `utility_cost` monthly rollups from `daily_summary`
- optionally, refresh-function/materialized surfaces:
  `refresh_relay_stuck`, `refresh_climate_merged`,
  `refresh_greenhouse_state`, `mv_zone_band_grade`, `mv_band_curve`

Not recomputed:

- raw telemetry gaps from Home Assistant; use the HA gap backfill job/script
- `plan_journal` outcome or anchor scores, because those are frozen planner
  evidence unless a migration explicitly re-anchors them
- synthetic `climate_action_log` rows

## Safe Operator Flow

Prefer dev for the first full-history pass. Dev is a nightly prod-restored copy,
so this shows the likely blast radius without touching the live writer DB.

```bash
# Terminal 1
scripts/verdify-db.sh dev --tunnel

# Terminal 2
export PGPASSWORD="$(kubectl -n verdify-dev get secret verdify-app-secrets \
  -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
DB_HOST=127.0.0.1 DB_PORT=5434 DB_NAME=verdify DB_USER=verdify \
  .venv/bin/python scripts/reconcile-derived-history.py \
  --all-history --limit-days=14 --log-level=INFO
```

If the sample looks sane, run a full dev dry-run:

```bash
DB_HOST=127.0.0.1 DB_PORT=5434 DB_NAME=verdify DB_USER=verdify \
  .venv/bin/python scripts/reconcile-derived-history.py \
  --all-history --log-level=INFO
```

For prod, dry-run first and save the JSON summary in the incident or change
notes:

```bash
# Terminal 1
scripts/verdify-db.sh prod --tunnel

# Terminal 2
export PGPASSWORD="$(kubectl -n verdify-prod get secret verdify-app-secrets \
  -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
DB_HOST=127.0.0.1 DB_PORT=5433 DB_NAME=verdify DB_USER=verdify \
  .venv/bin/python scripts/reconcile-derived-history.py \
  --all-history --log-level=INFO
```

Apply only after reviewing the dry-run:

```bash
DB_HOST=127.0.0.1 DB_PORT=5433 DB_NAME=verdify DB_USER=verdify \
  .venv/bin/python scripts/reconcile-derived-history.py \
  --all-history --apply --refresh-matviews --log-level=INFO
```

## Validation

```bash
scripts/verdify-db.sh prod -c "
  SELECT date, temp_avg, compliance_pct, compliance_v2_attributable_pct,
         cost_total, updated_at
    FROM daily_summary
   ORDER BY date DESC
   LIMIT 10;
"

scripts/verdify-db.sh prod -c "
  SELECT date, count(*)
    FROM daily_zone_compliance
   WHERE date >= current_date - 14
   GROUP BY date
   ORDER BY date DESC;
"

scripts/verdify-db.sh prod -c "
  SELECT month, category, amount_usd, kwh, gallons
    FROM utility_cost
   ORDER BY month DESC, category;
"
```

If only materialized/function surfaces need a bounce:

```bash
DB_HOST=127.0.0.1 DB_PORT=5433 DB_NAME=verdify DB_USER=verdify \
  .venv/bin/python scripts/reconcile-derived-history.py \
  --days=1 --skip-daily --skip-utility-cost --apply --refresh-matviews
```
