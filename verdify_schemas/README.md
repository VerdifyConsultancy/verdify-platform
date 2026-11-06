# verdify_schemas

Shared Pydantic v2 models. The database is the source of truth; this package
is the typed contract every other layer uses when it reads from, writes to,
or passes data alongside the DB.

## Why this exists

Before Sprint 20/21 the project had ~40 ad-hoc data shapes drifting across
layers:

- Ingestor INSERTs built dicts by hand; a typo in a column name landed
  silently as `None` in the row.
- MCP tools took free-form JSON strings and parsed them with `.get()`; the
  planner could omit required fields and the tool wrote partial rows.
- `api/main.py` had 4 local Pydantic models; none of them were shared with
  the MCP server or the ingestor.
- Vault markdown writers built frontmatter as anonymous dicts; a misspelled
  key meant a broken Obsidian dataview query three days later.
- Open-Meteo's 27 parallel hourly arrays were zipped by index with no length
  check.

One schema package ends all of that. Every shape lives here; every layer
validates at its boundary; drift is caught by CI.

## What's inside

| File | Purpose | Layers that use it |
|---|---|---|
| `tunables.py` | Canonical set of dispatcher-emitted parameters + `TunableParameter` validator | mcp `set_plan` / `set_tunable`, ingestor dispatcher, schema tests |
| `plan.py` | `Plan`, `PlanTransition`, `PlanHypothesisStructured`, `PlanEvaluation`, `PlanJournalRow` | mcp `set_plan` / `plan_evaluate`, `generate-daily-plan.py`, backfill script |
| `setpoint.py` | `SetpointChange`, `SetpointPlanRow`, `SetpointSnapshot`, `SetpointClamp` | ingestor confirmation loop, dispatcher audit, website renderers |
| `telemetry.py` | `ClimateRow`, `Diagnostics`, `EquipmentStateEvent`, `EnergySample`, `SystemStateRow`, `OverrideEvent` | ingestor write paths, MCP `query` tool consumers |
| `alerts.py` | `AlertLogRow`, `AlertEnvelope`, `AlertAction` | `alert_monitor` task, MCP `alerts` tool |
| `activity.py` | `ActivityLogRow` (`activity_log`), `DesiredStateRow` (`desired_state`), `ACTOR_SOURCE_PATTERN` (the R4 sources) and `ACTIVITY_ACTION_PATTERN` (the architecture §9 actions), migration 280 | `verdify_services.activity` (operator API and MCP writes), the ingestor's forecast-rule activity rows |
| `crops.py` | `Crop`, `CropCreate`, `CropUpdate`, `CropEvent`, `Observation`, `ObservationCreate`, `EventCreate` + action envelopes | `api/main.py`, MCP `crops` / `observations` tools, `vault-crop-writer.py` |
| `lessons.py` | `PlannerLesson`, `LessonCreate`, `LessonUpdate`, `LessonValidate`, `LessonAction` | MCP `lessons_manage` tool, `generate-lessons-page.py` |
| `daily.py` | `DailySummaryRow` (61 fields) | `daily-summary-snapshot.py`, `vault-daily-writer.py`, `generate-daily-plan.py` |
| `forecast.py` | `ForecastHour` | `forecast-sync.py`, `generate-forecast-page.py` |
| `views.py` | `PlannerPerformance`, `PlanAccuracy`, `DewPointRiskRow`, `WaterBudgetRow`, `DailyOscillation`, `DailyOscillationSummary`, `OverrideActivity24h`, `ClampActivity24h` | website renderers, MCP `scorecard` tool |
| `vault.py` | `DailyVaultFrontmatter`, `DailyPlanVaultFrontmatter`, `CropVaultFrontmatter`, `ForecastVaultFrontmatter`, `LessonsVaultFrontmatter` | every script that writes markdown into the Obsidian vault |
| `external.py` | `OpenMeteoHourly`, `OpenMeteoForecastResponse`, `HAEntityState` | `forecast-sync.py`, Shelly/Tempest/HA sync tasks |
| `irrigation.py` | The irrigation program record `VIP1` (`ProgramRecord`, `RecordLine`, `RecordSlot`, `pack_program`, `unpack_program`, `program_sha256`, `program_hash16`, `program_chunks`), the `irr_program_id`, `irr_state` and `irr_events` grammars (`parse_irr_*` and `format_irr_*`, `seq_newer`), `IrrigationCause`, `RunOutcome`, `REASON_WORDS` and the fixed codes (`DEVICE_IDX`, `MISTER_MASK_BIT`, `START_KIND`, `SCHEDULABLE_IDX`). Golden vectors: `tests/fixtures/irrigation_program_vectors.json`, written only by `scripts/gen-irrigation-program-vectors.py`. The schedule model (VD-37): `LineSpec`, `ScheduleSpec`, `Start`, `SiteValues` (`site_values`), the run-now `RunArgs` per line kind, `BOUNDS_BY_KIND` (`bounds_by_kind`), the submit checks `validate_schedule`, `validate_line` and `validate_run` (each a `Refusal` or `None`), `compile_program` (`CompiledProgram`) and `agenda` (`Agenda`) | the ingestor push and ledger (#1157), API and MCP irrigation reads and writes (#1155, #1156), firmware decoder native tests (#1159) |
| `band_rows.py` | The `crop_band_anchors` rows each band setting reads and writes (VD-67): `row_season_score`, the zone crop maps (`ZONE_CROP`, `CROP_TO_ZONE`), the width columns and `BAND_PARAM_ROWS` (crop types, series, anchor and column per setting) with `rows_in_force`, moved out of `ingestor/tasks/band_anchors.py`, which imports them | the ingestor band-anchor sync, `verdify_services.settings` crop-band writes, the band preview (#1170) |
| `solar.py` | The NOAA sunrise, solar-noon and sunset ephemeris and the Longmont site constants (`compute_solar_times`, `SolarTimes`, `GREENHOUSE_LAT_DEG`, `GREENHOUSE_LON_DEG`), moved from `ingestor/solar.py` (VD-37), which re-exports them beside the band-curve engine; `scripts/check-solar-constants.py` holds the firmware and SQL mirrors to it | the irrigation agenda's solar starts, the ingestor band-anchor sync |
| `planner_study.py` + `planner_ab_winter_2026.yaml` | The AI-planner-versus-baseline study definition (#1360): `PlannerStudy` and its parts, validated on load (the two arms, one primary first, a margin only on a guardrail, heat and electric run time reported and never scored, looks that end at the planned pairs, `field_count` equal to `PLANNER_PUSHABLE_REG`), `load_study`, `studies` and `definition_sha256` | `verdify_services.planner` (operator API-23 and API-24), the experiment orchestrator (#1360, next) |

## Principles

1. **Names match DB columns exactly.** A schema field is valid iff it also
   appears in `information_schema.columns` for its table. `test_drift_guards.py`
   enforces this against the live DB.
2. **Enums replace magic strings.** `SetpointSource`, `AlertSeverity`,
   `CropStage`, `LessonConfidence`, etc. — any magic string the codebase
   used more than once is now a `Literal[...]`.
3. **Validators enforce physics invariants.** `vpd_low < vpd_high`,
   `temp_low < temp_high`, `0 <= rh_pct <= 100`, `outcome_score in [1, 10]`.
   The same invariants as the ingestor's `_PHYSICS_INVARIANTS`, but at the
   schema level — so a bad plan fails at the MCP boundary, not after it's
   partially written to `setpoint_plan`.
4. **`extra="ignore"` on DB-row models; `extra="forbid"` on input envelopes.**
   DB-row schemas tolerate new columns (additive migrations are the common
   case). Input envelopes (CropCreate, LessonCreate, etc.) reject unknown
   fields — if the planner sends a typo'd key we want to hear about it.

## Adding a new schema

1. Add the model to the appropriate file (or create a new one if it's a new
   domain). One class, one docstring, one example in a unit test.
2. Re-export from `verdify_schemas/__init__.py` (`__all__` + the `from`
   import at the top).
3. Add a unit test in `verdify_schemas/tests/`:
   - Happy path
   - At least one rejection case (bad enum value, out-of-range number,
     missing required field)
4. If the new model mirrors a DB table, add it to the `DB_BACKED` list in
   `test_drift_guards.py` and confirm the drift guard passes.
5. Re-run `make test` (covers `tests/` + `verdify_schemas/tests/`).

## Integration patterns

### Validate input at the MCP boundary

```python
from verdify_schemas import Plan
from pydantic import ValidationError

try:
    plan = Plan.model_validate_json(raw_transitions)
except ValidationError as e:
    return json.dumps({"error": "Plan validation failed", "details": json.loads(e.json())})
# plan is now typed; every transition has a valid TunableParameter, etc.
```

### Validate rows at ingestor write boundary

```python
from verdify_schemas import ClimateRow

row = ClimateRow.model_validate({"ts": ts, "temp_avg": t, "rh_avg": rh, ...})
# .model_dump(mode='python') hands the dict to asyncpg; out-of-range
# values get caught before they reach the hypertable.
```

### Drive a website renderer

```python
from verdify_schemas import DailyVaultFrontmatter

fm = DailyVaultFrontmatter(date=date.today(), temp_avg=t, ...)
yaml_block = yaml.safe_dump(fm.model_dump(mode="json", exclude_none=True))
```

### Audit a view projection

```python
from verdify_schemas import PlannerPerformance

rows = db_query_json("SELECT * FROM v_planner_performance WHERE date = %s", today)
perf = PlannerPerformance.model_validate(rows[0])
# perf.planner_score is a typed Decimal; if the view drops a column the
# renderer fails here, not silently mid-page.
```

## Testing

- `pytest verdify_schemas/tests/` — unit tests for every model
- Drift guards run against live Postgres via `docker exec`; skip if
  `docker` isn't available (CI runners)
- Full suite: `make test` covers both `tests/` (integration) and
  `verdify_schemas/tests/` (unit + drift)

## Migration history

- **Sprint 20** established the pattern with 8 models (Plan, PlanTransition,
  PlanEvaluation, SetpointChange, DailySummaryRow, ForecastHour,
  TunableParameter, PlanHypothesisStructured) around the planner feedback
  loop.
- **Sprint 21** extended coverage to every DB table the ingestor writes to,
  every view the renderers read, every MCP tool that accepts JSON, every
  vault markdown writer, and every external API. ~30 new models; 140+
  unit tests; 18 drift guards.
