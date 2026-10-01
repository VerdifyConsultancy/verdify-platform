#include "api_stall_trace.h"
#include <cstddef>
#ifdef VERDIFY_STALL_TRACE_HOST
#include "stall_platform_stubs.h"
#else
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "lwip/sys.h"
#include "esphome/core/log.h"
#endif
#ifndef VERDIFY_STALL_SOURCE
#error "Diagnostic source identity must be supplied by the guarded build hook"
#endif
static_assert(__atomic_always_lock_free(sizeof(uint32_t), nullptr));
namespace verdify_stall {
static constexpr uint32_t MAGIC = 0x56535431;
static DRAM_ATTR Record live{};
struct RetainedEnvelope {
  uint8_t legacy_ee1_initialized_prefix[32];
  Record record;
};
static_assert(offsetof(RetainedEnvelope, record) == 32);
static_assert(sizeof(Record) == 128);
static_assert(sizeof(RetainedEnvelope) == 160);
// The old ee1 image loads initialized RTC bytes at50000000..1f. Let those
// writes hit expendable padding, preserving the complete diagnostic payload.
static RTC_NOINIT_ATTR RetainedEnvelope retained;
static DRAM_ATTR Record saved{};
static DRAM_ATTR bool saved_valid = false;
static DRAM_ATTR uint32_t owner = 0, owner_core = 0, owner_pc = 0;
static uint32_t task() { return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(xTaskGetCurrentTaskHandle())); }
static inline uint32_t load(const uint32_t *p) { return __atomic_load_n(p, __ATOMIC_RELAXED); }
static inline void store(uint32_t *p, uint32_t value) { __atomic_store_n(p, value, __ATOMIC_RELAXED); }
void boot() {
  saved_valid = retained.record.magic == MAGIC;
  if (saved_valid) { saved = retained.record; saved.source[64] = 0; }
  // Keep RTC until existing crash logger is actually invoked after connection.
  // A normal reboot/OTA before first collection must not discard the record.
  live = {};
  live.boot_nonce = esp_random();
  const char identity[] = VERDIFY_STALL_SOURCE;
  static_assert(sizeof(identity) == sizeof(live.source));
  for (unsigned i = 0; i < sizeof(identity); ++i) live.source[i] = identity[i];
}
void drain_begin(uint32_t bytes) {
  store(&live.drain_task, task()); live.drain_core = xPortGetCoreID();
  live.drain_cycle = esp_cpu_get_cycle_count(); live.drain_bytes = bytes;
  live.protect_queue = 0; live.protect_wait_cycle = 0; live.self_reentry = 0;
  store(&live.phase, 1);  // Inside exact synchronous socket write.
}
void drain_end() { store(&live.phase, 0); }
Record snapshot() {
  Record result = live;
  result.owner_task = load(&owner); result.owner_core = load(&owner_core); result.owner_return_pc = load(&owner_pc);
  return result;
}
void IRAM_ATTR panic_capture() {
  // No allocator, logger, RTOS getter, timer, queue introspection or mutex here.
  // All source identity bytes were copied to DRAM at normal boot.
  retained.record.magic = 0;
  const volatile uint8_t *src = reinterpret_cast<const volatile uint8_t *>(&live);
  volatile uint8_t *dst = reinterpret_cast<volatile uint8_t *>(&retained.record);
  for (unsigned i = 0; i < sizeof(Record); ++i) dst[i] = src[i];
  retained.record.owner_task = load(&owner); retained.record.owner_core = load(&owner_core); retained.record.owner_return_pc = load(&owner_pc);
  retained.record.captured_cycle = esp_cpu_get_cycle_count();
  __atomic_thread_fence(__ATOMIC_RELEASE);
  retained.record.magic = MAGIC;  // Publish last; no partial record is accepted at boot.
}
void log_retained() {
  if (!saved_valid) return;
  // No native acknowledgement exists: logging attempts never consume RTC.
  // Source token + boot nonce allow consumers to recognize repeated records.
#ifndef VERDIFY_STALL_TRACE_HOST
  ESP_LOGE("verdify.stall", "diagnostic source=%s boot_nonce=%08x phase=%u drain_task=%08x core=%u cycle=%u bytes=%u panic_cycle=%u", saved.source, saved.boot_nonce, saved.phase, saved.drain_task, saved.drain_core, saved.drain_cycle, saved.drain_bytes, saved.captured_cycle);
  ESP_LOGE("verdify.stall", "protect queue=%08x wait_cycle=%u owner_task=%08x owner_core=%u owner_pc=%08x self_reentry=%u calls=%u (owner advisory around handoff)", saved.protect_queue, saved.protect_wait_cycle, saved.owner_task, saved.owner_core, saved.owner_return_pc, saved.self_reentry, saved.protect_calls);
#endif
}
}  // namespace verdify_stall
extern "C" sys_prot_t __real_sys_arch_protect();
extern "C" void __real_sys_arch_unprotect(sys_prot_t);
extern "C" BaseType_t __real_xQueueSemaphoreTake(QueueHandle_t, TickType_t);
extern "C" sys_prot_t __wrap_sys_arch_protect() {
  using namespace verdify_stall;
  uint32_t current = task();
  bool draining = load(&live.phase) == 1 && load(&live.drain_task) == current;
  if (draining) {
    live.protect_wait_cycle = esp_cpu_get_cycle_count();
    live.self_reentry = load(&owner) == current;
    ++live.protect_calls;
    store(&live.phase, 2);  // Actual protector acquire is about to begin.
  }
  sys_prot_t result = __real_sys_arch_protect();
  store(&owner_core, xPortGetCoreID());
  store(&owner_pc, static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__builtin_return_address(0))));
  store(&owner, current);
  if (draining) store(&live.phase, 1);
  return result;
}
extern "C" void __wrap_sys_arch_unprotect(sys_prot_t level) {
  using namespace verdify_stall;
  uint32_t current = task();
  __real_sys_arch_unprotect(level);
  // Another task may already acquire. Never clear a different task's observation.
  __atomic_compare_exchange_n(&owner, &current, 0, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED);
}
extern "C" BaseType_t __wrap_xQueueSemaphoreTake(QueueHandle_t queue, TickType_t ticks) {
  using namespace verdify_stall;
  if (load(&live.phase) == 2 && load(&live.drain_task) == task())
    live.protect_queue = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(queue));
  return __real_xQueueSemaphoreTake(queue, ticks);  // Original wait/return unchanged.
}
