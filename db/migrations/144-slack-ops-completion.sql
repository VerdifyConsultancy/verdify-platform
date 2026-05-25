-- 144-slack-ops-completion.sql
-- Completion surfaces for the Slack greenhouse operations PRD.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE slack_command_audit
  DROP CONSTRAINT IF EXISTS slack_command_audit_status_check;
ALTER TABLE slack_command_audit
  ADD CONSTRAINT slack_command_audit_status_check
  CHECK (status IN (
    'received', 'parsed', 'needs_confirmation', 'executed', 'denied',
    'failed', 'canceled', 'expired', 'confirmed', 'not_found',
    'ambiguous', 'error', 'unsupported', 'unsafe_blocked'
  ));

ALTER TABLE slack_command_audit
  DROP CONSTRAINT IF EXISTS slack_command_audit_model_routing_check;
ALTER TABLE slack_command_audit
  ADD CONSTRAINT slack_command_audit_model_routing_check
  CHECK (model_routing IN ('deterministic', 'openclaw', 'hermes', 'openclaw_ai', 'hybrid'));

CREATE TABLE IF NOT EXISTS slack_alert_runbooks (
  id serial PRIMARY KEY,
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  alert_type text NOT NULL,
  severity text,
  title text NOT NULL,
  summary text NOT NULL,
  runbook_url text,
  steps text[] NOT NULL DEFAULT ARRAY[]::text[],
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_slack_alert_runbooks_active
  ON slack_alert_runbooks (greenhouse_id, lower(alert_type), COALESCE(severity, ''))
  WHERE is_active;

CREATE TABLE IF NOT EXISTS slack_ai_work_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  work_type text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'canceled')),
  model_routing text NOT NULL DEFAULT 'openclaw' CHECK (model_routing IN ('openclaw', 'hermes')),
  requested_by text,
  channel_id text,
  message_ts text,
  thread_ts text,
  related_record_type text,
  related_record_id text,
  input_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text
);
CREATE INDEX IF NOT EXISTS idx_slack_ai_work_items_status
  ON slack_ai_work_items (greenhouse_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_slack_ai_work_items_related
  ON slack_ai_work_items (related_record_type, related_record_id);

INSERT INTO slack_alert_runbooks (alert_type, severity, title, summary, runbook_url, steps)
VALUES
  (
    'sensor_offline', NULL, 'Sensor offline',
    'Confirm whether this is an expected sensor gap, power issue, network issue, or failed probe.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check the sensor id and last-seen time in the alert details.',
      'Compare nearby redundant sensors before making control assumptions.',
      'If the sensor is control-critical, inspect power/network and avoid firmware deploys until healthy.',
      'Resolve the alert only after fresh readings resume.'
    ]
  ),
  (
    'soil_sensor_offline', NULL, 'Soil sensor offline',
    'Treat stale soil data as advisory loss; do not infer moisture from a stale probe.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check probe power and cable seating.',
      'Compare against recent irrigation and crop condition notes.',
      'Use manual inspection before changing irrigation.',
      'Resolve after fresh soil telemetry is present.'
    ]
  ),
  (
    'relay_stuck', NULL, 'Relay stuck or commanded unexpectedly',
    'Determine whether the relay state is demanded by the current mode or stuck outside policy.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check current greenhouse mode, active band, and relay command history.',
      'Physically verify the controlled equipment if the alert is critical.',
      'Do not force relay state from Slack; use planner/dispatcher paths or manual operator intervention.',
      'Resolve after command state and physical state agree.'
    ]
  ),
  (
    'temp_safety', 'critical', 'Temperature safety',
    'Temperature is outside the safe operational envelope for the greenhouse.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check current temperature, outdoor forecast, and equipment state immediately.',
      'Verify heaters, vents, fans, and power before changing setpoints.',
      'Keep firmware OTA blocked while the critical alert is open.',
      'Record the corrective action before resolving.'
    ]
  ),
  (
    'vpd_extreme', 'critical', 'Extreme VPD',
    'VPD is outside the safe range and may require climate intervention.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check current VPD, humidity, temperature, and mister/fog/fan state.',
      'Review whether the active band or a guardrail is holding a safer value.',
      'Use bounded planner/dispatcher changes, not direct relay control.',
      'Record crop impact notes if stress is visible.'
    ]
  ),
  (
    'leak_detected', 'critical', 'Leak detected',
    'A leak signal requires physical inspection before normal automation assumptions continue.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Inspect the leak location and stop water manually if needed.',
      'Check pumps, irrigation lines, and Shelly state.',
      'Do not clear until the floor/bench is dry and the signal is normal.',
      'Record repair notes on the alert resolution.'
    ]
  ),
  (
    'forecast_deviation', NULL, 'Forecast deviation',
    'Actual weather or greenhouse conditions have diverged from the forecast enough to affect planning.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Run forecast triage to see parameters, deltas, thresholds, and recent forecast actions.',
      'Check whether Iris already received a FORECAST_DEVIATION trigger.',
      'If no plan follows, trigger planner from Slack through the confirmation path.',
      'Resolve after the planner has adapted or the deviation has cleared.'
    ]
  ),
  (
    'setpoint_unconfirmed', NULL, 'Setpoint unconfirmed',
    'A requested setpoint was not confirmed by readback in the expected window.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check dispatcher logs and cfg_* readback for the parameter.',
      'Verify whether a newer setpoint superseded the pending row.',
      'Avoid repeated pushes until the cause is known.',
      'Resolve after readback matches or the row is marked superseded/expired.'
    ]
  ),
  (
    'planner_gateway_delivery_failed', NULL, 'Planner gateway delivery failed',
    'Hermes/OpenClaw did not deliver an expected planning message cleanly.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check OpenClaw gateway status and Hermes container status.',
      'Inspect plan_delivery_log and planner_trigger_ledger for the failed trigger.',
      'Restart only the failed gateway/service if health checks agree.',
      'Resolve after a follow-up plan lands or the trigger is canceled.'
    ]
  ),
  (
    'planner_required_plan_missed', NULL, 'Required plan missed',
    'A required sunrise/sunset/midnight plan did not land inside its SLA.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check planner_trigger_ledger for the missed expected trigger.',
      'Check OpenClaw/Hermes auth and model status.',
      'Trigger planner manually if current conditions need a fresh plan.',
      'Resolve after a valid plan_journal row lands.'
    ]
  ),
  (
    'planner_trigger_sla_timeout', NULL, 'Planner trigger SLA timeout',
    'A planner trigger stayed pending past its expected SLA.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check trigger id, event type, and instance in planner_trigger_ledger.',
      'Check OpenClaw/Hermes runtime status and delivery logs.',
      'Retry through trigger planner if still operationally relevant.',
      'Resolve after delivery or mark canceled with a reason.'
    ]
  ),
  (
    'tunable_zero_variance', NULL, 'Tunable zero variance',
    'A tunable stayed pinned unexpectedly and may indicate stale band/profile/dispatcher inputs.',
    'https://lab.verdify.ai/greenhouse/',
    ARRAY[
      'Check whether the parameter should be band-owned or intentionally fixed.',
      'Inspect crop profile, band function, and recent setpoint_changes rows.',
      'Do not change firmware for this alone; correct the source profile or planner path.',
      'Resolve after variance resumes or intentional pinning is documented.'
    ]
  )
ON CONFLICT DO NOTHING;

CREATE OR REPLACE VIEW v_slack_public_ops_log AS
SELECT
  id::text AS id,
  ts,
  greenhouse_id,
  'command'::text AS event_kind,
  normalized_intent AS event_type,
  status,
  role,
  record_type,
  record_id,
  model_routing,
  handled_by,
  left(regexp_replace(COALESCE(response_text, command_text), '<@[A-Z0-9]+>', '@user', 'g'), 320) AS public_text
FROM slack_command_audit
UNION ALL
SELECT
  id::text AS id,
  ts,
  greenhouse_id,
  'notification'::text AS event_kind,
  event_type,
  status,
  NULL::text AS role,
  entity_type AS record_type,
  entity_id AS record_id,
  'deterministic'::text AS model_routing,
  source AS handled_by,
  left(COALESCE(payload->>'text', event_type), 320) AS public_text
FROM slack_notification_events
UNION ALL
SELECT
  id::text AS id,
  ts,
  greenhouse_id,
  'alert_action'::text AS event_kind,
  action AS event_type,
  'executed'::text AS status,
  NULL::text AS role,
  'alert_log'::text AS record_type,
  alert_id::text AS record_id,
  'deterministic'::text AS model_routing,
  'python'::text AS handled_by,
  left(COALESCE(note, 'alert action recorded'), 320) AS public_text
FROM slack_alert_actions;

CREATE OR REPLACE VIEW v_slack_forecast_triage AS
WITH items AS (
  SELECT
    ts,
    greenhouse_id,
    'forecast_deviation'::text AS item_type,
    parameter AS target,
    CASE WHEN triggered THEN 'triggered' ELSE 'observed' END AS status,
    abs(delta) AS urgency,
    jsonb_build_object(
      'observed', observed,
      'forecasted', forecasted,
      'delta', delta,
      'threshold', threshold,
      'triggered', triggered
    ) AS details
  FROM forecast_deviation_log
  WHERE ts >= now() - interval '72 hours'
  UNION ALL
  SELECT
    COALESCE(triggered_at, outcome_evaluated_at, now()) AS ts,
    greenhouse_id,
    'forecast_action'::text AS item_type,
    COALESCE(param, rule_name) AS target,
    COALESCE(outcome, action_taken) AS status,
    CASE WHEN action_taken <> 'evaluated_ok' THEN 1 ELSE 0 END::double precision AS urgency,
    jsonb_build_object(
      'rule_name', rule_name,
      'action_taken', action_taken,
      'plan_id', plan_id,
      'old_value', old_value,
      'new_value', new_value,
      'condition', forecast_condition,
      'outcome_metrics', outcome_metrics
    ) AS details
  FROM forecast_action_log
  WHERE COALESCE(triggered_at, outcome_evaluated_at, now()) >= now() - interval '72 hours'
)
SELECT row_number() OVER (ORDER BY urgency DESC NULLS LAST, ts DESC) AS id, *
FROM items
ORDER BY urgency DESC NULLS LAST, ts DESC;

CREATE OR REPLACE VIEW v_slack_guardrail_summary AS
SELECT
  pj.plan_id,
  pj.created_at,
  pj.planner_instance,
  COALESCE(gs.guardrail_events, 0) AS guardrail_events,
  COALESCE(gs.held_guardrail_events, 0) AS held_guardrail_events,
  COALESCE(gs.dispatched_guardrail_events, 0) AS dispatched_guardrail_events,
  COALESCE(gs.vpd_high_guardrail_events, 0) AS vpd_high_guardrail_events,
  COALESCE(gs.guardrail_penalty, 0) AS guardrail_penalty
FROM plan_journal pj
LEFT JOIN v_plan_guardrail_scorecard gs ON gs.plan_id = pj.plan_id
ORDER BY pj.created_at DESC;
