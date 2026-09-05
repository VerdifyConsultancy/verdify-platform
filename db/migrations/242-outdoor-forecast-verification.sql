-- #780: outdoor forecasts must be verified against outdoor observations.
-- Contract 2 uses fetched_at (recorded availability, NOT provider issued_at).
-- One valid time per lead bucket; duplicate vintages never multiply samples.
-- Open-Meteo temperature/RH/VPD are instantaneous; shortwave radiation is
-- PRECEDING-hour mean (https://open-meteo.com/en/docs). Instant observations
-- use the valid-time one-minute bin, not the subsequent whole-hour mean.
-- Completed UTC hours only. No raw forecasts/telemetry or control state changes.
-- Forward migration, outer-transaction/rollback safe; preserve existing view
-- prefixes and function signature/owner/ACLs. The 30-day window matches retention.

CREATE VIEW public.v_forecast_verification_contract AS
SELECT 2 AS verification_contract_version,
    'fetched_at_recorded_availability_not_provider_issuance'::text AS vintage_basis,
    'outdoor_valid_time_one_minute_bin'::text AS instant_truth_basis,
    'outdoor_preceding_hour_all_60_minute_bins'::text AS solar_truth_basis,
    false AS indoor_response_verified;
REVOKE ALL ON public.v_forecast_verification_contract FROM PUBLIC;
GRANT SELECT ON public.v_forecast_verification_contract TO verdify_ingestor_runtime;

CREATE VIEW public.v_forecast_outdoor_hourly AS
WITH minutes AS (
    SELECT date_bin(interval '1 minute', ts, timestamptz '1970-01-01 UTC') AS minute,
        avg(outdoor_temp_f) FILTER (WHERE outdoor_temp_f BETWEEN -100 AND 150) AS temp_f,
        avg(outdoor_rh_pct) FILTER (WHERE outdoor_rh_pct BETWEEN 0 AND 100) AS rh_pct,
        avg(solar_irradiance_w_m2) FILTER (
            WHERE solar_irradiance_w_m2 >= 0 AND solar_irradiance_w_m2 < 'Infinity'::float8
        ) AS solar_w_m2
    FROM public.climate
    WHERE ts >= date_bin(interval '1 hour', now() - interval '30 days 1 hour', timestamptz '1970-01-01 UTC')
      AND ts < date_bin(interval '1 hour', now(), timestamptz '1970-01-01 UTC')
    GROUP BY 1
), samples AS (
    SELECT *, CASE WHEN temp_f IS NOT NULL AND rh_pct IS NOT NULL
        THEN 0.6108 * exp(17.27 * ((temp_f - 32) / 1.8) / (((temp_f - 32) / 1.8) + 237.3))
             * (1 - rh_pct / 100.0)
        END AS vpd_kpa
    FROM minutes
)
SELECT date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC') AS hour,
    avg(temp_f) FILTER (WHERE minute = date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC')) AS actual_temp,
    avg(rh_pct) FILTER (WHERE minute = date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC')) AS actual_rh,
    avg(vpd_kpa) FILTER (WHERE minute = date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC')) AS actual_vpd,
    CASE WHEN count(solar_w_m2) = 60 THEN avg(solar_w_m2) END AS actual_solar,
    count(temp_f) FILTER (WHERE minute = date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC')) AS temp_minutes,
    count(rh_pct) FILTER (WHERE minute = date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC')) AS rh_minutes,
    count(vpd_kpa) FILTER (WHERE minute = date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC')) AS vpd_minutes,
    count(solar_w_m2) AS solar_minutes
FROM samples GROUP BY 1;

CREATE VIEW public.v_forecast_outdoor_pairs AS
WITH vintages AS (
    SELECT ts, fetched_at,
        count(*) AS vintage_rows,
        count(DISTINCT jsonb_build_array(temp_f, rh_pct, vpd_kpa, solar_w_m2)) > 1 AS vintage_conflict,
        min(temp_f) AS temp_f, min(rh_pct) AS rh_pct,
        min(vpd_kpa) AS vpd_kpa, min(solar_w_m2) AS solar_w_m2
    FROM public.weather_forecast
    WHERE ts >= date_bin(interval '1 hour', now() - interval '30 days', timestamptz '1970-01-01 UTC')
      AND ts < date_bin(interval '1 hour', now(), timestamptz '1970-01-01 UTC')
      AND ts = date_bin(interval '1 hour', ts, timestamptz '1970-01-01 UTC')
      AND fetched_at <= ts
    GROUP BY ts, fetched_at
), paired AS (
    SELECT f.ts AS forecast_hour, f.fetched_at,
        extract(epoch FROM (f.ts - f.fetched_at)) / 3600.0 AS lead_hours,
        CASE WHEN NOT f.vintage_conflict AND f.temp_f BETWEEN -100 AND 150 THEN f.temp_f END AS forecast_temp,
        CASE WHEN NOT f.vintage_conflict AND f.rh_pct BETWEEN 0 AND 100 THEN f.rh_pct END AS forecast_rh,
        CASE WHEN NOT f.vintage_conflict AND f.vpd_kpa >= 0 AND f.vpd_kpa < 'Infinity'::float8 THEN f.vpd_kpa END AS forecast_vpd,
        CASE WHEN NOT f.vintage_conflict AND f.fetched_at <= f.ts - interval '1 hour'
                  AND f.solar_w_m2 >= 0 AND f.solar_w_m2 < 'Infinity'::float8
             THEN f.solar_w_m2 END AS forecast_solar,
        o.actual_temp, o.actual_rh, o.actual_vpd, s.actual_solar,
        COALESCE(o.temp_minutes, 0) AS temp_minutes,
        COALESCE(o.rh_minutes, 0) AS rh_minutes,
        COALESCE(o.vpd_minutes, 0) AS vpd_minutes,
        COALESCE(s.solar_minutes, 0) AS solar_minutes,
        f.vintage_rows, f.vintage_conflict
    FROM vintages f LEFT JOIN public.v_forecast_outdoor_hourly o ON o.hour = f.ts
    LEFT JOIN public.v_forecast_outdoor_hourly s ON s.hour = f.ts - interval '1 hour'
)
SELECT *, CASE WHEN lead_hours < 6 THEN '00-06h'
               WHEN lead_hours < 24 THEN '06-24h'
               WHEN lead_hours < 48 THEN '24-48h' ELSE '48h+' END AS lead_bucket,
    forecast_temp - actual_temp AS temp_error_f,
    forecast_rh - actual_rh AS rh_error_pct,
    forecast_vpd - actual_vpd AS vpd_error_kpa,
    forecast_solar - actual_solar AS solar_error_w,
    2 AS verification_contract_version
FROM paired;

COMMENT ON VIEW public.v_forecast_outdoor_pairs IS
'Contract 2: one recorded forecast vintage per valid UTC hour. Instant temp/RH/VPD use '
'the valid-time one-minute outdoor bin; solar uses the PRECEDING hour with all 60 '
'minute bins required. VPD uses outdoor temperature/RH, never indoor vpd_avg. '
'fetched_at is availability, not provider issuance. '
'Solar vintages fetched after the preceding-hour window started are excluded. '
'Conflicting duplicate vintages are unavailable; counts are observed minutes, not coverage claims. '
'Bounded to 30 days. For historical decisions filter fetched_at <= decision_at BEFORE selecting latest.';

REVOKE ALL ON public.v_forecast_outdoor_hourly, public.v_forecast_outdoor_pairs FROM PUBLIC;
GRANT SELECT ON public.v_forecast_outdoor_pairs TO verdify_ingestor_runtime;

CREATE OR REPLACE VIEW public.v_forecast_accuracy AS
WITH weather AS (
    SELECT DISTINCT ON (forecast_hour) *
    FROM public.v_forecast_outdoor_pairs ORDER BY forecast_hour, fetched_at DESC
), solar AS (
    SELECT DISTINCT ON (forecast_hour) *
    FROM public.v_forecast_outdoor_pairs WHERE lead_hours >= 1
    ORDER BY forecast_hour, fetched_at DESC
)
SELECT
    w.forecast_hour, w.fetched_at, round(w.lead_hours, 1) AS lead_hours,
    w.forecast_temp, w.actual_temp, round(w.temp_error_f::numeric, 1) AS temp_error_f,
    w.forecast_vpd, w.actual_vpd, round(w.vpd_error_kpa::numeric, 2) AS vpd_error_kpa,
    s.forecast_solar, s.actual_solar, round(s.solar_error_w::numeric, 1) AS solar_error_w,
    w.forecast_rh, w.actual_rh, w.rh_error_pct,
    w.temp_minutes, w.rh_minutes, w.vpd_minutes, s.solar_minutes,
    w.vintage_rows, w.vintage_conflict, w.verification_contract_version,
    s.fetched_at AS solar_fetched_at, s.lead_hours AS solar_lead_hours,
    s.vintage_conflict AS solar_vintage_conflict
FROM weather w LEFT JOIN solar s USING (forecast_hour);

CREATE OR REPLACE VIEW public.v_forecast_accuracy_daily AS
SELECT (f.forecast_hour AT TIME ZONE 'America/Denver')::date AS date,
    p.param,
    avg(p.forecast) FILTER (WHERE p.actual IS NOT NULL) AS forecast_avg,
    avg(p.actual) FILTER (WHERE p.forecast IS NOT NULL) AS observed_avg,
    avg(p.forecast - p.actual) AS bias,
    avg(abs(p.forecast - p.actual)) AS abs_error,
    round(avg(CASE WHEN p.param = 'solar_w_m2' THEN f.solar_lead_hours ELSE f.lead_hours END)
        FILTER (WHERE p.forecast IS NOT NULL AND p.actual IS NOT NULL), 1) AS horizon_hours,
    count(p.forecast - p.actual) AS samples,
    sum(p.observed_minutes) FILTER (WHERE p.forecast IS NOT NULL AND p.actual IS NOT NULL) AS observed_minutes,
    2 AS verification_contract_version
FROM public.v_forecast_accuracy f
CROSS JOIN LATERAL (VALUES
    ('temp_f', f.forecast_temp, f.actual_temp, f.temp_minutes),
    ('rh_pct', f.forecast_rh, f.actual_rh, f.rh_minutes),
    ('vpd_kpa', f.forecast_vpd, f.actual_vpd, f.vpd_minutes),
    ('solar_w_m2', f.forecast_solar, f.actual_solar, f.solar_minutes),
    ('cloud_cover_pct', NULL::float8, NULL::float8, 0::bigint)
) p(param, forecast, actual, observed_minutes)
WHERE f.forecast_hour >= now() - interval '14 days'
GROUP BY 1, p.param;

CREATE OR REPLACE VIEW public.v_forecast_accuracy_lead_buckets AS
WITH candidates AS (
    SELECT f.*, p.param, p.error, p.observed_minutes
    FROM public.v_forecast_outdoor_pairs f
    CROSS JOIN LATERAL (VALUES
        ('temp_f', f.temp_error_f, f.temp_minutes),
        ('rh_pct', f.rh_error_pct, f.rh_minutes),
        ('vpd_kpa', f.vpd_error_kpa, f.vpd_minutes),
        ('solar_w_m2', f.solar_error_w, f.solar_minutes)
    ) p(param, error, observed_minutes)
    WHERE p.param <> 'solar_w_m2' OR f.lead_hours >= 1
), selected AS (
    SELECT DISTINCT ON (forecast_hour, lead_bucket, param) *
    FROM candidates ORDER BY forecast_hour, lead_bucket, param, fetched_at DESC
)
SELECT (f.forecast_hour AT TIME ZONE 'America/Denver')::date AS date,
    f.lead_bucket, f.param,
    count(f.error) AS samples,
    round(avg(f.error)::numeric, CASE f.param WHEN 'vpd_kpa' THEN 3 WHEN 'solar_w_m2' THEN 1 ELSE 2 END) AS bias,
    round(avg(abs(f.error))::numeric, CASE f.param WHEN 'vpd_kpa' THEN 3 WHEN 'solar_w_m2' THEN 1 ELSE 2 END) AS mae,
    round(avg(f.lead_hours) FILTER (WHERE f.error IS NOT NULL), 2) AS mean_lead_hours,
    sum(f.observed_minutes) FILTER (WHERE f.error IS NOT NULL) AS observed_minutes,
    count(*) FILTER (WHERE f.vintage_conflict) AS conflicting_hours,
    2 AS verification_contract_version
FROM selected f
GROUP BY 1, f.lead_bucket, f.param;

CREATE OR REPLACE VIEW public.v_forecast_vs_actual AS
SELECT forecast_hour AS hour,
    round(forecast_temp::numeric, 1) AS forecast_temp,
    round(forecast_rh::numeric, 0) AS forecast_rh,
    round(forecast_vpd::numeric, 2) AS forecast_vpd,
    round(forecast_solar::numeric, 0) AS forecast_solar,
    round(actual_temp::numeric, 1) AS actual_temp,
    round(actual_rh::numeric, 0) AS actual_rh,
    round(actual_solar::numeric, 0) AS actual_solar,
    temp_error_f AS temp_error, round(solar_error_w, 0) AS solar_error
FROM public.v_forecast_accuracy
WHERE forecast_hour > now() - interval '7 days';

CREATE OR REPLACE FUNCTION public.fn_forecast_correction(param text, lead_hours_max numeric DEFAULT 24)
RETURNS TABLE(parameter text, avg_error numeric, samples bigint)
LANGUAGE sql STABLE AS $function$
    WITH selected AS (
        SELECT DISTINCT ON (forecast_hour) *
        FROM public.v_forecast_outdoor_pairs
        WHERE forecast_hour > now() - interval '7 days'
          AND lead_hours <= lead_hours_max
          AND (param <> 'solar_w_m2' OR lead_hours >= 1)
        ORDER BY forecast_hour, fetched_at DESC
    ), errors AS (
        SELECT CASE param
            WHEN 'temp_f' THEN temp_error_f
            WHEN 'rh_pct' THEN rh_error_pct
            WHEN 'vpd_kpa' THEN vpd_error_kpa
            WHEN 'solar_w_m2' THEN solar_error_w END AS error
        FROM selected
    )
    SELECT param, round(avg(error)::numeric, CASE param WHEN 'vpd_kpa' THEN 3 WHEN 'solar_w_m2' THEN 0 ELSE 1 END),
        count(error)
    FROM errors;
$function$;

COMMENT ON FUNCTION public.fn_forecast_correction(text, numeric) IS
'Contract 2: 7-day bias against observed OUTDOOR truth. lead_hours_max limits forecast '
'lead (valid_at minus fetched_at), not age of the observation. One latest eligible '
'vintage per completed valid hour; sample count is paired hours, not telemetry rows. '
'Unsupported parameter or missing observations returns NULL bias and zero samples.';
COMMENT ON VIEW public.v_forecast_accuracy IS
'Contract 2: latest pre-valid-hour available forecast against correctly timed outdoor truth; '
'not a historical fixed-decision forecast. Use paired vintages with an as-of cutoff for that.';
COMMENT ON VIEW public.v_forecast_accuracy_daily IS
'Contract 2: matched-hour outdoor forecast bias and mean absolute hourly error, '
'not absolute daily-mean bias. samples count paired hours; unavailable remains NULL.';
COMMENT ON VIEW public.v_forecast_accuracy_lead_buckets IS
'Contract 2: latest eligible vintage per valid hour and lead bucket. samples count '
'paired hours, never repeated forecasts times telemetry rows. Outdoor truth only.';

-- Prospective context uses only data available NOW, with per-row freshness and
-- matching lead bucket. Missing calibration is NULL, not a zero-bias estimate.
CREATE VIEW public.v_forecast_planning_priors AS
WITH history_candidates AS (
    SELECT f.*, p.param, p.error, p.observed_minutes
    FROM public.v_forecast_outdoor_pairs f
    CROSS JOIN LATERAL (VALUES
        ('temp_f', f.temp_error_f, f.temp_minutes),
        ('vpd_kpa', f.vpd_error_kpa, f.vpd_minutes),
        ('solar_w_m2', f.solar_error_w, f.solar_minutes)
    ) p(param, error, observed_minutes)
    WHERE f.forecast_hour > now() - interval '7 days'
      AND (p.param <> 'solar_w_m2' OR f.lead_hours >= 1)
), selected_history AS (
    SELECT DISTINCT ON (forecast_hour, lead_bucket, param) *
    FROM history_candidates ORDER BY forecast_hour, lead_bucket, param, fetched_at DESC
), calibration AS (
    SELECT lead_bucket, param, avg(error) AS bias, count(error) AS paired_hours,
        sum(observed_minutes) FILTER (WHERE error IS NOT NULL) AS observed_minutes
    FROM selected_history GROUP BY lead_bucket, param
), vintages AS (
    SELECT ts, fetched_at,
        count(DISTINCT jsonb_build_array(temp_f, vpd_kpa, solar_w_m2)) > 1 AS vintage_conflict,
        min(temp_f) AS temp_f, min(vpd_kpa) AS vpd_kpa, min(solar_w_m2) AS solar_w_m2
    FROM public.weather_forecast
    WHERE ts > now() AND ts <= now() + interval '24 hours'
      AND fetched_at <= now()
    GROUP BY ts, fetched_at
), latest AS (
    SELECT DISTINCT ON (ts) *,
        extract(epoch FROM (ts - fetched_at)) / 3600.0 AS lead_hours,
        extract(epoch FROM (now() - fetched_at)) / 60.0 AS fetch_age_minutes
    FROM vintages ORDER BY ts, fetched_at DESC
), raw AS (
    SELECT f.*, p.param, p.raw_forecast,
        CASE WHEN lead_hours < 6 THEN '00-06h' WHEN lead_hours < 24 THEN '06-24h'
             WHEN lead_hours < 48 THEN '24-48h' ELSE '48h+' END AS lead_bucket
    FROM latest f
    CROSS JOIN LATERAL (VALUES
        ('temp_f', CASE WHEN f.temp_f BETWEEN -100 AND 150 THEN f.temp_f END),
        ('vpd_kpa', CASE WHEN f.vpd_kpa >= 0 AND f.vpd_kpa < 'Infinity'::float8 THEN f.vpd_kpa END),
        ('solar_w_m2', CASE WHEN f.solar_w_m2 >= 0 AND f.solar_w_m2 < 'Infinity'::float8 THEN f.solar_w_m2 END)
    ) p(param, raw_forecast)
)
SELECT now() AS decision_at, f.ts AS valid_at, f.fetched_at AS available_at,
    f.lead_hours, f.fetch_age_minutes, f.lead_bucket, f.param,
    CASE WHEN NOT f.vintage_conflict THEN f.raw_forecast END AS raw_forecast,
    c.bias, COALESCE(c.paired_hours, 0) AS calibration_paired_hours,
    c.observed_minutes AS calibration_observed_minutes,
    CASE WHEN NOT f.vintage_conflict AND f.fetch_age_minutes <= 120
              AND f.raw_forecast IS NOT NULL AND c.paired_hours > 0
              AND (f.param <> 'solar_w_m2' OR f.lead_hours >= 1)
         THEN CASE WHEN f.param = 'temp_f' THEN f.raw_forecast - c.bias
                   ELSE greatest(0, f.raw_forecast - c.bias) END
    END AS corrected_prior,
    CASE WHEN f.vintage_conflict THEN 'conflicting_vintage'
         WHEN f.raw_forecast IS NULL THEN 'missing_forecast'
         WHEN f.fetch_age_minutes > 120 THEN 'stale_forecast'
         WHEN f.param = 'solar_w_m2' AND f.lead_hours < 1 THEN 'partial_window_nowcast'
         WHEN COALESCE(c.paired_hours, 0) = 0 THEN 'missing_calibration'
         ELSE 'available_diagnostic' END AS availability,
    2 AS verification_contract_version
FROM raw f LEFT JOIN calibration c USING (lead_bucket, param);

REVOKE ALL ON public.v_forecast_planning_priors FROM PUBLIC;
GRANT SELECT ON public.v_forecast_planning_priors TO verdify_ingestor_runtime;
COMMENT ON VIEW public.v_forecast_planning_priors IS
'Contract 2: as-of-now future forecasts with matching-lead outdoor calibration. '
'Available_at is recorded fetch time, not provider issue time. Missing/stale/conflicting '
'forecasts or absent calibration yield NULL corrected prior. No automatic control '
'retuning or evidence of indoor response; inspect observed minutes and paired hours.';
