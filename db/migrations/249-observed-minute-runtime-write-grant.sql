-- The daily ingestor owns this diagnostic, but migration 245 added its column
-- after the column-scoped runtime grants in migration 217. Restore only the
-- missing write permission; keep the table-wide UPDATE boundary closed.
GRANT UPDATE (climate_observed_minute_metrics)
    ON public.daily_summary TO verdify_ingestor_runtime;
