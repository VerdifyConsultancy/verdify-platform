-- 136-band-heat-target-lower-quartile.sql
--
-- Keep the dashboard/planner band traceability function aligned with the
-- firmware lower-quartile heat target.

DO $$
DECLARE
    fn_def text;
    old_expr constant text := '(t.firmware_temp_low + t.firmware_temp_high) * 0.5';
    new_expr constant text := 't.firmware_temp_low + t.temp_width_f * 0.25';
BEGIN
    SELECT pg_get_functiondef(
        'public.fn_band_timeline(timestamp with time zone, timestamp with time zone, interval, text)'::regprocedure
    )
      INTO fn_def;

    IF position(new_expr IN fn_def) > 0 THEN
        RETURN;
    END IF;

    IF position(old_expr IN fn_def) = 0 THEN
        RAISE EXCEPTION 'fn_band_timeline heat-target expression not found';
    END IF;

    EXECUTE replace(fn_def, old_expr, new_expr);
END $$;

COMMENT ON FUNCTION public.fn_band_timeline(timestamptz, timestamptz, interval, text)
IS 'Actual-to-forecast band timeline for dashboards: historical firmware-pushed setpoints through now, dispatcher-projected band after now, crop provenance, and firmware-derived trigger/padding thresholds. Heat target mirrors firmware lower-quartile band-first policy.';
