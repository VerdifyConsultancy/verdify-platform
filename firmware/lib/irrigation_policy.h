#pragma once

#include <cmath>
#include <cstdint>

// Pure irrigation/fertigation policy used by both ESPHome and native tests.
// It owns command-origin resolution, fail-closed wall commissioning, calibrated
// liters-to-duration conversion, and restart-safe weekly admission.  It never
// touches a relay directly.

enum class WetCommandOrigin : uint8_t {
    CLIMATE_VPD,
    EXPLICIT_IRRIGATION,
    AUTOMATIC_WEEKLY_WALL_FEED,
    MANUAL_FEED,
    PLANNER_FEED,
    LEGACY_SCHEDULE,
    RETRY_REPLAY,
    REPAIR,
};

enum class WetZone : uint8_t {
    NONE,
    CENTER_MIST,
    CENTER_DRIP,
    SOUTH_MIST,
    WEST_MIST,
    WALL_DRIP,
};

enum class WetChemistry : uint8_t { CLEAN, FERTILIZER };

enum class WetRelay : uint8_t {
    NONE,
    CENTER_MISTER,
    CENTER_DRIP,
    SOUTH_MISTER,
    WEST_MISTER,
    WALL_DRIP_CLEAN,
    WALL_DRIP_FERTILIZED,
};

struct WetTopologyPolicy {
    bool center_drip_enabled{false};
    bool south_irrigation_enabled{false};
    bool west_irrigation_enabled{false};
    bool wall_irrigation_enabled{false};
    bool wall_fertigation_commissioned{false};
};

struct WetRequest {
    WetCommandOrigin origin{WetCommandOrigin::LEGACY_SCHEDULE};
    WetZone requested_zone{WetZone::NONE};
    WetChemistry chemistry{WetChemistry::CLEAN};
};

struct WetResolution {
    bool admitted{false};
    WetRelay relay{WetRelay::NONE};
    const char* reason{"invalid_request"};
};

inline WetResolution resolve_wet_request(
    const WetRequest& request,
    const WetTopologyPolicy& policy) noexcept {
    // Climate is clean-water-only and has exactly one actuator.  A stale zone
    // hint is deliberately ignored so every VPD source converges on center.
    if (request.origin == WetCommandOrigin::CLIMATE_VPD) {
        if (request.chemistry != WetChemistry::CLEAN) {
            return {false, WetRelay::NONE, "climate_fertilizer_forbidden"};
        }
        return {true, WetRelay::CENTER_MISTER, "center_climate"};
    }

    // The center mister is reverse-exclusive: no irrigation, feed, manual,
    // legacy schedule, planner, repair, or replay origin may ever select it.
    if (request.requested_zone == WetZone::CENTER_MIST) {
        return {false, WetRelay::NONE, "center_mist_climate_only"};
    }

    // Fertilizer is accepted only from the exact-once weekly wall sequence.
    // Manual/planner/legacy/repair/replay requests cannot bypass that sequence.
    if (request.chemistry == WetChemistry::FERTILIZER) {
        if (request.origin != WetCommandOrigin::AUTOMATIC_WEEKLY_WALL_FEED) {
            return {false, WetRelay::NONE, "fertilizer_origin_forbidden"};
        }
        if (request.requested_zone != WetZone::WALL_DRIP) {
            return {false, WetRelay::NONE, "fertilizer_wall_only"};
        }
        if (!policy.wall_irrigation_enabled || !policy.wall_fertigation_commissioned) {
            return {false, WetRelay::NONE, "wall_not_commissioned"};
        }
        return {true, WetRelay::WALL_DRIP_FERTILIZED, "commissioned_wall_feed"};
    }

    if (request.origin != WetCommandOrigin::EXPLICIT_IRRIGATION
        && request.origin != WetCommandOrigin::AUTOMATIC_WEEKLY_WALL_FEED) {
        return {false, WetRelay::NONE, "clean_origin_forbidden"};
    }

    switch (request.requested_zone) {
        case WetZone::CENTER_DRIP:
            return policy.center_drip_enabled
                ? WetResolution{true, WetRelay::CENTER_DRIP, "explicit_center_drip"}
                : WetResolution{false, WetRelay::NONE, "center_drip_disabled"};
        case WetZone::SOUTH_MIST:
            return policy.south_irrigation_enabled
                ? WetResolution{true, WetRelay::SOUTH_MISTER, "explicit_south_irrigation"}
                : WetResolution{false, WetRelay::NONE, "south_irrigation_disabled"};
        case WetZone::WEST_MIST:
            return policy.west_irrigation_enabled
                ? WetResolution{true, WetRelay::WEST_MISTER, "explicit_west_irrigation"}
                : WetResolution{false, WetRelay::NONE, "west_irrigation_disabled"};
        case WetZone::WALL_DRIP:
            return policy.wall_irrigation_enabled
                ? WetResolution{true, WetRelay::WALL_DRIP_CLEAN, "wall_clean"}
                : WetResolution{false, WetRelay::NONE, "wall_irrigation_disabled"};
        case WetZone::NONE:
        case WetZone::CENTER_MIST:
        default:
            return {false, WetRelay::NONE, "invalid_clean_zone"};
    }
}

enum WallCommissioningEvidence : uint16_t {
    WALL_SOURCE_WATER_VERIFIED = 1u << 0,
    WALL_PRODUCT_ANALYSIS_VERIFIED = 1u << 1,
    WALL_INJECTOR_RATIO_VERIFIED = 1u << 2,
    WALL_FLOW_CALIBRATED = 1u << 3,
    WALL_DISTRIBUTION_VERIFIED = 1u << 4,
    WALL_LINE_FILL_MEASURED = 1u << 5,
    WALL_FLUSH_ENDPOINT_VERIFIED = 1u << 6,
    WALL_DELIVERED_CHEMISTRY_VERIFIED = 1u << 7,
    WALL_SEASONAL_MULTIPLIER_VERIFIED = 1u << 8,
};

static constexpr uint16_t WALL_COMMISSIONING_REQUIRED_MASK =
    WALL_SOURCE_WATER_VERIFIED
    | WALL_PRODUCT_ANALYSIS_VERIFIED
    | WALL_INJECTOR_RATIO_VERIFIED
    | WALL_FLOW_CALIBRATED
    | WALL_DISTRIBUTION_VERIFIED
    | WALL_LINE_FILL_MEASURED
    | WALL_FLUSH_ENDPOINT_VERIFIED
    | WALL_DELIVERED_CHEMISTRY_VERIFIED
    | WALL_SEASONAL_MULTIPLIER_VERIFIED;

struct WallFeedCommissioning {
    bool armed{false};
    uint32_t revision{0};
    uint16_t evidence_mask{0};
    uint32_t now_epoch_s{0};
    uint32_t commissioning_valid_until_epoch_s{0};
    float calibrated_flow_lpm{0.0f};
    uint32_t flow_valid_until_epoch_s{0};
    float prewet_liters{0.0f};
    float feed_liters{0.0f};
    float flush_liters{0.0f};
};

enum class WallFeedCommissioningStatus : uint8_t {
    READY,
    NOT_ARMED,
    MISSING_REVISION,
    INCOMPLETE_EVIDENCE,
    CLOCK_INVALID,
    COMMISSIONING_STALE,
    FLOW_MISSING_OR_INVALID,
    FLOW_STALE,
    VOLUME_MISSING_OR_INVALID,
    DURATION_OUT_OF_BOUNDS,
};

struct WallFeedDurations {
    bool valid{false};
    uint32_t prewet_ms{0};
    uint32_t feed_ms{0};
    uint32_t flush_ms{0};
    WallFeedCommissioningStatus status{WallFeedCommissioningStatus::NOT_ARMED};
};

inline bool liters_to_bounded_duration_ms(
    float liters,
    float flow_lpm,
    uint32_t min_ms,
    uint32_t max_ms,
    uint32_t& result_ms) noexcept {
    if (!std::isfinite(liters) || !std::isfinite(flow_lpm)
        || liters <= 0.0f || flow_lpm <= 0.0f) {
        return false;
    }
    const double duration_ms = std::ceil((double(liters) / double(flow_lpm)) * 60000.0);
    if (!std::isfinite(duration_ms)
        || duration_ms < double(min_ms)
        || duration_ms > double(max_ms)
        || duration_ms > double(UINT32_MAX)) {
        return false;
    }
    result_ms = uint32_t(duration_ms);
    return true;
}

inline WallFeedDurations validate_wall_feed_commissioning(
    const WallFeedCommissioning& commissioning) noexcept {
    WallFeedDurations result{};
    if (!commissioning.armed) {
        result.status = WallFeedCommissioningStatus::NOT_ARMED;
        return result;
    }
    if (commissioning.revision == 0) {
        result.status = WallFeedCommissioningStatus::MISSING_REVISION;
        return result;
    }
    if ((commissioning.evidence_mask & WALL_COMMISSIONING_REQUIRED_MASK)
        != WALL_COMMISSIONING_REQUIRED_MASK) {
        result.status = WallFeedCommissioningStatus::INCOMPLETE_EVIDENCE;
        return result;
    }
    if (commissioning.now_epoch_s == 0) {
        result.status = WallFeedCommissioningStatus::CLOCK_INVALID;
        return result;
    }
    if (commissioning.commissioning_valid_until_epoch_s <= commissioning.now_epoch_s) {
        result.status = WallFeedCommissioningStatus::COMMISSIONING_STALE;
        return result;
    }
    if (!std::isfinite(commissioning.calibrated_flow_lpm)
        || commissioning.calibrated_flow_lpm < 0.1f
        || commissioning.calibrated_flow_lpm > 100.0f) {
        result.status = WallFeedCommissioningStatus::FLOW_MISSING_OR_INVALID;
        return result;
    }
    if (commissioning.flow_valid_until_epoch_s <= commissioning.now_epoch_s) {
        result.status = WallFeedCommissioningStatus::FLOW_STALE;
        return result;
    }
    if (!std::isfinite(commissioning.prewet_liters)
        || !std::isfinite(commissioning.feed_liters)
        || !std::isfinite(commissioning.flush_liters)
        || commissioning.prewet_liters <= 0.0f
        || commissioning.feed_liters <= 0.0f
        || commissioning.flush_liters <= 0.0f
        || commissioning.prewet_liters > 200.0f
        || commissioning.feed_liters > 200.0f
        || commissioning.flush_liters > 400.0f) {
        result.status = WallFeedCommissioningStatus::VOLUME_MISSING_OR_INVALID;
        return result;
    }

    // The lower bound prevents sub-tick pulses; the upper bounds reject a bad
    // calibration instead of silently clamping and under-delivering a stage.
    const bool prewet_ok = liters_to_bounded_duration_ms(
        commissioning.prewet_liters, commissioning.calibrated_flow_lpm,
        5000u, 30u * 60u * 1000u, result.prewet_ms);
    const bool feed_ok = liters_to_bounded_duration_ms(
        commissioning.feed_liters, commissioning.calibrated_flow_lpm,
        5000u, 30u * 60u * 1000u, result.feed_ms);
    const bool flush_ok = liters_to_bounded_duration_ms(
        commissioning.flush_liters, commissioning.calibrated_flow_lpm,
        5000u, 60u * 60u * 1000u, result.flush_ms);
    if (!prewet_ok || !feed_ok || !flush_ok) {
        result.prewet_ms = result.feed_ms = result.flush_ms = 0;
        result.status = WallFeedCommissioningStatus::DURATION_OUT_OF_BOUNDS;
        return result;
    }

    result.valid = true;
    result.status = WallFeedCommissioningStatus::READY;
    return result;
}

enum class WallFeedStage : uint8_t {
    IDLE,
    PREWET,
    FEED,
    FLUSH,
    COMPLETE,
    CANCELLED,
};

struct WeeklyWallFeedState {
    WallFeedStage stage{WallFeedStage::IDLE};
    uint32_t claimed_solar_day{0};
    uint32_t last_terminal_solar_day{0};
    uint32_t commissioning_revision{0};
};

struct WeeklyWallEligibilityInput {
    bool clock_valid{false};
    bool leak_blocked{false};
    float solar_phase{0.0f};
    uint32_t solar_day{0};
    uint32_t minimum_interval_days{7};
};

inline bool weekly_wall_feed_eligible(
    const WeeklyWallEligibilityInput& input,
    const WeeklyWallFeedState& state,
    const WallFeedDurations& durations) noexcept {
    if (!input.clock_valid || input.leak_blocked || !durations.valid
        || input.solar_day == 0 || !std::isfinite(input.solar_phase)) {
        return false;
    }
    // Solar cadence: admission is in the sunrise-to-solar-noon half of the
    // device day.  If that window is missed, the next solar day remains due.
    if (input.solar_phase < 0.0f || input.solar_phase >= 1.0f) {
        return false;
    }
    if (state.stage == WallFeedStage::PREWET
        || state.stage == WallFeedStage::FEED
        || state.stage == WallFeedStage::FLUSH) {
        return false;
    }
    if (state.claimed_solar_day == input.solar_day) {
        return false;
    }
    if (state.last_terminal_solar_day != 0
        && (input.solar_day < state.last_terminal_solar_day
            || (input.solar_day - state.last_terminal_solar_day) < input.minimum_interval_days)) {
        return false;  // clock regression or still inside the weekly interval
    }
    return true;
}

inline WeeklyWallFeedState claim_weekly_wall_feed(
    const WeeklyWallEligibilityInput& input,
    const WeeklyWallFeedState& state,
    uint32_t commissioning_revision) noexcept {
    WeeklyWallFeedState claimed = state;
    claimed.stage = WallFeedStage::PREWET;
    claimed.claimed_solar_day = input.solar_day;
    claimed.commissioning_revision = commissioning_revision;
    return claimed;
}

inline WeeklyWallFeedState cancel_interrupted_wall_feed(
    const WeeklyWallFeedState& restored,
    uint32_t current_solar_day) noexcept {
    WeeklyWallFeedState cancelled = restored;
    if (restored.stage == WallFeedStage::PREWET
        || restored.stage == WallFeedStage::FEED
        || restored.stage == WallFeedStage::FLUSH) {
        cancelled.stage = WallFeedStage::CANCELLED;
        cancelled.last_terminal_solar_day = restored.claimed_solar_day != 0
            ? restored.claimed_solar_day
            : current_solar_day;
    }
    return cancelled;
}

struct WallFeedRelayIntent {
    bool wall_clean{false};
    bool wall_fertilized{false};
    bool fertilizer_master{false};
};

inline WallFeedRelayIntent wall_feed_relay_intent(WallFeedStage stage) noexcept {
    switch (stage) {
        case WallFeedStage::PREWET:
            return {true, false, false};
        case WallFeedStage::FEED:
            return {false, true, true};
        case WallFeedStage::FLUSH:
            // The transition into FLUSH is the fertilizer-off boundary.  Clean
            // wall water starts immediately; there is no global absorption hold.
            return {true, false, false};
        case WallFeedStage::IDLE:
        case WallFeedStage::COMPLETE:
        case WallFeedStage::CANCELLED:
        default:
            return {false, false, false};
    }
}

inline WeeklyWallFeedState advance_wall_feed_sequence(
    const WeeklyWallFeedState& state,
    bool stage_complete,
    bool cancel,
    uint32_t current_solar_day) noexcept {
    if (cancel) {
        return cancel_interrupted_wall_feed(state, current_solar_day);
    }
    if (!stage_complete) {
        return state;
    }
    WeeklyWallFeedState next = state;
    switch (state.stage) {
        case WallFeedStage::PREWET:
            next.stage = WallFeedStage::FEED;
            break;
        case WallFeedStage::FEED:
            next.stage = WallFeedStage::FLUSH;
            break;
        case WallFeedStage::FLUSH:
            next.stage = WallFeedStage::COMPLETE;
            next.last_terminal_solar_day = state.claimed_solar_day != 0
                ? state.claimed_solar_day
                : current_solar_day;
            break;
        case WallFeedStage::IDLE:
        case WallFeedStage::COMPLETE:
        case WallFeedStage::CANCELLED:
        default:
            break;
    }
    return next;
}
