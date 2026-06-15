-- 168-orchid-dry-night-band-curve.sql
-- =============================================================================
-- Restore overnight DRYNESS for the Vandas by lifting the house + orchid VPD
-- band off its humid solar-midnight dip and onto a SMOOTH, flatter, dry-night
-- curve. Pure data UPDATE on crop_band_anchors (the canonical source the
-- dispatcher syncs to the on-chip NVS anchors in VERDIFY_BAND_SOURCE=anchors
-- mode, contract B2/B7; mirrored in firmware globals.yaml defaults by the
-- accompanying OTA).
--
-- WHY (verified 2026-06-15, full review in project memory
-- orchid-overnight-regression-2026-06-15):
--   firmware-v2's solar-midnight ('mid') VPD anchors command a WET night —
--   house/orchid vpd_target mid=0.50 kPa = ~RH 80% at the 64F night temp, low
--   edge 0.42. The band is specified in VPD, and because saturation pressure
--   ~doubles from a 64F night to an 84F day, the old 0.50-night / 1.05-day
--   numbers are really a near-constant ~75% RH day AND night — far too humid
--   for bare-root Vandas overnight. Pre-OTA the commanded night floor was
--   vpd_low 0.62 / vpd_high 1.18 and the house ran RH ~39% overnight (dry,
--   healthy). The OTA dropped the floor to 0.42/0.75 and the house now traps
--   ~14-15 g/m3 of moisture overnight vs ~6 outside.
--
-- THE FIX = FLATTEN THE VPD CURVE UPWARD (no block-time cutoffs; it stays a
-- smooth solar-cosine). At the cold night temp the same VPD yields a much
-- lower RH, so a flatter/higher VPD curve gives a DRY night and a humid warm
-- day automatically. The existing on-chip DEHUM_VENT (vent+fans, not night-
-- gated) then keeps venting the moist house air against the ~half-as-humid
-- outdoor air until the house is genuinely dry. The night floor is set ABOVE
-- the pre-OTA 0.62 to MAXIMIZE overnight dry time (owner directive 2026-06-15).
--
-- RESULTING RH (Magnus, at each phase's commanded temp):
--   phase   temp   vpd_low->RH   vpd_target->RH   vpd_high->RH
--   noon    84F    0.95 -> 76%    1.10 -> 72%       1.50 -> 62%
--   sunset  73F    0.90 -> 68%    1.05 -> 62%       1.25 -> 55%
--   midnight 64F   0.88 -> 57%    1.05 -> 48%       1.22 -> 40%
--   sunrise 66F    0.90 -> 59%    1.05 -> 52%       1.25 -> 43%
-- i.e. a clean dry-night (RH ~48% target, vent above ~57%, never humidify
-- until <40%) / humid-warm-day (RH ~65-76%) diurnal. Bounded above by the
-- firmware safety_vpd_max rail; RH ~40% night floor matches the healthy
-- pre-OTA regime.
--
-- center(orchid) vpd_target is raised in LOCKSTEP with the house and now sits
-- ABOVE cannabis/citrus/pepper, which (once the per-zone arbiter is wired to
-- actuation in the companion OTA) correctly makes the orchid CENTER the DRIEST
-- zone — fixing the inverted priority where orchids were the most-humidified.
--
-- Night temperature is nudged up modestly (heat supports the vent-to-dry path
-- without erasing the Vanda day/night drop).
--
-- ROLLBACK SAFETY: pure data UPDATE, no schema change, no CONCURRENTLY, no
-- top-level COMMIT -> NON-self-transactional. Rollback-validate by wrapping in
-- an outer BEGIN; ... ROLLBACK;. To revert in prod, re-seed the pre-168 values
-- (recorded below) or flip VERDIFY_BAND_SOURCE=legacy.
--
-- PRE-168 VALUES (for revert):
--   house vpd_low    sr/sm/ss/mid = 0.40/0.60/0.45/0.42
--   house vpd_target sr/sm/ss/mid = 0.60/1.05/0.60/0.50
--   house vpd_high   sr/sm/ss/mid = 0.90/1.40/0.90/0.75
--   house temp_low   mid          = 60      house temp_target mid = 64
--   orchid vpd_target sr/sm/ss/mid = 0.60/1.05/0.60/0.50
-- =============================================================================

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                 WHERE table_schema='public' AND table_name='crop_band_anchors') THEN
    RAISE NOTICE '168: crop_band_anchors absent — skipping (pre-161 DB)';
    RETURN;
  END IF;

  -- ── HOUSE VPD band: smooth flatter dry-night curve ──────────────────────
  UPDATE crop_band_anchors SET value = v.val, updated_at = now()
  FROM (VALUES
    ('vpd_low','sr',0.90),('vpd_low','sm',0.95),('vpd_low','ss',0.90),('vpd_low','mid',0.88),
    ('vpd_target','sr',1.05),('vpd_target','sm',1.10),('vpd_target','ss',1.05),('vpd_target','mid',1.05),
    ('vpd_high','sr',1.25),('vpd_high','sm',1.50),('vpd_high','ss',1.25),('vpd_high','mid',1.22)
  ) AS v(series, anchor, val)
  WHERE crop_band_anchors.greenhouse_id='vallery'
    AND crop_band_anchors.crop_type='house'
    AND crop_band_anchors.series=v.series
    AND crop_band_anchors.anchor=v.anchor;

  -- ── HOUSE night TEMP nudge (heat supports vent-to-dry; keep day untouched) ─
  UPDATE crop_band_anchors SET value = v.val, updated_at = now()
  FROM (VALUES
    ('temp_low','mid',62.0),
    ('temp_target','mid',65.0)
  ) AS v(series, anchor, val)
  WHERE crop_band_anchors.greenhouse_id='vallery'
    AND crop_band_anchors.crop_type='house'
    AND crop_band_anchors.series=v.series
    AND crop_band_anchors.anchor=v.anchor;

  -- ── ORCHID (center) vpd_target: lockstep with house -> driest zone ──────
  UPDATE crop_band_anchors SET value = v.val, updated_at = now()
  FROM (VALUES
    ('sr',1.05),('sm',1.10),('ss',1.05),('mid',1.05)
  ) AS v(anchor, val)
  WHERE crop_band_anchors.greenhouse_id='vallery'
    AND crop_band_anchors.crop_type='orchid'
    AND crop_band_anchors.series='vpd_target'
    AND crop_band_anchors.anchor=v.anchor;

  RAISE NOTICE '168: house+orchid dry-night band curve applied';
END $$;

-- Post-conditions (informational; harmless under the rollback wrap):
--   the served band peaks/troughs unchanged in SHAPE (solar cosine) but the
--   night VPD floor is now ~1.05 target / 0.88 low (was 0.50 / 0.42).
