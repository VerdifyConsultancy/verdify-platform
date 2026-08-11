-- 160-orchid-vpd-band-realign-PROPOSAL.sql
-- =====================================================================
-- ███  PROPOSAL — DEV-DB / SHADOW ONLY.  DO NOT APPLY TO PROD.  ███
-- =====================================================================
-- Lane: laptop-root LANE C firmware-optimization (#249; covers #250 #251).
-- This migration RE-AUTHORS only the orchid VPD ideal band (vpd_ideal_min/max)
-- of crop_target_profiles to the shadow-verified "candB" curve:
--   * wider + higher VPD ideal band (tracks the house p75, not below p50),
--   * VPD evening shoulder ALIGNED to the temp shoulder,
--   * evening ceiling held high (~1.55) through h22 so h18-21 is not graded
--     against an unachievable floor (house evening VPD p50=1.46, p90=2.21).
-- It LEAVES UNTOUCHED: temp_ideal_*, temp_stress_*, vpd_stress_*, dli_target_mol.
--
-- Rationale + shadow analysis: docs/proposals/verdify-band-redesign-2026-06-07.md
-- Gated live-apply procedure (NOT this file): docs/runbooks/verdify-band-live-apply-safe.md
--
-- Safety: idempotent (UPDATE keyed on the unique row), reversible (the prior
-- vanda_spec_v1.0 values are recorded in migration 145). NOT wired into any
-- kustomize overlay; NOT auto-applied by ArgoCD. Apply ONLY to verdify-dev by
-- hand, eyeball the band-tuning-diurnal dashboard, then a GATED prod decision.
--
-- Apply to DEV ONLY:
--   ssh jason@192.168.30.32 "sudo k3s kubectl -n verdify-dev exec -i \
--     statefulset/verdify-db -- psql -U verdify -d verdify -v ON_ERROR_STOP=1" \
--     < db/migrations/160-orchid-vpd-band-realign-PROPOSAL.sql
-- =====================================================================

BEGIN;

-- Guard: refuse to run unless the orchid rows exist (apply 145 first) and
-- refuse to run against a DB that looks like prod-with-active-orchid unless the
-- operator explicitly sets verdify.allow_band_write = 'on' (belt-and-suspenders;
-- prod apply still goes through the gated runbook, never this guard alone).
DO $guard$
DECLARE
    v_rows int;
BEGIN
    SELECT count(*) INTO v_rows
      FROM crop_target_profiles
     WHERE crop_type='orchid' AND growth_stage='vegetative' AND greenhouse_id='vallery';
    IF v_rows < 24 THEN
        RAISE EXCEPTION 'PROPOSAL 160 prerequisite: orchid/vegetative rows missing (apply 145 first). found=%', v_rows;
    END IF;
END
$guard$;

-- candB VPD ideal curve, per local hour (0..23), applied to BOTH seasons so the
-- season-aware resolver serves it whatever fn_current_season() returns. Values
-- chosen from the 14-day house VPD distribution (night p50~0.69, day p50~1.22,
-- evening p50~1.46) — band centered slightly above p50 with >=0.30 kPa width,
-- evening ceiling held to 1.55 to avoid grading an unachievable target.
WITH candb(h, vlo, vhi) AS (VALUES
  (0,0.65,1.15),(1,0.65,1.15),(2,0.65,1.15),(3,0.65,1.15),(4,0.65,1.15),
  (5,0.65,1.15),(6,0.65,1.18),(7,0.68,1.25),(8,0.72,1.32),(9,0.78,1.40),
  (10,0.82,1.45),(11,0.86,1.50),(12,0.90,1.52),(13,0.92,1.53),(14,0.93,1.55),
  (15,0.93,1.55),(16,0.92,1.55),(17,0.90,1.55),(18,0.86,1.55),(19,0.82,1.55),
  (20,0.78,1.52),(21,0.74,1.45),(22,0.70,1.35),(23,0.67,1.22)
)
UPDATE crop_target_profiles p
   SET vpd_ideal_min = candb.vlo,
       vpd_ideal_max = candb.vhi,
       -- widen the VPD stress envelope to 0.50..1.62 so the new ideal ceiling
       -- (up to 1.55) sits INSIDE the stress band with a graded shoulder above
       -- it. Justified by the 14-day evening house VPD (p90=2.21) — a Vanda
       -- tolerates the wider stress range; this is NOT a setpoint change, it
       -- only changes how far the grader gives partial credit. vpd_stress_low
       -- stays 0.50 (sub-0.5 sustained = fungal risk).
       vpd_stress_high = GREATEST(p.vpd_stress_high, 1.62),
       source          = 'vanda_spec_v1.1_vpd_realign'
  FROM candb
 WHERE p.crop_type='orchid'
   AND p.growth_stage='vegetative'
   AND p.greenhouse_id='vallery'
   AND p.hour_of_day = candb.h;

-- Feasibility assertion: every authored VPD width >= 0.30 kPa, floor < ceiling,
-- floor >= vpd_stress_low, ceiling <= vpd_stress_high (stay inside the stress envelope).
DO $check$
DECLARE
    v_bad int;
BEGIN
    SELECT count(*) INTO v_bad
      FROM crop_target_profiles
     WHERE crop_type='orchid' AND growth_stage='vegetative' AND greenhouse_id='vallery'
       AND ( (vpd_ideal_max - vpd_ideal_min) < 0.30
          OR vpd_ideal_min < vpd_stress_low
          OR vpd_ideal_max > vpd_stress_high );
    IF v_bad > 0 THEN
        RAISE EXCEPTION 'PROPOSAL 160 feasibility check failed on % orchid rows (width<0.30 or outside stress envelope)', v_bad;
    END IF;
END
$check$;

COMMIT;

-- Post-apply verification (run on dev after COMMIT):
--   SELECT hour_of_day, vpd_ideal_min, vpd_ideal_max,
--          round((vpd_ideal_max-vpd_ideal_min)::numeric,2) AS width
--     FROM crop_target_profiles
--    WHERE crop_type='orchid' AND season='summer' AND growth_stage='vegetative'
--    ORDER BY hour_of_day;
-- Then re-render grafana band-tuning-diurnal ($crop=orchid, $season=summer).
