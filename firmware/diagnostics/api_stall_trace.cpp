#include "api_stall_trace.h"
#include <cstddef>
#include <cstring>
#ifdef VERDIFY_STALL_TRACE_HOST
#define RTC_NOINIT_ATTR
#define DRAM_ATTR
#else
#include "esp_attr.h"
#include "esphome/core/log.h"
#endif
namespace verdify_stall {
static constexpr uint32_t MAGIC = 0x56535431;
static constexpr char EXPECTED_SOURCE[] = "44884f32ef335759f7481daed6cece302cc22a10bc53f976d1a2de22116383a1";
struct RetainedEnvelope { uint8_t legacy_ee1_initialized_prefix[32]; Record record; };
static_assert(offsetof(RetainedEnvelope, record) == 32);
static_assert(sizeof(Record) == 128);
static_assert(sizeof(RetainedEnvelope) == 160);
static RTC_NOINIT_ATTR RetainedEnvelope retained;
static DRAM_ATTR Record saved{};
static DRAM_ATTR bool saved_valid = false;
void boot() {
  // Read only. Never initialize, repair, synthesize, consume or overwrite RTC.
  saved = retained.record;
  saved_valid = saved.magic == MAGIC && saved.boot_nonce != 0 &&
      std::memcmp(saved.source, EXPECTED_SOURCE, sizeof(EXPECTED_SOURCE)) == 0 &&
      saved.phase <= 2 && saved.drain_core <= 1 && saved.owner_core <= 1 && saved.self_reentry <= 1;
}
void log_retained() {
#ifndef VERDIFY_STALL_TRACE_HOST
  if (!saved_valid) {
    ESP_LOGE("verdify.collector", "No validated retained6ba record; collection unavailable (no cause inference)");
    return;
  }
  ESP_LOGE("verdify.stall", "diagnostic source=%s boot_nonce=%08x phase=%u drain_task=%08x core=%u cycle=%u bytes=%u panic_cycle=%u", saved.source, saved.boot_nonce, saved.phase, saved.drain_task, saved.drain_core, saved.drain_cycle, saved.drain_bytes, saved.captured_cycle);
  ESP_LOGE("verdify.stall", "protect queue=%08x wait_cycle=%u owner_task=%08x owner_core=%u owner_pc=%08x self_reentry=%u calls=%u (owner advisory around handoff)", saved.protect_queue, saved.protect_wait_cycle, saved.owner_task, saved.owner_core, saved.owner_return_pc, saved.self_reentry, saved.protect_calls);
#endif
}
}  // namespace verdify_stall
