-- Migration 143: Slack greenhouse operations foundation
--
-- Adds durable audit, role, confirmation, alert action, notification, and crop
-- task surfaces for the Slack-first greenhouse operator workflow.

BEGIN;

ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS slack_channel_id TEXT;
ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS slack_message_ts TEXT;
ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT;
ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS slack_last_posted_at TIMESTAMPTZ;
ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS slack_snoozed_until TIMESTAMPTZ;
ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS slack_snoozed_by TEXT;
ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS slack_assigned_to TEXT;

UPDATE alert_log
   SET slack_message_ts = COALESCE(slack_message_ts, slack_ts),
       slack_thread_ts = COALESCE(slack_thread_ts, slack_ts)
 WHERE slack_ts IS NOT NULL;

ALTER TABLE observations ADD COLUMN IF NOT EXISTS slack_channel_id TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS slack_message_ts TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS slack_user_id TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS slack_file_ids TEXT[];
ALTER TABLE observations ADD COLUMN IF NOT EXISTS slack_file_refs JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE crop_events ADD COLUMN IF NOT EXISTS slack_channel_id TEXT;
ALTER TABLE crop_events ADD COLUMN IF NOT EXISTS slack_message_ts TEXT;
ALTER TABLE crop_events ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT;
ALTER TABLE crop_events ADD COLUMN IF NOT EXISTS slack_user_id TEXT;

ALTER TABLE harvests ADD COLUMN IF NOT EXISTS slack_channel_id TEXT;
ALTER TABLE harvests ADD COLUMN IF NOT EXISTS slack_message_ts TEXT;
ALTER TABLE harvests ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT;
ALTER TABLE harvests ADD COLUMN IF NOT EXISTS slack_user_id TEXT;

ALTER TABLE treatments ADD COLUMN IF NOT EXISTS slack_channel_id TEXT;
ALTER TABLE treatments ADD COLUMN IF NOT EXISTS slack_message_ts TEXT;
ALTER TABLE treatments ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT;
ALTER TABLE treatments ADD COLUMN IF NOT EXISTS slack_user_id TEXT;

CREATE TABLE IF NOT EXISTS slack_user_roles (
    id              SERIAL PRIMARY KEY,
    greenhouse_id   TEXT NOT NULL DEFAULT 'vallery' REFERENCES greenhouses(id),
    slack_team_id   TEXT,
    slack_user_id   TEXT NOT NULL,
    display_name    TEXT,
    role            TEXT NOT NULL DEFAULT 'viewer',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT slack_user_roles_role_check
        CHECK (role IN ('viewer', 'operator', 'grower', 'coordinator'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_slack_user_roles_active
    ON slack_user_roles (greenhouse_id, slack_user_id)
    WHERE is_active;

DROP TRIGGER IF EXISTS trg_slack_user_roles_updated_at ON slack_user_roles;
CREATE TRIGGER trg_slack_user_roles_updated_at
    BEFORE UPDATE ON slack_user_roles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS slack_command_audit (
    id                    SERIAL PRIMARY KEY,
    ts                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    greenhouse_id          TEXT NOT NULL DEFAULT 'vallery' REFERENCES greenhouses(id),
    channel_id             TEXT,
    channel_name           TEXT,
    message_ts             TEXT,
    thread_ts              TEXT,
    slack_team_id          TEXT,
    slack_user_id          TEXT,
    slack_user_name        TEXT,
    role                   TEXT,
    command_text           TEXT NOT NULL,
    normalized_intent      TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'received',
    requires_confirmation  BOOLEAN NOT NULL DEFAULT FALSE,
    confirmation_id        UUID,
    target_type            TEXT,
    target_id              TEXT,
    record_type            TEXT,
    record_id              TEXT,
    response_text          TEXT,
    error                  TEXT,
    raw_event              JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_routing          TEXT NOT NULL DEFAULT 'deterministic',
    handled_by             TEXT NOT NULL DEFAULT 'slack_ops',
    CONSTRAINT slack_command_audit_status_check
        CHECK (status IN (
            'received', 'parsed', 'denied', 'needs_confirmation',
            'confirmed', 'executed', 'not_found', 'ambiguous', 'error',
            'unsupported', 'unsafe_blocked'
        )),
    CONSTRAINT slack_command_audit_role_check
        CHECK (role IS NULL OR role IN ('viewer', 'operator', 'grower', 'coordinator')),
    CONSTRAINT slack_command_audit_model_routing_check
        CHECK (model_routing IN ('deterministic', 'openclaw_ai', 'hybrid'))
);

CREATE INDEX IF NOT EXISTS idx_slack_command_audit_ts
    ON slack_command_audit (ts DESC);
CREATE INDEX IF NOT EXISTS idx_slack_command_audit_user
    ON slack_command_audit (greenhouse_id, slack_user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_slack_command_audit_intent
    ON slack_command_audit (normalized_intent, ts DESC);

CREATE TABLE IF NOT EXISTS slack_confirmation_requests (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at         TIMESTAMPTZ NOT NULL,
    confirmed_at       TIMESTAMPTZ,
    canceled_at        TIMESTAMPTZ,
    greenhouse_id       TEXT NOT NULL DEFAULT 'vallery' REFERENCES greenhouses(id),
    slack_team_id       TEXT,
    slack_user_id       TEXT NOT NULL,
    channel_id          TEXT,
    message_ts          TEXT,
    thread_ts           TEXT,
    normalized_intent   TEXT NOT NULL,
    target_type         TEXT,
    target_id           TEXT,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'pending',
    command_audit_id    INT REFERENCES slack_command_audit(id) ON DELETE SET NULL,
    confirmation_text   TEXT NOT NULL,
    CONSTRAINT slack_confirmation_status_check
        CHECK (status IN ('pending', 'confirmed', 'canceled', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_slack_confirmation_pending
    ON slack_confirmation_requests (greenhouse_id, slack_user_id, created_at DESC)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS slack_alert_actions (
    id              SERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    greenhouse_id   TEXT NOT NULL DEFAULT 'vallery' REFERENCES greenhouses(id),
    alert_id        INT NOT NULL REFERENCES alert_log(id) ON DELETE CASCADE,
    action          TEXT NOT NULL,
    slack_user_id   TEXT,
    slack_user_name TEXT,
    channel_id      TEXT,
    message_ts      TEXT,
    thread_ts       TEXT,
    note            TEXT,
    snoozed_until   TIMESTAMPTZ,
    assigned_to     TEXT,
    command_audit_id INT REFERENCES slack_command_audit(id) ON DELETE SET NULL,
    CONSTRAINT slack_alert_actions_action_check
        CHECK (action IN ('acknowledge', 'snooze', 'assign', 'note', 'false_positive', 'resolve'))
);

CREATE INDEX IF NOT EXISTS idx_slack_alert_actions_alert
    ON slack_alert_actions (alert_id, ts DESC);

CREATE TABLE IF NOT EXISTS slack_notification_events (
    id              SERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    greenhouse_id   TEXT NOT NULL DEFAULT 'vallery' REFERENCES greenhouses(id),
    source          TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    severity        TEXT,
    channel_id      TEXT,
    message_ts      TEXT,
    thread_ts       TEXT,
    entity_type     TEXT,
    entity_id       TEXT,
    dedupe_key      TEXT,
    status          TEXT NOT NULL DEFAULT 'posted',
    post_mode       TEXT NOT NULL DEFAULT 'immediate',
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT,
    CONSTRAINT slack_notification_events_status_check
        CHECK (status IN ('planned', 'posted', 'suppressed', 'digest', 'failed', 'deleted')),
    CONSTRAINT slack_notification_events_post_mode_check
        CHECK (post_mode IN ('immediate', 'thread', 'digest', 'suppressed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_slack_notification_dedupe
    ON slack_notification_events (greenhouse_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS crop_tasks (
    id                     SERIAL PRIMARY KEY,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    greenhouse_id           TEXT NOT NULL DEFAULT 'vallery' REFERENCES greenhouses(id),
    task_type               TEXT NOT NULL,
    priority                TEXT NOT NULL DEFAULT 'normal',
    status                  TEXT NOT NULL DEFAULT 'open',
    crop_id                 INT REFERENCES crops(id) ON DELETE CASCADE,
    position_id             INT REFERENCES positions(id) ON DELETE SET NULL,
    zone_id                 INT REFERENCES zones(id) ON DELETE SET NULL,
    due_at                  TIMESTAMPTZ NOT NULL,
    completed_at            TIMESTAMPTZ,
    completed_by            TEXT,
    source                  TEXT NOT NULL DEFAULT 'slack_ops',
    related_observation_id  INT REFERENCES observations(id) ON DELETE SET NULL,
    related_treatment_id    INT REFERENCES treatments(id) ON DELETE SET NULL,
    related_harvest_id      INT REFERENCES harvests(id) ON DELETE SET NULL,
    slack_channel_id        TEXT,
    slack_message_ts        TEXT,
    slack_thread_ts         TEXT,
    notes                   TEXT,
    CONSTRAINT crop_tasks_type_check
        CHECK (task_type IN (
            'scouting', 'treatment_followup', 'harvest_due',
            'harvest_overdue', 'stage_check', 'observation_followup'
        )),
    CONSTRAINT crop_tasks_priority_check
        CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    CONSTRAINT crop_tasks_status_check
        CHECK (status IN ('open', 'snoozed', 'completed', 'canceled'))
);

CREATE INDEX IF NOT EXISTS idx_crop_tasks_due
    ON crop_tasks (greenhouse_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_crop_tasks_crop
    ON crop_tasks (crop_id, due_at DESC);

DROP TRIGGER IF EXISTS trg_crop_tasks_updated_at ON crop_tasks;
CREATE TRIGGER trg_crop_tasks_updated_at
    BEFORE UPDATE ON crop_tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE VIEW v_slack_open_alert_threads AS
SELECT id AS alert_id,
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
SELECT ct.id,
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

COMMIT;
