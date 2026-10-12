-- Retry owner only, through the existing production read-only SQL path.
-- Aggregate metadata only: no equipment rows exported or rewritten.
-- The fixed-source cutoff is an owner receipt observation, not a claim that
-- every earlier row came from the API or that later rows prove physical truth.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '1s';
SELECT jsonb_build_object(
  'schema', 'verdify-965-retained-light-history-bounds-v1',
  'observed_at', clock_timestamp(),
  'transaction_read_only', current_setting('transaction_read_only'),
  'database', current_database(),
  'prior_audit_cutoff', '2026-10-11T15:33:39.925243Z',
  'fixed_source_observed_at', '2026-10-12T19:50:36.717489Z',
  'schema_columns', (
    SELECT jsonb_agg(jsonb_build_object('column', column_name, 'type', data_type)
                     ORDER BY ordinal_position)
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'equipment_state'
  ),
  'retained_lights', (
    SELECT jsonb_agg(to_jsonb(stream) ORDER BY greenhouse_id, equipment)
      FROM (
        SELECT greenhouse_id, equipment, count(*) AS retained_rows,
               min(ts) AS first_ts, max(ts) AS last_ts,
               count(*) FILTER (WHERE ts <= '2026-10-11T15:33:39.925243Z'::timestamptz)
                 AS rows_at_or_before_prior_audit,
               count(*) FILTER (
                 WHERE ts > '2026-10-11T15:33:39.925243Z'::timestamptz
                   AND ts <= '2026-10-12T19:50:36.717489Z'::timestamptz
               ) AS rows_between_prior_audit_and_fixed_source_observation,
               count(*) FILTER (WHERE ts > '2026-10-12T19:50:36.717489Z'::timestamptz)
                 AS rows_after_fixed_source_observation
          FROM public.equipment_state
         WHERE equipment IN ('grow_light_main', 'grow_light_grow')
         GROUP BY greenhouse_id, equipment
      ) AS stream
  ),
  'row_origin_classified', false,
  'physical_runtime_or_resource_credit_qualified', false,
  'history_or_study_rewritten', false
);
ROLLBACK;
