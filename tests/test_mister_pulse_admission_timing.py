"""Qualify actual-YAML mister phase timing with clock/storage/relay adapters.

The contiguous S1 admission, escalation, de-escalation and pulse-timer source is
executed together with its real turn_on_zone, dwell fence, cancellation and
center GPIO callbacks. Selectors and upstream safety decisions are supplied
inputs. This does not qualify the full controller, sensor selection, physical
GPIO transients, upstream engage-delay reset/accumulation, irrigation
commissioning or a firmware deployment.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTROLS_PATH = ROOT / "firmware/greenhouse/controls.yaml"
HARDWARE_PATH = ROOT / "firmware/greenhouse/hardware.yaml"


def _fragments():
    controls = CONTROLS_PATH.read_text()

    def fragment(start, end):
        assert controls.count(start) == 1, start
        begin = controls.index(start)
        return controls[begin : controls.index(end, begin)]

    center = next(
        item for item in yaml.safe_load(HARDWARE_PATH.read_text())["switch"] if item.get("id") == "center_mister"
    )
    return {
        "policy_selection": fragment("const auto pol =", "const auto polb ="),
        "phase_constants": fragment("const uint32_t MISTER_ENGAGE_DELAY_MS", "float south_vpd ="),
        "watchdog_and_topology": fragment("auto direct_wet_relay_watchdog =", "// If misters WERE firing"),
        "vent_cancellation": fragment("if (!mister_vent_ok &&", "// Defensive: zero the engage-delay"),
        "blocked_predicate": fragment("bool mister_blocked =", "if(mister_blocked) {"),
        "blocked_cancellation": fragment("if(mister_blocked && id(mister_state) > 0)", "if(!mister_blocked){"),
        "cadence_constants": fragment("const uint32_t PULSE_ON_MS", "// Mister re-fire dwell fence:"),
        "dwell_fence": fragment("const uint32_t MISTER_MIN_OFF_MS", "// IRR-2: raise the hysteresis floor"),
        "hysteresis": fragment("const float MISTER_HYST", "// Helper: select mister zone"),
        "relay_application": fragment("auto turn_on_zone =", "// Fairness watchdog (sprint-2)"),
        "fallback": fragment("auto fallback_wet_zone =", "// --- S1 ENGAGE:"),
        # One contiguous fragment retains the original same-tick fallthrough.
        "admission_and_timer": fragment("// --- S1 ENGAGE:", "// Update h for telemetry"),
        "center_on_callback": center["on_turn_on"][0]["lambda"],
        "center_off_callback": center["on_turn_off"][0]["lambda"],
    }


def _source(parts):
    return (
        r"""
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <functional>
#include <string>
#include "policy_rom_baseline_generated.h"
using std::isnan;
using std::max;
#define id(x) x
#define ESP_LOGI(...) do {} while (false)
#define ESP_LOGE(...) do {} while (false)
uint32_t clock_ms = 1000000U;
uint32_t millis() { return clock_ms; }

// ESPHome switches deduplicate identical states and synchronously dispatch
// state callbacks. The actual center ON/OFF callback bodies are inserted below.
struct SwitchAdapter {
  bool state = false;
  unsigned starts = 0, stops = 0;
  std::function<void()> on_callback, off_callback;
  void turn_on() {
    if (state) return;
    state = true; ++starts;
    if (on_callback) on_callback();
  }
  void turn_off() {
    if (!state) return;
    state = false; ++stops;
    if (off_callback) off_callback();
  }
};
struct SensorAdapter { float state = 0; void publish_state(float value) { state = value; } };
struct Fixture {
  SwitchAdapter south_wall_mister, west_wall_mister;
  SwitchAdapter south_wall_mister_fertilized, west_wall_mister_fertilized;
  SwitchAdapter center_drips_fertilized, center_mister, fertilizer_master_valve;
  SensorAdapter mister_vpd_delta;
  bool climate_wet_controller_actuating = false;
  int irrig_state = 0, mister_state = 0, mister_pulse_zone = 0;
  int mister_selected_zone = 0, mister_rotation_index = 0, mister_duty_phase = 0;
  uint32_t mister_pulse_timer_ms = 0, mister_duty_timer_ms = 0;
  uint32_t humid_s1_duration_ms = 45000U, humid_s2_duration_ms = 0;
  uint32_t last_off_south_ms = 0, last_off_west_ms = 0, last_off_center_ms = 0;
  uint32_t last_fire_center_ms = 0;
  unsigned cnt_mister_center_today = 0;
  float mister_vpd_at_start = 3.5f;
  float mister_engage_delay_s = 45, mister_all_delay_s = 300;
  float mister_pulse_on_s = 60, mister_pulse_gap_s = 10, mister_min_off_s = 45;
  float mister_engage_kpa = 3.2f, mister_all_kpa = 3.8f;
  float mister_hysteresis_kpa = 0.1f, mister_vpd_weight = 1.5f;
  float vpd_target_south = 3.1f, vpd_target_west = 3.1f, vpd_target_center = 3.1f;
  struct { int center_burst = 0; } ctl_state;
  static constexpr int CENTER_BURST_NONE = 0;
  struct {
    bool experiment_active = false;
    std::array<float, verdify_policy::kPolicyFieldCount> values{};
  } psnap;
  float avg_vpd = 3.5f, south_vpd = 3.5f, west_vpd = 3.5f;
  bool center_wet_allowed = true, humidity_demand = true;
  bool mister_vent_ok = true, leak_block = false, budget_block = false;
  bool irrigation_block = false, occupancy_blocks = false;
  bool any_zone_stressed = true, global_stressed = true;
  int selected_hint = 3;

  Fixture(bool snapshot, uint32_t on_ms) {
    clock_ms = 1000000U;
    psnap.experiment_active = snapshot;
    psnap.values[verdify_policy::kPF_mister_engage_delay_s] = mister_engage_delay_s;
    psnap.values[verdify_policy::kPF_mister_all_delay_s] = mister_all_delay_s;
    psnap.values[verdify_policy::kPF_mister_pulse_gap_s] = mister_pulse_gap_s;
    psnap.values[verdify_policy::kPF_mister_min_off_s] = mister_min_off_s;
    psnap.values[verdify_policy::kPF_mister_engage_kpa] = mister_engage_kpa;
    psnap.values[verdify_policy::kPF_mister_all_kpa] = mister_all_kpa;
    psnap.values[verdify_policy::kPF_mister_vpd_weight] = mister_vpd_weight;
    const auto& contract = verdify_policy::kPolicyFields[verdify_policy::kPF_mister_pulse_on_s];
    const float seconds = on_ms / 1000.0f;
    assert(seconds * contract.scale >= contract.min_raw);
    assert(seconds * contract.scale <= contract.max_raw);
    if (snapshot) psnap.values[verdify_policy::kPF_mister_pulse_on_s] = seconds;
    else mister_pulse_on_s = seconds;
    center_mister.on_callback = [&]() {
"""
        + parts["center_on_callback"]
        + "\n};\ncenter_mister.off_callback = [&]() {\n"
        + parts["center_off_callback"]
        + r"""
    };
  }
  void assert_center_only() const {
    assert(!south_wall_mister.state && !west_wall_mister.state);
    assert(south_wall_mister.starts == 0 && west_wall_mister.starts == 0);
    assert(!south_wall_mister_fertilized.state && !west_wall_mister_fertilized.state);
    assert(!center_drips_fertilized.state);
    assert(south_wall_mister_fertilized.starts == 0);
    assert(west_wall_mister_fertilized.starts == 0 && center_drips_fertilized.starts == 0);
    assert(!climate_wet_controller_actuating);
  }
  void tick(uint32_t dt_ms) {
    clock_ms += dt_ms;
    const uint32_t now_ms = clock_ms;
    const bool south_wet_allowed = false, west_wet_allowed = false;
    const bool any_mister_wet_allowed = center_wet_allowed;
    const bool zone_mister_demand = humidity_demand && mister_vent_ok;
    const float HV = 0.25f;
"""
        + parts["policy_selection"]
        + parts["phase_constants"]
        + parts["watchdog_and_topology"]
        + parts["vent_cancellation"]
        + parts["blocked_predicate"]
        + parts["blocked_cancellation"]
        + "\nif (!mister_blocked) {\n"
        + parts["cadence_constants"]
        + parts["dwell_fence"]
        + parts["hysteresis"]
        + parts["relay_application"]
        + "\nauto select_zone_for_pulse = [&]() -> int { return selected_hint; };\n"
        + parts["fallback"]
        + parts["admission_and_timer"]
        + r"""
    }
    assert_center_only();
  }
};

void initial(bool snapshot, uint32_t on_ms) {
  Fixture f(snapshot, on_ms);
  f.tick(1000U);  // Entire interval predates admission.
  assert(f.center_mister.state && f.mister_state == 1 && f.mister_pulse_zone == 3);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 1);
  f.tick(on_ms - 1U);
  assert(f.center_mister.state && f.mister_pulse_timer_ms == 1U);
  f.tick(1U);
  assert(!f.center_mister.state && f.mister_pulse_zone == 0);
  assert(f.mister_pulse_timer_ms == 10000U && f.center_mister.stops == 1);
  assert(f.last_off_center_ms == clock_ms);
}

void rearm(bool snapshot, uint32_t on_ms, bool escalation) {
  Fixture f(snapshot, on_ms);
  f.mister_state = escalation ? 1 : 2;
  f.mister_pulse_zone = 0; f.mister_pulse_timer_ms = 5000U;
  f.humid_s2_duration_ms = 299000U;
  f.avg_vpd = escalation ? 4.0f : 3.3f;
  f.tick(1000U);
  assert(f.mister_state == (escalation ? 2 : 1));
  assert(f.center_mister.state && f.mister_pulse_zone == 3);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 1);
  f.tick(on_ms - 1U);
  assert(f.center_mister.state && f.mister_pulse_timer_ms == 1U);
  f.tick(1U);
  assert(!f.center_mister.state && f.mister_pulse_timer_ms == 10000U);
}

void active(bool snapshot, uint32_t on_ms, bool gap) {
  Fixture f(snapshot, on_ms);
  f.mister_state = 1;
  f.mister_pulse_timer_ms = gap ? 10000U : on_ms;
  f.mister_pulse_zone = gap ? 0 : 3;
  f.center_mister.state = !gap;
  f.tick((gap ? 10000U : on_ms) - 1U);
  assert(f.center_mister.state == !gap && f.mister_pulse_timer_ms == 1U);
  f.tick(1U);
  assert(f.center_mister.state == gap);
  assert(f.mister_pulse_timer_ms == (gap ? on_ms : 10000U));
  assert(f.mister_pulse_zone == (gap ? 3 : 0));
}

void dwell(bool snapshot, uint32_t on_ms, bool boundary) {
  Fixture f(snapshot, on_ms);
  f.mister_state = 1; f.mister_pulse_timer_ms = 1U;
  f.last_off_center_ms = clock_ms - (boundary ? 44999U : 44998U);
  f.tick(1U);
  assert(f.center_mister.state == boundary);
  assert(f.mister_pulse_zone == (boundary ? 3 : 0));
  assert(f.mister_pulse_timer_ms == on_ms);
  const auto last_off = f.last_off_center_ms;
  f.tick(1U);  // Repeated OFF requests must not slide the dwell timestamp.
  assert(f.last_off_center_ms == last_off);
}

void cancellation(bool snapshot, uint32_t on_ms, const std::string& reason) {
  Fixture f(snapshot, on_ms);
  f.tick(1000U);
  assert(f.center_mister.state);
  if (reason == "leak") f.leak_block = true;
  else if (reason == "budget") f.budget_block = true;
  else if (reason == "occupancy") f.occupancy_blocks = true;
  else if (reason == "irrigation") { f.irrigation_block = true; f.irrig_state = 1; }
  else if (reason == "vent") f.mister_vent_ok = false;
  else if (reason == "topology") f.center_wet_allowed = false;
  else if (reason == "demand") f.humidity_demand = false;
  else assert(false);
  f.tick(1U);  // Hard cancellation must not wait for the armed ON timer.
  assert(!f.center_mister.state && f.mister_state == 0);
  const uint32_t stopped = f.last_off_center_ms;
  f.tick(1U);
  assert(!f.center_mister.state && f.last_off_center_ms == stopped);
  assert(f.center_mister.starts == 1 && f.center_mister.stops == 1);
}

void reentry(bool snapshot, uint32_t on_ms) {
  Fixture f(snapshot, on_ms);
  f.tick(1000U);
  f.leak_block = true; f.tick(1U);
  assert(!f.center_mister.state && f.mister_state == 0);
  f.leak_block = false;
  f.tick(45000U);  // Prior idle time must not consume the newly armed phase.
  assert(f.center_mister.state && f.mister_state == 1);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 2);
}

void topology(bool snapshot, uint32_t on_ms, int stale_hint) {
  Fixture f(snapshot, on_ms); f.selected_hint = stale_hint;
  f.tick(1000U);
  assert(f.center_mister.state && f.mister_pulse_zone == 3);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 1);
}

void callback_conflict(bool snapshot, uint32_t on_ms, bool manual) {
  Fixture f(snapshot, on_ms);
  f.climate_wet_controller_actuating = !manual;
  if (!manual) f.fertilizer_master_valve.state = true;
  f.center_mister.turn_on();
  // Actual ON callback rejects the request synchronously. This is a logical
  // callback assertion, not proof that a physical GPIO never briefly changed.
  assert(!f.center_mister.state && f.center_mister.stops == 1);
  assert(f.last_off_center_ms == clock_ms);
  f.climate_wet_controller_actuating = false;
  f.assert_center_only();
}

int main(int argc, char** argv) {
  assert(argc == 4);
  const std::string scenario = argv[1];
  const bool snapshot = std::string(argv[2]) == "snapshot";
  const uint32_t on_ms = std::stoul(argv[3]);
  if (scenario == "initial") initial(snapshot, on_ms);
  else if (scenario == "escalation") rearm(snapshot, on_ms, true);
  else if (scenario == "deescalation") rearm(snapshot, on_ms, false);
  else if (scenario == "active") active(snapshot, on_ms, false);
  else if (scenario == "gap") active(snapshot, on_ms, true);
  else if (scenario == "dwell_before") dwell(snapshot, on_ms, false);
  else if (scenario == "dwell_at") dwell(snapshot, on_ms, true);
  else if (scenario == "reentry") reentry(snapshot, on_ms);
  else if (scenario == "hint_south") topology(snapshot, on_ms, 1);
  else if (scenario == "hint_west") topology(snapshot, on_ms, 2);
  else if (scenario == "hint_none") topology(snapshot, on_ms, 0);
  else if (scenario == "manual") callback_conflict(snapshot, on_ms, true);
  else if (scenario == "fertilizer") callback_conflict(snapshot, on_ms, false);
  else if (scenario.rfind("cancel_", 0) == 0) cancellation(snapshot, on_ms, scenario.substr(7));
  else return 2;
}
"""
    )


@pytest.fixture(scope="module")
def mister_binary(tmp_path_factory, record_testsuite_property):
    parts = _fragments()
    manifest = {
        "controls_sha256": hashlib.sha256(CONTROLS_PATH.read_bytes()).hexdigest(),
        "hardware_sha256": hashlib.sha256(HARDWARE_PATH.read_bytes()).hexdigest(),
        "wire_contract_sha256": hashlib.sha256(
            (ROOT / "firmware/lib/policy_vector_generated.h").read_bytes()
        ).hexdigest(),
        "fragments": {name: hashlib.sha256(value.encode()).hexdigest() for name, value in parts.items()},
        "scope": "Actual phase/relay/cancellation source; synthetic clock, storage, selector inputs and switch dispatch",
        "limits": "No complete upstream safety-controller or physical GPIO qualification",
    }
    record_testsuite_property("mister_source_manifest", json.dumps(manifest, sort_keys=True))
    directory = tmp_path_factory.mktemp("mister-admission")
    (directory / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    source = directory / "actual-mister.cpp"
    source.write_text(_source(parts))
    binary = directory / "actual-mister"
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT / "firmware/lib"), str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return binary


@pytest.mark.parametrize("mode", ["legacy", "snapshot"])
@pytest.mark.parametrize("on_ms", [30000, 90000], ids=["30s", "90s"])
@pytest.mark.parametrize(
    "scenario",
    [
        "initial",
        "escalation",
        "deescalation",
        "active",
        "gap",
        "dwell_before",
        "dwell_at",
        "reentry",
        "hint_south",
        "hint_west",
        "hint_none",
        "manual",
        "fertilizer",
        "cancel_leak",
        "cancel_budget",
        "cancel_occupancy",
        "cancel_irrigation",
        "cancel_vent",
        "cancel_topology",
        "cancel_demand",
    ],
)
def test_actual_yaml_mister_phase_timing(mister_binary, scenario, mode, on_ms):
    result = subprocess.run(
        [str(mister_binary), scenario, mode, str(on_ms)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
