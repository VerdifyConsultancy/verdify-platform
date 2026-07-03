-- test-188-night-anchor-dry-roots.sql
--
-- Manual/CI psql fixture for migration 188 (#411 dry-roots night anchors).
--
-- Usage from the repository root against a DISPOSABLE database (CI service
-- container or a local docker timescale/timescaledb). Everything runs inside
-- one transaction and rolls back, but prod DML is banned outright:
--
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-188-night-anchor-dry-roots.sql
--
-- The fixture seeds the canonical band via migration 177, rewinds the four
-- house 'mid' rows to the OBSERVED live prod pre-188 state (the #409 Item-4
-- out-of-band edit: temp 58/62/66, vpd_target 0.918 — verified 2026-07-03),
-- applies migration 188, asserts that exactly those four rows changed to the
-- g-411 decision values (60.71 / 65.71 / 70.71 / 0.83) with every other
-- anchor row untouched, proves a second apply writes ZERO rows, then rolls
-- back.

\set ON_ERROR_STOP on

BEGIN;

-- Disposable-DB seed: the anchors FK requires the greenhouse row, and 177
-- (the canonical reconcile) creates/aligns every canonical band row.
INSERT INTO public.greenhouses (id, name)
VALUES ('vallery', 'fixture greenhouse')
ON CONFLICT (id) DO NOTHING;
\i db/migrations/177-band-defaults-from-canonical-seed.sql

-- Rewind to the observed live prod pre-188 state (#409 Item-4 out-of-band).
UPDATE public.crop_band_anchors
SET value = CASE series
        WHEN 'temp_low'    THEN 58
        WHEN 'temp_target' THEN 62
        WHEN 'temp_high'   THEN 66
        WHEN 'vpd_target'  THEN 0.918
    END
WHERE crop_type = 'house' AND anchor = 'mid' AND greenhouse_id = 'vallery'
  AND series IN ('temp_low', 'temp_target', 'temp_high', 'vpd_target');

-- Snapshot every anchor row: 188 may change ONLY the four targeted rows.
CREATE TEMP TABLE pre_188 ON COMMIT DROP AS
SELECT crop_type, growth_stage, season, series, anchor, greenhouse_id,
       value, width_below, width_above
FROM public.crop_band_anchors;

\i db/migrations/188-night-anchor-dry-roots.sql

-- Assert: exactly the four house night rows changed (188's own DO-block has
-- already asserted the decided values and low < target < high coherence).
DO $t$
DECLARE
    n_changed int;
    n_offtarget int;
BEGIN
    SELECT count(*) INTO n_changed
    FROM public.crop_band_anchors a
    JOIN pre_188 p USING (crop_type, growth_stage, season, series, anchor, greenhouse_id)
    WHERE a.value IS DISTINCT FROM p.value
       OR a.width_below IS DISTINCT FROM p.width_below
       OR a.width_above IS DISTINCT FROM p.width_above;
    IF n_changed <> 4 THEN
        RAISE EXCEPTION 'test-188: expected exactly 4 changed rows, got %', n_changed;
    END IF;

    SELECT count(*) INTO n_offtarget
    FROM public.crop_band_anchors a
    JOIN pre_188 p USING (crop_type, growth_stage, season, series, anchor, greenhouse_id)
    WHERE (a.value IS DISTINCT FROM p.value
           OR a.width_below IS DISTINCT FROM p.width_below
           OR a.width_above IS DISTINCT FROM p.width_above)
      AND NOT (a.crop_type = 'house' AND a.anchor = 'mid'
               AND a.series IN ('temp_low', 'temp_target', 'temp_high', 'vpd_target'));
    IF n_offtarget <> 0 THEN
        RAISE EXCEPTION 'test-188: % changed row(s) outside the four house night anchors', n_offtarget;
    END IF;

    RAISE NOTICE 'test-188: exactly the four house night anchors changed';
END
$t$;

-- Idempotence: a second apply must write ZERO rows. updated_at cannot
-- discriminate here (now() is frozen inside one transaction), so compare
-- ctids — any rewritten tuple gets a new ctid, same-values or not.
CREATE TEMP TABLE pre_rerun ON COMMIT DROP AS
SELECT crop_type, growth_stage, season, series, anchor, greenhouse_id,
       ctid::text AS tuple_id
FROM public.crop_band_anchors;

\i db/migrations/188-night-anchor-dry-roots.sql

DO $t$
DECLARE
    n_rewritten int;
BEGIN
    SELECT count(*) INTO n_rewritten
    FROM public.crop_band_anchors a
    JOIN pre_rerun p USING (crop_type, growth_stage, season, series, anchor, greenhouse_id)
    WHERE a.ctid::text IS DISTINCT FROM p.tuple_id;
    IF n_rewritten <> 0 THEN
        RAISE EXCEPTION 'test-188: re-apply rewrote % row(s); expected an exact no-op', n_rewritten;
    END IF;
    RAISE NOTICE 'test-188: re-apply wrote zero rows (idempotent)';
END
$t$;

ROLLBACK;
