-- Run only inside an explicit rollback-only transaction after exact281.
-- Native checks of M4 on production rows: reads only; the temporary table is
-- dropped by the rollback. Any failed check raises and rolls everything back.
CREATE TEMP TABLE irrigation_minutes_qualification (name text PRIMARY KEY, value numeric) ON COMMIT DROP;
DO $fixture$
DECLARE
    today constant date := (now() AT TIME ZONE 'America/Denver')::date;
    owner_oid constant oid := (SELECT datdba FROM pg_database WHERE datname = current_database());
    started timestamptz;
BEGIN
    -- Catalog: owner, definer reader, exact readers, nothing for PUBLIC or the api login.
    IF (SELECT count(*) FROM pg_class c
        WHERE c.oid IN ('public.v_irrigation_minutes_daily'::regclass, 'public.v_irrigation_day_coverage'::regclass)
          AND c.relowner = owner_oid AND c.relkind = 'v') <> 2
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid = 'public.fn_irrigation_minutes_daily(date,date)'::regprocedure
             AND p.proowner = owner_oid AND p.prosecdef AND p.provolatile = 's' AND p.proisstrict
             AND p.proconfig = ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT array_agg(DISTINCT format('%s:%s:%s', c.relname, a.grantee::regrole, a.privilege_type)
                            ORDER BY format('%s:%s:%s', c.relname, a.grantee::regrole, a.privilege_type))
           FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
           WHERE c.oid IN ('public.v_irrigation_minutes_daily'::regclass, 'public.v_irrigation_day_coverage'::regclass)
             AND a.grantee <> owner_oid)
          IS DISTINCT FROM ARRAY[
              'v_irrigation_day_coverage:verdify_ingestor_runtime:SELECT',
              'v_irrigation_day_coverage:verdify_mcp_runtime:SELECT',
              'v_irrigation_minutes_daily:verdify_ingestor_runtime:SELECT',
              'v_irrigation_minutes_daily:verdify_mcp_runtime:SELECT']
       OR (SELECT array_agg(format('%s:%s', a.grantee::regrole, a.privilege_type) ORDER BY a.grantee::regrole::text)
           FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
           WHERE p.oid = 'public.fn_irrigation_minutes_daily(date,date)'::regprocedure AND a.grantee <> owner_oid)
          IS DISTINCT FROM ARRAY['verdify_ingestor_runtime:EXECUTE', 'verdify_mcp_runtime:EXECUTE']
       OR has_table_privilege('verdify_api_runtime_login', 'public.v_irrigation_minutes_daily', 'SELECT')
       OR has_table_privilege('verdify_api_runtime_login', 'public.v_irrigation_day_coverage', 'SELECT')
       OR has_function_privilege('verdify_api_runtime_login', 'public.fn_irrigation_minutes_daily(date,date)', 'EXECUTE')
       OR NOT has_table_privilege('verdify_mcp_runtime_login', 'public.v_irrigation_minutes_daily', 'SELECT')
       OR NOT has_table_privilege('verdify_ingestor_runtime_login', 'public.v_irrigation_day_coverage', 'SELECT')
       OR NOT has_function_privilege('verdify_mcp_runtime_login', 'public.fn_irrigation_minutes_daily(date,date)', 'EXECUTE')
       OR NOT has_function_privilege('verdify_ingestor_runtime_login', 'public.fn_irrigation_minutes_daily(date,date)', 'EXECUTE')
    THEN RAISE EXCEPTION '281 native catalog check failed'; END IF;

    -- The bounded reader returns exactly the view's rows for the last 30 days.
    IF EXISTS (SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN today - 29 AND today
               EXCEPT SELECT * FROM public.fn_irrigation_minutes_daily(today - 29, today))
       OR EXISTS (SELECT * FROM public.fn_irrigation_minutes_daily(today - 29, today)
                  EXCEPT SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN today - 29 AND today)
    THEN RAISE EXCEPTION '281 reader differs from the view'; END IF;

    -- Gallons equal v_water_run_accounting's per start day and line.
    IF EXISTS (
        WITH accounting AS (
            SELECT (r.run_start AT TIME ZONE 'America/Denver')::date AS local_day, r.relay_slug,
                   round(sum(r.meter_attributed_gal)::numeric)::integer AS gal
            FROM public.v_water_run_accounting r
            WHERE r.run_start >= (today - 29)::timestamp AT TIME ZONE 'America/Denver'
            GROUP BY 1, 2
            HAVING sum(r.meter_attributed_gal) IS NOT NULL
        ), lines(relay_slug, line_id) AS (
            VALUES ('drip_wall', 'L01'), ('drip_wall_fert', 'L02'), ('mister_center', 'L04'),
                   ('drip_center', 'L05'), ('drip_center_fert', 'L06'), ('mister_south', 'L07'),
                   ('mister_south_fert', 'L08'), ('mister_west', 'L09'), ('mister_west_fert', 'L10'), ('fog', 'L11')
        )
        SELECT 1
        FROM accounting a
        JOIN lines l USING (relay_slug)
        JOIN public.fn_irrigation_minutes_daily(today - 29, today) m
          ON m.local_day = a.local_day AND m.line_id = l.line_id
        WHERE m.gal IS DISTINCT FROM a.gal)
    THEN RAISE EXCEPTION '281 gallons differ from v_water_run_accounting'; END IF;

    -- 2026-05-29, the wall drip's last run, and today's honest coverage.
    IF NOT EXISTS (SELECT 1 FROM public.fn_irrigation_minutes_daily(date '2026-05-29', date '2026-05-29')
                   WHERE line_id = 'L01' AND minutes > 0 AND pulses > 0)
       OR (SELECT coverage FROM public.v_irrigation_day_coverage
           WHERE greenhouse_id = 'vallery' AND local_day = today) IS DISTINCT FROM 'partial'
       OR EXISTS (SELECT 1 FROM public.fn_irrigation_minutes_daily(today - 399, today) m
                  JOIN public.v_irrigation_day_coverage c USING (greenhouse_id, local_day)
                  WHERE c.coverage = 'no_data')
    THEN RAISE EXCEPTION '281 native day checks failed'; END IF;

    started := clock_timestamp();
    PERFORM count(*) FROM public.fn_irrigation_minutes_daily(today - 29, today);
    INSERT INTO irrigation_minutes_qualification
    VALUES ('reader_30d_ms', round(extract(epoch FROM clock_timestamp() - started) * 1000));
    started := clock_timestamp();
    PERFORM count(*) FROM public.fn_irrigation_minutes_daily(today - 399, today);
    INSERT INTO irrigation_minutes_qualification
    VALUES ('reader_400d_ms', round(extract(epoch FROM clock_timestamp() - started) * 1000));
    started := clock_timestamp();
    PERFORM count(*) FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN today - 29 AND today;
    INSERT INTO irrigation_minutes_qualification
    VALUES ('view_30d_ms', round(extract(epoch FROM clock_timestamp() - started) * 1000));
    INSERT INTO irrigation_minutes_qualification
    SELECT 'rows_400d', count(*) FROM public.fn_irrigation_minutes_daily(today - 399, today);
END $fixture$;
SELECT json_build_object(
    'checks', json_build_array('catalog_and_grants', 'reader_equals_view_30d', 'gallons_equal_run_accounting_30d',
                               'wall_drip_2026_05_29', 'today_partial', 'no_data_days_without_rows'),
    'all_passed', true,
    'rows_400d', (SELECT value FROM irrigation_minutes_qualification WHERE name = 'rows_400d'),
    'reader_30d_ms', (SELECT value FROM irrigation_minutes_qualification WHERE name = 'reader_30d_ms'),
    'reader_400d_ms', (SELECT value FROM irrigation_minutes_qualification WHERE name = 'reader_400d_ms'),
    'view_30d_ms', (SELECT value FROM irrigation_minutes_qualification WHERE name = 'view_30d_ms'));
