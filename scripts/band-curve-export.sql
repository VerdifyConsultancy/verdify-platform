-- band-curve-export.sql — dump a crop's 24-hour band curve for safe re-authoring.
-- Lane VerdifyConsultancy/verdify-platform#221 (band-tuning tooling).
--
-- Usage (dev DB — NEVER point this WRITE-side; this is read-only):
--   ssh jason@192.168.30.32 \
--     "sudo k3s kubectl -n verdify-dev exec -i statefulset/verdify-db -- \
--      psql -U verdify -d verdify -v crop=orchid -v season=spring" \
--      < scripts/band-curve-export.sql
--
-- Override the crop/season with -v crop=<name> -v season=<season>
-- (defaults below if unset).

\if :{?crop}
\else
  \set crop 'orchid'
\endif
\if :{?season}
\else
  \set season 'spring'
\endif

\echo '── Band curve for crop=' :crop ' season=' :season ' ──'

-- 1. The raw 24-hour curve + computed widths (the re-author input table).
SELECT
    hour_of_day                                  AS h,
    round(temp_ideal_min::numeric, 1)            AS temp_lo,
    round(temp_ideal_max::numeric, 1)            AS temp_hi,
    round((temp_ideal_max - temp_ideal_min)::numeric, 1) AS temp_width,
    round(vpd_ideal_min::numeric, 2)             AS vpd_lo,
    round(vpd_ideal_max::numeric, 2)             AS vpd_hi,
    round((vpd_ideal_max - vpd_ideal_min)::numeric, 2)   AS vpd_width,
    round(temp_stress_low::numeric, 1)           AS temp_stress_lo,
    round(temp_stress_high::numeric, 1)          AS temp_stress_hi,
    round(vpd_stress_low::numeric, 2)            AS vpd_stress_lo,
    round(vpd_stress_high::numeric, 2)           AS vpd_stress_hi,
    source
FROM crop_target_profiles
WHERE crop_type = :'crop'
  AND season    = :'season'
ORDER BY hour_of_day;

-- 2. Curve shape summary — peak/trough + swing (the "time-of-day" signature).
\echo '── Diurnal swing summary (how much the band moves across the day) ──'
SELECT
    round(min(temp_ideal_min)::numeric,1) AS temp_lo_min,
    round(max(temp_ideal_max)::numeric,1) AS temp_hi_max,
    round((max(temp_ideal_max) - min(temp_ideal_min))::numeric,1) AS temp_total_swing,
    round(min(vpd_ideal_min)::numeric,2)  AS vpd_lo_min,
    round(max(vpd_ideal_max)::numeric,2)  AS vpd_hi_max,
    round((max(vpd_ideal_max) - min(vpd_ideal_min))::numeric,2) AS vpd_total_swing,
    count(*) AS rows
FROM crop_target_profiles
WHERE crop_type = :'crop' AND season = :'season';

-- 3. Feasibility flags — hours where the band may be too tight to hold.
\echo '── Tight-band warnings (temp_width<6F or vpd_width<0.3kPa = churn risk) ──'
SELECT hour_of_day AS h,
       round((temp_ideal_max - temp_ideal_min)::numeric,1) AS temp_width,
       round((vpd_ideal_max  - vpd_ideal_min)::numeric,2)  AS vpd_width
FROM crop_target_profiles
WHERE crop_type = :'crop' AND season = :'season'
  AND ( (temp_ideal_max - temp_ideal_min) < 6.0
        OR (vpd_ideal_max - vpd_ideal_min) < 0.30 )
ORDER BY hour_of_day;

-- 4. What the dispatcher would actually push right now (served = intersection of
--    all active crops). If this differs from the curve above, another crop is the
--    binding constraint this hour.
\echo '── Resolved served setpoints fn_band_setpoints(now()) (all active crops) ──'
SELECT * FROM fn_band_setpoints(now());
