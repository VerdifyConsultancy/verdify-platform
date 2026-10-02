CREATE OR REPLACE FUNCTION _timescaledb_functions.policy_job_stat_history_retention(job_id integer, config jsonb) RETURNS void LANGUAGE plpgsql SET search_path TO pg_catalog, pg_temp AS $native_policy$
DECLARE
    search_point TIMESTAMPTZ;
    id_found BIGINT;
BEGIN
  PERFORM set_config('lock_timeout', coalesce(config->>'lock_timeout', '5s'), true /* is local */);

  -- We need to prevent concurrent changes on this table when running this retention job
  -- We take an AccessExclusiveLock at the start since we TRUNCATE later
  LOCK TABLE _timescaledb_internal.bgw_job_stat_history IN ACCESS EXCLUSIVE MODE;

  search_point := now() - (config->>'drop_after')::interval;

  id_found := _timescaledb_functions.job_history_bsearch(search_point);

  IF id_found IS NULL THEN
    RETURN;
  END IF;

  -- Build a table that contains only rows younger than the max age
  -- and satisfy the constraints on number of successfull and failures
  -- for each job. Since the table is ordered, we can use the "id"
  -- column to find out what records to remove.
  CREATE TEMP TABLE __tmp_bgw_job_stat_history ON COMMIT DROP AS
  WITH
    enumerated AS (
      SELECT *,
             row_number() OVER (
                 PARTITION BY j.job_id, j.succeeded
                 ORDER BY j.execution_finish DESC
             ) AS row_number
        FROM _timescaledb_internal.bgw_job_stat_history j
       WHERE id >= id_found)
  SELECT id, e.job_id, pid, execution_start, execution_finish, succeeded, data
    FROM enumerated e
   WHERE succeeded AND row_number <= (config->>'max_successes_per_job')::int
      OR NOT succeeded AND row_number <= (config->>'max_failures_per_job')::int
  ORDER BY id;

  TRUNCATE _timescaledb_internal.bgw_job_stat_history;

  INSERT INTO _timescaledb_internal.bgw_job_stat_history
  SELECT * FROM __tmp_bgw_job_stat_history;

END
$native_policy$;
