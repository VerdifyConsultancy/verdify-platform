#pragma once

#include <cstdint>

#ifdef USE_ESP32
#include <esp_sntp.h>
#endif

// ESPHome also emits on_time_sync for a valid retained RTC at startup.
// Only ESP-IDF's completed network synchronization is acquisition evidence.
inline bool record_sntp_acquisition(bool completed, bool rtc_valid, uint32_t now_ms,
                                    bool &acquired, uint32_t &last_sync_ms) {
    if (!completed || !rtc_valid) return false;
    acquired = true;
    last_sync_ms = now_ms;
    return true;
}

inline bool sntp_acquisition_valid(bool acquired, bool rtc_valid, bool failed) {
    return acquired && rtc_valid && !failed;
}
