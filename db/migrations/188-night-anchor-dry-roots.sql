-- 188-night-anchor-dry-roots.sql
--
-- #411 dry-roots decision (gate g-411, Jason 2026-07-03): the bare-root Vanda
-- night priority is DRY ROOTS, not deep DIF. Full revert of the house night
-- (solar-midnight 'mid') temp anchors to the pre-#409 canonical values plus
-- the night vpd_target retune, in ONE migration:
--
--     temp_low    mid  58     -> 60.71   (revert of #409 Item 4)
--     temp_target mid  62     -> 65.71   (revert of #409 Item 4)
--     temp_high   mid  66     -> 70.71   (revert of #409 Item 4)
--     vpd_target  mid  0.918  -> 0.83    (dry-night retune)
--
-- Why (#411): the 62 F night target caps achievable night VPD at ~0.71 under
-- full outdoor exchange — the Vandas' exposed roots stay wet all night. The
-- house naturally holds ~66.7 F on retained thermal mass, so the revert spends
-- ZERO heat; vpd_target 0.83 is physically achievable with the measured
-- +3 g/m3 indoor moisture source. The flower-initiation deep-DIF goal is
-- deferred to fall (natural cooling + window closure, #412). See GitHub #411
-- (2026-07-03 decision comment) and
-- docs/adr/0009-solar-night-realized-dryout.md.
--
-- Context: PR #409 was never merged — its Item-4 anchor edit reached prod
-- crop_band_anchors as an out-of-band UPDATE (verified live 2026-07-03:
-- mid = 58/62/66, vpd_target 0.918). The canonical seed
-- (verdify_schemas/band_defaults.yaml + generated migration 177) still carries
-- the pre-#409 temp values, so this migration is the prod reconcile back to
-- canonical plus the one NEW canonical value (vpd_target mid 0.83 — YAML and
-- 177 regenerated in the same change; test_band_defaults_single_source pins
-- them together).
--
-- Band coherence (decision design check): vpd_low mid 0.738 < 0.83 <
-- vpd_high mid 1.168, so low < target < high holds. sr/sm/ss anchors,
-- vpd_low/vpd_high, and every zone row are NOT touched.
--
-- Safety: 177-pattern INSERT ... ON CONFLICT DO UPDATE ... WHERE the value
-- actually DIFFERS — idempotent; a re-run updates zero rows. Only the four
-- house 'mid' rows above are in the change surface.
--
-- Non-self-transactional (no top-level BEGIN/COMMIT, no CONCURRENTLY): safe to
-- rollback-validate by wrapping in BEGIN; ... ROLLBACK;.

-- House night ('mid') anchors: dry-roots revert + vpd_target retune.
INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value) VALUES
  ('house','temp_low','mid',60.71),
  ('house','temp_target','mid',65.71),
  ('house','temp_high','mid',70.71),
  ('house','vpd_target','mid',0.83)
ON CONFLICT (crop_type, growth_stage, season, series, anchor, greenhouse_id) DO UPDATE SET value = EXCLUDED.value
  WHERE public.crop_band_anchors.value IS DISTINCT FROM EXCLUDED.value;

-- Post-apply assertion: the four decided rows match g-411 exactly, and the
-- house night band stays coherent (low < target < high for temp and VPD).
DO $assert$
DECLARE
    n_mismatch int;
    n_incoherent int;
BEGIN
    SELECT count(*) INTO n_mismatch FROM (VALUES
      ('house','temp_low','mid',60.71::double precision),
      ('house','temp_target','mid',65.71::double precision),
      ('house','temp_high','mid',70.71::double precision),
      ('house','vpd_target','mid',0.83::double precision)
    ) AS want(crop_type, series, anchor, value)
    JOIN public.crop_band_anchors a USING (crop_type, series, anchor)
    WHERE a.greenhouse_id = 'vallery' AND a.value IS DISTINCT FROM want.value;
    IF n_mismatch > 0 THEN
        RAISE EXCEPTION '188: % night anchor row(s) failed to apply the g-411 decision values', n_mismatch;
    END IF;

    SELECT count(*) INTO n_incoherent FROM (
        SELECT
            max(value) FILTER (WHERE series = 'temp_low')    AS t_low,
            max(value) FILTER (WHERE series = 'temp_target') AS t_tgt,
            max(value) FILTER (WHERE series = 'temp_high')   AS t_high,
            max(value) FILTER (WHERE series = 'vpd_low')     AS v_low,
            max(value) FILTER (WHERE series = 'vpd_target')  AS v_tgt,
            max(value) FILTER (WHERE series = 'vpd_high')    AS v_high
        FROM public.crop_band_anchors
        WHERE crop_type = 'house' AND anchor = 'mid' AND greenhouse_id = 'vallery'
    ) b
    WHERE NOT (b.t_low < b.t_tgt AND b.t_tgt < b.t_high
           AND b.v_low < b.v_tgt AND b.v_tgt < b.v_high);
    IF n_incoherent > 0 THEN
        RAISE EXCEPTION '188: house night band incoherent after apply (need low < target < high)';
    END IF;

    RAISE NOTICE '188: night anchors reconciled to the g-411 dry-roots decision';
END
$assert$;
