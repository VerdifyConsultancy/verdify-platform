# Hermes — Iris profile

Canonical config for the `hermes-iris` agent container that replaces OpenClaw
as Verdify's sole planner gateway. OpenClaw was decommissioned on 2026-05-11;
Hermes is now the only production route for Iris planning cycles.

## Files

- `config.yaml` — active Hermes profile: Cortex's OpenAI-compatible `custom`
  provider on `llm.primary.longctx`, with 98,304-token context, a 16,384-token
  client output fence, medium reasoning, and an MCP-only tool surface. The
  allowlist tightens the toolset to Verdify's MCP server; `query` (raw SQL) is
  excluded.
- `SOUL.md` — durable identity, authoritative-source priority order,
  behavioral contract. Short by design — per-cycle context comes from
  `gather-plan-context.sh` via the ingestor.
- `slack.yaml` — copied from the Verdify repo root into the runtime dir by
  `make hermes-deploy-config`; the container sees it as `/opt/data/slack.yaml`
  through `VERDIFY_SLACK_CONFIG`.
- Runtime state — **not in git**. Lives at `/var/lib/verdify/hermes/iris`
  and is bind-mounted as `/opt/data` in the container.
- Runtime secrets — **not in git**. Live at `/etc/verdify/hermes-iris.env`
  and hold `OPENAI_API_KEY`, `VERDIFY_MCP_TOKEN`, `HERMES_IRIS_API_KEY`.
  Slack token contents stay in `/etc/verdify/slack`, mounted read-only into
  the container at the same path.

## Deployment

```bash
# One-time host setup
sudo mkdir -p /var/lib/verdify/hermes/iris /etc/verdify
sudo install -m 640 -o root -g "$(id -gn)" /path/to/hermes-iris.env \
  /etc/verdify/hermes-iris.env

# Copy versioned config into the host runtime
make hermes-deploy-config

# Bring up the service
docker compose --profile hermes up -d hermes-iris

# Smoke
curl -fsS http://127.0.0.1:8642/health

curl -X POST http://127.0.0.1:8642/v1/runs \
     -H "Authorization: Bearer $HERMES_IRIS_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"input": "ping", "session_id": "smoke-test"}'
```

## Updating Config

```bash
make hermes-restart
```

`make hermes-restart` syncs the versioned config/SOUL files into
`/var/lib/verdify/hermes/iris` and recreates the container through the Hermes
compose profile. The container is kept alive by Docker's `restart: unless-stopped`
policy; there is no host-side systemd unit for `hermes-iris`.

## Roll Forward

OpenClaw rollback is gone. Planner regressions are fixed in place by editing
the Hermes config, Iris prompts, MCP allowlist, or planner context pack, then
deploying via `make hermes-restart` and validating through `plan_delivery_log`.
