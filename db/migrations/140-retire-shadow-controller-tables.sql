-- Retire the old planner/Hermes shadow-mode schema.
--
-- ClimateIntent now has one production controller path:
-- planner emits climate_intent -> MCP materializes Tier 1 -> dispatcher -> ESP32.
-- Historical shadow evidence remains in migration/git history; these tables are
-- no longer part of the live schema or fresh-db baseline.

BEGIN;

DROP TABLE IF EXISTS plan_delivery_log_shadow CASCADE;
DROP TABLE IF EXISTS setpoint_plan_shadow CASCADE;
DROP TABLE IF EXISTS plan_journal_shadow CASCADE;

COMMIT;
