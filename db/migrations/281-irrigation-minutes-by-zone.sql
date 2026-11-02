-- VD-39 (#1154), dashboard migration M4: watering minutes by zone from relay runs.
-- Read-only and inert: two views over migration 194's v_water_relay_runs plus the
-- L03 fert_master_valve relay, and one day-bounded reader with the same rows.
-- No table, trigger, row write or change to any migration-194 view. Local days
-- are America/Denver; DST comes from the time-zone conversion; gaps stay gaps.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=280 AND filename='db/migrations/280-desired-state-activity-log.sql'
        AND sha256='44531a0c3a9ddd48f64ce639f9f9f1c2d0f948960ba65a68943d5947881b8bfe'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=281)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'af3647c841270c17097fd00c5d27d8dd6b46413a15838f08fd5b6cbbcda343d7' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '4697e11eb2e66a73f727304df3419d3dd4635199bc346ba5e62cfe2f2a194103' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '685212a93b79c980c422db5baacb8f61309af392a9a7a8e9a46baf8e55fde291'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('af3647c841270c17097fd00c5d27d8dd6b46413a15838f08fd5b6cbbcda343d7','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('4697e11eb2e66a73f727304df3419d3dd4635199bc346ba5e62cfe2f2a194103','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('685212a93b79c980c422db5baacb8f61309af392a9a7a8e9a46baf8e55fde291','hex'))
    THEN RAISE EXCEPTION '281 refuses an unqualified predecessor'; END IF;
END $predecessor$;

-- One row per America/Denver day from the first wet-relay equipment_state row
-- to today, checked in order: no_data (after the newest climate row's day, every
-- day when there is no climate row at all, or wholly inside one data_gaps
-- interval), partial (today, the newest climate row's day, or any data_gaps
-- overlap), complete. Only the newest climate row is read, so days older than
-- climate's 365-day retention stay complete.
CREATE VIEW public.v_irrigation_day_coverage AS
WITH wet_relays(relay_slug) AS (
    VALUES ('drip_wall'), ('drip_wall_fert'), ('fert_master_valve'),
           ('mister_center'), ('drip_center'), ('drip_center_fert'),
           ('mister_south'), ('mister_south_fert'), ('mister_west'),
           ('mister_west_fert'), ('fog')
), greenhouse_ids(greenhouse_id) AS (
    SELECT g.id FROM public.greenhouses g
    UNION
    SELECT 'vallery'::text
), first_rows AS (
    SELECT g.greenhouse_id, min(f.ts) AS first_ts
    FROM greenhouse_ids g
    CROSS JOIN wet_relays r
    CROSS JOIN LATERAL (
        SELECT es.ts
        FROM public.equipment_state es
        WHERE es.equipment = r.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = g.greenhouse_id
        ORDER BY es.ts
        LIMIT 1
    ) f
    GROUP BY g.greenhouse_id
), bounds AS MATERIALIZED (
    -- Materialized so the newest climate row is looked up once per greenhouse,
    -- not once per day.
    SELECT f.greenhouse_id,
           (f.first_ts AT TIME ZONE 'America/Denver')::date AS first_day,
           (now() AT TIME ZONE 'America/Denver')::date AS today,
           ((SELECT max(c.ts) FROM public.climate c
             WHERE COALESCE(c.greenhouse_id, 'vallery') = f.greenhouse_id)
            AT TIME ZONE 'America/Denver')::date AS newest_climate_day
    FROM first_rows f
), days AS (
    SELECT b.greenhouse_id, s::date AS local_day, b.today, b.newest_climate_day,
           s::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
           (s::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
    FROM bounds b
    CROSS JOIN LATERAL generate_series(b.first_day::timestamp, b.today::timestamp, interval '1 day') s
)
SELECT d.greenhouse_id,
       d.local_day,
       CASE
           WHEN d.newest_climate_day IS NULL
             OR d.local_day > d.newest_climate_day
             OR EXISTS (SELECT 1 FROM public.data_gaps g
                        WHERE g.start_ts <= d.day_start AND g.end_ts >= d.day_end)
               THEN 'no_data'
           WHEN d.local_day = d.today
             OR d.local_day = d.newest_climate_day
             OR EXISTS (SELECT 1 FROM public.data_gaps g
                        WHERE g.start_ts < d.day_end AND g.end_ts > d.day_start)
               THEN 'partial'
           ELSE 'complete'
       END AS coverage
FROM days d;

COMMENT ON VIEW public.v_irrigation_day_coverage IS
'Each America/Denver day from the first wet-relay row to today: no_data (after the newest climate row''s day, every day when there is no climate row, or wholly inside one data_gaps interval), partial (today, the newest climate row''s day, or a data_gaps overlap) or complete. A complete day with no runs has no minutes rows; nothing is zero-filled.';

-- Runs are migration 194's v_water_relay_runs UNION ALL the L03 runs built the
-- same way. Each run is split at local midnight (a day is d 00:00 to d+1 00:00
-- local, so 2026-11-01 is 25 hours); minutes are its overlap with the day,
-- pulses count on the start day, and gal sums v_water_run_accounting's
-- meter_attributed_gal over the runs that start that day.
CREATE VIEW public.v_irrigation_minutes_daily AS
WITH line_map(relay_slug, line_id, zone, chemistry, cause) AS (
    VALUES
        ('drip_wall', 'L01', 'wall', 'clean', 'unknown'),
        ('drip_wall_fert', 'L02', 'wall', 'fert', 'feed'),
        ('fert_master_valve', 'L03', 'supply', 'fert', 'feed'),
        ('mister_center', 'L04', 'center', 'clean', 'vpd'),
        ('drip_center', 'L05', 'center', 'clean', 'unknown'),
        ('drip_center_fert', 'L06', 'center', 'fert', 'unknown'),
        ('mister_south', 'L07', 'south', 'clean', 'vpd'),
        ('mister_south_fert', 'L08', 'south', 'fert', 'unknown'),
        ('mister_west', 'L09', 'west', 'clean', 'vpd'),
        ('mister_west_fert', 'L10', 'west', 'fert', 'unknown'),
        ('fog', 'L11', 'fog', 'clean', 'vpd')
), master_observed AS (
    SELECT COALESCE(es.greenhouse_id, 'vallery') AS greenhouse_id,
           es.ts,
           bool_or(es.state) AS state,
           count(DISTINCT es.state) > 1 AS conflicting_state
    FROM public.equipment_state es
    WHERE es.equipment = 'fert_master_valve'
    GROUP BY COALESCE(es.greenhouse_id, 'vallery'), es.ts
), master_changed AS (
    SELECT x.greenhouse_id, x.ts, x.state
    FROM (
        SELECT o.*,
               lag(o.state) OVER (PARTITION BY o.greenhouse_id ORDER BY o.ts) AS prior_state
        FROM master_observed o
    ) x
    WHERE x.prior_state IS DISTINCT FROM x.state
       OR x.prior_state IS NULL
       OR x.conflicting_state
), master_intervals AS (
    SELECT c.*,
           lead(c.ts, 1, now()) OVER (PARTITION BY c.greenhouse_id ORDER BY c.ts) AS run_end
    FROM master_changed c
), runs AS (
    SELECT r.greenhouse_id, r.relay_slug, r.run_start, r.run_end
    FROM public.v_water_relay_runs r
    UNION ALL
    SELECT m.greenhouse_id, 'fert_master_valve'::text, m.ts, m.run_end
    FROM master_intervals m
    WHERE m.state IS TRUE
      AND m.run_end > m.ts
), pieces AS (
    SELECT r.greenhouse_id, r.relay_slug, r.run_start, r.run_end, d.local_day,
           d.local_day = (r.run_start AT TIME ZONE 'America/Denver')::date AS run_starts,
           extract(epoch FROM (least(r.run_end, d.day_end) - greatest(r.run_start, d.day_start))) AS open_s
    FROM runs r
    CROSS JOIN LATERAL (
        SELECT s::date AS local_day,
               s::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
               (s::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
        FROM generate_series((r.run_start AT TIME ZONE 'America/Denver')::date::timestamp,
                             (r.run_end AT TIME ZONE 'America/Denver')::date::timestamp,
                             interval '1 day') s
    ) d
    WHERE least(r.run_end, d.day_end) > greatest(r.run_start, d.day_start)
), metered AS (
    -- v_water_run_accounting's per-run meter_attributed_gal, read only for a
    -- run's start-day piece. An accepted delta spans at most 5 minutes (194's
    -- materializer flags longer intervals source_gap), so bounding the scan at
    -- run_end + 5 minutes keeps every overlapping delta and keeps reads bounded.
    SELECT p.*, run_gal.gal
    FROM pieces p
    LEFT JOIN LATERAL (
        SELECT CASE WHEN count(*) = 0 THEN NULL::double precision
                    ELSE round(COALESCE(sum(a.delta_gal) FILTER (
                             WHERE a.attribution_class = 'meter_attributed'), 0)::numeric, 3)::double precision
               END AS gal
        FROM public.v_water_event_attribution a
        WHERE p.run_starts
          AND p.relay_slug <> 'fert_master_valve'
          AND a.greenhouse_id = p.greenhouse_id
          AND a.ts > p.run_start
          AND a.ts <= p.run_end + interval '5 minutes'
          AND a.prior_ts < p.run_end
    ) run_gal ON true
), daily AS (
    SELECT m.greenhouse_id, m.local_day, m.relay_slug,
           sum(m.open_s) AS open_s,
           count(*) FILTER (WHERE m.run_starts) AS pulses,
           sum(m.gal) FILTER (WHERE m.run_starts) AS run_gal
    FROM metered m
    GROUP BY m.greenhouse_id, m.local_day, m.relay_slug
), coverage AS MATERIALIZED (
    SELECT c.greenhouse_id, c.local_day, c.coverage
    FROM public.v_irrigation_day_coverage c
)
SELECT d.greenhouse_id,
       d.local_day,
       l.zone,
       l.line_id,
       l.cause,
       l.chemistry,
       NULL::integer AS slot_id,
       round((d.open_s / 60.0)::numeric, 1)::double precision AS minutes,
       d.pulses::integer AS pulses,
       CASE
           WHEN l.line_id = 'L03' THEN NULL::integer
           WHEN d.run_gal IS NOT NULL THEN round(d.run_gal::numeric)::integer
           WHEN EXISTS (
               SELECT 1 FROM public.v_water_event_attribution a
               WHERE a.greenhouse_id = d.greenhouse_id
                 AND a.ts >= d.local_day::timestamp AT TIME ZONE 'America/Denver'
                 AND a.ts < (d.local_day + 1)::timestamp AT TIME ZONE 'America/Denver'
           ) THEN 0
           ELSE NULL::integer
       END AS gal,
       c.coverage <> 'complete' AS partial
FROM daily d
JOIN line_map l ON l.relay_slug = d.relay_slug
JOIN coverage c
  ON c.greenhouse_id = d.greenhouse_id
 AND c.local_day = d.local_day
WHERE c.coverage <> 'no_data';

COMMENT ON VIEW public.v_irrigation_minutes_daily IS
'Valve-open minutes (0.1), pulses (runs starting that day) and whole gallons per America/Denver day by greenhouse, zone, line, cause and chemistry. gal is NULL for L03, and when these runs had no accepted delta it is 0 if the meter recorded one that day and NULL if not. partial marks a day whose coverage is not complete; no_data days have no rows. slot_id is NULL until M3.';

-- The view's rows for local days p_from..p_to, with the equipment_state scan
-- bounded by the day range: seeded with each relay's state at p_from's local
-- midnight (read as v_water_relay_runs reads it: bool_or over the newest
-- timestamp before it), and a run still open at the end looks ahead for its
-- closing edge so its gallons match the view's.
CREATE FUNCTION public.fn_irrigation_minutes_daily(p_from date, p_to date)
RETURNS TABLE (
    greenhouse_id text,
    local_day date,
    zone text,
    line_id text,
    cause text,
    chemistry text,
    slot_id integer,
    minutes double precision,
    pulses integer,
    gal integer,
    partial boolean
)
LANGUAGE sql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $minutes$
WITH bounds AS (
    SELECT p_from::timestamp AT TIME ZONE 'America/Denver' AS from_ts,
           (p_to + 1)::timestamp AT TIME ZONE 'America/Denver' AS to_ts
), line_map(relay_slug, line_id, zone, chemistry, cause) AS (
    VALUES
        ('drip_wall', 'L01', 'wall', 'clean', 'unknown'),
        ('drip_wall_fert', 'L02', 'wall', 'fert', 'feed'),
        ('fert_master_valve', 'L03', 'supply', 'fert', 'feed'),
        ('mister_center', 'L04', 'center', 'clean', 'vpd'),
        ('drip_center', 'L05', 'center', 'clean', 'unknown'),
        ('drip_center_fert', 'L06', 'center', 'fert', 'unknown'),
        ('mister_south', 'L07', 'south', 'clean', 'vpd'),
        ('mister_south_fert', 'L08', 'south', 'fert', 'unknown'),
        ('mister_west', 'L09', 'west', 'clean', 'vpd'),
        ('mister_west_fert', 'L10', 'west', 'fert', 'unknown'),
        ('fog', 'L11', 'fog', 'clean', 'vpd')
), greenhouse_ids(greenhouse_id) AS (
    SELECT g.id FROM public.greenhouses g
    UNION
    SELECT 'vallery'::text
), seed AS (
    SELECT g.greenhouse_id, l.relay_slug, s.ts, s.state, s.conflicting_state
    FROM greenhouse_ids g
    CROSS JOIN line_map l
    CROSS JOIN bounds b
    CROSS JOIN LATERAL (
        SELECT es.ts,
               bool_or(es.state) AS state,
               count(DISTINCT es.state) > 1 AS conflicting_state
        FROM public.equipment_state es
        WHERE es.equipment = l.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = g.greenhouse_id
          AND es.ts < b.from_ts
        GROUP BY es.ts
        ORDER BY es.ts DESC
        LIMIT 1
    ) s
), observed AS (
    SELECT COALESCE(es.greenhouse_id, 'vallery') AS greenhouse_id,
           es.equipment AS relay_slug,
           es.ts,
           bool_or(es.state) AS state,
           count(DISTINCT es.state) > 1 AS conflicting_state
    FROM public.equipment_state es
    CROSS JOIN bounds b
    WHERE es.equipment IN (
            'drip_wall', 'drip_wall_fert', 'fert_master_valve', 'mister_center',
            'drip_center', 'drip_center_fert', 'mister_south', 'mister_south_fert',
            'mister_west', 'mister_west_fert', 'fog')
      AND es.ts >= b.from_ts
      AND es.ts < b.to_ts
    GROUP BY COALESCE(es.greenhouse_id, 'vallery'), es.equipment, es.ts
), changed AS (
    SELECT x.greenhouse_id, x.relay_slug, x.ts, x.state
    FROM (
        SELECT s.*,
               lag(s.state) OVER (PARTITION BY s.greenhouse_id, s.relay_slug ORDER BY s.ts) AS prior_state
        FROM (
            SELECT greenhouse_id, relay_slug, ts, state, conflicting_state FROM seed
            UNION ALL
            SELECT greenhouse_id, relay_slug, ts, state, conflicting_state FROM observed
        ) s
    ) x
    WHERE x.prior_state IS DISTINCT FROM x.state
       OR x.prior_state IS NULL
       OR x.conflicting_state
), intervals AS (
    SELECT c.*,
           lead(c.ts) OVER (PARTITION BY c.greenhouse_id, c.relay_slug ORDER BY c.ts) AS next_ts
    FROM changed c
), runs AS (
    SELECT i.greenhouse_id, i.relay_slug, i.ts AS run_start,
           COALESCE(i.next_ts, ahead.ts, now()) AS run_end
    FROM intervals i
    CROSS JOIN bounds b
    LEFT JOIN LATERAL (
        SELECT es.ts
        FROM public.equipment_state es
        WHERE i.next_ts IS NULL
          AND i.state IS TRUE
          AND es.equipment = i.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = i.greenhouse_id
          AND es.ts >= b.to_ts
        GROUP BY es.ts
        HAVING NOT bool_or(es.state) OR count(DISTINCT es.state) > 1
        ORDER BY es.ts
        LIMIT 1
    ) ahead ON true
    WHERE i.state IS TRUE
      AND COALESCE(i.next_ts, ahead.ts, now()) > i.ts
), pieces AS (
    SELECT r.greenhouse_id, r.relay_slug, r.run_start, r.run_end, d.local_day,
           d.local_day = (r.run_start AT TIME ZONE 'America/Denver')::date AS run_starts,
           extract(epoch FROM (least(r.run_end, d.day_end) - greatest(r.run_start, d.day_start))) AS open_s
    FROM runs r
    CROSS JOIN LATERAL (
        SELECT s::date AS local_day,
               s::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
               (s::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
        FROM generate_series(greatest((r.run_start AT TIME ZONE 'America/Denver')::date, p_from)::timestamp,
                             least((r.run_end AT TIME ZONE 'America/Denver')::date, p_to)::timestamp,
                             interval '1 day') s
    ) d
    WHERE least(r.run_end, d.day_end) > greatest(r.run_start, d.day_start)
), metered AS (
    SELECT p.*, run_gal.gal
    FROM pieces p
    LEFT JOIN LATERAL (
        SELECT CASE WHEN count(*) = 0 THEN NULL::double precision
                    ELSE round(COALESCE(sum(a.delta_gal) FILTER (
                             WHERE a.attribution_class = 'meter_attributed'), 0)::numeric, 3)::double precision
               END AS gal
        FROM public.v_water_event_attribution a
        WHERE p.run_starts
          AND p.relay_slug <> 'fert_master_valve'
          AND a.greenhouse_id = p.greenhouse_id
          AND a.ts > p.run_start
          AND a.ts <= p.run_end + interval '5 minutes'
          AND a.prior_ts < p.run_end
    ) run_gal ON true
), daily AS (
    SELECT m.greenhouse_id, m.local_day, m.relay_slug,
           sum(m.open_s) AS open_s,
           count(*) FILTER (WHERE m.run_starts) AS pulses,
           sum(m.gal) FILTER (WHERE m.run_starts) AS run_gal
    FROM metered m
    GROUP BY m.greenhouse_id, m.local_day, m.relay_slug
), coverage AS MATERIALIZED (
    SELECT c.greenhouse_id, c.local_day, c.coverage
    FROM public.v_irrigation_day_coverage c
    WHERE c.local_day BETWEEN p_from AND p_to
)
SELECT d.greenhouse_id,
       d.local_day,
       l.zone,
       l.line_id,
       l.cause,
       l.chemistry,
       NULL::integer AS slot_id,
       round((d.open_s / 60.0)::numeric, 1)::double precision AS minutes,
       d.pulses::integer AS pulses,
       CASE
           WHEN l.line_id = 'L03' THEN NULL::integer
           WHEN d.run_gal IS NOT NULL THEN round(d.run_gal::numeric)::integer
           WHEN EXISTS (
               SELECT 1 FROM public.v_water_event_attribution a
               WHERE a.greenhouse_id = d.greenhouse_id
                 AND a.ts >= d.local_day::timestamp AT TIME ZONE 'America/Denver'
                 AND a.ts < (d.local_day + 1)::timestamp AT TIME ZONE 'America/Denver'
           ) THEN 0
           ELSE NULL::integer
       END AS gal,
       c.coverage <> 'complete' AS partial
FROM daily d
JOIN line_map l ON l.relay_slug = d.relay_slug
JOIN coverage c
  ON c.greenhouse_id = d.greenhouse_id
 AND c.local_day = d.local_day
WHERE c.coverage <> 'no_data'
$minutes$;

COMMENT ON FUNCTION public.fn_irrigation_minutes_daily(date, date) IS
'v_irrigation_minutes_daily for local days p_from..p_to with the equipment_state scan bounded by the range, for 30- to 400-day reads (API-IRR-10).';

REVOKE ALL ON public.v_irrigation_day_coverage, public.v_irrigation_minutes_daily FROM PUBLIC;
GRANT SELECT ON public.v_irrigation_day_coverage, public.v_irrigation_minutes_daily
    TO verdify_mcp_runtime, verdify_ingestor_runtime;
REVOKE ALL ON FUNCTION public.fn_irrigation_minutes_daily(date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_irrigation_minutes_daily(date, date)
    TO verdify_mcp_runtime, verdify_ingestor_runtime;

-- Exact production-OID successor seals; the rolled-back native qualification
-- (tests/fixtures/281-irrigation-minutes-native-qualification.json) proves them.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'aa226e5fb29a9aa0548c597ef718236d0d83737cb23c322920977a9655958dc7' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '2449cf2fc05464a4deedb5f78de6da6d3208c870dd9f841133d3c8fe5f0513db' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '9cb30130e47e95af13af14f2fc08c89e2422e89e14bd3b448e4448849ab194b3'
    THEN RAISE EXCEPTION '281 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('aa226e5fb29a9aa0548c597ef718236d0d83737cb23c322920977a9655958dc7','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '281 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('2449cf2fc05464a4deedb5f78de6da6d3208c870dd9f841133d3c8fe5f0513db','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '281 missing ingestor receipt'; END IF;
    UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('9cb30130e47e95af13af14f2fc08c89e2422e89e14bd3b448e4448849ab194b3','hex') WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION '281 missing mcp receipt'; END IF;
END $successor$;
