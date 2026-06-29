# Planner I/O schema — inputs, outputs, allowed tunables, decision ledger

Authoritative consolidation for **L4 (#346)**: what the AI planner reads, what it
is allowed to write, the bounds, and the auditable decision→outcome chain. This
is the single source-of-truth doc that AC2 (I/O schema), AC3 (allowed tunables +
bounds), AC4 (write contract / lockout), and AC5 (decision ledger) point at.

- Orchestration decision (Hermes vs direct GPT-5): `docs/adr/0002-planner-hermes-vs-direct-gpt5.md`.
- Bounded-write contract semantics + SLA: `docs/iris-planner-contract.md` (v1.5).
- The registry is the bounds authority: `verdify_schemas/tunable_registry.py`.
- Control architecture + the band-vs-planner ownership split: `docs/CONTROL-ARCHITECTURE.md`.

> Verified live 2026-06-17 (ns `verdify-prod`, `verdify-db-0`). Numbers below are
> stamped; re-run the cited probes to refresh.

---

## 1. Inputs (11 categories)

The context is assembled by `scripts/gather-plan-context.sh` (a 14-section static
pack built from the `v_iris_planning_context` DB view + supplementary queries) and
augmented on demand by MCP read tools (`climate`, `history`, `forecast`,
`scorecard`, `equipment_state`, `get_setpoints`, `lessons`, `query`).

| # | Input | Where it comes from | Notes |
|---|---|---|---|
| 1 | Last 72h observed climate | gather §1 core view + §4 (24h hourly) + §7 DIF (7d); full 72h via MCP `history(metric='climate', hours=72)` | **24h is in-pack**; the deeper 72h window is reachable on demand, not prompted by default |
| 2 | Last 72h relay / equipment runtime | gather §9 (equipment runtime 24h); full via MCP `history` / `equipment_state` | 24h in-pack; 72h on demand |
| 3 | Forecast-vs-observed drift | gather §1 (forecast column) + the FORECAST_DEVIATION σ-watcher; `v_forecast_accuracy_daily` | drift breach is itself a trigger (see §4 triggers) |
| 4 | Current 72h forecast | gather §1 core view; MCP `forecast(hours=72)` | Open-Meteo feed |
| 5 | Compliance KPIs | gather §5 scorecard (today + 7-day trend) + §6 compliance (24h by zone); MCP `scorecard` | feasibility-aware grade (`fn_zone_band_grade`) |
| 6 | Mechanical runtime history | gather §9 equipment + §10 energy + §11 irrigation | |
| 7 | Greenhouse lessons | MCP `lessons` / `lessons_search`; `planner_lessons` | extracted from prior plan outcomes |
| 8 | Current tunables | MCP `get_setpoints`; gather §1/§3 | live device-confirmed values |
| 9 | Safety constraints | registry bounds (`_PLANNER_CORE` inline table) + gather §3 switches | the rails are read-only context (see §4 lockout) |
| 10 | Firmware-supported setpoints / bounds | `tunable_registry.py` — the authoritative per-tunable min/max + the MCP `registry_value_error` gate | **registry is authoritative**; the HA "TUNABLE CONSTRAINTS" block in the pack is a best-effort cross-check that may be absent |
| 11 | Physical-limit assumptions | gather §8 hydroponic + site pressure + the firmware physics invariants | |

The `planner_graph.contracts.PlannerContextPack` model is the run-store *wire
summary* of a planning run, **not** the live Hermes text pack — don't confuse the
two.

## 2. Outputs (3 MCP tools)

The planner's entire write surface is three MCP tools. Each is typed and
DB-validated before persistence — see `mcp/server.py`.

| Tool | Pydantic model(s) | DB target | Purpose |
|---|---|---|---|
| `set_plan` | `Plan` / `PlanTransition` / `ClimateIntent` (`verdify_schemas/plan.py`, `climate_intent.py`) + `PlanHypothesisStructured` | `setpoint_plan` (tactical waypoints) + `plan_journal` (hypothesis/experiment/expected) + `plan_delivery_log` (correlation) | write a 72h plan of time-based waypoints |
| `set_tunable` | registry-validated single param | `setpoint_plan` (`source='iris'`) | nudge one bounded tunable now |
| `acknowledge_trigger` | — | `plan_delivery_log` (`status='acked'`) | record "no change needed" for a trigger |

`set_plan` requires a `climate_intent` on every transition and a `trigger_id`
(UUID, from the prompt's audit-headers banner); a write missing the trigger_id is
rejected. The tactical Tier-1 params are required on every transition.

## 3. Allowed tunables + bounds (AC3)

The planner may write **only** `planner_pushable=True` registry tunables, each
clamped to its registry min/max (the same bounds the firmware enforces). As of
2026-06-17: **42 pushable of 229 total**. The directional-lever map:

1. **Night-dry bias — `night_vpd_bias_kpa`** (pushable, live). Bounds 0–0.25 kPa,
   `cfg_night_vpd_bias_kpa` readback. Adds to the OVERNIGHT VPD band on a smooth
   sin² weight peaking at solar midnight; raises the night dryness floor without
   moving `crop_band_anchors`. Pushed 30× in the last 36h.
2. **Humidify / dehumidify aggressiveness** — `mister_engage_kpa`,
   `mister_all_kpa`, `mister_vpd_weight`, `min_fog_on_s`, `vpd_watch_dwell_s`,
   `fog_escalation_kpa`. No dedicated daytime "humidify scalar": the daytime VPD
   band is crop-band-owned by design.
3. **Hysteresis / tolerance + runtime preferences** — `heat_hysteresis`,
   `cool_exit_hysteresis_f`, `temp_hysteresis`, `vpd_hysteresis`, the mister/fog
   timing knobs, and the water budget — all pushable with min/max + `cfg_*`
   readbacks, consumed by the live band-first controller.
4. **Forecast offsets** — `ClimateIntent.forecast_temp_bias_f` (±4 °F),
   `forecast_vpd_bias_kpa` (±0.4 kPa), `solar_precool_gain_f` (0–4 °F),
   `economizer_*_advantage_f`. These materialize live but are **asymmetric**:
   cooling / wetting / venting / pre-cool only — there is **no forecast→heat**
   path (a forecast cannot pre-warm the house).

**Retired / intentionally NOT exposed:** the directional heat/cool *target*
biases `bias_heat` / `bias_cool` are **retired and inert** under the band-first
controller (`band_heat_target_f` has no bias term). Flipping them
`planner_pushable` would hand the planner dead knobs — a real heat/cool-target
lever is firmware-v2 work (OTA-gated, out of L4 scope). The planner has no
heat-TARGET lever today and that is by design, not an oversight.

## 4. Write contract / lockout (AC4) — what the planner CANNOT control

Enforced at the MCP boundary + registry classification, and proven both
statically (`verdify_schemas/tests/test_tunable_registry.py::TestPlannerWriteContractLockout`)
and at runtime by the live ledger:

- **Deterministic target curve / `crop_band_anchors`** — all 52 band-curve anchors
  (`band_*_{sr,sm,ss,mid}`, `zone_vpd_*`, `zone_priority_*`) are `crop_band` class,
  non-pushable, and *dropped* from the `setpoint_plan` INSERT by `set_plan` before
  any actuation (`band_params_dropped`). Live proof: **0** band-anchor rows ever
  written by `source='iris'`.
- **Hard safety rails** — `safety_min`/`safety_max`/`safety_vpd_min`/`safety_vpd_max`
  (`push_owner='safety'`) and the whole `controller_safety` class are non-pushable;
  `set_plan` rejects any plan carrying a non-policy param. Live proof: **0**
  safety-rail rows ever written by `iris`.
- **FSM / emergency** — `sw_fsm_controller_enabled` is non-pushable and listed in
  `FORCED_ON_SWITCH_PARAMS`, so `set_plan` force-rewrites any attempt to disable it
  back to `1.0`. Live proof: of **260** `sw_fsm_controller_enabled` rows the planner
  produced (Apr 27–May 23), **all 260 are value `1.0`** — the planner could never
  turn the deterministic controller off.

## 5. Decision ledger + outcome scoring (AC5)

The auditable decision→outcome chain (all live, mechanism MET):

1. `set_plan` writes `plan_journal` (hypothesis / experiment / expected_outcome /
   params_changed / climate_intents) + the `setpoint_plan` waypoints.
2. `mcp.server.plan_evaluate` (server.py:1388) writes `outcome_score` /
   `actual_outcome` / `lesson_extracted`, computes `fn_plan_anchor_score` into
   `anchor_score`, and warns when `|outcome − anchor| > 2`.
3. Lessons flow to `planner_lessons` (and back into input #7).
4. `planner_trigger_ledger` reconciles trigger → delivery → plan for SLA/health.

Live auditability snapshot (2026-06-17, `plan_journal`): **265 plans total, 263
outcome-scored, 137 anchor-scored, 131 with extracted lessons.** Latest plans:
`iris-20260617-0533`, `-0105`, `-0017` (SUNRISE/MIDNIGHT/SUNSET cycles firing).

Re-probe:
```
kubectl exec -n verdify-prod verdify-db-0 -c postgres -- psql -U verdify -d verdify -tAc \
  "SELECT count(*), count(*) FILTER (WHERE outcome_score IS NOT NULL), \
          count(*) FILTER (WHERE anchor_score IS NOT NULL) FROM plan_journal"
```

## 6. Triggers (when the planner runs)

`PLANNER_TRIGGER_MATRIX` (`ingestor/tasks/_common.py`), fired by the 60s planning
heartbeat (`ingestor/tasks/heartbeat.py`): **MIDNIGHT** (end-of-day review),
**SUNRISE**, **SOLAR_MAX**, **TRANSITION:peak_stress**, **TRANSITION:decline**,
**SUNSET**, **FORECAST_DEVIATION** (σ-gated observed-vs-forecast breach),
**MANUAL** (MCP `plan_run`), and **WEEKLY** (deep performance review + strategy
adjustment, materialized once per week on the review weekday). MIDNIGHT and WEEKLY
are review-cadence triggers; the solar set drives tactical day-shape planning;
FORECAST_DEVIATION is the dynamic-replanning path.
