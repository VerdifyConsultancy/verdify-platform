-- 159-band-season-resolver-guard.sql
-- =====================================================================
-- #253 guard: assert the band resolver chain is SEASON-AWARE.
-- =====================================================================
-- Lane: laptop-root LANE C firmware-optimization (#249; #253).
--
-- Background: #253 was filed because db/schema.sql ON THE `main` BRANCH still
-- showed `fn_band_setpoints` hardcoding `season='spring'`. That snapshot is
-- STALE. The DB prod actually deploys (live/platform-main, migrations 145/146
-- "Vanda band/compliance rearchitecture") already resolves season via
-- fn_current_season() across the whole chain. Verified live 2026-06-07.
--
-- This migration MUTATES NOTHING. It is an idempotent ASSERTION that FAILS LOUDLY
-- if any band resolver ever regresses to a hardcoded season literal — turning the
-- latent #253 risk into a migration/CI tripwire. Safe to run on dev and prod
-- (read-only); re-runnable.
--
-- Apply (dev first, then OK on prod — it only reads catalog):
--   ssh jason@192.168.30.32 "sudo k3s kubectl -n verdify-dev exec -i \
--     statefulset/verdify-db -- psql -U verdify -d verdify -v ON_ERROR_STOP=1" \
--     < db/migrations/159-band-season-resolver-guard.sql
-- =====================================================================

DO $guard$
DECLARE
    fns text[] := ARRAY[
        'fn_band_setpoints',
        'fn_center_band_setpoints',
        'fn_zone_band',
        'fn_zone_vpd_targets'
    ];
    fn text;
    src text;
    n_present int := 0;
BEGIN
    FOREACH fn IN ARRAY fns LOOP
        SELECT string_agg(pg_get_functiondef(p.oid), E'\n')
          INTO src
          FROM pg_proc p
          JOIN pg_namespace ns ON ns.oid = p.pronamespace
         WHERE ns.nspname='public' AND p.proname = fn;

        IF src IS NULL THEN
            -- function not present in this DB lineage; skip (not all DBs carry
            -- every helper — e.g. fn_zone_vpd_targets may be absent in dev).
            CONTINUE;
        END IF;
        n_present := n_present + 1;

        -- Must reference the season resolver.
        IF position('fn_current_season' in src) = 0 THEN
            RAISE EXCEPTION '#253 REGRESSION: %() does not call fn_current_season() — season-aware resolution lost.', fn;
        END IF;

        -- Must NOT carry a *sole* hardcoded-season WHERE without the dynamic
        -- variable. We allow `:= ''spring''` (the nearest-season FALLBACK) but
        -- flag a `season = ''spring''` predicate that is NOT a fallback assign.
        IF src ~ 'season\s*=\s*''spring''' AND src !~ 'season\s*=\s*v_season'
           AND src !~ 'season\s*=\s*fn_current_season' THEN
            RAISE EXCEPTION '#253 REGRESSION: %() filters season=''spring'' literally with no v_season/fn_current_season predicate.', fn;
        END IF;
    END LOOP;

    IF n_present = 0 THEN
        RAISE EXCEPTION '#253 guard: none of the expected band resolver functions exist — wrong DB?';
    END IF;

    RAISE NOTICE '#253 guard OK: % band resolver function(s) are season-aware.', n_present;
END
$guard$;
