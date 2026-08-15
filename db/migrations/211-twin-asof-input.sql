-- 211-twin-asof-input.sql
--
-- Issue #587 (Lane F of epic #581): the DB half of the §8.9 firmware-twin
-- LIVE-shadow adapter (docs/research/planner-efficacy-current-firmware-2026-08-14.md
-- §8.9, closing paragraph). Adds:
--
--   1. v_policy_twin_asof_input — security-barrier view pairing each settled
--      telemetry tick (climate) with the latest DEVICE-CONFIRMED policy
--      identity at or before that tick (policy_device_snapshots joined back
--      to the admitted effective_policy_vectors row), plus the §8.9 feed
--      manifest: tick timestamp + clock validity, sensor values/validity/
--      freshness, relay/readback state, firmware boot/reset signal, and the
--      water/budget/dwell/fairness state AVAILABLE in telemetry today.
--   2. twin_live_results — append-only per-tick shadow outcome (paired
--      identity, twin decision, live action, agreement flags, §8.9
--      classification). Hypertable like twin_decisions (migration 155).
--   3. Grants: the twin runtime roles (twin_ro from migration 155; the
--      experiment-era verdify_twin_ro name fixed by
--      db/roles/experiment-roles.sql) get SELECT on the view and INSERT on
--      twin_live_results ONLY — no policy/control DML, per §8.9.
--
-- §8.9 FEED COLUMNS AVAILABLE vs GAPS (enumerated, not fabricated):
--   AVAILABLE  tick ts (climate.ts, deduplicated), clock validity
--              (diagnostics.sntp_valid / last_sntp_sync_age_s as-of join),
--              sensor values (climate), sensor validity (non-null triple),
--              outdoor freshness (conservative change-observation, bounded
--              24 h — same semantics as scripts/export-replay-overrides.sh),
--              relay/readback state (equipment_state as-of, event-sourced),
--              boot/reset signal (diagnostics.uptime_s < 300 boot inference +
--              reset_reason — the v_reboot_log criterion, migration 059),
--              water state (climate.mister_water_today / flow_gpm /
--              water_total_gal), dwell/fairness state (diagnostics
--              sealed_timer_s / vpd_watch_timer_s / mist_backoff_timer_s /
--              vent_latch_timer_s / relief_cycle_count / zone_wet_granted /
--              band_source), non-wire setpoint posture (setpoint_snapshot
--              complete-batch as-of jsonb).
--   GAPS       (a) no typed firmware boot/reset EVENT row exists — boots are
--              inferred from the uptime_s drop (§8.9 asks for "its
--              deterministic initialization event"; firmware Lane owns that);
--              (b) per-relay last-off timestamps / per-zone runtime seconds
--              are DERIVED views over equipment_state, not telemetry — the
--              twin reconstructs dwell state internally instead;
--              (c) no budget-remaining telemetry beyond the
--              mister_water_today accumulator (resets on reboot);
--              (d) resident-FSM state is not echoed by firmware; the twin's
--              own resident state stands in, with warm-up classified per
--              §8.9 (never as agreement).
--
-- NON-SELF-TRANSACTIONAL (no top-level BEGIN/COMMIT; no commit-forcing
-- statement — create_hypertable is autocommit-safe, per migration 155) —
-- safe to wrap in BEGIN;...;ROLLBACK; for #23 rollback validation.
--
-- IDEMPOTENT: CREATE OR REPLACE VIEW, CREATE TABLE/INDEX IF NOT EXISTS,
-- if_not_exists => TRUE, role-exists DO shims, DROP TRIGGER IF EXISTS +
-- CREATE TRIGGER, REVOKE-then-GRANT. Safe to re-run.
--
-- ROLLBACK NOTES (disposable fixture only, never live):
--   REVOKE ALL ON public.v_policy_twin_asof_input FROM twin_ro, verdify_twin_ro;
--   REVOKE ALL ON public.twin_live_results FROM twin_ro, verdify_twin_ro;
--   DROP TRIGGER IF EXISTS trg_twin_live_results_append_only ON public.twin_live_results;
--   DROP VIEW IF EXISTS public.v_policy_twin_asof_input;
--   DROP TABLE IF EXISTS public.twin_live_results;
--   (roles are shared with 155 / the role scaffold — never dropped here)

-- ============================================================================
-- 1. v_policy_twin_asof_input — the §8.9 as-of feed (security barrier)
--
-- One row per deduplicated climate tick. Every join is an as-of LATERAL
-- (latest source row at or before the tick), so the view is incrementally
-- pollable: ts/greenhouse_id predicates push down into the climate scan
-- (leakproof btree operators; the dedup window partitions on exactly those
-- columns).
-- ============================================================================

CREATE OR REPLACE VIEW public.v_policy_twin_asof_input
    WITH (security_barrier = true) AS
WITH ranked_climate AS (
    -- climate carries historical duplicate timestamps from earlier ingestion
    -- paths (same guard as scripts/export-replay-overrides.sh): pick one
    -- deterministic whole row per (greenhouse_id, ts); never merge fields
    -- across duplicates or emit two feed rows for one tick.
    SELECT c0.*,
           row_number() OVER (
               PARTITION BY c0.greenhouse_id, c0.ts
               ORDER BY (c0.temp_avg IS NOT NULL
                         AND c0.vpd_avg IS NOT NULL
                         AND c0.rh_avg IS NOT NULL) DESC,
                        (c0.outdoor_temp_f IS NOT NULL
                         AND c0.outdoor_rh_pct IS NOT NULL) DESC,
                        c0.temp_avg DESC NULLS LAST,
                        c0.vpd_avg DESC NULLS LAST,
                        c0.rh_avg DESC NULLS LAST
           ) AS duplicate_rank
      FROM public.climate c0
)
SELECT
    c.greenhouse_id,
    c.ts,
    -- ── Sensor values (§8.9: value, validity, freshness) ────────────────────
    c.temp_avg,
    c.rh_avg,
    c.vpd_avg,
    c.dew_point AS indoor_dew_point,
    COALESCE(c.enthalpy_delta, -5) AS enthalpy_delta,
    c.solar_irradiance_w_m2,
    c.outdoor_temp_f,
    COALESCE(c.outdoor_rh_pct, 30) AS outdoor_rh_pct,
    -- Magnus outdoor dew point, identical to the corpus exporter.
    CASE
        WHEN c.outdoor_temp_f IS NULL OR c.outdoor_rh_pct IS NULL
          OR c.outdoor_rh_pct <= 0 THEN NULL
        ELSE ((243.04 * (ln(c.outdoor_rh_pct / 100.0)
                 + (17.625 * ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0))
                   / (243.04 + ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0))))
              / (17.625 - (ln(c.outdoor_rh_pct / 100.0)
                 + (17.625 * ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0))
                   / (243.04 + ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0))))
             ) * 9.0 / 5.0 + 32.0
    END AS outdoor_dewpoint_f,
    (c.temp_avg IS NOT NULL AND c.rh_avg IS NOT NULL AND c.vpd_avg IS NOT NULL)
        AS sensors_valid,
    -- Conservative outdoor freshness: last tick (bounded 24 h) where the
    -- persisted outdoor pair actually CHANGED — export-script semantics; an
    -- observation older than the bound yields NULL and the harness treats the
    -- outdoor feed as stale (fail-conservative).
    od.outdoor_observation_ts,
    CASE
        WHEN c.outdoor_temp_f IS NULL OR c.outdoor_rh_pct IS NULL
          OR od.outdoor_observation_ts IS NULL THEN NULL
        ELSE GREATEST(0, extract(epoch FROM (c.ts - od.outdoor_observation_ts))::int)
    END AS outdoor_data_age_s,
    -- ── Occupancy (system_state as-of) ──────────────────────────────────────
    (occ.value = 'occupied') AS occupied,
    -- ── Clock validity (§8.9; diagnostics as-of) ────────────────────────────
    diag.ts AS diag_asof,
    diag.sntp_valid,
    diag.last_sntp_sync_age_s,
    (diag.sntp_valid = 1) AS clock_valid,
    -- ── Firmware boot/reset signal (§8.9 twin-state reset trigger) ──────────
    boot.boot_event_ts,
    boot.reset_reason,
    diag.uptime_s,
    diag.firmware_version,
    -- ── Water / budget / dwell / fairness state available in telemetry ──────
    c.mister_water_today,
    c.flow_gpm,
    c.water_total_gal,
    diag.sealed_timer_s,
    diag.vpd_watch_timer_s,
    diag.mist_backoff_timer_s,
    diag.vent_latch_timer_s,
    diag.relief_cycle_count,
    diag.zone_wet_granted,
    diag.band_source,
    -- ── Relay / readback state (equipment_state as-of; event-sourced) ───────
    (eq.states ->> 'fog')::boolean            AS live_relay_fog,
    (eq.states ->> 'vent')::boolean           AS live_relay_vent,
    (eq.states ->> 'fan1')::boolean           AS live_relay_fan1,
    (eq.states ->> 'fan2')::boolean           AS live_relay_fan2,
    (eq.states ->> 'heat1')::boolean          AS live_relay_heat1,
    (eq.states ->> 'heat2')::boolean          AS live_relay_heat2,
    (eq.states ->> 'mister_south')::boolean   AS live_mister_south,
    (eq.states ->> 'mister_west')::boolean    AS live_mister_west,
    (eq.states ->> 'mister_center')::boolean  AS live_mister_center,
    eq.newest_transition                      AS relay_readback_asof,
    -- ── Non-wire setpoint posture (complete dispatcher batch as-of) ─────────
    spb.ts AS sp_asof,
    spv.sp_payload,
    -- ── Paired device-confirmed policy identity (§8.9 as-of pairing) ────────
    snap.snapshot_id,
    snap.device_id,
    snap.reported_at AS snapshot_reported_at,
    GREATEST(0, extract(epoch FROM (c.ts - snap.reported_at)))::bigint
        AS snapshot_age_s,
    snap.device_generation,
    snap.assignment_id,
    snap.content_sha256    AS observed_content_sha256,
    snap.activation_sha256 AS observed_activation_sha256,
    snap.validity          AS observed_validity,
    snap.apply_state,
    snap.schema_revision,
    vec.vector_id,
    vec.content_sha256     AS vector_content_sha256,
    vec.activation_sha256  AS vector_activation_sha256,
    vec.canonical_bytes    AS vector_canonical_bytes,
    vec.status             AS vector_status,
    vec.validity           AS vector_validity,
    (snap.content_sha256 IS NOT NULL
     AND snap.content_sha256 = vec.content_sha256) AS policy_hash_match,
    ((snap.validity IS NULL OR snap.validity @> c.ts)
     AND (vec.validity IS NULL OR vec.validity @> c.ts)) AS validity_contains_tick,
    -- ── Confirmed exposure interval covering the tick (stale-vector guard) ──
    expo.exposure_id,
    expo.identity_confirmed AS exposure_identity_confirmed
FROM ranked_climate c
LEFT JOIN LATERAL (
    -- Last CHANGE of the persisted outdoor pair at or before the tick
    -- (export-script lag semantics), bounded to 24 h so the as-of probe
    -- stays chunk-local. An observation older than the bound yields NULL
    -- age and the harness treats the outdoor feed as stale.
    SELECT w.ts AS outdoor_observation_ts
      FROM (
          SELECT o.ts, o.outdoor_temp_f, o.outdoor_rh_pct,
                 lag(o.outdoor_temp_f) OVER (ORDER BY o.ts) AS prev_temp,
                 lag(o.outdoor_rh_pct) OVER (ORDER BY o.ts) AS prev_rh
            FROM public.climate o
           WHERE o.greenhouse_id = c.greenhouse_id
             AND o.ts <= c.ts
             AND o.ts >= c.ts - interval '24 hours'
      ) w
     WHERE w.outdoor_temp_f IS NOT NULL
       AND w.outdoor_rh_pct IS NOT NULL
       AND (w.outdoor_temp_f IS DISTINCT FROM w.prev_temp
            OR w.outdoor_rh_pct IS DISTINCT FROM w.prev_rh)
     ORDER BY w.ts DESC
     LIMIT 1
) od ON true
LEFT JOIN LATERAL (
    SELECT s.value
      FROM public.system_state s
     WHERE s.greenhouse_id = c.greenhouse_id
       AND s.entity = 'occupancy'
       AND s.ts <= c.ts
     ORDER BY s.ts DESC
     LIMIT 1
) occ ON true
LEFT JOIN LATERAL (
    SELECT d.ts, d.sntp_valid, d.last_sntp_sync_age_s, d.uptime_s,
           d.firmware_version, d.sealed_timer_s, d.vpd_watch_timer_s,
           d.mist_backoff_timer_s, d.vent_latch_timer_s,
           d.relief_cycle_count, d.zone_wet_granted, d.band_source
      FROM public.diagnostics d
     WHERE d.greenhouse_id = c.greenhouse_id
       AND d.ts <= c.ts
       AND d.ts >= c.ts - interval '1 hour'
     ORDER BY d.ts DESC
     LIMIT 1
) diag ON true
LEFT JOIN LATERAL (
    -- Boot inference (no typed boot event exists — see GAPS above): the
    -- newest diagnostics row within 7 days whose uptime is under the
    -- v_reboot_log 300 s criterion marks the last boot at or before the tick.
    SELECT d.ts AS boot_event_ts, d.reset_reason
      FROM public.diagnostics d
     WHERE d.greenhouse_id = c.greenhouse_id
       AND d.ts <= c.ts
       AND d.ts >= c.ts - interval '7 days'
       AND d.uptime_s < 300
     ORDER BY d.ts DESC
     LIMIT 1
) boot ON true
LEFT JOIN LATERAL (
    SELECT jsonb_object_agg(e.equipment, e.state) AS states,
           max(e.ts) AS newest_transition
      FROM (
          SELECT DISTINCT ON (es.equipment) es.equipment, es.state, es.ts
            FROM public.equipment_state es
           WHERE es.greenhouse_id = c.greenhouse_id
             AND es.ts <= c.ts
             AND es.equipment IN ('fog', 'vent', 'fan1', 'fan2', 'heat1',
                                  'heat2', 'mister_south', 'mister_west',
                                  'mister_center')
           ORDER BY es.equipment, es.ts DESC
      ) e
) eq ON true
LEFT JOIN LATERAL (
    -- Latest COMPLETE dispatcher batch (temp_high marks batch completeness —
    -- export-script convention).
    SELECT ss.ts
      FROM public.setpoint_snapshot ss
     WHERE ss.greenhouse_id = c.greenhouse_id
       AND ss.parameter = 'temp_high'
       AND ss.ts <= c.ts
     ORDER BY ss.ts DESC
     LIMIT 1
) spb ON true
LEFT JOIN LATERAL (
    SELECT jsonb_object_agg(ss.parameter, to_jsonb(ss.value)) AS sp_payload
      FROM public.setpoint_snapshot ss
     WHERE ss.greenhouse_id = c.greenhouse_id
       AND ss.ts = spb.ts
) spv ON true
LEFT JOIN LATERAL (
    -- Latest device-echoed identity at or before the tick. Snapshots may
    -- predate the greenhouse_id backfill; a NULL greenhouse_id row is
    -- accepted for the tick's greenhouse (single-house deployment).
    SELECT s.snapshot_id, s.device_id, s.reported_at, s.device_generation,
           s.assignment_id, s.content_sha256, s.activation_sha256,
           s.validity, s.apply_state, s.schema_revision
      FROM public.policy_device_snapshots s
     WHERE (s.greenhouse_id = c.greenhouse_id OR s.greenhouse_id IS NULL)
       AND s.reported_at <= c.ts
     ORDER BY s.reported_at DESC
     LIMIT 1
) snap ON true
LEFT JOIN LATERAL (
    -- The admitted vector the echo confirms: exact activation-hash match
    -- first (assignment-bound identity), generation fallback for baseline/
    -- recovery echoes that carry no activation hash.
    SELECT v.vector_id, v.content_sha256, v.activation_sha256,
           v.canonical_bytes, v.status, v.validity
      FROM public.effective_policy_vectors v
     WHERE (snap.activation_sha256 IS NOT NULL
            AND v.activation_sha256 = snap.activation_sha256)
        OR (snap.activation_sha256 IS NULL
            AND snap.device_generation IS NOT NULL
            AND v.greenhouse_id = c.greenhouse_id
            AND v.device_generation = snap.device_generation)
     ORDER BY v.created_at DESC
     LIMIT 1
) vec ON true
LEFT JOIN LATERAL (
    SELECT x.exposure_id, x.identity_confirmed
      FROM public.policy_exposures x
     WHERE x.device_id = snap.device_id
       AND x.started_at <= c.ts
       AND (x.ended_at IS NULL OR x.ended_at >= c.ts)
     ORDER BY x.started_at DESC
     LIMIT 1
) expo ON true
WHERE c.duplicate_rank = 1;

COMMENT ON VIEW public.v_policy_twin_asof_input IS
    'Firmware-twin LIVE as-of input feed (#587, audit §8.9): one row per '
    'deduplicated climate tick, paired with the latest device-confirmed policy '
    'identity (policy_device_snapshots -> effective_policy_vectors) at or '
    'before the tick, plus clock validity, sensor validity/freshness, relay '
    'readback, boot/reset inference, water/dwell/fairness telemetry, and the '
    'as-of complete setpoint batch. security_barrier view; the twin role holds '
    'SELECT here and INSERT on twin_live_results ONLY. Feed gaps (no typed '
    'boot event, no per-relay last-off telemetry, no budget-remaining echo, '
    'no resident-FSM echo) are enumerated in the migration 211 header.';

-- ============================================================================
-- 2. twin_live_results — append-only per-tick live-shadow outcome
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.twin_live_results (
    ts              timestamptz NOT NULL DEFAULT now(),  -- row write time
    greenhouse_id   text NOT NULL REFERENCES public.greenhouses(id),
    tick_ts         timestamptz NOT NULL,                -- the paired telemetry tick
    twin_env        text NOT NULL CHECK (twin_env IN ('dev', 'stage', 'prod')),
    twin_ref        text NOT NULL,                       -- pinned git sha / fw_version
    twin_mode       text NOT NULL DEFAULT 'live' CHECK (twin_mode IN ('corpus', 'live')),
    -- Observed (device-confirmed) policy identity paired at the tick.
    snapshot_id     bigint,
    device_id       text,
    device_generation bigint,
    assignment_id   uuid,
    observed_content_sha256 text,
    observed_activation_sha256 text,
    apply_state     text,
    vector_id       uuid,
    vector_content_sha256 text,
    policy_hash_match boolean,
    -- Twin decision outputs (replay_emit_follow stream row).
    twin_decision_mode text,
    twin_climate_action text,
    twin_mist_stage integer,
    twin_relay_fog  boolean,
    twin_relay_vent boolean,
    twin_relay_fan1 boolean,
    twin_relay_fan2 boolean,
    twin_relay_heat1 boolean,
    twin_relay_heat2 boolean,
    twin_mode_reason text,
    twin_override_bits integer,
    -- Live action (equipment_state as-of readback at the tick).
    live_relay_fog  boolean,
    live_relay_vent boolean,
    live_relay_fan1 boolean,
    live_relay_fan2 boolean,
    live_relay_heat1 boolean,
    live_relay_heat2 boolean,
    live_relay_asof timestamptz,
    -- Agreement flags + §8.9 classification.
    action_agree    boolean,
    classification  text NOT NULL CHECK (classification IN (
                        'agreement', 'divergence', 'warm_up',
                        'unmatched_state', 'gap')),
    gap_reason      text,
    twin_metadata   jsonb,
    -- §8.9 hard invariant: agreement REQUIRES byte-identical policy identity
    -- AND relay-action equality; gaps/warm-up/unmatched can never satisfy it.
    CONSTRAINT twin_live_results_agreement_chk CHECK (
        classification <> 'agreement'
        OR (action_agree AND policy_hash_match)
    )
);

SELECT create_hypertable('public.twin_live_results', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_twin_live_results_env_tick
    ON public.twin_live_results (twin_env, tick_ts DESC);
CREATE INDEX IF NOT EXISTS idx_twin_live_results_class_tick
    ON public.twin_live_results (classification, tick_ts DESC);

COMMENT ON TABLE public.twin_live_results IS
    'Firmware-twin LIVE shadow outcomes (#587, audit §8.9): one append-only row '
    'per settled tick — paired device-confirmed policy identity, twin decision, '
    'live relay action, agreement flags, and the §8.9 classification '
    '(agreement | divergence | warm_up | unmatched_state | gap; gaps never '
    'count as agreement — CHECK-enforced). Written INSERT-only by the twin '
    'role; twin/report_agreement.py computes the 7-14 day live-shadow gate '
    'over this table.';
COMMENT ON COLUMN public.twin_live_results.classification IS
    '§8.9 tick class: agreement (byte-identical policy AND relay equality), '
    'divergence (comparable but unequal), warm_up (twin state not settled '
    'after start/boot/feed-gap reset), unmatched_state (identity mismatch: '
    'apply_state, hash, validity, or undecodable vector), gap (feed missing: '
    'sensors, clock, device echo, relay readback, or malformed decision).';
COMMENT ON COLUMN public.twin_live_results.gap_reason IS
    'Machine-readable reason for every non-agreement class (twin/live_driver.py '
    'vocabulary: sensor_missing, clock_invalid, no_device_snapshot, '
    'stale_device_snapshot, relay_readback_missing, apply_state:*, '
    'vector_unknown, content_hash_mismatch, outside_validity, '
    'vector_decode_failed, warm_up_window, boot_reset, feed_gap_reset, '
    'twin_decision_malformed).';

-- Append-only by trigger, exactly like the migration-207 experiment ledgers
-- (fn_experiment_append_only already exists there).
DROP TRIGGER IF EXISTS trg_twin_live_results_append_only
    ON public.twin_live_results;
CREATE TRIGGER trg_twin_live_results_append_only
    BEFORE UPDATE OR DELETE ON public.twin_live_results
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_append_only();

-- ============================================================================
-- 3. Twin-role grants: SELECT on the view + INSERT on results ONLY
--
-- twin_ro is the live NOLOGIN group role from migration 155 (the twin login
-- user is a member). verdify_twin_ro is the experiment-era name fixed by
-- db/roles/experiment-roles.sql (§8.7 role split scaffold; its LANE-F note
-- reserves exactly this grant). Both are shimmed idempotently and granted the
-- same narrow surface so the rollout can switch login membership without a
-- migration.
-- ============================================================================

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['twin_ro', 'verdify_twin_ro'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        END IF;
    END LOOP;
END
$$;

-- Idempotent reset: converge to the exact narrow set on re-run.
REVOKE ALL ON public.v_policy_twin_asof_input FROM twin_ro, verdify_twin_ro;
REVOKE ALL ON public.twin_live_results        FROM twin_ro, verdify_twin_ro;

GRANT SELECT ON public.v_policy_twin_asof_input TO twin_ro, verdify_twin_ro;
GRANT INSERT ON public.twin_live_results        TO twin_ro, verdify_twin_ro;

COMMENT ON ROLE verdify_twin_ro IS
    'Experiment-era twin role (§8.9, #587): SELECT on v_policy_twin_asof_input '
    'and INSERT on twin_live_results ONLY — no policy/control DML, no UPDATE/'
    'DELETE anywhere. NOLOGIN group role per db/roles/experiment-roles.sql.';
