-- 163-setpoint-zone-audit-columns.sql
-- =============================================================================
-- Firmware v2 (#324 / contract B7): per-zone setpoint audit columns.
--
-- Design: docs/design/firmware-v2-contract-2026-06-10.md §B7 — "Dispatcher:
-- computes ephemeris (astral) for audit + emits per-zone bands into
-- setpoint_snapshot (new zone, band_role, target_value columns) every cycle
-- FOR SCORING; pushes anchor tunables only on change." This migration adds
-- the columns only; the dispatcher emission work is a separate consumer
-- change (schema-first ordering, CLAUDE.md discipline 1).
--
-- Live table shapes (inspected on dev — a nightly prod replica — 2026-06-10):
--   setpoint_snapshot:  (ts, parameter, value, greenhouse_id) — TimescaleDB
--     hypertable (13 chunks), compression ENABLED since migration 149
--     (segmentby parameter, orderby ts DESC).
--   setpoint_changes:   (ts, parameter, value, source, greenhouse_id,
--     confirmed_at, planner_instance, trigger_id, delivery_status,
--     expired_at, superseded_by_ts) — hypertable (45 chunks). Gains the same
--     `zone` attribution column: anchor-tunable pushes are per (crop, zone),
--     and the FW-4 confirmation join (setpoint_changes -> setpoint_snapshot)
--     should carry zone lineage end-to-end.
--
-- Compressed-hypertable note: every ADD COLUMN below is nullable with NO
-- default — metadata-only, which TimescaleDB permits on hypertables with
-- compressed chunks (no chunk rewrite). Statements are kept single-action on
-- purpose. No new index: the columns are sparse until the dispatcher emission
-- lands; scoring access paths stay (parameter, ts) via existing indexes, and
-- an index can be added with the consumer once the query shape is real.
--
-- verdify_schemas drift guards: test_schema_fields_subset_of_db_columns is
-- one-way (model fields must exist in the DB; extra DB columns are fine), and
-- zone/band_role/target_value are not tenancy/linkage-critical — so no schema
-- model change is forced by this migration. The SetpointSnapshot /
-- SetpointChange models gain these fields with the dispatcher consumer PR.
--
-- #23 rollback-replay safety: NON-self-transactional — no top-level
-- BEGIN;/COMMIT; and no commit-forcing statement (plain ALTER TABLE ... ADD
-- COLUMN is transactional). SAFE to wrap in an outer `BEGIN; ... ROLLBACK;`
-- for rollback validation. Idempotent via IF NOT EXISTS.
--
-- RESTARTS (CLAUDE.md rule 7): does not touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py — no restart obligation. Existing
-- INSERTs name their columns explicitly, so writers are unaffected.
--
-- ROLLBACK (documented; for a disposable validation DB, never live):
--   ALTER TABLE public.setpoint_snapshot DROP COLUMN IF EXISTS target_value;
--   ALTER TABLE public.setpoint_snapshot DROP COLUMN IF EXISTS band_role;
--   ALTER TABLE public.setpoint_snapshot DROP COLUMN IF EXISTS zone;
--   ALTER TABLE public.setpoint_changes  DROP COLUMN IF EXISTS zone;
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────
-- 163.1  setpoint_snapshot: zone / band_role / target_value
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE public.setpoint_snapshot ADD COLUMN IF NOT EXISTS zone text;
ALTER TABLE public.setpoint_snapshot ADD COLUMN IF NOT EXISTS band_role text;
ALTER TABLE public.setpoint_snapshot ADD COLUMN IF NOT EXISTS target_value double precision;

COMMENT ON COLUMN public.setpoint_snapshot.zone IS
'Zone attribution for per-zone band audit rows (contract B7): center|south|west|east, '
'or house for the single-air-mass thermal band. NULL on plain cfg_* readback rows.';
COMMENT ON COLUMN public.setpoint_snapshot.band_role IS
'Role of the emitted band row within its series: target|low|high (low/high '
'reconstructed as target -/+ the crop_band_anchors half-widths). NULL on plain '
'cfg_* readback rows.';
COMMENT ON COLUMN public.setpoint_snapshot.target_value IS
'The deterministic crop+solar curve value (fn_crop_band_value) at emission time, '
'recorded alongside the served value for compliance scoring. NULL on plain cfg_* '
'readback rows.';

-- ─────────────────────────────────────────────────────────────────────
-- 163.2  setpoint_changes: zone
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE public.setpoint_changes ADD COLUMN IF NOT EXISTS zone text;

COMMENT ON COLUMN public.setpoint_changes.zone IS
'Zone attribution for per-zone anchor-tunable pushes (contract B7): '
'center|south|west|east, or house. NULL on non-zone-scoped pushes.';

-- ─────────────────────────────────────────────────────────────────────
-- 163.3  Assertion: all four columns present with the expected types.
-- ─────────────────────────────────────────────────────────────────────
DO $assert$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'setpoint_snapshot'
       AND ((column_name = 'zone'         AND data_type = 'text') OR
            (column_name = 'band_role'    AND data_type = 'text') OR
            (column_name = 'target_value' AND data_type = 'double precision'));
    IF n <> 3 THEN
        RAISE EXCEPTION 'migration 163: setpoint_snapshot audit columns incomplete (found %/3)', n;
    END IF;

    SELECT count(*) INTO n
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'setpoint_changes'
       AND column_name = 'zone' AND data_type = 'text';
    IF n <> 1 THEN
        RAISE EXCEPTION 'migration 163: setpoint_changes.zone column missing';
    END IF;
    RAISE NOTICE 'migration 163 OK: setpoint_snapshot(zone, band_role, target_value) + setpoint_changes(zone) present.';
END
$assert$;
