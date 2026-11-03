#pragma once

#include <cmath>
#include <cstdint>

// Pure wet-request routing used by both ESPHome and native tests: the climate
// path's command-origin resolution (CLIMATE_VPD -> center mister). It never
// touches a relay directly.
//
// The weekly wall-feed commissioning gate, its journal and its state machine
// went with the job machine when the irrigation engine replaced it (#1160;
// firmware/lib/irrigation_program.h). This resolver and its Wet* types stay
// until #1161 removes center-only routing, and the file with them.

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
