-- 155-twin-observability-tables.sql
-- =============================================================================
-- TWIN-6 (issue #33): firmware digital-twin observability schema.
--
-- Design: docs/design/firmware-digital-twin.md §5.2 (observability schema) and
-- §5.3 (the read-only / no-actuation safety model, layer L2). The MVP and all
-- comparison streams write to these two tables; nothing else in the repo owns
-- them. Serialized shared-territory migration (db/migrations) — coordinator
-- validated.
--
-- Adds:
--   1. twin_decisions          — hypertable, 1:1 with replay_emit's TSV row
--                                (+ climate_action via describe_effective_
--                                climate_decision(), + twin_metadata jsonb).
--   2. firmware_twin_divergence — windowed disagreement summary (counts/pcts,
--                                per-relay breakdown, by_mode/by_daypart/
--                                by_outdoor_band jsonb conditioning, worst
--                                examples).
--   3. twin_ro                  — group role (NOLOGIN). SELECT on the telemetry
--                                tables the twin reads; INSERT on ONLY the two
--                                tables above; NO UPDATE/DELETE anywhere and NO
--                                write on any control-plane table (L2 of the
--                                four independent read-only guarantees).
--
-- ADDITIVE ONLY. Net-new tables + role; no DROP/ALTER of live data. On live,
-- to_regclass('public.twin_decisions') and ('public.firmware_twin_divergence')
-- are NULL, and twin_ro does not exist.
--
-- #23 rollback-replay safety: this migration is NON-self-transactional — it has
-- NO top-level BEGIN;/COMMIT; and NO commit-forcing statement (no CREATE INDEX
-- CONCURRENTLY; create_hypertable runs fine inside psql's implicit autocommit).
-- It is therefore SAFE to wrap in an outer `BEGIN; ... ROLLBACK;` for
-- rollback-validation: there is no inner COMMIT to defeat the rollback. Every
-- statement is idempotent (IF NOT EXISTS / if_not_exists => TRUE / a
-- role-exists shim / REVOKE-then-GRANT), so it is also safe to re-run.
--
-- ROLLBACK (documented; for the disposable rollback fixture, never live):
--   REVOKE ALL ON public.twin_decisions, public.firmware_twin_divergence FROM twin_ro;
--   REVOKE ALL ON public.climate, public.equipment_state, public.system_state,
--          public.setpoint_snapshot, public.setpoint_changes,
--          public.climate_action_log FROM twin_ro;
--   DROP TABLE IF EXISTS public.firmware_twin_divergence;
--   DROP TABLE IF EXISTS public.twin_decisions;
--   DROP ROLE IF EXISTS twin_ro;
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────
-- 1. twin_decisions: per-tick twin FSM output (1:1 with replay_emit TSV)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.twin_decisions (
    ts             timestamptz NOT NULL,
    twin_env       text NOT NULL,            -- 'dev' | 'stage' | 'prod'
    twin_ref       text NOT NULL,            -- git sha / fw_version the image was pinned to
    input_ts       timestamptz NOT NULL,     -- the climate row that drove this decision
    mode           text NOT NULL,
    climate_action text,                     -- via describe_effective_climate_decision (§2.4)
    mist_stage     integer NOT NULL,
    relay_fog      boolean NOT NULL,
    relay_vent     boolean NOT NULL,
    relay_fan1     boolean NOT NULL,
    relay_fan2     boolean NOT NULL,
    relay_heat1    boolean NOT NULL,
    relay_heat2    boolean NOT NULL,
    mode_reason    text,
    override_bits  integer NOT NULL,
    twin_metadata  jsonb,                     -- vpd_zone_inputs='homogenized', warmup flags, etc.
    greenhouse_id  text DEFAULT 'vallery' REFERENCES public.greenhouses(id),
    CONSTRAINT twin_decisions_env_chk CHECK (twin_env IN ('dev', 'stage', 'prod'))
);

SELECT create_hypertable('public.twin_decisions', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_twin_decisions_env_ts
    ON public.twin_decisions (twin_env, ts DESC);
CREATE INDEX IF NOT EXISTS idx_twin_decisions_input_ts
    ON public.twin_decisions (twin_env, input_ts DESC);

COMMENT ON TABLE public.twin_decisions IS
    'Firmware digital-twin per-tick FSM output (TWIN-6). 1:1 map of replay_emit''s TSV row plus climate_action and twin_metadata, so the live driver does no semantic translation. One row per twin (dev/stage/prod) per settled input tick. Written by the twin via the read-only twin_ro role (INSERT-only). Design: firmware-digital-twin.md §5.2.';
COMMENT ON COLUMN public.twin_decisions.twin_env IS
    'Which twin produced the row: dev (CI candidate), stage (bake candidate), prod (last-good shadow).';
COMMENT ON COLUMN public.twin_decisions.twin_ref IS
    'git sha / fw_version the twin image was pinned to; lets the bake/agreement gates confirm the twin ran the intended SHA.';
COMMENT ON COLUMN public.twin_decisions.input_ts IS
    'The climate telemetry row timestamp that drove this decision (the twin lags live by the settling window).';
COMMENT ON COLUMN public.twin_decisions.climate_action IS
    'Effective climate action via describe_effective_climate_decision() (greenhouse_logic.h); not a separate translation table.';
COMMENT ON COLUMN public.twin_decisions.twin_metadata IS
    'Free-form per-row context: vpd_zone_inputs, warm-up flags, econ_block reconstruction notes, etc.';

-- ─────────────────────────────────────────────────────────────────────
-- 2. firmware_twin_divergence: windowed disagreement summary
-- ─────────────────────────────────────────────────────────────────────
-- One row per (comparison, window). comparison is e.g. 'stage_vs_prod' (bake
-- agreement gate) or 'prod_vs_reality' (deployed-firmware drift). Holds the
-- rolling relay-disagreement counts/pcts, the per-relay breakdown, the
-- by_mode/by_daypart/by_outdoor_band jsonb conditioning, and worst_examples.
CREATE TABLE IF NOT EXISTS public.firmware_twin_divergence (
    ts                timestamptz NOT NULL,        -- when the summary was computed
    comparison        text NOT NULL,               -- 'stage_vs_prod' | 'prod_vs_reality' | 'dev_vs_corpus' ...
    window_start      timestamptz NOT NULL,
    window_end        timestamptz NOT NULL,
    ref_twin_ref      text,                         -- the reference side SHA (e.g. prod last-good)
    cmp_twin_ref      text,                         -- the candidate side SHA (e.g. stage candidate)
    samples           integer NOT NULL,
    disagree_count    integer NOT NULL,
    relay_disagree_pct double precision NOT NULL,
    mode_disagree_pct  double precision,
    per_relay         jsonb DEFAULT '{}'::jsonb NOT NULL,  -- {"fog": 0.0, "vent": 1.2, "fan1": 0.0, ...}
    by_mode           jsonb DEFAULT '{}'::jsonb NOT NULL,
    by_daypart        jsonb DEFAULT '{}'::jsonb NOT NULL,
    by_outdoor_band   jsonb DEFAULT '{}'::jsonb NOT NULL,
    worst_examples    jsonb DEFAULT '[]'::jsonb NOT NULL,
    greenhouse_id     text DEFAULT 'vallery' REFERENCES public.greenhouses(id),
    CONSTRAINT firmware_twin_divergence_window_chk CHECK (window_end >= window_start),
    CONSTRAINT firmware_twin_divergence_pct_chk CHECK (
        relay_disagree_pct >= 0 AND relay_disagree_pct <= 100
    )
);

SELECT create_hypertable('public.firmware_twin_divergence', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_firmware_twin_divergence_cmp_ts
    ON public.firmware_twin_divergence (comparison, ts DESC);

COMMENT ON TABLE public.firmware_twin_divergence IS
    'Windowed firmware-twin disagreement summary (TWIN-6). One row per (comparison, window): rolling relay-disagreement counts/pcts, per-relay breakdown, by_mode/by_daypart/by_outdoor_band jsonb conditioning, and worst_examples. Feeds the live agreement gate (rule 8), the 48h bake clock (rule 3), and the alert_monitor rows that plug into firmware-deploy-preflight (rule 1). Design: firmware-digital-twin.md §5.2/§5.3. Written by twin_ro (INSERT-only).';
COMMENT ON COLUMN public.firmware_twin_divergence.comparison IS
    'stage_vs_prod (bake agreement) | prod_vs_reality (deployed drift) | dev_vs_corpus etc.';
COMMENT ON COLUMN public.firmware_twin_divergence.relay_disagree_pct IS
    'Percent of samples in the window where any relay output differed between the two sides (0..100); the gated quantity for THRESHOLD_PCT.';
COMMENT ON COLUMN public.firmware_twin_divergence.per_relay IS
    'Per-relay disagreement pct: {"fog":0.0,"vent":1.2,"fan1":0.0,"fan2":0.0,"heat1":0.0,"heat2":0.0}.';
COMMENT ON COLUMN public.firmware_twin_divergence.worst_examples IS
    'Array of the highest-divergence input rows for triage: [{"input_ts":..., "ref":..., "cmp":..., "relays":...}].';

-- ─────────────────────────────────────────────────────────────────────
-- 3. twin_ro: read-only twin role (safety layer L2)
-- ─────────────────────────────────────────────────────────────────────
-- Design §5.3 L2: the twin holds a DB role that can ONLY read the telemetry it
-- needs and INSERT into the two observability tables above. NO UPDATE, NO
-- DELETE, and NO write on any control-plane table. The twin container's
-- entrypoint additionally asserts at startup that this role cannot write a
-- control table. PostgreSQL has no `CREATE ROLE IF NOT EXISTS`, so use the
-- standard idempotent DO-block shim. NOLOGIN group role: the per-twin login
-- user (created out-of-band with a secret password) is GRANTed this role; we
-- never put a credential in a migration.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_ro') THEN
        CREATE ROLE twin_ro NOLOGIN;
    END IF;
END
$$;

-- Idempotent reset: revoke everything we manage, then re-grant the exact narrow
-- set. REVOKE-then-GRANT makes re-applying the migration converge to the same
-- privilege set even if a prior run or manual change drifted it.
REVOKE ALL ON public.twin_decisions FROM twin_ro;
REVOKE ALL ON public.firmware_twin_divergence FROM twin_ro;
REVOKE ALL ON public.climate FROM twin_ro;
REVOKE ALL ON public.equipment_state FROM twin_ro;
REVOKE ALL ON public.system_state FROM twin_ro;
REVOKE ALL ON public.setpoint_snapshot FROM twin_ro;
REVOKE ALL ON public.setpoint_changes FROM twin_ro;
REVOKE ALL ON public.climate_action_log FROM twin_ro;

-- READ: the telemetry the twin feeds on (design §5.3 L2 explicit list).
GRANT SELECT ON public.climate            TO twin_ro;
GRANT SELECT ON public.equipment_state    TO twin_ro;
GRANT SELECT ON public.system_state       TO twin_ro;
GRANT SELECT ON public.setpoint_snapshot  TO twin_ro;
GRANT SELECT ON public.setpoint_changes   TO twin_ro;
GRANT SELECT ON public.climate_action_log TO twin_ro;

-- WRITE: INSERT ONLY, and ONLY on the two observability tables. No UPDATE,
-- no DELETE, no TRUNCATE — append-only by privilege.
GRANT INSERT ON public.twin_decisions           TO twin_ro;
GRANT INSERT ON public.firmware_twin_divergence  TO twin_ro;

COMMENT ON ROLE twin_ro IS
    'Firmware digital-twin read-only role (TWIN-6, safety layer L2). SELECT on climate/equipment_state/system_state/setpoint_snapshot/setpoint_changes/climate_action_log; INSERT ONLY on twin_decisions/firmware_twin_divergence; NO UPDATE/DELETE and NO write on any control-plane table. NOLOGIN group role; per-twin login users are GRANTed this role. Design: firmware-digital-twin.md §5.3.';
