#!/usr/bin/env python3
"""Live firmware digital-twin follower (audit §8.9 live-shadow adapter, #587).

Sits alongside ``offline_driver.py`` in the same pre-baked twin image
(twin/Dockerfile) and turns the corpus shadow into the §8.9 LIVE follower:

  1. Polls ``v_policy_twin_asof_input`` (migration 211) incrementally — each
     row pairs one settled telemetry tick with the latest DEVICE-CONFIRMED
     policy identity (policy_device_snapshots joined to the admitted
     effective_policy_vectors row) at or before that tick.
  2. Decodes the paired vector's canonical bytes with the SAME 48-field wire
     codec the planner and firmware share (``verdify_schemas.policy_vector``)
     and maps every wire field the replay harness consumes onto its existing
     ``sp_*`` input column (``POLICY_TO_SP`` below). No replay_emit.cpp change
     is needed: the stream harness already binds the whole posture surface by
     header name, so the live adapter is a pure input-mapping concern.
  3. Feeds one TSV line per tick to the resident ``replay_emit_follow``
     binary (the exact -DREPLAY_EMIT_STREAM compile the rule-8 gate trusts)
     and reads back one decision row.
  4. Classifies each tick per §8.9 — agreement / divergence / warm_up /
     unmatched_state / gap (gaps NEVER count as agreement; agreement requires
     BOTH byte-identical policy identity and relay-action equality) — and
     INSERTs one append-only ``twin_live_results`` row.
  5. Resets twin state on firmware boot events (follower restart + warm-up
     window), mirroring the firmware's own deterministic post-boot
     initialization (§8.9 "reset twin state on the same boot event").

Read-only by construction, identically to the offline driver (§5.3 L2): the
role can only SELECT the as-of view and INSERT twin result rows; the startup
probe asserts the control-plane write is impossible, and the pod's
NetworkPolicy allows DB egress only. No actuation path exists here.

Env:
  TWIN_DSN                 REQUIRED: DSN for the twin login user
  TWIN_ENV                 dev|stage|prod            (default "prod")
  TWIN_REF                 git sha / fw_version pin   (default "last-good")
  TWIN_GREENHOUSE          greenhouse id              (default "vallery")
  TWIN_BINARY              follower path              (default replay_emit_follow)
  TWIN_POLL_INTERVAL_S     poll sleep                 (default 60)
  TWIN_SETTLE_S            ingest settling lag        (default 120)
  TWIN_BATCH_LIMIT         max ticks per poll         (default 720)
  TWIN_WARMUP_TICKS        ticks after (re)start classified warm_up (default 30)
  TWIN_SNAPSHOT_MAX_AGE_S  device-echo staleness gap threshold (default 21600)
  TWIN_RELAY_MAX_AGE_S     live relay readback age gap threshold (default 0 =
                           disabled: equipment_state is event-sourced, so an
                           old last transition is a steady relay, not a stale
                           readback)
  TWIN_RESET_GAP_S         feed gap that resets harness state (default 600 —
                           MUST match replay_emit.cpp's 600 s reset)
  TWIN_ONCE                "1": one poll pass then exit (smoke/CI)
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

# ── §8.9 classification vocabulary ───────────────────────────────────────────
CLASS_AGREEMENT = "agreement"
CLASS_DIVERGENCE = "divergence"
CLASS_WARM_UP = "warm_up"
CLASS_UNMATCHED = "unmatched_state"
CLASS_GAP = "gap"
CLASSIFICATIONS = (CLASS_AGREEMENT, CLASS_DIVERGENCE, CLASS_WARM_UP, CLASS_UNMATCHED, CLASS_GAP)

RELAYS = ("fog", "vent", "fan1", "fan2", "heat1", "heat2")

# ── Wire-field → sp_* input-column mapping ───────────────────────────────────
# Every 48-field wire vector value that the replay harness CONSUMES has an
# existing sp_* column (replay_emit.cpp EXPECTED_SP_COLUMNS; the source
# contract test parses that array so this table cannot drift silently).
# Units are already aligned: the *_s wire fields map onto *_s columns the
# harness multiplies to ms itself (mirroring controls.yaml).
POLICY_TO_SP: dict[str, str] = {
    "cold_vent_guard_delta_f": "sp_cold_vent_guard_delta_f",
    "cool_exit_hysteresis_f": "sp_cool_exit_hysteresis_f",
    "cool_stage2_over_high_f": "sp_cool_stage2_over_high_f",
    "direct_wet_stress_min_dew_margin_f": "sp_direct_wet_stress_min_dew_margin_f",
    "direct_wet_stress_vpd_margin_kpa": "sp_direct_wet_stress_vpd_margin_kpa",
    "dwell_gate_ms": "sp_dwell_gate_ms",
    "fog_escalation_kpa": "sp_fog_escalation_kpa",
    "heat_hysteresis": "sp_heat_hysteresis",
    "mist_backoff_s": "sp_mist_backoff_s",
    "mist_max_closed_vent_s": "sp_sealed_max_s",
    "mist_thermal_relief_s": "sp_relief_duration_s",
    "mister_all_delay_s": "sp_mist_s2_delay_s",
    "outdoor_staleness_max_s": "sp_outdoor_staleness_max_s",
    "sw_cool_all_fans_at_high_enabled": "sp_cool_all_fans_at_high_enabled",
    "sw_direct_wet_stress_override_enabled": "sp_direct_wet_stress_override_enabled",
    "sw_dwell_gate_enabled": "sp_sw_dwell_gate_enabled",
    "sw_summer_vent_enabled": "sp_sw_summer_vent_enabled",
    "temp_hysteresis": "sp_temp_hysteresis",
    "vent_prefer_dp_delta_f": "sp_vent_prefer_dp_delta_f",
    "vent_prefer_temp_delta_f": "sp_vent_prefer_temp_delta_f",
    "vpd_hysteresis": "sp_vpd_hysteresis",
    "vpd_watch_dwell_s": "sp_watch_dwell_s",
}

# Wire fields with NO input column on the replay harness. Their CONSUMERS live
# outside the greenhouse_logic.h surface replay_emit compiles (zone/mister
# engagement lambdas, per-relay min-on/off dwell fairness, the enthalpy
# economizer, night VPD bias, and the band pinch that process_row pins to the
# ADR-0004 default). They still participate fully in the §8.9 POLICY identity
# check (hash equality is over the complete canonical bytes); only the ACTION
# agreement surface is scoped to the harness FSM (mode/relays/mist_stage) —
# the audit's shared-oracle extraction (issue #587, Lane E coordination)
# closes that residual surface. Enumerated explicitly so drift fails loudly:
# ``assert_wire_mapping_partition`` refuses to run if the registry gains a
# field this file has not classified.
UNMAPPED_WIRE_FIELDS: frozenset[str] = frozenset(
    {
        "band_track_fraction",
        "cool_stage2_exit_hysteresis_f",
        "enthalpy_close",
        "enthalpy_open",
        "min_fan_off_s",
        "min_fan_on_s",
        "min_fog_off_s",
        "min_fog_on_s",
        "min_heat_off_s",
        "min_heat_on_s",
        "min_vent_off_s",
        "min_vent_on_s",
        "mister_all_kpa",
        "mister_center_penalty",
        "mister_engage_delay_s",
        "mister_engage_kpa",
        "mister_min_off_s",
        "mister_pulse_gap_s",
        "mister_pulse_on_s",
        "mister_vpd_weight",
        "mister_water_budget_gal",
        "night_vpd_bias_kpa",
        "sw_direct_wet_gate_enabled",
        "sw_fog_closes_vent",
        "sw_mister_closes_vent",
        "vent_exchange_fraction",
    }
)

# ── Setpoint-batch (non-wire posture) → sp_* mapping ─────────────────────────
# The band/safety/window posture is NOT part of the 48-field wire vector; it
# arrives from the as-of setpoint_snapshot batch the view exposes as
# ``sp_payload`` (same parameter names scripts/export-replay-overrides.sh
# maps). Wire-covered parameters are deliberately absent here: the decoded
# device-confirmed vector overrides them, never the dispatcher projection.
SP_PARAM_TO_COLUMN: dict[str, str] = {
    "temp_high": "sp_temp_high",
    "temp_low": "sp_temp_low",
    "vpd_high": "sp_vpd_high",
    "vpd_low": "sp_vpd_low",
    "bias_cool": "sp_bias_cool",
    "bias_heat": "sp_bias_heat",
    "safety_max": "sp_safety_max",
    "safety_min": "sp_safety_min",
    "safety_vpd_max": "sp_vpd_max_safe",
    "safety_vpd_min": "sp_vpd_min_safe",
    "sw_fsm_controller_enabled": "sp_sw_fsm_controller_enabled",
    "max_relief_cycles": "sp_max_relief_cycles",
    "fog_rh_ceiling_pct": "sp_fog_rh_ceiling",
    "fog_min_temp_f": "sp_fog_min_temp",
    "dehum_aggressive_kpa": "sp_dehum_aggressive_kpa",
    "sw_occupancy_inhibit": "sp_occupancy_inhibit",
    "vent_latch_timeout_ms": "sp_vent_latch_timeout_ms",
    "safety_max_seal_margin_f": "sp_safety_max_seal_margin_f",
    "econ_heat_margin_f": "sp_econ_heat_margin_f",
    "summer_vent_min_runtime_s": "sp_summer_vent_min_runtime_s",
    "sw_wet_taper_enabled": "sp_sw_wet_taper_enabled",
    "wet_taper_before_sunset_min": "sp_wet_taper_before_sunset_min",
    "sw_night_stress_wet_enabled": "sp_sw_night_stress_wet_enabled",
    "night_stress_min_dew_margin_f": "sp_night_stress_min_dew_margin_f",
    "sw_dawn_rehydrate_enabled": "sp_sw_dawn_rehydrate_enabled",
    "dawn_boost_offset_min": "sp_dawn_boost_offset_min",
    "dawn_rehydrate_window_min": "sp_dawn_rehydrate_window_min",
    "dawn_rehydrate_on_s": "sp_dawn_rehydrate_on_s",
    "dawn_rehydrate_gap_s": "sp_dawn_rehydrate_gap_s",
    "sw_midday_drench_enabled": "sp_sw_midday_drench_enabled",
    "midday_boost_offset_min": "sp_midday_boost_offset_min",
    "midday_drench_window_min": "sp_midday_drench_window_min",
    "midday_drench_on_s": "sp_midday_drench_on_s",
    "midday_drench_gap_s": "sp_midday_drench_gap_s",
}

# Telemetry columns fed straight through from the view (same names the
# export corpus uses, so the harness header parse is unchanged).
BASE_INPUT_COLUMNS = (
    "ts",
    "temp_avg",
    "vpd_avg",
    "rh_avg",
    "outdoor_rh_pct",
    "enthalpy_delta",
    "outdoor_temp_f",
    "outdoor_dewpoint_f",
    "indoor_dew_point",
    "solar_irradiance_w_m2",
    "outdoor_data_age_s",
    "occupied",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def expected_sp_columns() -> tuple[str, ...]:
    """Parse EXPECTED_SP_COLUMNS out of replay_emit.cpp (single source of truth).

    The image ships the compiled binary, not the source, so fall back to the
    frozen copy below when the firmware tree is absent. The source-contract
    test asserts the frozen copy matches the .cpp whenever both exist.
    """
    cpp = _repo_root() / "firmware" / "test" / "replay_emit.cpp"
    if cpp.is_file():
        parsed = parse_expected_sp_columns(cpp.read_text())
        if parsed:
            return parsed
    return FROZEN_EXPECTED_SP_COLUMNS


def parse_expected_sp_columns(source: str) -> tuple[str, ...]:
    """Extract the EXPECTED_SP_COLUMNS string array from replay_emit.cpp."""
    import re

    match = re.search(r"EXPECTED_SP_COLUMNS\[\]\s*=\s*\{(.*?)\};", source, re.DOTALL)
    if not match:
        return ()
    return tuple(re.findall(r'"(sp_[a-z0-9_]+)"', match.group(1)))


# Frozen mirror of replay_emit.cpp EXPECTED_SP_COLUMNS (TWIN-3 surface) — the
# in-image fallback. tests/test_twin_live_driver.py pins this against the
# parsed .cpp array.
FROZEN_EXPECTED_SP_COLUMNS: tuple[str, ...] = (
    "sp_temp_low",
    "sp_temp_high",
    "sp_vpd_low",
    "sp_vpd_high",
    "sp_bias_cool",
    "sp_bias_heat",
    "sp_vpd_hysteresis",
    "sp_temp_hysteresis",
    "sp_safety_max",
    "sp_safety_min",
    "sp_vpd_max_safe",
    "sp_vpd_min_safe",
    "sp_fog_escalation_kpa",
    "sp_watch_dwell_s",
    "sp_mist_backoff_s",
    "sp_mist_s2_delay_s",
    "sp_sw_fsm_controller_enabled",
    "sp_heat_hysteresis",
    "sp_sealed_max_s",
    "sp_relief_duration_s",
    "sp_max_relief_cycles",
    "sp_fog_rh_ceiling",
    "sp_fog_min_temp",
    "sp_dehum_aggressive_kpa",
    "sp_occupancy_inhibit",
    "sp_vent_latch_timeout_ms",
    "sp_safety_max_seal_margin_f",
    "sp_econ_heat_margin_f",
    "sp_sw_summer_vent_enabled",
    "sp_vent_prefer_temp_delta_f",
    "sp_vent_prefer_dp_delta_f",
    "sp_outdoor_staleness_max_s",
    "sp_summer_vent_min_runtime_s",
    "sp_sw_dwell_gate_enabled",
    "sp_dwell_gate_ms",
    "sp_cool_stage2_over_high_f",
    "sp_cool_exit_hysteresis_f",
    "sp_cold_vent_guard_delta_f",
    "sp_cool_all_fans_at_high_enabled",
    "sp_direct_wet_stress_override_enabled",
    "sp_direct_wet_stress_vpd_margin_kpa",
    "sp_direct_wet_stress_min_dew_margin_f",
    "sp_sw_wet_taper_enabled",
    "sp_wet_taper_before_sunset_min",
    "sp_sw_night_stress_wet_enabled",
    "sp_night_stress_min_dew_margin_f",
    "sp_sw_dawn_rehydrate_enabled",
    "sp_dawn_boost_offset_min",
    "sp_dawn_rehydrate_window_min",
    "sp_dawn_rehydrate_on_s",
    "sp_dawn_rehydrate_gap_s",
    "sp_sw_midday_drench_enabled",
    "sp_midday_boost_offset_min",
    "sp_midday_drench_window_min",
    "sp_midday_drench_on_s",
    "sp_midday_drench_gap_s",
)


def input_header() -> tuple[str, ...]:
    """The full TSV header the live adapter feeds the stream harness."""
    return BASE_INPUT_COLUMNS + expected_sp_columns()


def assert_wire_mapping_partition(wire_field_names: set[str]) -> None:
    """POLICY_TO_SP and UNMAPPED_WIRE_FIELDS must exactly partition the wire schema.

    A new registry field that is neither mapped nor explicitly classified as
    unmapped aborts the driver — silent defaulting is the exact false-
    divergence failure mode TWIN-3 closed for the corpus path.
    """
    mapped = set(POLICY_TO_SP)
    overlap = mapped & UNMAPPED_WIRE_FIELDS
    if overlap:
        raise SystemExit(f"FATAL: wire fields both mapped and unmapped: {sorted(overlap)}")
    declared = mapped | UNMAPPED_WIRE_FIELDS
    missing = wire_field_names - declared
    extra = declared - wire_field_names
    if missing or extra:
        raise SystemExit(
            "FATAL: wire-schema mapping drift (live adapter must classify every "
            f"wire field): unclassified={sorted(missing)} stale={sorted(extra)}"
        )


# ── Value formatting for the harness TSV ─────────────────────────────────────


def _fmt(value: object) -> str:
    """Format one view/policy value the way the export corpus does."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def build_input_row(view_row: dict, policy_values: dict | None) -> dict[str, str]:
    """Map one v_policy_twin_asof_input row (+ decoded vector) onto the
    harness input columns.

    Precedence per sp_* column:
      1. decoded device-confirmed wire vector (POLICY_TO_SP)   — treatment truth
      2. as-of setpoint batch payload (SP_PARAM_TO_COLUMN)     — non-wire posture
      3. ""  → harness falls back to default_setpoints()        — explicit gap
    """
    out: dict[str, str] = {col: "" for col in input_header()}
    for col in BASE_INPUT_COLUMNS:
        if col == "occupied":
            out[col] = "t" if view_row.get("occupied") else "f"
        else:
            out[col] = _fmt(view_row.get(col))
    payload = view_row.get("sp_payload") or {}
    for param, col in SP_PARAM_TO_COLUMN.items():
        if param in payload:
            out[col] = _fmt(payload[param])
    if policy_values:
        for field, col in POLICY_TO_SP.items():
            if field in policy_values:
                out[col] = _fmt(policy_values[field])
    return out


# ── §8.9 classification ──────────────────────────────────────────────────────


def classify_tick(
    view_row: dict,
    *,
    warmed_up: bool,
    vector_decoded: bool,
    snapshot_max_age_s: float,
    relay_max_age_s: float,
) -> tuple[str, str | None]:
    """Classify one tick BEFORE comparing actions.

    Returns (classification, gap_reason). ``CLASS_AGREEMENT`` here means
    "comparable" — the caller downgrades to divergence on any relay or hash
    mismatch. Ordering is deliberate: feed gaps first (nothing to compare),
    then identity mismatches (comparison would be against the wrong policy),
    then warm-up (twin state not yet trustworthy).
    """
    if view_row.get("temp_avg") is None or view_row.get("rh_avg") is None or view_row.get("vpd_avg") is None:
        return CLASS_GAP, "sensor_missing"
    if not view_row.get("clock_valid", True):
        return CLASS_GAP, "clock_invalid"
    if view_row.get("snapshot_id") is None:
        return CLASS_GAP, "no_device_snapshot"
    age = view_row.get("snapshot_age_s")
    if age is not None and snapshot_max_age_s > 0 and age > snapshot_max_age_s:
        return CLASS_GAP, "stale_device_snapshot"
    if any(view_row.get(f"live_relay_{r}") is None for r in RELAYS):
        return CLASS_GAP, "relay_readback_missing"
    relay_age = view_row.get("relay_readback_age_s")
    if relay_max_age_s > 0 and relay_age is not None and relay_age > relay_max_age_s:
        return CLASS_GAP, "relay_readback_stale"
    apply_state = view_row.get("apply_state")
    if apply_state != "active":
        return CLASS_UNMATCHED, f"apply_state:{apply_state or 'unknown'}"
    if view_row.get("vector_id") is None:
        return CLASS_UNMATCHED, "vector_unknown"
    if not view_row.get("policy_hash_match"):
        return CLASS_UNMATCHED, "content_hash_mismatch"
    if not view_row.get("validity_contains_tick", True):
        return CLASS_UNMATCHED, "outside_validity"
    if not vector_decoded:
        return CLASS_UNMATCHED, "vector_decode_failed"
    if not warmed_up:
        return CLASS_WARM_UP, "warm_up_window"
    return CLASS_AGREEMENT, None


def compare_actions(decision: dict[str, str], view_row: dict) -> tuple[bool, dict[str, bool], dict[str, bool]]:
    """Relay-level action comparison: twin decision vs live equipment readback."""
    twin_relays = {r: decision[f"relay_{r}"] in ("1", "true", "True") for r in RELAYS}
    live_relays = {r: bool(view_row[f"live_relay_{r}"]) for r in RELAYS}
    return twin_relays == live_relays, twin_relays, live_relays


def detect_boot(view_row: dict, prev_tick_ts: datetime | None) -> bool:
    """Firmware boot/reset since the previous processed tick (§8.9 reset)."""
    boot_ts = view_row.get("boot_event_ts")
    if boot_ts is None or prev_tick_ts is None:
        return False
    return boot_ts > prev_tick_ts


# ── Follower subprocess management ───────────────────────────────────────────


class Follower:
    """One resident replay_emit_follow process fed by a fixed synthetic header."""

    def __init__(self, binary: str, header_cols: tuple[str, ...], tmp_dir: str = "/tmp"):  # noqa: S108
        self.binary = binary
        self.header_cols = header_cols
        self.tmp_dir = tmp_dir
        self.proc: subprocess.Popen | None = None
        self.ticks_since_start = 0

    def start(self) -> None:
        self.stop()
        header_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 - lives for process lifetime
            mode="w", suffix=".csv", dir=self.tmp_dir, delete=False
        )
        header_file.write("\t".join(self.header_cols) + "\n")
        header_file.close()
        env = dict(os.environ, REPLAY_EMIT_REQUIRE_FULL_SETPOINTS="1")
        self.proc = subprocess.Popen(  # noqa: S603 - fixed binary, no shell
            [self.binary, "--stream", "--header-from", header_file.name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        assert self.proc.stdin is not None and self.proc.stdout is not None
        header = self.proc.stdout.readline()
        if not header.startswith("ts\tmode\t"):
            raise SystemExit(f"FATAL: unexpected follower header: {header!r}")
        self.decision_cols = tuple(header.rstrip("\n").split("\t"))
        self.ticks_since_start = 0

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
            self.proc = None

    def step(self, row: dict[str, str]) -> dict[str, str] | None:
        assert self.proc is not None
        line = "\t".join(row.get(col, "") for col in self.header_cols)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        decision = self.proc.stdout.readline().rstrip("\n").split("\t")
        self.ticks_since_start += 1
        if len(decision) != len(self.decision_cols):
            return None
        return dict(zip(self.decision_cols, decision, strict=True))


# ── DB plumbing ──────────────────────────────────────────────────────────────

VIEW_QUERY = """
SELECT *
  FROM public.v_policy_twin_asof_input
 WHERE greenhouse_id = %(greenhouse)s
   AND ts > %(after)s
   AND ts <= now() - make_interval(secs => %(settle)s)
 ORDER BY ts
 LIMIT %(limit)s
"""

INSERT_RESULT = """
INSERT INTO public.twin_live_results
    (greenhouse_id, tick_ts, twin_env, twin_ref, twin_mode,
     snapshot_id, device_id, device_generation, assignment_id,
     observed_content_sha256, observed_activation_sha256, apply_state,
     vector_id, vector_content_sha256, policy_hash_match,
     twin_decision_mode, twin_climate_action, twin_mist_stage,
     twin_relay_fog, twin_relay_vent, twin_relay_fan1, twin_relay_fan2,
     twin_relay_heat1, twin_relay_heat2, twin_mode_reason, twin_override_bits,
     live_relay_fog, live_relay_vent, live_relay_fan1, live_relay_fan2,
     live_relay_heat1, live_relay_heat2, live_relay_asof,
     action_agree, classification, gap_reason, twin_metadata)
VALUES
    (%(greenhouse_id)s, %(tick_ts)s, %(twin_env)s, %(twin_ref)s, 'live',
     %(snapshot_id)s, %(device_id)s, %(device_generation)s, %(assignment_id)s,
     %(observed_content_sha256)s, %(observed_activation_sha256)s, %(apply_state)s,
     %(vector_id)s, %(vector_content_sha256)s, %(policy_hash_match)s,
     %(twin_decision_mode)s, %(twin_climate_action)s, %(twin_mist_stage)s,
     %(twin_relay_fog)s, %(twin_relay_vent)s, %(twin_relay_fan1)s, %(twin_relay_fan2)s,
     %(twin_relay_heat1)s, %(twin_relay_heat2)s, %(twin_mode_reason)s, %(twin_override_bits)s,
     %(live_relay_fog)s, %(live_relay_vent)s, %(live_relay_fan1)s, %(live_relay_fan2)s,
     %(live_relay_heat1)s, %(live_relay_heat2)s, %(live_relay_asof)s,
     %(action_agree)s, %(classification)s, %(gap_reason)s, %(twin_metadata)s::jsonb)
"""


def _load_offline_driver():
    """Reuse the offline driver's read-only startup probe (same directory in
    the repo AND in the image's /usr/local/bin)."""
    path = Path(__file__).resolve().parent / "offline_driver.py"
    spec = importlib.util.spec_from_file_location("twin_offline_driver", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def decode_vector(canonical_bytes: bytes | memoryview | None):
    """Decode the paired vector's canonical bytes; None on any codec reject."""
    if canonical_bytes is None:
        return None
    from verdify_schemas.policy_vector import decode_policy_vector

    try:
        return decode_policy_vector(bytes(canonical_bytes))
    except ValueError as exc:
        print(f"[twin-live] vector decode rejected: {exc}", file=sys.stderr)
        return None


def process_tick(
    view_row: dict,
    follower: Follower,
    *,
    warmup_ticks: int,
    snapshot_max_age_s: float,
    relay_max_age_s: float,
    prev_tick_ts: datetime | None,
    reset_gap_s: float,
) -> tuple[dict, bool]:
    """Advance the twin by one tick; return (result-row params, did_reset)."""
    reset_cause: str | None = None
    tick_ts = view_row["ts"]
    if detect_boot(view_row, prev_tick_ts):
        # §8.9: the firmware rebooted — restart the follower so the twin's
        # ControlState re-initializes exactly like initial_state() on-device,
        # and hold a warm-up window before counting agreement again.
        follower.start()
        reset_cause = "boot_reset"
    elif (
        prev_tick_ts is not None
        and (tick_ts - prev_tick_ts).total_seconds() > reset_gap_s
        and follower.ticks_since_start > 0
    ):
        # The harness itself resets state on >600 s input gaps; restart so
        # our warm-up accounting matches its internal reset.
        follower.start()
        reset_cause = "feed_gap_reset"
    did_reset = reset_cause is not None

    policy_values = decode_vector(view_row.get("vector_canonical_bytes"))
    input_row = build_input_row(view_row, policy_values)
    decision = follower.step(input_row)

    warmed_up = follower.ticks_since_start > warmup_ticks
    classification, gap_reason = classify_tick(
        view_row,
        warmed_up=warmed_up,
        vector_decoded=policy_values is not None,
        snapshot_max_age_s=snapshot_max_age_s,
        relay_max_age_s=relay_max_age_s,
    )
    if reset_cause is not None and classification in (CLASS_AGREEMENT, CLASS_WARM_UP):
        classification, gap_reason = CLASS_WARM_UP, reset_cause

    action_agree = None
    twin_relays = dict.fromkeys(RELAYS)
    if decision is None:
        classification, gap_reason = CLASS_GAP, "twin_decision_malformed"
    else:
        agree, twin_relays, _live = compare_actions(decision, view_row)
        if view_row.get("live_relay_fog") is not None:
            action_agree = agree
        if classification == CLASS_AGREEMENT and not agree:
            classification = CLASS_DIVERGENCE

    params = {
        "greenhouse_id": view_row["greenhouse_id"],
        "tick_ts": tick_ts,
        "twin_env": os.environ.get("TWIN_ENV", "prod"),
        "twin_ref": os.environ.get("TWIN_REF", "last-good"),
        "snapshot_id": view_row.get("snapshot_id"),
        "device_id": view_row.get("device_id"),
        "device_generation": view_row.get("device_generation"),
        "assignment_id": view_row.get("assignment_id"),
        "observed_content_sha256": view_row.get("observed_content_sha256"),
        "observed_activation_sha256": view_row.get("observed_activation_sha256"),
        "apply_state": view_row.get("apply_state"),
        "vector_id": view_row.get("vector_id"),
        "vector_content_sha256": view_row.get("vector_content_sha256"),
        "policy_hash_match": view_row.get("policy_hash_match"),
        "twin_decision_mode": decision["mode"] if decision else None,
        "twin_climate_action": decision.get("climate_action") if decision else None,
        "twin_mist_stage": int(decision["mist_stage"]) if decision else None,
        **{f"twin_relay_{r}": twin_relays[r] for r in RELAYS},
        "twin_mode_reason": decision["reason"] if decision else None,
        "twin_override_bits": int(decision["override_bits"]) if decision else None,
        **{f"live_relay_{r}": view_row.get(f"live_relay_{r}") for r in RELAYS},
        "live_relay_asof": view_row.get("relay_readback_asof"),
        "action_agree": action_agree,
        "classification": classification,
        "gap_reason": gap_reason,
        "twin_metadata": _metadata_json(view_row, policy_values),
    }
    return params, did_reset


def _metadata_json(view_row: dict, policy_values: dict | None) -> str:
    import json

    from verdify_schemas.tunable_registry import POLICY_WIRE_FIELD_COUNT, WIRE_SCHEMA_VERSION

    return json.dumps(
        {
            "driver": "live",
            "wire_schema_version": WIRE_SCHEMA_VERSION,
            "wire_field_count": POLICY_WIRE_FIELD_COUNT,
            "wire_fields_mapped": len(POLICY_TO_SP),
            "wire_fields_unmapped": len(UNMAPPED_WIRE_FIELDS),
            "vpd_zone_inputs": "homogenized",
            "vector_decoded": policy_values is not None,
            "sp_batch_asof": _fmt(view_row.get("sp_asof")) or None,
            "snapshot_age_s": view_row.get("snapshot_age_s"),
        }
    )


def main() -> int:
    dsn = os.environ.get("TWIN_DSN", "").strip()
    if not dsn:
        print("FATAL: live mode requires TWIN_DSN (the twin login user)", file=sys.stderr)
        return 2
    twin_env = os.environ.get("TWIN_ENV", "prod")
    if twin_env not in ("dev", "stage", "prod"):
        print(f"FATAL: TWIN_ENV must be dev|stage|prod, got {twin_env!r}", file=sys.stderr)
        return 2
    greenhouse = os.environ.get("TWIN_GREENHOUSE", "vallery")
    binary = os.environ.get("TWIN_BINARY", "replay_emit_follow")
    poll_s = float(os.environ.get("TWIN_POLL_INTERVAL_S", "60"))
    settle_s = float(os.environ.get("TWIN_SETTLE_S", "120"))
    limit = int(os.environ.get("TWIN_BATCH_LIMIT", "720"))
    warmup_ticks = int(os.environ.get("TWIN_WARMUP_TICKS", "30"))
    snapshot_max_age_s = float(os.environ.get("TWIN_SNAPSHOT_MAX_AGE_S", "21600"))
    relay_max_age_s = float(os.environ.get("TWIN_RELAY_MAX_AGE_S", "0"))
    reset_gap_s = float(os.environ.get("TWIN_RESET_GAP_S", "600"))
    once = os.environ.get("TWIN_ONCE", "") not in ("", "0")

    from verdify_schemas.policy_vector import wire_fields

    assert_wire_mapping_partition({d.name for d in wire_fields()})

    import psycopg
    from psycopg.rows import dict_row

    offline = _load_offline_driver()
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    offline._assert_read_only(conn)
    print(f"[twin-live] connected; {offline.CONTROL_TABLE} write-lock verified (L2)", file=sys.stderr)

    follower = Follower(binary, input_header())
    follower.start()

    # Fresh start = warm-up by construction: begin far enough back that the
    # warm-up window is fed before "now", but never claim agreement in it.
    cursor_ts: datetime | None = None
    prev_tick_ts: datetime | None = None
    written = 0
    try:
        while True:
            if cursor_ts is None:
                row = conn.execute(
                    "SELECT now() - make_interval(secs => %s) AS t",
                    ((warmup_ticks + 5) * 60 + settle_s,),
                ).fetchone()
                cursor_ts = row["t"]
            rows = conn.execute(
                VIEW_QUERY,
                {"greenhouse": greenhouse, "after": cursor_ts, "settle": settle_s, "limit": limit},
            ).fetchall()
            for view_row in rows:
                params, _reset = process_tick(
                    view_row,
                    follower,
                    warmup_ticks=warmup_ticks,
                    snapshot_max_age_s=snapshot_max_age_s,
                    relay_max_age_s=relay_max_age_s,
                    prev_tick_ts=prev_tick_ts,
                    reset_gap_s=reset_gap_s,
                )
                conn.execute(INSERT_RESULT, params)
                written += 1
                prev_tick_ts = view_row["ts"]
                cursor_ts = view_row["ts"]
            if rows:
                print(f"[twin-live] {len(rows)} ticks -> twin_live_results (total {written})", file=sys.stderr)
            if once:
                break
            time.sleep(poll_s)
    finally:
        follower.stop()
        conn.close()
    print(f"[twin-live] exiting after {written} ticks", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
