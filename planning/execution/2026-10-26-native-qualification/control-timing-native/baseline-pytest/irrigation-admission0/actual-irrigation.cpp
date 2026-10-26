
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>
#include "irrigation_policy.h"
#define id(x) x
#define ESP_LOGI(tag, message, ...) events.emplace_back(message)
#define ESP_LOGW(tag, message, ...) events.emplace_back(message)
#define ESP_LOGE(tag, message, ...) events.emplace_back(message)

// Adapters record accepted switch state changes; actual hardware callbacks and
// physical GPIO transients are outside this source-ordering qualification.
struct RelayAdapter {
    std::vector<std::string>& events;
    std::string name;
    bool state{false};
    unsigned starts{0};
    void turn_on() {
        if (!state) { events.push_back(name + ":on"); ++starts; }
        state = true;
    }
    void turn_off() {
        if (state) events.push_back(name + ":off");
        state = false;
    }
};

struct Fixture {
    std::vector<std::string> events;
    RelayAdapter wall_drips{events, "wall_clean"};
    RelayAdapter wall_drips_fertilized{events, "wall_feed"};
    RelayAdapter south_wall_mister{events, "south_clean"};
    RelayAdapter south_wall_mister_fertilized{events, "south_feed"};
    RelayAdapter west_wall_mister{events, "west_clean"};
    RelayAdapter west_wall_mister_fertilized{events, "west_feed"};
    RelayAdapter center_drips{events, "center_clean"};
    RelayAdapter center_drips_fertilized{events, "center_feed"};
    RelayAdapter fertilizer_master_valve{events, "master"};
    RelayAdapter center_mister{events, "center_mist"};
    bool clock_valid{true}, leak_block{false};
    bool water_controller_actuating{false}, fert_controller_actuating{false};
    float current_solar_phase{0.25f};
    uint32_t solar_day{20'000u}, wall_commissioning_revision{7u};
    int irrig_state{0}, irrig_queue{0};
    uint32_t irrig_timer_ms{0};
    int irrig_wall_duration_min{1}, irrig_center_duration_min{1};
    unsigned cnt_drip_wall_today{0}, cnt_drip_center_today{0};
    unsigned irrig_wall_cycle_count{0};
    WetTopologyPolicy topology{true, false, false, true, true};
    WeeklyWallFeedState weekly_state{};
    WallFeedDurations durations{};
    bool save_allowed{true}, sync_allowed{true}, pending_valid{false};
    unsigned saves{0}, syncs{0};
    WeeklyWallFeedJournalRecord journal{}, pending{}, durable{};

    Fixture(float flow = 12.0f, float prewet = 1.0f,
            float feed = 1.0f, float flush = 1.0f) {
        WallFeedCommissioning commissioning{
            true, wall_commissioning_revision, WALL_COMMISSIONING_REQUIRED_MASK,
            1'800'000'000u, 1'800'086'400u, flow, 1'800'086'400u,
            prewet, feed, flush};
        durations = validate_wall_feed_commissioning(commissioning);
        assert(durations.valid);
    }

    void assert_no_forbidden_relay() const {
        assert(!south_wall_mister.state && !west_wall_mister.state);
        assert(!south_wall_mister_fertilized.state);
        assert(!west_wall_mister_fertilized.state && !center_drips_fertilized.state);
        assert(!center_mister.state);
    }

    void tick(uint32_t irrig_dt) {
auto turn_off_all_irrigation = [&]() {
              id(wall_drips).turn_off();
              id(wall_drips_fertilized).turn_off();
              id(south_wall_mister).turn_off();
              id(south_wall_mister_fertilized).turn_off();
              id(west_wall_mister).turn_off();
              id(west_wall_mister_fertilized).turn_off();
              id(center_drips).turn_off();
              id(center_drips_fertilized).turn_off();
              id(fertilizer_master_valve).turn_off();
            };
auto weekly_stage_active = [](WallFeedStage stage) {
            return stage == WallFeedStage::PREWET
              || stage == WallFeedStage::FEED
              || stage == WallFeedStage::FLUSH;
          };
          
        // Existing synchronous journal primitive is real. Only the NVS device
        // and readback mirrors are adapted; a failed save/sync never commits.
        auto persist_weekly_and_sync = [&](const WeeklyWallFeedState& state,
                                           int terminal_code) {
            WeeklyWallFeedJournalRecord committed{};
            const bool ok = commit_wall_feed_journal(
                state, static_cast<uint8_t>(terminal_code), journal.sequence + 1u,
                [&](const WeeklyWallFeedJournalRecord& candidate) {
                    ++saves; events.emplace_back("journal:save");
                    if (!save_allowed) return false;
                    pending = candidate; pending_valid = true; return true;
                },
                [&]() {
                    ++syncs; events.emplace_back("journal:sync");
                    if (!sync_allowed) { pending_valid = false; return false; }
                    assert(pending_valid); durable = pending;
                    pending_valid = false; return true;
                }, committed);
            if (ok) {
                assert(wall_feed_journal_record_valid(committed));
                journal = committed;
                weekly_state = wall_feed_state_from_journal(committed);
            }
            return ok;
        };
auto cancel_all = [&](const char* reason) {
            turn_off_all_irrigation();
            if(weekly_stage_active(weekly_state.stage)) {
              auto cancelled = advance_wall_feed_sequence(
                weekly_state, false, true, solar_day);
              if(!persist_weekly_and_sync(cancelled, 2)) {
                ESP_LOGE("irrig", "Cancellation persistence failed; all relays remain OFF");
              }
            }
            id(irrig_state) = 0;
            id(irrig_queue) = 0;
            id(irrig_timer_ms) = 0;
            ESP_LOGE("irrig", "STOPPED fail-closed: %s", reason);
          };

          const bool weekly_active = weekly_stage_active(weekly_state.stage);
// Claim at most once each seven-day interval, only from sunrise to
          // solar noon. Missing today's window leaves the next solar day due.
          if(id(irrig_state) == 0 && id(irrig_queue) == 0 && !weekly_active) {
            WeeklyWallEligibilityInput eligibility{
              clock_valid, leak_block, current_solar_phase, solar_day, 7u};
            const auto feed_route = resolve_wet_request(
              {WetCommandOrigin::AUTOMATIC_WEEKLY_WALL_FEED,
               WetZone::WALL_DRIP, WetChemistry::FERTILIZER}, topology);
            if(feed_route.admitted
               && weekly_wall_feed_eligible(eligibility, weekly_state, durations)) {
              auto claimed = claim_weekly_wall_feed(
                eligibility, weekly_state, id(wall_commissioning_revision));
              if(!persist_weekly_and_sync(claimed, 0)) {
                cancel_all("claim persistence sync failed");
                return;
              }
              id(irrig_state) = 5;
              id(irrig_timer_ms) = durations.prewet_ms;
              id(center_mister).turn_off();
              id(water_controller_actuating) = true;
              id(wall_drips).turn_on();
              id(water_controller_actuating) = false;
              id(cnt_drip_wall_today) += 1;
              ESP_LOGI("irrig", "WALL PREWET claim_day=%u rev=%u ms=%u",
                solar_day, id(wall_commissioning_revision), durations.prewet_ms);
            }
          }

          // Advance the wall sequence. FEED completion closes fertilizer first
          // and opens wall-local clean flush in this same control tick.
          if(id(irrig_state) >= 5 && id(irrig_state) <= 7) {
            if(irrig_dt >= id(irrig_timer_ms)) id(irrig_timer_ms) = 0;
            else id(irrig_timer_ms) -= irrig_dt;
            if(id(irrig_timer_ms) == 0) {
              if(id(irrig_state) == 5) {
                id(wall_drips).turn_off();
                auto next = advance_wall_feed_sequence(weekly_state, true, false, solar_day);
                if(!persist_weekly_and_sync(next, 0)) {
                  cancel_all("FEED transition persistence sync failed");
                  return;
                }
                id(irrig_state) = 6;
                id(irrig_timer_ms) = durations.feed_ms;
                id(fert_controller_actuating) = true;
                id(fertilizer_master_valve).turn_on();
                id(wall_drips_fertilized).turn_on();
                id(fert_controller_actuating) = false;
                ESP_LOGI("irrig", "WALL FEED ms=%u", durations.feed_ms);
              } else if(id(irrig_state) == 6) {
                id(wall_drips_fertilized).turn_off();
                id(fertilizer_master_valve).turn_off();
                auto next = advance_wall_feed_sequence(weekly_state, true, false, solar_day);
                if(!persist_weekly_and_sync(next, 0)) {
                  cancel_all("FLUSH transition persistence sync failed");
                  return;
                }
                id(irrig_state) = 7;
                id(irrig_timer_ms) = durations.flush_ms;
                id(water_controller_actuating) = true;
                id(wall_drips).turn_on();
                id(water_controller_actuating) = false;
                id(cnt_drip_wall_today) += 1;
                ESP_LOGI("irrig", "WALL IMMEDIATE CLEAN FLUSH ms=%u", durations.flush_ms);
              } else {
                id(wall_drips).turn_off();
                auto complete = advance_wall_feed_sequence(weekly_state, true, false, solar_day);
                if(!persist_weekly_and_sync(complete, 1)) {
                  cancel_all("COMPLETE transition persistence sync failed");
                  return;
                }
                id(irrig_wall_cycle_count) += 1;
                id(irrig_state) = 0;
                ESP_LOGI("irrig", "WALL FEED COMPLETE claim_day=%u", complete.claimed_solar_day);
              }
            }
            return;  // weekly owner excludes every explicit clean writer
          }

          // Explicit clean irrigation remains represented but has no automatic
          // clock schedule. Center drip defaults disabled and requires its
          // operator enable; center mist is not an irrigation destination.
          if(id(irrig_state) == 0 && id(irrig_queue) != 0) {
            int job = 0, bit = 0;
            WetZone zone = WetZone::NONE;
            if(id(irrig_queue) & 1) { job = 1; bit = 1; zone = WetZone::WALL_DRIP; }
            else if(id(irrig_queue) & 4) { job = 2; bit = 4; zone = WetZone::CENTER_DRIP; }
            id(irrig_queue) &= ~bit;
            const auto route = resolve_wet_request(
              {WetCommandOrigin::EXPLICIT_IRRIGATION, zone, WetChemistry::CLEAN}, topology);
            if(route.admitted) {
              turn_off_all_irrigation();
              id(center_mister).turn_off();
              id(irrig_state) = job;
              const int duration_min = job == 2
                ? id(irrig_center_duration_min) : id(irrig_wall_duration_min);
              id(irrig_timer_ms) = uint32_t(duration_min) * 60000UL;
              id(water_controller_actuating) = true;
              if(route.relay == WetRelay::WALL_DRIP_CLEAN) { id(wall_drips).turn_on(); id(cnt_drip_wall_today) += 1; }
              else if(route.relay == WetRelay::CENTER_DRIP) { id(center_drips).turn_on(); id(cnt_drip_center_today) += 1; }
              id(water_controller_actuating) = false;
              ESP_LOGI("irrig", "EXPLICIT CLEAN START state=%d ms=%u", job, id(irrig_timer_ms));
            } else {
              ESP_LOGW("irrig", "EXPLICIT CLEAN REJECTED: %s", route.reason);
            }
          }

          if(id(irrig_state) >= 1 && id(irrig_state) <= 2) {
            if(irrig_dt >= id(irrig_timer_ms)) id(irrig_timer_ms) = 0;
            else id(irrig_timer_ms) -= irrig_dt;
            if(id(irrig_timer_ms) == 0) {
              if(id(irrig_state) == 1) id(wall_drips).turn_off();
              else if(id(irrig_state) == 2) id(center_drips).turn_off();
              id(irrig_state) = 0;
              ESP_LOGI("irrig", "EXPLICIT CLEAN COMPLETE");
            }
          }

    }
};

void minimum_prewet() {
    Fixture f;
    assert(f.durations.prewet_ms == 5000u);
    f.tick(5000u);  // This interval happened before the new claim.
    assert(f.irrig_state == 5 && f.weekly_state.stage == WallFeedStage::PREWET);
    assert(f.irrig_timer_ms == 5000u && f.wall_drips.state);
    assert(!f.wall_drips_fertilized.state && !f.fertilizer_master_valve.state);
    assert(f.saves == 1 && f.syncs == 1 && f.journal.sequence == 1);
    assert(f.cnt_drip_wall_today == 1 && !f.water_controller_actuating);
    const auto sync = std::find(f.events.begin(), f.events.end(), "journal:sync");
    const auto water = std::find(f.events.begin(), f.events.end(), "wall_clean:on");
    assert(sync != f.events.end() && water != f.events.end() && sync < water);
    f.tick(4999u);
    assert(f.irrig_timer_ms == 1u && f.wall_drips.state && f.saves == 1);
    f.tick(1u);
    assert(f.irrig_state == 6 && f.weekly_state.stage == WallFeedStage::FEED);
    assert(!f.wall_drips.state && f.wall_drips_fertilized.state);
    assert(f.fertilizer_master_valve.state && f.wall_drips_fertilized.starts == 1);
    assert(f.saves == 2 && !f.fert_controller_actuating);
    f.tick(5000u);
    assert(f.irrig_state == 7 && f.weekly_state.stage == WallFeedStage::FLUSH);
    assert(f.wall_drips.state && !f.wall_drips_fertilized.state);
    assert(!f.fertilizer_master_valve.state && f.saves == 3);
    f.tick(5000u);
    assert(f.irrig_state == 0 && f.weekly_state.stage == WallFeedStage::COMPLETE);
    assert(!f.wall_drips.state && f.irrig_wall_cycle_count == 1);
    assert(f.saves == 4 && f.journal.terminal_code == 1);
    f.tick(5000u);  // Same-day duplicate cannot obtain another claim.
    assert(f.saves == 4 && f.wall_drips_fertilized.starts == 1);
    f.assert_no_forbidden_relay();
}

void longer_prewet() {
    Fixture f(10.0f, 2.0f, 1.0f, 3.0f);
    assert(f.durations.prewet_ms == 12000u);
    f.tick(5000u);
    assert(f.irrig_state == 5 && f.irrig_timer_ms == 12000u);
    f.tick(5000u); assert(f.irrig_timer_ms == 7000u && f.wall_drips.state);
    f.tick(5000u); assert(f.irrig_timer_ms == 2000u && f.wall_drips.state);
    f.tick(1999u); assert(f.irrig_timer_ms == 1u && f.wall_drips.state);
    f.tick(1u);
    assert(f.irrig_state == 6 && f.irrig_timer_ms == 6000u);
    assert(f.saves == 2 && f.wall_drips_fertilized.starts == 1);
    f.assert_no_forbidden_relay();
}

void claim_failure(bool fail_save) {
    Fixture f;
    f.save_allowed = !fail_save; f.sync_allowed = fail_save;
    f.tick(5000u);
    assert(f.irrig_state == 0 && f.irrig_timer_ms == 0 && f.irrig_queue == 0);
    assert(f.weekly_state.stage == WallFeedStage::IDLE && f.journal.sequence == 0);
    assert(f.saves == 1 && f.syncs == (fail_save ? 0u : 1u));
    assert(f.wall_drips.starts == 0 && f.wall_drips_fertilized.starts == 0);
    assert(f.fertilizer_master_valve.starts == 0 && f.cnt_drip_wall_today == 0);
    assert(!f.wall_drips.state && !f.wall_drips_fertilized.state);
    assert(!f.fertilizer_master_valve.state);
    f.assert_no_forbidden_relay();
}

void active_stage(WallFeedStage stage, int irrig_state,
                  WallFeedStage expected_next, int expected_irrig) {
    Fixture f;
    f.weekly_state = {stage, f.solar_day, f.solar_day - 7u,
                      f.wall_commissioning_revision};
    f.journal = make_wall_feed_journal_record(f.weekly_state, 0, 1);
    assert(wall_feed_journal_record_valid(f.journal));
    f.irrig_state = irrig_state; f.irrig_timer_ms = 10000u;
    const auto intent = wall_feed_relay_intent(stage);
    f.wall_drips.state = intent.wall_clean;
    f.wall_drips_fertilized.state = intent.wall_fertilized;
    f.fertilizer_master_valve.state = intent.fertilizer_master;
    f.irrig_queue = 4;  // Active weekly owner must not dispatch queued center clean.
    f.tick(1000u);
    assert(f.weekly_state.stage == stage && f.irrig_state == irrig_state);
    assert(f.irrig_timer_ms == 9000u && f.saves == 0);
    assert(f.irrig_queue == 4 && f.center_drips.starts == 0);
    assert(f.weekly_state.claimed_solar_day == f.solar_day);
    f.tick(9000u);
    assert(f.weekly_state.stage == expected_next && f.irrig_state == expected_irrig);
    assert(f.saves == 1 && f.journal.sequence == 2);
    assert(f.irrig_queue == 4 && f.center_drips.starts == 0);
    assert(f.weekly_state.claimed_solar_day == f.solar_day);
    f.assert_no_forbidden_relay();
}

void explicit_clean(bool center) {
    Fixture f;
    f.topology.wall_fertigation_commissioned = false;
    f.irrig_queue = center ? 4 : 1;
    const int expected_state = center ? 2 : 1;
    RelayAdapter& relay = center ? f.center_drips : f.wall_drips;
    f.tick(5000u);  // The existing minimum clean duration is one minute.
    assert(f.irrig_state == expected_state && f.irrig_timer_ms == 60000u);
    assert(f.irrig_queue == 0 && relay.state && relay.starts == 1);
    assert(f.cnt_drip_center_today == (center ? 1u : 0u));
    assert(f.cnt_drip_wall_today == (center ? 0u : 1u));
    assert(!f.water_controller_actuating && f.saves == 0);
    f.tick(59999u);
    assert(f.irrig_state == expected_state && f.irrig_timer_ms == 1u && relay.state);
    f.tick(1u);
    assert(f.irrig_state == 0 && f.irrig_timer_ms == 0 && !relay.state);
    f.tick(5000u);
    assert(f.irrig_state == 0 && relay.starts == 1 && f.saves == 0);
    assert(!f.wall_drips_fertilized.state && !f.fertilizer_master_valve.state);
    f.assert_no_forbidden_relay();
}

void explicit_zero_duration(bool center) {
    Fixture f;
    f.topology.wall_fertigation_commissioned = false;
    // Zero is outside the supported tunable range. Preserve the existing
    // immediate-OFF fallback rather than retaining a zero-timer active relay.
    f.irrig_wall_duration_min = 0; f.irrig_center_duration_min = 0;
    f.irrig_queue = center ? 4 : 1;
    RelayAdapter& relay = center ? f.center_drips : f.wall_drips;
    f.tick(5000u);
    assert(f.irrig_state == 0 && f.irrig_timer_ms == 0 && f.irrig_queue == 0);
    assert(relay.starts == 1 && !relay.state);
    const auto on = std::find(f.events.begin(), f.events.end(), relay.name + ":on");
    const auto off = std::find(f.events.begin(), f.events.end(), relay.name + ":off");
    assert(on != f.events.end() && off != f.events.end() && on < off);
    assert(f.cnt_drip_center_today == (center ? 1u : 0u));
    assert(f.cnt_drip_wall_today == (center ? 0u : 1u));
    assert(!f.water_controller_actuating && f.saves == 0);
    assert(!f.wall_drips_fertilized.state && !f.fertilizer_master_valve.state);
    f.tick(5000u);
    assert(f.irrig_state == 0 && !relay.state && relay.starts == 1);
    f.assert_no_forbidden_relay();
}

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string scenario = argv[1];
    if (scenario == "minimum_prewet") minimum_prewet();
    else if (scenario == "longer_prewet") longer_prewet();
    else if (scenario == "claim_save_failure") claim_failure(true);
    else if (scenario == "claim_sync_failure") claim_failure(false);
    else if (scenario == "active_prewet")
        active_stage(WallFeedStage::PREWET, 5, WallFeedStage::FEED, 6);
    else if (scenario == "active_feed")
        active_stage(WallFeedStage::FEED, 6, WallFeedStage::FLUSH, 7);
    else if (scenario == "active_flush")
        active_stage(WallFeedStage::FLUSH, 7, WallFeedStage::COMPLETE, 0);
    else if (scenario == "explicit_wall") explicit_clean(false);
    else if (scenario == "explicit_center") explicit_clean(true);
    else if (scenario == "explicit_wall_zero") explicit_zero_duration(false);
    else if (scenario == "explicit_center_zero") explicit_zero_duration(true);
    else return 2;
}
