-- 306 (#1433): the achievable night VPD envelope in SQL, a mirror of verdify_schemas/vpd_envelope.py.
-- Jason, 2026-11-07: build the SQL mirror now. The read model (API-06 achievable, the band editor's preview) keeps
-- using the Python functions; these let a rollup or a view compute the same envelope inside the database. Each
-- function returns what its Python twin returns for the same inputs, bit for bit, so it repeats the Python's float
-- arithmetic in the same order (tests/test_dashboard_db_vpd_envelope_306.py compares them):
--   fn_vpd_envelope_round(x, n)              Python's round(x, n): the double's exact value, ties to even;
--   fn_vpd_envelope_es_kpa(temp_f)           saturation vapour pressure (Tetens, 0.6108/17.27/237.3), kPa;
--   fn_vpd_envelope_dewpoint_f(kpa)          the inverse, °F;
--   fn_vpd_envelope_outdoor_dewpoint_f(t,rh) outdoor dewpoint from temperature and humidity;
--   fn_vpd_envelope_is_night(phase)          fn_solar_phase >= 2 (sunset to sunrise);
--   fn_vpd_envelope_fit(t_in[], td_in[], t_out[], rh_out[])
--                                            fit_dewpoint: least squares of indoor dewpoint on a + b·T_in + c·T_out
--                                            + e·Td_out over the rows in array order (a row with a null or
--                                            rh_out <= 0 is skipped); no row with fewer than 24 hours or a singular
--                                            fit. Sums are Python's (Neumaier-compensated) sum();
--   fn_vpd_envelope_expected(a,b,c,e,rmse_f,temp_f,t_out,td_out)
--                                            expected_vpd: the ribbon (p25, p75);
--   fn_vpd_envelope_achievable_band(low,high,p25,p75)
--                                            achievable_band: the band widened to the ribbon; the band unchanged
--                                            when an edge or the ribbon is null;
--   fn_vpd_envelope_grade(value,low,high)    grade: 'below', 'in', 'above' or null.
-- The training rows (deep-night hours, no moisture equipment on) stay the caller's query, as in
-- verdify_services/vpd_envelope.py. No consumer changes. The functions are verdify's with PUBLIC's EXECUTE revoked,
-- so no runtime boundary moves; a consumer migration grants what it needs. Both blocks pin exact305's seals.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=305 AND filename='db/migrations/305-band-nightly-dryout.sql'
            AND sha256='4e2db419f15541080310ca99782f38f298b17d8318b845a997b88672799b971e'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>305)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '42091d8bc52adfff17ef589ca7d71ac3a97fe37af4460b551a938f313635bb8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'c7ad6a83e598b8f6da9ebd0086c008b13cfa2812647516ecdf8a7d1b32331940'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '2c6a1cbe628ea66fafe30784189f6c9ba7c33983792a46cd7383d7dae67dc245'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR EXISTS(SELECT 1 FROM pg_proc WHERE pronamespace='public'::regnamespace
            AND proname LIKE 'fn\_vpd\_envelope\_%')
    THEN RAISE EXCEPTION '306 refuses an unqualified predecessor'; END IF;
END $predecessor$;

-- Python's round(x, n): the nearest multiple of 10^-n to the double's exact value, ties to the even one. The
-- shortest round-trip text of x brackets it between k·10^-n and (k+1)·10^-n; which side of the midpoint
-- (2k+1)/(2·10^n) it lies on is the exact sign of x·2^(n+1)·5^n - (2k+1), taken with a Veltkamp split so every
-- product is exact. n is 0..11 and |x|·10^n below 2^50 (the envelope uses 2 and 3 on kPa and °F).
CREATE FUNCTION public.fn_vpd_envelope_round(p_value double precision, p_digits integer)
RETURNS double precision
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET extra_float_digits = 1
AS $round$
DECLARE
    scale numeric;
    k numeric;
    y double precision;
    split double precision;
    yh double precision;
    yl double precision;
    five double precision;
    side double precision;
BEGIN
    IF p_digits < 0 OR p_digits > 11 THEN
        RAISE EXCEPTION 'fn_vpd_envelope_round: digits % outside 0..11', p_digits;
    END IF;
    IF p_value = 'NaN'::double precision OR p_value IN ('Infinity'::double precision, '-Infinity'::double precision)
    THEN
        RETURN p_value;
    END IF;
    IF abs(p_value) * (10::double precision ^ p_digits) >= 2::double precision ^ 50 THEN
        RAISE EXCEPTION 'fn_vpd_envelope_round: % outside the exact range at % digits', p_value, p_digits;
    END IF;
    scale := ('1e' || p_digits)::numeric;
    k := floor(p_value::text::numeric * scale);
    y := p_value * (2::double precision ^ (p_digits + 1));
    split := 134217729::double precision * y;
    yh := split - (split - y);
    yl := y - yh;
    five := 5::double precision ^ p_digits;
    side := sign((yh * five - (2 * k + 1)::double precision) + yl * five);
    IF side < 0 OR (side = 0 AND mod(k, 2) = 0) THEN
        RETURN (k / scale)::double precision;
    END IF;
    RETURN ((k + 1) / scale)::double precision;
END
$round$;

CREATE FUNCTION public.fn_vpd_envelope_es_kpa(p_temp_f double precision)
RETURNS double precision
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
AS $es$
DECLARE
    temp_c double precision := (p_temp_f - 32::double precision) * 5::double precision / 9::double precision;
BEGIN
    RETURN 0.6108::double precision
        * exp((17.27::double precision * temp_c) / (temp_c + 237.3::double precision));
END
$es$;

CREATE FUNCTION public.fn_vpd_envelope_dewpoint_f(p_vapour_kpa double precision)
RETURNS double precision
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
AS $dew$
DECLARE
    g double precision := ln(GREATEST(p_vapour_kpa, 1e-6::double precision) / 0.6108::double precision);
BEGIN
    RETURN (237.3::double precision * g / (17.27::double precision - g)) * 9::double precision / 5::double precision
        + 32::double precision;
END
$dew$;

CREATE FUNCTION public.fn_vpd_envelope_outdoor_dewpoint_f(p_temp_f double precision, p_rh_pct double precision)
RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $outdoor$
    SELECT public.fn_vpd_envelope_dewpoint_f(p_rh_pct / 100::double precision * public.fn_vpd_envelope_es_kpa(p_temp_f))
$outdoor$;

CREATE FUNCTION public.fn_vpd_envelope_is_night(p_phase double precision)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $night$
    SELECT p_phase IS NOT NULL AND p_phase >= 2::double precision
$night$;

CREATE FUNCTION public.fn_vpd_envelope_fit(
    p_t_in double precision[], p_td_in double precision[], p_t_out double precision[], p_rh_out double precision[])
RETURNS TABLE(a double precision, b double precision, c double precision, e double precision,
              rmse_f double precision, hours integer, dewpoint_p10_f double precision)
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fit$
DECLARE
    rows_in integer := COALESCE(cardinality(p_t_in), 0);
    xs double precision[] := '{}';  -- n×4, row-major: 1, T_in, T_out, Td_out
    ys double precision[] := '{}';
    n integer := 0;
    -- 4×4 X'X then 4 X'y: Python's sum() per entry (running total and Neumaier compensation).
    tot double precision[] := array_fill(0::double precision, ARRAY[20]);
    cmp double precision[] := array_fill(0::double precision, ARRAY[20]);
    m double precision[];
    coef double precision[] := array_fill(NULL::double precision, ARRAY[4]);
    i integer; j integer; r integer; col integer; piv integer;
    v double precision; t double precision; f double precision; s double precision; s_c double precision;
    factor double precision; tmp double precision;
    resid_tot double precision; resid_cmp double precision;
    observed double precision[];
BEGIN
    IF COALESCE(cardinality(p_td_in), 0) <> rows_in OR COALESCE(cardinality(p_t_out), 0) <> rows_in
        OR COALESCE(cardinality(p_rh_out), 0) <> rows_in THEN
        RAISE EXCEPTION 'fn_vpd_envelope_fit: the four arrays differ in length';
    END IF;
    FOR r IN 1..rows_in LOOP
        IF p_t_in[r] IS NULL OR p_td_in[r] IS NULL OR p_t_out[r] IS NULL OR p_rh_out[r] IS NULL
            OR p_rh_out[r] <= 0 THEN
            CONTINUE;
        END IF;
        xs := xs || ARRAY[1::double precision, p_t_in[r], p_t_out[r],
                          public.fn_vpd_envelope_outdoor_dewpoint_f(p_t_out[r], p_rh_out[r])];
        ys := ys || p_td_in[r];
        n := n + 1;
    END LOOP;
    IF n < 24 THEN
        RETURN;
    END IF;
    FOR r IN 1..n LOOP
        FOR i IN 0..19 LOOP
            IF i < 16 THEN
                v := xs[(r - 1) * 4 + i / 4 + 1] * xs[(r - 1) * 4 + i % 4 + 1];
            ELSE
                v := xs[(r - 1) * 4 + (i - 16) + 1] * ys[r];
            END IF;
            IF r = 1 THEN
                tot[i + 1] := 0::double precision + v;
            ELSE
                f := tot[i + 1];
                t := f + v;
                IF abs(f) >= abs(v) THEN
                    cmp[i + 1] := cmp[i + 1] + ((f - t) + v);
                ELSE
                    cmp[i + 1] := cmp[i + 1] + ((v - t) + f);
                END IF;
                tot[i + 1] := t;
            END IF;
        END LOOP;
    END LOOP;
    FOR i IN 1..20 LOOP
        IF cmp[i] <> 0 AND cmp[i] NOT IN ('Infinity'::double precision, '-Infinity'::double precision)
            AND cmp[i] <> 'NaN'::double precision THEN
            tot[i] := tot[i] + cmp[i];
        END IF;
    END LOOP;
    -- Gauss-Jordan on [X'X | X'y] with partial pivoting (the first largest), as _solve.
    m := array_fill(0::double precision, ARRAY[4, 5]);
    FOR i IN 1..4 LOOP
        FOR j IN 1..4 LOOP
            m[i][j] := tot[(i - 1) * 4 + j];
        END LOOP;
        m[i][5] := tot[16 + i];
    END LOOP;
    FOR col IN 1..4 LOOP
        piv := col;
        FOR r IN col + 1..4 LOOP
            IF abs(m[r][col]) > abs(m[piv][col]) THEN
                piv := r;
            END IF;
        END LOOP;
        IF abs(m[piv][col]) < 1e-9::double precision THEN
            RETURN;
        END IF;
        IF piv <> col THEN
            FOR j IN 1..5 LOOP
                tmp := m[col][j];
                m[col][j] := m[piv][j];
                m[piv][j] := tmp;
            END LOOP;
        END IF;
        FOR r IN 1..4 LOOP
            IF r <> col THEN
                factor := m[r][col] / m[col][col];
                FOR j IN 1..5 LOOP
                    m[r][j] := m[r][j] - factor * m[col][j];
                END LOOP;
            END IF;
        END LOOP;
    END LOOP;
    FOR i IN 1..4 LOOP
        coef[i] := m[i][5] / m[i][i];
    END LOOP;
    -- Residuals: y - sum(c·x) (Python's sum over four terms), then sum(r²) the same way.
    FOR r IN 1..n LOOP
        s := 0::double precision + coef[1] * xs[(r - 1) * 4 + 1];
        s_c := 0;
        FOR i IN 2..4 LOOP
            v := coef[i] * xs[(r - 1) * 4 + i];
            t := s + v;
            IF abs(s) >= abs(v) THEN
                s_c := s_c + ((s - t) + v);
            ELSE
                s_c := s_c + ((v - t) + s);
            END IF;
            s := t;
        END LOOP;
        IF s_c <> 0 AND s_c NOT IN ('Infinity'::double precision, '-Infinity'::double precision)
            AND s_c <> 'NaN'::double precision THEN
            s := s + s_c;
        END IF;
        v := (ys[r] - s) * (ys[r] - s);
        IF r = 1 THEN
            resid_tot := 0::double precision + v;
            resid_cmp := 0;
        ELSE
            t := resid_tot + v;
            IF abs(resid_tot) >= abs(v) THEN
                resid_cmp := resid_cmp + ((resid_tot - t) + v);
            ELSE
                resid_cmp := resid_cmp + ((v - t) + resid_tot);
            END IF;
            resid_tot := t;
        END IF;
    END LOOP;
    IF resid_cmp <> 0 AND resid_cmp NOT IN ('Infinity'::double precision, '-Infinity'::double precision)
        AND resid_cmp <> 'NaN'::double precision THEN
        resid_tot := resid_tot + resid_cmp;
    END IF;
    SELECT array_agg(y ORDER BY y) INTO observed FROM unnest(ys) AS y;
    a := coef[1];
    b := coef[2];
    c := coef[3];
    e := coef[4];
    rmse_f := public.fn_vpd_envelope_round(sqrt(resid_tot / n::double precision), 3);
    hours := n;
    dewpoint_p10_f := public.fn_vpd_envelope_round(
        observed[GREATEST(0, ceil(0.1::double precision * n::double precision)::integer - 1) + 1], 2);
    RETURN NEXT;
END
$fit$;

CREATE FUNCTION public.fn_vpd_envelope_expected(
    p_a double precision, p_b double precision, p_c double precision, p_e double precision,
    p_rmse_f double precision, p_temp_f double precision, p_t_out double precision, p_td_out double precision,
    OUT p25 double precision, OUT p75 double precision)
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
AS $expected$
DECLARE
    td double precision := p_a + p_b * p_temp_f + p_c * p_t_out + p_e * p_td_out;
    spread double precision := 0.674::double precision * p_rmse_f;
    es double precision := public.fn_vpd_envelope_es_kpa(p_temp_f);
BEGIN
    p25 := public.fn_vpd_envelope_round(
        GREATEST(0::double precision, es - public.fn_vpd_envelope_es_kpa(LEAST(p_temp_f, td + spread))), 3);
    p75 := public.fn_vpd_envelope_round(
        GREATEST(0::double precision, es - public.fn_vpd_envelope_es_kpa(LEAST(p_temp_f, td - spread))), 3);
END
$expected$;

CREATE FUNCTION public.fn_vpd_envelope_achievable_band(
    p_low double precision, p_high double precision, p_p25 double precision, p_p75 double precision,
    OUT vpd_low double precision, OUT vpd_high double precision)
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $band$
    SELECT CASE WHEN p_low IS NULL OR p_high IS NULL OR p_p25 IS NULL OR p_p75 IS NULL THEN p_low
                ELSE public.fn_vpd_envelope_round(LEAST(p_low, p_p25), 3) END,
           CASE WHEN p_low IS NULL OR p_high IS NULL OR p_p25 IS NULL OR p_p75 IS NULL THEN p_high
                ELSE public.fn_vpd_envelope_round(GREATEST(p_high, p_p75), 3) END
$band$;

CREATE FUNCTION public.fn_vpd_envelope_grade(p_value double precision, p_low double precision, p_high double precision)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $grade$
    SELECT CASE WHEN p_value IS NULL OR p_low IS NULL OR p_high IS NULL THEN NULL
                WHEN p_value < p_low THEN 'below'
                WHEN p_value > p_high THEN 'above'
                ELSE 'in' END
$grade$;

COMMENT ON FUNCTION public.fn_vpd_envelope_fit(double precision[], double precision[], double precision[],
    double precision[]) IS
    'The rolling indoor-dewpoint fit of verdify_schemas.vpd_envelope.fit_dewpoint (306, #1433), bit for bit: '
    'pass the deep-night hours with no moisture equipment on, in time order, as four arrays; no row when the fit '
    'has fewer than 24 hours or is singular.';
COMMENT ON FUNCTION public.fn_vpd_envelope_expected(double precision, double precision, double precision,
    double precision, double precision, double precision, double precision, double precision) IS
    'The expected night VPD ribbon (p25, p75) of verdify_schemas.vpd_envelope.expected_vpd (306, #1433).';
COMMENT ON FUNCTION public.fn_vpd_envelope_achievable_band(double precision, double precision, double precision,
    double precision) IS
    'The served band widened to the ribbon, verdify_schemas.vpd_envelope.achievable_band (306, #1433).';

REVOKE ALL ON FUNCTION public.fn_vpd_envelope_round(double precision, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_es_kpa(double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_dewpoint_f(double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_outdoor_dewpoint_f(double precision, double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_is_night(double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_fit(double precision[], double precision[], double precision[],
    double precision[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_expected(double precision, double precision, double precision,
    double precision, double precision, double precision, double precision, double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_achievable_band(double precision, double precision,
    double precision, double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_vpd_envelope_grade(double precision, double precision, double precision)
    FROM PUBLIC;

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '42091d8bc52adfff17ef589ca7d71ac3a97fe37af4460b551a938f313635bb8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'c7ad6a83e598b8f6da9ebd0086c008b13cfa2812647516ecdf8a7d1b32331940'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '2c6a1cbe628ea66fafe30784189f6c9ba7c33983792a46cd7383d7dae67dc245'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR (SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace
            AND proname LIKE 'fn\_vpd\_envelope\_%')<>9
        OR EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.pronamespace='public'::regnamespace AND p.proname LIKE 'fn\_vpd\_envelope\_%'
              AND (pg_get_userbyid(p.proowner) <> 'verdify' OR p.prosecdef
                   OR EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) x
                       WHERE x.grantee <> p.proowner)))
    THEN RAISE EXCEPTION '306 changed a runtime boundary'; END IF;
END $successor$;
