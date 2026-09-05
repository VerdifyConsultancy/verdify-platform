-- #371 prerequisite: preserve stored climate metrics before formula changes.
-- Additive, outer-transaction safe. No daily_summary value/formula is changed.
-- This records database row revisions, NOT historical measurement definitions,
-- raw inputs, fixed sensor membership, target lineage or firmware consumption.
-- Serialize seed + trigger installation with writers; apply in one transaction.
LOCK TABLE public.daily_summary IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE public.daily_climate_metric_revisions (
    revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    capture_schema text NOT NULL DEFAULT 'daily-summary-capture-v1'
        CHECK (capture_schema = 'daily-summary-capture-v1'),
    operation text NOT NULL CHECK (operation IN ('baseline', 'insert', 'before_update', 'after_update', 'delete')),
    greenhouse_id text,
    day date NOT NULL,
    source_captured_at timestamptz,
    metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
    transaction_id bigint NOT NULL DEFAULT txid_current()
);
CREATE INDEX daily_climate_metric_revisions_lookup
    ON public.daily_climate_metric_revisions (greenhouse_id, day, revision_id DESC);

COMMENT ON TABLE public.daily_climate_metric_revisions IS
'Append-only database capture of stored daily climate metrics. Baseline is the '
'value at migration time, not original historical truth. capture_schema versions '
'the capture layout, NOT the measurement formula. No raw-input, target, panel or '
'writer-source identity is inferred. Prior edits before installation are unknown. '
'A future measurement contract must publish those identities separately.';

CREATE FUNCTION public.fn_daily_climate_metric_payload(d public.daily_summary)
RETURNS jsonb LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, public, pg_temp
AS $function$
    SELECT jsonb_build_object(
        'binary', jsonb_build_object(
            'compliance_pct', d.compliance_pct,
            'temp_compliance_pct', d.temp_compliance_pct,
            'vpd_compliance_pct', d.vpd_compliance_pct,
            'stress_hours_heat', d.stress_hours_heat,
            'stress_hours_cold', d.stress_hours_cold,
            'stress_hours_vpd_high', d.stress_hours_vpd_high,
            'stress_hours_vpd_low', d.stress_hours_vpd_low),
        'graded', jsonb_build_object(
            'compliance_v2_raw_pct', d.compliance_v2_raw_pct,
            'compliance_v2_attributable_pct', d.compliance_v2_attributable_pct,
            'compliance_v2_unachievable_frac', d.compliance_v2_unachievable_frac,
            'graded_temp_compliance_pct', d.graded_temp_compliance_pct,
            'graded_vpd_compliance_pct', d.graded_vpd_compliance_pct,
            'graded_stress_hours_heat', d.graded_stress_hours_heat,
            'graded_stress_hours_cold', d.graded_stress_hours_cold,
            'graded_stress_hours_vpd_high', d.graded_stress_hours_vpd_high,
            'graded_stress_hours_vpd_low', d.graded_stress_hours_vpd_low)
    );
$function$;

CREATE FUNCTION public.fn_capture_daily_climate_metric_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
    prior_metrics jsonb;
    next_metrics jsonb;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        prior_metrics := public.fn_daily_climate_metric_payload(OLD);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        next_metrics := public.fn_daily_climate_metric_payload(NEW);
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

CREATE FUNCTION public.fn_reject_daily_climate_revision_mutation()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
    IF TG_TABLE_NAME = 'daily_summary' THEN
        RAISE EXCEPTION 'TRUNCATE bypasses climate revision capture; use audited row deletion'
            USING ERRCODE = '55000';
    END IF;
    RAISE EXCEPTION 'daily climate metric revisions are append-only'
        USING ERRCODE = '55000';
END;
$function$;

-- Pin ownership to the database owner, never a duty role or its login. The
-- deployment migrator must have the ordinary role/DDL authority to do this.
DO $owner$
DECLARE owner_name text;
BEGIN
    SELECT pg_get_userbyid(datdba) INTO owner_name
      FROM pg_database WHERE datname = current_database();
    IF owner_name IN ('verdify_api_runtime', 'verdify_ingestor_runtime',
                      'verdify_api_runtime_login', 'verdify_ingestor_runtime_login') THEN
        RAISE EXCEPTION 'runtime role must not own the revision security boundary';
    END IF;
    EXECUTE format('ALTER TABLE public.daily_climate_metric_revisions OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary) OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_capture_daily_climate_metric_revision() OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_reject_daily_climate_revision_mutation() OWNER TO %I', owner_name);
END;
$owner$;

REVOKE ALL ON public.daily_climate_metric_revisions FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime, verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON SEQUENCE public.daily_climate_metric_revisions_revision_id_seq FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime, verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary),
    public.fn_capture_daily_climate_metric_revision(), public.fn_reject_daily_climate_revision_mutation()
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime, verdify_api_runtime_login, verdify_ingestor_runtime_login;
GRANT SELECT ON public.daily_climate_metric_revisions TO verdify_api_runtime, verdify_ingestor_runtime;

CREATE TRIGGER daily_climate_revisions_no_mutation
    BEFORE UPDATE OR DELETE ON public.daily_climate_metric_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_reject_daily_climate_revision_mutation();
CREATE TRIGGER daily_climate_revisions_no_truncate
    BEFORE TRUNCATE ON public.daily_climate_metric_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_reject_daily_climate_revision_mutation();

INSERT INTO public.daily_climate_metric_revisions
    (operation, greenhouse_id, day, source_captured_at, metrics)
SELECT 'baseline', d.greenhouse_id, d.date, d.captured_at, public.fn_daily_climate_metric_payload(d)
FROM public.daily_summary d;

CREATE TRIGGER daily_summary_capture_climate_revision
    AFTER INSERT OR UPDATE OR DELETE ON public.daily_summary
    FOR EACH ROW EXECUTE FUNCTION public.fn_capture_daily_climate_metric_revision();
CREATE TRIGGER daily_summary_no_unaudited_truncate
    BEFORE TRUNCATE ON public.daily_summary
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_reject_daily_climate_revision_mutation();
