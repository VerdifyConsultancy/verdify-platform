-- 143-slack-ops.sql
-- Shared Slack operations state for Iris/OpenClaw/Hermes.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE alert_log
  ADD COLUMN IF NOT EXISTS slack_channel_id text,
  ADD COLUMN IF NOT EXISTS slack_message_ts text,
  ADD COLUMN IF NOT EXISTS slack_thread_ts text,
  ADD COLUMN IF NOT EXISTS slack_last_posted_at timestamptz,
  ADD COLUMN IF NOT EXISTS slack_snoozed_until timestamptz,
  ADD COLUMN IF NOT EXISTS slack_snoozed_by text,
  ADD COLUMN IF NOT EXISTS slack_assigned_to text;

ALTER TABLE observations
  ADD COLUMN IF NOT EXISTS slack_channel_id text,
  ADD COLUMN IF NOT EXISTS slack_message_ts text,
  ADD COLUMN IF NOT EXISTS slack_thread_ts text,
  ADD COLUMN IF NOT EXISTS slack_user_id text,
  ADD COLUMN IF NOT EXISTS slack_file_ids text[],
  ADD COLUMN IF NOT EXISTS slack_file_refs jsonb;

ALTER TABLE crop_events
  ADD COLUMN IF NOT EXISTS slack_channel_id text,
  ADD COLUMN IF NOT EXISTS slack_message_ts text,
  ADD COLUMN IF NOT EXISTS slack_thread_ts text,
  ADD COLUMN IF NOT EXISTS slack_user_id text;

ALTER TABLE harvests
  ADD COLUMN IF NOT EXISTS slack_channel_id text,
  ADD COLUMN IF NOT EXISTS slack_message_ts text,
  ADD COLUMN IF NOT EXISTS slack_thread_ts text,
  ADD COLUMN IF NOT EXISTS slack_user_id text;

ALTER TABLE treatments
  ADD COLUMN IF NOT EXISTS slack_channel_id text,
  ADD COLUMN IF NOT EXISTS slack_message_ts text,
  ADD COLUMN IF NOT EXISTS slack_thread_ts text,
  ADD COLUMN IF NOT EXISTS slack_user_id text;

CREATE TABLE IF NOT EXISTS slack_user_roles (
  id serial PRIMARY KEY,
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  slack_team_id text,
  slack_user_id text NOT NULL,
  display_name text,
  role text NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer', 'grower', 'operator', 'coordinator')),
  is_active boolean NOT NULL DEFAULT true,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_slack_user_roles_active
  ON slack_user_roles (greenhouse_id, slack_user_id)
  WHERE is_active;

CREATE TABLE IF NOT EXISTS slack_command_audit (
  id serial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  channel_id text NOT NULL,
  channel_name text,
  message_ts text,
  thread_ts text,
  slack_team_id text,
  slack_user_id text NOT NULL,
  slack_user_name text,
  role text NOT NULL DEFAULT 'viewer',
  command_text text NOT NULL,
  normalized_intent text,
  status text NOT NULL DEFAULT 'received',
  requires_confirmation boolean NOT NULL DEFAULT false,
  confirmation_id uuid,
  target_type text,
  target_id text,
  record_type text,
  record_id text,
  response_text text,
  error text,
  raw_event jsonb,
  model_routing text NOT NULL DEFAULT 'deterministic',
  handled_by text NOT NULL DEFAULT 'python'
);
CREATE INDEX IF NOT EXISTS idx_slack_command_audit_ts ON slack_command_audit (ts DESC);
CREATE INDEX IF NOT EXISTS idx_slack_command_audit_user ON slack_command_audit (greenhouse_id, slack_user_id, ts DESC);

CREATE TABLE IF NOT EXISTS slack_confirmation_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  confirmed_at timestamptz,
  canceled_at timestamptz,
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  slack_team_id text,
  slack_user_id text NOT NULL,
  channel_id text NOT NULL,
  message_ts text,
  thread_ts text,
  normalized_intent text NOT NULL,
  target_type text,
  target_id text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending',
  command_audit_id integer REFERENCES slack_command_audit(id) ON DELETE SET NULL,
  confirmation_text text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_slack_confirm_pending
  ON slack_confirmation_requests (greenhouse_id, slack_user_id, expires_at)
  WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS slack_alert_actions (
  id serial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  alert_id integer NOT NULL REFERENCES alert_log(id) ON DELETE CASCADE,
  action text NOT NULL,
  slack_user_id text NOT NULL,
  slack_user_name text,
  channel_id text NOT NULL,
  message_ts text,
  thread_ts text,
  note text,
  snoozed_until timestamptz,
  assigned_to text,
  command_audit_id integer REFERENCES slack_command_audit(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_slack_alert_actions_alert ON slack_alert_actions (alert_id, ts DESC);

CREATE TABLE IF NOT EXISTS slack_notification_events (
  id serial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  source text NOT NULL,
  event_type text NOT NULL,
  severity text,
  channel_id text NOT NULL,
  message_ts text,
  thread_ts text,
  entity_type text,
  entity_id text,
  dedupe_key text,
  status text NOT NULL DEFAULT 'posted' CHECK (status IN ('planned', 'posted', 'suppressed', 'digest', 'failed', 'deleted')),
  post_mode text NOT NULL DEFAULT 'immediate' CHECK (post_mode IN ('immediate', 'thread', 'digest', 'suppressed')),
  payload jsonb,
  error text
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_slack_notification_dedupe
  ON slack_notification_events (greenhouse_id, dedupe_key)
  WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS crop_tasks (
  id serial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  task_type text NOT NULL,
  priority text NOT NULL DEFAULT 'normal',
  status text NOT NULL DEFAULT 'open',
  crop_id integer REFERENCES crops(id) ON DELETE SET NULL,
  position_id integer REFERENCES positions(id) ON DELETE SET NULL,
  zone_id integer REFERENCES zones(id) ON DELETE SET NULL,
  due_at timestamptz,
  completed_at timestamptz,
  completed_by text,
  source text NOT NULL DEFAULT 'system',
  related_observation_id integer REFERENCES observations(id) ON DELETE SET NULL,
  related_treatment_id integer REFERENCES treatments(id) ON DELETE SET NULL,
  related_harvest_id integer REFERENCES harvests(id) ON DELETE SET NULL,
  slack_channel_id text,
  slack_message_ts text,
  slack_thread_ts text,
  notes text
);
CREATE INDEX IF NOT EXISTS idx_crop_tasks_due ON crop_tasks (greenhouse_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_crop_tasks_crop ON crop_tasks (crop_id, status);

CREATE OR REPLACE VIEW v_slack_open_alert_threads AS
SELECT
  id AS alert_id,
  greenhouse_id,
  alert_type,
  severity,
  category,
  sensor_id,
  zone,
  zone_id,
  message,
  disposition,
  ts,
  acknowledged_at,
  acknowledged_by,
  slack_channel_id,
  COALESCE(slack_thread_ts, slack_ts) AS slack_thread_ts,
  COALESCE(slack_message_ts, slack_ts) AS slack_message_ts,
  slack_snoozed_until,
  slack_assigned_to
FROM alert_log
WHERE resolved_at IS NULL
  AND disposition IN ('open', 'acknowledged');

CREATE OR REPLACE VIEW v_slack_crop_tasks_due AS
SELECT
  ct.id,
  ct.greenhouse_id,
  ct.task_type,
  ct.priority,
  ct.status,
  ct.due_at,
  ct.crop_id,
  c.name AS crop_name,
  c.variety AS crop_variety,
  c.stage AS crop_stage,
  ct.position_id,
  p.label AS position_label,
  z.slug AS zone_slug,
  ct.notes
FROM crop_tasks ct
LEFT JOIN crops c ON c.id = ct.crop_id
LEFT JOIN positions p ON p.id = ct.position_id
LEFT JOIN zones z ON z.id = COALESCE(ct.zone_id, c.zone_id)
WHERE ct.status IN ('open', 'snoozed')
  AND ct.due_at <= now() + interval '24 hours'
ORDER BY ct.due_at, ct.priority DESC, ct.id;
