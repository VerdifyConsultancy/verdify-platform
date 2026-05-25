# Verdify Slack Operations

`slack.yaml` is the versioned source of truth for Verdify Slack behavior. Runtime secrets are never committed. The Iris Slack app tokens live on the Iris VM under `/etc/verdify/slack/`:

- `iris_slack_bot_token.txt` - Web API bot token used for `chat.postMessage`, history/archive reads, and deterministic command replies.
- `iris_slack_app_token.txt` - Socket Mode app token used by OpenClaw when the Slack listener is enabled.
- `iris_slack_signing_secret.txt` - Events API signing secret for any HTTP receiver.

All runtime code should load Slack settings through `slack_config.load_slack_settings()`. `SLACK_TOKEN_FILE`, `SLACK_CHANNEL`, and `VERDIFY_SLACK_CONFIG` are deployment overrides only.

## Identity

All greenhouse posts are sent as Iris:

- Slack channel: `#greenhouse`
- Channel ID: `C0ANVVAPLD6`
- Display username: `Iris`
- Icon: `:seedling:`
- Greenhouse ID: `vallery`

The displayed Slack identity ultimately depends on the bot token file containing the Iris Slack app bot token. A token from another Slack app will display as that app no matter what username is passed in the payload.

## Agents

Hermes is the production Iris planner gateway. It does not monitor Slack directly. Hermes can use the MCP `slack_ops` tool, which executes deterministic Slack operations through Python and the database.

OpenClaw is the operator-facing assistant/listener for Iris Slack. The expected OpenClaw auth profiles for `iris` and `iris-planner` are `openai-codex:jason@verdify.ai`, default model `openai-codex/gpt-5.5`, fallback `vllm/gemma4-26b`. OpenClaw should use the Iris Slack app token from the configured local token file and either Socket Mode or the MCP `slack_ops` tool for greenhouse operations.

## Current Runtime State

As of 2026-05-25, OpenClaw is the only agent runtime confirmed to monitor and respond directly in `#greenhouse`. The active OpenClaw Slack connector is Socket Mode, not the HTTP Events API. It reads:

- Bot token: `/etc/verdify/slack/iris_slack_bot_token.txt`
- App token: `/etc/verdify/slack/iris_slack_app_token.txt`
- Channel: `C0ANVVAPLD6`
- Per-channel mention policy: `requireMention=false`

The OpenClaw gateway reports Slack as enabled, configured, running, connected, and healthy. `iris` and `iris-planner` both use `openai-codex:jason@verdify.ai` with `openai-codex/gpt-5.5`; stale `jason@vallery.net` auth profiles are not present in the active OpenClaw config or per-agent auth stores.

Live Slack tests on 2026-05-25:

- Shared config/Web API smoke posted to Slack as `username=Iris`, `icon=:seedling:`, `bot_id=B0ANY7P8PR6`, `ts=1779744739.013619`.
- OpenClaw `iris` delivered `[smoke] OpenClaw iris Slack delivery OK 2026-05-25` via `openai-codex/gpt-5.5`, `bot_id=B0ANY7P8PR6`, `ts=1779745414.906819`.
- OpenClaw `iris-planner` delivered `[smoke] OpenClaw iris-planner Slack delivery OK 2026-05-25` via `openai-codex/gpt-5.5`, `bot_id=B0ANY7P8PR6`, `ts=1779745462.607289`.
- Deterministic command smokes passed for `runbook temp_safety`, `forecast triage`, `guardrail summary`, `ops log`, and `extract lessons`.
- Post-deploy OpenClaw MCP smoke called `verdify.slack_ops` once, returned `intent=alert.runbook.get`, and delivered to Slack at `ts=1779746566.359979`.
- Post-deploy operator brief validation posted through shared config as `username=Iris`, `icon=:seedling:`, `bot_id=B0ANY7P8PR6`, `ts=1779746583.673699`, and was recorded in `slack_notification_events`.
- Post-deploy gates passed: `make lint`; `make test` (`554 passed, 2 skipped, 1 xfailed`).

OpenClaw outbound delivery uses the same Iris Slack bot token, but its default send path does not set Slack `username` or `icons` on the message payload. For OpenClaw posts to display as Iris, the Slack app/bot display name itself must be Iris or OpenClaw delivery must be extended/configured to pass the shared identity fields.

## Slack Integration Points

| Source | Trigger | Destination | Config path | Notes |
|---|---|---|---|---|
| `ingestor/tasks.py::alert_monitor` | every 5 min in ingestor task loop | `#greenhouse` alert parent/thread messages | `slack.yaml` via `ingestor/config.py` | Posts critical/immediate alerts and resolution thread updates. Policy comes from `notifications.alert_policy`. |
| `ingestor/tasks.py::planning_heartbeat` | every 60 s | `#greenhouse` | `slack.yaml` | Posts planner delivery failures and MCP auto-restart notices. |
| `ingestor/tasks.py::midnight_watch` | 00:05-00:10 MDT | `#greenhouse` | `slack.yaml` | Posts one nightly status for the MIDNIGHT trigger. |
| `ingestor/tasks.py::slack_operator_briefs` | morning/evening schedule from `slack.yaml` | `#greenhouse` | `slack.yaml` | Posts deterministic operator brief and records `slack_notification_events`. |
| `ingestor/tasks.py::forecast_action_engine` | every 15 min | `#greenhouse` | `slack.yaml` | Uses shared `_post_slack`; standalone script also uses shared config. |
| `scripts/checklist-to-slack.sh` | cron `0 13 * * *` | `#greenhouse` | `slack.yaml` | Daily checklist post. |
| `scripts/slack-channel-archive.py` | cron `0 */6 * * *` | archive files | `slack.yaml` | Reads channel history and writes markdown archive under configured archive dir. |
| `scripts/alert-monitor.py` | legacy/manual cron path | `#greenhouse` | `slack.yaml` | Kept for compatibility; in-process ingestor task is preferred. |
| `scripts/forecast-action-engine.py` | legacy/manual cron path | `#greenhouse` | `slack.yaml` | Kept for compatibility with standalone runs. |
| `mcp/server.py::slack_ops` | Hermes/OpenClaw MCP call | database/Slack command state | `slack.yaml` | Deterministic command path; direct relay control is denied. |

## Command Surface

Deterministic commands are parsed in `slack_ops/intents.py` and executed in `slack_ops/service.py`.

- Read-only: `status`, `brief morning`, `plan status`, `firmware health`, `zone south`, `position A3`, `equipment`, `sensor temp`, `crop map`, `empty positions`, `harvest due`, `scouting due`, `tasks due`, `runbook temp_safety`, `forecast triage`, `guardrail summary`, `ops log`.
- Alert actions: `ack alert 12`, `resolve alert 12 fixed`, `snooze alert 12 2h`, `assign alert 12 @name`, `false positive alert 12`.
- Crop writes: `plant basil in A3`, `observe basil A3 ...`, `photo observation basil A3 ...`, `clear basil A3`, `transplant basil A3 to B2`, `harvest basil A3 230g grade A destination kitchen labor 12 min`, `treat basil ...`, `refresh crop tasks`, `complete task 12`.
- Planner: `trigger planner`.
- OpenClaw AI work: `extract lessons` queues recent Slack/alert/plan context for OpenClaw reasoning; photo observation intake queues image-analysis work.
- Confirmation: risky writes return a confirmation id and require `confirm <uuid>` or `cancel <uuid>`.

Slack is not an actuator surface. Commands that directly open/close relays, force heaters/fans/fog/misters/vents/lights, or bypass the firmware controller are denied.

## Data Model

Migration `db/migrations/143-slack-ops.sql` adds:

- Slack linkage fields on `alert_log`, `observations`, `crop_events`, `harvests`, and `treatments`.
- `slack_user_roles` for RBAC.
- `slack_command_audit` for every parsed command.
- `slack_confirmation_requests` for risky writes.
- `slack_alert_actions` for alert lifecycle actions.
- `slack_notification_events` for outbound post tracking.
- `crop_tasks` and `v_slack_crop_tasks_due` for scouting/harvest/treatment follow-up.
- `slack_alert_runbooks` for alert-type runbook text and operator links.
- `slack_ai_work_items` for OpenClaw/Hermes reasoning work queued from deterministic Slack commands.
- `v_slack_public_ops_log` for public-safe command/notification/action history.
- `v_slack_forecast_triage` and `v_slack_guardrail_summary` for deterministic operator triage.
- `v_slack_open_alert_threads` for active alert thread state.

## Validation

Local deterministic tests:

```bash
/srv/greenhouse/.venv/bin/python -m pytest tests/test_slack_config.py tests/test_slack_ops.py verdify_schemas/tests/test_slack_ops.py
```

Live smoke without posting:

```bash
/srv/greenhouse/.venv/bin/python scripts/slack-ops.py status --json
/srv/greenhouse/.venv/bin/python scripts/slack-ops.py "brief morning" --json
```

Live Slack post smoke requires the Iris bot token to be present at the configured token path. Use a clearly marked test message and record the Slack timestamp in `slack_notification_events`.
