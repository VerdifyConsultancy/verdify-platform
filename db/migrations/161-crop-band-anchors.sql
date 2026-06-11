-- 161-crop-band-anchors.sql
-- =============================================================================
-- Firmware v2 (#287 / contract B2+B7): crop_band_anchors — the CANONICAL
-- deterministic band source.
--
-- Design: docs/design/firmware-v2-contract-2026-06-10.md §B2 (anchor data) +
-- §B7 (DB contract); docs/design/firmware-v2-simplification-2026-06-10.md §3
-- (crop/zone model). The target band is a pure, deterministic function of
-- (crop, zone, solar phase). This table holds the 4-point solar-anchored
-- curves (value at sunrise / solar noon / sunset / solar midnight) that the
-- dispatcher pushes to the ESP32 as NVS-persisted anchor tunables and that
-- DB-side resolvers (migration 164: fn_crop_band_value / fn_zone_vpd_targets)
-- cosine-interpolate, exactly mirroring the on-chip band_value_at_phase().
--
-- Anchor semantics (contract B1 phase mapping):
--   sr  = sunrise          (phase 0)
--   sm  = solar noon       (phase 1)
--   ss  = sunset           (phase 2)
--   mid = solar midnight   (phase 3; ~midpoint(SS, next SR))
--
-- Width semantics: width_below/width_above are NULLable and only meaningful on
-- *_target series rows — min = target - width_below, max = target + width_above
-- (contract B2: "min/target/max are reconstructible at every instant"). The
-- house curves carry explicit low/target/high series instead (asymmetry
-- matters: tight midday ceiling, loose night floor), so house rows have NULL
-- widths.
--
-- Seeded curves (contract §B2, SR / SM / SS / MID order):
--   house    — Vanda-anchored reconciled house curve (§3.1): all 6 series.
--   orchid   — Vanda zone (CENTER) vpd_target + widths (-0.20/+0.35).
--   cannabis — SOUTH vpd_target + widths (-0.18/+0.22) + advisory temp_target
--              (per-zone temp is un-actuatable — one air mass — so per-crop
--              temp_target rows are ADVISORY context, not a served band).
--   citrus   — WEST (lime) vpd_target + widths (-0.16/+0.22) + advisory temp_target.
--   pepper   — EAST vpd_target + widths (-0.15/+0.23) + advisory temp_target.
--
-- Relationship to crop_target_profiles: crop_target_profiles stays the per-hour
-- GRADING band source for fn_zone_band / compliance scoring (migrations
-- 145/146) — UNTOUCHED. crop_band_anchors is the new SERVED-band source
-- (compact 4-anchor parameterization, matching the firmware's NVS tunables).
--
-- #23 rollback-replay safety: NON-self-transactional — no top-level
-- BEGIN;/COMMIT; and no commit-forcing statement. SAFE to wrap in an outer
-- `BEGIN; ... ROLLBACK;` for rollback validation. Idempotent: CREATE TABLE IF
-- NOT EXISTS + ON CONFLICT DO NOTHING seeds (re-runs never clobber later
-- operator tuning of the values).
--
-- RESTARTS (CLAUDE.md rule 7): does not touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py — no restart obligation. The
-- dispatcher only starts reading this table once its band-emission work lands.
--
-- ROLLBACK (documented; for a disposable validation DB, never live):
--   DROP TRIGGER IF EXISTS trg_crop_band_anchors_updated_at ON public.crop_band_anchors;
--   DROP TABLE IF EXISTS public.crop_band_anchors;
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────
-- 161.1  Table
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.crop_band_anchors (
    id            serial PRIMARY KEY,
    crop_type     text NOT NULL,
    growth_stage  text NOT NULL DEFAULT 'default',
    season        text NOT NULL DEFAULT 'all',
    series        text NOT NULL CHECK (series IN
                      ('temp_low','temp_target','temp_high',
                       'vpd_low','vpd_target','vpd_high')),
    anchor        text NOT NULL CHECK (anchor IN ('sr','sm','ss','mid')),
    value         double precision NOT NULL,
    width_below   double precision,
    width_above   double precision,
    greenhouse_id text NOT NULL DEFAULT 'vallery' REFERENCES greenhouses(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (crop_type, growth_stage, season, series, anchor, greenhouse_id)
);

COMMENT ON TABLE public.crop_band_anchors IS
'Canonical deterministic band source (firmware-v2 contract B2/B7, migration 161). '
'4-point solar-anchored curves per (crop, series): value at sunrise(sr) / solar '
'noon(sm) / sunset(ss) / solar midnight(mid). Cosine-interpolated by '
'fn_crop_band_value (DB) and band_value_at_phase (ESP32). The dispatcher pushes '
'these as NVS-persisted anchor tunables on change only.';
COMMENT ON COLUMN public.crop_band_anchors.anchor IS
'Solar anchor: sr=sunrise(phase 0), sm=solar noon(1), ss=sunset(2), mid=solar midnight(3).';
COMMENT ON COLUMN public.crop_band_anchors.width_below IS
'Half-width below a *_target series value (min = target - width_below). NULL on non-target series.';
COMMENT ON COLUMN public.crop_band_anchors.width_above IS
'Half-width above a *_target series value (max = target + width_above). NULL on non-target series.';
COMMENT ON COLUMN public.crop_band_anchors.season IS
'''all'' (default) or a fn_current_season() value for season-specific overrides.';

-- keep updated_at honest on operator tuning (reuses the fleet-standard trigger fn)
DROP TRIGGER IF EXISTS trg_crop_band_anchors_updated_at ON public.crop_band_anchors;
CREATE TRIGGER trg_crop_band_anchors_updated_at
    BEFORE UPDATE ON public.crop_band_anchors
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ─────────────────────────────────────────────────────────────────────
-- 161.2  Seed: house — Vanda-anchored reconciled house curve (§3.1)
--        All six series, explicit low/target/high (NULL widths).
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value) VALUES
  -- temp_low (°F):     60 / 76 / 66 / 60
  ('house','temp_low','sr',60), ('house','temp_low','sm',76),
  ('house','temp_low','ss',66), ('house','temp_low','mid',60),
  -- temp_target (°F):  66 / 84 / 73 / 64
  ('house','temp_target','sr',66), ('house','temp_target','sm',84),
  ('house','temp_target','ss',73), ('house','temp_target','mid',64),
  -- temp_high (°F):    72 / 86 / 80 / 70
  ('house','temp_high','sr',72), ('house','temp_high','sm',86),
  ('house','temp_high','ss',80), ('house','temp_high','mid',70),
  -- vpd_low (kPa):     0.40 / 0.60 / 0.45 / 0.42
  ('house','vpd_low','sr',0.40), ('house','vpd_low','sm',0.60),
  ('house','vpd_low','ss',0.45), ('house','vpd_low','mid',0.42),
  -- vpd_target (kPa):  0.60 / 1.05 / 0.60 / 0.50
  ('house','vpd_target','sr',0.60), ('house','vpd_target','sm',1.05),
  ('house','vpd_target','ss',0.60), ('house','vpd_target','mid',0.50),
  -- vpd_high (kPa):    0.90 / 1.40 / 0.90 / 0.75
  ('house','vpd_high','sr',0.90), ('house','vpd_high','sm',1.40),
  ('house','vpd_high','ss',0.90), ('house','vpd_high','mid',0.75)
ON CONFLICT (crop_type, growth_stage, season, series, anchor, greenhouse_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 161.3  Seed: per-zone crop vpd_target curves + half-widths (contract B2)
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value, width_below, width_above) VALUES
  -- orchid (Vanda, CENTER): 0.60 / 1.05 / 0.60 / 0.50, widths -0.20/+0.35
  ('orchid','vpd_target','sr',0.60,0.20,0.35), ('orchid','vpd_target','sm',1.05,0.20,0.35),
  ('orchid','vpd_target','ss',0.60,0.20,0.35), ('orchid','vpd_target','mid',0.50,0.20,0.35),
  -- cannabis (SOUTH): 0.85 / 1.18 / 0.95 / 0.75, widths -0.18/+0.22
  ('cannabis','vpd_target','sr',0.85,0.18,0.22), ('cannabis','vpd_target','sm',1.18,0.18,0.22),
  ('cannabis','vpd_target','ss',0.95,0.18,0.22), ('cannabis','vpd_target','mid',0.75,0.18,0.22),
  -- citrus (lime, WEST): 0.60 / 1.10 / 0.70 / 0.57, widths -0.16/+0.22
  ('citrus','vpd_target','sr',0.60,0.16,0.22), ('citrus','vpd_target','sm',1.10,0.16,0.22),
  ('citrus','vpd_target','ss',0.70,0.16,0.22), ('citrus','vpd_target','mid',0.57,0.16,0.22),
  -- pepper (EAST): 0.80 / 1.22 / 0.90 / 0.74, widths -0.15/+0.23
  ('pepper','vpd_target','sr',0.80,0.15,0.23), ('pepper','vpd_target','sm',1.22,0.15,0.23),
  ('pepper','vpd_target','ss',0.90,0.15,0.23), ('pepper','vpd_target','mid',0.74,0.15,0.23)
ON CONFLICT (crop_type, growth_stage, season, series, anchor, greenhouse_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 161.4  Seed: advisory per-crop temp_target curves (°F).
--        ADVISORY ONLY: temp is one house air mass (§3.1) — these document
--        each crop's thermal preference for planner/compliance context; the
--        served temp band stays the house curve.
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value) VALUES
  -- cannabis: 66 / 78 / 71 / 65
  ('cannabis','temp_target','sr',66), ('cannabis','temp_target','sm',78),
  ('cannabis','temp_target','ss',71), ('cannabis','temp_target','mid',65),
  -- citrus (lime): 61 / 84 / 73 / 60
  ('citrus','temp_target','sr',61), ('citrus','temp_target','sm',84),
  ('citrus','temp_target','ss',73), ('citrus','temp_target','mid',60),
  -- pepper: 64 / 78 / 72 / 64
  ('pepper','temp_target','sr',64), ('pepper','temp_target','sm',78),
  ('pepper','temp_target','ss',72), ('pepper','temp_target','mid',64)
ON CONFLICT (crop_type, growth_stage, season, series, anchor, greenhouse_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 161.5  Post-seed assertion: fail loud if the canonical set is incomplete.
-- ─────────────────────────────────────────────────────────────────────
DO $assert$
DECLARE
    n_house    int;
    n_zone_tgt int;
    n_advisory int;
BEGIN
    SELECT count(*) INTO n_house
      FROM public.crop_band_anchors
     WHERE crop_type = 'house' AND greenhouse_id = 'vallery';
    SELECT count(*) INTO n_zone_tgt
      FROM public.crop_band_anchors
     WHERE crop_type IN ('orchid','cannabis','citrus','pepper')
       AND series = 'vpd_target' AND greenhouse_id = 'vallery'
       AND width_below IS NOT NULL AND width_above IS NOT NULL;
    SELECT count(*) INTO n_advisory
      FROM public.crop_band_anchors
     WHERE crop_type IN ('cannabis','citrus','pepper')
       AND series = 'temp_target' AND greenhouse_id = 'vallery';

    IF n_house < 24 THEN
        RAISE EXCEPTION 'migration 161: house curve incomplete — % rows, expected 24 (6 series x 4 anchors)', n_house;
    END IF;
    IF n_zone_tgt < 16 THEN
        RAISE EXCEPTION 'migration 161: zone vpd_target curves incomplete — % rows, expected 16 (4 crops x 4 anchors)', n_zone_tgt;
    END IF;
    IF n_advisory < 12 THEN
        RAISE EXCEPTION 'migration 161: advisory temp_target curves incomplete — % rows, expected 12 (3 crops x 4 anchors)', n_advisory;
    END IF;
    RAISE NOTICE 'migration 161 OK: house=%, zone vpd_target=%, advisory temp_target=% anchor rows.',
        n_house, n_zone_tgt, n_advisory;
END
$assert$;
