-- 172-smooth-solar-phase.sql
--
-- SMOOTH THE SOLAR PHASE (Jason, 2026-06-15). The band is value(solar_phase(t)).
-- fn_solar_phase was piecewise-LINEAR between the 4 solar anchors (sunrise=0,
-- noon=1, sunset=2, midnight=3): constant rate within the day half (SR→SS, ~15 h
-- → phase 0→2) and a DIFFERENT constant rate within the night half (SS→SR, ~9 h →
-- phase 2→4). So dphase/dt JUMPED ~1.6× at sunrise and sunset, putting a slope
-- CORNER in the band there. That corner scales with band amplitude: visible on
-- the ~26 °F temp band, invisible on the ~0.45 kPa VPD band → "temp lumpy, VPD
-- smooth" even though the harmonic value() is perfectly smooth in phase.
--
-- Fix: replace the linear interpolation with a piecewise cubic HERMITE through
-- the same 4 anchors, where each anchor's tangent = the mean of its two adjacent
-- segment rates. That makes dphase/dt continuous (C1) → the band is a smooth
-- curve in TIME. It still passes EXACTLY through 0/1/2/3 at the solar events, and
-- it stays monotone for every day/night ratio this site sees (tangent/secant in
-- ~[0.8,1.33], well inside the Hermite monotonicity bound [0,3]).
--
-- ALIGNMENT: this is byte-for-byte the same math now in firmware
-- greenhouse_solar.h::solar_phase and ingestor solar.py::solar_phase — the band
-- is computed identically on the device, in the DB, and in the ingestor audit.
-- (Residual: the DB approximates solar noon as midpoint(SR,SS) while firmware/
-- ingestor use the NOAA ephemeris noon; that sub-degree difference predates this
-- change and is orthogonal to the kink fix.)
--
-- Non-self-transactional (plain CREATE OR REPLACE, no COMMIT / no CONCURRENTLY):
-- safe to rollback-validate under an outer BEGIN; … ROLLBACK;. No device impact
-- on its own (the device runs its own copy); this aligns the DB/graph to it.

-- Reusable cubic-Hermite evaluator: phase from pa→pa+1 over [a,b] with endpoint
-- slopes da,db (phase units per hour). Returns pa when the segment is degenerate.
CREATE OR REPLACE FUNCTION public.fn_hermite_phase(
    v double precision, a double precision, b double precision,
    pa double precision, da double precision, db double precision
) RETURNS double precision
  LANGUAGE sql IMMUTABLE
AS $$
  SELECT CASE
    WHEN b <= a THEN pa
    ELSE pa + (3.0*u*u - 2.0*u*u*u)
            + da*len*(u*u*u - 2.0*u*u + u)
            + db*len*(u*u*u - u*u)
  END
  FROM (SELECT (b - a) AS len, (v - a) / NULLIF(b - a, 0) AS u) q;
$$;

CREATE OR REPLACE FUNCTION public.fn_solar_phase(target_ts timestamp with time zone)
 RETURNS double precision
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    h     double precision;  -- local decimal hour of target_ts
    sr    double precision;  -- sunrise, local decimal hours
    ss    double precision;  -- sunset
    sm    double precision;  -- solar noon ~ midpoint(SR, SS)
    nsr   double precision;  -- next sunrise = SR + 24
    smid  double precision;  -- solar midnight ~ midpoint(SS, next SR)
    hh    double precision;  -- h unwrapped onto the monotonic solar day
    r0 double precision; r1 double precision; r2 double precision; r3 double precision;
    d_sr double precision; d_sm double precision; d_ss double precision; d_mid double precision;
BEGIN
    h  := EXTRACT(epoch FROM (target_ts AT TIME ZONE 'America/Denver')
                          - date_trunc('day', target_ts AT TIME ZONE 'America/Denver')) / 3600.0;
    sr := fn_solar_sunrise_hour(target_ts);
    ss := fn_solar_sunset_hour(target_ts);
    sm := (sr + ss) / 2.0;
    nsr := sr + 24.0;
    smid := (ss + nsr) / 2.0;
    hh := CASE WHEN h < sr THEN h + 24.0 ELSE h END;  -- pre-dawn → night half

    -- Segment rates (phase per hour) and C1 anchor tangents (mean of adjacent).
    r0 := 1.0 / GREATEST(sm - sr, 1e-6);
    r1 := 1.0 / GREATEST(ss - sm, 1e-6);
    r2 := 1.0 / GREATEST(smid - ss, 1e-6);
    r3 := 1.0 / GREATEST(nsr - smid, 1e-6);
    d_sr  := 0.5 * (r3 + r0);
    d_sm  := 0.5 * (r0 + r1);
    d_ss  := 0.5 * (r1 + r2);
    d_mid := 0.5 * (r2 + r3);

    IF hh <= sm THEN
        RETURN fn_hermite_phase(hh, sr,  sm,   0.0, d_sr,  d_sm);
    ELSIF hh <= ss THEN
        RETURN fn_hermite_phase(hh, sm,  ss,   1.0, d_sm,  d_ss);
    ELSIF hh <= smid THEN
        RETURN fn_hermite_phase(hh, ss,  smid, 2.0, d_ss,  d_mid);
    END IF;
    RETURN LEAST(fn_hermite_phase(hh, smid, nsr, 3.0, d_mid, d_sr), 3.9999999);
END;
$function$;
