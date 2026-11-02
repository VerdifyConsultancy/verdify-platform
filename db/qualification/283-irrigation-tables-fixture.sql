-- Run only inside an explicit rollback-only transaction after exact283, as the
-- superuser owner: scripts/qualify-migration-successor.py --native-fixture runs it
-- at the end of its final session. Native checks of M3 on the production catalog:
-- the five tables and the line-state view, the seeds and the slot sequence, every
-- grant exactly as listed (relations, columns and functions), the minutes view and
-- reader still read by 281's logins only, equal for 30 days and, with no ledger
-- row and no command yet, keeping 281's topology cause for every run; and a
-- dashboard login that reaches only its sessions and the irrigation reads and
-- writes. Then each runtime login under SET SESSION AUTHORIZATION attests and
-- reads, writes and is refused as granted. Every row, the temporary table and
-- every authorization change end with the rollback; the last line reports the
-- result to the qualifier.
CREATE TEMP TABLE irrigation_tables_qualification (name text PRIMARY KEY, value numeric) ON COMMIT DROP;
DO $catalog$
DECLARE
    owner_oid constant oid := (SELECT datdba FROM pg_database WHERE datname = current_database());
    today constant date := (now() AT TIME ZONE 'America/Denver')::date;
    login constant regrole := 'verdify_dashboard_runtime_login'::regrole;
    reached text[];
    started timestamptz;
BEGIN
    IF (SELECT count(*) FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relowner = owner_oid AND NOT c.relrowsecurity
          AND (c.relname::text, c.relkind::text) IN (('irrigation_line', 'r'), ('irrigation_slot', 'r'),
              ('irrigation_command', 'r'), ('irrigation_push', 'r'), ('irrigation_ledger', 'r'),
              ('v_irrigation_line_state', 'v'))) <> 6
       OR EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                  WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace
                    AND c.relname IN ('irrigation_line', 'irrigation_slot', 'irrigation_command', 'irrigation_push',
                                      'irrigation_ledger'))
       OR (SELECT format('%s|%s|%s|%s|%s', s.data_type, s.start_value, s.min_value, s.max_value, s.cycle)
           FROM pg_sequences s WHERE s.schemaname = 'public' AND s.sequencename = 'irrigation_slot_slot_id_seq')
          IS DISTINCT FROM 'integer|4|1|65535|f'
    THEN RAISE EXCEPTION '283 native catalog check failed'; END IF;

    -- The seeds: irrigation 3, the D-IRP-06 caps, L01 and L04 on with no check, L05 and L06
    -- reserved, and the three vanda-degraded slots on L04.
    IF (SELECT string_agg(format('%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s', line_id, name, zone, kind, relay,
                                 equipment, water_system, device_idx, clean_partner, reserved, enabled, vpd_member,
                                 schedule_member, daily_cap_min, vpd_reserve_min), E'\n' ORDER BY line_id COLLATE "C")
        FROM public.irrigation_line)
       IS DISTINCT FROM concat_ws(E'\n',
           'L01|Drip • Wall|wall|drip|wall_drips|drip_wall|wall_drip_clean|0||f|t|||60|',
           'L02|Drip • Wall (Fert.)|wall|fert_drip|wall_drips_fertilized|drip_wall_fert|wall_drip_fert|1|L01|f|f|||0|',
           'L03|Valve • Fert. Master|supply|master|fertilizer_master_valve|fert_master_valve||2||f|f|||0|',
           'L04|Mister • Center|center|mister|center_mister|mister_center|center_mister_clean|3||f|t|t|f|120|60',
           'L05|Drip • Center|center|drip|center_drips|drip_center|center_drip_clean|4||t|f|||30|',
           'L06|Drip • Center (Fert.)|center|fert_drip|center_drips_fertilized|drip_center_fert|center_drip_fert|5|L05|t|f|||0|',
           'L07|Mister • South Wall|south|mister|south_wall_mister|mister_south|south_mister_clean|6||f|f|f|f|45|15',
           'L08|Mister • South Wall (Fert.)|south|fert_mister|south_wall_mister_fertilized|mister_south_fert|south_mister_fert|7|L07|f|f|||0|',
           'L09|Mister • West Wall|west|mister|west_wall_mister|mister_west|west_mister_clean|8||f|f|f|f|45|15',
           'L10|Mister • West Wall (Fert.)|west|fert_mister|west_wall_mister_fertilized|mister_west_fert|west_mister_fert|9|L09|f|f|||0|',
           'L11|Fog (AquaFog XE 2000)|house|fog|fog_rly|fog|center_fog|10||f|f|||0|')
       OR EXISTS (SELECT 1 FROM public.irrigation_line
                  WHERE greenhouse_id <> 'vallery' OR manual OR paused_until IS NOT NULL OR checked_by IS NOT NULL
                     OR checked_at IS NOT NULL OR check_note IS NOT NULL OR source <> 'system'
                     OR updated_by <> 'migration')
       OR (SELECT string_agg(format('%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s', slot_id, greenhouse_id, line_id,
                                    enabled, days, start_kind, start_min, on_s, count, gap_s, prewet_s, flush_s,
                                    degraded_only, label, manual, source || ':' || updated_by), E'\n' ORDER BY slot_id)
           FROM public.irrigation_slot)
          IS DISTINCT FROM concat_ws(E'\n',
              '1|vallery|L04|t|127|solar_noon|-120|45|4|60|0|0|t|vanda-degraded|f|system:migration',
              '2|vallery|L04|t|127|solar_noon|0|45|4|60|0|0|t|vanda-degraded|f|system:migration',
              '3|vallery|L04|t|127|solar_noon|120|45|4|60|0|0|t|vanda-degraded|f|system:migration')
       OR EXISTS (SELECT 1 FROM public.irrigation_command) OR EXISTS (SELECT 1 FROM public.irrigation_push)
       OR EXISTS (SELECT 1 FROM public.irrigation_ledger)
    THEN RAISE EXCEPTION '283 native seed check failed'; END IF;

    -- Every grant, exactly as listed: relations and sequences, columns, functions.
    IF EXISTS (
        WITH expected(relname, grantee, privilege) AS (
            SELECT r, d, 'SELECT' FROM unnest(ARRAY['irrigation_line', 'irrigation_slot', 'irrigation_command',
                   'irrigation_push', 'irrigation_ledger', 'v_irrigation_line_state']) r
            CROSS JOIN unnest(ARRAY['verdify_dashboard_runtime', 'verdify_mcp_runtime', 'verdify_ingestor_runtime']) d
            UNION ALL
            SELECT 'v_water_event_attribution', d, 'SELECT'
            FROM unnest(ARRAY['verdify_dashboard_runtime', 'verdify_mcp_runtime']) d
            UNION ALL
            SELECT 'irrigation_slot', d, p FROM unnest(ARRAY['verdify_dashboard_runtime', 'verdify_mcp_runtime']) d
            CROSS JOIN unnest(ARRAY['INSERT', 'UPDATE', 'DELETE']) p
            UNION ALL
            SELECT 'irrigation_command', d, 'INSERT'
            FROM unnest(ARRAY['verdify_dashboard_runtime', 'verdify_mcp_runtime']) d
            UNION ALL
            SELECT 'irrigation_slot_slot_id_seq', d, 'USAGE'
            FROM unnest(ARRAY['verdify_dashboard_runtime', 'verdify_mcp_runtime']) d
            UNION ALL
            VALUES ('irrigation_push', 'verdify_ingestor_runtime', 'INSERT'),
                   ('irrigation_ledger', 'verdify_ingestor_runtime', 'INSERT'),
                   ('irrigation_push_revision_seq', 'verdify_ingestor_runtime', 'USAGE'),
                   ('irrigation_ledger_id_seq', 'verdify_ingestor_runtime', 'USAGE'),
                   ('v_irrigation_minutes_daily', 'verdify_mcp_runtime', 'SELECT'),
                   ('v_irrigation_minutes_daily', 'verdify_ingestor_runtime', 'SELECT')
        ), actual AS (
            SELECT c.relname::text, a.grantee::regrole::text, a.privilege_type::text
            FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE c.relnamespace = 'public'::regnamespace AND a.grantee <> c.relowner
              AND c.relname IN ('irrigation_line', 'irrigation_slot', 'irrigation_command', 'irrigation_push',
                                'irrigation_ledger', 'v_irrigation_line_state', 'v_water_event_attribution',
                                'v_irrigation_minutes_daily', 'irrigation_slot_slot_id_seq',
                                'irrigation_push_revision_seq', 'irrigation_ledger_id_seq')
        )
        (SELECT * FROM expected EXCEPT SELECT * FROM actual) UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected))
       OR EXISTS (
        WITH expected(relname, attname, grantee) AS (
            SELECT 'irrigation_line', c, d
            FROM unnest(ARRAY['enabled', 'vpd_member', 'schedule_member', 'daily_cap_min', 'vpd_reserve_min',
                              'paused_until', 'manual', 'source', 'updated_by', 'updated_at', 'reason']) c
            CROSS JOIN unnest(ARRAY['verdify_dashboard_runtime', 'verdify_mcp_runtime']) d
            UNION ALL
            SELECT 'irrigation_line', c, 'verdify_dashboard_runtime'
            FROM unnest(ARRAY['checked_by', 'checked_at', 'check_note']) c
            UNION ALL
            SELECT 'irrigation_command', c, 'verdify_ingestor_runtime'
            FROM unnest(ARRAY['status', 'sent_at', 'fail_reason', 'args']) c
            UNION ALL
            SELECT 'irrigation_push', c, 'verdify_ingestor_runtime'
            FROM unnest(ARRAY['pushed_at', 'confirmed_at', 'reject_code']) c
            UNION ALL
            SELECT 'irrigation_ledger', c, 'verdify_ingestor_runtime'
            FROM unnest(ARRAY['ended_at', 'open_s', 'pulses', 'outcome', 'reason', 'event_seq']) c
        ), actual AS (
            SELECT c.relname::text, t.attname::text, a.grantee::regrole::text
            FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid CROSS JOIN LATERAL aclexplode(t.attacl) a
            WHERE c.relnamespace = 'public'::regnamespace AND t.attnum > 0 AND a.privilege_type = 'UPDATE'
              AND c.relname IN ('irrigation_line', 'irrigation_slot', 'irrigation_command', 'irrigation_push',
                                'irrigation_ledger')
        )
        (SELECT * FROM expected EXCEPT SELECT * FROM actual) UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected))
       OR EXISTS (SELECT 1 FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid
                  CROSS JOIN LATERAL aclexplode(t.attacl) a
                  WHERE c.relnamespace = 'public'::regnamespace AND t.attnum > 0 AND a.privilege_type <> 'UPDATE'
                    AND c.relname IN ('irrigation_line', 'irrigation_slot', 'irrigation_command', 'irrigation_push',
                                      'irrigation_ledger'))
       OR (SELECT array_agg(a.grantee::regrole::text ORDER BY a.grantee::regrole::text COLLATE "C")
           FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
           WHERE p.oid = 'public.fn_mcp_armed_assignment_context()'::regprocedure AND a.grantee <> p.proowner)
          IS DISTINCT FROM ARRAY['verdify_ingestor_runtime', 'verdify_mcp_runtime']
       OR (SELECT array_agg(a.grantee::regrole::text ORDER BY a.grantee::regrole::text COLLATE "C")
           FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
           WHERE p.oid = 'public.fn_irrigation_minutes_daily(date,date)'::regprocedure AND a.grantee <> p.proowner)
          IS DISTINCT FROM ARRAY['verdify_ingestor_runtime', 'verdify_mcp_runtime']
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid = 'public.fn_irrigation_minutes_daily(date,date)'::regprocedure
             AND p.proowner = owner_oid AND p.prosecdef AND p.provolatile = 's' AND p.proisstrict
             AND p.proconfig = ARRAY['search_path=pg_catalog, public, pg_temp'])
    THEN RAISE EXCEPTION '283 native grant check failed'; END IF;

    -- Through its own grants or PUBLIC's, the dashboard login reaches its sessions, the
    -- irrigation tables, the line state, 194's per-event attribution and the slot sequence,
    -- and no SECURITY DEFINER function outside an extension. A failure names the difference.
    reached := ARRAY(
        SELECT object FROM (
            SELECT n.nspname || '.' || c.relname AS object
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
               AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_namespace'::regclass AND d.objid = n.oid
                     AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e')
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
               AND CASE WHEN c.relkind = 'S'
                        THEN has_sequence_privilege(login, c.oid, 'USAGE,SELECT,UPDATE')
                        ELSE has_table_privilege(login, c.oid,
                                 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                             OR has_any_column_privilege(login, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')
                   END
            UNION ALL
            SELECT 'function ' || p.oid::regprocedure::text
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE p.prosecdef
               AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_namespace'::regclass AND d.objid = n.oid
                     AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e')
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
               AND has_function_privilege(login, p.oid, 'EXECUTE')
        ) reach ORDER BY object COLLATE "C");
    IF reached IS DISTINCT FROM ARRAY['public.dashboard_session', 'public.irrigation_command',
                                      'public.irrigation_ledger', 'public.irrigation_line', 'public.irrigation_push',
                                      'public.irrigation_slot', 'public.irrigation_slot_slot_id_seq',
                                      'public.v_irrigation_line_state', 'public.v_water_event_attribution'] THEN
        RAISE EXCEPTION '283 dashboard login reaches %', array_to_string(reached, ', ');
    END IF;

    -- One line-state row per line; nothing routes irr_state or irr_events yet.
    IF (SELECT count(*) FROM public.v_irrigation_line_state) <> 11
       OR (SELECT count(DISTINCT line_id) FROM public.v_irrigation_line_state) <> 11
       OR NOT EXISTS (SELECT 1 FROM public.v_irrigation_line_state WHERE line_id = 'L04' AND relay_at IS NOT NULL)
    THEN RAISE EXCEPTION '283 native line-state check failed'; END IF;

    -- The redefined minutes: the reader equals the view for the last 30 days and, with no
    -- ledger row and no command yet, every run keeps 281's topology cause and a NULL slot.
    IF EXISTS (SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN today - 29 AND today
               EXCEPT SELECT * FROM public.fn_irrigation_minutes_daily(today - 29, today))
       OR EXISTS (SELECT * FROM public.fn_irrigation_minutes_daily(today - 29, today)
                  EXCEPT SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN today - 29 AND today)
       OR EXISTS (SELECT 1 FROM public.fn_irrigation_minutes_daily(today - 399, today) m
                  WHERE m.slot_id IS NOT NULL
                     OR m.cause IS DISTINCT FROM CASE WHEN m.line_id IN ('L02', 'L03') THEN 'feed'
                                                      WHEN m.line_id IN ('L04', 'L07', 'L09', 'L11') THEN 'vpd'
                                                      ELSE 'unknown' END)
       OR NOT EXISTS (SELECT 1 FROM public.fn_irrigation_minutes_daily(date '2026-05-29', date '2026-05-29')
                      WHERE line_id = 'L01' AND cause = 'unknown' AND minutes > 0)
    THEN RAISE EXCEPTION '283 native minutes check failed'; END IF;

    started := clock_timestamp();
    PERFORM count(*) FROM public.fn_irrigation_minutes_daily(today - 29, today);
    INSERT INTO irrigation_tables_qualification
    VALUES ('reader_30d_ms', round(extract(epoch FROM clock_timestamp() - started) * 1000));
    started := clock_timestamp();
    PERFORM count(*) FROM public.fn_irrigation_minutes_daily(today - 399, today);
    INSERT INTO irrigation_tables_qualification
    VALUES ('reader_400d_ms', round(extract(epoch FROM clock_timestamp() - started) * 1000));
    started := clock_timestamp();
    PERFORM count(*) FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN today - 29 AND today;
    INSERT INTO irrigation_tables_qualification
    VALUES ('view_30d_ms', round(extract(epoch FROM clock_timestamp() - started) * 1000));
    started := clock_timestamp();
    PERFORM count(*) FROM public.v_irrigation_line_state;
    INSERT INTO irrigation_tables_qualification
    VALUES ('line_state_ms', round(extract(epoch FROM clock_timestamp() - started) * 1000));
END $catalog$;

-- After the re-seal every runtime login still attests. The api login reaches nothing
-- new; the dashboard and MCP logins read everything, write schedules and commands and
-- edit lines, and only the dashboard records a check; the ingestor marks a command,
-- records an outcome and a push and asks whether an experiment is armed, and cannot
-- write a schedule. SET SESSION AUTHORIZATION needs the superuser owner; the rollback
-- undoes it.
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $api$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '283 api login does not attest';
    END IF;
    BEGIN
        PERFORM 1 FROM public.irrigation_line LIMIT 1;
        RAISE EXCEPTION '283 api login read irrigation_line';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM public.v_irrigation_line_state LIMIT 1;
        RAISE EXCEPTION '283 api login read v_irrigation_line_state';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM public.fn_mcp_armed_assignment_context();
        RAISE EXCEPTION '283 api login executed fn_mcp_armed_assignment_context';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $api$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '283 dashboard login does not attest';
    END IF;
    PERFORM 1 FROM public.irrigation_line LIMIT 1;
    PERFORM 1 FROM public.irrigation_slot LIMIT 1;
    PERFORM 1 FROM public.irrigation_command LIMIT 1;
    PERFORM 1 FROM public.irrigation_push LIMIT 1;
    PERFORM 1 FROM public.irrigation_ledger LIMIT 1;
    PERFORM 1 FROM public.v_irrigation_line_state LIMIT 1;
    PERFORM 1 FROM public.v_water_event_attribution LIMIT 1;
    INSERT INTO public.irrigation_command (command_id, kind, line_id, source, actor, reason)
    VALUES ('00000000-0000-4000-8000-000000000283', 'run', 'L01', 'dashboard', '283-qualification',
            '283 native qualification');
    UPDATE public.irrigation_line SET checked_by = '283-qualification', checked_at = now(),
           check_note = '283 native qualification' WHERE line_id = 'L01';
    IF NOT FOUND THEN RAISE EXCEPTION '283 dashboard login could not record a check'; END IF;
END $dashboard$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_mcp_runtime_login;
DO $mcp$
DECLARE
    added integer;
BEGIN
    IF public.fn_mcp_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '283 MCP login does not attest';
    END IF;
    PERFORM 1 FROM public.irrigation_push LIMIT 1;
    PERFORM 1 FROM public.irrigation_ledger LIMIT 1;
    PERFORM 1 FROM public.v_irrigation_line_state LIMIT 1;
    PERFORM 1 FROM public.v_water_event_attribution LIMIT 1;
    INSERT INTO public.irrigation_slot (line_id, days, start_kind, start_min, on_s, source, updated_by, reason)
    VALUES ('L01', 21, 'clock', 450, 600, 'agent:qualification', '283-qualification', '283 native qualification')
    RETURNING slot_id INTO added;
    IF added < 4 THEN RAISE EXCEPTION '283 slot ids did not start at 4'; END IF;
    UPDATE public.irrigation_slot SET enabled = false WHERE slot_id = added;
    DELETE FROM public.irrigation_slot WHERE slot_id = added;
    INSERT INTO public.irrigation_command (kind, line_id, source, actor)
    VALUES ('stop', NULL, 'agent:qualification', '283-qualification');
    UPDATE public.irrigation_line SET paused_until = now() + interval '1 hour', manual = false,
           source = 'agent:qualification', updated_by = '283-qualification', updated_at = now()
     WHERE line_id = 'L07';
    BEGIN
        UPDATE public.irrigation_line SET checked_by = '283-qualification', checked_at = now() WHERE line_id = 'L07';
        RAISE EXCEPTION '283 MCP login recorded a check';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        INSERT INTO public.irrigation_slot (line_id, days, start_kind, start_min, on_s, source, updated_by)
        VALUES ('L03', 127, 'clock', 420, 600, 'agent:qualification', '283-qualification');
        RAISE EXCEPTION '283 accepted a slot on L03';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO public.irrigation_slot (line_id, days, start_kind, start_min, on_s, degraded_only, source,
                                            updated_by)
        VALUES ('L01', 127, 'solar_noon', 0, 600, true, 'agent:qualification', '283-qualification');
        RAISE EXCEPTION '283 accepted a degraded_only slot on L01';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $mcp$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $ingestor$
DECLARE
    ledger_id bigint;
BEGIN
    IF public.fn_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '283 ingestor login does not attest';
    END IF;
    PERFORM 1 FROM public.irrigation_line LIMIT 1;
    PERFORM 1 FROM public.irrigation_slot LIMIT 1;
    PERFORM 1 FROM public.v_irrigation_line_state LIMIT 1;
    UPDATE public.irrigation_command SET status = 'sent', sent_at = now()
     WHERE command_id = '00000000-0000-4000-8000-000000000283';
    IF NOT FOUND THEN RAISE EXCEPTION '283 ingestor login could not mark the command sent'; END IF;
    INSERT INTO public.irrigation_ledger (line_id, local_day, cause, started_at, command_id, actor)
    VALUES ('L01', (now() AT TIME ZONE 'America/Denver')::date, 'run_now', now(),
            '00000000-0000-4000-8000-000000000283', '283-qualification')
    RETURNING id INTO ledger_id;
    UPDATE public.irrigation_ledger SET ended_at = now(), open_s = 60, pulses = 1, outcome = 'ok', event_seq = 1
     WHERE id = ledger_id;
    INSERT INTO public.irrigation_push (sha256, bytes)
    VALUES (encode(sha256(decode(repeat('00', 584), 'hex')), 'hex'), decode(repeat('00', 584), 'hex'));
    UPDATE public.irrigation_push SET pushed_at = now(), confirmed_at = now();
    PERFORM 1 FROM public.fn_mcp_armed_assignment_context();
    BEGIN
        INSERT INTO public.irrigation_slot (line_id, days, start_kind, start_min, on_s, source, updated_by)
        VALUES ('L01', 21, 'clock', 450, 600, 'system', '283-qualification');
        RAISE EXCEPTION '283 ingestor login wrote irrigation_slot';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM public.v_water_event_attribution LIMIT 1;
        RAISE EXCEPTION '283 ingestor login read v_water_event_attribution';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $ingestor$;
RESET SESSION AUTHORIZATION;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('tables_view_sequence', 'seeds', 'grants_exact', 'dashboard_reach', 'line_state',
                               'reader_equals_view_30d', 'topology_cause_without_ledger', 'logins_attest',
                               'dashboard_and_mcp_reads_and_writes', 'mcp_check_denied', 'record_checks',
                               'ingestor_outcomes_and_armed_context', 'ingestor_slot_denied', 'api_denied'),
    'all_passed', true,
    'attested', json_build_object('api', true, 'ingestor', true, 'mcp', true, 'dashboard', true),
    'rows_400d', (SELECT count(*) FROM public.fn_irrigation_minutes_daily((now() AT TIME ZONE 'America/Denver')::date - 399,
                                                                          (now() AT TIME ZONE 'America/Denver')::date)),
    'reader_30d_ms', (SELECT value FROM irrigation_tables_qualification WHERE name = 'reader_30d_ms'),
    'reader_400d_ms', (SELECT value FROM irrigation_tables_qualification WHERE name = 'reader_400d_ms'),
    'view_30d_ms', (SELECT value FROM irrigation_tables_qualification WHERE name = 'view_30d_ms'),
    'line_state_ms', (SELECT value FROM irrigation_tables_qualification WHERE name = 'line_state_ms'))::text;
