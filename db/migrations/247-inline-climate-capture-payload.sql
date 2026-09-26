-- #783/#371: remove the untracked private payload callee from the capture path.
-- Forward repair; migrations 217 and 241--246 remain byte-for-byte unchanged.
-- Preserve capture layout v2 and all existing rows. No receipt refresh, new
-- privilege, physical/experimental eligibility, or release authorization.
-- The ordinary-login startup transition remains a separate release gate.
LOCK TABLE public.daily_summary IN SHARE ROW EXCLUSIVE MODE;

DO $predecessor$
DECLARE
    owner_oid oid;
    capture_row record;
    payload_row record;
    capture_hash text;
BEGIN
    SELECT datdba INTO owner_oid FROM pg_catalog.pg_database WHERE datname=current_database();
    SELECT p.*, l.lanname INTO capture_row
      FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
     WHERE p.oid=pg_catalog.to_regprocedure('public.fn_capture_daily_climate_metric_revision()');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'inline capture requires its known predecessor';
    END IF;
    IF capture_row.proowner IS DISTINCT FROM owner_oid
       OR capture_row.lanname <> 'plpgsql' OR NOT capture_row.prosecdef
       OR capture_row.prorettype <> 'pg_catalog.trigger'::regtype
       OR capture_row.provolatile <> 'v' OR capture_row.proisstrict
       OR capture_row.pronargdefaults <> 0
       OR capture_row.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, public, pg_temp']::text[]
       OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(coalesce(capture_row.proacl,
                    pg_catalog.acldefault('f',capture_row.proowner))) a
                   WHERE a.grantee <> capture_row.proowner) THEN
        RAISE EXCEPTION 'inline capture refuses predecessor ownership or privilege drift';
    END IF;
    capture_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(capture_row.prosrc,'UTF8')),'hex');
    SELECT p.*, l.lanname INTO payload_row
      FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
     WHERE p.oid=pg_catalog.to_regprocedure('public.fn_daily_climate_metric_payload(public.daily_summary)');
    IF capture_hash = 'c07a3ade92256325fdb89f0d1d516825e723256101e65c24798e0e7d44613940' AND NOT FOUND THEN
        RETURN; -- exact repaired state: safe direct rerun without recreating the helper
    END IF;
    IF NOT FOUND OR capture_hash <> 'af5d98a0e285cf41a0bc0cdc2ea6b7cd77e94d9a86285e2a595eb9d9a46a165f' THEN
        RAISE EXCEPTION 'inline capture refuses unknown function state';
    END IF;
    -- SQL/PLpgSQL string bodies need not have a pg_depend edge to their callees.
    -- Conservatively refuse other literal references instead of removing an
    -- unrelated caller's dependency. Dynamic SQL still needs restored-schema review.
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND p.oid NOT IN (capture_row.oid, payload_row.oid)
          AND strpos(lower(p.prosrc),'fn_daily_climate_metric_payload') > 0
    ) THEN
        RAISE EXCEPTION 'inline capture refuses other stored payload callers';
    END IF;
    IF encode(pg_catalog.sha256(pg_catalog.convert_to(payload_row.prosrc,'UTF8')),'hex')
           <> 'a235e62704ed4d45c191b5d75ff3210f49d9c7efe17f941a657a6f735cd85f57'
       OR payload_row.proowner IS DISTINCT FROM owner_oid
       OR payload_row.lanname <> 'sql' OR payload_row.prosecdef
       OR payload_row.prorettype <> 'pg_catalog.jsonb'::regtype
       OR payload_row.provolatile <> 'i' OR payload_row.proisstrict
       OR payload_row.pronargdefaults <> 0
       OR payload_row.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, public, pg_temp']::text[]
       OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(coalesce(payload_row.proacl,
                    pg_catalog.acldefault('f',payload_row.proowner))) a
                   WHERE a.grantee <> payload_row.proowner) THEN
        RAISE EXCEPTION 'inline capture refuses payload source or privilege drift';
    END IF;
END;
$predecessor$;

CREATE OR REPLACE FUNCTION public.fn_capture_daily_climate_metric_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
    prior_metrics jsonb;
    next_metrics jsonb;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        prior_metrics := jsonb_build_object(
        'binary', jsonb_build_object(
            'compliance_pct', OLD.compliance_pct,
            'temp_compliance_pct', OLD.temp_compliance_pct,
            'vpd_compliance_pct', OLD.vpd_compliance_pct,
            'stress_hours_heat', OLD.stress_hours_heat,
            'stress_hours_cold', OLD.stress_hours_cold,
            'stress_hours_vpd_high', OLD.stress_hours_vpd_high,
            'stress_hours_vpd_low', OLD.stress_hours_vpd_low),
        'graded', jsonb_build_object(
            'compliance_v2_raw_pct', OLD.compliance_v2_raw_pct,
            'compliance_v2_attributable_pct', OLD.compliance_v2_attributable_pct,
            'compliance_v2_unachievable_frac', OLD.compliance_v2_unachievable_frac,
            'graded_temp_compliance_pct', OLD.graded_temp_compliance_pct,
            'graded_vpd_compliance_pct', OLD.graded_vpd_compliance_pct,
            'graded_stress_hours_heat', OLD.graded_stress_hours_heat,
            'graded_stress_hours_cold', OLD.graded_stress_hours_cold,
            'graded_stress_hours_vpd_high', OLD.graded_stress_hours_vpd_high,
            'graded_stress_hours_vpd_low', OLD.graded_stress_hours_vpd_low),
        'observed_minute_diagnostic', OLD.climate_observed_minute_metrics
    );
    END IF;
    IF TG_OP <> 'DELETE' THEN
        next_metrics := jsonb_build_object(
        'binary', jsonb_build_object(
            'compliance_pct', NEW.compliance_pct,
            'temp_compliance_pct', NEW.temp_compliance_pct,
            'vpd_compliance_pct', NEW.vpd_compliance_pct,
            'stress_hours_heat', NEW.stress_hours_heat,
            'stress_hours_cold', NEW.stress_hours_cold,
            'stress_hours_vpd_high', NEW.stress_hours_vpd_high,
            'stress_hours_vpd_low', NEW.stress_hours_vpd_low),
        'graded', jsonb_build_object(
            'compliance_v2_raw_pct', NEW.compliance_v2_raw_pct,
            'compliance_v2_attributable_pct', NEW.compliance_v2_attributable_pct,
            'compliance_v2_unachievable_frac', NEW.compliance_v2_unachievable_frac,
            'graded_temp_compliance_pct', NEW.graded_temp_compliance_pct,
            'graded_vpd_compliance_pct', NEW.graded_vpd_compliance_pct,
            'graded_stress_hours_heat', NEW.graded_stress_hours_heat,
            'graded_stress_hours_cold', NEW.graded_stress_hours_cold,
            'graded_stress_hours_vpd_high', NEW.graded_stress_hours_vpd_high,
            'graded_stress_hours_vpd_low', NEW.graded_stress_hours_vpd_low),
        'observed_minute_diagnostic', NEW.climate_observed_minute_metrics
    );
    END IF;
    IF TG_OP = 'UPDATE'
       AND prior_metrics IS NOT DISTINCT FROM next_metrics
       AND OLD.date IS NOT DISTINCT FROM NEW.date
       AND OLD.greenhouse_id IS NOT DISTINCT FROM NEW.greenhouse_id THEN
        RETURN NULL; -- unrelated fields/no-op refresh must not manufacture revisions
    END IF;
    IF TG_OP <> 'INSERT' THEN
        INSERT INTO public.daily_climate_metric_revisions
            (operation, greenhouse_id, day, source_captured_at, metrics)
        VALUES (CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'before_update' END,
                OLD.greenhouse_id, OLD.date, OLD.captured_at, prior_metrics);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        INSERT INTO public.daily_climate_metric_revisions
            (operation, greenhouse_id, day, source_captured_at, metrics)
        VALUES (CASE WHEN TG_OP = 'INSERT' THEN 'insert' ELSE 'after_update' END,
                NEW.greenhouse_id, NEW.date, NEW.captured_at, next_metrics);
    END IF;
    RETURN NULL; -- AFTER trigger; never modifies or skips the source write
END;
$function$;

-- RESTRICT deliberately refuses unexpected dependencies. The runner's outer
-- transaction restores both functions on failure; never cascade unrelated work.
DROP FUNCTION IF EXISTS public.fn_daily_climate_metric_payload(public.daily_summary) RESTRICT;

COMMENT ON FUNCTION public.fn_capture_daily_climate_metric_revision() IS
'Capture layout v2 with inline binary/graded/observed-minute JSON. No private payload callee. Existing metric semantics and eligibility are unchanged; startup boundary transition is still required.';

DO $installed$
BEGIN
    IF pg_catalog.to_regprocedure('public.fn_daily_climate_metric_payload(public.daily_summary)') IS NOT NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_proc p
            WHERE p.oid=pg_catalog.to_regprocedure('public.fn_capture_daily_climate_metric_revision()')
              AND encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')
                  = 'c07a3ade92256325fdb89f0d1d516825e723256101e65c24798e0e7d44613940'
       ) THEN
        RAISE EXCEPTION 'inline capture installation failed its exact source check';
    END IF;
END;
$installed$;
