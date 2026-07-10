#pragma once

#include <cstddef>
#include <cstdint>

// Recovery release floor (#428): healthy historical firmware held 38-44 KB
// free. Requiring >=32 KB current free and >=16 KB largest contiguous block
// across the settled 48-hour watch leaves meaningful headroom above the 3-8 KB
// chronic-failure floor without treating the boot allocation watermark as a
// steady-state sample. A 250 ms loop high-water is one quarter of the 1 s
// control cadence and twenty times below the 5 s Task-WDT timeout.
constexpr float GH_PRE_OTA_MIN_FREE_HEAP_KB = 32.0f;
constexpr float GH_PRE_OTA_MIN_LARGEST_BLOCK_KB = 16.0f;
constexpr uint32_t GH_CONTROL_LOOP_WARN_US = 250000u;

inline uint32_t gh_control_loop_last_us_value = 0;
inline uint32_t gh_control_loop_max_us_value = 0;
inline uint32_t gh_control_loop_overrun_count_value = 0;

inline void gh_record_control_loop_duration_us(uint32_t duration_us) {
    gh_control_loop_last_us_value = duration_us;
    if (duration_us > gh_control_loop_max_us_value) gh_control_loop_max_us_value = duration_us;
    if (duration_us > GH_CONTROL_LOOP_WARN_US) gh_control_loop_overrun_count_value++;
}

inline uint32_t gh_control_loop_last_us() { return gh_control_loop_last_us_value; }

inline uint32_t gh_take_control_loop_max_us() {
    const uint32_t value = gh_control_loop_max_us_value;
    gh_control_loop_max_us_value = 0;
    return value;
}

inline uint32_t gh_take_control_loop_overrun_count() {
    const uint32_t value = gh_control_loop_overrun_count_value;
    gh_control_loop_overrun_count_value = 0;
    return value;
}

inline bool gh_pre_ota_heap_floor_met(float free_kb, float largest_block_kb) {
    return free_kb >= GH_PRE_OTA_MIN_FREE_HEAP_KB
        && largest_block_kb >= GH_PRE_OTA_MIN_LARGEST_BLOCK_KB;
}

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#include "esp_system.h"

inline float gh_free_heap_kb() {
    return esp_get_free_heap_size() / 1024.0f;
}

inline float gh_min_free_heap_kb() {
    return esp_get_minimum_free_heap_size() / 1024.0f;
}

inline float gh_largest_free_heap_block_kb() {
    return heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) / 1024.0f;
}
#else
inline float gh_free_heap_kb() { return 0.0f; }
inline float gh_min_free_heap_kb() { return 0.0f; }
inline float gh_largest_free_heap_block_kb() { return 0.0f; }
#endif
