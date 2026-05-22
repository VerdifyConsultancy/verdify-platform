# Schema Relationships

<!-- BEGIN AUTO-ERD -->

*Auto-generated from `information_schema.referential_constraints` by 
`scripts/generate-erd.py`. Do not hand-edit. 61 FK(s) found.*

```mermaid
erDiagram
    crop_catalog ||--o{ crop_target_profiles : "crop_catalog_id"
    crop_catalog ||--o{ crops : "crop_catalog_id"
    crops ||--o{ crop_events : "crop_id"
    crops ||--o{ harvests : "crop_id"
    crops ||--o{ lab_results : "crop_id"
    crops ||--o{ nutrient_recipes : "crop_id"
    crops ||--o{ observations : "crop_id"
    crops ||--o{ treatments : "crop_id"
    daily_checklist_template ||--o{ daily_checklist_log : "template_id"
    equipment ||--o{ switches : "equipment_id"
    equipment ||--o{ water_systems : "equipment_id"
    equipment_assets ||--o{ maintenance_log : "equipment"
    forecast_action_rules ||--o{ forecast_action_log : "rule_id"
    greenhouses ||--o{ alert_log : "greenhouse_id"
    greenhouses ||--o{ camera_zone_map : "greenhouse_id"
    greenhouses ||--o{ crop_events : "greenhouse_id"
    greenhouses ||--o{ crops : "greenhouse_id"
    greenhouses ||--o{ daily_summary : "greenhouse_id"
    greenhouses ||--o{ equipment : "greenhouse_id"
    greenhouses ||--o{ equipment_state : "greenhouse_id"
    greenhouses ||--o{ forecast_action_log : "greenhouse_id"
    greenhouses ||--o{ forecast_deviation_log : "greenhouse_id"
    greenhouses ||--o{ greenhouse_sensor_config : "greenhouse_id"
    greenhouses ||--o{ image_observations : "greenhouse_id"
    greenhouses ||--o{ model_predictions : "greenhouse_id"
    greenhouses ||--o{ observations : "greenhouse_id"
    greenhouses ||--o{ plan_journal : "greenhouse_id"
    greenhouses ||--o{ planner_lessons : "greenhouse_id"
    greenhouses ||--o{ positions : "greenhouse_id"
    greenhouses ||--o{ pressure_groups : "greenhouse_id"
    greenhouses ||--o{ sensors : "greenhouse_id"
    greenhouses ||--o{ setpoint_changes : "greenhouse_id"
    greenhouses ||--o{ setpoint_plan : "greenhouse_id"
    greenhouses ||--o{ setpoint_snapshot : "greenhouse_id"
    greenhouses ||--o{ shelves : "greenhouse_id"
    greenhouses ||--o{ soil_moisture_targets : "greenhouse_id"
    greenhouses ||--o{ switches : "greenhouse_id"
    greenhouses ||--o{ system_state : "greenhouse_id"
    greenhouses ||--o{ water_systems : "greenhouse_id"
    greenhouses ||--o{ weather_forecast : "greenhouse_id"
    greenhouses ||--o{ zones : "greenhouse_id"
    image_observations ||--o{ observations : "image_observation_id"
    irrigation_schedule ||--o{ irrigation_log : "schedule_id (retired compatibility)"
    observations ||--o{ treatments : "observation_id"
    planner_lessons }o--|| planner_lessons : "superseded_by (self-ref)"
    positions ||--o{ crop_events : "position_id"
    positions ||--o{ crops : "position_id"
    positions ||--o{ harvests : "position_id"
    positions ||--o{ observations : "position_id"
    positions ||--o{ sensors : "position_id"
    positions ||--o{ treatments : "position_id"
    pressure_groups ||--o{ water_systems : "pressure_group_id"
    shelves ||--o{ positions : "shelf_id"
    zones ||--o{ alert_log : "zone_id"
    zones ||--o{ crops : "zone_id"
    zones ||--o{ equipment : "zone_id"
    zones ||--o{ image_observations : "zone_id"
    zones ||--o{ observations : "zone_id"
    zones ||--o{ sensors : "zone_id"
    zones ||--o{ shelves : "zone_id"
    zones ||--o{ water_systems : "zone_id"
```

### Full FK inventory

| Parent | Parent col | Child | Child col |
|---|---|---|---|
| `crop_catalog` | `id` | `crop_target_profiles` | `crop_catalog_id` |
| `crop_catalog` | `id` | `crops` | `crop_catalog_id` |
| `crops` | `id` | `crop_events` | `crop_id` |
| `crops` | `id` | `harvests` | `crop_id` |
| `crops` | `id` | `lab_results` | `crop_id` |
| `crops` | `id` | `nutrient_recipes` | `crop_id` |
| `crops` | `id` | `observations` | `crop_id` |
| `crops` | `id` | `treatments` | `crop_id` |
| `daily_checklist_template` | `id` | `daily_checklist_log` | `template_id` |
| `equipment` | `id` | `switches` | `equipment_id` |
| `equipment` | `id` | `water_systems` | `equipment_id` |
| `equipment_assets` | `equipment` | `maintenance_log` | `equipment` |
| `forecast_action_rules` | `id` | `forecast_action_log` | `rule_id` |
| `greenhouses` | `id` | `alert_log` | `greenhouse_id` |
| `greenhouses` | `id` | `camera_zone_map` | `greenhouse_id` |
| `greenhouses` | `id` | `crop_events` | `greenhouse_id` |
| `greenhouses` | `id` | `crops` | `greenhouse_id` |
| `greenhouses` | `id` | `daily_summary` | `greenhouse_id` |
| `greenhouses` | `id` | `equipment` | `greenhouse_id` |
| `greenhouses` | `id` | `equipment_state` | `greenhouse_id` |
| `greenhouses` | `id` | `forecast_action_log` | `greenhouse_id` |
| `greenhouses` | `id` | `forecast_deviation_log` | `greenhouse_id` |
| `greenhouses` | `id` | `greenhouse_sensor_config` | `greenhouse_id` |
| `greenhouses` | `id` | `image_observations` | `greenhouse_id` |
| `greenhouses` | `id` | `model_predictions` | `greenhouse_id` |
| `greenhouses` | `id` | `observations` | `greenhouse_id` |
| `greenhouses` | `id` | `plan_journal` | `greenhouse_id` |
| `greenhouses` | `id` | `planner_lessons` | `greenhouse_id` |
| `greenhouses` | `id` | `positions` | `greenhouse_id` |
| `greenhouses` | `id` | `pressure_groups` | `greenhouse_id` |
| `greenhouses` | `id` | `sensors` | `greenhouse_id` |
| `greenhouses` | `id` | `setpoint_changes` | `greenhouse_id` |
| `greenhouses` | `id` | `setpoint_plan` | `greenhouse_id` |
| `greenhouses` | `id` | `setpoint_snapshot` | `greenhouse_id` |
| `greenhouses` | `id` | `shelves` | `greenhouse_id` |
| `greenhouses` | `id` | `soil_moisture_targets` | `greenhouse_id` |
| `greenhouses` | `id` | `switches` | `greenhouse_id` |
| `greenhouses` | `id` | `system_state` | `greenhouse_id` |
| `greenhouses` | `id` | `water_systems` | `greenhouse_id` |
| `greenhouses` | `id` | `weather_forecast` | `greenhouse_id` |
| `greenhouses` | `id` | `zones` | `greenhouse_id` |
| `image_observations` | `id` | `observations` | `image_observation_id` |
| `irrigation_schedule` | `id` | `irrigation_log` | `schedule_id` (retired compatibility; canonical schedule is `v_irrigation_schedule_current`, events are `v_irrigation_fertigation_runs`) |
| `observations` | `id` | `treatments` | `observation_id` |
| `planner_lessons` | `id` | `planner_lessons` | `superseded_by` |
| `positions` | `id` | `crop_events` | `position_id` |
| `positions` | `id` | `crops` | `position_id` |
| `positions` | `id` | `harvests` | `position_id` |
| `positions` | `id` | `observations` | `position_id` |
| `positions` | `id` | `sensors` | `position_id` |
| `positions` | `id` | `treatments` | `position_id` |
| `pressure_groups` | `id` | `water_systems` | `pressure_group_id` |
| `shelves` | `id` | `positions` | `shelf_id` |
| `zones` | `id` | `alert_log` | `zone_id` |
| `zones` | `id` | `crops` | `zone_id` |
| `zones` | `id` | `equipment` | `zone_id` |
| `zones` | `id` | `image_observations` | `zone_id` |
| `zones` | `id` | `observations` | `zone_id` |
| `zones` | `id` | `sensors` | `zone_id` |
| `zones` | `id` | `shelves` | `zone_id` |
| `zones` | `id` | `water_systems` | `zone_id` |

<!-- END AUTO-ERD -->

Pydantic models in `verdify_schemas/` are intentionally standalone — no cross-imports between models, no `ForeignKey` fields, no nested model-refs beyond what one row needs on its own. The relationships below are **documented**, not enforced at the schema level.

## Why document, don't enforce

Enforcing FK relationships in Pydantic would mean one of:

1. **Embedded refs:** `CropEvent.crop: Crop` — forces every consumer that wants a `CropEvent` to also hydrate the parent `Crop`. Over-fetches on the common "just show the event" case, and pulls every model file into every other file's import graph. Circular-import hell.
2. **ID-typed references with runtime resolvers:** `CropEvent.crop_id: Annotated[int, MustExist(Crop)]` — requires a live DB connection at validation time. Schemas are supposed to be pure — they describe shape, not liveness. Every test that constructs a fake `CropEvent` would need to stand up a Postgres.
3. **String-ref placeholders:** `crop_id: int  # FK → crops.id` — this is just a comment.

We already have the real constraint system: **Postgres itself.** Every FK listed below is a real `REFERENCES` declaration in the DB (or a soft join on a well-known column name). The DB rejects orphan rows; the Pydantic layer handles shape. Stop mixing the two.

## The relationship map

```mermaid
erDiagram
    crops ||--o{ crop_events : "crop_id"
    crops ||--o{ observations : "crop_id"
    crops ||--o{ harvests : "crop_id"
    crops ||--o{ treatments : "crop_id"
    crops ||--o{ image_observations : "crop_id (via crops_observed JSONB)"
    crops ||--o{ lab_results : "crop_id"
    crops ||--o{ crop_target_profiles : "crop_type (soft)"

    plan_journal ||--|{ setpoint_plan : "plan_id"
    plan_journal ||--o{ setpoint_changes : "plan_id (soft — via reason)"

    setpoint_plan ||--o{ setpoint_changes : "dispatcher pushes matching plan_id"
    setpoint_changes ||--o| setpoint_snapshot : "value match (1% dead-band)"
    setpoint_changes ||--o{ setpoint_clamps : "parameter"

    planner_lessons }o--|| planner_lessons : "superseded_by (self-ref)"
    alert_log ||--o| alert_log : "acknowledged_by / resolved_by (string refs)"

    weather_forecast ||--o{ forecast_deviation_log : "ts + parameter"
    forecast_deviation_log }o--|| forecast_action_rules : "metric match"
    forecast_action_rules ||--o{ forecast_action_log : "rule_id"

    climate ||--o{ daily_summary : "date rollup"
    equipment_state ||--o{ daily_summary : "runtime rollup"
    energy ||--o{ daily_summary : "cost rollup"
    setpoint_plan ||--o{ v_plan_compliance : "waypoint match"
    v_plan_compliance ||--o{ v_plan_accuracy : "plan_id aggregate"
    v_planner_performance }o--|| daily_summary : "date"
```

## Canonical FK table

Hard constraints (DB-enforced `REFERENCES` clauses):

| Parent | Child | FK column | Cascade |
|---|---|---|---|
| `crops.id` | `crop_events.crop_id` | `crop_id` | no |
| `crops.id` | `observations.crop_id` | `crop_id` | no |
| `crops.id` | `harvests.crop_id` | `crop_id` | no |
| `crops.id` | `treatments.crop_id` | `crop_id` | no |
| `crops.id` | `lab_results.crop_id` | `crop_id` | no |
| `observations.id` | `treatments.observation_id` | `observation_id` | no |
| `image_observations.id` | `observations.image_observation_id` | `image_observation_id` | no |
| `greenhouses.id` | everywhere | `greenhouse_id` | no |
| `planner_lessons.id` | `planner_lessons.superseded_by` | self-ref | no |
| `forecast_action_rules.id` | `forecast_action_log.rule_id` | `rule_id` | no |
| `irrigation_schedule.id` | `irrigation_log.schedule_id` | `schedule_id` | no; retired compatibility only |

Soft relationships (no FK; joins happen by well-known column matching):

| Parent | Child | Join column | Notes |
|---|---|---|---|
| `plan_journal.plan_id` | `setpoint_plan.plan_id` | `plan_id` | 1:N — every plan has 10-30 waypoints |
| `plan_journal.plan_id` | `setpoint_changes` | `reason` (substring) | planner encodes plan_id in the `reason` text |
| `setpoint_changes.parameter,ts` | `setpoint_snapshot.parameter,ts` | value match w/ 1% dead-band | FW-4 confirmation loop |
| `setpoint_changes.parameter` | `setpoint_clamps.parameter` | `parameter` | audit trail when planner values got clamped |
| `climate.ts`, `equipment_state.ts`, `energy.ts` | `daily_summary.date` | date bucket | nightly snapshot script rolls these up |
| `weather_forecast.ts,parameter` | `forecast_deviation_log.ts,parameter` | time+parameter match | hourly comparison |
| `override_events.override_type` | `v_override_activity_24h.override_type` | group by | view-only rollup |
| `setpoint_clamps.parameter` | `v_clamp_activity_24h.parameter` | group by | view-only rollup |
| `crop_events.event_type` | `CropEventType` literal | string | 10 valid types per `verdify_schemas/crops.py` |
| `alert_log.alert_type` | (no enum) | string | ~15 types in use; see alerts.py for severity enum |

## Self-referential chains

- **`planner_lessons.superseded_by`** → another `planner_lessons.id`. When a newer lesson replaces an older one, the old row sets `is_active=false` and `superseded_by` points to the new id. Follow the chain forward to find the currently-canonical form.
- **`alert_log.acknowledged_by` / `resolved_by`** → string operator name (not an FK to a users table; there isn't one). Values seen: `iris`, `jason`, `system`, `api`.

## View projections (many-to-one rollups)

Each view in `verdify_schemas/views.py` is a projection of one or more tables:

| View | Source tables | Projection key |
|---|---|---|
| `v_planner_performance` | `daily_summary`, `v_plan_compliance` | `date` |
| `v_plan_compliance` | `setpoint_plan`, `climate` | `planned_ts`, `parameter` |
| `v_plan_accuracy` | `v_plan_compliance` | `plan_id` |
| `v_dew_point_risk` | `climate` | `date` (24h aggregate of margin) |
| `v_water_budget` | `v_irrigation_fertigation_runs`, `daily_summary`, `equipment_state` fallback runtime | `date` |
| `v_daily_oscillation` | `equipment_state` | `date`, `equipment` |
| `v_override_activity_24h` | `override_events` | 24h window |
| `v_clamp_activity_24h` | `setpoint_clamps` | 24h window |

## Embedding the model inventory

Every model file in `verdify_schemas/` declares its DB table (or view/API envelope) in the module docstring. When in doubt:

```bash
grep -l "table row\|response envelope\|view row" verdify_schemas/*.py
```

gives the complete list.

## Follow-ups (Sprint 23+)

- Generate the Mermaid ERD above automatically from `information_schema.table_constraints` so it can't drift from reality. (Today: hand-maintained.)
- Add a `test_relationships.py` that walks every FK listed here and confirms both (a) the declared parent column exists and (b) every child row's parent exists. Makes the soft-ref rows hard.
- Consider an `Annotated[int, FKToCropId]` marker type that's a compile-time comment but a runtime no-op — documents intent in the model without re-introducing the circular-import problem.
