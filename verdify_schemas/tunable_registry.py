"""Tunable registry — Phase-1 single source of truth for every tunable.

Consolidates what used to live in four separate places that drifted:
- `verdify_schemas.tunables.ALL_TUNABLES` (canonical schema enum)
- `mcp.server.TIER1` (hand-maintained planner allowlist)
- `ingestor.entity_map.SETPOINT_MAP` (dispatcher route)
- `ingestor.entity_map.CFG_READBACK_MAP` (firmware readback)
- `firmware/greenhouse/controls.yaml` clamp pairs

Each new tunable previously required 4 updates kept in sync by hand. Sprint-15
and sprint-15.1 each hit this as the drift cause of planner-rejected pushes
("not a Tier 1 tunable"). This registry is the single definition; TIER1 +
SETPOINT_MAP + CFG_READBACK_MAP become computed views.

Phase 1b.3: registry covers every SETPOINT_MAP entry. MCP set_tunable gates
on PLANNER_PUSHABLE_REG so only canonical planner-policy tunables are writable
from the planner. Operator, crop-band, safety, readback, and retired values remain
visible for traceability but are not planner write targets.

Phase 1c adds cfg_* readbacks for the last fire-and-forget tunables; Phase 1d
retires the legacy tunables.py layer so ALL_TUNABLES derives from here.

Readback-only params (fallback_window_s, outdoor_temp_f, outdoor_dewpoint_f)
are registered with no SETPOINT_MAP route. They are not planner-pushable, but
they are still part of the control contract because firmware consumes or
publishes them and the planner needs to understand their evidence surface.

See the rewrite plan at
`.claude-agents/iris-dev/plans/yo-iris-dev-you-help-humming-stonebraker.md`
for the full roadmap.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel


class TunableDef(BaseModel):
    """One row in the tunable registry. Each field has exactly one owner.

    Attributes:
        name: canonical name (snake_case). Matches entry_map.SETPOINT_MAP value.
        kind: numeric (float/int) or switch (bool) or enum (int mapped to symbolic).
        min, max: schema-level bounds (advisory for planner UI). None if unbounded.
        default: firmware default from `greenhouse_types.h::default_setpoints()`.
        fw_clamp_lo, fw_clamp_hi: dispatcher + firmware clamp. Must match the
            ESPHome number entity min_value/max_value in
            firmware/greenhouse/tunables.yaml. Drift guard checks this every CI run.
        esp_object_id: ESPHome entity object_id (slug). Same string as
            SETPOINT_MAP key today for dispatcher-routed tunables. None for
            readback-only firmware inputs.
        cfg_readback_object_id: `cfg_*` sensor slug, if firmware publishes one.
            None ⇒ fire-and-forget; must document rationale in `notes`.
        push_owner: who is the source of truth.
        planner_pushable: true only for canonical planner-policy knobs. Effective
            MCP write access is the intersection of this flag and `control_class`.
        tier: 1 ⇒ canonical planner-policy surface in `_PLANNER_CORE`; 2 ⇒
            visible contract/context only.
        enum_values: for `kind == "enum"`, int value ↔ symbolic name.
        notes: anything human.
    """

    name: str
    kind: Literal["numeric", "switch", "enum"]
    min: float | None = None
    max: float | None = None
    default: float
    fw_clamp_lo: float | None = None
    fw_clamp_hi: float | None = None
    esp_object_id: str | None
    cfg_readback_object_id: str | None = None
    push_owner: Literal["planner", "band", "schedule", "safety", "operator", "dispatcher_default", "firmware_internal"]
    planner_pushable: bool = True
    tier: Literal[1, 2] = 1
    enum_values: dict[str, int] | None = None
    notes: str = ""

    @property
    def control_class(
        self,
    ) -> Literal["planner_policy", "crop_band", "scheduled_policy", "controller_safety", "readback_context", "retired"]:
        """Canonical cleanup class used by planner/MCP/site surfaces."""
        return tunable_control_class(self.name, self)


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY — complete coverage for every SETPOINT_MAP entry. Entries are
# grouped by functional area.
#
# Tier assignment rule: tier=1 is the planner's canonical policy surface (must
# match MCP TIER1 and PLAN_REQUIRED_PARAMS). Everything else (crop bands,
# operator toggles, safety rails, irrigation schedule knobs, readback-only
# inputs, and retired aliases) is tier=2 context and MCP rejects planner writes.
# ─────────────────────────────────────────────────────────────────────────────

REGISTRY: dict[str, TunableDef] = {
    # ─────────────────────────────────────────────────────────────────────
    # Crop band (push_owner="band"). Dispatcher pushes every planner cycle
    # from the active crop profile. The planner sees these as read-only context and
    # MCP rejects planner writes.
    # ─────────────────────────────────────────────────────────────────────
    "temp_low": TunableDef(
        name="temp_low",
        kind="numeric",
        min=30.0,
        max=80.0,
        default=40.0,
        fw_clamp_lo=30.0,
        fw_clamp_hi=80.0,
        esp_object_id="set_temp_low__f",
        cfg_readback_object_id="cfg___temp_low___f_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
        notes="Crop band — dispatcher pushes every cycle. Firmware default wide (40) so safety rails govern if dispatcher silent.",
    ),
    "temp_high": TunableDef(
        name="temp_high",
        kind="numeric",
        min=40.0,
        max=100.0,
        default=95.0,
        fw_clamp_lo=40.0,
        fw_clamp_hi=100.0,
        esp_object_id="set_temp_high__f",
        cfg_readback_object_id="cfg___temp_high___f_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
        notes="Crop band — dispatcher pushes every cycle.",
    ),
    "d_heat_stage_2": TunableDef(
        name="d_heat_stage_2",
        kind="numeric",
        min=2.0,
        max=15.0,
        default=5.0,
        fw_clamp_lo=2.0,
        fw_clamp_hi=15.0,
        esp_object_id="__heat_stage_2__f",
        cfg_readback_object_id="cfg___d_heat_stage_2___f_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Retired from live band-first control. Heat2 now latches at the lower crop-band edge; "
        "this value is retained only for legacy fallback/readback visibility.",
    ),
    "d_cool_stage_2": TunableDef(
        name="d_cool_stage_2",
        kind="numeric",
        min=2.0,
        max=15.0,
        default=3.0,
        fw_clamp_lo=2.0,
        fw_clamp_hi=15.0,
        esp_object_id="__cool_stage_2__f",
        cfg_readback_object_id="cfg___d_cool_stage_2___f_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes=(
            "Legacy compatibility value. The live band-first controller stages "
            "fan2 from cool_stage2_over_high_f instead; keep this out of "
            "planner waypoints."
        ),
    ),
    "cool_stage2_over_high_f": TunableDef(
        name="cool_stage2_over_high_f",
        kind="numeric",
        min=0.0,
        max=3.0,
        default=1.0,
        fw_clamp_lo=0.0,
        fw_clamp_hi=3.0,
        esp_object_id="cool_stage_2_over_high__f",
        cfg_readback_object_id="cfg___cool_s2_over_high___f_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes=("Live band-first fan2 threshold above temp_high. This replaces planner use of legacy d_cool_stage_2."),
    ),
    "cool_exit_hysteresis_f": TunableDef(
        name="cool_exit_hysteresis_f",
        kind="numeric",
        min=0.3,
        max=3.0,
        default=1.5,
        fw_clamp_lo=0.3,
        fw_clamp_hi=3.0,
        esp_object_id="cool_exit_hysteresis__f",
        cfg_readback_object_id="cfg___cool_exit_hyst___f_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Cooling hold exits at temp_high minus this margin in the live band-first controller.",
    ),
    "cold_vent_guard_delta_f": TunableDef(
        name="cold_vent_guard_delta_f",
        kind="numeric",
        min=0.0,
        max=15.0,
        default=10.0,
        fw_clamp_lo=0.0,
        fw_clamp_hi=15.0,
        esp_object_id="cold_vent_guard_delta__f",
        cfg_readback_object_id="cfg___cold_vent_guard_delta___f_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes=(
            "Cold-outdoor ventilation guard. Outdoor air below temp_low minus "
            "this delta requires extra cooling margin before opening the vent."
        ),
    ),
    "sw_cool_all_fans_at_high_enabled": TunableDef(
        name="sw_cool_all_fans_at_high_enabled",
        kind="switch",
        min=0.0,
        max=1.0,
        default=0.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id="cool_all_fans_at_high",
        cfg_readback_object_id="cfg___cool_all_fans_at_high",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="When enabled, VENTILATE runs both fans immediately above temp_high.",
    ),
    "temp_hysteresis": TunableDef(
        name="temp_hysteresis",
        kind="numeric",
        min=0.5,
        max=3.0,
        default=1.5,
        fw_clamp_lo=0.5,
        fw_clamp_hi=3.0,
        esp_object_id="temp_hysteresis__f",
        cfg_readback_object_id="cfg___temp_hyst___f_",
        push_owner="band",
        planner_pushable=True,
        tier=1,
        notes="Forecast-tuned temperature transition hysteresis. Wider values reduce mode churn; narrower values tighten band tracking.",
    ),
    "heat_hysteresis": TunableDef(
        name="heat_hysteresis",
        kind="numeric",
        min=0.0,
        max=3.0,
        default=1.0,
        fw_clamp_lo=0.0,
        fw_clamp_hi=3.0,
        esp_object_id="heat_hysteresis__f",
        cfg_readback_object_id="cfg___heat_hyst___f_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Forecast-tuned heat-stage clear margin above the interior heating target. Higher values hold heat longer.",
    ),
    "vpd_low": TunableDef(
        name="vpd_low",
        kind="numeric",
        min=0.1,
        max=1.0,
        default=0.35,
        fw_clamp_lo=0.1,
        fw_clamp_hi=1.0,
        esp_object_id="set_vpd_low_kpa",
        cfg_readback_object_id="cfg___vpd_low__kpa_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
        notes="Crop band low-VPD threshold. Dispatcher pushes every cycle.",
    ),
    "vpd_high": TunableDef(
        name="vpd_high",
        kind="numeric",
        min=0.4,
        max=3.0,
        default=2.80,
        fw_clamp_lo=0.4,
        fw_clamp_hi=3.0,
        esp_object_id="set_vpd_high_kpa",
        cfg_readback_object_id="cfg___vpd_high__kpa_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
        notes="Crop band high-VPD threshold; firmware seal entry trigger.",
    ),
    "vpd_hysteresis": TunableDef(
        name="vpd_hysteresis",
        kind="numeric",
        min=0.05,
        max=0.5,
        default=0.30,
        fw_clamp_lo=0.05,
        fw_clamp_hi=0.5,
        esp_object_id="vpd_hysteresis_kpa",
        cfg_readback_object_id="cfg___vpd_hyst__kpa_",
        push_owner="band",
        planner_pushable=True,
        tier=1,
        notes="In MCP TIER1 — planner tunes to widen/tighten seal exit during humid regimes.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Safety rails (push_owner="safety"). Operator-only. Planner cannot push
    # these (planner_pushable=False) — drift guard will keep MCP from
    # accidentally exposing them via set_tunable.
    # ─────────────────────────────────────────────────────────────────────
    "safety_min": TunableDef(
        name="safety_min",
        kind="numeric",
        min=30.0,
        max=60.0,
        default=45.0,
        fw_clamp_lo=30.0,
        fw_clamp_hi=60.0,
        esp_object_id="safety_min__f",
        cfg_readback_object_id="cfg___safety_min___f_",
        push_owner="safety",
        planner_pushable=False,
        tier=2,
        notes="Hard cold rail — SAFETY_HEAT mode trigger. Operator-only.",
    ),
    "safety_max": TunableDef(
        name="safety_max",
        kind="numeric",
        min=80.0,
        max=110.0,
        default=95.0,
        fw_clamp_lo=80.0,
        fw_clamp_hi=110.0,
        esp_object_id="safety_max__f",
        cfg_readback_object_id="cfg___safety_max___f_",
        push_owner="safety",
        planner_pushable=False,
        tier=2,
        notes="Hard hot rail — SAFETY_COOL mode trigger. Operator-only.",
    ),
    "safety_vpd_min": TunableDef(
        name="safety_vpd_min",
        kind="numeric",
        min=0.1,
        max=1.5,
        default=0.30,
        fw_clamp_lo=0.1,
        fw_clamp_hi=1.5,
        esp_object_id="safety_vpd_min_kpa",
        cfg_readback_object_id="cfg___safety_vpd_min__kpa_",
        push_owner="safety",
        planner_pushable=False,
        tier=2,
        notes="Hard low-VPD rail (vpd_min_safe). Operator-only.",
    ),
    "safety_vpd_max": TunableDef(
        name="safety_vpd_max",
        kind="numeric",
        min=2.5,
        max=3.0,
        default=2.50,
        fw_clamp_lo=2.5,
        fw_clamp_hi=3.0,
        esp_object_id="safety_vpd_max_kpa",
        cfg_readback_object_id="cfg___safety_vpd_max__kpa_",
        push_owner="safety",
        planner_pushable=False,
        tier=2,
        notes="Hard high-VPD rail (vpd_max_safe). Operator-only.",
    ),
    "safety_max_seal_margin_f": TunableDef(
        name="safety_max_seal_margin_f",
        kind="numeric",
        min=1.0,
        max=15.0,
        default=5.0,
        fw_clamp_lo=1.0,
        fw_clamp_hi=15.0,
        esp_object_id="safety_seal_margin__f",
        cfg_readback_object_id="cfg___safety_seal_margin___f_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Blocks new sealed-mist entries when temp is within this margin of safety_max.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Mister engage thresholds + delays (push_owner="planner", tier=1).
    # ─────────────────────────────────────────────────────────────────────
    "mister_engage_kpa": TunableDef(
        name="mister_engage_kpa",
        kind="numeric",
        min=0.5,
        max=2.5,
        default=1.6,
        fw_clamp_lo=0.5,
        fw_clamp_hi=2.5,
        esp_object_id="vpd_mister_engage_kpa",
        cfg_readback_object_id="cfg_mister_engage__kpa_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes=(
            "Physical S1 mister-pulse threshold once humidity/zone demand exists. "
            "SEALED_MIST entry is vpd_high plus vpd_watch_dwell_s. During live "
            "VPD-high stress with healthy dew margin, dispatcher clamps overly "
            "conservative values back near the active vpd_high band."
        ),
    ),
    "mister_all_kpa": TunableDef(
        name="mister_all_kpa",
        kind="numeric",
        min=1.0,
        max=2.5,
        default=1.9,
        fw_clamp_lo=1.0,
        fw_clamp_hi=2.5,
        esp_object_id="vpd_mister_all_kpa",
        cfg_readback_object_id="cfg_mister_all__kpa_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes=(
            "Physical all-zone mister rotation threshold. Keep close to active "
            "vpd_high on hot/dry ventilation cycles; dispatcher clamps values "
            "that would prevent all-zone assist during live VPD-high or near-edge VENTILATE stress."
        ),
    ),
    "mister_engage_delay_s": TunableDef(
        name="mister_engage_delay_s",
        kind="numeric",
        min=30,
        max=300,
        default=45,
        fw_clamp_lo=30,
        fw_clamp_hi=300,
        esp_object_id="mister_engage_delay__s_",
        cfg_readback_object_id="cfg___mister_engage_delay__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Delay before first physical mister pulse during a sealed or vent-assist moisture cycle. "
        "Tightened to 30-300s so plans cannot postpone humidity response through an entire stress window.",
    ),
    "mister_all_delay_s": TunableDef(
        name="mister_all_delay_s",
        kind="numeric",
        min=60,
        max=600,
        default=300,
        fw_clamp_lo=60,
        fw_clamp_hi=600,
        esp_object_id="mister_all_delay__s_",
        cfg_readback_object_id="cfg___mister_all_delay__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Dwell before physical all-zone rotation. Also feeds the header mist-stage delay; "
        "stress defaults stay near 60-90s.",
    ),
    "mister_on_s": TunableDef(
        name="mister_on_s",
        kind="numeric",
        min=60,
        max=600,
        default=300,
        fw_clamp_lo=60,
        fw_clamp_hi=600,
        esp_object_id="mister_on__s_",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Deprecated legacy duty-cycle ON duration. Current pulse controller does not consume it.",
    ),
    "mister_off_s": TunableDef(
        name="mister_off_s",
        kind="numeric",
        min=120,
        max=900,
        default=600,
        fw_clamp_lo=120,
        fw_clamp_hi=900,
        esp_object_id="mister_off__s_",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Deprecated legacy duty-cycle OFF duration. Current pulse controller does not consume it.",
    ),
    "mister_all_on_s": TunableDef(
        name="mister_all_on_s",
        kind="numeric",
        min=120,
        max=600,
        default=480,
        fw_clamp_lo=120,
        fw_clamp_hi=600,
        esp_object_id="mister_all_on__s_",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Deprecated legacy all-zone duty-cycle ON duration. Current pulse controller does not consume it.",
    ),
    "mister_all_off_s": TunableDef(
        name="mister_all_off_s",
        kind="numeric",
        min=120,
        max=600,
        default=420,
        fw_clamp_lo=120,
        fw_clamp_hi=600,
        esp_object_id="mister_all_off__s_",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Deprecated legacy all-zone duty-cycle OFF duration. Current pulse controller does not consume it.",
    ),
    "mister_water_budget_gal": TunableDef(
        name="mister_water_budget_gal",
        kind="numeric",
        min=100,
        max=300,
        default=300.0,
        fw_clamp_lo=100,
        fw_clamp_hi=300,
        esp_object_id="mister_water_budget__gal_",
        cfg_readback_object_id="cfg___mister_water_budget__gal_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Daily mister water cap. In MCP TIER1. Practical operating range tightened to 100-300 gal/day.",
    ),
    "mister_max_runtime_min": TunableDef(
        name="mister_max_runtime_min",
        kind="numeric",
        min=5,
        max=480,
        default=120,
        fw_clamp_lo=5,
        fw_clamp_hi=480,
        esp_object_id="mister_max_runtime__min_",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Deprecated legacy runtime cap. Current water-budget logic uses mister_water_budget_gal.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Mister pulse-rotation model (tier=1). Phase-1c added cfg_* readbacks.
    # ─────────────────────────────────────────────────────────────────────
    "mister_pulse_on_s": TunableDef(
        name="mister_pulse_on_s",
        kind="numeric",
        min=30,
        max=90,
        default=60,
        fw_clamp_lo=30,
        fw_clamp_hi=90,
        esp_object_id="mister_pulse_on__s_",
        cfg_readback_object_id="cfg___mister_pulse_on__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Pulse-rotation ON duration per zone.",
    ),
    "mister_pulse_gap_s": TunableDef(
        name="mister_pulse_gap_s",
        kind="numeric",
        min=10,
        max=60,
        default=45,
        fw_clamp_lo=10,
        fw_clamp_hi=60,
        esp_object_id="mister_pulse_gap__s_",
        cfg_readback_object_id="cfg___mister_pulse_gap__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Pulse-rotation OFF (evap dwell) between zones.",
    ),
    "mister_vpd_weight": TunableDef(
        name="mister_vpd_weight",
        kind="numeric",
        min=0.5,
        max=3.0,
        default=1.5,
        fw_clamp_lo=0.5,
        fw_clamp_hi=3.0,
        esp_object_id="mister_vpd_weight",
        cfg_readback_object_id="cfg___mister_vpd_weight",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Weight on VPD gap in zone-selection scoring formula. Capped at 3.0 until replay proves higher values help.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Per-zone VPD targets (push_owner="band" — dispatcher pushes from crop
    # profile) + related scoring tunables. Tier=2: visible context, not
    # planner-writable unless promoted in a replay-backed contract PR.
    # ─────────────────────────────────────────────────────────────────────
    "vpd_target_south": TunableDef(
        name="vpd_target_south",
        kind="numeric",
        min=0.3,
        max=3.0,
        default=1.3,
        fw_clamp_lo=0.3,
        fw_clamp_hi=3.0,
        esp_object_id="vpd_target_south__kpa_",
        cfg_readback_object_id="cfg___vpd_target_south__kpa_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
        notes="Per-zone VPD target — dispatcher pushes from crop band.",
    ),
    "vpd_target_west": TunableDef(
        name="vpd_target_west",
        kind="numeric",
        min=0.3,
        max=3.0,
        default=1.2,
        fw_clamp_lo=0.3,
        fw_clamp_hi=3.0,
        esp_object_id="vpd_target_west__kpa_",
        cfg_readback_object_id="cfg___vpd_target_west__kpa_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
    ),
    "vpd_target_east": TunableDef(
        name="vpd_target_east",
        kind="numeric",
        min=0.3,
        max=3.0,
        default=1.0,
        fw_clamp_lo=0.3,
        fw_clamp_hi=3.0,
        esp_object_id="vpd_target_east__kpa_",
        cfg_readback_object_id="cfg___vpd_target_east__kpa_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
    ),
    "vpd_target_center": TunableDef(
        name="vpd_target_center",
        kind="numeric",
        min=0.1,
        max=3.0,
        default=0.8,
        fw_clamp_lo=0.1,
        fw_clamp_hi=3.0,
        esp_object_id="vpd_target_center__kpa_",
        cfg_readback_object_id="cfg___vpd_target_center__kpa_",
        push_owner="band",
        planner_pushable=False,
        tier=2,
        notes="Center zone has wider floor (0.1) to tolerate seedling propagation.",
    ),
    "mister_center_penalty": TunableDef(
        name="mister_center_penalty",
        kind="numeric",
        min=0.0,
        max=1.0,
        default=0.5,
        fw_clamp_lo=0.0,
        fw_clamp_hi=1.0,
        esp_object_id="mister_center_penalty",
        cfg_readback_object_id="cfg___mister_center_penalty",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Score penalty on center zone to discourage over-misting seedlings.",
    ),
    "east_adjacency_factor": TunableDef(
        name="east_adjacency_factor",
        kind="numeric",
        min=0.0,
        max=1.0,
        default=0.3,
        fw_clamp_lo=0.0,
        fw_clamp_hi=1.0,
        esp_object_id="east_adjacency_factor",
        cfg_readback_object_id="cfg___east_adjacency_factor",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Weight for borrowing east-zone signal into adjacent scoring (no east mister).",
    ),
    "min_fog_on_s": TunableDef(
        name="min_fog_on_s",
        kind="numeric",
        min=15,
        max=300,
        default=60,
        fw_clamp_lo=15,
        fw_clamp_hi=300,
        esp_object_id="min_fog_on__s_",
        cfg_readback_object_id="cfg___min_fog_on__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Fog minimum ON time — in MCP TIER1.",
    ),
    "min_fog_off_s": TunableDef(
        name="min_fog_off_s",
        kind="numeric",
        min=15,
        max=300,
        default=60,
        fw_clamp_lo=15,
        fw_clamp_hi=300,
        esp_object_id="min_fog_off__s_",
        cfg_readback_object_id="cfg___min_fog_off__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Fog minimum OFF time — in MCP TIER1.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Equipment timing (heaters/fans/vent/bursts).
    # ─────────────────────────────────────────────────────────────────────
    "min_heat_on_s": TunableDef(
        name="min_heat_on_s",
        kind="numeric",
        min=30,
        max=300,
        default=120,
        fw_clamp_lo=30,
        fw_clamp_hi=300,
        esp_object_id="min_heat_on__s_",
        cfg_readback_object_id="cfg___min_heat_on__s_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Heater relay protection dwell. Operator/firmware policy, not routine planner policy.",
    ),
    "min_fan_on_s": TunableDef(
        name="min_fan_on_s",
        kind="numeric",
        min=30,
        max=300,
        default=120,
        fw_clamp_lo=30,
        fw_clamp_hi=300,
        esp_object_id="min_fan_on__s_",
        cfg_readback_object_id="cfg___min_fan_on__s_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Fan relay protection dwell. Operator/firmware policy.",
    ),
    "min_fan_off_s": TunableDef(
        name="min_fan_off_s",
        kind="numeric",
        min=30,
        max=300,
        default=90,
        fw_clamp_lo=30,
        fw_clamp_hi=300,
        esp_object_id="min_fan_off__s_",
        cfg_readback_object_id="cfg___min_fan_off__s_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Fan relay protection dwell. Operator/firmware policy.",
    ),
    "min_vent_on_s": TunableDef(
        name="min_vent_on_s",
        kind="numeric",
        min=10,
        max=300,
        default=60,
        fw_clamp_lo=10,
        fw_clamp_hi=300,
        esp_object_id="min_vent_on__s_",
        cfg_readback_object_id="cfg___min_vent_on__s_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Vent actuator protection dwell. Operator/firmware policy, not routine planner policy.",
    ),
    "min_vent_off_s": TunableDef(
        name="min_vent_off_s",
        kind="numeric",
        min=10,
        max=300,
        default=60,
        fw_clamp_lo=10,
        fw_clamp_hi=300,
        esp_object_id="min_vent_off__s_",
        cfg_readback_object_id="cfg___min_vent_off__s_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Vent actuator protection dwell. Operator/firmware policy, not routine planner policy.",
    ),
    "lead_rotate_s": TunableDef(
        name="lead_rotate_s",
        kind="numeric",
        min=60,
        max=1800,
        default=600,
        fw_clamp_lo=60,
        fw_clamp_hi=1800,
        esp_object_id="lead_rotate__s_",
        cfg_readback_object_id="cfg___lead_rotate_timeout__s_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Lead/lag fan rotation timer. Operator-tuned.",
    ),
    "fan_burst_min": TunableDef(
        name="fan_burst_min",
        kind="numeric",
        min=1,
        max=60,
        default=10,
        fw_clamp_lo=1,
        fw_clamp_hi=60,
        esp_object_id="fan_burst__min_",
        cfg_readback_object_id="cfg___fan_burst__min_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Manual fan-burst duration placeholder. Current firmware does not consume it for timing.",
    ),
    "vent_bypass_min": TunableDef(
        name="vent_bypass_min",
        kind="numeric",
        min=1,
        max=60,
        default=10,
        fw_clamp_lo=1,
        fw_clamp_hi=60,
        esp_object_id="vent_bypass__min_",
        cfg_readback_object_id="cfg___vent_bypass__min_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Manual vent-bypass duration placeholder. Current firmware does not consume it for timing.",
    ),
    "fog_burst_min": TunableDef(
        name="fog_burst_min",
        kind="numeric",
        min=1,
        max=60,
        default=10,
        fw_clamp_lo=1,
        fw_clamp_hi=60,
        esp_object_id="fog_burst__min_",
        cfg_readback_object_id="cfg___fog_burst__min_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Manual fog-burst duration placeholder. Current firmware updates the value but does not consume it for timing.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Fog safety gates. Tier=2 — operator rarely tunes.
    # ─────────────────────────────────────────────────────────────────────
    "fog_rh_ceiling_pct": TunableDef(
        name="fog_rh_ceiling_pct",
        kind="numeric",
        min=75,
        max=98,
        default=90,
        fw_clamp_lo=75,
        fw_clamp_hi=98,
        esp_object_id="fog_rh_ceiling____",
        cfg_readback_object_id="cfg___fog_rh_ceiling____",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Fog blocks when indoor RH exceeds this. Safety-adjacent.",
    ),
    "fog_min_temp_f": TunableDef(
        name="fog_min_temp_f",
        kind="numeric",
        min=40,
        max=65,
        default=55,
        fw_clamp_lo=40,
        fw_clamp_hi=65,
        esp_object_id="fog_min_temp__f_",
        cfg_readback_object_id="cfg___fog_min_temp___f_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Fog blocks when indoor temp below this (evap cooling hurts in cold).",
    ),
    "fog_time_window_start": TunableDef(
        name="fog_time_window_start",
        kind="numeric",
        min=5,
        max=12,
        default=6,
        fw_clamp_lo=5,
        fw_clamp_hi=12,
        esp_object_id="fog_window_start__hr_",
        cfg_readback_object_id="cfg___fog_window_start__hour_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Start hour for fog eligibility window.",
    ),
    "fog_time_window_end": TunableDef(
        name="fog_time_window_end",
        kind="numeric",
        min=14,
        max=20,
        default=18,
        fw_clamp_lo=14,
        fw_clamp_hi=20,
        esp_object_id="fog_window_end__hr_",
        cfg_readback_object_id="cfg___fog_window_end__hour_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="End hour for fog eligibility window.",
    ),
    "sw_fog_stress_window_extend_enabled": TunableDef(
        name="sw_fog_stress_window_extend_enabled",
        kind="switch",
        min=0,
        max=1,
        default=0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id="fog_stress_window_extend_enabled",
        cfg_readback_object_id="cfg_fog_stress_window_extend_enabled",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes=(
            "Allows fog after the normal fog window during VPD-high stress. "
            "Firmware still enforces RH, temperature, latest-hour, and dew-margin gates."
        ),
    ),
    "fog_stress_window_latest_hour": TunableDef(
        name="fog_stress_window_latest_hour",
        kind="numeric",
        min=17,
        max=22,
        default=19,
        fw_clamp_lo=17,
        fw_clamp_hi=22,
        esp_object_id="fog_stress_window_latest_hour",
        cfg_readback_object_id="cfg_fog_stress_window_latest_hour",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Latest local hour for the bounded VPD-high fog stress extension.",
    ),
    "fog_stress_min_dew_margin_f": TunableDef(
        name="fog_stress_min_dew_margin_f",
        kind="numeric",
        min=5,
        max=15,
        default=10,
        fw_clamp_lo=5,
        fw_clamp_hi=15,
        esp_object_id="fog_stress_min_dew_margin_f",
        cfg_readback_object_id="cfg_fog_stress_min_dew_margin_f",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Minimum indoor temp-minus-dewpoint margin required for fog stress extension.",
    ),
    "max_relief_cycles": TunableDef(
        name="max_relief_cycles",
        kind="numeric",
        min=1,
        max=10,
        default=3,
        fw_clamp_lo=1,
        fw_clamp_hi=10,
        esp_object_id="max_relief_cycles",
        cfg_readback_object_id="cfg___max_relief_cycles",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Consecutive sealed→relief cycles before forced ventilation latch.",
    ),
    "dehum_aggressive_kpa": TunableDef(
        name="dehum_aggressive_kpa",
        kind="numeric",
        min=0.05,
        max=1.0,
        default=0.6,
        fw_clamp_lo=0.05,
        fw_clamp_hi=1.0,
        esp_object_id="dehum_aggressive_kpa",
        cfg_readback_object_id="cfg___dehum_aggressive__kpa_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Margin below vpd_low that upgrades dehumidification demand; validate_setpoints also caps it below vpd_low.",
    ),
    "vent_latch_timeout_ms": TunableDef(
        name="vent_latch_timeout_ms",
        kind="numeric",
        min=60000,
        max=7200000,
        default=1800000,
        fw_clamp_lo=60000,
        fw_clamp_hi=7200000,
        esp_object_id="vent_latch_timeout__ms_",
        cfg_readback_object_id="cfg___vent_latch_timeout__ms_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Maximum forced-vent latch duration after relief-cycle breaker trips.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # VPD-primary state machine dwell / lead / reopen (MCP TIER1 core).
    # ─────────────────────────────────────────────────────────────────────
    "vpd_watch_dwell_s": TunableDef(
        name="vpd_watch_dwell_s",
        kind="numeric",
        min=15,
        max=120,
        default=60,
        fw_clamp_lo=15,
        fw_clamp_hi=120,
        esp_object_id="vpd_watch_dwell__s_",
        cfg_readback_object_id="cfg___vpd_watch_dwell__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="VPD_WATCH observation window before engaging mist.",
    ),
    "mist_vent_close_lead_s": TunableDef(
        name="mist_vent_close_lead_s",
        kind="numeric",
        min=0,
        max=60,
        default=15,
        fw_clamp_lo=0,
        fw_clamp_hi=60,
        esp_object_id="mist_vent_close_lead__s_",
        cfg_readback_object_id="cfg___mist_vent_close_lead__s_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Reserved/no-op in current firmware. Exposed/read back but not consumed by the control loop.",
    ),
    "mist_max_closed_vent_s": TunableDef(
        name="mist_max_closed_vent_s",
        kind="numeric",
        min=120,
        max=900,
        default=600,
        fw_clamp_lo=120,
        fw_clamp_hi=900,
        esp_object_id="mist_max_closed_vent__s_",
        cfg_readback_object_id="cfg___mist_max_closed_vent__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Maximum time vent can stay closed during misting before thermal-relief burst.",
    ),
    "mist_vent_reopen_delay_s": TunableDef(
        name="mist_vent_reopen_delay_s",
        kind="numeric",
        min=0,
        max=120,
        default=45,
        fw_clamp_lo=0,
        fw_clamp_hi=120,
        esp_object_id="mist_vent_reopen_delay__s_",
        cfg_readback_object_id="cfg___mist_vent_reopen_delay__s_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Reserved/no-op in current firmware. Exposed/read back but not consumed by the control loop.",
    ),
    "mist_thermal_relief_s": TunableDef(
        name="mist_thermal_relief_s",
        kind="numeric",
        min=30,
        max=300,
        default=90,
        fw_clamp_lo=30,
        fw_clamp_hi=300,
        esp_object_id="mist_thermal_relief__s_",
        cfg_readback_object_id="cfg___mist_thermal_relief__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Mandatory vent-open relief duration if mist-closed-vent cap hit.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Greenhouse activity and direct-wet window gates.
    # ─────────────────────────────────────────────────────────────────────
    "activity_start_hour": TunableDef(
        name="activity_start_hour",
        kind="numeric",
        min=0,
        max=23,
        default=6,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="activity_start_hour",
        cfg_readback_object_id="cfg_activity_start_hour",
        push_owner="dispatcher_default",
        planner_pushable=False,
        tier=2,
        notes="Dispatcher-owned mirror of the main-light activity start hour. Direct-wet gates consume it so lights and wetting share one activity policy.",
    ),
    "activity_start_minute": TunableDef(
        name="activity_start_minute",
        kind="numeric",
        min=0,
        max=59,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=59,
        esp_object_id="activity_start_min",
        cfg_readback_object_id="cfg_activity_start_min",
        push_owner="dispatcher_default",
        planner_pushable=False,
        tier=2,
        notes="Dispatcher-owned mirror of the main-light activity start minute.",
    ),
    "activity_duration_min": TunableDef(
        name="activity_duration_min",
        kind="numeric",
        min=0,
        max=1440,
        default=1440,
        fw_clamp_lo=0,
        fw_clamp_hi=1440,
        esp_object_id="activity_duration__min_",
        cfg_readback_object_id="cfg_activity_duration_min",
        push_owner="dispatcher_default",
        planner_pushable=False,
        tier=2,
        notes="Dispatcher-owned mirror of main-light target runtime. Direct wetting is blocked outside this global biological activity window.",
    ),
    "direct_wet_min_temp_f": TunableDef(
        name="direct_wet_min_temp_f",
        kind="numeric",
        min=0,
        max=90,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=90,
        esp_object_id="direct_wet_min_temp__f_",
        cfg_readback_object_id="cfg_direct_wet_min_temp_f",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Minimum house temperature for automated direct wetting across misters, fert paths, and drips.",
    ),
    "direct_wet_south_start_offset_min": TunableDef(
        name="direct_wet_south_start_offset_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_south_start_offset__min_",
        cfg_readback_object_id="cfg_direct_wet_south_start_offset_min",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="South-zone direct-wet start offset from global activity start. Applies to clean/fert wetting paths in the zone.",
    ),
    "direct_wet_wall_start_offset_min": TunableDef(
        name="direct_wet_wall_start_offset_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_wall_start_offset__min_",
        cfg_readback_object_id="cfg___direct_wet_wall_start_offset__min_",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Wall drip direct-wet start offset from global activity start. Applies to wall clean/fert drip and flush paths.",
    ),
    "direct_wet_wall_drydown_before_off_min": TunableDef(
        name="direct_wet_wall_drydown_before_off_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_wall_drydown_before_off__min_",
        cfg_readback_object_id="cfg___direct_wet_wall_drydown_before_off__min_",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Wall drip drydown hold before global activity off. Applies to wall clean/fert drip and flush paths.",
    ),
    "direct_wet_south_drydown_before_off_min": TunableDef(
        name="direct_wet_south_drydown_before_off_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_south_drydown_before_off__min_",
        cfg_readback_object_id="cfg_direct_wet_south_drydown_before_off_min",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="South-zone drydown hold before global activity off. Applies to clean/fert wetting paths in the zone.",
    ),
    "direct_wet_west_start_offset_min": TunableDef(
        name="direct_wet_west_start_offset_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_west_start_offset__min_",
        cfg_readback_object_id="cfg_direct_wet_west_start_offset_min",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="West-zone direct-wet start offset from global activity start. Applies to clean/fert wetting paths in the zone.",
    ),
    "direct_wet_west_drydown_before_off_min": TunableDef(
        name="direct_wet_west_drydown_before_off_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_west_drydown_before_off__min_",
        cfg_readback_object_id="cfg_direct_wet_west_drydown_before_off_min",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="West-zone drydown hold before global activity off. Applies to clean/fert wetting paths in the zone.",
    ),
    "direct_wet_center_start_offset_min": TunableDef(
        name="direct_wet_center_start_offset_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_center_start_offset__min_",
        cfg_readback_object_id="cfg_direct_wet_center_start_offset_min",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Center-zone direct-wet start offset from global activity start. Applies to clean/fert wetting paths in the zone.",
    ),
    "direct_wet_center_drydown_before_off_min": TunableDef(
        name="direct_wet_center_drydown_before_off_min",
        kind="numeric",
        min=0,
        max=720,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=720,
        esp_object_id="direct_wet_center_drydown_before_off__min_",
        cfg_readback_object_id="cfg_direct_wet_center_drydown_before_off_min",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Center-zone drydown hold before global activity off. Applies to clean/fert wetting paths in the zone.",
    ),
    "sw_direct_wet_stress_override_enabled": TunableDef(
        name="sw_direct_wet_stress_override_enabled",
        kind="switch",
        min=0,
        max=1,
        default=0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id="direct_wet_stress_override_enabled",
        cfg_readback_object_id="cfg_direct_wet_stress_override_enabled",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes=(
            "Allows direct-wet mister zones to bypass drydown windows only during VPD-high stress. "
            "Firmware still enforces direct-wet master gate, min temp, irrigation/occupancy locks, latest hour, and dew margin."
        ),
    ),
    "direct_wet_stress_vpd_margin_kpa": TunableDef(
        name="direct_wet_stress_vpd_margin_kpa",
        kind="numeric",
        min=0,
        max=0.5,
        default=0.05,
        fw_clamp_lo=0,
        fw_clamp_hi=0.5,
        esp_object_id="direct_wet_stress_vpd_margin_kpa",
        cfg_readback_object_id="cfg_direct_wet_stress_vpd_margin_kpa",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="VPD excess above active vpd_high required before direct-wet stress override can open drydown-gated zones.",
    ),
    "direct_wet_stress_min_dew_margin_f": TunableDef(
        name="direct_wet_stress_min_dew_margin_f",
        kind="numeric",
        min=3,
        max=15,
        default=8,
        fw_clamp_lo=3,
        fw_clamp_hi=15,
        esp_object_id="direct_wet_stress_min_dew_margin_f",
        cfg_readback_object_id="cfg_direct_wet_stress_min_dew_margin_f",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Minimum indoor temp-minus-dewpoint margin required for direct-wet stress override.",
    ),
    "direct_wet_stress_latest_hour": TunableDef(
        name="direct_wet_stress_latest_hour",
        kind="numeric",
        min=17,
        max=24,
        default=22,
        fw_clamp_lo=17,
        fw_clamp_hi=24,
        esp_object_id="direct_wet_stress_latest_hour",
        cfg_readback_object_id="cfg_direct_wet_stress_latest_hour",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Latest local hour for direct-wet dry-stress override; 24 means through 23:59.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Irrigation schedule (push_owner="operator"). Tier=2 across the board.
    # ─────────────────────────────────────────────────────────────────────
    "irrig_wall_start_hour": TunableDef(
        name="irrig_wall_start_hour",
        kind="numeric",
        min=0,
        max=23,
        default=10,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="irrig_wall_start_hour",
        cfg_readback_object_id="cfg_irrig_wall_start_hour",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Wall-drip schedule — operator configures.",
    ),
    "irrig_wall_start_min": TunableDef(
        name="irrig_wall_start_min",
        kind="numeric",
        min=0,
        max=59,
        default=30,
        fw_clamp_lo=0,
        fw_clamp_hi=59,
        esp_object_id="irrig_wall_start_min",
        cfg_readback_object_id="cfg_irrig_wall_start_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_wall_duration_min": TunableDef(
        name="irrig_wall_duration_min",
        kind="numeric",
        min=1,
        max=120,
        default=10,
        fw_clamp_lo=1,
        fw_clamp_hi=120,
        esp_object_id="irrig_wall_duration__min_",
        cfg_readback_object_id="cfg_irrig_wall_duration_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_wall_fert_duration_min": TunableDef(
        name="irrig_wall_fert_duration_min",
        kind="numeric",
        min=0,
        max=60,
        default=6,
        fw_clamp_lo=0,
        fw_clamp_hi=60,
        esp_object_id="irrig_wall_fert_duration__min_",
        cfg_readback_object_id="cfg_irrig_wall_fert_duration_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_wall_fert_every_n": TunableDef(
        name="irrig_wall_fert_every_n",
        kind="numeric",
        min=0,
        max=30,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=30,
        esp_object_id="irrig_wall_fert_every_n_cycles",
        cfg_readback_object_id="cfg_irrig_wall_fert_every_n",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Naming quirk: canonical drops '_cycles' suffix that SETPOINT_MAP key carries.",
    ),
    "irrig_wall_days_mask": TunableDef(
        name="irrig_wall_days_mask",
        kind="numeric",
        min=0,
        max=127,
        default=127,
        fw_clamp_lo=0,
        fw_clamp_hi=127,
        esp_object_id="irrig_wall_days_mask",
        cfg_readback_object_id="cfg_irrig_wall_days_mask",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Explicit wall watering day mask, bit0=Sunday through bit6=Saturday.",
    ),
    "irrig_wall_fert_days_mask": TunableDef(
        name="irrig_wall_fert_days_mask",
        kind="numeric",
        min=0,
        max=127,
        default=127,
        fw_clamp_lo=0,
        fw_clamp_hi=127,
        esp_object_id="irrig_wall_fert_days_mask",
        cfg_readback_object_id="cfg_irrig_wall_fert_days_mask",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Explicit wall fertigation day mask. Nonzero supersedes irrig_wall_fert_every_n.",
    ),
    "irrig_wall_flush_min": TunableDef(
        name="irrig_wall_flush_min",
        kind="numeric",
        min=0,
        max=30,
        default=2,
        fw_clamp_lo=0,
        fw_clamp_hi=30,
        esp_object_id="irrig_wall_flush__min_",
        cfg_readback_object_id="cfg_irrig_wall_flush_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_wall_interval_days": TunableDef(
        name="irrig_wall_interval_days",
        kind="numeric",
        min=1,
        max=14,
        default=1,
        fw_clamp_lo=1,
        fw_clamp_hi=14,
        esp_object_id="irrig_wall_interval__days_",
        cfg_readback_object_id="cfg_irrig_wall_interval_days",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_center_start_hour": TunableDef(
        name="irrig_center_start_hour",
        kind="numeric",
        min=0,
        max=23,
        default=10,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="irrig_center_start_hour",
        cfg_readback_object_id="cfg_irrig_center_start_hour",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_center_start_min": TunableDef(
        name="irrig_center_start_min",
        kind="numeric",
        min=0,
        max=59,
        default=30,
        fw_clamp_lo=0,
        fw_clamp_hi=59,
        esp_object_id="irrig_center_start_min",
        cfg_readback_object_id="cfg_irrig_center_start_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_center_duration_min": TunableDef(
        name="irrig_center_duration_min",
        kind="numeric",
        min=1,
        max=120,
        default=10,
        fw_clamp_lo=1,
        fw_clamp_hi=120,
        esp_object_id="irrig_center_duration__min_",
        cfg_readback_object_id="cfg_irrig_center_duration_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_center_fert_duration_min": TunableDef(
        name="irrig_center_fert_duration_min",
        kind="numeric",
        min=0,
        max=60,
        default=6,
        fw_clamp_lo=0,
        fw_clamp_hi=60,
        esp_object_id="irrig_center_fert_duration__min_",
        cfg_readback_object_id="cfg_irrig_center_fert_duration_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_center_fert_every_n": TunableDef(
        name="irrig_center_fert_every_n",
        kind="numeric",
        min=0,
        max=30,
        default=0,
        fw_clamp_lo=0,
        fw_clamp_hi=30,
        esp_object_id="irrig_center_fert_every_n_cycles",
        cfg_readback_object_id="cfg_irrig_center_fert_every_n",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Naming quirk: canonical drops '_cycles' suffix.",
    ),
    "irrig_center_days_mask": TunableDef(
        name="irrig_center_days_mask",
        kind="numeric",
        min=0,
        max=127,
        default=127,
        fw_clamp_lo=0,
        fw_clamp_hi=127,
        esp_object_id="irrig_center_days_mask",
        cfg_readback_object_id="cfg_irrig_center_days_mask",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Explicit center watering day mask, bit0=Sunday through bit6=Saturday.",
    ),
    "irrig_center_fert_days_mask": TunableDef(
        name="irrig_center_fert_days_mask",
        kind="numeric",
        min=0,
        max=127,
        default=127,
        fw_clamp_lo=0,
        fw_clamp_hi=127,
        esp_object_id="irrig_center_fert_days_mask",
        cfg_readback_object_id="cfg_irrig_center_fert_days_mask",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Explicit center fertigation day mask. Nonzero supersedes irrig_center_fert_every_n.",
    ),
    "irrig_center_flush_min": TunableDef(
        name="irrig_center_flush_min",
        kind="numeric",
        min=0,
        max=30,
        default=2,
        fw_clamp_lo=0,
        fw_clamp_hi=30,
        esp_object_id="irrig_center_flush__min_",
        cfg_readback_object_id="cfg_irrig_center_flush_min",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_center_interval_days": TunableDef(
        name="irrig_center_interval_days",
        kind="numeric",
        min=1,
        max=14,
        default=1,
        fw_clamp_lo=1,
        fw_clamp_hi=14,
        esp_object_id="irrig_center_interval__days_",
        cfg_readback_object_id="cfg_irrig_center_interval_days",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "irrig_vpd_boost_pct": TunableDef(
        name="irrig_vpd_boost_pct",
        kind="numeric",
        min=0,
        max=200,
        default=25,
        fw_clamp_lo=0,
        fw_clamp_hi=200,
        esp_object_id="irrig_vpd_boost__",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Irrigation duration boost after sustained high-VPD stress.",
    ),
    "irrig_vpd_boost_threshold_hrs": TunableDef(
        name="irrig_vpd_boost_threshold_hrs",
        kind="numeric",
        min=0,
        max=24,
        default=3.0,
        fw_clamp_lo=0,
        fw_clamp_hi=24,
        esp_object_id="irrig_vpd_boost_threshold__hrs_",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Accumulated high-VPD hours before irrigation boost applies.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Grow lights.
    # ─────────────────────────────────────────────────────────────────────
    "gl_dli_target": TunableDef(
        name="gl_dli_target",
        kind="numeric",
        min=1,
        max=50,
        default=14.0,
        fw_clamp_lo=1,
        fw_clamp_hi=50,
        esp_object_id="gl_dli_target__mol_",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Daily light integral target for supplemental grow-light automation.",
    ),
    "gl_lux_threshold": TunableDef(
        name="gl_lux_threshold",
        kind="numeric",
        min=100,
        max=50000,
        default=3000.0,
        fw_clamp_lo=100,
        fw_clamp_hi=50000,
        esp_object_id="gl_lux_threshold",
        cfg_readback_object_id=None,
        push_owner="dispatcher_default",
        planner_pushable=False,
        tier=2,
        notes="Legacy shared grow-light threshold. Read-only fallback now that per-circuit gl_main_* and gl_grow_* thresholds are planner-writable.",
    ),
    "gl_lux_hysteresis": TunableDef(
        name="gl_lux_hysteresis",
        kind="numeric",
        min=0,
        max=10000,
        default=1500.0,
        fw_clamp_lo=0,
        fw_clamp_hi=10000,
        esp_object_id="gl_lux_hysteresis",
        cfg_readback_object_id="cfg___gl_lux_hysteresis",
        push_owner="dispatcher_default",
        planner_pushable=False,
        tier=2,
        notes="Legacy shared grow-light hysteresis. Read-only fallback now that per-circuit hysteresis tunables are planner-writable.",
    ),
    "gl_sunrise_hour": TunableDef(
        name="gl_sunrise_hour",
        kind="numeric",
        min=0,
        max=23,
        default=7,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="gl_start_hour",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Earliest local hour grow lights may turn on.",
    ),
    "gl_sunset_hour": TunableDef(
        name="gl_sunset_hour",
        kind="numeric",
        min=0,
        max=23,
        default=19,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="gl_cutoff_hour",
        cfg_readback_object_id=None,
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Latest local hour grow lights may remain on.",
    ),
    "gl_main_dli_target": TunableDef(
        name="gl_main_dli_target",
        kind="numeric",
        min=1,
        max=50,
        default=14.0,
        fw_clamp_lo=1,
        fw_clamp_hi=50,
        esp_object_id="gl_main_dli_target",
        cfg_readback_object_id="cfg_gl_main_dli_target",
        push_owner="dispatcher_default",
        planner_pushable=False,
        tier=2,
        notes="Legacy main/overhead lighting DLI telemetry target. Runtime control now uses gl_main_target_light_minutes.",
    ),
    "gl_main_target_light_minutes": TunableDef(
        name="gl_main_target_light_minutes",
        kind="numeric",
        min=0,
        max=1080,
        default=960.0,
        fw_clamp_lo=0,
        fw_clamp_hi=1080,
        esp_object_id="gl_main_target_light_minutes",
        cfg_readback_object_id="cfg_gl_main_target_light_minutes",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Main/overhead daily qualified-light-minutes target. A minute counts once when natural lux is above threshold or the actual switch is ON.",
    ),
    "gl_main_lux_threshold": TunableDef(
        name="gl_main_lux_threshold",
        kind="numeric",
        min=100,
        max=100000,
        default=40000.0,
        fw_clamp_lo=100,
        fw_clamp_hi=100000,
        esp_object_id="gl_main_lux_threshold",
        cfg_readback_object_id="cfg_gl_main_lux_threshold",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Main/overhead circuit ON threshold from Tempest outdoor lux.",
    ),
    "gl_main_lux_hysteresis": TunableDef(
        name="gl_main_lux_hysteresis",
        kind="numeric",
        min=0,
        max=25000,
        default=8000.0,
        fw_clamp_lo=0,
        fw_clamp_hi=25000,
        esp_object_id="gl_main_lux_hysteresis",
        cfg_readback_object_id="cfg_gl_main_lux_hysteresis",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Main/overhead circuit OFF threshold is ON threshold plus this hysteresis.",
    ),
    "gl_main_sunrise_hour": TunableDef(
        name="gl_main_sunrise_hour",
        kind="numeric",
        min=0,
        max=23,
        default=7,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="gl_main_sunrise_hour",
        cfg_readback_object_id="cfg_gl_main_sunrise_hour",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Earliest local hour the main/overhead circuit may turn on.",
    ),
    "gl_main_sunset_hour": TunableDef(
        name="gl_main_sunset_hour",
        kind="numeric",
        min=0,
        max=23,
        default=19,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="gl_main_sunset_hour",
        cfg_readback_object_id="cfg_gl_main_sunset_hour",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Latest local hour the main/overhead circuit may remain on.",
    ),
    "gl_main_min_on_s": TunableDef(
        name="gl_main_min_on_s",
        kind="numeric",
        min=0,
        max=3600,
        default=120,
        fw_clamp_lo=0,
        fw_clamp_hi=3600,
        esp_object_id="gl_main_min_on_s",
        cfg_readback_object_id="cfg_gl_main_min_on_s",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Minimum ON dwell for main/overhead lighting state machine.",
    ),
    "gl_main_min_off_s": TunableDef(
        name="gl_main_min_off_s",
        kind="numeric",
        min=0,
        max=3600,
        default=60,
        fw_clamp_lo=0,
        fw_clamp_hi=3600,
        esp_object_id="gl_main_min_off_s",
        cfg_readback_object_id="cfg_gl_main_min_off_s",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Minimum OFF dwell for main/overhead lighting state machine.",
    ),
    "gl_grow_dli_target": TunableDef(
        name="gl_grow_dli_target",
        kind="numeric",
        min=1,
        max=50,
        default=14.0,
        fw_clamp_lo=1,
        fw_clamp_hi=50,
        esp_object_id="gl_grow_dli_target",
        cfg_readback_object_id="cfg_gl_grow_dli_target",
        push_owner="dispatcher_default",
        planner_pushable=False,
        tier=2,
        notes="Legacy grow/secondary lighting DLI telemetry target. Runtime control now uses gl_grow_target_light_minutes.",
    ),
    "gl_grow_target_light_minutes": TunableDef(
        name="gl_grow_target_light_minutes",
        kind="numeric",
        min=0,
        max=1080,
        default=960.0,
        fw_clamp_lo=0,
        fw_clamp_hi=1080,
        esp_object_id="gl_grow_target_light_minutes",
        cfg_readback_object_id="cfg_gl_grow_target_light_minutes",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Grow/secondary daily qualified-light-minutes target. A minute counts once when natural lux is above threshold or the actual switch is ON.",
    ),
    "gl_grow_lux_threshold": TunableDef(
        name="gl_grow_lux_threshold",
        kind="numeric",
        min=100,
        max=100000,
        default=40000.0,
        fw_clamp_lo=100,
        fw_clamp_hi=100000,
        esp_object_id="gl_grow_lux_threshold",
        cfg_readback_object_id="cfg_gl_grow_lux_threshold",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Grow/secondary circuit ON threshold from Tempest outdoor lux.",
    ),
    "gl_grow_lux_hysteresis": TunableDef(
        name="gl_grow_lux_hysteresis",
        kind="numeric",
        min=0,
        max=25000,
        default=8000.0,
        fw_clamp_lo=0,
        fw_clamp_hi=25000,
        esp_object_id="gl_grow_lux_hysteresis",
        cfg_readback_object_id="cfg_gl_grow_lux_hysteresis",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Grow/secondary circuit OFF threshold is ON threshold plus this hysteresis.",
    ),
    "gl_grow_sunrise_hour": TunableDef(
        name="gl_grow_sunrise_hour",
        kind="numeric",
        min=0,
        max=23,
        default=7,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="gl_grow_sunrise_hour",
        cfg_readback_object_id="cfg_gl_grow_sunrise_hour",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Earliest local hour the grow/secondary circuit may turn on.",
    ),
    "gl_grow_sunset_hour": TunableDef(
        name="gl_grow_sunset_hour",
        kind="numeric",
        min=0,
        max=23,
        default=19,
        fw_clamp_lo=0,
        fw_clamp_hi=23,
        esp_object_id="gl_grow_sunset_hour",
        cfg_readback_object_id="cfg_gl_grow_sunset_hour",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Latest local hour the grow/secondary circuit may remain on.",
    ),
    "gl_grow_min_on_s": TunableDef(
        name="gl_grow_min_on_s",
        kind="numeric",
        min=0,
        max=3600,
        default=120,
        fw_clamp_lo=0,
        fw_clamp_hi=3600,
        esp_object_id="gl_grow_min_on_s",
        cfg_readback_object_id="cfg_gl_grow_min_on_s",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Minimum ON dwell for grow/secondary lighting state machine.",
    ),
    "gl_grow_min_off_s": TunableDef(
        name="gl_grow_min_off_s",
        kind="numeric",
        min=0,
        max=3600,
        default=60,
        fw_clamp_lo=0,
        fw_clamp_hi=3600,
        esp_object_id="gl_grow_min_off_s",
        cfg_readback_object_id="cfg_gl_grow_min_off_s",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Minimum OFF dwell for grow/secondary lighting state machine.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Economiser (enthalpy-based vent gating).
    # ─────────────────────────────────────────────────────────────────────
    "enthalpy_open": TunableDef(
        name="enthalpy_open",
        kind="numeric",
        min=-5.0,
        max=0.0,
        default=-2.0,
        fw_clamp_lo=-5.0,
        fw_clamp_hi=0.0,
        esp_object_id="enthalpy_open__kj_kg_",
        cfg_readback_object_id="cfg___enthalpy_open__kj_kg___",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Economiser opens when outdoor-indoor enthalpy ≤ this. In MCP TIER1.",
    ),
    "enthalpy_close": TunableDef(
        name="enthalpy_close",
        kind="numeric",
        min=-5.0,
        max=20.0,
        default=1.0,
        fw_clamp_lo=-5.0,
        fw_clamp_hi=20.0,
        esp_object_id="enthalpy_close__kj_kg_",
        cfg_readback_object_id="cfg___enthalpy_close__kj_kg___",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Economiser closes when enthalpy ≥ this. In MCP TIER1.",
    ),
    "econ_heat_margin_f": TunableDef(
        name="econ_heat_margin_f",
        kind="numeric",
        min=1.0,
        max=15.0,
        default=5.0,
        fw_clamp_lo=1.0,
        fw_clamp_hi=15.0,
        esp_object_id="econ_heat_margin__f",
        cfg_readback_object_id="cfg___econ_heat_margin___f_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Blocks economiser venting when heating demand is active and temp is far below high band.",
    ),
    "site_pressure_hpa": TunableDef(
        name="site_pressure_hpa",
        kind="numeric",
        min=700,
        max=1100,
        default=840.0,
        fw_clamp_lo=700,
        fw_clamp_hi=1100,
        esp_object_id="site_pressure__hpa_",
        cfg_readback_object_id="cfg___site_pressure__hpa_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Elevation-corrected barometric pressure for VPD calc. Site constant — not planner-pushable.",
    ),
    "sw_economiser_enabled": TunableDef(
        name="sw_economiser_enabled",
        kind="switch",
        default=1,
        esp_object_id="economiser_enabled",
        cfg_readback_object_id=None,
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Economiser master enable. Default ON.",
    ),
    "sw_gl_auto_mode": TunableDef(
        name="sw_gl_auto_mode",
        kind="switch",
        default=1,
        esp_object_id="gl_auto_mode",
        cfg_readback_object_id=None,
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Grow-light automation master enable.",
    ),
    "sw_gl_main_auto_mode": TunableDef(
        name="sw_gl_main_auto_mode",
        kind="switch",
        default=1,
        esp_object_id="gl_main_auto_mode",
        cfg_readback_object_id="cfg_gl_main_auto_mode",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Per-circuit automation enable for main/overhead lighting.",
    ),
    "sw_gl_grow_auto_mode": TunableDef(
        name="sw_gl_grow_auto_mode",
        kind="switch",
        default=1,
        esp_object_id="gl_grow_auto_mode",
        cfg_readback_object_id="cfg_gl_grow_auto_mode",
        push_owner="schedule",
        planner_pushable=False,
        tier=2,
        notes="Per-circuit automation enable for grow/secondary lighting.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Operator switches (push_owner="operator", tier=2). Planner reads these
    # as context; operator tooling owns changes.
    # ─────────────────────────────────────────────────────────────────────
    "sw_irrigation_enabled": TunableDef(
        name="sw_irrigation_enabled",
        kind="switch",
        default=1,
        esp_object_id="irrigation_enabled",
        cfg_readback_object_id=None,
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Master irrigation enable.",
    ),
    "sw_irrigation_wall_enabled": TunableDef(
        name="sw_irrigation_wall_enabled",
        kind="switch",
        default=1,
        esp_object_id="irrigation_wall_enabled",
        cfg_readback_object_id=None,
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "sw_irrigation_center_enabled": TunableDef(
        name="sw_irrigation_center_enabled",
        kind="switch",
        default=1,
        esp_object_id="irrigation_center_enabled",
        cfg_readback_object_id=None,
        push_owner="operator",
        planner_pushable=False,
        tier=2,
    ),
    "sw_irrigation_weather_skip": TunableDef(
        name="sw_irrigation_weather_skip",
        kind="switch",
        default=0,
        esp_object_id="irrigation_weather_skip",
        cfg_readback_object_id=None,
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Operator-only legacy outdoor weather skip; default off for greenhouse irrigation.",
    ),
    "sw_occupancy_inhibit": TunableDef(
        name="sw_occupancy_inhibit",
        kind="switch",
        default=1,
        esp_object_id="occupancy_mist_inhibit",
        cfg_readback_object_id=None,
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Block misting while greenhouse is occupied.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Sprint-15 summer-vent gate (the class of tunable that repeatedly hit
    # the TIER1 drift bug). Definitive source-of-truth for min/max + clamps.
    # ─────────────────────────────────────────────────────────────────────
    "sw_summer_vent_enabled": TunableDef(
        name="sw_summer_vent_enabled",
        kind="switch",
        default=1,
        esp_object_id="summer_vent_enabled",
        cfg_readback_object_id="cfg_summer_vent_enabled",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Master switch for the sprint-15 outdoor-cooler-and-drier gate. "
        "Default ON: firmware behavior without it is wrong in summer.",
    ),
    "vent_prefer_temp_delta_f": TunableDef(
        name="vent_prefer_temp_delta_f",
        kind="numeric",
        min=2.0,
        max=15.0,
        default=5.0,
        fw_clamp_lo=2.0,
        fw_clamp_hi=15.0,
        esp_object_id="vent_prefer_temp_delta__f_",
        cfg_readback_object_id="cfg___vent_prefer_temp_delta__f_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Outdoor must be ≥ this many °F cooler than indoor for gate to fire.",
    ),
    "vent_prefer_dp_delta_f": TunableDef(
        name="vent_prefer_dp_delta_f",
        kind="numeric",
        min=2.0,
        max=15.0,
        default=5.0,
        fw_clamp_lo=2.0,
        fw_clamp_hi=15.0,
        esp_object_id="vent_prefer_dp_delta__f_",
        cfg_readback_object_id="cfg___vent_prefer_dp_delta__f_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Outdoor dewpoint must be ≥ this many °F below indoor DP for gate.",
    ),
    "outdoor_staleness_max_s": TunableDef(
        name="outdoor_staleness_max_s",
        kind="numeric",
        min=120,
        max=1800,
        default=600,
        fw_clamp_lo=120,
        fw_clamp_hi=1800,
        esp_object_id="outdoor_staleness_max__s_",
        cfg_readback_object_id="cfg___outdoor_staleness_max__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Gate disables when outdoor data older than this. Sprint-15.1 "
        "raised default 300→600 and floor 60→120 so dispatcher cadence "
        "jitter doesn't intermittently disqualify the gate.",
    ),
    "summer_vent_min_runtime_s": TunableDef(
        name="summer_vent_min_runtime_s",
        kind="numeric",
        min=60,
        max=600,
        default=180,
        fw_clamp_lo=60,
        fw_clamp_hi=600,
        esp_object_id="summer_vent_min_runtime__s_",
        cfg_readback_object_id="cfg___summer_vent_min_runtime__s_",
        push_owner="planner",
        planner_pushable=False,
        tier=2,
        notes="Reserved/no-op in current firmware. Setpoints field is clamped but not consumed by the gate.",
    ),
    # ── Sprint-15.1 vent-close interlocks (fixes 4/5/7) ──────────────
    "sw_fog_closes_vent": TunableDef(
        name="sw_fog_closes_vent",
        kind="switch",
        default=1,
        esp_object_id="fog_closes_vent",
        cfg_readback_object_id="fog_closes_vent",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Fog is suppressed while the vent is physically open, except vent-mist assist. "
        "Default ON; disabling re-enables open-vent fog during VENTILATE (tradeoff).",
    ),
    "sw_mister_closes_vent": TunableDef(
        name="sw_mister_closes_vent",
        kind="switch",
        default=0,  # globals.yaml initial_value 'false'
        esp_object_id="mister_closes_vent",
        cfg_readback_object_id="mister_closes_vent",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Normal mister pulses are suppressed while the vent is physically open. "
        "The explicit VENTILATE vent-mist assist path bypasses this interlock.",
    ),
    "sw_direct_wet_gate_enabled": TunableDef(
        name="sw_direct_wet_gate_enabled",
        kind="switch",
        default=1,
        esp_object_id="direct_wet_gate_enabled",
        cfg_readback_object_id="direct_wet_gate_enabled",
        push_owner="planner",
        planner_pushable=True,
        tier=2,
        notes="Enables global biological activity and per-zone direct-wet gates for clean/fert misters and irrigation paths.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Legacy compatibility/readback knobs.
    # ─────────────────────────────────────────────────────────────────────
    "bias_heat": TunableDef(
        name="bias_heat",
        kind="numeric",
        min=-10.0,
        max=10.0,
        default=0.0,
        fw_clamp_lo=-10.0,
        fw_clamp_hi=10.0,  # controls.yaml clamp
        esp_object_id="bias_heat__f",  # entity_map.SETPOINT_MAP
        cfg_readback_object_id="cfg___bias_heat___f_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Retired from live band-first control. Heating uses the crop-band lower quartile target; "
        "this value is retained for legacy fallback/readback visibility.",
    ),
    "bias_cool": TunableDef(
        name="bias_cool",
        kind="numeric",
        min=-10.0,
        max=10.0,
        default=0.0,
        fw_clamp_lo=-10.0,
        fw_clamp_hi=10.0,
        esp_object_id="bias_cool__f",
        cfg_readback_object_id="cfg___bias_cool___f_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Retired from live band-first control. Cooling enters at the crop-band high edge; "
        "this value is retained for legacy fallback/readback visibility.",
    ),
    "min_heat_off_s": TunableDef(
        name="min_heat_off_s",
        kind="numeric",
        min=60,
        max=600,
        default=180,
        fw_clamp_lo=60,
        fw_clamp_hi=600,
        esp_object_id="min_heat_off__s_",
        cfg_readback_object_id="cfg___min_heat_off__s_",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Heater relay protection dwell. Default 180s from Sprint-15.1; operator/firmware policy.",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Phase-2 dwell gate.
    # ─────────────────────────────────────────────────────────────────────
    "sw_dwell_gate_enabled": TunableDef(
        name="sw_dwell_gate_enabled",
        kind="switch",
        default=0,
        esp_object_id="dwell_gate_enabled",
        cfg_readback_object_id="cfg_dwell_gate_enabled",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Master switch for Phase-2 mode-dwell gate. ClimateIntent may "
        "enable this on the single live controller path when relay-churn "
        "preference warrants it; offline replay remains diagnostic only.",
    ),
    "dwell_gate_ms": TunableDef(
        name="dwell_gate_ms",
        kind="numeric",
        min=60000,
        max=1800000,
        default=300000,
        fw_clamp_lo=60000,
        fw_clamp_hi=1800000,
        esp_object_id="dwell_gate__ms_",
        cfg_readback_object_id="cfg_dwell_gate_ms",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Dwell hold duration. Default 5 min. Safety rails + R2-3 "
        "dry override + vpd_min_safe rescue preempt unconditionally.",
    ),
    "sw_fsm_controller_enabled": TunableDef(
        name="sw_fsm_controller_enabled",
        kind="switch",
        default=1,
        esp_object_id="fsm_controller_enabled",
        cfg_readback_object_id="cfg_fsm_controller_enabled",
        push_owner="operator",
        planner_pushable=False,
        tier=2,
        notes="Unified band-first controller is the live controller path. "
        "ESPHome control loop, dispatcher, MCP, and outbound-listener guardrails force this ON; "
        "rollback requires an explicit firmware/config rollback outside the planner surface.",
    ),
    "mist_backoff_s": TunableDef(
        name="mist_backoff_s",
        kind="numeric",
        min=60,
        max=3600,
        default=600,
        fw_clamp_lo=60,
        fw_clamp_hi=3600,
        esp_object_id="mist_backoff__s_",
        cfg_readback_object_id="cfg___mist_backoff__s_",
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes="Controller lockout after sealed humidification times out. "
        "Suppresses another SEALED_MIST attempt without forcing venting.",
    ),
    "fog_escalation_kpa": TunableDef(
        name="fog_escalation_kpa",
        kind="numeric",
        min=0.1,
        max=0.5,
        default=0.4,
        fw_clamp_lo=0.1,
        fw_clamp_hi=0.5,
        esp_object_id="fog_escalation__kpa_",
        cfg_readback_object_id="cfg___fog_escalation__kpa_",  # Phase 1c added readback
        push_owner="planner",
        planner_pushable=True,
        tier=1,
        notes=(
            "VPD delta above active vpd_high that escalates from mist to fog. "
            "Lower values mean earlier fog inside VENTILATE mist assist; dispatcher "
            "caps overly conservative deltas during live VPD-high or near-edge VENTILATE stress when dew "
            "margin is healthy. Tightened to 0.5 kPa max so plans stay band-relative."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────
    # Readback-only firmware inputs. These are in ALL_TUNABLES because
    # setpoint_snapshot rows validate against that schema, but there is no
    # dispatcher route and MCP must never expose them for planner writes.
    # ─────────────────────────────────────────────────────────────────────
    "fallback_window_s": TunableDef(
        name="fallback_window_s",
        kind="numeric",
        min=60.0,
        max=3600.0,
        default=900.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg___fallback_window__s_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes=(
            "Readback-only external-probe watchdog window. Firmware reboots if all external probes "
            "are silent longer than this; not a planner control knob."
        ),
    ),
    "outdoor_temp_f": TunableDef(
        name="outdoor_temp_f",
        kind="numeric",
        min=None,
        max=None,
        default=math.nan,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg___outdoor_temp___f_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes=("Readback-only Tempest outdoor temperature input consumed by the summer-vent cooler-and-drier gate."),
    ),
    "outdoor_dewpoint_f": TunableDef(
        name="outdoor_dewpoint_f",
        kind="numeric",
        min=None,
        max=None,
        default=math.nan,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg___outdoor_dewpoint___f_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes=(
            "Readback-only outdoor dew point derived from Tempest temperature/RH; consumed by "
            "the summer-vent drier-air comparator."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────
    # FRT-8 / F1 / F3 — AM-only Vanda feed window (firmware backstop globals).
    # No dispatcher number/switch entity: these are firmware-internal globals
    # with cfg_* readbacks only (sensors.yaml). The dispatcher owns actual feed
    # timing via irrig_center_start_hour/min; these gate fert jobs to a safe AM
    # window regardless. Routed here so the cfg readbacks are not fire-and-forget.
    # ─────────────────────────────────────────────────────────────────────
    "feed_start_hour": TunableDef(
        name="feed_start_hour",
        kind="numeric",
        min=0.0,
        max=23.0,
        default=6.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_feed_start_hour",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="FRT-8 feed-window backstop: earliest local hour fert jobs may run. cfg readback only; no dispatcher number entity.",
    ),
    "feed_end_hour": TunableDef(
        name="feed_end_hour",
        kind="numeric",
        min=0.0,
        max=23.0,
        default=9.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_feed_end_hour",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="FRT-8 feed-window backstop: latest local hour fert jobs may run. feed_start_hour==feed_end_hour fails SAFE (no feed).",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # IRR-3 (dawn rehydrate) / IRR-4 (midday drench) — CENTER-zone denser mist
    # cadence overrides. Firmware-internal globals; ESPHome num_*/sw_* entities
    # exist in tunables.yaml but are not on the dispatcher SETPOINT_MAP route,
    # so they stay readback-only context here (esp_object_id=None). cfg_*
    # readbacks (sensors.yaml) routed so the IRR-3/IRR-4 knobs are not
    # fire-and-forget (CLAUDE.md rule 6).
    # ─────────────────────────────────────────────────────────────────────
    "sw_dawn_rehydrate_enabled": TunableDef(
        name="sw_dawn_rehydrate_enabled",
        kind="switch",
        default=1.0,
        esp_object_id=None,
        cfg_readback_object_id="cfg_dawn_rehydrate_enabled",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-3 dawn-rehydrate burst master enable (ships ON).",
    ),
    "dawn_rehydrate_start_minute": TunableDef(
        name="dawn_rehydrate_start_minute",
        kind="numeric",
        min=0.0,
        max=59.0,
        default=0.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_dawn_rehydrate_start_minute",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-3 dawn window minute-of-hour (anchor hour = gl_sunrise_hour).",
    ),
    "dawn_rehydrate_window_min": TunableDef(
        name="dawn_rehydrate_window_min",
        kind="numeric",
        min=0.0,
        max=120.0,
        default=12.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_dawn_rehydrate_window__min_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-3 dawn rehydrate window length (minutes).",
    ),
    "dawn_rehydrate_on_s": TunableDef(
        name="dawn_rehydrate_on_s",
        kind="numeric",
        min=1.0,
        max=600.0,
        default=90.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_dawn_rehydrate_pulse_on__s_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-3 center pulse ON during dawn window (s).",
    ),
    "dawn_rehydrate_gap_s": TunableDef(
        name="dawn_rehydrate_gap_s",
        kind="numeric",
        min=5.0,
        max=600.0,
        default=20.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_dawn_rehydrate_pulse_gap__s_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-3 center pulse GAP during dawn window (s).",
    ),
    "sw_midday_drench_enabled": TunableDef(
        name="sw_midday_drench_enabled",
        kind="switch",
        default=1.0,
        esp_object_id=None,
        cfg_readback_object_id="cfg_midday_drench_enabled",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-4 midday-drench burst master enable (ships ON).",
    ),
    "midday_drench_hour": TunableDef(
        name="midday_drench_hour",
        kind="numeric",
        min=0.0,
        max=23.0,
        default=14.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_midday_drench_hour",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-4 local hour the midday window opens (~solar peak).",
    ),
    "midday_drench_start_minute": TunableDef(
        name="midday_drench_start_minute",
        kind="numeric",
        min=0.0,
        max=59.0,
        default=0.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_midday_drench_start_minute",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-4 midday window minute-of-hour.",
    ),
    "midday_drench_window_min": TunableDef(
        name="midday_drench_window_min",
        kind="numeric",
        min=0.0,
        max=120.0,
        default=11.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_midday_drench_window__min_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-4 midday drench window length (minutes).",
    ),
    "midday_drench_on_s": TunableDef(
        name="midday_drench_on_s",
        kind="numeric",
        min=1.0,
        max=600.0,
        default=120.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_midday_drench_pulse_on__s_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-4 center pulse ON during midday window (s).",
    ),
    "midday_drench_gap_s": TunableDef(
        name="midday_drench_gap_s",
        kind="numeric",
        min=5.0,
        max=600.0,
        default=25.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_midday_drench_pulse_gap__s_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="IRR-4 center pulse GAP during midday window (s).",
    ),
    # ─────────────────────────────────────────────────────────────────────
    # CYC-4 (NB7) — overnight ≤5s fog micro-pulse. Firmware-internal globals
    # with cfg_* readbacks only; the ≤5s pulse is a hard safety constraint
    # (validate_setpoints clamps micropulse_max_on_s ≤ 10). Routed here so the
    # micro-pulse knobs are not fire-and-forget.
    # ─────────────────────────────────────────────────────────────────────
    "sw_overnight_micropulse_enabled": TunableDef(
        name="sw_overnight_micropulse_enabled",
        kind="switch",
        default=1.0,
        esp_object_id=None,
        cfg_readback_object_id="cfg_overnight_micropulse_enabled",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="CYC-4 overnight micro-pulse master enable (ships ON; gate is dark+dew+RH+temp gated).",
    ),
    "sw_night_humidity_source_present": TunableDef(
        name="sw_night_humidity_source_present",
        kind="switch",
        default=0.0,
        esp_object_id=None,
        cfg_readback_object_id="cfg_night_humidity_source_present",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="CYC-4 auto-disable flag: flip true once NB5/OPN-2 night humidity HW lands → CYC-4 micro-pulse self-disables.",
    ),
    "micropulse_vpd_ceiling": TunableDef(
        name="micropulse_vpd_ceiling",
        kind="numeric",
        min=0.8,
        max=3.0,
        default=1.25,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_micropulse_vpd_ceiling",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="CYC-4 overnight VPD (kPa) above which a micro-pulse may fire.",
    ),
    "micropulse_max_on_s": TunableDef(
        name="micropulse_max_on_s",
        kind="numeric",
        min=1.0,
        max=10.0,
        default=5.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_micropulse_max_on__s_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="CYC-4 HARD cap on a single overnight pulse (s); validate_setpoints clamps ≤10.",
    ),
    "micropulse_min_gap_s": TunableDef(
        name="micropulse_min_gap_s",
        kind="numeric",
        min=60.0,
        max=3600.0,
        default=600.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_micropulse_min_gap__s_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="CYC-4 lockout between micro-pulses (s) — pulses can never chain into a run.",
    ),
    "micropulse_min_dew_margin_f": TunableDef(
        name="micropulse_min_dew_margin_f",
        kind="numeric",
        min=3.0,
        max=15.0,
        default=6.0,
        fw_clamp_lo=None,
        fw_clamp_hi=None,
        esp_object_id=None,
        cfg_readback_object_id="cfg_micropulse_min_dew_margin__f_",
        push_owner="firmware_internal",
        planner_pushable=False,
        tier=2,
        notes="CYC-4 min temp-dewpoint spread (°F) to micro-pulse — no crown condensation.",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Derived views — ONE source of truth above, many consumers below.
# ─────────────────────────────────────────────────────────────────────────────

RETIRED_TUNABLES_REG: frozenset[str] = frozenset(
    {
        "bias_cool",
        "bias_heat",
        "d_cool_stage_2",
        "d_heat_stage_2",
        "fan_burst_min",
        "fog_burst_min",
        "mist_vent_close_lead_s",
        "mist_vent_reopen_delay_s",
        "mister_all_off_s",
        "mister_all_on_s",
        "mister_max_runtime_min",
        "mister_off_s",
        "mister_on_s",
        "sw_fsm_controller_enabled",
        "summer_vent_min_runtime_s",
        "vent_bypass_min",
    }
)


def tunable_control_class(
    name: str, spec: TunableDef
) -> Literal["planner_policy", "crop_band", "scheduled_policy", "controller_safety", "readback_context", "retired"]:
    """Classify a tunable for the cleanup contract.

    This is the single policy split used by MCP, planner context, and the
    public AI Tunables page. It deliberately separates visibility from
    writeability: non-policy rows stay documented and searchable, but planner
    may only write `planner_policy` rows.

    `scheduled_policy` is the lighting/irrigation *schedule* layer
    (`push_owner="schedule"`): per-circuit `gl_main_*`/`gl_grow_*` lighting
    state-machine knobs and the `irrig_*`/`direct_wet_*` watering-schedule
    offsets/masks. These are owned by the band/schedule layer that publishes
    them every cycle — the planner reads them as context but does not drive
    them (verified live: 0 planner-sourced `setpoint_changes` over 30d versus
    the dispatcher `band` source that owns the cadence). They were previously
    mislabeled `planner_policy`, inflating the writable surface with knobs Iris
    never touched. They stay fully visible/readback-routed for traceability but
    are no longer planner write targets. The genuinely tactical
    `direct_wet_stress_*` / `sw_direct_wet_stress_override_enabled` override
    knobs stay `planner_policy` (the planner actively pushes them during
    VPD-high recovery).

    Kept deliberately distinct from `crop_band` so the schedule layer does not
    leak into `CROP_BAND_REG`/`BAND_OWNED_REG` (which the dispatcher and the
    HTTP/setpoint-server fallbacks treat as the crop temp/VPD band surface).
    """
    if name in RETIRED_TUNABLES_REG:
        return "retired"
    if spec.esp_object_id is None or spec.push_owner == "firmware_internal":
        return "readback_context"
    if spec.planner_pushable:
        return "planner_policy"
    if spec.push_owner == "schedule":
        return "scheduled_policy"
    if spec.push_owner == "band":
        return "crop_band"
    return "controller_safety"


def _contract_classes() -> dict[str, str]:
    return {n: d.control_class for n, d in REGISTRY.items()}


def _all_tunables() -> frozenset[str]:
    return frozenset(REGISTRY)


def _active_tunables() -> frozenset[str]:
    """Live tunable surface — every registry row EXCEPT retired aliases.

    Retired rows (`RETIRED_TUNABLES_REG`) stay in `REGISTRY` so their cfg_*
    readbacks and legacy `setpoint_changes` history remain documented and
    searchable (`control_class == "retired"`). They must NOT appear on the
    live MCP/planner surface: they are not planner-pushable, MCP `set_tunable`
    rejects them, and `get_setpoints` / the `ALL_TUNABLES` schema enum should
    enumerate `ACTIVE_TUNABLES_REG` rather than `ALL_TUNABLES_REG` so a stale
    alias the firmware no longer consumes (verified live 2026-05-29:
    `fog_burst_min`, `mister_on_s`, `bias_heat`, etc. still receive dispatcher
    writes) cannot masquerade as a live knob.
    """
    return frozenset(REGISTRY) - RETIRED_TUNABLES_REG


def _tier1() -> frozenset[str]:
    """Canonical planner-policy surface. Replaces mcp/server.py TIER1."""
    return frozenset(n for n, d in REGISTRY.items() if d.planner_pushable and d.tier == 1)


def _planner_pushable() -> frozenset[str]:
    """Everything the planner may write via MCP.

    Tier 1 planner-policy rows are mandatory full-plan waypoints. Tier 2
    planner-policy rows are optional tactical knobs such as per-circuit lighting
    thresholds. MCP accepts both, while operator, crop-band, safety,
    readback-only, and retired rows remain read-only to the planner.
    """
    return frozenset(n for n, d in REGISTRY.items() if d.control_class == "planner_policy")


_LIGHTING_CIRCUIT_PREFIXES = ("gl_main_", "gl_grow_", "sw_gl_main_", "sw_gl_grow_")


def _crop_band() -> frozenset[str]:
    """Tunables owned by crop/zone band policy rather than routine plans."""
    return frozenset(n for n, d in REGISTRY.items() if d.control_class == "crop_band")


def _scheduled_policy() -> frozenset[str]:
    """Lighting/irrigation schedule-layer tunables (push_owner="schedule").

    Per-circuit `gl_main_*`/`gl_grow_*` lighting state-machine knobs and the
    `irrig_*`/`direct_wet_*` watering-schedule offsets/masks. The band/schedule
    layer publishes these every cycle; the planner sees them as read-only
    context and MCP rejects planner writes. Deliberately disjoint from
    `CROP_BAND_REG` so the schedule layer does not leak into the crop temp/VPD
    band surface the dispatcher and HTTP fallbacks resolve.
    """
    return frozenset(n for n, d in REGISTRY.items() if d.control_class == "scheduled_policy")


def _legacy_shared_lighting() -> frozenset[str]:
    """Legacy shared lighting values computed by policy, not planner waypoints.

    Per-circuit `gl_main_*` and `gl_grow_*` tunables are planner-policy rows.
    The remaining shared `gl_*` values and the shared auto-mode switch stay
    dispatcher-owned compatibility values while older firmware/routes exist.
    """
    return frozenset(
        n
        for n, d in REGISTRY.items()
        if d.control_class == "controller_safety"
        and not d.planner_pushable
        and (n == "sw_gl_auto_mode" or (n.startswith("gl_") and not n.startswith(("gl_main_", "gl_grow_"))))
    )


def _lighting_circuit_defaults() -> frozenset[str]:
    """Per-circuit lighting state-machine tunables keyed by main/grow circuit."""
    return frozenset(n for n in REGISTRY if n.startswith(_LIGHTING_CIRCUIT_PREFIXES))


def _setpoint_map() -> dict[str, str]:
    """ESPHome object_id → canonical name. Replaces ingestor.entity_map.SETPOINT_MAP."""
    return {d.esp_object_id: n for n, d in REGISTRY.items() if d.esp_object_id}


def _cfg_readback_map() -> dict[str, str]:
    """cfg_* slug → canonical name. Replaces ingestor.entity_map.CFG_READBACK_MAP."""
    return {d.cfg_readback_object_id: n for n, d in REGISTRY.items() if d.cfg_readback_object_id}


def _cfg_readback_aliases() -> dict[str, str]:
    """Legacy/alternate firmware readback slugs accepted by the ingestor.

    These aliases are intentionally centralized here rather than in
    `ingestor/entity_map.py` so old entity names remain visible in the same
    contract as the canonical `cfg_readback_object_id` route.
    """
    return {
        "cfg_____cool_s2___f_": "d_cool_stage_2",
        "cfg_____heat_s2___f_": "d_heat_stage_2",
        "cfg_d_cool_stage_2_f": "d_cool_stage_2",
        "cfg_d_heat_stage_2_f": "d_heat_stage_2",
        "cfg_fog_min_temp_f": "fog_min_temp_f",
        "cfg_fog_rh_ceiling_pct": "fog_rh_ceiling_pct",
        "mister_center_penalty": "mister_center_penalty",
        "sw_mister_closes_vent": "sw_mister_closes_vent",
        "vpd_target_center__kpa_": "vpd_target_center",
        "vpd_target_east__kpa_": "vpd_target_east",
        "vpd_target_south__kpa_": "vpd_target_south",
        "vpd_target_west__kpa_": "vpd_target_west",
    }


# Phase 1b.2: expose as module attributes for consumers that already import
# computed-view style. `ALL_TUNABLES_REG` etc. coexist with the legacy
# `ALL_TUNABLES` in `tunables.py` during migration; Phase 1d flips the legacy
# module to import from here.
ALL_TUNABLES_REG: frozenset[str] = _all_tunables()
ACTIVE_TUNABLES_REG: frozenset[str] = _active_tunables()
TIER1_REG: frozenset[str] = _tier1()
PLANNER_PUSHABLE_REG: frozenset[str] = _planner_pushable()
TUNABLE_CONTRACT_CLASSES_REG: dict[str, str] = _contract_classes()
CROP_BAND_REG: frozenset[str] = _crop_band()
SCHEDULED_POLICY_REG: frozenset[str] = _scheduled_policy()
LEGACY_SHARED_LIGHTING_REG: frozenset[str] = _legacy_shared_lighting()
BAND_OWNED_REG: frozenset[str] = CROP_BAND_REG | LEGACY_SHARED_LIGHTING_REG
LIGHTING_CIRCUIT_DEFAULT_REG: frozenset[str] = _lighting_circuit_defaults()
SETPOINT_MAP_REG: dict[str, str] = _setpoint_map()
CFG_READBACK_MAP_REG: dict[str, str] = _cfg_readback_map()
CFG_READBACK_ALIASES_REG: dict[str, str] = _cfg_readback_aliases()


def get(name: str) -> TunableDef | None:
    """Lookup a tunable by canonical name. None if not registered."""
    return REGISTRY.get(name)


def registry_value_error(name: str, value: float) -> str | None:
    """Return a human-readable registry violation, or None when valid.

    This is the shared planner/dispatcher boundary check. Firmware still clamps
    defensively, but planner writes should fail before invalid waypoints become
    active in `setpoint_plan`.
    """
    tunable = get(name)
    if tunable is None:
        return f"{name!r} is not registered in tunable_registry"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return f"{name}={value!r} is not numeric"

    if not math.isfinite(numeric):
        return f"{name}={value!r} is not finite"

    if tunable.kind == "switch" and numeric not in (0.0, 1.0):
        return f"{name}={numeric:g} outside registry switch values [0, 1]"

    if tunable.kind == "enum":
        allowed = sorted((tunable.enum_values or {}).values())
        if numeric % 1 != 0 or int(numeric) not in allowed:
            return f"{name}={numeric:g} outside registry enum values {allowed}"

    lo = "-inf" if tunable.min is None else f"{tunable.min:g}"
    hi = "inf" if tunable.max is None else f"{tunable.max:g}"

    if tunable.min is not None and numeric < tunable.min:
        return f"{name}={numeric:g} outside registry bounds [{lo}, {hi}] nearest_safe={tunable.min:g}"
    if tunable.max is not None and numeric > tunable.max:
        return f"{name}={numeric:g} outside registry bounds [{lo}, {hi}] nearest_safe={tunable.max:g}"

    return None
