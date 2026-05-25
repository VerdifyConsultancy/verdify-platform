# Slack Greenhouse Operations PRD

Date: 2026-05-25
Status: proposal
Primary channel: `#greenhouse`
Primary identity: Iris, the greenhouse agent

## 1. Executive Summary

Verdify already has the core data and control model needed for a Slack-first
greenhouse operations console:

- ESP32 firmware owns deterministic relay control and safety behavior.
- The ingestor synchronizes telemetry, dispatches setpoints, checks alerts, and
  runs recurring operational tasks.
- Iris planner creates bounded tactical plans through Hermes, using MCP and
  historical context.
- The API and website expose crop, topology, equipment, observations, public
  proof, and live operations views.
- Slack already receives alerts, planner delivery notices, forecast action
  messages, checklists, and operator-facing summaries.

The missing product layer is a unified Slack operations workflow: alerts,
planner status, crop inventory, crop health observations, treatment follow-up,
and operator acknowledgements should all be addressable from `#greenhouse`
without bypassing the existing safety and audit boundaries.

This PRD proposes Slack as an operator console layered on top of the existing
Verdify APIs, MCP tools, and ingestor tasks. Slack should not directly control
relays. It should expose safe read operations, structured operator inputs, and
bounded write actions that flow through existing contracts.

## 2. Evidence Reviewed

Repo paths reviewed for this proposal:

- `README.md`
- `SLACK.md`
- `slack.yaml`
- `slack_config.py`
- `ingestor/config.py`
- `ingestor/ingestor.py`
- `ingestor/tasks.py`
- `ingestor/iris_planner.py`
- `scripts/alert-monitor.py`
- `scripts/forecast-action-engine.py`
- `scripts/checklist-to-slack.sh`
- `scripts/slack-channel-archive.py`
- `scripts/slack-post.py`
- `mcp/server.py`
- `api/main.py`
- `firmware/DESIGN.md`
- `docs/site-content-map.md`
- `docs/greenhouse-control-loop-audit-2026-05-16.md`
- `docs/SYSTEM-ARCHITECTURE.md`

## 3. Current State

### 3.1 System Roles

| Component | Current responsibility | Slack relevance |
| --- | --- | --- |
| Firmware | Local relay decisions, safety preemption, dwell, readbacks, counters, mode reason | Produces the operational facts that should be summarized and alerted |
| Ingestor | Telemetry sync, alert monitor, dispatcher, readback confirmation, planner heartbeat, forecast actions, daily summaries | Main producer of scheduled Slack notifications |
| Iris planner | Creates tactical greenhouse plans through Hermes and MCP | Should explain plan intent, plan delivery status, deviations, and lessons |
| Hermes | Runtime for Iris planning service and MCP integration | Should use shared Slack config for outbound notifications, but should not be the Slack listener |
| OpenClaw Iris | Slack Socket Mode listener for `#greenhouse` | Primary Slack response agent |
| API | Crop, observation, topology, equipment, public proof, health endpoints | Main contract for Slack crop and operations commands |
| Website | Operator and public evidence views | Source of canonical visual summaries and public-safe data |

### 3.2 Existing Slack Producers

| Path | Trigger | Current Slack behavior |
| --- | --- | --- |
| `ingestor/tasks.py::alert_monitor` | Every 300 seconds | Posts new non-quiet system alerts, critical escalations, and resolution replies |
| `ingestor/tasks.py::planning_heartbeat` | Every 60 seconds | Posts missed sunrise/sunset plan delivery and MCP restart notices |
| `ingestor/tasks.py::midnight_watch` | Every 60 seconds around midnight window | Posts MIDNIGHT planning result |
| `scripts/forecast-action-engine.py` | Ingestor every 900 seconds | Posts forecast action rules with `action_type=alert` |
| `scripts/checklist-to-slack.sh` | User cron, daily | Posts grower checklist |
| `scripts/slack-channel-archive.py` | User cron, every 6 hours | Archives channel history into Iris memory |
| Iris planner prompt | Planner runs | Instructs Iris to report planning outcomes to `#greenhouse` |
| OpenClaw Iris | Slack Socket Mode | Monitors and responds in `#greenhouse` |

### 3.3 Existing Alert Surface

Current alert types found in ingestor and scripts include:

| Alert type | Domain | Proposed Slack treatment |
| --- | --- | --- |
| `leak_detected` | Water safety | Critical immediate post, thread updates until resolved |
| `temp_safety` | Climate safety | Critical immediate post, include current temp, band, equipment state |
| `vpd_extreme` | Climate safety | Critical immediate post, include VPD, temp, RH, active firmware mode |
| `safety_invalid` | Safety contract | Critical immediate post |
| `heat_staging_inversion` | Equipment safety | Critical immediate post if heating behavior conflicts with cooling/venting |
| `heap_pressure_critical` | Firmware health | Critical immediate post |
| `relay_stuck` | Equipment | Warning post, escalate if repeated |
| `sensor_offline` | Sensor health | Digest by default; critical only for required control sensors or long outage |
| `soil_sensor_offline` | Crop/irrigation | Warning during active grow periods, digest otherwise |
| `irrigation_feedback_gap` | Irrigation | Warning post with affected valve/zone |
| `water_flowing` anomalies | Water safety | Critical if unexpected flow exceeds leak threshold |
| `esp32_reboot` | Firmware health | Digest by default, warning if repeated |
| `firmware_version_mismatch` | Deployment | Warning or critical depending on OTA state |
| `firmware_relief_ceiling` | Climate control | Warning post with heat/VPD context |
| `firmware_vent_latched` | Climate control | Warning post if prolonged |
| `setpoint_unconfirmed` | Dispatcher/readback | Warning post, escalate after configured SLA |
| `esp32_push_failed` | Dispatcher | Warning post, escalate if sustained |
| `planner_stale` | Planner | Warning post if no recent active plan |
| `planner_required_plan_missed` | Planner delivery | Critical for required sunrise/sunset plan miss |
| `planner_gateway_delivery_failed` | Planner delivery | Warning or critical depending on trigger |
| `planner_trigger_sla_timeout` | Planner delivery | Warning post |
| `planner_evaluation_missed` | Evaluation | Digest or warning |
| `planner_band_ownership_drift` | Control contract | Warning, route to coordinator |
| `planner_tunable_range_drift` | Control contract | Warning, route to coordinator |
| `planner_plan_horizon_missing` | Planner quality | Warning post |
| `forecast_deviation` | Forecast/planner | Warning post; offer "ask Iris why" action |
| `climate_action_proof_stale` | Audit/proof | Warning or digest |
| `tunable_zero_variance` | Control quality | Digest unless coupled with poor score |
| `vpd_stress` | Climate/crop stress | Warning post with trend and affected crop zones |
| `vent_vpd_moisture_gap` | Climate/crop stress | Warning post when moisture assist is insufficient |
| `vent_moisture_capacity_limit` | Climate/crop stress | Warning or digest |

### 3.4 Existing Crop and Observation Capabilities

MCP and API already support the foundation for crop operations:

- Create, update, deactivate, list, and inspect crops.
- Assign crops to zones and positions.
- Read topology by greenhouse, zone, shelf, and position.
- Plant, clear, transplant, and harvest crops through API endpoints.
- Record observations with health score, severity, notes, stress tags,
  morphology, photos, and affected percentage.
- Record lifecycle events, treatments, and harvests.
- Summarize crop health and greenhouse health.

Slack should become a structured input/output surface for these capabilities,
not a parallel crop database.

## 4. Product Vision

An operator should be able to run the greenhouse from `#greenhouse` at the level
of decisions, observations, and acknowledgements:

- "What needs attention right now?"
- "What is Iris doing about the heat forecast?"
- "Which crops are in shelf A3, and are they healthy?"
- "Record that the basil in A3 has aphids on 20 percent of plants."
- "Acknowledge this VPD alert for 2 hours."
- "Show the last plan, why it changed, and whether setpoints reached firmware."
- "Post the morning greenhouse brief."

Slack should make it easy for the operator to act, but every action should still
land in Verdify's existing audit trail.

## 5. Goals

1. Centralize all Slack configuration in root project configuration.
2. Keep Iris as the single operational Slack identity for greenhouse operations.
3. Make current alerts easier to triage from Slack.
4. Add Slack workflows for crop inventory, crop health, scouting, harvests, and
   treatments.
5. Provide planner, dispatcher, and firmware state in Slack without exposing
   unsafe direct-control commands.
6. Preserve auditability through database records, API/MCP contracts, and Slack
   thread IDs.
7. Keep public website and Slack stories consistent.

## 6. Non-Goals

- Direct Slack commands that toggle relays or override firmware safety.
- A second source of truth for crops, topology, observations, or alerts.
- Public posting of sensitive operational data without redaction.
- Replacing the website or database with Slack history.
- Bypassing setpoint dispatcher readback confirmation.

## 7. Personas

### 7.1 Greenhouse Operator

Primary human responsible for keeping plants alive. Needs concise, trusted
status, actionable alerts, and simple ways to log observations while standing in
the greenhouse.

### 7.2 Iris Greenhouse Agent

The Slack-facing agent. Reads telemetry, plans, crops, observations, forecast,
and memory. Responds in `#greenhouse` and records structured outcomes through
MCP or API.

### 7.3 Coordinator

Maintains contracts, config, safety policies, deployments, and cross-agent
changes. Needs audit trails and clear escalation for system drift.

### 7.4 Public/Website Reader

Sees public-safe proof of system behavior through the website. Does not need raw
Slack threads, secrets, or sensitive operational context.

## 8. User Stories and Requirements

### Epic A: Slack Configuration and Identity

#### SLACK-OPS-001: Single Slack Config Source

As a coordinator, I want one root Slack config so Hermes, OpenClaw, and the
ingestor do not drift.

Priority: P0

Acceptance criteria:

- `slack.yaml` is the documented source of truth.
- All Verdify Slack producers read channel, username, icon, and token reference
  from the shared config.
- Local secrets are referenced by path or environment variable, not committed.
- `SLACK.md` documents every current integration point.
- Runtime services have explicit mounts or environment variables for Slack
  access.

#### SLACK-OPS-002: Iris Display Identity

As an operator, I want all greenhouse automation posts to appear as Iris so the
channel is not confused by agent identity drift.

Priority: P0

Acceptance criteria:

- Outbound greenhouse posts use the configured Iris bot token.
- Posts use consistent display name and icon where Slack permits overrides.
- Smoke tests confirm messages appear from the Iris Slack app identity.
- Orbit or other personal identities cannot post routine Verdify automation.

#### SLACK-OPS-003: Slack Integration Inventory

As a coordinator, I want an inventory of all Slack producers and listeners so
new changes do not create hidden notification paths.

Priority: P0

Acceptance criteria:

- `SLACK.md` lists producers, listeners, cron jobs, systemd services, container
  mounts, tokens, config file paths, and smoke-test commands.
- A repo search for Slack tokens does not find committed secrets.
- New Slack integrations must update `SLACK.md` and `slack.yaml`.

### Epic B: Situational Awareness

#### SLACK-OPS-010: Current Greenhouse Status

As an operator, I want to ask for current status in Slack so I can decide
whether to intervene.

Priority: P0

Example commands:

- `iris status`
- `iris greenhouse status`
- `iris what needs attention?`

Response should include:

- Current temp, RH, VPD, dew margin, outdoor temp, and forecast risk.
- Active firmware mode and mode reason.
- Active relays and safety suppressions.
- Current effective temp/VPD bands.
- Active plan trigger and age.
- Open critical and warning alerts.
- Top three crop or zone concerns.

Acceptance criteria:

- Response is generated from API/MCP/DB facts, not Slack memory alone.
- Response fits in one Slack message with optional threaded detail.
- If data is stale, the response leads with data-health warnings.

#### SLACK-OPS-011: Morning and Evening Briefs

As an operator, I want scheduled Slack briefs so I know the operational posture
without opening dashboards.

Priority: P0

Morning brief should include:

- Overnight climate compliance.
- Current open alerts.
- Forecast risks for heat, cold, dew, VPD, wind, and water demand.
- Today's planner trigger status.
- Crop scouting tasks due today.
- Harvest/treatment/follow-up tasks due today.

Evening brief should include:

- Daytime scorecard and water use.
- Heat/VPD stress windows.
- Any unconfirmed setpoints or equipment anomalies.
- Night strategy and dew risk.
- Tasks not completed.

Acceptance criteria:

- Briefs use shared Slack config.
- Brief content links to website/API where applicable.
- Missed data sources are explicitly called out.

#### SLACK-OPS-012: On-Demand Zone and Equipment Status

As an operator, I want to ask about a zone, shelf, position, sensor, or relay so
I can inspect a local issue quickly.

Priority: P1

Example commands:

- `iris zone north status`
- `iris position A3`
- `iris relay exhaust fan`
- `iris sensor vpd probe 2`

Acceptance criteria:

- Zone replies include current microclimate, assigned crops, sensors, and
  equipment.
- Position replies include occupancy, crop stage, last observation, and due
  tasks.
- Equipment replies include state, recent runtime, anomalies, and relevant
  firmware counters.

### Epic C: Alert Triage

#### SLACK-OPS-020: Alert Thread Lifecycle

As an operator, I want each alert to have a stable Slack thread so context is
not scattered.

Priority: P0

Acceptance criteria:

- First alert post creates or reuses a thread keyed by alert ID.
- Escalations, status changes, and resolution notices reply in the thread.
- Alert records store Slack channel ID and thread timestamp.
- Resolving the alert posts a short closure with duration and final state.

#### SLACK-OPS-021: Acknowledge and Snooze Alerts

As an operator, I want to acknowledge or snooze alerts from Slack so Iris knows
what is being handled.

Priority: P0

Allowed actions:

- Acknowledge.
- Snooze for 30 minutes, 2 hours, or until tomorrow morning.
- Assign to a Slack user.
- Add note.
- Mark false positive with reason.

Acceptance criteria:

- Slack actions write to the alert audit table.
- Snoozing suppresses duplicate Slack posts but does not suppress critical
  database records.
- Critical safety alerts can be acknowledged but not fully muted unless a
  coordinator override is recorded.

#### SLACK-OPS-022: Alert Runbooks

As an operator, I want Slack alerts to include the immediate next checks so I
can respond faster.

Priority: P1

Acceptance criteria:

- Each alert type has a short runbook snippet.
- Critical water, heat, and firmware alerts include the first physical check.
- Runbooks link to internal docs where available.
- Runbook text is generated from versioned repo content, not ad hoc prompt
  memory.

#### SLACK-OPS-023: Alert Noise Policy

As an operator, I want low-signal events to be batched so `#greenhouse` stays
usable.

Priority: P0

Acceptance criteria:

- Immediate posts are limited to critical and operator-actionable warning
  events.
- Known noisy events, such as transient sensor offline or ESP32 reboot, default
  to digest unless thresholds are exceeded.
- Slack policy is declarative in configuration.
- Existing quiet alert behavior is preserved and documented.

### Epic D: Crop Inventory and Topology

#### SLACK-OPS-030: Query Planting Map

As an operator, I want to ask Slack what is planted where so I can navigate the
greenhouse.

Priority: P0

Example commands:

- `iris planting map`
- `iris what's in north shelf A?`
- `iris empty positions`
- `iris crops due for harvest`

Acceptance criteria:

- Replies come from topology and crop API/MCP records.
- Empty positions can be listed by zone, shelf, or full greenhouse.
- Crop cards include name, variety, count, stage, planted date, expected
  harvest, zone, position, and last health score.

#### SLACK-OPS-031: Plant a Crop From Slack

As an operator, I want to create a crop in a position from Slack so inventory is
updated while planting.

Priority: P1

Example:

`iris plant basil genovese in A3 count 12 stage seedling`

Acceptance criteria:

- Iris validates position occupancy before creation.
- Ambiguous crop names or positions require confirmation.
- The crop record is created through the API/MCP contract.
- Slack confirms the new crop ID and location.
- A crop lifecycle event is recorded.

#### SLACK-OPS-032: Clear, Transplant, and Harvest

As an operator, I want Slack workflows for clearing, transplanting, and
harvesting crops so location records stay accurate.

Priority: P1

Acceptance criteria:

- Clear and transplant commands use existing API endpoints.
- Harvest records include weight, unit count, grade, salable/cull quantities,
  destination, labor, and operator when provided.
- Destructive operations require confirmation.
- Each operation writes a lifecycle event and Slack audit message.

### Epic E: Crop Health and Scouting

#### SLACK-OPS-040: Record Crop Observation

As an operator, I want to record observations from Slack so crop health data is
captured at the point of work.

Priority: P0

Example commands:

- `iris observe A3: basil has aphids on 20 percent, severity medium`
- `iris record health score 78 for crop basil-a3`
- `iris note tomatoes east shelf have leaf curl`

Acceptance criteria:

- Iris resolves crop/position references and asks for confirmation if
  ambiguous.
- Observation records support notes, severity, health score, affected
  percentage, stress tags, plant height, leaf count, canopy cover, flowering or
  fruit count, mortality count, and observer.
- Slack thread includes the saved observation ID.
- Observation severity can create or update crop-health alerts.

#### SLACK-OPS-041: Photo-Based Observation Intake

As an operator, I want to upload a plant photo in Slack and have it attached to
the crop observation.

Priority: P1

Acceptance criteria:

- Slack file metadata is captured.
- Image is stored or referenced according to Verdify media policy.
- Observation includes crop/position, photo path, notes, observer, and optional
  AI vision summary.
- Iris asks for crop/position when the photo message is ambiguous.
- Public website only receives redacted or approved media.

#### SLACK-OPS-042: Scouting Schedule

As an operator, I want Iris to remind me which crops need inspection so problems
are not missed.

Priority: P1

Acceptance criteria:

- Each active crop has a configurable scouting cadence by crop type or stage.
- Morning brief includes overdue scouting tasks.
- Slack can mark a scouting task done by recording an observation.
- Repeated missed scouting tasks create a warning-level operations item.

#### SLACK-OPS-043: Treatment Follow-Up

As an operator, I want treatment records and follow-up reminders in Slack so pest
and disease work closes the loop.

Priority: P1

Acceptance criteria:

- Slack can record treatments using existing fields: product, active ingredient,
  concentration, rate, method, target pest, PHI, REI, applicator, follow-up due,
  and notes.
- Follow-up due dates appear in daily briefs.
- Follow-up completion records outcome and optional observation.
- PHI/REI warnings appear before harvest commands.

### Epic F: Planner Operations

#### SLACK-OPS-050: Plan Status and Explanation

As an operator, I want to ask Iris what plan is active and why it chose it.

Priority: P0

Example commands:

- `iris plan status`
- `iris why are we venting?`
- `iris what changed since sunrise?`

Acceptance criteria:

- Reply includes trigger, plan age, horizon, setpoints/tunables, forecast basis,
  and active constraints.
- Iris distinguishes crop target bands from planner-owned tactical tunables.
- Reply includes whether setpoints were dispatched and confirmed by firmware
  readbacks.

#### SLACK-OPS-051: Manual Planner Trigger

As an operator, I want to request a planner run from Slack when conditions
change.

Priority: P1

Acceptance criteria:

- Allowed trigger names map to existing planner trigger contracts.
- Manual runs include requester and reason in audit records.
- Planner delivery result posts to Slack thread.
- Slack command cannot bypass planner guardrails or firmware safety.

#### SLACK-OPS-052: Forecast Deviation Triage

As an operator, I want forecast deviation alerts to explain operational impact.

Priority: P1

Acceptance criteria:

- Forecast alerts include changed forecast inputs and affected risk windows.
- Iris suggests whether a new plan is needed.
- Operator can request a planner run or dismiss if irrelevant.

### Epic G: Dispatcher and Firmware Monitoring

#### SLACK-OPS-060: Setpoint Dispatch Confirmation

As an operator, I want Slack to show whether planned setpoints actually reached
the ESP32.

Priority: P0

Acceptance criteria:

- `setpoint_unconfirmed` alerts include requested value, clamped/applied value,
  readback value, age, and target entity.
- Slack distinguishes "not pushed", "pushed but unconfirmed", and "confirmed".
- Alerts resolve automatically when readback confirms.

#### SLACK-OPS-061: Guardrail Visibility

As an operator, I want to know when Iris asked for something that was constrained
by dispatcher or firmware guardrails.

Priority: P1

Acceptance criteria:

- Slack summaries include clamped setpoints and held-by-guardrail states.
- Guardrail events are searchable by plan, crop band, and firmware mode.
- Repeated guardrail holds create a warning item for planner review.

#### SLACK-OPS-062: Firmware Health Summary

As an operator, I want firmware health summarized in Slack so I can catch device
issues before plants are at risk.

Priority: P1

Acceptance criteria:

- Summary includes firmware version, uptime, reset reason, WiFi, heap, active
  probe count, mode reason, relay counters, and override events where available.
- Version mismatch and heap pressure alerts link to deployment/runbook context.
- Firmware OTA safety gates remain outside Slack direct control.

### Epic H: Website and Public Evidence Integration

#### SLACK-OPS-070: Link Slack Events to Operator Views

As an operator, I want Slack messages to link to the relevant website or API view
so I can inspect detail.

Priority: P1

Acceptance criteria:

- Crop alerts link to crop or position pages.
- Climate alerts link to operations dashboards.
- Planner messages link to plan/evidence pages where available.
- Links are generated from canonical IDs, not hardcoded prose.

#### SLACK-OPS-071: Public-Safe Operations Log

As a website reader, I want public greenhouse evidence to reflect real operations
without exposing secrets or sensitive Slack content.

Priority: P2

Acceptance criteria:

- Slack-originated observations and events can be included in public evidence
  only after redaction or approval.
- Public endpoints expose summaries, not raw Slack messages.
- Secret material and private Slack user names are not published.

### Epic I: Knowledge and Memory

#### SLACK-OPS-080: Structured Slack Archive

As Iris, I want Slack history archived into memory with structure so future plans
can use operator context.

Priority: P1

Acceptance criteria:

- Archive job stores message text, author identity class, timestamps, thread
  relationships, linked alert/crop/plan IDs, and action outcomes.
- Messages are classified as alert, observation, command, plan discussion,
  checklist, or general context.
- Sensitive content is filtered according to memory policy.

#### SLACK-OPS-081: Extract Lessons and Actions

As a coordinator, I want Iris to extract lessons and unresolved actions from
Slack so operational learning is not lost.

Priority: P2

Acceptance criteria:

- Daily or weekly job proposes lessons from resolved alert threads and crop
  observations.
- Proposed lessons are reviewable before becoming planner context.
- Unresolved Slack action items appear in daily briefs.

### Epic J: Auth, Roles, and Safety

#### SLACK-OPS-090: Slack User Role Mapping

As a coordinator, I want Slack users mapped to Verdify roles so write actions
are controlled.

Priority: P0

Roles:

- Viewer: read status and summaries.
- Operator: acknowledge alerts and record observations.
- Grower: plant, clear, transplant, harvest, and record treatments.
- Coordinator: change config, approve risky planner actions, override alert
  mute policy.

Acceptance criteria:

- Role mapping is stored outside source control.
- Unknown users default to read-only or denied writes.
- Every write action records Slack user ID, resolved Verdify actor, timestamp,
  command text, and resulting record ID.

#### SLACK-OPS-091: Confirmation for Risky Writes

As an operator, I want destructive or high-impact Slack actions to require
confirmation so accidental messages do not alter greenhouse records.

Priority: P0

Confirmation required for:

- Clear crop.
- Transplant crop.
- Harvest crop.
- Mark alert false positive.
- Snooze critical alert.
- Trigger planner run with non-default reason.
- Any future command that changes config.

Acceptance criteria:

- Confirmation message includes exact entity, action, and irreversible fields.
- Confirmation expires after a short window.
- Expired confirmations do nothing.

#### SLACK-OPS-092: No Direct Relay Control

As a coordinator, I want Slack to be unable to directly toggle greenhouse relays
so firmware safety remains authoritative.

Priority: P0

Acceptance criteria:

- Slack commands cannot call Home Assistant relay toggles directly.
- Slack commands cannot write ESPHome actuator state directly.
- All control-affecting changes go through planner/setpoint dispatcher contracts
  and firmware safety.
- Documentation explicitly states this boundary.

## 9. Notification Taxonomy

### 9.1 Severity Levels

| Level | Slack behavior | Examples |
| --- | --- | --- |
| Critical | Immediate channel post, thread lifecycle, escalation, cannot be silently muted | Leak, temp safety, VPD extreme, firmware critical heap pressure |
| Warning | Immediate or batched post depending on dedupe policy, thread updates | Relay stuck, setpoint unconfirmed, planner delivery failed, soil sensor offline |
| Digest | Morning/evening summary only unless repeated or coupled with other failure | ESP32 reboot, transient sensor offline, missed low-priority evaluation |
| Info | Thread reply or requested response only | Plan posted, crop observation saved, checklist completed |

### 9.2 Proposed Crop Alert Types

| Alert type | Trigger | Slack behavior |
| --- | --- | --- |
| `crop_observation_overdue` | Crop exceeds scouting cadence | Morning brief, warning if repeated |
| `crop_health_declining` | Health score drops beyond threshold | Warning post with crop card |
| `crop_pest_detected` | Observation severity medium or higher with pest tag | Warning or critical based on severity and spread |
| `crop_disease_detected` | Observation severity medium or higher with disease tag | Warning or critical based on severity and spread |
| `crop_treatment_followup_due` | Treatment follow-up date reached | Morning brief, warning if overdue |
| `crop_harvest_due` | Expected harvest date reached | Morning brief |
| `crop_harvest_overdue` | Expected harvest overdue beyond threshold | Warning |
| `crop_position_conflict` | More than one active crop assigned to occupied position | Critical data-quality post |
| `crop_profile_missing` | Active crop lacks target profile or band inputs | Warning, route to coordinator/grower |
| `crop_stage_stale` | Crop stage not updated by expected window | Digest or warning |

### 9.3 Proposed Operations Digests

| Digest | Cadence | Content |
| --- | --- | --- |
| Morning operator brief | Daily morning | Overnight compliance, forecast risk, alerts, scouting, harvests, treatments |
| Evening operator brief | Daily evening | Day score, water use, stress windows, incomplete tasks, night plan |
| Weekly crop review | Weekly | Health trends, harvests, treatments, empty positions, crop stage changes |
| Weekly control review | Weekly | Planner score, band compliance, guardrails, unconfirmed setpoints, firmware health |

## 10. Command Model

### 10.1 Message Forms

Slack commands should support natural language, but internally normalize to
explicit intents:

| Intent | Examples | Backend |
| --- | --- | --- |
| `status.get` | `iris status` | API/MCP read |
| `zone.status.get` | `iris zone north status` | API topology/readings |
| `crop.map.get` | `iris planting map` | API/MCP crop list |
| `crop.create` | `iris plant basil in A3 count 12` | API/MCP crop create |
| `crop.observe` | `iris observe A3 aphids severity medium` | API/MCP observation create |
| `crop.harvest` | `iris harvest basil A3 230g` | API harvest endpoint |
| `alert.ack` | Button or `ack alert 123` | Alert audit write |
| `alert.snooze` | Button or `snooze alert 123 2h` | Alert audit write |
| `plan.status.get` | `iris plan status` | MCP/API read |
| `plan.trigger` | `iris run planner because storm front shifted` | Existing planner trigger path |

### 10.2 Confirmation Pattern

Slack should use a two-step pattern for write operations:

1. Iris parses command and replies with a structured confirmation card.
2. User confirms with a button or short reply.
3. Iris performs the action through API/MCP.
4. Iris posts saved record ID and any follow-up alert/task.

### 10.3 Error Handling

Responses should be explicit when a command cannot complete:

- Missing permission.
- Ambiguous crop, position, zone, or alert.
- Data source stale.
- Backend unavailable.
- Validation failed.
- Action would bypass safety boundary.

## 11. Data and Configuration Requirements

### 11.1 Configuration

The Slack configuration file should grow from identity and channel routing into
a declarative notification policy:

```yaml
identity:
  display_name: Iris
  icon_emoji: ":seedling:"

channels:
  greenhouse:
    id: C0ANVVAPLD6
    name: greenhouse

notifications:
  alert_defaults:
    critical: immediate
    warning: immediate
    digest: digest
  quiet_types:
    - sensor_offline
    - esp32_reboot
  crop_alerts:
    crop_observation_overdue: digest
    crop_health_declining: warning

commands:
  enabled_channel_ids:
    - C0ANVVAPLD6
  unsafe_direct_relay_control: false
```

### 11.2 Secret Management

Requirements:

- Bot token and app token must stay outside git.
- Runtime paths must be local service-accessible paths, not NAS-dependent
  references.
- `.gitignore` must exclude local secret files and backups.
- Service units and containers must mount/read secrets consistently.
- Smoke tests should confirm token identity and channel access without posting
  durable noise.

### 11.3 Database/API Gaps

The current API/MCP surface is strong enough for MVP, but Slack operations need
several durable fields or tables:

- Alert Slack channel ID and thread timestamp.
- Alert acknowledgement, snooze, assignment, and false-positive audit records.
- Slack user ID to Verdify actor/role mapping.
- Slack command audit table.
- Crop observation Slack message/thread/file references.
- Crop task table for scouting, follow-up, harvest, and stage checks.
- Notification dedupe table keyed by event source and entity.
- Guardrail/held-by-guardrail state for requested setpoints that were evaluated
  but not applied.

## 12. Architecture Proposal

### 12.1 Read Path

`#greenhouse` message -> OpenClaw Iris Slack Socket Mode -> intent router ->
MCP/API/DB read -> Iris response -> Slack Web API using shared config.

### 12.2 Write Path

`#greenhouse` command -> OpenClaw Iris -> intent parser -> authorization check
-> confirmation if needed -> API/MCP write -> audit record -> Slack
confirmation -> optional website/public evidence update.

### 12.3 Notification Path

Ingestor task or script -> alert/notification policy -> shared Slack helper ->
Slack Web API -> record Slack timestamp/thread in DB when entity-backed.

### 12.4 Memory Path

Slack channel archive -> structured classifier -> Iris memory -> reviewed
lessons/actions -> planner context only after policy allows it.

## 13. MVP Scope

### Phase 0: Stabilize Existing Slack

Status: partially implemented by current Slack config unification work.

Deliverables:

- Shared `slack.yaml`.
- Shared Slack helper.
- `SLACK.md` integration inventory.
- Runtime token paths for ingestor, Hermes, and OpenClaw Iris.
- Smoke tests for bot post/delete and Socket Mode connection.

### Phase 1: Operator Status and Alert Triage

Deliverables:

- `iris status`.
- `iris plan status`.
- Alert thread persistence.
- Ack/snooze/assign/note actions.
- Morning/evening operator briefs.
- Declarative alert notification policy in `slack.yaml`.

### Phase 2: Crop Inventory and Observations

Deliverables:

- Planting map queries.
- Position/crop cards.
- Record text observations.
- Photo observation intake.
- Crop health alerts.
- Scouting cadence and reminders.

### Phase 3: Full Crop Operations

Deliverables:

- Plant, clear, transplant, harvest commands.
- Treatment records and follow-up reminders.
- PHI/REI warnings.
- Weekly crop review.
- Public-safe evidence integration.

### Phase 4: Knowledge Loop

Deliverables:

- Structured Slack archive.
- Lesson/action extraction.
- Reviewed lessons into planner memory.
- Weekly control review with planner/firmware/dispatcher metrics.

## 14. Success Metrics

| Metric | Target |
| --- | --- |
| Slack identity drift | Zero routine Verdify posts from non-Iris identity |
| Critical alert delivery | Critical alert appears in Slack within one alert monitor cycle |
| Alert thread completeness | 95 percent of entity-backed alerts have thread ID and resolution update |
| Operator status latency | `iris status` responds within 10 seconds when backends are healthy |
| Crop observation capture | 90 percent of scouting observations recorded in structured crop records |
| Missed scouting tasks | Visible in morning brief every day until completed |
| False duplicate alerts | Reduced through dedupe/thread policy |
| Safety boundary violations | Zero Slack commands directly toggle relays |

## 15. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Slack becomes noisy | Declarative policy, digest defaults, dedupe, stable threads |
| Natural language command ambiguity | Confirmation cards, entity resolution, short IDs, role checks |
| Secret drift across services | Single config, local token files, smoke tests, documented service mounts |
| Unsafe control actions from Slack | No direct relay control, writes through API/MCP/dispatcher, confirmation gates |
| Slack archive contaminates planner memory | Structured classification and review before lessons enter planner context |
| Public website leaks private operations | Redaction and public-safe summary endpoints only |
| Crop data becomes inconsistent | API/MCP remain source of truth; Slack only writes through contracts |

## 16. Open Questions

1. What are the canonical Slack user IDs and Verdify roles for operator, grower,
   and coordinator?
2. Should crop commands use human-readable position labels only, or introduce QR
   codes/short IDs at each physical position?
3. Which crop observation severities should create Slack alerts immediately?
4. What retention policy should apply to Slack-uploaded crop photos?
5. Should morning/evening briefs live in ingestor tasks, Iris/OpenClaw, or a
   shared notification scheduler?
6. Which website pages should be deep-linked first for operations, crop, and
   planner evidence?

## 17. Recommended Next Build Order

1. Add alert thread persistence and ack/snooze audit tables.
2. Add `iris status` and `iris plan status` read-only commands.
3. Move alert routing/noise policy into `slack.yaml`.
4. Add Slack user role mapping outside source control.
5. Add crop/position query commands.
6. Add text observation capture.
7. Add photo observation intake.
8. Add planting, clearing, transplanting, harvest, and treatment workflows after
   confirmation and role checks are in place.
