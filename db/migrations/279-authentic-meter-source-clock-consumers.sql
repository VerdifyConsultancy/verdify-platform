-- #981: accept the explicit authentic Modbus source revision in existing
-- partial diagnostic consumers. Preserve every age, finite, signed-total,
-- original-entity, missing-interval and available_for_scoring=false guard.
-- No old energy row, write facade, grant, ownership or control policy changes.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=278 AND filename='db/migrations/278-grafana-public-saved-query-bounds.sql'
        AND sha256='3ba5b19da2695d6656cfd5174dac0028eba917738f5498b83af0914ec5d86312'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=279)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02','hex'))
    THEN RAISE EXCEPTION '279 refuses an unqualified predecessor'; END IF;
END $predecessor$;
DO $source_clock$
DECLARE
    subject text;
    expected text;
    definition text;
    before_metadata jsonb;
    old_predicate constant text := 'e.measurement_revision = ''ha_shelly_power_v1''::text';
    new_predicate constant text := 'e.measurement_revision = ANY (ARRAY[''ha_shelly_power_v1''::text, ''shelly_modbus_source_v2''::text])';
BEGIN
    FOR subject,expected IN SELECT * FROM (VALUES
        ('v_energy_daily','c6088ba4248b32ef13725307b479b10412072afb6fdbe1a7cf08bb6bcaac67a8'),
        ('v_resource_accounting_health','e9851af0c20438180d89ee980343c262c68ef4e22fc823e5b51f1f04c2458140')
    ) predecessors(name,sha256) LOOP
        SELECT pg_get_viewdef(c.oid,true),to_jsonb(c)-'relfilenode'
          INTO definition,before_metadata FROM pg_class c
          WHERE c.oid=to_regclass('public.'||subject)
            AND c.relowner=(SELECT datdba FROM pg_database WHERE datname=current_database());
        IF definition IS NULL OR encode(sha256(convert_to(definition,'UTF8')),'hex')<>expected
           OR (length(definition)-length(replace(definition,old_predicate,'')))/length(old_predicate)<>1
        THEN RAISE EXCEPTION '279 refuses unknown energy consumer predecessor'; END IF;
        EXECUTE format('CREATE OR REPLACE VIEW public.%I AS %s',subject,
                       replace(definition,old_predicate,new_predicate));
        IF (SELECT to_jsonb(c)-'relfilenode' FROM pg_class c
            WHERE c.oid=to_regclass('public.'||subject)) IS DISTINCT FROM before_metadata
        THEN RAISE EXCEPTION '279 changed consumer identity, ownership, grants or options'; END IF;
    END LOOP;
END $source_clock$;

-- Exact production-OID successor measured in a rollback-only transaction.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '9904555fb62c32503b99cd3cd447c4412afd0451782d7216cf7a21557339357e' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '0e03e19dd7ceaf65c54e14d8fa38dacffa8472e2173d0d7f41a6a03ea758d8f7' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02'
    THEN RAISE EXCEPTION '279 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('9904555fb62c32503b99cd3cd447c4412afd0451782d7216cf7a21557339357e','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '279 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('0e03e19dd7ceaf65c54e14d8fa38dacffa8472e2173d0d7f41a6a03ea758d8f7','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '279 missing ingestor receipt'; END IF;
END $successor$;
