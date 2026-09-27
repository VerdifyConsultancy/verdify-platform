-- #780: run only in the disposable private replay database. This creates
-- historical diagnostics for frozen inputs; it is not a production migration.
-- Migration 242 remains the applied forecast verification contract.

CREATE FUNCTION public.fn_forecast_planning_priors_as_of(p_decision_at timestamptz)
RETURNS SETOF public.v_forecast_planning_priors
LANGUAGE sql STABLE
SET search_path = pg_catalog, public AS $function$
WITH history_candidates AS (
    SELECT f.*, p.param, p.error, p.observed_minutes
    FROM public.v_forecast_outdoor_pairs f
    CROSS JOIN LATERAL (VALUES
        ('temp_f', f.temp_error_f, f.temp_minutes),
        ('vpd_kpa', f.vpd_error_kpa, f.vpd_minutes),
        ('solar_w_m2', f.solar_error_w, f.solar_minutes)
    ) p(param, error, observed_minutes)
    WHERE p_decision_at <= now()
      AND f.forecast_hour > p_decision_at - interval '7 days'
      AND f.forecast_hour < date_bin(interval '1 hour', p_decision_at, timestamptz '1970-01-01 UTC')
      AND f.fetched_at <= p_decision_at
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
    WHERE p_decision_at <= now()
      AND ts > p_decision_at AND ts <= p_decision_at + interval '24 hours'
      AND fetched_at <= p_decision_at
    GROUP BY ts, fetched_at
), latest AS (
    SELECT DISTINCT ON (ts) *,
        extract(epoch FROM (ts - fetched_at)) / 3600.0 AS lead_hours,
        extract(epoch FROM (p_decision_at - fetched_at)) / 60.0 AS fetch_age_minutes
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
SELECT p_decision_at AS decision_at, f.ts AS valid_at, f.fetched_at AS available_at,
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
$function$;
COMMENT ON FUNCTION public.fn_forecast_planning_priors_as_of(timestamptz) IS
'Historical contract 2 diagnostics. Forecast vintage cutoff is applied before latest selection; only valid hours completed before the decision contribute calibration. Uses recorded fetched_at, not unknown provider issuance. Observation event time does not prove arrival time, so retrospective truth can include late corrections. No control retuning.';

CREATE VIEW public.v_forecast_indoor_response AS
WITH indoor_minutes AS (
    SELECT date_bin(interval '1 minute', ts, timestamptz '1970-01-01 UTC') AS minute,
        avg(vpd_avg) FILTER (WHERE vpd_avg >= 0 AND vpd_avg < 'Infinity'::float8) AS indoor_vpd_kpa
    FROM public.climate
    WHERE ts >= date_bin(interval '1 hour', now() - interval '30 days', timestamptz '1970-01-01 UTC')
      AND ts < date_bin(interval '1 hour', now(), timestamptz '1970-01-01 UTC')
    GROUP BY 1
), at_valid_hour AS (
    SELECT minute AS valid_at, indoor_vpd_kpa
    FROM indoor_minutes
    WHERE minute = date_bin(interval '1 hour', minute, timestamptz '1970-01-01 UTC')
)
SELECT i.valid_at, i.indoor_vpd_kpa, o.actual_vpd AS outdoor_vpd_kpa,
    i.indoor_vpd_kpa - o.actual_vpd AS indoor_minus_outdoor_vpd_kpa,
    'observed_indoor_outdoor_differential_not_forecast_error'::text AS outcome_label,
    2 AS verification_contract_version
FROM at_valid_hour i
LEFT JOIN public.v_forecast_outdoor_hourly o ON o.hour = i.valid_at;
COMMENT ON VIEW public.v_forecast_indoor_response IS
'Separate observed indoor/outdoor VPD differential at the valid-time minute. Not provider forecast error, control response, or a causal effect. Missing outdoor truth remains NULL.';
