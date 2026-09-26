# Hermes — Iris profile

Canonical config for the `hermes-iris` agent container that replaces OpenClaw
as Verdify's sole planner gateway. OpenClaw was decommissioned on 2026-05-11;
Hermes is now the only production route for Iris planning cycles.

## Files

- `config.yaml` — repo-selected Hermes profile: Cortex's OpenAI-compatible
  98K text route `llm.primary.longctx`, explicit 98,304/8,192 context/output
  budgets, tool-use guidance, and an MCP-only tool surface. Merging config does
  not activate it; live rollout is separately gated. The allowlist tightens the
  toolset to Verdify's MCP server; `query` (raw SQL) is excluded.
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

For k3s, the embedded profile is
`deploy/k8s/components/hermes-iris/hermes-config.yaml`. Its
`verdify.io/hermes-profile-revision` pod-template annotation is the first 12
hex characters of the embedded profile's SHA-256. The drift test requires the
annotation and profile to move together, so an ArgoCD sync recreates the
singleton pod and the init container reseeds the PVC before Hermes starts.

## Routing audit — 2026-08-25

- The canonical and embedded live profiles select provider `custom`, endpoint
  `https://cortex.vallery.net/v1`, text-only model alias
  `llm.primary.longctx`, `model.context_length: 98304`,
  `model.max_tokens: 8192`, `agent.reasoning_effort: medium`, explicit tool-use
  guidance, and `max_turns: 30`. Cortex publishes the 98,304-token context,
  16,384-token default output cap, and medium reasoning default in route
  metadata. The explicit context and narrower 8,192-token client output values
  prevent Hermes's unknown-model fallback and fail a runaway turn sooner. The
  bundled custom provider does not forward Hermes's reasoning scalar, so the
  effective medium behavior comes from Cortex's verified vLLM 0.27.1 default.
  The explicit tool-use boolean remains defense-in-depth for this
  tool-mandatory workload.
- The dark experiment profile selects the same route and agent limits while
  retaining its narrower, server-bound experiment MCP audience.
- The API Deployment supplies
  `hermes-iris/cortex:llm.primary.longctx` as the current public route label;
  it deliberately keeps attribution route-scoped while the delivery ledger
  lacks immutable per-run provider metadata.
  Historical delivery rows deliberately omit a model label until provider/model
  identity is persisted per run, so a route change cannot rewrite their
  provenance.
- These are source and rollout declarations, not evidence of a completed live
  sync. Cortex route acceptance is separately proven by public metadata,
  bounded inference, and Hermes-style auto-tool probes. Verdify activation is
  proven only after ArgoCD is Synced + Healthy, a new Hermes pod has the matching
  profile revision annotation, and a required cycle writes a correlated plan.

## Roll Forward

OpenClaw rollback is gone. Planner regressions are fixed in place by editing
the Hermes config, Iris prompts, MCP allowlist, or planner context pack, then
deploying via `make hermes-restart` and validating through `plan_delivery_log`.
