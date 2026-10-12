"""Execute the actual ESPHome safety fragments offline, without a device client."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = (ROOT / "firmware/greenhouse/controls.yaml").read_text()
HARDWARE = (ROOT / "firmware/greenhouse/hardware.yaml").read_text()


def run_cpp(tmp_path, body):
    source = tmp_path / "case.cpp"
    source.write_text(
        '#include <cassert>\n#include <cstdint>\n#include <initializer_list>\n#include "irrigation_policy.h"\n#define id(x) x\n'
        + body
    )
    binary = tmp_path / "case"
    subprocess.run(["c++", "-std=c++17", "-I", str(ROOT / "firmware/lib"), str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_active_wall_disable_cancels_every_stage_without_redose(tmp_path):
    start = CONTROLS.index("if(weekly_active && (")
    end = CONTROLS.index("if(!id(irrig_enabled))", start)
    guard = CONTROLS[start:end]
    run_cpp(
        tmp_path,
        """
int main() {
 for(auto stage : {WallFeedStage::PREWET, WallFeedStage::FEED, WallFeedStage::FLUSH}) {
  bool irrig_wall_enabled=false, clock_valid=true, weekly_active=true;
  uint32_t wall_commissioning_revision=7;
  WallFeedDurations durations{}; durations.valid=true;
  WeeklyWallFeedState weekly_state{stage, 200, 193, 7};
  bool cancelled=false, relay_on=true;
  auto cancel_all=[&](const char*) {
    relay_on=false;
    weekly_state=advance_wall_feed_sequence(weekly_state,false,true,200);
    cancelled=true;
  };
  auto tick=[&]() { """
        + guard
        + """ };
  tick();
  assert(cancelled && !relay_on);
  assert(weekly_state.stage==WallFeedStage::CANCELLED);
  assert(weekly_state.claimed_solar_day==200);
  assert(!weekly_wall_feed_eligible({true,false,0.5f,200,7},weekly_state,durations));
  assert(!weekly_wall_feed_eligible({true,false,0.5f,201,7},weekly_state,durations));
  irrig_wall_enabled=true;cancelled=false;relay_on=true;
  weekly_state={stage,200,193,7};tick();
  assert(!cancelled && relay_on && weekly_state.stage==stage);
 }
}
""",
    )


def test_all_actual_clean_relay_falling_edges_enforce_existing_dwell(tmp_path):
    callbacks = []
    for relay, stamp in [
        ("south_wall_mister", "last_off_south_ms"),
        ("west_wall_mister", "last_off_west_ms"),
        ("center_mister", "last_off_center_ms"),
    ]:
        start = HARDWARE.index("    id: " + relay + "\n")
        end = HARDWARE.index("\n  - platform:", start)
        block = HARDWARE[start:end]
        callback = block.split("    on_turn_off:\n      - lambda: |-\n", 1)[1]
        assert f"id({stamp}) = millis();" in callback
        callbacks.append(callback)
    start = CONTROLS.index("auto mister_zone_last_off =")
    end = CONTROLS.index("// IRR-2:", start)
    dwell = CONTROLS[start:end]
    midnight = CONTROLS[CONTROLS.index("// SAF-4/IRR-5:") : CONTROLS.index("// B5: reset DLI")]
    assert "id(last_off_" not in midnight
    assert CONTROLS.count("id(last_off_center_ms) =") == 0
    run_cpp(
        tmp_path,
        """
uint32_t clock_ms=0;
uint32_t millis(){return clock_ms;}
int main(){
 uint32_t last_off_south_ms=0,last_off_west_ms=0,last_off_center_ms=0,now_ms=0;
 const uint32_t MISTER_MIN_OFF_MS=45000;
 """
        + dwell
        + """
 auto south_off=[&](){"""
        + callbacks[0]
        + """};
 auto west_off=[&](){"""
        + callbacks[1]
        + """};
 auto center_off=[&](){"""
        + callbacks[2]
        + """};
 // ESPHome Switch::publish_state deduplicates identical states. Model the
 // same falling-edge contract, exercising the real callback lambda above.
 bool on=true;
 auto center_turn_off=[&](){if(on){on=false;center_off();}};
 clock_ms=100000; center_turn_off();
 now_ms=101000; assert(!mister_zone_can_on(3));
 clock_ms=120000; center_turn_off(); assert(last_off_center_ms==100000);
 now_ms=144999; assert(!mister_zone_can_on(3));
 now_ms=145000; assert(mister_zone_can_on(3));
 clock_ms=200000;south_off();west_off();
 now_ms=244999;assert(!mister_zone_can_on(1)&&!mister_zone_can_on(2));
 now_ms=245000;assert(mister_zone_can_on(1)&&mister_zone_can_on(2));
 // Midnight changes wall-clock counters, not millis stamps.
 now_ms=200001;assert(!mister_zone_can_on(1));
 // Conservative startup OFF publication/reset cannot admit a pulse early.
 clock_ms=0;center_off();
 now_ms=44999;assert(!mister_zone_can_on(3));
 now_ms=45000;assert(mister_zone_can_on(3));
 // The existing unsigned subtraction remains safe across millis rollover.
 clock_ms=UINT32_MAX-9999;center_off();
 now_ms=34999;assert(!mister_zone_can_on(3));
 now_ms=35000;assert(mister_zone_can_on(3));
}
""",
    )
