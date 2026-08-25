# Iris Planner Contract v1.5

> Historical contract: this document describes the pre-2026-05-11 OpenClaw /
> local-Gemma planner path. Production planning routes through Hermes
> (`hermes-iris`) with the MCP-only tool allowlist in `hermes/iris/config.yaml`.
> The live profile is Cortex `custom:llm.primary.longctx` with medium reasoning,
> a 98,304-token context contract, and a 16,384-token output fence. Use
> `plan_delivery_log.hermes_run_id` as the active gateway correlation field.
>
> **For the current planner I/O schema, allowed-tunables/bounds, write-contract
> lockout, and decision ledger, see `docs/planner/planner-io-schema.md`** (the L4
> #346 source-of-truth). The orchestration decision is `docs/adr/0002-planner-hermes-vs-direct-gpt5.md`.
> The ledger / exact-trigger-correlation / registry-validation semantics in *this*
> doc remain active; the gateway/model details below are historical.

**Status:** historical v1.5 by coordinator, 2026-05-04. It ratified the local-first OpenClaw/Gemma path that was superseded on 2026-05-11 by Hermes `hermes-iris`. The live Hermes profile is Cortex `custom:llm.primary.longctx` with medium reasoning and a 16,384-token output fence. The trigger-ledger, exact-correlation, lifecycle-status, and registry-validation rules remain active; the gateway/model details below are historical.

**v1.5 change log (2026-05-04):** makes local Gemma4-on-cortext the default production planner path, defines the trigger ledger shape before more code changes, and closes the unsafe correlation rule: new planner rows correlate to triggers by exact `trigger_id` only. Legacy time-window fallback is permitted only for historical rows where both sides lack a UUID.

**v1.4 change log (2026-04-19):** reconciled §2.D with pre-existing `plan_delivery_log` table from ingestor Sprint 24.6 F14 (migration 092). Contract no longer proposes a parallel `plan_delivery_log` — the existing table is **extended** with the contract-required columns (`trigger_id`, `instance`, `acked_at`, `status`). Column names in §2.D / §2.F use the existing migration's names (`delivered_at`, `event_type`, `resulting_plan_id`) rather than the v1.3 synonyms.

**Applies to historically:** the default `iris-planner` OpenClaw agent (local Gemma4 on cortext), an explicit cloud escalation agent/session if configured, future peers, every trigger type (SUNRISE, SUNSET, MIDNIGHT, TRANSITION, FORECAST, DEVIATION, HEARTBEAT, MANUAL), and every agent that touched the planning path. Current production applies these ledger semantics to Hermes `hermes-iris`.

## 0. Why this exists

Today's failure mode exposed the gap: a trigger was POSTed to `/hooks/agent`, the gateway returned 200 OK, and nothing happened downstream for 18h. No agent owned the "did Iris actually write a plan?" question because there was no contract saying who owes what in which direction. This document names every layer, its owner, its inputs, its outputs, its invariants, its SLAs, and its failure signals — so any future drop is detected within one trigger cycle, not one operator's notice.

## 1. Path of a planning request

```
ingestor.planning_heartbeat()          (ingestor)
  │ build context + prompt + planner_path="local_gemma4" by default
  ▼
iris_planner.send_to_iris()            (genai: content) (ingestor: config/env)
  │ POST http://127.0.0.1:18789/hooks/agent
  │ Authorization: Bearer $OPENCLAW_TOKEN
  │ X-Planner-Instance: local_gemma4 | cloud_escalation
  │ X-Trigger-Type: SUNRISE | ... | DEVIATION
  │ X-Trigger-Id: <uuid>                  ← new; end-to-end correlation
  │ body: { message, agentId: "iris-planner", sessionKey, wakeMode, deliver: false }
  ▼
OpenClaw gateway                       (iris-dev: config)
  │ validate bearer, validate sessionKey prefix, route to agent session
  ▼
Iris agent session                     (iris-dev: profile / openclaw: runtime)
  │ local Gemma4 receives prompt, reasons, emits MCP tool calls
  ▼
mcp.server.set_plan(), set_tunable()   (genai)
  │ extract X-Planner-Instance + X-Trigger-Id from MCP request context
  │ validate registry min/max, write row to plan_journal / setpoint_plan
  ▼
TimescaleDB                            (coordinator: schema)
  │ trigger ledger + plan rows share trigger_id
  ▼
ingestor.setpoint_dispatcher()         (ingestor)
  │ read active plan waypoints → push to ESP32 every 5min
  ▼
alert_monitor.planner_stale_rule()     (ingestor)
  │ if no new plan_journal row with matching trigger_id within SLA → Slack alert
```

During the v1.4→v1.5 migration, existing code and historical rows may still
use `instance='local'` for Gemma-backed planning and `instance='opus'` for
cloud planning. New operator docs and code should use the semantic names
`local_gemma4` and `cloud_escalation`; compatibility readers must keep the
legacy labels readable until historical dashboards are migrated.

## 2. Contract by interface

### A. ingestor → OpenClaw gateway (POST /hooks/agent)

**Owner of call site:** ingestor (`tasks.py::planning_heartbeat`).
**Owner of call implementation:** genai (`iris_planner.py::send_to_iris`).
**Owner of receiver:** iris-dev (`~/.openclaw/openclaw.json` hooks block).

**Required request fields:**
| field | location | semantics |
|---|---|---|
| `Authorization` | header | `Bearer $OPENCLAW_TOKEN` — must match `openclaw.json:hooks.token` |
| `X-Planner-Instance` | header | `local_gemma4` \| `cloud_escalation` for new sends; legacy `local` \| `opus` accepted during migration |
| `X-Trigger-Type` | header | `SUNRISE` \| `SUNSET` \| `MIDNIGHT` \| `TRANSITION` \| `FORECAST` \| `DEVIATION` \| `HEARTBEAT` \| `MANUAL` |
| `X-Trigger-Id` | header | UUID generated by caller; unique per trigger instance |
| `agentId` | body | `iris-planner` for local Gemma4; cloud escalation uses an explicitly configured cloud agent |
| `sessionKey` | body | normal local triggers use `agent:iris-planner:main:trigger:<trigger_id>`; operator TUI may still use `agent:iris-planner:main`; prefix must be in `openclaw.json:hooks.allowedSessionKeyPrefixes` |
| `wakeMode` | body | `now` \| `next-heartbeat` |
| `deliver` | body | `false` (no Slack echo of the raw prompt) |
| `message` | body | rendered prompt; Gemma4-sized local context pack by default; cloud may receive extended reference material |

**Response contract (from gateway):**
- `200 OK` = message accepted and queued/delivered to session. Does **NOT** mean Iris read it, acted on it, or wrote a plan.
- `401` = bad bearer. ingestor alerts Slack.
- `403` = sessionKey prefix not in allowlist. ingestor alerts Slack.
- `404` = agentId not resolvable. ingestor alerts Slack.
- `5xx` = gateway fault. Retry once, then alert.

**Invariant:** every trigger gets one trigger-ledger row with a non-null
`trigger_id` before or immediately after gateway delivery. For v1.5 rows,
delivery-to-plan correlation is by exact UUID only.

### B. OpenClaw gateway → Iris agent session

**Owner:** iris-dev (config) + openclaw (runtime).

**Profile requirements (per agent in `openclaw.json:agents.list`):**
- The production `iris-planner` profile points at the local Gemma4 provider on cortext with a **correct contextWindow**. The rendered prompt + context + tool-use round-trips must fit within `contextWindow` with ≥20% headroom.
- Any cloud profile is named and routed as escalation, not as the ordinary `iris-planner` target.
- `agentDir` must contain a `models.json` with the same contextWindow as the top-level config — values MUST match.
- MCP server access is inherited from global `mcp.servers` (Verdify MCP at `http://127.0.0.1:8000/mcp`).
- `heartbeat.every = "0m"` (event-driven, not heartbeat-driven).

**Invariant:** a trigger delivered to an agent session MUST either result in an MCP tool call OR a human-readable "I'm declining this because X" reply visible in the session transcript. Silent drops are a contract violation.

### C. Iris agent session → MCP server

**Owner:** genai (tool surface + prompt content guiding tool use).

**Required MCP call shape for a planning cycle:**
- `set_plan(plan_id=..., hypothesis=..., transitions=..., experiment=..., expected_outcome=..., trigger_id=..., planner_instance=...)` — exactly once per trigger that changes the tactical posture. Either called with a real plan, or the agent must call `acknowledge_trigger(trigger_id=..., reason=..., planner_instance=...)` to record "read but no plan needed."
- Optional: zero or more `set_tunable(parameter=..., value=..., reason=..., trigger_id=..., planner_instance=...)` calls.

**MCP request context (new, requires FastMCP header passthrough):**
- `X-Planner-Instance: local_gemma4 | cloud_escalation` — forwarded from the /hooks/agent call through to MCP calls made in that session. Stamped on every DB row. Legacy `local` and `opus` remain readable during migration.
- `X-Trigger-Id: <uuid>` — same. Enables ingestor to verify end-to-end that THIS trigger produced THIS plan row.
- `X-Heartbeat-Readonly: true` (optional) — present only on HEARTBEAT trigger sends during the first-week safety window (see §2.G). When present, MCP rejects `set_tunable` and `set_plan` with HTTP 403 "heartbeat readonly mode". `acknowledge_trigger` remains allowed.

**Validation gate:** `set_plan` and `set_tunable` must reject any planner-owned
value outside `verdify_schemas.tunable_registry` `min`/`max` before writing
`setpoint_plan`. The error must name the offending parameter, requested value,
registry range, and nearest safe value so Iris can retry with a valid plan.

**Invariant:** MCP writes are idempotent on `plan_id`. A duplicate `set_plan` with the same `plan_id` is a **409 from MCP with a clear error**; Iris must generate a new `plan_id` per cycle. *Note: today's `set_plan` silently overwrites; making this actually return 409 is a genai-owned change in the MCP PR, not inherited behavior.*

**Server-side audience authorization (#585, audit §8.8, Lane D tranche 1).**
The `Authorization: Bearer` header Hermes already sends is now validated by the
MCP server itself; the client-side `tools.include` list and the NetworkPolicy
are no longer the only boundaries. Every tool call resolves its bearer token to
an **audience** (constant-time comparison) and is checked against the
server-side `TOOL_AUDIENCES` inventory in `mcp/server.py`:

- `iris` — exactly the 23 tools in the hermes-config `tools.include` list
  (drift-guarded by `tests/test_mcp_audience_auth.py`); current behavior.
- `experiment` — the blinded experiment planner arm: qualified
  climate/forecast/crop/topology reads + `acknowledge_trigger` only;
  treatment-revealing reads (`get_setpoints`, `plan_status`, `history`,
  scorecards, lessons) and all ordinary writes denied.
  `policy_template_propose` joins this audience in Lane C/D tranche 2.
- `admin` — operator/debug credential; all tools.

Config: `VERDIFY_MCP_TOKEN_IRIS` / `VERDIFY_MCP_TOKEN_EXPERIMENT` /
`VERDIFY_MCP_TOKEN_ADMIN` on the MCP server (the iris value is the same secret
hermes-config sends as `${VERDIFY_MCP_TOKEN}`), and
`VERDIFY_MCP_AUTH_MODE=off|log|enforce` (default `off`). Modes and required
rollout order:

1. `off` (default) — authorization bypassed; byte-identical pre-#585 behavior.
2. `log` — **prod runs this first**: would-be denials are emitted as
   structured `mcp_tool_authz_denial` JSON log lines (never token values) but
   nothing is blocked. Soak until the denial log is quiet for the iris
   audience.
3. `enforce` — denials reject the tool call with a clear tool error; a
   missing/unknown token denies everything (fail closed). An unrecognized
   mode value also behaves as `enforce`. **`enforce` must be live before any
   experiment assignment is armed.**

`/readyz` reports `auth_mode`, `auth_audiences_configured` (names only), and
`auth_misconfigured`; `enforce` with an empty token registry reports not-ready
instead of serving a fully bricked tool surface.

### D. MCP server → TimescaleDB

**Owner:** genai (MCP code) + coordinator (schema).

**Trigger ledger shape (v1.5 logical contract).**

The trigger ledger is the source of truth for expected planning work. It is
currently backed by `plan_delivery_log`; future migrations may rename or
normalize it, but consumers should reason about this logical shape:

| field | required | semantics |
|---|---:|---|
| `trigger_id` | yes for v1.5 | UUID generated once per trigger; primary correlation key |
| `greenhouse_id` | yes | tenant / greenhouse scope; `vallery` in production today |
| `event_type` | yes | `SUNRISE`, `SUNSET`, `MIDNIGHT`, `TRANSITION`, `FORECAST`, `DEVIATION`, `HEARTBEAT`, or `MANUAL` |
| `event_label` | no | human label such as `Peak Stress`, `New forecast data`, or the deviation summary |
| `origin` | target | scheduler source: `solar`, `fixed_boundary`, `forecast_refresh`, `forecast_deviation`, `manual`, or `context_failure` |
| `expected_at` | target | when the trigger should have fired, even if ingestor was down |
| `delivered_at` | yes when attempted | when OpenClaw accepted or rejected the POST |
| `due_by` | target | SLA deadline computed from trigger type and planner path |
| `session_key` | yes when attempted | OpenClaw session key that received the prompt |
| `instance` | yes | planner path; v1.5 semantic values are `local_gemma4` or `cloud_escalation`; legacy `local`/`opus` accepted |
| `planner_model` | target | concrete model/provider label, e.g. `gemma4:cortext` |
| `gateway_status` / `gateway_body` | no | OpenClaw delivery result |
| `status` | yes | `pending`, `acked`, `plan_written`, `timed_out`, or `delivery_failed` |
| `acked_at` / `ack_reason` | no | no-op acknowledgement time and reason |
| `resulting_plan_id` / `plan_written_at` | no | plan row this trigger produced |
| `validation_status` / `validation_errors` | target | whether MCP accepted, rejected, or clipped the proposed plan |
| `context_digest_id` | target | versioned digest of the historical/forecast/site context used by the planner |

**Lifecycle invariant:** every trigger resolves to exactly one terminal state:
`plan_written`, `acked`, `delivery_failed`, or `timed_out`. A row with
`status='pending'` past `due_by` is a planner outage, not a harmless backlog.

**Correlation invariant:** for v1.5 rows, `resulting_plan_id` may be set only
when `plan_journal.trigger_id = plan_delivery_log.trigger_id`. The old
time-window fallback is allowed only for historical rows where both
`plan_delivery_log.trigger_id` and `plan_journal.trigger_id` are NULL.

**Current schema: extend existing `plan_delivery_log` (from migration 092 / sprint 24.6) + add instance/trigger columns to plan tables (coordinator migration).**

`plan_delivery_log` is the established audit table, populated today by `ingestor/tasks.py::planning_heartbeat` immediately after `send_to_iris` returns. It already has `delivered_at`, `event_type`, `event_label`, `session_key`, `wake_mode`, `gateway_status`, `gateway_body`, `resulting_plan_id`, `plan_written_at`, `greenhouse_id`. The v1.4 contract extends it rather than creating a parallel `plan_delivery_log` (v1.3 spec'd one before I'd read migration 092 — my miss; web agent flagged the overlap 2026-04-19).

```sql
BEGIN;

-- Extend plan_delivery_log with contract-required columns
ALTER TABLE plan_delivery_log ADD COLUMN IF NOT EXISTS trigger_id UUID UNIQUE;
ALTER TABLE plan_delivery_log ADD COLUMN IF NOT EXISTS instance   TEXT;
ALTER TABLE plan_delivery_log ADD COLUMN IF NOT EXISTS acked_at   TIMESTAMPTZ;
ALTER TABLE plan_delivery_log ADD COLUMN IF NOT EXISTS status     TEXT DEFAULT 'pending';

-- Status domain constraint
ALTER TABLE plan_delivery_log DROP CONSTRAINT IF EXISTS plan_delivery_log_status_check;
ALTER TABLE plan_delivery_log ADD CONSTRAINT plan_delivery_log_status_check
    CHECK (status IN ('pending','acked','plan_written','timed_out','delivery_failed'));

CREATE INDEX IF NOT EXISTS plan_delivery_log_trigger_id_idx ON plan_delivery_log(trigger_id);
CREATE INDEX IF NOT EXISTS plan_delivery_log_status_idx     ON plan_delivery_log(status, delivered_at);

-- Backfill existing rows with derived status + 'iris-planner' instance label
UPDATE plan_delivery_log SET
    instance = 'iris-planner',
    status = CASE
        WHEN resulting_plan_id IS NOT NULL                             THEN 'plan_written'
        WHEN gateway_status IS NOT NULL
             AND gateway_status NOT BETWEEN 200 AND 299                THEN 'delivery_failed'
        WHEN delivered_at < now() - interval '2 hours'                 THEN 'timed_out'
        ELSE 'pending'
    END
WHERE instance IS NULL;

-- Instance + trigger correlation on plan tables (unchanged from v1.3)
ALTER TABLE plan_journal       ADD COLUMN IF NOT EXISTS planner_instance TEXT;
ALTER TABLE plan_journal       ADD COLUMN IF NOT EXISTS trigger_id       UUID;
ALTER TABLE setpoint_changes   ADD COLUMN IF NOT EXISTS planner_instance TEXT;
ALTER TABLE setpoint_changes   ADD COLUMN IF NOT EXISTS trigger_id       UUID;
CREATE INDEX IF NOT EXISTS idx_plan_journal_trigger_id ON plan_journal(trigger_id);
CREATE INDEX IF NOT EXISTS idx_plan_journal_instance   ON plan_journal(planner_instance);

UPDATE plan_journal     SET planner_instance = 'iris-planner' WHERE planner_instance IS NULL;
UPDATE setpoint_changes SET planner_instance = 'iris-planner' WHERE planner_instance IS NULL AND source = 'iris';

COMMIT;
```

**Column-name reference (for consumers):**

| concept                                      | column on `plan_delivery_log`          |
|---|---|
| trigger correlation id                       | `trigger_id` (new)                     |
| planner path (`local_gemma4` / escalation)   | `instance` (new)                       |
| lifecycle state                              | `status` (new)                         |
| when the ack was recorded                    | `acked_at` (new)                       |
| when OpenClaw 200-OK'd the POST              | `delivered_at` (existing)              |
| trigger type (SUNRISE/TRANSITION/...)        | `event_type` (existing)                |
| human label ("Tree Shade")                   | `event_label` (existing)               |
| plan row that this trigger produced          | `resulting_plan_id` (existing)         |
| when that plan was written                   | `plan_written_at` (existing)           |
| gateway response code / body                 | `gateway_status`, `gateway_body`       |
| greenhouse tenant                            | `greenhouse_id` (existing; multi-tenant)|

**Rationale for extension over parallel table:** the existing table has live production data since sprint 24.6, rich delivery metadata already being written, multi-tenant support, and an ingestor write path that's proven. Creating a parallel table would fragment the concept and require duplicated inserts. Extension gains trigger-id + instance + ack + status — the contract's actual needs — without churn.

**Invariant:** every MCP write stamps both columns from the request context. Missing header → `planner_instance = 'unknown'`, `trigger_id = NULL`, plus a WARNING in `mcp.log`.

### E. TimescaleDB → ingestor dispatcher

**Owner:** ingestor.

Unchanged — already working. Dispatcher reads `setpoint_plan`, pushes to ESP32 every 5 min.

### F. Failure detection (who raises the alert)

**Owner:** ingestor (`alert_monitor.planner_stale_rule`).

**Rule upgrade (from current rule 7):**
- Ingestor's existing `planning_heartbeat` write path (sprint 24.6) inserts one row per `send_to_iris` call into `plan_delivery_log`. In v1.5, that row is the trigger-ledger entry and must carry `trigger_id`, `instance`, and `status='pending'` for every attempted trigger.
- Rule fires a Slack alert if ANY of:
  - `delivered_at + SLA(event_type, instance)` passes with a row still at `status='pending'` (no matching `plan_journal.trigger_id` observed AND no corresponding `acknowledge_trigger` call). On alert, the rule flips the row to `status='timed_out'` so it's not re-alerted.
  - A `plan_journal` row is written where `planner_instance` doesn't match the trigger's requested `instance` (wrong-peer write).
  - `gateway_status NOT BETWEEN 200 AND 299` → surface immediately with `status='delivery_failed'`, no SLA wait.

**SLA table (2-dim — per trigger type × per instance).** Local Gemma4 is the
default, so its SLA is the baseline. Cloud escalation keeps a separate SLA so
operators can distinguish "local inference slow" from "cloud review slow":

| trigger_type | SLA(local_gemma4) | SLA(cloud_escalation) |
|---|---|---|
| SUNRISE     | 30 min | 15 min |
| SUNSET      | 30 min | 15 min |
| MIDNIGHT    | 30 min | 15 min |
| TRANSITION  | 30 min | 30 min |
| FORECAST    | 60 min | 30 min |
| DEVIATION   | 20 min | 10 min |
| HEARTBEAT   | 15 min | n/a |
| MANUAL      | none   | none   |

Storage: ingestor reads the table from a small YAML section (proposed `config/ai.yaml:planner_sla`) so tuning needs no code change.

**Alert payload includes:** trigger_id, instance, trigger_type, sent_at, elapsed_seconds, openclaw_response_code.

### G. Planner routing end-to-end

A normal production trigger must:
1. Route via `agentId: iris-planner` and a trigger-scoped session key (`agent:iris-planner:main:trigger:<trigger_id>`, or configured equivalent).
2. Use local Gemma4 on cortext as the backing model.
3. Produce either a `plan_journal` row with matching `trigger_id`, a valid
   `set_tunable` write tied to the same trigger, or an `acknowledge_trigger`
   row that resolves the trigger ledger.

A cloud escalation trigger must:
1. Be requested explicitly by operator policy or severity classification.
2. Route via a separately named cloud agent/session.
3. Stamp `planner_instance = 'cloud_escalation'` (or legacy `opus` during the
   migration window) and match the same `trigger_id`.

**Routing policy (v1.5).** The operator principle is:

> **Local Gemma4 owns normal planning and tuning. Cloud is an explicit escalation path.**

The caller picks the planner path from a policy table keyed on `(trigger_type,
severity)`. Severity is computed by ingestor from live data (forecast delta
magnitude, deviation magnitude, plan age) and can be overridden by the operator.

| trigger_type | condition | default planner path |
|---|---|---|
| `HEARTBEAT` | first-week observe-only loop | **local_gemma4** |
| `SUNRISE` | daily full plan review | **local_gemma4** |
| `SUNSET` | daily retrospective + overnight plan | **local_gemma4** |
| `MIDNIGHT` | fixed overnight boundary review | **local_gemma4** |
| `TRANSITION` | solar or fixed local-time boundary | **local_gemma4** |
| `FORECAST` | routine forecast refresh or minor shift | **local_gemma4** |
| `FORECAST` | major shift above thresholds | **cloud_escalation optional**, operator-visible |
| `DEVIATION` | routine observed-vs-forecast drift | **local_gemma4** |
| `DEVIATION` | major/prolonged deviation | **cloud_escalation optional**, operator-visible |
| `MANUAL` | operator-specified | **caller** |

**Thresholds live in `config/ai.yaml:planner_routing`** (coordinator-owned; one source of truth; tunable without code change). Ingestor reads the thresholds and classifies severity at call time.

**No implicit failover.** If the selected path is down (gateway 5xx,
SLA-timeout), ingestor alerts and **does not silently re-route**. Cloud
escalation is an auditable trigger decision, not a hidden retry.

**First-week safety constraint (ratified by Jason).** HEARTBEAT cycles during the first week after cutover run in **`acknowledge_trigger`-only mode** — the local model is **not** permitted to call `set_tunable` or `set_plan` during HEARTBEATs. It may only observe and acknowledge with a `reason`. This lets operators watch Gemma4's reasoning quality without any risk of bad tweaks reaching the plants. Removal of this constraint is an explicit post-week-1 operator decision. Enforcement: ingestor sets a header flag `X-Heartbeat-Readonly: true` on HEARTBEAT sends; MCP server rejects `set_tunable` / `set_plan` with HTTP 403 when that header is present on a local Gemma4 heartbeat call.

## 3. Ownership summary

| Layer | File(s) | Owning component | Required compatibility checks |
|---|---|---|---|
| Trigger source & SLA detection | `ingestor/tasks.py`, `ingestor/alert_monitor.py` | ingestor | update the `trigger_id` schema and consumers together |
| Dispatch function | `ingestor/iris_planner.py` | planner | validate ingestor environment variables and call sites |
| Env vars + config | `ingestor/config.py` | ingestor | — |
| OpenClaw profile | `~/.openclaw/openclaw.json` + `agents/*/agent/models.json` | planner runtime | validate token rotation and hook compatibility |
| Prompt variants | `ingestor/iris_planner.py`, `templates/planner-prompt.md` | genai | — |
| MCP tool surface | `mcp/server.py` | planner | validate every schema column it uses |
| Schema cols, migration | `db/migrations/NNN_planner_instance_audit.sql`, `verdify_schemas/*` | shared data contract | disposable-schema migration test |
| Contract doc | `docs/iris-planner-contract.md` (this doc) | repository | keep all component references current |

## 4. Changes required vs. today

**New surface area (additive, no breaking changes):**

1. **Request headers** — `X-Planner-Instance`, `X-Trigger-Type`, `X-Trigger-Id`.
2. **MCP header passthrough** — FastMCP `ctx.request_context.request.headers` reads; default-safe when missing.
3. **Schema columns** — nullable + backfilled; no downstream code breaks.
4. **Trigger ledger** — `plan_delivery_log` is the current backing table; target shape is defined in §2.D.
5. **SLA-based stale detection** — rule 7 upgrade; no new alert channels.
6. **New MCP tool `acknowledge_trigger(trigger_id, reason)`** — lets Iris record "read, no action needed" without writing a fake plan.

**Backwards compat:**
- MCP writes through `set_plan` / `set_tunable` with missing/invalid `trigger_id` fail at the MCP boundary. Operator changes should be initiated through an audited MANUAL trigger rather than direct orphan writes.
- Legacy `instance='local'` means Gemma/local; legacy `instance='opus'` means cloud. New code should write `local_gemma4` / `cloud_escalation` once the schema and dashboards are migrated.
- `OPENCLAW_SESSION_KEY` env var still honored for one cycle; deprecation notice in release notes.

## 5. Resolved questions (v1.1 review) + one remaining blocker

Q1 (acknowledge_trigger tool). **Resolved: yes for v1.** Core shape as specified. Future refinement (`next_check_after` / `max_consecutive_acks` to prevent silent drift during extended quiet periods) → follow-up, not blocking.

Q2 (Trigger log storage). **Resolved: table, not in-memory.** See §2.D `plan_delivery_log`. Reasoning: survives ingestor restarts, matches G10/G7 §15c retrospective-join pattern, ~5 rows/day is noise, debuggability beats ephemeral logs.

Q3 (SLAs). **Resolved: per-trigger × per-instance.** See §2.F table. v1.5 treats local Gemma4 as the baseline and cloud as an explicit escalation path.

Q4 (Prompt lite target). **Resolved: ≤60k tokens for Gemma4.** Achievable via genai's G6 (prompt caching split — rubric vs per-cycle context). Drop candidates: physical reference + mode-controller detail + low-confidence lessons + structured-hypothesis example. Final size measured after G6 lands.

Q5 (**BLOCKER FOR HEADER-ONLY CARRIER**). **FastMCP header passthrough.** genai believes the streamable-HTTP transport forwards client headers via `ctx.request_context.request.headers`, but this is unverified against our installed FastMCP version. Until proven, prompts must continue to include the audit banner and Iris must pass `trigger_id` / `planner_instance` kwargs explicitly. MCP now rejects planner-owned `set_plan` / `set_tunable` writes that omit `trigger_id`, so the fallback carrier is enforced by tool validation rather than trust. If headers are swallowed, §2.C's carrier stays message/tool-argument based. Owner: **Iris (`iris-planner` local Gemma4 session)**.

## 6. Rollout sequence (contract-first)

1. Coordinator lands this v1.5 contract update and trigger-ledger shape.
2. Coordinator/ingestor removes unsafe UUID-bearing time-window correlation.
3. Coordinator/genai moves registry min/max validation to the Pydantic/MCP boundary so invalid plans never become active.
4. ingestor/genai flips routing semantics so `iris-planner` means local Gemma4 on cortext, not "local label but cloud delivery."
5. ingestor replaces implicit milestone state with the trigger matrix and expected-trigger ledger writes, including fixed boundaries and late/missed sunrise handling.
6. genai builds the full Gemma4-sized context pack and distilled site/lesson memory.
7. Live smoke: send a MANUAL trigger to `iris-planner`, verify one ledger row resolves to `plan_written` or `acked` with matching `trigger_id`, and assert no active/future plan rows violate registry ranges.
8. After one week of local data, tune SLAs and decide whether major deviations should auto-create cloud-escalation trigger rows.

## 7. Non-goals (this contract) / deferred refinements

- Shadow/dual-dispatch mode.
- Automatic cloud/local failover.
- Multi-tenant planner routing (per-crop, per-zone instances).
- OpenClaw bearer token rotation policy (separate ops doc).
- `acknowledge_trigger` refinement (`next_check_after` / `max_consecutive_acks`) — prevents silent drift during quiet periods; file as follow-up after v1 ships.
- HEARTBEAT trigger self-regulation — if the local model starts thrashing tunables, add rate-limit (≤4 `set_tunable` per HEARTBEAT cycle; flagged in alert_monitor if exceeded).
