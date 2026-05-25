# Slack Greenhouse Operations PRD

Status legend: `active` is implemented in deterministic Python or MCP, `partial` has a working foundation but still needs live operational proof, `planned` is documented but not complete.

| ID | Story | Status | Acceptance |
|---|---|---:|---|
| SLACK-OPS-001 | Single Slack config source | active | `slack.yaml` controls channel, identity, token files, alert policy, and brief schedule. |
| SLACK-OPS-002 | Iris display identity | active | Posts use `slack_config.build_slack_payload()` with Iris username/icon; actual identity depends on Iris token file. |
| SLACK-OPS-003 | Slack integration inventory | active | `SLACK.md` lists all producers, cron jobs, agents, and MCP paths. |
| SLACK-OPS-010 | Current greenhouse status | active | `status` reads `v_greenhouse_now`. |
| SLACK-OPS-011 | Morning and evening briefs | partial | `slack_operator_briefs` builds deterministic briefs from DB and posts on configured schedule; needs live post observation. |
| SLACK-OPS-012 | On-demand zone/equipment status | active | `zone`, `position`, `equipment`, and `sensor` commands query deterministic views/tables. |
| SLACK-OPS-020 | Alert thread lifecycle | active | Alert posts, escalation, resolution threading, and open-thread view are tracked. |
| SLACK-OPS-021 | Acknowledge and snooze alerts | active | `ack`, `snooze`, `assign`, `resolve`, and `false positive` commands update DB and audit rows. |
| SLACK-OPS-022 | Alert runbooks | planned | Runbook text/link mapping still needs per-alert content. |
| SLACK-OPS-023 | Alert noise policy | active | `slack_ops.policy` reads `notifications.alert_policy`. |
| SLACK-OPS-030 | Query planting map | active | `crop map` and `empty positions` use `v_position_current`. |
| SLACK-OPS-031 | Plant a crop from Slack | active | `plant NAME in POSITION` creates crop rows with Slack audit. |
| SLACK-OPS-032 | Clear, transplant, and harvest | active | Risky lifecycle writes require confirmation and write crop/harvest events. |
| SLACK-OPS-040 | Record crop observation | active | `observe ...` writes `observations` with Slack metadata. |
| SLACK-OPS-041 | Photo-based observation intake | planned | Schema stores Slack file refs; image analysis workflow is not implemented here. |
| SLACK-OPS-042 | Scouting schedule | partial | `crop_tasks` and `v_slack_crop_tasks_due` exist; automatic task generation needs tuning. |
| SLACK-OPS-043 | Treatment follow-up | partial | Treatment command and `crop_tasks` links exist; follow-up automation needs live use. |
| SLACK-OPS-050 | Plan status and explanation | active | `plan status` reads latest plan journal row. |
| SLACK-OPS-051 | Manual planner trigger | active | `trigger planner` writes `planner_trigger_ledger`. |
| SLACK-OPS-052 | Forecast deviation triage | partial | Existing forecast action engine posts through shared config; interactive triage flow is not complete. |
| SLACK-OPS-060 | Setpoint dispatch confirmation | active | Briefs surface unconfirmed setpoints; existing confirmation monitor opens alerts. |
| SLACK-OPS-061 | Guardrail visibility | partial | Existing firmware/planner alerts surface guardrails; richer Slack explanations remain planned. |
| SLACK-OPS-062 | Firmware health summary | active | `firmware health` reads latest diagnostics. |
| SLACK-OPS-070 | Link Slack events to operator views | active | Slack metadata columns link crop/alert/notification rows back to Slack. |
| SLACK-OPS-071 | Public-safe operations log | partial | DB audit exists; public rendering is outside this change. |
| SLACK-OPS-080 | Structured Slack archive | active | `scripts/slack-channel-archive.py` uses shared config and configured archive dir. |
| SLACK-OPS-081 | Extract lessons and actions | planned | Archive exists; lesson extraction workflow remains AI-assisted future work. |
| SLACK-OPS-090 | Slack user role mapping | active | `slack_user_roles` maps Slack users to viewer/grower/operator/coordinator. |
| SLACK-OPS-091 | Confirmation for risky writes | active | Risky crop writes, snooze, and planner trigger use `slack_confirmation_requests`. |
| SLACK-OPS-092 | No direct relay control | active | Parser blocks relay/equipment forcing commands. |

## Validation Plan

1. Run focused tests:

   ```bash
   /srv/greenhouse/.venv/bin/python -m pytest tests/test_slack_config.py tests/test_slack_ops.py verdify_schemas/tests/test_slack_ops.py
   ```

2. Run drift guard against live DB:

   ```bash
   /srv/greenhouse/.venv/bin/python -m pytest verdify_schemas/tests/test_drift_guards.py
   ```

3. Smoke deterministic command path:

   ```bash
   /srv/greenhouse/.venv/bin/python scripts/slack-ops.py status --json
   /srv/greenhouse/.venv/bin/python scripts/slack-ops.py "brief morning" --json
   ```

4. Deploy/restart:

   ```bash
   make hermes-deploy-config
   systemctl --user restart openclaw-gateway.service
   sudo systemctl restart verdify-ingestor.service verdify-mcp.service
   ```

5. Live Slack smoke: post one marked Iris test message only after the local Iris bot token is readable by the runtime user.
