# verdify_services

The one service layer the operator API (`api/operator_api.py`, #1148) and MCP (`mcp/server.py`) share, so
people and agents get the same validation, the same refusal codes and the same activity row for the same
write ([architecture §6](../docs/design/dashboard/architecture.md#6-shared-service-layer), US-PLAT-05). This
page is the conventions every service here follows; VD-31 (#1147) set them.

| Module | What it holds |
|---|---|
| `actor.py` | `Actor` (who is calling) and `ActorRef` (who wrote, as responses show it) |
| `problems.py` | `ProblemCode`, `Problem`, `ServiceError` and `problem()` |
| `activity.py` | `append`, `list` and `recorded_write`, the one write convention, with `WriteResult`, `ActivityEntry` and `ActivityPage` |

Later modules (`settings`, `alerts`, `reads`, `series`, `irrigation`) follow the same rules.

## Rules

- **Imports.** `verdify_schemas`, `asyncpg`, Pydantic and the standard library only: no FastAPI, MCP, HTTP
  client, environment or file reads. `tests/test_verdify_services_core.py` checks it. The ingestor never
  imports this package (its image does not carry it); it reads `desired_state` and writes its own activity
  rows with SQL, validated through `verdify_schemas.activity`.
- **Shape.** Every function is `async`, takes an asyncpg connection first and returns Pydantic models built
  on `verdify_schemas`. Writes also take an `Actor`; reads do not (the adapters check roles).
- **Actor.** `Actor(source, name, role)`, frozen. `source` is `dashboard`, `iris`, `system` or
  `agent:<name>` (R4, `verdify_schemas.activity.ACTOR_SOURCE_PATTERN`, the OpenAPI `Actor.source`
  pattern); `role` is `operator` or `viewer` for `dashboard` and None otherwise; `name` is 1 to 64
  characters. Build it with `Actor.person(username, role)`, `Actor.iris()`, `Actor.agent(name)` (the admin
  MCP token is `Actor.agent("admin")`) or `Actor.system(name)` (`forecast-engine`). Adapters build it from the
  session (operator API) or the token's request context (MCP, `mcp/server.py` `current_actor()`); a service
  never takes `actor`, `source` or a `*_by` value from request arguments. A write with no actor, or by a
  viewer, is refused with `forbidden`.
- **Refusals.** Raise `ServiceError(problem(code, detail, field=..., min=..., max=..., current=...))`. The
  code is from `ProblemCode` (the OpenAPI enum, verbatim), the title is the code in words and the status
  follows the code. Nothing is stored or sent when a write is refused.

| Status | Codes |
|---|---|
| 400 | `invalid_request` |
| 401 | `unauthenticated` |
| 403 | `forbidden`, `origin_mismatch` |
| 404 | `not_found` (an unknown id on a read) |
| 503 | `writes_disabled`, `query_timeout`, `unavailable` |
| 422 | every validation and refusal code: `window_too_large`, `unknown_target`, `read_only`, `out_of_range`, `manual`, `request_id_reused`, `check_required`, `too_close`, `too_many_schedules`, `line_disabled`, `line_paused`, `line_faulted`, `not_schedule_member`, `line_busy`, `manifold_busy`, `cap_reached` |

  `retryable` is true only for `line_busy`, `manifold_busy` (with `retry_after_s` when known),
  `query_timeout` and `unavailable`; pass `retryable=False` where waiting cannot help (the pre-engine
  `unavailable`, "arrives with the irrigation firmware"). The model refuses `retryable=True` for any other
  code. An irrigation `Refusal` is the same problem: `ServiceError(Problem.model_validate(refusal.problem()))`.
  REST adapters render `problem.body()` as `application/problem+json` with `problem.status`; MCP adapters
  return the same JSON with `isError`. Either may set `request_id` (`problem.model_copy(update=...)`).

## Writes: `activity.recorded_write`

Every write runs through it. One call is one transaction: the domain row and its `activity_log` row commit
together or not at all.

```python
result = await activity.recorded_write(
    conn,                       # outside any transaction: it opens its own
    actor,                      # from the session or the token, never from arguments
    op="API-10",                # the REST operation id; an MCP tool passes its REST twin's id
    action="setting.write",     # the activity action (architecture §9)
    target=param,
    request=body,               # every input of `op` that `target` does not carry, as validated JSON (or its model)
    request_id=idempotency_key, # Idempotency-Key (REST) or request_id (MCP); None when not sent
    reason=reason,              # at most 500 characters
    perform=perform,            # async (conn) -> (value, detail): validate and write the domain row
    load=load,                  # async (conn, ActivityLogRow) -> value: read the stored object back
)
```

1. A missing actor, or a viewer, is refused with `forbidden`.
2. `request_sha256` is the SHA-256 of the canonical JSON of `{"op", "target", "request"}`: sorted keys, no
   spaces, no NaN (`activity.request_hash`). MCP tools hash their REST twin's request body, so both
   surfaces hash one request the same way. `request` holds every input that `target` does not: the body,
   plus any path or query value that changes the write (API-20's `{state}`, API-11's `?reason=`), so two
   writes that differ only there never replay each other. Times, dates and UUIDs are written as Pydantic's
   JSON mode writes them (a UTC time ends in `Z`), so a dict of typed arguments and the model it validates
   into hash alike.
3. With a `request_id`, the `activity_log` row for (`actor.source`, `request_id`) is read first. The same
   hash returns `WriteResult(value=await load(conn, row), replayed=True)` and appends nothing; another hash
   raises `request_id_reused` (422, not retryable). Idempotency is per source and lives only on the activity
   row (R7).
4. `experiment_id` is `SELECT experiment_id FROM fn_mcp_armed_assignment_context()`, None when nothing is
   armed. Nothing is refused because an experiment is armed.
5. `value, detail = await perform(conn)` writes the domain row. `detail` names it as `ref: {table, id}` so a
   replay can `load` it, plus the before and after values the Activity screen shows. Then `append` writes
   the activity row with the request id, the hash and the experiment id, and the transaction commits.
6. A concurrent duplicate that hits `UNIQUE (source, request_id)` rolls back and runs step 3 again in a new
   transaction, where it replays (or is refused).

Validate inside `perform`, so a retried request replays even if the rules changed since it was applied.
A refusal raised there rolls back everything.

`WriteResult(value, replayed)` answers both surfaces: `rest_json()` is the response body,
`rest_headers()` is `{"Idempotency-Replayed": "true"}` on a replay, and `mcp_payload()` is the REST JSON
with a top-level `replayed: true` on a replay (MCP has no headers).

## The log: `activity.append` and `activity.list`

- `append(conn, actor, *, action, target, detail=None, reason=None, request_id=None, request_sha256=None,
  experiment_id=None) -> int` inserts one row validated through `verdify_schemas.activity.ActivityLogRow`
  and returns its id. Actions: `setting.write`, `setting.release`, `plan.write`, `alert.ack`,
  `alert.resolve`, `irrigation.*`, `light.on`, `light.off`; extend `ACTIVITY_ACTION_PATTERN` there for a new
  kind of write. A write with a request id uses `recorded_write`, which stores the id and the hash together.
  The log takes a reason of any length, as its column does (Iris's unchanged `set_tunable` is never
  refused, R5); `recorded_write` refuses one over 500 characters with `invalid_request`, as the REST
  bodies do.
- `list(conn, *, source=None, actor=None, action=None, target=None, from_=None, to=None, limit=50,
  cursor=None) -> ActivityPage` (API-17, MCP `activity`): newest first by (`ts`, `id`); `source='agent'`
  matches every `agent:<name>`; `action` is a prefix match; `actor` and `target` are exact; `from_` is
  inclusive and `to` exclusive (times with an offset); `limit` is 1 to 200; `cursor` is the previous page's
  `next_cursor`, an opaque base64url of its last (`ts`, `id`). `ActivityEntry.state` and `confirmed_at` stay
  None here; the settings service fills them for setting writes.

The connection's login needs SELECT and INSERT on `activity_log`, USAGE on its sequence and EXECUTE on
`fn_mcp_armed_assignment_context()`: the MCP login has them (migrations 259 and 280); the dashboard login
gets them in M2.

## Route modules and images

`api/operator_routes/__init__.py` holds the route-module rules: one `router` per module, paths without
`/v1`, `operation_id` set to the design id, no auth dependencies and no SQL. `api/Dockerfile` and
`mcp/Dockerfile` copy this package with a plain `COPY verdify_services/ ./verdify_services/` and import
`verdify_services.activity` at build time; the ingestor image does not get it.

## Tests

- `tests/test_verdify_services_core.py` (in `scripts/ci-local.sh`): `Actor`, `Problem` against the committed
  OpenAPI, the request hash, every `recorded_write` branch against an in-memory connection, `list`, and the
  import and image boundaries.
- `tests/test_dashboard_db_services.py` (the campaign step's private TimescaleDB): replay,
  `request_id_reused`, the experiment stamp, a concurrent duplicate, atomicity and `list` as the MCP login.
- `verdify_schemas/tests/test_activity.py`: the row models against migration 280.
