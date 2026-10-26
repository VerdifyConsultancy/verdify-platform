"""Run the actual ESPHome relay consumer offline across policy changes.

The generated C++ includes the real snapshot/legacy policy-selection lambda,
policy-derived constants, static RelayMeta initialization, any subsequent
refresh, the real relay lambdas, and the final counter/application caller verbatim.
Only Switch state, request inputs and snapshot/legacy storage are adapted; relay
behavior is not reimplemented here. Every case starts a fresh process so the
firmware's static initialization executes
once, followed by several ticks in that same process.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROLS_PATH = ROOT / "firmware/greenhouse/controls.yaml"
RELAYS = ("heat1", "heat2", "fan1", "fan2", "fog", "vent")


@pytest.fixture(scope="module")
def native_relay_consumer(tmp_path_factory, record_testsuite_property):
    controls = CONTROLS_PATH.read_text()
    policy_start = controls.index("const auto pol =")
    policy_end = controls.index("const auto polb =", policy_start)
    constants_start = controls.index("const uint32_t MIN_HEAT_ON_MS")
    constants_end = controls.index("const float    EXTRA_COOL_S3_DELTA_F", constants_start)
    relay_start = controls.index("struct RelayMeta {")
    relay_end = controls.index("/**************** 3", relay_start)
    caller_start = controls.index("/**************** 10 — CYCLE COUNTERS")
    caller_end = controls.index("/**************** 12 — MISTER STATE MACHINE", caller_start)
    policy_selection = controls[policy_start:policy_end]
    constants = controls[constants_start:constants_end]
    relay = controls[relay_start:relay_end]
    caller = controls[caller_start:caller_end]
    manifest = {
        "source": str(CONTROLS_PATH.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(CONTROLS_PATH.read_bytes()).hexdigest(),
        "duration_contract_sha256": hashlib.sha256(
            (ROOT / "firmware/lib/policy_vector_generated.h").read_bytes()
        ).hexdigest(),
        "test_durations_s": [60, 120, 240],
        "duration_validation": "Native cases check generated wire min/max/scale before each change",
        "fragments": {
            "snapshot_or_legacy_policy_selection": {
                "first_line": controls.count("\n", 0, policy_start) + 1,
                "last_line": controls.count("\n", 0, policy_end) + 1,
                "sha256": hashlib.sha256(policy_selection.encode()).hexdigest(),
            },
            "policy_constants": {
                "first_line": controls.count("\n", 0, constants_start) + 1,
                "last_line": controls.count("\n", 0, constants_end) + 1,
                "sha256": hashlib.sha256(constants.encode()).hexdigest(),
            },
            "relay_initialization_refresh_and_lambdas": {
                "first_line": controls.count("\n", 0, relay_start) + 1,
                "last_line": controls.count("\n", 0, relay_end) + 1,
                "sha256": hashlib.sha256(relay.encode()).hexdigest(),
            },
            "cycle_counters_and_actual_relay_application": {
                "first_line": controls.count("\n", 0, caller_start) + 1,
                "last_line": controls.count("\n", 0, caller_end) + 1,
                "sha256": hashlib.sha256(caller.encode()).hexdigest(),
            },
        },
    }
    record_testsuite_property("relay_consumer_source_sha256", manifest["source_sha256"])
    record_testsuite_property(
        "relay_policy_selection_sha256", manifest["fragments"]["snapshot_or_legacy_policy_selection"]["sha256"]
    )
    work = tmp_path_factory.mktemp("policy-relay-dwell")
    (work / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    source = work / "relay_consumer.cpp"
    source.write_text(
        r"""
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include "policy_rom_baseline_generated.h"

namespace esphome { namespace switch_ {
struct Switch {
  bool state = false;
  bool accepts_on = true;
  unsigned transitions = 0;
  void turn_on() { if (accepts_on && !state) { state = true; ++transitions; } }
  void turn_off() { if (state) { state = false; ++transitions; } }
};
}}
#define id(x) x

std::array<esphome::switch_::Switch, 6> switches;
auto* heat1_rly = &switches[0];
auto* heat2_rly = &switches[1];
auto* fan1_rly = &switches[2];
auto* fan2_rly = &switches[3];
auto* fog_rly = &switches[4];
auto* vent_rly = &switches[5];
int cnt_heat1_today=0, cnt_heat2_today=0, cnt_fan1_today=0;
int cnt_fan2_today=0, cnt_fog_today=0, cnt_vent_today=0;
const int* cycle_counters[] = {
  &cnt_heat1_today, &cnt_heat2_today, &cnt_fan1_today,
  &cnt_fan2_today, &cnt_fog_today, &cnt_vent_today};
struct PolicySnapshot {
  bool experiment_active = false;
  std::array<float, verdify_policy::kPolicyFieldCount> values{};
} psnap;
float min_heat_on_s=120, min_heat_off_s=120;
float min_fan_on_s=120, min_fan_off_s=120;
float min_fog_on_s=120, min_fog_off_s=120;
float min_vent_on_s=120, min_vent_off_s=120;

// Storage adapters only: update the selected source and leave the other source
// unchanged, so every boundary case also exercises the actual pol() selection.
struct DurationStorage { uint8_t field; float* legacy; };
const DurationStorage durations[] = {
  {verdify_policy::kPF_min_heat_on_s, &min_heat_on_s},
  {verdify_policy::kPF_min_heat_off_s, &min_heat_off_s},
  {verdify_policy::kPF_min_fan_on_s, &min_fan_on_s},
  {verdify_policy::kPF_min_fan_off_s, &min_fan_off_s},
  {verdify_policy::kPF_min_fog_on_s, &min_fog_on_s},
  {verdify_policy::kPF_min_fog_off_s, &min_fog_off_s},
  {verdify_policy::kPF_min_vent_on_s, &min_vent_on_s},
  {verdify_policy::kPF_min_vent_off_s, &min_vent_off_s}
};

void set_duration(uint8_t field, float seconds) {
  const auto& def = verdify_policy::kPolicyFields[field];
  const double raw = double(seconds) * def.scale;
  if (raw < def.min_raw || raw > def.max_raw || raw != int64_t(raw)) {
    std::fprintf(stderr, "test duration outside wire contract: %s=%g\n", def.name, seconds);
    std::exit(64);
  }
  for (const auto& storage : durations) {
    if (storage.field != field) continue;
    if (psnap.experiment_active) psnap.values[field] = seconds;
    else *storage.legacy = seconds;
    return;
  }
  std::exit(64);
}

void set_all_durations(float seconds) {
  for (const auto& storage : durations) set_duration(storage.field, seconds);
}

const uint8_t on_field[] = {
  verdify_policy::kPF_min_heat_on_s, verdify_policy::kPF_min_heat_on_s,
  verdify_policy::kPF_min_fan_on_s, verdify_policy::kPF_min_fan_on_s,
  verdify_policy::kPF_min_fog_on_s, verdify_policy::kPF_min_vent_on_s
};
const uint8_t off_field[] = {
  verdify_policy::kPF_min_heat_off_s, verdify_policy::kPF_min_heat_off_s,
  verdify_policy::kPF_min_fan_off_s, verdify_policy::kPF_min_fan_off_s,
  verdify_policy::kPF_min_fog_off_s, verdify_policy::kPF_min_vent_off_s
};

struct Observation {
  bool on;
  uint32_t min_on, min_off, last_on, last_off, on_stamp, runtime;
  unsigned transitions;
  int cycles, all_cycles;
};

Observation tick(int selected, uint32_t now_ms, bool want,
                 bool force_on=false, bool force_off=false,
                 bool actual_caller=false) {
"""
        + policy_selection
        + constants
        + relay
        + r"""
  if (actual_caller) {
    // Adapt already-resolved requests and safety inputs only. The firmware's
    // complete counter/application block below remains byte-for-byte source.
    const bool willHeat1 = selected == 0 && want;
    const bool willHeat2 = selected == 1 && want;
    const bool willFan1 = selected == 2 && want;
    const bool willFan2 = selected == 3 && want;
    const bool willFog = selected == 4 && want;
    const bool willVent = selected == 5 && want;
    const bool heat_air_exchange_interlock_active = force_off;
    const bool sensor_fault_relay_lock = force_off;
    const bool fan_requires_vent = selected == 5 && force_on;
    const bool manual_fan_force = (selected == 2 || selected == 3) && force_on;
    const bool manual_fog_force = selected == 4 && force_on;
    const bool leak_block=false, occupancy_moisture_block=false;
    const bool irrigation_water_conflict=false, controller_time_valid=true;
    const bool climate_water_budget_block=false, mister_volume_hard_block=false;
    const bool fert_master_on=false;
"""
        + caller
        + r"""
  } else {
    set_relay(R[selected], want, force_on, force_off);
  }
  const auto& r = R[selected];
  int all_cycles = 0;
  for (const auto* counter : cycle_counters) all_cycles += *counter;
  return {r.sw->state, r.min_on_ms, r.min_off_ms, r.last_on_ms,
          r.last_off_ms, r.on_stamp_ms, r.runtime_ms, r.sw->transitions,
          *cycle_counters[selected], all_cycles};
}

// Explicit expected boundary observations, not an alternate relay algorithm.
#define CHECK(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "failed line %d: %s\n", __LINE__, #condition); return 1; \
} } while (false)

int main(int argc, char** argv) {
  if (argc != 4) return 64;
  const int selected = std::atoi(argv[1]);
  if (selected < 0 || selected >= 6) return 64;
  const std::string scenario = argv[2];
  const std::string mode = argv[3];
  if (mode != "snapshot" && mode != "legacy") return 64;
  psnap.experiment_active = mode == "snapshot";
  for (const auto& storage : durations) psnap.values[storage.field] = 120.0f;
  set_all_durations(120.0f);
  const uint32_t start = 1000000U;

  if (scenario == "count_boot") {
    auto r = tick(selected, 1000U, true, false, false, true);
    CHECK(!r.on && r.transitions == 0U && r.cycles == 0 && r.all_cycles == 0);
    r = tick(selected, 119999U, true, false, false, true);
    CHECK(!r.on && r.transitions == 0U && r.cycles == 0 && r.all_cycles == 0);
    r = tick(selected, 120000U, true, false, false, true);
    CHECK(r.on && r.transitions == 1U && r.cycles == 1 && r.all_cycles == 1);
    r = tick(selected, 121000U, true, false, false, true);
    CHECK(r.on && r.transitions == 1U && r.cycles == 1 && r.all_cycles == 1);
    return 0;
  }

  if (scenario == "count_dwell") {
    auto r = tick(selected, start, true, false, false, true);
    CHECK(r.on && r.cycles == 1 && r.all_cycles == 1);
    const uint32_t stopped = start + 1000U;
    r = tick(selected, stopped, false, false, true, true);
    CHECK(!r.on && r.last_off == stopped && r.cycles == 1 && r.runtime == 1000U);
    r = tick(selected, stopped + 1000U, true, false, false, true);
    CHECK(!r.on && r.cycles == 1 && r.all_cycles == 1 && r.transitions == 2U);
    r = tick(selected, stopped + 119999U, true, false, false, true);
    CHECK(!r.on && r.cycles == 1 && r.all_cycles == 1 && r.last_off == stopped);
    r = tick(selected, stopped + 120000U, true, false, false, true);
    CHECK(r.on && r.cycles == 2 && r.all_cycles == 2 && r.transitions == 3U);
    r = tick(selected, stopped + 121000U, true, false, false, true);
    CHECK(r.on && r.cycles == 2 && r.all_cycles == 2 && r.transitions == 3U);
    return 0;
  }

  if (scenario == "count_rejected") {
    // A callback/hardware adapter may refuse the requested steady state.
    // Eligibility or an ON call alone cannot count as an accepted start.
    switches[selected].accepts_on = false;
    auto r = tick(selected, start, true, false, false, true);
    CHECK(!r.on && r.cycles == 0 && r.all_cycles == 0 && r.transitions == 0U);
    switches[selected].accepts_on = true;
    r = tick(selected, start + 1000U, true, false, false, true);
    CHECK(r.on && r.cycles == 1 && r.all_cycles == 1 && r.transitions == 1U);
    r = tick(selected, start + 2000U, true, false, false, true);
    CHECK(r.on && r.cycles == 1 && r.all_cycles == 1 && r.transitions == 1U);
    return 0;
  }

  if (scenario == "count_force") {
    // The actual caller exposes force-on only for fans, fog and vent.
    if (selected < 2) return 64;
    set_all_durations(240.0f);
    auto r = tick(selected, 1000U, true, true, false, true);
    CHECK(r.on && r.cycles == 1 && r.all_cycles == 1 && r.last_on == 1000U);
    r = tick(selected, 1001U, true, true, false, true);
    CHECK(r.on && r.cycles == 1 && r.all_cycles == 1);
    r = tick(selected, 1002U, false, false, true, true);
    CHECK(!r.on && r.cycles == 1 && r.last_off == 1002U && r.runtime == 2U);
    r = tick(selected, 1003U, true, false, false, true);
    CHECK(!r.on && r.cycles == 1 && r.all_cycles == 1);
    r = tick(selected, 1004U, true, true, false, true);
    CHECK(r.on && r.cycles == 2 && r.all_cycles == 2 && r.transitions == 3U);
    return 0;
  }

  if (scenario == "extend_on" || scenario == "shorten_on") {
    const bool extend = scenario == "extend_on";
    auto r = tick(selected, start, true);
    CHECK(r.on && r.last_on == start && r.on_stamp == start);
    set_duration(on_field[selected], extend ? 240.0f : 60.0f);
    const uint32_t duration = extend ? 240000U : 60000U;
    r = tick(selected, start + 30000U, false);
    CHECK(r.on && r.last_on == start && r.on_stamp == start && r.runtime == 0U);
    if (extend) {
      r = tick(selected, start + 120000U, false);
      CHECK(r.on && r.last_on == start && r.transitions == 1U);
    }
    r = tick(selected, start + duration - 1U, false);
    CHECK(r.on && r.last_on == start && r.on_stamp == start);
    r = tick(selected, start + duration, false);
    CHECK(!r.on && r.last_on == start && r.last_off == start + duration);
    CHECK(r.on_stamp == 0U && r.runtime == duration && r.transitions == 2U);
    return 0;
  }

  if (scenario == "extend_off" || scenario == "shorten_off") {
    const bool extend = scenario == "extend_off";
    tick(selected, start, true);
    const uint32_t stopped = start + 1000U;
    auto r = tick(selected, stopped, false, false, true);
    CHECK(!r.on && r.last_off == stopped && r.runtime == 1000U);
    set_duration(off_field[selected], extend ? 240.0f : 60.0f);
    const uint32_t duration = extend ? 240000U : 60000U;
    r = tick(selected, stopped + 30000U, true);
    CHECK(!r.on && r.last_off == stopped && r.runtime == 1000U);
    if (extend) {
      r = tick(selected, stopped + 120000U, true);
      CHECK(!r.on && r.last_off == stopped && r.transitions == 2U);
    }
    r = tick(selected, stopped + duration - 1U, true);
    CHECK(!r.on && r.last_off == stopped && r.last_on == start);
    r = tick(selected, stopped + duration, true);
    CHECK(r.on && r.last_off == stopped && r.last_on == stopped + duration);
    CHECK(r.on_stamp == stopped + duration && r.runtime == 1000U && r.transitions == 3U);
    return 0;
  }

  if (scenario == "force") {
    set_all_durations(240.0f);
    auto r = tick(selected, 1000U, true, true);
    CHECK(r.on && r.last_on == 1000U && r.transitions == 1U);
    set_duration(on_field[selected], 60.0f);
    r = tick(selected, 1001U, false, false, true);
    CHECK(!r.on && r.last_off == 1001U && r.runtime == 1U);
    set_duration(off_field[selected], 60.0f);
    r = tick(selected, 1002U, true, true);
    CHECK(r.on && r.last_on == 1002U && r.last_off == 1001U && r.transitions == 3U);
    return 0;
  }

  if (scenario == "rollover_on") {
    const uint32_t began = UINT32_MAX - 30000U;
    auto r = tick(selected, began, true, true);
    CHECK(r.on && r.last_on == began);
    set_duration(on_field[selected], 60.0f);
    r = tick(selected, began + 59999U, false);
    CHECK(r.on && r.last_on == began);
    r = tick(selected, began + 60000U, false);
    CHECK(!r.on && r.last_on == began && r.last_off == began + 60000U);
    CHECK(r.runtime == 60000U && r.on_stamp == 0U);
    return 0;
  }

  if (scenario == "rollover_off") {
    const uint32_t began = UINT32_MAX - 31000U;
    const uint32_t stopped = began + 1000U;
    tick(selected, began, true, true);
    auto r = tick(selected, stopped, false, false, true);
    CHECK(!r.on && r.last_off == stopped && r.runtime == 1000U);
    set_duration(off_field[selected], 60.0f);
    r = tick(selected, stopped + 59999U, true);
    CHECK(!r.on && r.last_off == stopped);
    r = tick(selected, stopped + 60000U, true);
    CHECK(r.on && r.last_off == stopped && r.last_on == stopped + 60000U);
    CHECK(r.runtime == 1000U && r.transitions == 3U);
    return 0;
  }
  return 64;
}
"""
    )
    binary = work / "relay_consumer"
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT / "firmware/lib"), str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return binary, manifest


@pytest.mark.parametrize("relay", RELAYS)
@pytest.mark.parametrize("mode", ("snapshot", "legacy"))
@pytest.mark.parametrize(
    "scenario",
    ("extend_on", "shorten_on", "extend_off", "shorten_off", "force", "rollover_on", "rollover_off"),
)
def test_policy_dwell_uses_original_relay_history(native_relay_consumer, relay, mode, scenario):
    binary, manifest = native_relay_consumer
    result = subprocess.run(
        [str(binary), str(RELAYS.index(relay)), scenario, mode],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"{relay}/{mode}/{scenario}: {result.stderr}\n"
        f"Actual firmware extraction: {json.dumps(manifest, sort_keys=True)}\n{result.stdout}"
    )


@pytest.mark.parametrize("relay", RELAYS)
@pytest.mark.parametrize("mode", ("snapshot", "legacy"))
@pytest.mark.parametrize("scenario", ("count_boot", "count_dwell", "count_rejected"))
def test_actual_caller_counts_only_accepted_relay_edges(native_relay_consumer, relay, mode, scenario):
    binary, manifest = native_relay_consumer
    result = subprocess.run(
        [str(binary), str(RELAYS.index(relay)), scenario, mode],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"{relay}/{mode}/{scenario}: {result.stderr}\n"
        f"Actual firmware extraction: {json.dumps(manifest, sort_keys=True)}\n{result.stdout}"
    )


@pytest.mark.parametrize("relay", ("fan1", "fan2", "fog", "vent"))
@pytest.mark.parametrize("mode", ("snapshot", "legacy"))
def test_actual_caller_counts_forced_start_once(native_relay_consumer, relay, mode):
    binary, manifest = native_relay_consumer
    result = subprocess.run(
        [str(binary), str(RELAYS.index(relay)), "count_force", mode],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"{relay}/{mode}/count_force: {result.stderr}\n"
        f"Actual firmware extraction: {json.dumps(manifest, sort_keys=True)}\n{result.stdout}"
    )
