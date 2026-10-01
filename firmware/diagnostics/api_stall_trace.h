#pragma once
#include <cstdint>
namespace verdify_stall {
// Diagnostic observations, never used for scheduling/control or mutex decisions.
struct Record {
  uint32_t magic, boot_nonce, captured_cycle;
  char source[65];
  uint32_t drain_task, drain_core, drain_cycle, drain_bytes;
  uint32_t phase, protect_queue, protect_wait_cycle;
  uint32_t owner_task, owner_core, owner_return_pc;
  uint32_t self_reentry, protect_calls;
};
void boot();
void log_retained();
}  // namespace verdify_stall
