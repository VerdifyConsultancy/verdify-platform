# Agent: `genai`

Iris the planner agent, the MCP tool surface, prompt templates, plan scoring and evaluation, and the feedback loop that makes planning learn over time.

## Owns

- `ingestor/iris_planner.py` — planner invocation (lives in ingestor/ for deploy; content is genai's)
- `mcp/server.py` — FastMCP tool definitions (climate, scorecard, equipment_state, forecast, get_setpoints, set_tunable, set_plan, plan_evaluate, crops, observations, alerts, lessons_manage)
- `templates/` — Jinja2 planner prompt + reference docs the planner reads
- `config/ai_config.py` or equivalent — model selection, temperature, tool whitelists
- `scripts/smoke-sprint*.py` — end-to-end planner feedback loop tests
- `verdify_schemas/tunables.py` — planner tunable contract
- `verdify_schemas/plan.py`, `lessons.py` — plan and lesson contracts
- Systemd unit: `verdify-mcp.service`, `verdify-plan-publish.*`

## Does not own

- How the planner's output reaches the ESP32 (`ingestor` dispatcher)
- How plans are rendered into the vault (`web` — vault writers). The
  `scripts/generate-*.py` files (`generate-daily-plan`, `generate-forecast-page`,
  `generate-lessons-page`, `generate-plans-index`, `generate-observation-embeddings`)
  physically live in the genai tree but are `web` scope — genai owns the Pydantic
  data models they consume, web owns the rendering.
- The DB tables that store plan history (shared migration surface)

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `ingestor` | Planner's tunable set changes | Update `ALL_TUNABLES` + `Plan` first; the dispatcher consumes it through `SetpointChange` validation |
| `firmware` | Planner needs a tunable the firmware doesn't expose yet | Request firmware add it, then genai adds it to `ALL_TUNABLES` — serialized |
| `web` | Vault page wants a new planner-derived field | Add the view, migration, and schema before the renderer consumes it |
| runtime | Model swap, cost/latency-sensitive prompt rewrite, or new MCP tool | Measure cost, latency, behavior, and action-surface changes before deploy |

## Required checks

- Planner dry-run must succeed (`make planner-dry`) before a prompt change ships.
- Plan feedback loop smoke (`scripts/smoke-feedback-loop.py`) must pass end-to-end against the live stack.
- Measure token/cost impact; investigate any prompt change that inflates average
  plan-cycle cost by more than 20%.

## Cross-component checks

- Benchmark planner model or routing-policy changes for behavior, latency, and cost.
- Validate the safety and cost effects of each new MCP tool.
- Version scorecard or evaluation-rubric changes and update their consumers.
- Update downstream renderers with any core prompt output-shape change.

## Recent arc (pre-agent-org)

- Sprint 20: Unified plan schema + feedback loop + manifestation
- Sprint 21: Pydantic coverage across MCP boundary (planner → MCP tool → DB all validated)
- Sprint 22: API response_model, RELATIONSHIPS.md
- Sprint 23 (in flight): MCP record_harvest/record_treatment column bug fix + `HarvestCreate`/`TreatmentCreate` input envelopes

Use GitHub issues on `VerdifyConsultancy/verdify-platform` for next work; the
old `docs/backlog/genai.md` file is archived in
`/Users/jason/Orbit/context_dump/verdify-platform/`.
