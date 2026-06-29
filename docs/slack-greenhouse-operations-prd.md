# Slack Greenhouse Operations PRD

Status legend: `active` is implemented in deterministic Python, MCP, or OpenClaw-backed work queue and has a validation path.

| ID | Story | Status | Acceptance |
|---|---|---:|---|
| SLACK-OPS-001 | Single Slack config source | active | `slack.yaml` controls channel, identity, token files, alert policy, and brief schedule. |
| SLACK-OPS-002 | Iris display identity | active | Posts use `slack_config.build_slack_payload()` with Iris username/icon; actual identity depends on Iris token file. |
| SLACK-OPS-003 | Slack integration inventory | active | `SLACK.md` lists all producers, cron jobs, agents, and MCP paths. |
| SLACK-OPS-010 | Current greenhouse status | active | `status` reads `v_greenhouse_now`. |
| SLACK-OPS-011 | Morning and evening briefs | active | `slack_operator_briefs` builds deterministic briefs from DB, posts on configured schedule, and records `slack_notification_events`; live post smoke is part of validation. |
| SLACK-OPS-012 | On-demand zone/equipment status | active | `zone`, `position`, `equipment`, and `sensor` commands query deterministic views/tables. |
| SLACK-OPS-020 | Alert thread lifecycle | active | Alert posts, escalation, resolution threading, and open-thread view are tracked. |
| SLACK-OPS-021 | Acknowledge and snooze alerts | active | `ack`, `snooze`, `assign`, `resolve`, and `false positive` commands update DB and audit rows. |
| SLACK-OPS-022 | Alert runbooks | active | `slack_alert_runbooks` maps alert types/severities to operator steps; alert posts and `runbook ...` commands render mapped text. |
| SLACK-OPS-023 | Alert noise policy | active | `slack_ops.policy` reads `notifications.alert_policy`. |
| SLACK-OPS-030 | Query planting map | active | `crop map` and `empty positions` use `v_position_current`. |
| SLACK-OPS-031 | Plant a crop from Slack | active | `plant NAME in POSITION` creates crop rows with Slack audit. |
| SLACK-OPS-032 | Clear, transplant, and harvest | active | Risky lifecycle writes require confirmation and write crop/harvest events. |
| SLACK-OPS-040 | Record crop observation | active | `observe ...` writes `observations` with Slack metadata. |
| SLACK-OPS-041 | Photo-based observation intake | active | `photo observation ...` records Slack file refs on `observations` and queues `slack_ai_work_items` for OpenClaw image reasoning. |
| SLACK-OPS-042 | Scouting schedule | active | Crop creation and `refresh crop tasks` generate scouting tasks; observations/photo observations complete related scouting tasks. |
| SLACK-OPS-043 | Treatment follow-up | active | Treatment commands set `followup_due_at` and create `treatment_followup` crop tasks; later observations complete follow-up tasks. |
| SLACK-OPS-050 | Plan status and explanation | active | `plan status` reads latest plan journal row. |
| SLACK-OPS-051 | Manual planner trigger | active | `trigger planner` writes `planner_trigger_ledger`. |
| SLACK-OPS-052 | Forecast deviation triage | active | `forecast triage` reads `v_slack_forecast_triage` over forecast deviation/action logs for deterministic Slack triage. |
| SLACK-OPS-060 | Setpoint dispatch confirmation | active | Briefs surface unconfirmed setpoints; existing confirmation monitor opens alerts. |
| SLACK-OPS-061 | Guardrail visibility | active | `guardrail summary` reads `v_slack_guardrail_summary` and recent `setpoint_clamps` guardrail events. |
| SLACK-OPS-062 | Firmware health summary | active | `firmware health` reads latest diagnostics. |
| SLACK-OPS-070 | Link Slack events to operator views | active | Slack metadata columns link crop/alert/notification rows back to Slack. |
| SLACK-OPS-071 | Public-safe operations log | active | `ops log` reads `v_slack_public_ops_log`, which redacts Slack user mentions and omits Slack user ids. |
| SLACK-OPS-080 | Structured Slack archive | active | `scripts/slack-channel-archive.py` uses shared config and configured archive dir. |
| SLACK-OPS-081 | Extract lessons and actions | active | `extract lessons` queues `slack_ai_work_items` for OpenClaw reasoning with recent commands, alerts, and plans as context. |
| SLACK-OPS-090 | Slack user role mapping | active | `slack_user_roles` maps Slack users to viewer/grower/operator/coordinator. |
| SLACK-OPS-091 | Confirmation for risky writes | active | Risky crop writes, snooze, and planner trigger use `slack_confirmation_requests`. |
| SLACK-OPS-092 | No direct relay control | active | Parser blocks relay/equipment forcing commands. |

## Validation Plan

1. Run focused tests:

   ```bash
   python3 -m pytest tests/test_slack_config.py tests/test_slack_ops.py verdify_schemas/tests/test_slack_ops.py
   ```

2. Run drift guard against live DB:

   ```bash
   python3 -m pytest verdify_schemas/tests/test_drift_guards.py
   ```

3. Smoke deterministic command path:

   ```bash
   python3 scripts/slack-ops.py status --json
   python3 scripts/slack-ops.py "brief morning" --json
   python3 scripts/slack-ops.py "runbook temp_safety" --json
   python3 scripts/slack-ops.py "forecast triage" --json
   python3 scripts/slack-ops.py "guardrail summary" --json
   python3 scripts/slack-ops.py "ops log" --json
   python3 scripts/slack-ops.py "extract lessons" --json
   ```

4. Deploy/restart:

   ```bash
   make hermes-deploy-config
   systemctl --user restart openclaw-gateway.service
   sudo systemctl restart verdify-ingestor.service verdify-mcp.service
   ```

5. Live Slack smoke: post one marked Iris test message only after the local Iris bot token is readable by the runtime user.
