
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <string>
#if __has_include("probe_validity.h")
#include "probe_validity.h"
#endif
using std::isnan;
#define id(x) x
struct Sensor { float state = 0.0f; };
uint32_t clock_ms;
uint32_t millis() { return clock_ms; }
uint32_t last_case_ms, last_north_ms, last_west_ms, last_south_ms, last_east_ms;
Sensor case_temperature, case_humidity;
Sensor north_wall_temperature, north_wall_humidity, vpd_north;
Sensor west_wall_temperature, west_wall_humidity, vpd_west;
Sensor south_wall_temperature, south_wall_humidity, vpd_south;
Sensor east_wall_temperature, east_wall_humidity, vpd_east;
float active_count() {
const uint32_t now = millis();
const uint32_t STALE = 300000;
auto ok = [&](uint32_t ts, float temperature, float humidity, float vpd) {
  return probe_sample_fresh(now, ts, STALE) &&
         probe_triple_finite(temperature, humidity, vpd);
};
int n = 0;
if(ok(id(last_north_ms), id(north_wall_temperature).state, id(north_wall_humidity).state, id(vpd_north).state)) n++;
if(ok(id(last_west_ms), id(west_wall_temperature).state, id(west_wall_humidity).state, id(vpd_west).state)) n++;
if(ok(id(last_south_ms), id(south_wall_temperature).state, id(south_wall_humidity).state, id(vpd_south).state)) n++;
if(ok(id(last_east_ms), id(east_wall_temperature).state, id(east_wall_humidity).state, id(vpd_east).state)) n++;
return (float)n;
}
std::string probe_status() {
uint32_t now = millis();
const uint32_t STALE_MS = 120000;
char result[96];
size_t n = 0;
result[0] = '\0';
struct { const char* name; uint32_t last; bool values_finite; } probes[] = {
  {"Case", id(last_case_ms), probe_pair_finite(id(case_temperature).state, id(case_humidity).state)},
  {"North", id(last_north_ms), probe_triple_finite(id(north_wall_temperature).state, id(north_wall_humidity).state, id(vpd_north).state)},
  {"West", id(last_west_ms), probe_triple_finite(id(west_wall_temperature).state, id(west_wall_humidity).state, id(vpd_west).state)},
  {"South", id(last_south_ms), probe_triple_finite(id(south_wall_temperature).state, id(south_wall_humidity).state, id(vpd_south).state)},
  {"East", id(last_east_ms), probe_triple_finite(id(east_wall_temperature).state, id(east_wall_humidity).state, id(vpd_east).state)}
};
bool all_ok = true;
auto append = [&](const char* s) {
  while (*s != '\0' && n + 1 < sizeof(result)) {
    result[n++] = *s++;
  }
  result[n] = '\0';
};
for(auto &p : probes){
  const bool fresh = probe_sample_fresh(now, p.last, STALE_MS);
  if(!fresh || !p.values_finite){
    if(!all_ok) append(", ");
    append(p.name);
    append(fresh ? " INVALID" : " STALE");
    all_ok = false;
  }
}
return all_ok ? std::string("OK") : std::string(result);
}
float average_temperature() {
const uint32_t now = millis();
const uint32_t STALE = 300000; // 5 min, matches watchdog
auto ok = [&](uint32_t ts){ return ts > 0 && (now - ts) < STALE; };
float s = 0; int n = 0;
if(ok(id(last_north_ms)) && !isnan(id(north_wall_temperature).state)){ s += id(north_wall_temperature).state; n++; }
if(ok(id(last_west_ms))  && !isnan(id(west_wall_temperature).state)) { s += id(west_wall_temperature).state;  n++; }
if(ok(id(last_south_ms)) && !isnan(id(south_wall_temperature).state)){ s += id(south_wall_temperature).state; n++; }
if(ok(id(last_east_ms))  && !isnan(id(east_wall_temperature).state)) { s += id(east_wall_temperature).state;  n++; }
return n ? s/n : NAN;

}

float average_humidity() {
const uint32_t now = millis();
const uint32_t STALE = 300000;
auto ok = [&](uint32_t ts){ return ts > 0 && (now - ts) < STALE; };
float s = 0; int n = 0;
if(ok(id(last_north_ms)) && !isnan(id(north_wall_humidity).state)){ s += id(north_wall_humidity).state; n++; }
if(ok(id(last_west_ms))  && !isnan(id(west_wall_humidity).state)) { s += id(west_wall_humidity).state;  n++; }
if(ok(id(last_south_ms)) && !isnan(id(south_wall_humidity).state)){ s += id(south_wall_humidity).state; n++; }
if(ok(id(last_east_ms))  && !isnan(id(east_wall_humidity).state)) { s += id(east_wall_humidity).state;  n++; }
return n ? s/n : NAN;

}

float average_vpd_kpa() {
const uint32_t now = millis();
const uint32_t STALE = 300000;
auto ok = [&](uint32_t ts){ return ts > 0 && (now - ts) < STALE; };
float s = 0; int n = 0;
if(ok(id(last_north_ms)) && !isnan(id(vpd_north).state)){ s += id(vpd_north).state; n++; }
if(ok(id(last_west_ms))  && !isnan(id(vpd_west).state)) { s += id(vpd_west).state;  n++; }
if(ok(id(last_south_ms)) && !isnan(id(vpd_south).state)){ s += id(vpd_south).state; n++; }
if(ok(id(last_east_ms))  && !isnan(id(vpd_east).state)) { s += id(vpd_east).state;  n++; }
return n ? s/n : NAN;

}

int main(int argc, char** argv) {
  if (argc != 22 && argc != 23) return 64;
  clock_ms = uint32_t(std::strtoul(argv[1], nullptr, 10));
  struct Fields { uint32_t* last; Sensor* temp; Sensor* rh; Sensor* vpd; } fields[] = {
    {&last_case_ms, &case_temperature, &case_humidity, nullptr},
    {&last_north_ms, &north_wall_temperature, &north_wall_humidity, &vpd_north},
    {&last_west_ms, &west_wall_temperature, &west_wall_humidity, &vpd_west},
    {&last_south_ms, &south_wall_temperature, &south_wall_humidity, &vpd_south},
    {&last_east_ms, &east_wall_temperature, &east_wall_humidity, &vpd_east}
  };
  for (int i = 0; i < 5; ++i) {
    const int pos = 2 + i * 4;
    *fields[i].last = uint32_t(std::strtoul(argv[pos], nullptr, 10));
    fields[i].temp->state = std::strtof(argv[pos + 1], nullptr);
    fields[i].rh->state = std::strtof(argv[pos + 2], nullptr);
    if (fields[i].vpd) fields[i].vpd->state = std::strtof(argv[pos + 3], nullptr);
  }
  if (argc == 23) {
    if (std::string(argv[22]) != "averages") return 64;
    std::cout << std::setprecision(9) << average_temperature() << "|"
              << average_humidity() << "|" << average_vpd_kpa() << "\n";
  } else {
    std::cout << int(active_count()) << "|" << probe_status() << "\n";
  }
}
