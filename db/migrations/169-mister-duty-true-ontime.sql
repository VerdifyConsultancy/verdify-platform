-- 169-mister-duty-true-ontime.sql
-- =====================================================================
-- B7: fix the garbage mister duty metric in v_mister_zone_effectiveness.
-- =====================================================================
-- Lane: laptop-root (Verdify metrics correctness).
--
-- BUG: the old view's `starts` CTE filters equipment_state to state=true rows
-- FIRST, then computes off_ts := lead(ts) OVER (PARTITION BY equipment ORDER BY
-- ts). Because only ON rows survive the filter, that lead() returns the ts of
-- the NEXT ON event, not the matching OFF transition. So `duration_s` measured
-- the gap-to-next-pulse (often many minutes / hours), NOT the physical relay
-- ON-time. Live prod showed avg "duration" of 26-464 MINUTES and daily totals of
-- 1400+ min/day (> 24h) for a mister that physically pulses for ~5-90 SECONDS.
--
-- FIX: keep BOTH states in the window so the next state-change is visible.
-- equipment_state is an event-sourced alternating ON/OFF stream (state is
-- boolean; t/f), so for each ON row the true OFF interval is:
--   off_ts := (ts of the NEXT state-change for that equipment), CLAMPED so it
--             never spans past the matching OFF transition.
-- We compute the next event ts (next_change_ts, any state) and the next OFF ts
-- (next_off_ts, state=false) per equipment, then take LEAST(next_change_ts,
-- next_off_ts). In the normal alternating stream these are equal; the clamp only
-- matters if two consecutive ON rows ever appear (duplicate push) — then we still
-- bound the interval at the real OFF instead of running to the next ON.
-- A physical cap (MAX_PULSE_S = 600s) bounds a trailing ON whose OFF is missing
-- (e.g. ingestor restart / data gap) so a dropped OFF can't reintroduce a
-- multi-hour phantom pulse.
--
-- Output columns + types are UNCHANGED (on_ts timestamptz, equipment text, zone
-- text, duration_s integer, zone_vpd_before/after numeric, zone_vpd_delta
-- numeric), so CREATE OR REPLACE VIEW is in-place and the downstream consumer
-- v_mister_effectiveness_rollup / scn surfaces keep resolving without a drop.
--
-- Classification: NON-self-transactional — no top-level BEGIN/COMMIT, no
-- CONCURRENTLY, no commit-forcing statement. Safe to rollback-validate by
-- wrapping in an outer BEGIN; ... ROLLBACK; (see check_migration_rollback_safety
-- --check). Do NOT add a top-level COMMIT.
--
-- Apply (dev first, then prod):
--   cat db/migrations/169-mister-duty-true-ontime.sql | kubectl exec -i \
--     -n verdify-dev verdify-db-0 -c postgres -- \
--     psql -U verdify -d verdify -v ON_ERROR_STOP=1
-- =====================================================================

CREATE OR REPLACE VIEW public.v_mister_zone_effectiveness AS
 WITH events AS (
         SELECT equipment_state.ts,
            equipment_state.equipment,
                CASE equipment_state.equipment
                    WHEN 'mister_south'::text THEN 'south'::text
                    WHEN 'mister_west'::text THEN 'west'::text
                    WHEN 'mister_center'::text THEN 'center'::text
                    ELSE NULL::text
                END AS zone,
            equipment_state.state,
            -- next state-change of ANY state for this equipment
            lead(equipment_state.ts) OVER w AS next_change_ts,
            -- next OFF transition for this equipment (clamps duplicate-ON runs)
            (
                SELECT min(off_evt.ts)
                  FROM public.equipment_state off_evt
                 WHERE off_evt.equipment = equipment_state.equipment
                   AND off_evt.state = false
                   AND off_evt.ts > equipment_state.ts
            ) AS next_off_ts
           FROM public.equipment_state
          WHERE ((equipment_state.equipment = ANY (ARRAY['mister_south'::text, 'mister_west'::text, 'mister_center'::text]))
             AND (equipment_state.ts > (now() - '30 days'::interval)))
         WINDOW w AS (PARTITION BY equipment_state.equipment ORDER BY equipment_state.ts)
        ), starts AS (
         SELECT e.ts AS on_ts,
            e.equipment,
            e.zone,
            -- true ON interval: next state-change clamped to the matching OFF,
            -- and capped at MAX_PULSE_S (600s) so a missing OFF (data gap) can't
            -- reintroduce a multi-hour phantom pulse.
            LEAST(
                COALESCE(LEAST(e.next_change_ts, e.next_off_ts), e.next_off_ts, e.next_change_ts),
                e.ts + '00:10:00'::interval
            ) AS off_ts
           FROM events e
          WHERE (e.state = true)
        )
 SELECT s.on_ts,
    s.equipment,
    s.zone,
    (EXTRACT(epoch FROM (s.off_ts - s.on_ts)))::integer AS duration_s,
    before_vpd.vpd AS zone_vpd_before,
    after_vpd.vpd AS zone_vpd_after,
    round(((before_vpd.vpd - after_vpd.vpd))::numeric, 3) AS zone_vpd_delta
   FROM ((starts s
     LEFT JOIN LATERAL ( SELECT
                CASE s.zone
                    WHEN 'south'::text THEN climate.vpd_south
                    WHEN 'west'::text THEN climate.vpd_west
                    ELSE climate.vpd_avg
                END AS vpd
           FROM public.climate
          WHERE (climate.ts <= s.on_ts)
          ORDER BY climate.ts DESC
         LIMIT 1) before_vpd ON (true))
     LEFT JOIN LATERAL ( SELECT
                CASE s.zone
                    WHEN 'south'::text THEN climate.vpd_south
                    WHEN 'west'::text THEN climate.vpd_west
                    ELSE climate.vpd_avg
                END AS vpd
           FROM public.climate
          WHERE (climate.ts >= (COALESCE(s.off_ts, s.on_ts) + '00:05:00'::interval))
          ORDER BY climate.ts
         LIMIT 1) after_vpd ON (true))
  WHERE (s.off_ts IS NOT NULL);

COMMENT ON VIEW public.v_mister_zone_effectiveness IS 'Mister on-events with TRUE relay on-time (duration_s = next state-change clamped to the matching OFF, capped at 600s) and zone-local VPD before/after the pulse. Center uses greenhouse average until a center VPD sensor exists. (B7: duration_s was previously gap-to-next-pulse, overcounting by ~100x.)';
