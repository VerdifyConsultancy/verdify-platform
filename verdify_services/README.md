# verdify_services

The one service layer the operator API (`api/operator_api.py`, #1148) and MCP (`mcp/server.py`) share, so
people and agents get the same validation, the same refusal codes and the same activity row for the same
write ([architecture §6](../docs/design/dashboard/architecture.md#6-shared-service-layer), US-PLAT-05). This
page is the conventions every service here follows; VD-31 (#1147) set them.

| Module | What it holds |
|---|---|
| `actor.py` | `Actor` (who is calling) and `ActorRef` (who wrote, as responses show it) |
| `problems.py` | `ProblemCode`, `Problem`, `ServiceError` and `problem()` |
| `activity.py` | `append`, `list` and `recorded_write`, the one write convention, and `stored_write`, its refusals and replay alone, with `WriteResult`, `ActivityEntry` and `ActivityPage` |
| `band_profile.py` | `preview` (API-22) and `write` (API-23, #1334): the band editor. The preview overlays a draft (simple profile fields, compiled by `verdify_schemas.band_profile`, and raw anchors) on the house anchors and evaluates it with the controller's curve, adding the night bias, the guardrail and night-floor findings, the expected night VPD ribbon and the night heat estimate; `write` stores every changed anchor through `settings.write_in_transaction` under one `band.write` activity row |
| `lights.py` | `stored` and `record` (VD-57: API-20): a grow-light switch's `activity_log` row around the setpoint-server call the route makes, with `LightCommand` and `LightRecord`; `try_lock` and `unlock`, the per-light session advisory lock API-20 and the Back to automatic resync take around that call (#1300); `decision`, the controller's newest decision for a light (`LightDecision`); and `timer`, when a timed manual switch's minutes are up (`LightTimer`, #1335), which the operator API's `light_timer` sweep reads |
| `irrigation/writes.py` | `schedule_create`, `schedule_update`, `schedule_delete`, `line_update`, `run`, `stop`, `override` and `clear_fault` (VD-41: API-IRR-03 to -08, -12 and -13, the functions the MCP write tools call), with their bodies; `run` and `stop` go through the L01 bridge before the engine firmware (VD-64) |
| `settings.py` | `catalog`, `validate`, `write`, `release`, `status` and `history` (VD-67), and the batched `statuses` and `desired_states` (VD-55): the one validator and writer for desired state, with `Setting`, `DesiredSetting`, `StateReason`, `SettingHistory` and `SettingStatus`; `check_range` gives API-19's draft the refusal a Save would get |
| `alerts.py` | `list`, `open_count`, `ack` and `resolve` (VD-67): merged alert rows, the one open-alert count, with `Alert`, `AlertPage` and `AlertCounts` |
| `reads.py` | `now`, `plan` and `equipment` (VD-54: API-02, API-07 and API-13); `settings_rows`, `setting_history` and `activity_page` (VD-55: API-09, API-12 and API-17, which the MCP `settings` and `activity` tools call too), with `SettingRows`; `water_daily` (VD-57: API-21); and the shared `reporting()` (the one §7 test: heartbeat at most 180 s old, climate at most 300 s), which `settings.status` uses for its `writer_down` and `controller_not_reporting` reasons |
| `irrigation/reads.py` | `status`, `lines`, `agenda`, `minutes` and `runs` (VD-40: API-IRR-01, -02, -10 and -11), the functions the MCP read tools wrap (#1173) |
| `irrigation/runs.py` | The relay-edge runs, their cause (`run_causes`, for API-02 and API-05), a day's minutes by M4's rules and a run's gallons |
| `irrigation/program.py` | The controller's program: the `irr_engine` flag, `irr_program_id` and its pushes, for the reads and for the `irrigation_site` readback in `settings.status` |

Later modules (`series`) follow the same rules. The irrigation reads take an `Actor` too, as #1173's tools pass it; it changes nothing.

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
    conn,  # outside any transaction: it opens its own
    actor,  # from the session or the token, never from arguments
    op="API-10",  # the REST operation id; an MCP tool passes its REST twin's id
    action="setting.write",  # the activity action (architecture §9)
    target=param,
    request=body,  # every input of `op` that `target` does not carry, as validated JSON (or its model)
    request_id=idempotency_key,  # Idempotency-Key (REST) or request_id (MCP); None when not sent
    reason=reason,  # at most 500 characters, no NUL (400 invalid_request)
    perform=perform,  # async (conn) -> (value, detail): validate and write the domain row
    load=load,  # async (conn, ActivityLogRow) -> value: read the stored object back
    lock=None,  # optional async (conn) -> None: the first thing in the transaction, before the replay read
    target_of=None,  # optional (value) -> str: the activity row's target when only perform knows it (a new id)
    finish=None,  # optional async (conn, value) -> value: the answer, read after the activity row in this transaction
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
   transaction, where it replays (or is refused). A write whose checks read what an earlier write stored passes
   `lock`, so its duplicate waits before step 3, replays there and never meets those checks.

Validate inside `perform`, so a retried request replays even if the rules changed since it was applied.
A refusal raised there rolls back everything.

`WriteResult(value, replayed, activity_id)` answers both surfaces: `rest_json()` is the response body,
`rest_headers()` is `{"Idempotency-Replayed": "true"}` on a replay, and `mcp_payload()` is the REST JSON
with a top-level `replayed: true` on a replay (MCP has no headers). `activity_id` is the activity row's id.

A write that must call something outside the database before its row exists (API-20: the setpoint server, which no
transaction may wait on and this package may not call) runs `stored_write` first: the same refusals and the same
replay, closed before the call, with None meaning go ahead. Its row then goes through `recorded_write` with the same
arguments, so a concurrent duplicate that got there first still replays.

## The log: `activity.append` and `activity.list`

- `append(conn, actor, *, action, target, detail=None, reason=None, request_id=None, request_sha256=None,
  experiment_id=None) -> int` inserts one row validated through `verdify_schemas.activity.ActivityLogRow`
  and returns its id. Actions: `setting.write`, `setting.release`, `plan.write`, `alert.ack`,
  `alert.resolve`, `irrigation.*`, `light.on`, `light.off`; extend `ACTIVITY_ACTION_PATTERN` there for a new
  kind of write. A write with a request id uses `recorded_write`, which stores the id and the hash together.
  The log takes a reason of any length, as its column does (Iris's unchanged `set_tunable` is never
  refused, R5); `recorded_write` refuses one over 500 characters with `invalid_request`, as the REST
  bodies do, and one holding a NUL, which PostgreSQL text cannot store.
- `list(conn, *, source=None, actor=None, action=None, target=None, from_=None, to=None, limit=50,
  cursor=None) -> ActivityPage` (API-17, MCP `activity`): newest first by (`ts`, `id`); `source='agent'`
  matches every `agent:<name>`; `action` is a prefix match; `actor` and `target` are exact; `from_` is
  inclusive and `to` exclusive (times with an offset); `limit` is 1 to 200; `cursor` is the previous page's
  `next_cursor`, an opaque base64url of its last (`ts`, `id`). `ActivityEntry.state` and `confirmed_at` stay
  None here; the settings service fills them for setting writes.

The connection's login needs SELECT and INSERT on `activity_log`, USAGE on its sequence and EXECUTE on
`fn_mcp_armed_assignment_context()`: the MCP login has them (migrations 259 and 280); the dashboard login
gets them in M2.

## Settings: `settings`

Architecture §5.2 to §5.5. `validate(conn, actor, param, value)` is the only validator for desired state; REST
(API-10) and MCP (`set_setting`) call it through `write` and never re-implement bounds.

1. **Known and writable.** `unknown_target` for a name outside `REGISTRY`; `read_only` with the reason for
   every row `verdify_schemas.tunable_registry.desired_write_rule()` refuses (the dispatcher skips the same
   rows, #1149), and for a `crop_band` row while `crop_band_anchors` holds none of its rows in force
   (`verdify_schemas.band_rows`).
2. **Type and range.** `out_of_range` with `min` and `max` (`planner_effective_bounds`).
3. **Manual.** `manual` for an `iris` write (or release) of a setting whose newest row is manual.
4. **Snap** to the registry `step`, half to even, unless the value equals the value in force or the device
   value within its binary32 ULP; `snapped_from` says when snapping changed it.

Every refusal is 422 with `field`, `min`, `max`, `current` and `retryable` false. `write` and `release` run in
one `recorded_write` transaction: the `desired_state` row (with `manual` exactly as passed: the services have no
default), for a crop-band setting the `UPDATE` of its `crop_band_anchors` rows at the best season score present
(only `value`, `width_below` or `width_above`), and the `setting.write` or `setting.release` activity row with
`{ref, desired_id, before, value, requested, manual: {before, after}}` (plus `anchor_rows`) merged with the
caller's `extra_detail`. Nothing writes `setpoint_changes`; the `desired_state` insert trigger is the only wake.

`status(conn, param)` computes the readback state on every read and raises no alert. Readbacks are the newest
`setpoint_snapshot` row and, for a switch in `SWITCH_READBACK_EQUIPMENT`, its `equipment_state` mirror; only
those at or after the request count. A desired value confirms by `readback_matches()`, a plan value (or, with no
desired row and no plan, the dispatcher's newest push) by `readback_values_equivalent()`. A clamp after the
request holds the setting while the newest readback does not confirm it. `state_reason` (`clamped`,
`experiment`, `deferred`, `push_failed`, `writer_down`, `controller_not_reporting`, `no_match`) carries its
`since`; the two ages come from `reads.reporting()`. An `irrigation_site` row reads back through the program
(#1155): a write stays `requested` until the confirmed push whose `sha256` starts with the `irr_program_id` hash has
`site_desired_at` at or after it, then reads `confirmed` with the value from that record; a write of the value that
record already holds changes no hash, so it reads `confirmed` at once. With no engine it stays
`requested` with `no_engine`, its `since` the write; with the engine it reads `not_confirmed` 5 min after the write,
and a `no_match` names the first push compiled at or after it. `statuses(conn, params)` is `status` for several
settings in one batched read (API-07's lanes), and `desired_states(conn, ids)` gives named `desired_state` rows the
state `history` gives them: the newest row of a setting its full status, an older one released, confirmed (a readback
before the next row matched) or not_confirmed (API-17's setting writes). `catalog` and `history` serve API-09 and
API-12 through `reads` (below).

`reads` imports this module, so `settings` imports `reads` inside `_shared()` only, where it calls
`reads.reporting()`: either module may be imported first.

## Reads: `reads` (API-09, API-12 and API-17)

The composition VD-55 adds over the services, called by the REST routes and by the MCP `settings` and `activity`
tools (#1171), so both return the same rows. Each runs in one read-only snapshot.

- `settings_rows(conn) -> SettingRows` (`{items, server_time}`, API-09): `settings.catalog`, then each row's
  `plan` while a `v_active_plan` value is in force: the value as stored (Iris's 48.75 stays 48.75, unsnapped),
  `plan_id`, `source` (`setpoint_plan.source`: `iris`, or `preemptive` for the forecast engine), `written_at` and
  `next`, the first `plan_waypoints()` waypoint after now with its `effective`. Then the 30 Band in force rows:
  the newest `setpoint_snapshot` band row of each zone (house, center, south, west, east) and role in the last
  hour, `group` `band_in_force`, read-only ("derived by the dispatcher from the crop band anchors"), labelled
  "House temp low" in °F or "West VPD high" in kPa, `readback_via` `none`, state `no_readback`, and a null value
  with its reason when the dispatcher wrote none.
- `setting_history(conn, param, from_, to) -> SettingHistory` (API-12): `settings.history` (desired rows from the
  one in force at `from_`, readback change points; 404 `not_found` for an unknown setting) plus
  `plan_history()`: every `iris` and `preemptive` `setpoint_plan` row of the setting in the window, past,
  superseded and future, one per `ts` (the newest `created_at`). `effective` is false under the desired row in
  force at that time (`resolve_effective()`), and for a full Iris plan's row once another, newer full plan was
  written at or before it (`plan_journal`), so Graphs marks SUPERSEDED. Every list is bounded by the window, and
  readbacks by their 90-day retention: the window is not clamped, because Graphs asks for up to 400 days.
- `activity_page(conn, *, source, actor, action, target, from_, to, limit=50, cursor) -> ActivityPage`
  (API-17): `activity.list` with each `setting.write` and `setting.release` row's `state` and `confirmed_at`
  from `settings.desired_states` for its `detail.desired_id`.
- API-02's `alerts` is `alerts.open_count`, and its leak and stuck-delivery facts read `alert_log` under the
  same predicate; API-07's lanes take `applied_from` and `desired` from `settings.statuses`. `reads` reuses the
  models of the schemas the services own (`reads.DesiredSetting is settings.DesiredSetting`, `reads.AlertCounts is
  alerts.AlertCounts`).

## Alerts: `alerts`

Open rows are `alert_log` under `v_open_alerts`' own predicate (the MCP login cannot read the view). Repeats
merge by (`alert_type`, `sensor_id`), NULL with NULL, the key the ingestor's alert monitor keeps one row per
probe on; resolved rows merge apart. A merged row keeps the oldest unresolved repeat's `id`, sums
`details.count` and is `open` while any repeat is. `open_count` merges the same rows, so it always equals the
open rows `list` returns. `ack` and `resolve` act on every repeat in one `recorded_write` transaction, write
`acknowledged_by` or `resolved_by` as the username for a person and `actor.source` otherwise, and never refuse
a resolve for its alert type (the MCP adapter keeps its `planner_tool_dependency_failed` retrieval check); only
an unknown id or text holding a NUL is refused.

The login needs SELECT on `alert_log`, `slack_alert_runbooks`, `setpoint_snapshot`, `equipment_state`,
`setpoint_changes`, `setpoint_clamps`, `system_state`, `climate`, `crop_band_anchors`, `v_active_plan`,
`desired_state` and `v_desired_state_current`, UPDATE on the `alert_log` ack and resolve columns and the three
`crop_band_anchors` columns, and EXECUTE on `fn_current_season()` and `fn_mcp_armed_assignment_context()`
(the experiment check every read and write makes): migrations 259, 261 and 280 grant the MCP login all of
them, and M2 (#1179) and its successor the dashboard login. The `irrigation_site` rows also read `irrigation_push`
(M3, #1153, both logins).

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
- `tests/test_verdify_services_settings.py` and `tests/test_verdify_services_alerts.py` (in
  `scripts/ci-local.sh`): validation, snapping, the read-only list, the state precedence and each reason's
  `since`, `applied_from`, the catalog against `TunableDef` and the OpenAPI, the merge, the count and the
  cursor, all against in-memory connections.
- `tests/test_dashboard_db_settings_alerts.py` (the campaign step): the same as the MCP login, the crop band
  rows through `fn_zone_vpd_targets` and `band_anchors`, and the open set against `v_open_alerts`.
- `tests/test_operator_routes_reads.py` (in `scripts/ci-local.sh`): the API-09, API-12, API-14 and API-17
  adapters against fake services: operation ids, query parameters, the cursor and the Problem mapping.
  `tests/test_verdify_services_reads.py` adds the compositions against in-memory connections.
- `tests/test_dashboard_db_settings_reads.py` (the campaign step): VD-55's acceptance fixtures, the
  MCP-shared reads as a role with only the MCP login's production grants.
- `tests/test_verdify_services_irrigation_reads.py` (in `scripts/ci-local.sh`) and
  `verdify_schemas/tests/test_irrigation_line_state.py`: the run, cause, minutes, gallons, sync and agenda rules and
  `line_state()`, with no database. `tests/test_dashboard_db_irrigation_reads.py` (the campaign step): the four reads
  as the dashboard and the MCP logins, against the committed OpenAPI and API-IRR-10's own rows, and the timing case.
- `tests/test_mcp_settings_tools.py` (in `scripts/ci-local.sh`) and `tests/test_dashboard_db_mcp_settings.py` (the
  campaign step): the MCP `set_setting`, `settings`, `activity` and `alerts` adapters and the `plan.write` rows of
  `set_plan` and `set_tunable` (VD-58), against stubs and then as the MCP login's grants.
- `tests/test_dashboard_db_settings_parity.py` (the campaign step): MCP `set_setting` and API-10 store the same
  `desired_state` and `activity_log` rows, column by column.
- `verdify_schemas/tests/test_band_rows.py` and `test_settings_rules.py`: the band row map against the
  dispatcher's overlay, and `SWITCH_READBACK_EQUIPMENT` and the activity mirror against the ingestor.
