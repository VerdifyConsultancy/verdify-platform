# Agent: `genai`

Iris the planner agent, the MCP tool surface, prompt templates, plan scoring and evaluation, and the feedback loop that makes planning learn over time.

## Owns

- `ingestor/iris_planner.py` — planner invocation (lives in ingestor/ for deploy; content is genai's)
- `mcp/server.py` — FastMCP tool definitions (climate, scorecard, equipment_state, forecast, get_setpoints, set_tunable, set_plan, plan_evaluate, crops, observations, alerts, lessons_manage)
- `templates/` — Jinja2 planner prompt + reference docs the planner reads
- `config/ai_config.py` or equivalent — model selection, temperature, tool whitelists
- `scripts/smoke-sprint*.py` — end-to-end planner feedback loop tests
- `verdify_schemas/tunables.py` **proposal authority** — coordinator merges, but genai drives what tunables exist
- `verdify_schemas/plan.py`, `lessons.py` — same proposal authority for plan + lesson shapes
- Systemd unit: `verdify-mcp.service`, `verdify-plan-publish.*`

## Does not own

- How the planner's output reaches the ESP32 (`ingestor` dispatcher)
- How plans are rendered into the vault (`web` — vault writers). The
  `scripts/generate-*.py` files (`generate-daily-plan`, `generate-forecast-page`,
  `generate-lessons-page`, `generate-plans-index`, `generate-observation-embeddings`)
  physically live in the genai tree but are `web` scope — genai owns the Pydantic
  data models they consume, web owns the rendering.
- The DB tables that store plan history (coordinator — migrations)

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `ingestor` | Planner's tunable set changes | Genai updates `ALL_TUNABLES` + `Plan` schema first (via coordinator); ingestor dispatcher auto-picks it up through `SetpointChange` validation |
| `firmware` | Planner needs a tunable the firmware doesn't expose yet | Request firmware add it, then genai adds it to `ALL_TUNABLES` — serialized |
| `web` | Vault page wants a new planner-derived field | Web reads from DB views; genai adds a view + schema, coordinator migrates |
| `coordinator` | Model swap, prompt rewrite affecting cost/latency, new MCP tool | Review before deploy — these change the agent's behavior economically and operationally |

## Gates

- Planner dry-run must succeed (`make planner-dry`) before a prompt change ships.
- Plan feedback loop smoke (`scripts/smoke-feedback-loop.py`) must pass end-to-end against the live stack.
- Token/cost sanity check: if a prompt change inflates average plan-cycle cost by >20%, coordinator reviews.

## Ask coordinator before

- Switching planner models or routing policy (local ↔ cloud, Gemma/version bumps, or behavior-impacting model changes)
- Adding a new MCP tool (affects Iris's action space — review for safety + cost)
- Changing the scorecard formula or plan evaluation rubric (affects how performance is measured across sprints)
- Rewriting the core prompt template in a way that changes plan structure (downstream renderers depend on plan shape)

## Recent arc (pre-agent-org)

- Sprint 20: Unified plan schema + feedback loop + manifestation
- Sprint 21: Pydantic coverage across MCP boundary (planner → MCP tool → DB all validated)
- Sprint 22: API response_model, RELATIONSHIPS.md
- Sprint 23 (in flight): MCP record_harvest/record_treatment column bug fix + `HarvestCreate`/`TreatmentCreate` input envelopes

See `docs/backlog/genai.md` for next work.
