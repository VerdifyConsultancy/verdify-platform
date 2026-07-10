-- Source-timestamp and minimum-coverage fixture for migration 192.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.climate (
    ts timestamptz NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    outdoor_temp_f double precision,
    outdoor_rh_pct double precision
);

\i db/migrations/192-replay-outdoor-freshness-provenance.sql

-- 1,201 eligible rows with a real observed value change at least every five
-- minutes.  No age or freshness value is injected.
INSERT INTO public.climate(ts, outdoor_temp_f, outdoor_rh_pct)
SELECT
    '2026-01-01 00:00:00+00'::timestamptz + i * interval '1 minute',
    50 + ((i / 5) % 20),
    40 + ((i / 7) % 20)
FROM generate_series(0, 1200) AS g(i);

-- Then hold the exact last observed values for twenty minutes.  The view must
-- let age grow stale rather than refreshing it from row timestamps.
INSERT INTO public.climate(ts, outdoor_temp_f, outdoor_rh_pct)
SELECT
    '2026-01-01 00:00:00+00'::timestamptz + i * interval '1 minute',
    50,
    51
FROM generate_series(1201, 1220) AS g(i);

DO $$
DECLARE
    backed int;
    fresh int;
    max_age int;
    bad_future int;
BEGIN
    SELECT
        count(*) FILTER (WHERE source_backed),
        count(*) FILTER (WHERE outdoor_fresh),
        max(outdoor_data_age_s),
        count(*) FILTER (WHERE outdoor_source_ts > ts)
    INTO backed, fresh, max_age, bad_future
    FROM public.v_replay_outdoor_freshness;

    IF backed < 1200 OR fresh < 1000 THEN
        RAISE EXCEPTION 'source-backed coverage too small: backed %, fresh %', backed, fresh;
    END IF;
    IF max_age < 1200 THEN
        RAISE EXCEPTION 'silent source was incorrectly kept fresh; max age %', max_age;
    END IF;
    IF bad_future <> 0 THEN
        RAISE EXCEPTION 'freshness provenance points into the future';
    END IF;
END $$;

SELECT
    count(*) FILTER (WHERE source_backed) AS source_backed_rows,
    count(*) FILTER (WHERE outdoor_fresh) AS fresh_rows,
    max(outdoor_data_age_s) AS max_age_s,
    count(*) FILTER (WHERE outdoor_source_ts > ts) AS future_provenance_rows,
    min(freshness_basis) AS freshness_basis
FROM public.v_replay_outdoor_freshness;

ROLLBACK;
