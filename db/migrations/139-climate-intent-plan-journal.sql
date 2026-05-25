-- Store the semantic ClimateIntent surface emitted by planner set_plan calls.
-- The dispatcher still consumes materialized Tier 1 setpoint_plan rows; this
-- JSONB audit column preserves the compact AI-facing contract that produced
-- those rows.

ALTER TABLE plan_journal
  ADD COLUMN IF NOT EXISTS climate_intents jsonb,
  ADD COLUMN IF NOT EXISTS climate_intent_version text;

COMMENT ON COLUMN plan_journal.climate_intents IS
  'Validated ClimateIntent transition payloads emitted by set_plan, including materialized Tier 1 params. NULL for legacy plans.';

COMMENT ON COLUMN plan_journal.climate_intent_version IS
  'ClimateIntent contract version used by MCP materialization.';

CREATE INDEX IF NOT EXISTS idx_plan_journal_climate_intents
  ON plan_journal USING gin (climate_intents);
