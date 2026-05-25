# Verdify Slack Configuration

Verdify's greenhouse Slack identity is **Iris**. All greenhouse-facing automation should read the tracked root config at `slack.yaml` and post to `#greenhouse` (`C0ANVVAPLD6`) with the Iris bot token. Do not use the shared Orbit/ClawdBot token for Verdify greenhouse posts.

## Source Of Truth

- Tracked config: `slack.yaml`
- Runtime override env: `VERDIFY_SLACK_CONFIG`
- Canonical local bot token file: `/etc/verdify/slack/iris_slack_bot_token.txt`
- Canonical local app token file for OpenClaw socket mode: `/etc/verdify/slack/iris_slack_app_token.txt`
- Channel archive output: `/var/lib/verdify/openclaw-iris/memory/slack`

The YAML tracks channel IDs, identity metadata, archive paths, and secret file references only. Token contents stay out of git.

The local token files are installed on the host with owner `jason`, group id
`10000`, and mode `0640`. Host-side Verdify services and OpenClaw run as
`jason`; the `hermes-iris` container runs as uid/gid `10000` and mounts
`/etc/verdify/slack` read-only. OpenClaw uses file-backed SecretRefs for these
tokens; the provider allows this specific group-readable path because the same
local token files are intentionally shared with the Hermes runtime group.

For the proposed Slack-first greenhouse operator product, including crop
inventory, scouting, alert triage, and planner workflows, see
[`docs/slack-greenhouse-operations-prd.md`](docs/slack-greenhouse-operations-prd.md).

## Active Slack Paths

| Surface | Runtime | Schedule/Trigger | Slack behavior |
|---|---|---|---|
| `ingestor/tasks.py::alert_monitor` | `verdify-ingestor.service` task loop | Every 300s | Posts new non-quiet system alerts, critical escalations, and resolution thread replies to `#greenhouse`. `sensor_offline` and `esp32_reboot` stay DB-only in the ingestor path. |
| `ingestor/tasks.py::planning_heartbeat` | `verdify-ingestor.service` task loop | Every 60s | Posts if SUNRISE/SUNSET delivery verification finds no plan after 30 minutes, and posts if the MCP server is unreachable and auto-restarted. |
| `ingestor/tasks.py::midnight_watch` | `verdify-ingestor.service` task loop | Polls every 60s; posts once in the 00:05-00:10 MDT window | Posts one daily MIDNIGHT trigger outcome. |
| `scripts/forecast-action-engine.py` | Invoked by `ingestor/tasks.py::forecast_action_engine` | Every 900s | Posts forecast action rules whose `action_type` is `alert`. |
| `scripts/checklist-to-slack.sh` | user crontab | `0 13 * * *` MDT host crontab | Posts the daily grower checklist. Uses `scripts/slack-post.py`, which reads `slack.yaml`. |
| `scripts/slack-channel-archive.py` | user crontab | `0 */6 * * *` | Reads Slack history and writes the Iris memory archive. It does not post. |
| Iris planner prompt (`ingestor/iris_planner.py`) | Hermes `hermes-iris` | Event-driven planner triggers | Prompts Iris to report planning outcomes to `#greenhouse`. The Hermes runtime receives `slack.yaml` at `/opt/data/slack.yaml`; any Slack-capable tool/hook must use that config. |
| OpenClaw Iris runtime | `/home/jason/.openclaw/openclaw.json` | Slack events and OpenClaw tasks | Iris and Iris Planner are bound to `#greenhouse`. Keep this runtime pointed at the Iris Slack app credentials, not Orbit credentials. |

## Agent Monitoring

Live validation on 2026-05-25 showed that OpenClaw is the active Slack listener for
`#greenhouse`; Hermes is not.

| Agent/runtime | Current Slack role | Transport | Config |
|---|---|---|---|
| OpenClaw Iris | Monitors and can respond in `#greenhouse` | Slack Socket Mode via the app token; gateway on `127.0.0.1:18789` | `/home/jason/.openclaw/openclaw.json` has `channels.slack.mode: socket`, `enabled: true`, and `channels.C0ANVVAPLD6.requireMention: false`. Slack credentials resolve through file-backed SecretRefs pointing at `/etc/verdify/slack`. The separate OpenClaw `/hooks` endpoint is internal automation ingress, not Slack ingress. |
| Hermes Iris | Does not monitor Slack | None for Slack; HTTP API only on `127.0.0.1:8642` | `/opt/data/config.yaml` configures the Hermes API server and Verdify MCP tools. The container gets `VERDIFY_SLACK_CONFIG=/opt/data/slack.yaml` plus read-only access to `/etc/verdify/slack` for any future Slack-capable tool/hook, but it is not a Slack listener. |
| Orbit OpenClaw | Separate Orbit runtime can see `#greenhouse` only when mentioned | Slack Socket Mode | `/mnt/jason/orbit/.openclaw/openclaw.json` also lists `C0ANVVAPLD6`, but with `requireMention: true`; it should not be the greenhouse automation identity. |

### Iris OpenClaw Auth Path

- Runtime process: `/usr/bin/node /home/jason/.npm-global/lib/node_modules/openclaw/dist/index.js gateway --port 18789`
- Runtime config: `/home/jason/.openclaw/openclaw.json`
- Gateway: local loopback mode on `127.0.0.1:18789`
- Slack account: default Slack account in Socket Mode, using the Iris Slack bot token plus Iris Slack app token from `/etc/verdify/slack`
- Slack channel binding: `C0ANVVAPLD6` (`#greenhouse`) enabled with `requireMention: false`
- Primary agent: `iris`, identity `Iris`, workspace `/var/lib/verdify/openclaw-iris`, agent dir `/home/jason/.openclaw/agents/iris/agent`
- Primary LLM: `openai-codex/gpt-5.5`; fallback `vllm/gemma4-26b`
- Planner agents: `iris-planner` uses `openai-codex/gpt-5.5` with `vllm/gemma4-26b` fallback; `iris-planner-local` uses `vllm/gemma4-26b`
- Model auth: the `openai-codex` provider uses OAuth against `https://chatgpt.com/backend-api/codex`; the local `vllm` provider points at `http://192.168.30.105:11434/v1`

### 2026-05-25 Smoke Test

- OpenClaw gateway health: OK; `/ready` reported no failing checks.
- OpenClaw Slack channel probe: `enabled`, `configured`, `running`, `connected`, `healthy`, `works`.
- Iris bot token: `auth.test` succeeded in the Vallery workspace.
- `#greenhouse` permissions: channel lookup and history read succeeded; the bot is a member.
- Iris posting identity: `chat.postMessage` with `username: Iris` and `icon_emoji: :seedling:` succeeded, then `chat.delete` succeeded.
- Iris Socket Mode app token: `apps.connections.open` succeeded.
- OpenClaw message adapter: `openclaw message read` read recent `#greenhouse` messages; `openclaw message send` posted through the Slack adapter, and the smoke messages were deleted.
- Hermes health: `/health` returned OK, but Hermes has no Slack listener and no Slack/socket lines in container logs.

### 2026-05-25 Local Secret Cutover Test

- Copied the Iris Slack bot token and app token from the live Iris OpenClaw runtime into `/etc/verdify/slack`.
- Updated `/home/jason/.openclaw/openclaw.json` so `channels.slack.botToken` and `channels.slack.appToken` are file-backed SecretRefs, not inline token strings.
- Restarted `openclaw-gateway.service`; Slack channel probe stayed healthy.
- Recreated `hermes-iris`; the container now sees `/opt/data/slack.yaml`, `VERDIFY_SLACK_CONFIG=/opt/data/slack.yaml`, and a read-only `/etc/verdify/slack` mount. The Hermes uid can read both Slack token files.
- Restarted `verdify-ingestor`; the service is active and using the root `slack.yaml`.
- Posted a root-config smoke message to `#greenhouse` as `Iris` with `:seedling:` and deleted it successfully.
- Opened a Slack Socket Mode connection from the local app-token file successfully.

Recent `#greenhouse` history still showed alert posts from the `Orbit` bot profile.
That confirms the live posting drift is in the Web API posting path that still uses
Orbit credentials, not in Hermes Slack monitoring.

## Cron And Service Inventory

- Active user crontab has two Slack-related entries: `checklist-to-slack.sh` at 13:00 and `slack-channel-archive.py` every six hours.
- Active systemd Verdify timers do not post to Slack directly.
- `verdify-ingestor.service` owns the recurring alert, planner verification, midnight watch, and forecast-alert Slack paths through its async task loop.
- Legacy `scripts/alert-monitor.py` still exists for one-shot/manual compatibility, but the active path is the ingestor task loop.

## Associated Repo Findings

- `/mnt/iris/planner/planner_graph/clients/slack.py` is a stub adapter. It records `sent: false` and does not call Slack.
- `/mnt/iris/verdify-vault` contains Slack-derived observation/archive content but no active Slack posting code.
- `/mnt/agents/agents.json` maps `iris` and `iris-planner` to `#greenhouse`; Orbit and orbit-dev use separate fleet channels.
- `/home/jason/.openclaw/openclaw.json` is the runtime Iris OpenClaw Slack surface. Keep it aligned with this file and keep Slack credentials in the local token files named in `slack.yaml`.
- `/mnt/jason/orbit/.openclaw/openclaw.json` is Orbit's OpenClaw Slack surface and should remain separate from Verdify greenhouse posting.

## Configuration Rules

1. New Slack integrations must import `slack_config.py` or call `scripts/slack-post.py`.
2. Do not hard-code Slack token paths or channel IDs in new code.
3. Do not store Slack token values in tracked files. Store only secret file references.
4. Prefer the Iris bot token for greenhouse operations. Use Orbit Slack credentials only for Orbit fleet channels.
5. If a runtime needs environment variables, derive them from `slack.yaml` during deploy and document the handoff here.

## Cutover Checks

After provisioning `/etc/verdify/slack/iris_slack_bot_token.txt`, restart `verdify-ingestor` and send one smoke post through:

```bash
/srv/greenhouse/.venv/bin/python3 /srv/verdify/scripts/slack-post.py --text "Iris Slack config smoke test"
```

The message should appear in `#greenhouse` as Iris.
