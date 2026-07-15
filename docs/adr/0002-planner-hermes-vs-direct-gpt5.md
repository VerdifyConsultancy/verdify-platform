# ADR 0002 — AI planner orchestration: Hermes (hermes-iris) + GPT-5 + MCP vs a direct GPT-5 prompt

- **Status:** Accepted — keep Hermes for production; the direct-GPT-5.5 path is the documented, low-risk rollback/simplification.
- **Date:** 2026-06-17
- **Model-profile amendment:** 2026-07-15 — select GPT-5.6 Sol xhigh for Hermes; production activation remains separately gated.
- **Owner lane:** verdify-platform. **Closes:** #346 AC1 (L4 — AI Planner and Tunables).
- **Refs:** `hermes/iris/config.yaml`, `mcp/server.py`, `verdify_schemas/tunable_registry.py`, `ingestor/iris_planner.py`, `ingestor/tasks/heartbeat.py`, `planner_graph/`, `docs/iris-planner-contract.md` (v1.5 — ledger/correlation semantics active; the Gemma/OpenClaw gateway details are historical), #214/#315 (planner pipeline health), `docs/agents/genai.md`.

> Documentation artifact. No secrets/keys/PSKs — credentials referenced by name only.

> **2026-07-15 profile amendment:** repo source now selects
> `model.default: gpt-5.6-sol` and `agent.reasoning_effort: xhigh`. The deployed
> Hermes v0.13 runtime reads effort only from `agent.reasoning_effort`; the old
> `model.reasoning_effort: high` placement was ignored and therefore used the
> transport's medium default despite the former `high` audit label. The inactive
> `planner_graph` rollback remains deliberately pinned to GPT-5.5/medium until it
> receives its own parity evaluation. This changes the model profile, not the
> MCP/registry safety boundary or the orchestrator decision.

---

## 1. Context — the question

The greenhouse has an AI planner that runs over a 72-hour horizon and adjusts **bounded tunables** (never targets, rails, or FSM logic). The open question (Lane 4 brief): *"It is unclear whether Hermes adds value or whether a direct GPT-5 prompt with structured data would be simpler."*

**Production architecture (the thing this ADR ratifies):** the ingestor fires solar/forecast triggers (`ingestor/tasks/heartbeat.py` `_compute_milestones` + the `PLANNER_TRIGGER_MATRIX`), builds a 72h context pack, and dispatches to the **Hermes gateway** (`verdify-hermes-iris`, ns `verdify-prod`). The repo-selected Hermes profile runs **OpenAI GPT-5.6 Sol with `agent.reasoning_effort=xhigh`** and an **MCP-only tool allowlist** (`hermes/iris/config.yaml`: `model.default: gpt-5.6-sol`, `agent.reasoning_effort: xhigh`, 30-turn agentic loop, `mcp_servers` → Verdify MCP). GPT-5.6 Sol reads ~6–10 MCP tools in sequence, then writes via `set_plan` / `set_tunable` / `acknowledge_trigger` (`mcp/server.py`). Every write is validated against `verdify_schemas/tunable_registry.py` (the `planner_pushable` gate + per-tunable min/max) before it reaches `setpoint_plan` / `plan_journal`; the dispatcher pushes the resulting bounded waypoints to the device. Production activation of a model-profile change is a separate reviewed deployment step.

**The two options:**
- **(A) Hermes-orchestrated** GPT-5.6 Sol xhigh + MCP (selected): a managed agent gateway provides the multi-turn tool-use loop, session/conversation memory (kanban), the scheduler, and auth.
- **(B) Direct GPT-5.5** from the ingestor: on each trigger the ingestor calls the OpenAI API with a structured prompt, runs the function-calling tool loop itself, and applies the same registry-validated writes. Fewer moving parts (no Hermes deployment, no kanban DB).

## 2. Decision

**Keep Hermes (option A) as the production planner orchestrator.** Model selection is independently versioned: Hermes now selects GPT-5.6 Sol xhigh, while the inactive direct rollback remains GPT-5.5/medium pending parity evaluation. The orchestration decision and the MCP/registry safety boundary do not depend on those profile versions.

**The safety boundary is the MCP server + `tunable_registry`, NOT Hermes.** The bounded-write contract (only `planner_pushable=True` tunables, clamped to registry min/max; targets/`crop_band_anchors` rejected via `push_owner='band'`; safety rails/FSM not exposed) is enforced at the MCP/registry boundary and is **independent of the orchestrator**. So this decision carries low risk and is cleanly reversible.

## 3. Rationale

1. **It works and is already built.** Hermes + MCP is live and producing plans. Hermes's *net* value here is specifically the agentic multi-turn tool loop (`max_turns=30`), the MCP include/exclude tool allowlist, and the `/v1/runs` session API the ingestor fire-and-forgets (with `hermes_run_id` correlated into `plan_delivery_log`). Verdify deliberately disables Hermes's memory / skills / cron and keeps its own TimescaleDB ledger, so a direct path would have to re-implement the tool loop + allowlist + turn budget + retries, not those extras.
2. **Safety doesn't depend on it.** Because the write contract lives in MCP + the registry, swapping orchestrators never weakens the "AI cannot touch targets/rails/FSM" guarantee (see ADR §5 invariant + the AC4 contract test).
3. **Model version is independent.** Both paths use the GPT-5 family, but their defaults may differ while one is inactive. A switch to the direct path therefore requires an explicit model-parity decision in addition to changing orchestration.
4. **The cost is operational, and bounded.** Hermes's overhead is a separate deployment + a kanban/session store. Its one production incident class was *storage* (the 2026-06-08 Longhorn-EIO that took planning dark, #315) — now mitigated by moving `verdify-hermes-iris-data` to `node-local-temp-rwo` (off Longhorn). The agentic value (multi-turn tool use + memory) currently outweighs the overhead.

## 4. Consequences + rollback / simplification path

- **Rollback to (B) is pre-scaffolded.** `planner_graph/` (LangGraph) already exists as the sanctioned home for a direct GPT-5.5/medium function-calling loop driven by the ingestor's existing triggers + context pack, writing through the **same** MCP tools / registry-validated path. It remains intentionally unchanged by the 2026-07-15 Hermes profile update; activating it requires an explicit decision to retain GPT-5.5/medium or a separate GPT-5.6 parity evaluation. Switching orchestrators otherwise reuses the entire safety boundary, the ledger (`plan_journal`), and the trigger ledger unchanged. (Note: the legacy OpenClaw gateway is decommissioned and is **not** a rollback target; in-repo Hermes config/prompt iteration is the in-place tuning path, the LangGraph direct loop is the forward alternative.)
- **Trigger to switch to direct GPT-5.5:** recurring Hermes operational failure (storage EIO, kanban corruption, gateway downtime causing missed planning cycles beyond the SLA), **or** evidence the multi-turn agentic loop is unnecessary (a single structured prompt+response covers the planner's reasoning). At that point, stand up the direct loop in `planner_graph`, point the ingestor at it, and retire `verdify-hermes-iris`.
- **No lock-in:** because the contract is orchestrator-independent, this is reversible within a day, not a rewrite.

## 5. Invariant carried by this decision (the bounded-write guarantee)

Regardless of orchestrator, the planner **may** set only `planner_pushable=True` tunables within registry min/max (the bias / hysteresis / runtime-preference / forecast-offset surface — see `docs/planner/planner-io-schema.md` §3). It **must not** set: deterministic target temperature / `crop_band_anchors`, plant-stress thresholds, hard safety rails (`safety_max`/`safety_min`/`safety_vpd_*`), emergency override, or core FSM logic. This is enforced at the MCP/registry boundary and asserted by the AC4 contract test (`verdify_schemas/tests/test_tunable_registry.py::TestPlannerWriteContractLockout`).

**Live runtime proof (2026-06-17, `verdify-db-0`):** across all of `setpoint_plan` written by `source='iris'`, **0** band-curve-anchor rows and **0** hard-safety-rail rows exist; the planner *did* try to write the FSM switch `sw_fsm_controller_enabled` 260 times (Apr–May), and `set_plan`'s `FORCED_ON_SWITCH_PARAMS` rewrite forced **all 260 to value `1.0`** — the planner has never been able to disable the deterministic controller. The contract is real, not just declared.
