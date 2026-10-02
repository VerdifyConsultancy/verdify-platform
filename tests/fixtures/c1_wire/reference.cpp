#include <cstdio>
#include <cstdint>
#include <cstring>
#include "target2143_reference.h"

uint32_t bits(float value) {
    uint32_t result;
    memcpy(&result, &value, 4);
    return result;
}

int main() {
    for (int doy : {1, 59, 60, 61, 79, 80, 171, 172, 265, 266, 274, 275, 276, 355, 365, 366}) {
        for (int offset : {-420, -360}) {
            auto times = compute_solar_times(doy, GH_LATITUDE_DEG, GH_LONGITUDE_DEG, offset);
            for (int minute = 0; minute < 1440; minute++) {
                float phase = solar_phase(minute, times);
                printf("%d %d %d %d %d %d %08x %08x %08x\n", doy, offset, minute,
                    times.sunrise_min, times.solar_noon_min, times.sunset_min, bits(phase),
                    bits(band_value_at_phase(BandAnchors{62.5f, 73.8f, 72.0f, 60.7f}, phase)),
                    bits(band_value_at_phase(BandAnchors{.76f, .86f, .84f, .74f}, phase)));
            }
        }
    }
}
