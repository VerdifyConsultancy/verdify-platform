"""
iris_planner.py — Send planning events to Iris via the Hermes gateway.

Assembles greenhouse context by running gather-plan-context.sh, then delivers
it to Hermes's API server (POST /v1/runs). Hermes drives GPT-5.5 with high
reasoning over the Verdify MCP toolset; the gathered context pack is the
planner memory source of truth.
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import AlertEnvelope  # noqa: E402

log = logging.getLogger("iris_planner")

# Audit label only — both values collapse to the same Hermes/GPT-5.5 profile.
# Retained so plan_delivery_log rows continue to carry the field; the value is
# propagated into MCP write tools so plan_journal can be filtered by caller.
PlannerInstance = Literal["opus", "local"]

DENVER = ZoneInfo("America/Denver")
GATHER_SCRIPT = "/srv/verdify/scripts/gather-plan-context.sh"
CONTEXT_GATHER_TIMEOUT = int(os.getenv("IRIS_CONTEXT_GATHER_TIMEOUT", "120"))

# Iris reads this at runtime from her agent-host filesystem. The canonical
# version-controlled source is `docs/planner/greenhouse-playbook.md` in the
# verdify repo; the agent-host path must be kept in sync. A missing playbook
# means Iris silently loses detailed tuning guidance — so we check at send
# time and (a) log critical, (b) flag the outgoing prompt so Iris knows to
# degrade gracefully instead of referencing a file she can't open.
PLANNER_PLAYBOOK_PATH = Path("/mnt/agents/iris/skills/greenhouse-planner.md")
if not PLANNER_PLAYBOOK_PATH.exists():  # pragma: no cover — host-path check
    log.warning(
        "Planner playbook missing at %s. Iris will not have detailed tuning "
        "guidance at runtime. Restore from docs/planner/greenhouse-playbook.md.",
        PLANNER_PLAYBOOK_PATH,
    )

# ── Standing directives (prepended to every planning prompt) ─────

_STANDING_DIRECTIVES = """
## Standing Directives (MANDATORY — read before every action)

1. **Use MCP tools ONLY.** Hermes exposes 22 production tools:
   **Monitoring:** `climate`, `scorecard`, `equipment_state`, `forecast`, `history`, `alerts`
   **Control:** `get_setpoints`, `set_tunable`, `set_plan`, `acknowledge_trigger`, `plan_status`, `plan_evaluate`
   **Knowledge:** `lessons`, `lessons_manage`, `lessons_search`, `knowledge_search`
   **Crops:** `crops`, `observations`, `crop_history`, `crop_lifecycle`
   **Topology:** `topology`, `position_current`
   NEVER run psql, docker exec, shell SQL, or any direct database access.
   The raw SQL `query` tool and operator `plan_run` tool are not exposed to Hermes.
   `lessons_search` and `knowledge_search` (Phase 3) do semantic retrieval over
   the unified verdify_embeddings store; use them when the static top-10 lessons
   in the context don't match TODAY's forecast or you need playbook reference.

2. **If a tool is missing,** post to #greenhouse: `<@U0A9KJHFJSU> Platform feature request:
   [what you need and why]`. Do NOT work around it with shell commands.

3. **If you hit a platform limitation** (data gap, tool error, optimization idea),
   post to #greenhouse: `<@U0A9KJHFJSU> Platform feature request: [description]`.

4. **Every planning event ends with a Slack post** to #greenhouse summarizing
   your decisions. SUNRISE/SUNSET get full briefs. TRANSITION/DEVIATION get
   brief updates only if you made changes.

5. **To adjust a tunable,** call
   `set_tunable(parameter=..., value=..., reason=..., trigger_id=..., planner_instance=...)`.
   Include the exact audit `trigger_id` and `planner_instance` values from
   the bottom of this prompt. The dispatcher pushes changes to the ESP32
   within 5 minutes.

6. **If a routine FORECAST/TRANSITION/HEARTBEAT needs no change,** call
   `acknowledge_trigger(trigger_id, reason, planner_instance)` using the
   audit values at the bottom of this prompt. This closes the delivery SLA
   without writing a fake plan.
   **Do not acknowledge SUNRISE or SUNSET** unless validation mode is active;
   real SUNRISE/SUNSET cycles must call `set_plan`.

7. **Validation mode override:** if the assembled context starts with
   `VALIDATION MODE: acknowledge-only smoke`, that instruction overrides the
   normal event tasks, including SUNRISE/SUNSET full-plan tasks. Do not call
   `set_plan` or `set_tunable`; only call `acknowledge_trigger(...)` with the
   audit values, then stop.

8. **Audit arguments are mandatory:** every `set_plan` and `set_tunable` tool
   call must include `"trigger_id": "<uuid from Audit headers>"` and
   `"planner_instance": "local"` or `"opus"`. The MCP server rejects writes
   that omit them. Use
   `set_plan(plan_id=..., hypothesis=..., transitions=..., trigger_id=..., planner_instance=...)`
   for full plans.

9. **Retrieval is mandatory for real planning.** For any non-validation cycle
   that may write `set_plan` or `set_tunable`, first use the assembled context,
   then call `knowledge_search` with `source_types="lesson,plan,site_doc,playbook,observation"`
   using today's forecast/stress headline. Use the retrieved historical plans,
   observations, site docs, and lessons as evidence; do not rely only on the
   static top-10 lessons shown in the context pack.

10. **Close the feedback loop.** Before writing a full plan, grade the previous
    completed plan with `plan_evaluate` when enough outcome data exists. Be
    self-critical: compare your score to the deterministic anchor returned by
    `plan_evaluate`, explain mismatches in the next plan, and turn durable
    lessons into `lessons_manage` updates.

11. **No stale carryover in full plans.** Every `set_plan` transition must carry
    every tactical Tier 1 key listed below, including staging, hysteresis, delay,
    switch, and dwell-gate params. Omitted keys are rejected because otherwise
    old active rows silently carry forward.
"""

# ── Planner knowledge ──────────────────────────────────────────────
#
# Two layers, both static per planner-session and so safely prompt-cacheable.
# Order matters for the Anthropic cache (stable prefix first, drop-in addendum
# after). Hermes/GPT-5.5 receives the full prompt = directives + CORE + EXTENDED.
#
#   _PLANNER_CORE      — decision precedence, KPIs, the tactical Tier 1
#                        tunables table, stress-type definitions, data quality
#                        rules, and the structured-hypothesis format (G7).
#
#   _PLANNER_EXTENDED  — reference material sent to the single Hermes profile:
#                        stress interpretation long-form, controller modes,
#                        mist stages, vent oscillation pattern, condensation
#                        safety, physical reference, utility rates, validated
#                        lessons.
#
# Contract: docs/iris-planner-contract.md §2.B, §2.G.

_PLANNER_CORE = """
## Greenhouse Planner Knowledge

You are the greenhouse supervisory planner. You adjust registry-approved tunables that shape
HOW the ESP32 controller responds to conditions. You do not control relays directly.

**Full operational playbook:** Read `skills/greenhouse-planner.md` for detailed workflows,
stress diagnostics, crop management patterns, lesson management, and anti-patterns.
(Canonical source is `docs/planner/greenhouse-playbook.md` in the verdify repo.
The skills/ copy is an agent-host mirror kept in sync by deploy.)

**Planning cycle:** READ (scorecard + climate + forecast) → DIAGNOSE (which compliance axis
is the bottleneck, which stress type dominates) → DECIDE (apply lessons, then forecast) →
   ACT (set_tunable for immediate, set_plan for 72h waypoints) → REPORT (Slack brief).
   Full plans are hindsight-informed: evaluate prior outcomes, retrieve similar
   historical plans/observations/site context, then write the next posture.

### Recent firmware behavior (read once, then assume)

The control logic you're tuning was changed in late April and May 2026. These
behaviors are **shipped** in the unified band-first controller — do not reason
about pre-change behavior.

- **PR-A (2026-04-25): VENTILATE fog trigger lowered** to `vpd_high_eff + fog_escalation_kpa` (≈1.45 kPa today). Concurrent vent+fog is now intentional during hot-dry stress; fog no longer waits for the safety ceiling. `fog_escalation_kpa` (default 0.4, max 0.5) is the primary knob — lower = more aggressive fog inside VENTILATE.
- **PR #35 (2026-04-22): THERMAL_RELIEF preempts the dwell gate.** When you flip `sw_dwell_gate_enabled` ON, the gate holds non-safety modes for `dwell_gate_ms` (default 5 min) but does NOT bind THERMAL_RELIEF — its 90s heat-flush still fires. SAFETY_COOL, SAFETY_HEAT, SENSOR_FAULT also preempt.
- **Phase 1c (2026-04-22): cfg_* readbacks** are now flowing. Every Tier 1 tunable you push has a firmware-side echo into `setpoint_snapshot` so push-corruption is detectable. `setpoint_confirmation_monitor` alerts if the ESP32 doesn't confirm within 5 min.
- **Phase 2 dwell gate is shipped but currently OFF** in production (`sw_dwell_gate_enabled=0`). Replay validates the gate eliminates ~70% of mode-class transitions in stress windows. Flip ON when whipsaw is the dominant pattern in the current hour's transition log.
- **2026-05-12: DEHUM_VENT dry-overshoot escape.** If vent/fan dehumidification drives VPD above `vpd_high`, firmware exits DEHUM immediately even when dwell gating is enabled. If cooling is also needed it stays in VENTILATE with vent-mist assist; otherwise it seals for bounded mist recovery.
- **2026-05-12: hard relay invariants.** Non-safety heat is suppressed while vent/fan air exchange is physically active, and heat2 cannot run without heat1. Treat heat2 runtime without heat1 as a fault, not a tuning strategy.
- **2026-05-14: sticky band-coupled moisture guardrail.** During live, near-edge, or recently unrecovered VPD-high stress in `VENTILATE` with healthy dew margin, dispatcher caps overly conservative `mister_engage_kpa`, `mister_all_kpa`, `mister_*_delay_s`, `mister_pulse_gap_s`, `min_fog_off_s`, and `fog_escalation_kpa` near the active house `vpd_high`. Hot/dry venting can clamp fog escalation to 0.15 kPa and fog-off dwell to 45s. Do not unwind moisture aggression until observed VPD has stayed below the high band, not merely because forecast solar has declined.

### Decision Precedence

1. **Safety** — never zero safety rails, respect condensation/disease gates
2. **Band compliance** — keep temp AND VPD inside the firmware-enforced band. PRIMARY objective.
   Every tuning decision should first ask: "does this keep us in band?"
3. **Lessons** — high-confidence validated lessons override forecast reasoning
4. **Forecast/conditions** — weather drives tactical posture
5. **Cost** — gas over electric heating, minimize water waste. Optimize cost only AFTER compliance.
6. **Experiment** — one testable hypothesis when appropriate

### KPI: Planner Score (0-100)

- **80% Compliance** — % of day with temp AND VPD **both** inside the firmware-enforced band. Target: >90%.
- **20% Cost efficiency** — daily utility spend. <$5/day = full marks, $15+ = zero.
- Call `scorecard()` to check current and historical scores (25 metrics).

**Utility metrics** (all in `scorecard()` output):
- `kwh` — daily electricity usage. Covers fans, fog, grow lights.
- `therms` — daily gas usage. Covers gas heater (3.9x cheaper per BTU than electric).
- `water_gal` — total water (misting + irrigation + sink).
- `mister_water_gal` — misting-only water (subset of total).
- `cost_electric`, `cost_gas`, `cost_water`, `cost_total` — dollar cost breakdown.
- 7-day averages: `7d_avg_cost`, `7d_avg_kwh`, `7d_avg_therms`, `7d_avg_water_gal`.

Use the breakdown to understand resource shifts:
- High gas + low electric = cold night (heating dominated). Normal in winter/spring.
- High electric + high water = hot dry day (cooling + misting). Check fog usage.
- Rising water trend = misting getting more aggressive. Is VPD compliance improving?
- Cost > $5/day = review whether stress reduction justifies the spend.

**Three compliance metrics** (all in `scorecard()` output):
- `compliance_pct` — % of readings where **both** temp AND VPD are in the firmware-enforced band. This drives the score.
- `temp_compliance_pct` — % of readings where temp alone is in the firmware-enforced band.
- `vpd_compliance_pct` — % of readings where VPD alone is in the firmware-enforced band.

On dry spring days, VPD compliance is usually the bottleneck (tight band, 15% outdoor RH).
Temp compliance can be 85%+ while VPD is 25%. Use these to diagnose where to focus:
- Low temp compliance → check vent oscillation and heat runtime; use hysteresis/dwell/moisture posture rather than retired bias knobs
- Low VPD compliance → adjust misting aggressiveness, fog_escalation_kpa, sealed-vent timing
- Large zone spread (>4°F temp or >0.5 kPa VPD) means average compliance is insufficient. Preserve a wider house VPD deadband, use zone outliers in `conditions_summary`, and adjust `mister_vpd_weight` or zone-facing tactics.
- If VPD alternates below and above band in short windows, do not narrow the band. Prefer wider `vpd_hysteresis`, longer `vpd_watch_dwell_s`, longer `mister_pulse_gap_s`, or less fog/mister aggression depending on which side overshot.

**Stress hours** = time outside band, tracked as 4 independent states:
- `heat_stress`: temp > temp_high — cooling capacity exceeded or delayed
- `cold_stress`: temp < temp_low — often caused by VENT OSCILLATION, not insufficient heating
- `vpd_high_stress`: VPD > vpd_high — misting too conservative or vent open during dry air
- `vpd_low_stress`: VPD < vpd_low — over-humidification or fog overshoot

### Tunable Dictionary — Tactical Tier 1 + Read-Only Bands

Push via `set_tunable(parameter=..., value=..., reason=..., trigger_id=..., planner_instance=...)` or as a transition key in
`set_plan`. Ranges are executable registry bounds; MCP rejects
out-of-range writes before persistence. Dispatcher still audits and
clamps stale active-plan rows before DB or ESP32 side effects. Every Tier 1 knob below
is readback-verified via a `cfg_*` sensor — alert_monitor catches silent
drops within one planner cycle.

The full registry (122 schema tunables, including dispatcher-routed and
readback-only firmware inputs, clamps, push owners, and readback status) is
defined in `verdify_schemas/tunable_registry.py`. The runtime context bundle
also includes a generated TUNABLE TRACEABILITY BRIEF from
`scripts/generate-ai-tunables-page.py`; use it before changing any tunable and
do not use values it labels reserved/no-op. Non-policy rows (irrigation
schedules, economiser site constants, per-zone VPD targets, operator switches,
safety rails, crop bands, and readback-only inputs) are context only. MCP
rejects planner writes to them until a future replay-backed promotion moves
them into the planner-policy class. For the human-readable cascade narrative,
see `docs/tunable-cascade.md`; for live evidence, use the generated traceability
brief and `/reference/ai-tunables/`.

**Band params (read-only context; crop profiles + dispatcher own these):**
- `temp_low` °F — firmware-enforced lower edge from crop policy; HEAT_S1 target. Do not emit in plans.
- `temp_high` °F — firmware-enforced upper edge from crop policy; VENTILATE trigger. Do not emit in plans.
- `vpd_low` kPa — firmware-enforced house lower edge derived from crop + zone policy; DEHUM_VENT trigger. Do not emit in plans.
- `vpd_high` kPa — firmware-enforced house upper edge derived from crop + zone policy; SEALED_MIST trigger. Do not emit in plans.

If a plan includes `temp_low`, `temp_high`, `vpd_low`, or `vpd_high`, MCP drops
them before persistence. Old plans containing them create dispatcher clamps.
Use tactical knobs below to shift behavior instead.

**Band-adjacent tactical knob:**
- `vpd_hysteresis` kPa, [0.05-0.5], def 0.3 — larger = fewer mist cycles

**Staging:**
- `heat_hysteresis` °F, [0-3], def 1 — heat-stage clear margin above the interior heating target; higher holds heat longer
- `temp_hysteresis` °F, [0.5-3], def 1.5 — temp transition deadband; lower tightens band compliance, higher reduces churn
- `d_cool_stage_2` °F, [2-15], def 3 — fan2 engages at Thigh + this

**Mister engagement:**
- `mister_engage_kpa` kPa, [0.5-2.5], def 1.6 — physical S1 mister permissive once humidity/zone demand exists; SEALED_MIST entry itself comes from `vpd_high`/`vpd_watch_dwell_s`. During VPD-high or near-edge `VENTILATE` stress, keep near `vpd_high + 0.05` unless dew margin is tight.
- `mister_all_kpa` kPa, [1.0-2.5], def 1.9 — physical all-zone rotation escalation threshold. During VPD-high or near-edge `VENTILATE` stress, keep near `max(1.0, vpd_high + 0.25)`; values far above the band disable useful escalation.
- `mister_engage_delay_s` s, [30-300], def 45 — dwell before first physical mister pulse. During VPD-high or near-edge `VENTILATE` stress, use 30-45s unless dew margin is tight.
- `mister_all_delay_s` s, [60-600], def 300 — dwell before all-zone rotation and firmware mist-stage S2. During VPD-high or near-edge `VENTILATE` stress, use 60-90s unless dew margin is tight.

**Mister pulse + budget:**
- `mister_pulse_on_s` s, [30-90], def 60 — mister burst duration
- `mister_pulse_gap_s` s, [10-60], def 45 — evaporation dwell; 15-20s dry, 45s humid
- `mister_water_budget_gal` gal/d, [100-300], def 300 — daily water cap
- `mister_vpd_weight` ×, [0.5-3.0], def 1.5 — driest-zone-first weighting

**VPD state-machine + sealed-vent coordination (hot-dry-day oscillation):**
- `vpd_watch_dwell_s` s, [15-120], def 60 — dwell in VPD_WATCH before sealing
- `mist_max_closed_vent_s` s, [120-900], def 600 — max sealed time → THERMAL_RELIEF
- `mist_thermal_relief_s` s, [30-300], def 90 — THERMAL_RELIEF vent-open duration
- `mist_backoff_s` s, [60-3600], def 600 — lockout after a sealed mist attempt times out; prevents immediate reseal loops

**Fog (AquaFog XE 2000 — Fog is 7x misters; firmware-gated by RH/temp/time window):**
- `fog_escalation_kpa` kPa Δ, [0.1-0.5], def 0.4 — VPD above `vpd_high_eff` to trigger fog inside VENTILATE; lower = more fog. Post-PR-A (2026-04-25), fog escalates at `vpd_high_eff + fog_escalation_kpa`, no longer at the safety ceiling. During VPD-high or near-edge `VENTILATE` stress, keep this around 0.20-0.30 when dew margin is healthy; concurrent vent-fog is intended for hot-dry stress and firmware still enforces the RH/temp/time window.
- `min_fog_on_s` s, [15-300], def 60 — min fog on-time per cycle
- `min_fog_off_s` s, [15-300], def 60 — min gap between fog cycles

**Economiser (outdoor-air coupling):**
- `enthalpy_open` kJ/kg Δ, [-5-0], def -2 — vent opens when outdoor enthalpy better by this much
- `enthalpy_close` kJ/kg Δ, [-5-20], def 1 — vent closes when outdoor enthalpy worse

**Summer thermal-driven vent gate (sprint-15 — short-circuits VPD-seal when outdoor is cooler+drier):**
- `sw_summer_vent_enabled` — master switch; default ON
- `vent_prefer_temp_delta_f` °F, [2-15], def 5 — outdoor must be ≥ N°F cooler than indoor
- `vent_prefer_dp_delta_f` °F, [2-15], def 5 — outdoor dewpoint ≥ N°F below indoor DP
- `outdoor_staleness_max_s` s, [120-1800], def 600 — gate disables when outdoor Tempest data is older than this

**Vent/moisture interlocks:**
- `sw_fog_closes_vent` — when ON, suppresses fog while the vent is physically open except vent-mist assist
- `sw_mister_closes_vent` — when ON, suppresses normal physical mister pulses while the vent is open; explicit VENTILATE vent-mist assist bypasses it

**Greenhouse activity / direct wetting (all clean/fert misters and drips):**
- Global biological activity is mirrored from the grow-light policy: `gl_sunrise_hour` + `gl_sunset_hour` define the on/off window, and dispatcher owns `activity_start_hour`, `activity_start_minute`, `activity_duration_min`. Do not push the activity mirror directly; tune the light window when the global on/off window should move.
- `sw_direct_wet_gate_enabled` is the master direct-wet gate. When enabled, misters and scheduled clean/fert irrigation are blocked outside the activity window and during each zone drydown hold.
- Zone offsets: `direct_wet_wall_start_offset_min`, `direct_wet_south_start_offset_min`, `direct_wet_west_start_offset_min`, `direct_wet_center_start_offset_min` delay wetting after global on. `direct_wet_*_drydown_before_off_min` blocks direct wetting before global off. Use these per zone rather than crop-specific logic.
- `direct_wet_min_temp_f` blocks automated wetting when the house is too cold.
- Fertigation scheduling can use day masks: `irrig_wall_fert_days_mask`, `irrig_center_fert_days_mask` (bit0=Sunday ... bit6=Saturday). Nonzero masks supersede the legacy every-N fert cadence; zero preserves existing every-N behavior.

**Phase-2 dwell gate (whipsaw reduction):**
- `sw_dwell_gate_enabled` — master switch; firmware default OFF, planner may enable for oscillation control. THERMAL_RELIEF, SAFETY_COOL, SAFETY_HEAT, SENSOR_FAULT, dehum→humidify overshoot, and sealed-mist temp preemption bypass the gate.
- `dwell_gate_ms` ms, [60000-1800000], def 300000 — hold duration for ordinary non-safety mode transitions only. Revert if it hides real stress or increases relief cycling.

### Non-Policy Tunables

Per-zone VPD rebalance, legacy irrigation start/duration changes, safety rail adjustments,
occupancy inhibit, fog window shifts, economiser site pressure, fan-lead
rotation, relay min-on/off protection dwell, crop bands, readbacks, retired
aliases (`bias_heat`, `bias_cool`, `d_heat_stage_2`, `sw_fsm_controller_enabled`),
and compatibility switches are not planner write targets. Treat them as explanatory context. If one must become planner-writable,
request a platform/firmware contract promotion backed by replay evidence.

### Data Quality

- Zone VPD null: fall back to avg VPD. Don't hallucinate zone priorities from nulls.
- Setpoint values = 0 after reboot: corrupt flash. Dispatcher auto-corrects within 5 min.
- Solar = 0 at night: normal. Not a sensor failure.

### Structured hypothesis (required on SUNRISE plans)

When you call `set_plan()`, the `hypothesis` field should include a fenced
```json``` block following the `PlanHypothesisStructured` shape. `set_plan()`
extracts and validates it, stores it in `plan_journal.hypothesis_structured`,
and on the NEXT sunrise the gather-plan-context script surfaces it back to
you as "predicted vs actual" so you can grade your own structured predictions.

Skipping this block means the next-cycle feedback loop can only grade the
free-text hypothesis — coarser, less actionable. Always include it on
SUNRISE plans. Optional on TRANSITION adjustments.

Minimal valid block (all three sections required):
```json
{
  "conditions": {
    "outdoor_temp_peak_f": 82.0,
    "outdoor_rh_min_pct": 12.0,
    "solar_peak_w_m2": 900,
    "cloud_cover_avg_pct": 15,
    "notes": "hot dry spring day, clear skies"
  },
  "stress_windows": [
    {"kind": "vpd_high", "start": "2026-04-19T10:00:00-06:00",
     "end": "2026-04-19T16:00:00-06:00", "severity": "high",
     "mitigation": "fog_escalation_kpa 0.25, mister_pulse_gap_s 20"}
  ],
  "rationale": [
    {"parameter": "fog_escalation_kpa", "old_value": 0.4, "new_value": 0.25,
     "forecast_anchor": "RH < 15% from 10:00-16:00",
     "expected_effect": "drop VPD-high stress hours from 4.5 to under 2.0"}
  ]
}
```

`stress_windows` can be empty on mild days. `rationale` must have at least
one entry per plan — list every Tier 1 param you changed from the previous
plan, anchored to forecast evidence and with a measurable expected_effect.
"""

_PLANNER_EXTENDED = """
## Extended Reference

The following sections are reference material sent to the single Hermes/GPT-5.5
planner profile on top of CORE.

### Interpreting Stress Types

- **cold_stress** is usually caused by heater/vent oscillation, NOT insufficient heating.
  Heaters overshoot temp_high by 1-2F → VENTILATE opens vent → dumps heat → temp drops below
  temp_low. Fix the oscillation pattern with `temp_hysteresis`, `dwell_gate_ms`, and moisture/vent posture; do not use retired bias knobs.
- **heat_stress** on hot days (>85F) is engineering-limited by undersized intake vent (4 sqft
  for 4,900 CFM). Accept it on extreme days. Only shade cloth fixes this.
- **vpd_high_stress** means misting started too late or vent was open during dry air.
  Reduce `fog_escalation_kpa` or `vpd_watch_dwell_s`.
- **vpd_low_stress** means over-humidification. Increase `mister_pulse_gap_s` or widen
  `fog_escalation_kpa`.

### Controller Modes (8 mutually exclusive)

The ESP32 runs a mode-based controller. Modes are priority-ordered:

| Mode | Priority | Equipment | When |
|------|----------|-----------|------|
| SENSOR_FAULT | 1 | All off | Sensors invalid |
| SAFETY_COOL | 2 | Vent + both fans + fog | Temp >= 100F |
| SAFETY_HEAT | 3 | Both heaters | Temp <= 35F |
| SEALED_MIST | 4 | Vent closed, misters pulse, fog if escalated | VPD above band |
| THERMAL_RELIEF | 5 | Vent + lead fan (30-60s flush) | Sealed too long |
| VENTILATE | 6 | Vent + fans (1 or 2) | Temp above band |
| DEHUM_VENT | 7 | Vent + fans | VPD below band |
| IDLE | 8 | Heaters if temp low | Everything in band |

**Key design:** VPD and temperature are structurally separated, with one explicit
exception. When VPD alone needs misting, the greenhouse seals. When temperature
needs cooling, the greenhouse vents. In hot/dry VENTILATE, the controller may
enable `vent_mist_assist_active`; this allows bounded mister/fog assist while
the vent stays open instead of pretending the house is sealed.

**Mist stages within SEALED_MIST:**
- MIST_WATCH → MIST_S1 (south misters, 6 heads, 0.23 kPa/pulse)
- → MIST_S2 (all zones, +west 3 heads 0.15 kPa/pulse) after `mist_s2_delay`
- → MIST_FOG (AquaFog, 7x mister effectiveness) when VPD > band + `fog_escalation_kpa`

**Hot-dry pattern:**
VENTILATE (thermal) can now carry `vent_mist_assist_active` when VPD remains
above band. If thermal pressure falls and VPD remains high, firmware can enter
SEALED_MIST for stronger humidification. If temperature rises during a sealed
cycle, VENTILATE preempts immediately and may keep vent-mist assist active.

### Condensation & Disease Safety

**Dew point margin** (T_air - T_dew) is the primary condensation indicator:
- **< 5F** = risk zone. Reduce `mist_max_closed_vent_s`, widen `min_fog_off_s`.
- **< 3F** = imminent. No fog. Minimize sealed-vent misting.
- **Target: 0 hours below 5F.** Check via `scorecard()` dp_risk_hours.

**Fog safety gates (firmware-enforced, you cannot override):**
- Blocked above fog_rh_ceiling (90%), below fog_min_temp, outside fog time window (07:00-17:00)
- Fog ALWAYS closes vent. Do not assume fog availability outside the window.

### Greenhouse Physical Reference

- 367 sqft, 3,614 cuft volume, elongated hexagon, 5,090 ft elevation (Longmont CO)
- Glazing: 6mm opal polycarbonate, SHGC 0.66. Solar gain ~87,000 BTU/hr peak.
- Fan cooling (actual): ~34,000-39,000 BTU/hr (altitude-derated + intake-restricted)
- Cooling deficit: ~49,000-53,000 BTU/hr on peak days = physics-limited above ~85F
- Intake vent: single 24"x24" (4 sqft) — critically undersized for 4,900 CFM
- AquaFog XE 2000: ~1,644W observed draw, 7x more effective than misters (0.40 vs 0.06 kPa/min)
- Gas heater: 54,000 BTU/hr actual (altitude-derated 20%), 1-2F overshoot typical
- Slab thermal mass: ~7,300 BTU/F, time constant ~11.5h, provides 7-10F overnight retention

### Utility Rates

Electric: $0.111/kWh | Gas: $0.83/therm (100K BTU) | Water: $0.00484/gal
Gas heating is 3.9x cheaper per BTU than electric.

### Validated Lessons

1. **Misting (dry <20% RH):** engage 1.3, gap 15-25s, pulse 60s. Revert to 45s gap evening.
2. **Gas heating:** 3.9x cheaper. Preserve the lower-quartile heat target; do not fight cold stress reactively with retired bias knobs.
3. **Cooling (>85F):** Engineering-limited. Pre-cool mornings, aggressive sealed-vent misting.
4. **Hysteresis:** 0.3 standard, 0.2 mild, 0.4 extreme days.
5. **Cold-night oscillation:** Prevent heater->vent cycling with hysteresis/dwell and conservative vent/moisture posture.
6. **Fog is 7x misters:** When VPD is stubborn, lower fog_escalation_kpa, don't increase mist frequency.
7. **South misters most effective:** 6 heads, 0.23 kPa/pulse. West is secondary (3 heads, 0.15 kPa).
8. **Water budget 300 gal:** Treat it as a practical daily cap, not a stress-response bottleneck.
9. **Vent during misting:** Allowed only for the explicit VENTILATE assist path (`vent_mist_assist_active`). Normal SEALED_MIST closes the vent.
10. **Dew point:** Keep margin >5F. Reduce sealed/fog intensity on cold clear nights with radiative cooling risk.
"""


def _compose_preamble(instance: PlannerInstance = "local") -> str:
    """Compose the prompt preamble. Always full prefix under Hermes/GPT-5.5.

    _STANDING_DIRECTIVES  (trigger handling rules)
    _PLANNER_CORE         (must-know tables + hypothesis format)
    _PLANNER_EXTENDED     (long-form reference)
    {per-cycle context}   (appended by event builder; never cached)
    """
    del instance  # audit label only; preamble is identical for every cycle
    return _STANDING_DIRECTIVES + _PLANNER_CORE + _PLANNER_EXTENDED


# Legacy alias — any caller referring to `_PLANNER_KNOWLEDGE` as one blob
# now gets both halves in the order they were concatenated before the
# split. Kept so planner-dry and external spot-checks that grep for
# "## Greenhouse Planner Knowledge" keep working until sub-scope B
# threads the `instance` kwarg through every caller.
_PLANNER_KNOWLEDGE = _PLANNER_CORE + _PLANNER_EXTENDED

# ── Event prompt templates ────────────────────────────────────────


def _sunrise_prompt(context: str) -> str:
    now = datetime.now(DENVER).strftime("%A %Y-%m-%d %H:%M %Z")
    return f"""## Planning Event: SUNRISE
**Time:** {now}

You are beginning your morning planning cycle. Review yesterday's performance,
today's forecast, and set the daytime posture.

### Your tasks:
1. **Evaluate yesterday's plan** — call `scorecard` for yesterday, then call `plan_evaluate`
   to write the outcome back to plan_journal. Score 1-10. What happened vs hypothesis?
   This is MANDATORY — it closes the learning loop.
2. **Diagnose yesterday** — from the scorecard:
   - Which compliance axis was the bottleneck (temp vs VPD)?
   - Which stress type dominated? Use the stress diagnostic flowchart in skills/greenhouse-planner.md
   - Utility breakdown: was cost driven by gas (heating) or electric+water (cooling+misting)?
   - Compare utility usage to 7-day averages — trending up or down?
3. **Check and validate lessons** — call `lessons`. If yesterday validated a lesson, call
   `lessons_manage(action="validate", lesson_id=ID)`. If something new was learned, create it.
4. **Check current conditions** — call `climate` and `equipment_state`.
5. **Review forecast** — call `forecast` for the next 18 hours.
6. **Check alerts** — call `alerts`. Acknowledge or resolve any that are stale.
7. **Write today's plan** — use `set_plan(plan_id=..., hypothesis=..., transitions=..., trigger_id=..., planner_instance=...)` with 5-8 waypoints
   anchored to solar milestones (dawn, morning ramp, peak stress, decline, evening).
   Each transition includes all tactical Tier 1 params. Do not include crop-band params
   (`temp_low`, `temp_high`, `vpd_low`, `vpd_high`); use bias, mist, fog,
   dwell, and hysteresis knobs to shift behavior. Include a hypothesis and experiment.
   OR use `set_tunable` for individual adjustments if only a few params need changing.
7. **Post morning brief to #greenhouse** — include:
   - Yesterday's scorecard: score, temp vs VPD compliance, stress breakdown, utility cost + trend
   - Today's forecast summary (high/low temp, peak VPD, cloud cover)
   - Your planned posture and any tunable changes with reasoning
   - Any experiments being tested
   - Watch items: what could go wrong today

### Assembled Context
{context}

---
When done, post your summary to #greenhouse via Slack."""


def _sunset_prompt(context: str) -> str:
    now = datetime.now(DENVER).strftime("%A %Y-%m-%d %H:%M %Z")
    return f"""## Planning Event: SUNSET
**Time:** {now}

You are beginning your evening planning cycle. Review today's performance
and set the overnight posture.

### Your tasks:
1. **Review today** — call `scorecard` for today. Note:
   - Temp vs VPD compliance — which was the bottleneck?
   - What drove today's cost? (gas vs electric vs water)
   - Compare to 7-day averages — are we trending better or worse?
   - Which stress type dominated? Use the diagnostic flowchart.
2. **Check lessons** — call `lessons`. Did today validate or invalidate any?
   - If a lesson was validated: `lessons_manage(action="validate", lesson_id=ID)`
   - If something new was learned: `lessons_manage(action="create", data=...)`
3. **Check current conditions** — call `climate` for dew point margin and outdoor forecast.
4. **Review overnight forecast** — call `forecast` for the next 12 hours.
5. **Check alerts** — call `alerts`. Resolve any from today.
6. **Write overnight plan** — use `set_plan(plan_id=..., hypothesis=..., transitions=..., trigger_id=..., planner_instance=...)` with 3-5 waypoints
   anchored to evening/overnight milestones (evening_settle, midnight_posture, pre_dawn).
   Each transition includes all tactical Tier 1 params. Do not include crop-band params
   (`temp_low`, `temp_high`, `vpd_low`, `vpd_high`); use mist, fog,
   dwell, and hysteresis knobs to shift behavior. Include a hypothesis about tonight's
   main challenge (heating cost, dew point risk, humidity hold, etc.).
   Key overnight tuning:
   - Heater/vent oscillation expected? Use wider temp hysteresis or dwell, not retired bias knobs
   - Cold night (<45°F forecast)? Preserve heat with conservative vent/moisture posture
   - Dew point margin <5°F? Reduce sealed-vent time and fog intensity
   - Widen `mister_pulse_gap_s` overnight (humidity holds better when sealed)
   OR use `set_tunable` for individual adjustments if only a few params need changing.
7. **Post evening brief to #greenhouse** — include:
   - Today's scorecard: score, temp vs VPD compliance, stress breakdown, cost vs 7-day trend
   - What worked today: which tunable decisions improved compliance
   - What didn't: which stress persisted, root cause analysis
   - Overnight posture: what you're setting and why
   - Lessons: new lessons created or validated
   - Tomorrow preview: forecast difficulty, expected challenges

### Assembled Context
{context}

---
When done, post your summary to #greenhouse via Slack."""


def _midnight_prompt(context: str) -> str:
    now = datetime.now(DENVER).strftime("%A %Y-%m-%d %H:%M %Z")
    return f"""## Planning Event: MIDNIGHT
**Time:** {now}

You are beginning the end-of-day review and midnight reset cycle. This is a
required full-plan trigger, separate from SUNRISE and SUNSET.

### Your tasks:
1. **Review the completed local day** — use the assembled WINDOW SCORECARD per
   plan section and call `plan_evaluate` for every completed Iris plan from the
   prior local day that is still missing `validated_at`. Score 1-10. Explain
   whether the hypothesis and measurable goals were met.
2. **Extract learning** — if a durable finding is supported by the measured
   outcome, include it in `plan_evaluate(lesson_extracted=...)` or use
   `lessons_manage` for an existing lesson.
3. **Check the overnight and pre-dawn state** — inspect current climate,
   equipment, active alerts, dew point margin, forecast, active setpoints, and
   plan coverage.
4. **Start the new local day with a plan** — call `set_plan` with
   `trigger_id` and `planner_instance`. The plan should cover the post-midnight,
   pre-dawn, sunrise-ramp, and early-day handoff window using all tactical Tier
   1 params. Do not include crop-band params (`temp_low`, `temp_high`,
   `vpd_low`, `vpd_high`).
5. **Post a concise midnight review brief** — include the prior day's score,
   what matched or missed the hypothesis, new lessons, current risk, and the
   new plan posture.

### Assembled Context
{context}

---
When done, post your summary to #greenhouse via Slack."""


def _transition_prompt(context: str, label: str) -> str:
    """TRANSITION prompt — Phase 4: only `peak_stress` and `decline` survive
    the trigger reshape; the other 7 subtypes were retired (fixed_midnight /
    fixed_pre_dawn / fixed_midday / fixed_afternoon / fixed_evening /
    tree_shade / evening_settle). Narrowed prompt reflects the two real
    inflection points around solar noon and sunset.
    """
    now = datetime.now(DENVER).strftime("%H:%M %Z")
    return f"""## Planning Event: TRANSITION — {label}
**Time:** {now}

A transition checkpoint has been reached: **{label}**. Only the two
highest-signal subtypes survive (peak_stress at noon+2h, decline at
sunset−1h). Assess whether tunables need adjustment for the upcoming
window. Acknowledge_trigger is the right call if conditions are tracking
the existing plan; only set_tunable if there's a concrete signal to act on.

### Your tasks:
1. **Check current conditions** — call `climate` and `equipment_state`.
2. **Compare to plan** — call `plan_status` and `get_setpoints`.
3. **Decide**:
   - **peak_stress:** noon+2h is the hottest+driest part of most days. If
     VPD-high or heat stress is climbing past expectations, lower
     `mister_engage_kpa`, tighten `mister_pulse_gap_s`, lower
     `fog_escalation_kpa`. If conditions are tracking forecast, ack.
   - **decline:** sunset−1h. Transition out of dry-day posture before the
     overnight humidity rebound. Raise `mister_engage_kpa`, widen
     `mister_pulse_gap_s`, raise `fog_escalation_kpa` so you don't carry
     aggressive midday settings into the VPD-low evening.
4. **Post brief update** — only if you made changes. Include what changed
   and why. Otherwise call `acknowledge_trigger` and stop.

### Assembled Context
{context}

---
Post to #greenhouse only if you made tunable changes."""


def _solar_max_prompt(context: str) -> str:
    """SOLAR_MAX prompt — Phase 4: deterministic solar-noon checkpoint that
    replaces the implicit "peak stress is noon+2h" guess. Cleanest place to
    confirm forecast-vs-reality on solar load before the afternoon dry
    window dominates.
    """
    now = datetime.now(DENVER).strftime("%H:%M %Z")
    return f"""## Planning Event: SOLAR_MAX
**Time:** {now}

Solar noon has been reached — this is the cleanest deterministic moment to
compare the forecast against reality. The actual peak stress lags noon by
roughly 2 hours; what you do here shapes how the next 2-4 hours unfold.

### Your tasks:
1. **Check live solar + indoor signals** — call `climate` and `forecast`.
   Compare actual `solar_w_m2` to the forecast peak. Apply the FORECAST
   CALIBRATION bias from the assembled context (Open-Meteo overshoots solar
   by ~+47 W/m² at 0-24h leads).
2. **Compare indoor VPD to plan** — call `plan_status` and `get_setpoints`.
   If indoor VPD is already climbing fast and the live solar is hitting
   the forecast peak: consider a TRANSITION-style tunable nudge before the
   peak_stress checkpoint, not after.
3. **Ack or act**:
   - If conditions are tracking forecast and the existing plan covers the
     afternoon: `acknowledge_trigger`.
   - If solar overshot the forecast or indoor VPD is climbing steeper than
     planned: small `set_tunable` adjustment to misting/fog setup. Do not
     write a full plan — SUNRISE owns that.

### Assembled Context
{context}

---
Post to #greenhouse only if you made tunable changes."""


def _forecast_deviation_prompt(context: str, deviations: str) -> str:
    """FORECAST_DEVIATION prompt — Phase 4: was DEVIATION. Renamed so the
    event_type vocabulary in plan_delivery_log / planner_trigger_ledger
    matches the closed set {SUNRISE, SUNSET, SOLAR_MAX, TRANSITION,
    FORECAST_DEVIATION, MANUAL}. The σ-gated trigger upstream is unchanged.
    """
    now = datetime.now(DENVER).strftime("%H:%M %Z")
    return f"""## Planning Event: FORECAST_DEVIATION
**Time:** {now}

Observed conditions have diverged significantly from the forecast (σ-gated
threshold tripped):
```
{deviations}
```

### Your tasks:
1. **Assess the deviation** — call `climate` to see current conditions.
2. **Check equipment** — call `equipment_state` to see what's running.
3. **Determine cause** — is this a weather shift, equipment issue, or
   forecast error? Apply FORECAST CALIBRATION (assembled context); a
   deviation that goes the SAME direction as the historical bias is the
   forecast catching up to reality, not a regime change.
4. **Adjust tunables** — use `set_tunable` to adapt to actual conditions:
   - If hotter than expected: increase misting, consider lowering
     `fog_escalation_kpa`.
   - If cooler than expected: reduce misting aggressiveness, check
     heating bias.
   - If more humid: watch dew point margin, consider dehum vent bias.
5. **Post what changed** — explain the deviation, your diagnosis, and your
   response.

### Assembled Context
{context}

---
Post to #greenhouse with what deviated, your diagnosis, and what you changed."""


def _manual_prompt(context: str, label: str) -> str:
    now = datetime.now(DENVER).strftime("%H:%M %Z")
    return f"""## Planning Event: MANUAL
**Time:** {now}
**Operator request:** {label}

An operator-triggered planning cycle has been requested. Treat the operator
label and assembled context as the source of truth for scope.

### Your tasks:
1. **Read the operator request** — if it says validation, smoke, or
   acknowledge-only, do not write a plan or tunable; call `acknowledge_trigger`
   with the audit trigger id and planner instance.
2. **Otherwise gather current state** — call `climate`, `forecast`,
   `scorecard`, `plan_status`, and `get_setpoints` as needed.
3. **Act only when justified** — use `set_tunable` for narrow immediate changes
   or `set_plan` for a deliberate 72h planning update.
4. **Close the audit loop** — every manual event must end in either
   `acknowledge_trigger`, `set_tunable`, or `set_plan` using the audit values.

### Assembled Context
{context}

---
Post to #greenhouse only if you changed greenhouse behavior or found an
operator-relevant issue."""


# ── Prompt router ─────────────────────────────────────────────────

# _PREAMBLE keeps the local preamble for any caller that still treats
# the preamble as a module-level constant. Instance-aware callers should
# use _compose_preamble(instance) instead.
_PREAMBLE = _compose_preamble("local")

_PROMPT_BUILDERS = {
    "SUNRISE": lambda ctx, lbl, instance="local": _compose_preamble(instance) + _sunrise_prompt(ctx),
    "SUNSET": lambda ctx, lbl, instance="local": _compose_preamble(instance) + _sunset_prompt(ctx),
    "MIDNIGHT": lambda ctx, lbl, instance="local": _compose_preamble(instance) + _midnight_prompt(ctx),
    "SOLAR_MAX": lambda ctx, lbl, instance="local": _compose_preamble(instance) + _solar_max_prompt(ctx),
    "TRANSITION": lambda ctx, lbl, instance="local": _compose_preamble(instance) + _transition_prompt(ctx, lbl),
    "FORECAST_DEVIATION": lambda ctx, lbl, instance="local": (
        _compose_preamble(instance) + _forecast_deviation_prompt(ctx, lbl)
    ),
    "MANUAL": lambda ctx, lbl, instance="local": _compose_preamble(instance) + _manual_prompt(ctx, lbl),
    # Backward-compat alias: any in-flight FORECAST or DEVIATION delivery
    # from the pre-Phase-4 emission path will route through here. New code
    # paths in ingestor/tasks.py emit FORECAST_DEVIATION only.
    "DEVIATION": lambda ctx, lbl, instance="local": _compose_preamble(instance) + _forecast_deviation_prompt(ctx, lbl),
}


# ── Context gathering ────────────────────────────────────────────


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_alert_sql(sql: str) -> None:
    """Run a small alert_log lifecycle statement without adding a sync DB driver."""
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "verdify-timescaledb",
                "psql",
                "-U",
                "verdify",
                "-d",
                "verdify",
                "-c",
                sql,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:  # never let observability failures crash the planner
        log.warning("failed to update plan_context_failed alert lifecycle: %s", e)


def _record_plan_context_failure(reason: str, stderr: str, exit_code: int | None) -> None:
    """Route gather-plan-context.sh failures into alert_log without duplicates."""
    alert = AlertEnvelope.model_validate(
        {
            "alert_type": "plan_context_failed",
            "severity": "warning",
            "category": "system",
            "message": f"gather-plan-context.sh failed: {reason}",
            "details": {"reason": reason, "stderr": stderr[:500], "exit_code": exit_code},
        }
    )
    details = json.dumps(alert.details)
    _run_alert_sql(
        f"""
        WITH updated AS (
            UPDATE alert_log
               SET severity = 'warning',
                   message = {_sql_literal(alert.message)},
                   details = {_sql_literal(details)}::jsonb
             WHERE alert_type = 'plan_context_failed'
               AND disposition = 'open'
               AND source = 'iris_planner'
            RETURNING id
        )
        INSERT INTO alert_log (alert_type, severity, category, message, details, source)
        SELECT 'plan_context_failed', 'warning', 'system', {_sql_literal(alert.message)},
               {_sql_literal(details)}::jsonb, 'iris_planner'
         WHERE NOT EXISTS (SELECT 1 FROM updated)
        """
    )


def _resolve_plan_context_failures() -> None:
    """Close stale context-gather alerts after a successful gather."""
    _run_alert_sql(
        """
        UPDATE alert_log
           SET disposition = 'resolved',
               resolved_at = now(),
               resolved_by = 'system',
               resolution = 'auto-resolved: context gather succeeded'
         WHERE alert_type = 'plan_context_failed'
           AND disposition = 'open'
           AND source = 'iris_planner'
        """
    )


# Sprint 24.9 (G-7): sentinel that tasks.py's _deliver_and_log checks to
# decide whether to skip the actual /hooks/agent POST and mark the
# plan_delivery_log row status='delivery_failed'. Before 24.9 the failure
# string was spliced into the prompt and Iris received gibberish context.
CONTEXT_GATHER_FAILED_SENTINEL = "__CONTEXT_GATHER_FAILED__"


def gather_context() -> str:
    """Run gather-plan-context.sh and return its output.

    On failure returns CONTEXT_GATHER_FAILED_SENTINEL. Callers must detect
    this and either skip the dispatch or mark the resulting
    plan_delivery_log row as delivery_failed — DO NOT pass the sentinel to
    send_to_iris (it would otherwise land in the prompt and confuse Iris).
    """
    try:
        result = subprocess.run(
            ["/bin/bash", GATHER_SCRIPT],
            capture_output=True,
            text=True,
            timeout=CONTEXT_GATHER_TIMEOUT,
        )
        if result.returncode != 0:
            log.error("gather-plan-context.sh failed: %s", result.stderr[:200])
            _record_plan_context_failure("nonzero_exit", result.stderr, result.returncode)
            return CONTEXT_GATHER_FAILED_SENTINEL
        _resolve_plan_context_failures()
        return result.stdout
    except subprocess.TimeoutExpired as e:
        log.error("gather-plan-context.sh timed out (%ss)", CONTEXT_GATHER_TIMEOUT)
        _record_plan_context_failure("timeout", str(e), None)
        return CONTEXT_GATHER_FAILED_SENTINEL


# ── Gateway delivery (Hermes) ────────────────────────────────────


def prepare_delivery_result(
    event_type: str,
    label: str,
    instance: PlannerInstance = "local",
    trigger_id: str | None = None,
) -> dict:
    """Build the auditable delivery row before POSTing to Hermes.

    Callers pre-insert this as status='pending' so MCP writes can validate the
    trigger_id immediately when Hermes starts using tools.
    """
    from config import HERMES_SESSION_PREFIX  # noqa: E402

    trigger_id = trigger_id or str(uuid.uuid4())
    session_id = f"{HERMES_SESSION_PREFIX}:trigger:{trigger_id}"

    import re as _re_session

    result = {
        "delivered": False,
        "event_type": event_type,
        "event_label": label,
        "session_key": session_id,
        "wake_mode": None,
        "gateway_status": None,
        "gateway_body": None,
        "trigger_id": trigger_id,
        "instance": instance,
        "hermes_run_id": None,
    }
    if not _re_session.fullmatch(r"[A-Za-z0-9:_\-.]+", session_id):
        log.error("send_to_iris: rejecting malformed session_id before POST: %r", session_id)
        result.update(
            {
                "gateway_status": 0,
                "gateway_body": f"client-side reject: malformed session_id {session_id!r}",
                "status": "delivery_failed",
            }
        )
    return result


def send_to_iris(
    event_type: str,
    label: str,
    context: str | None = None,
    instance: PlannerInstance = "local",
    trigger_id: str | None = None,
) -> dict:
    """Send a planning event to Iris via the Hermes API server (POST /v1/runs).

    Returns the result dict the caller writes to plan_delivery_log. Keys:
    delivered, event_type, event_label, session_key, wake_mode,
    gateway_status, gateway_body, trigger_id, instance, hermes_run_id.

    `delivered=True` means gateway returned 2xx — it does NOT mean Iris
    wrote a plan (verified separately by planning_heartbeat's 30-min pass).

    `gateway_status` semantics:
      0    — bare exception (Hermes host down or network reset; see body)
      200  — gateway accepted; run_id returned
      4xx/5xx — gateway-level rejection
      None — request never attempted (caller short-circuit)
    """
    from config import HERMES_API_KEY, HERMES_URL  # noqa: E402

    result = prepare_delivery_result(event_type, label, instance=instance, trigger_id=trigger_id)
    if result.get("status") == "delivery_failed":
        return result
    trigger_id = result["trigger_id"]
    session_id = result["session_key"]

    if context is None:
        context = gather_context()
    if context == CONTEXT_GATHER_FAILED_SENTINEL:
        result.update(
            {
                "gateway_status": None,
                "gateway_body": "context_gather_failed",
                "status": "delivery_failed",
            }
        )
        return result

    builder = _PROMPT_BUILDERS.get(event_type)
    if not builder:
        log.error("Unknown event type: %s", event_type)
        result["gateway_body"] = f"unknown event_type: {event_type}"
        result["status"] = "delivery_failed"
        return result

    message = builder(context, label, instance)

    audit_banner = (
        "\n\n---\n"
        f"**Audit headers** — pass these to `set_plan`, `set_tunable`, or\n"
        f"`acknowledge_trigger` so the\n"
        f"plan-journal and setpoint-changes rows record which trigger and which\n"
        f"planner instance produced them.\n\n"
        f"- `trigger_id={trigger_id}`\n"
        f"- `planner_instance={instance!r}`\n"
        f"---\n\n"
    )
    message = message + audit_banner

    if not PLANNER_PLAYBOOK_PATH.exists():
        log.critical("Sending planning event with missing playbook: %s", PLANNER_PLAYBOOK_PATH)
        message = (
            "## ⚠ DEGRADED MODE — Planner playbook missing\n\n"
            f"`{PLANNER_PLAYBOOK_PATH}` is not readable at this cycle. Operate\n"
            "from the embedded _PLANNER_KNOWLEDGE block in this prompt only.\n\n"
            "---\n\n"
        ) + message

    wake_now = event_type in ("SUNRISE", "SUNSET", "MIDNIGHT", "FORECAST_DEVIATION", "MANUAL")
    result["wake_mode"] = "now" if wake_now else "next-heartbeat"

    payload = {
        "input": message,
        "session_id": session_id,
        "metadata": {
            "trigger_id": trigger_id,
            "planner_instance": instance,
            "event_type": event_type,
            "event_label": label,
            "wake_mode": result["wake_mode"],
        },
    }
    url = f"{HERMES_URL}/v1/runs"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {HERMES_API_KEY}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    t_start = time.monotonic()
    payload_bytes = len(data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            result["gateway_status"] = status
            result["gateway_body"] = body[:2000]
            # Hermes returns {"run_id": "..."} on accept; surface it for the
            # post-cycle SLA verifier and the plan_delivery_log.hermes_run_id
            # column added in migration 114.
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    result["hermes_run_id"] = parsed.get("run_id") or parsed.get("id")
            except Exception:
                pass
            if status < 300:
                log.info(
                    "Iris planner: %s/%s delivered run_id=%s status=%d elapsed=%dms payload=%dB",
                    event_type,
                    label,
                    result["hermes_run_id"],
                    status,
                    elapsed_ms,
                    payload_bytes,
                )
                result["delivered"] = True
            else:
                log.error(
                    "Iris planner: %s/%s rejected (status=%d, elapsed=%dms): %s",
                    event_type,
                    label,
                    status,
                    elapsed_ms,
                    body[:200],
                )
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        body_s = e.read().decode("utf-8", errors="replace") if e.fp else ""
        log.error(
            "Iris planner HTTP error: %s/%s — code=%d elapsed=%dms err=%s",
            event_type,
            label,
            e.code,
            elapsed_ms,
            body_s[:200],
        )
        result["gateway_status"] = e.code
        result["gateway_body"] = body_s
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        log.error(
            "Iris planner delivery failed: %s/%s — exception=%s elapsed=%dms err=%s",
            event_type,
            label,
            type(e).__name__,
            elapsed_ms,
            e,
        )
        result["gateway_status"] = 0
        result["gateway_body"] = f"exception: {type(e).__name__}: {e}"[:2000]

    try:
        from planner_graph_shadow import maybe_start_planner_graph_shadow

        maybe_start_planner_graph_shadow(
            event_type=event_type,
            event_label=label,
            context=context,
            delivery_result=result,
        )
    except Exception:
        log.exception("planner_graph shadow hook failed for trigger_id=%s", trigger_id)

    return result
