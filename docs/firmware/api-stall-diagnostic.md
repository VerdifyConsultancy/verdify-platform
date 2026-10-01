# API socket / lwIP protection diagnostic candidate

This is an uncommitted diagnostic candidate. It is not a reliability correction
and has not been flashed. The qualified254 firmware archive stays immutable.

## What it distinguishes

The exact overflow drain marks phase1 immediately before synchronous socket write
and clears it after return. A wrapped `sys_arch_protect` reached by that same task
marks phase2 before its original acquire, then returns to phase1 after success.
The semaphore wrapper observes the actual queue handle passed during that phase.
Successful global protect acquires record task/core/caller; releases clear only
the matching task. Same-task reacquisition is explicitly recorded.

A retained panic record therefore separates:

- phase0: no observed overflow socket write in flight;
- phase1: socket write in flight, outside the instrumented protection acquire;
- phase2: socket write in flight and protection acquire has not returned;
- phase2 + self_reentry1: the waiting task also matches a prior observed successful
  protection owner. This is stronger evidence than a stack location alone.

`owner_task` is a shadow observation, not an authoritative RTOS holder query.
Acquisition/release handoff can leave a short unknown/stale interval. Task handles
and queue handles are process-local coordinates, never persistent identities.
The trace does not determine which task owns an internal FreeRTOS queue spinlock.

## Safe readback decision

Selected ESP-IDF5.5.4 `xSemaphoreGetMutexHolder` enters the queue's own critical
section. Adding it to this path could itself wait on the suspect lock. The
`FromISR` primitive reads without that lock, but is documented for ISR context,
can be stale, and its selected implementation is flash-resident. Neither is used
from tasks or panic. Lock wrappers record scalar observations without new locks.

## Custody and recovery

The normal boot copies a source closure token and random boot nonce into DRAM.
Panic capture uses a bounded volatile byte copy into a separate RTC record,
without allocation, logging, task/queue getter, timer or lock. Magic is published
last. On the next boot it is published by the existing crash logger after an API
client connects. It remains in RTC until that logger runs, including intervening
ordinary reboot/OTA. Its token/nonce identifies the diagnostic source and boot;
it is not an OTA binary digest or UTC boot timestamp. Existing upstream crash
storage and decoding remain independent.

The token binds HEAD and exact diagnostic hook/header/source/config bytes. Keep
its compile receipt with the exact ELF/OTA hashes; decode only that ELF. The
version override is a diagnostic version and is not a production release.

## Behavior and limitations

No queue capacity, loop scheduling, yield, allocator, TCP timeout, semaphore wait,
return value, command, entity or physical control semantics are changed. Linker
wrappers inevitably add instructions, including within a held protector, and
must be treated as a diagnostic perturbation. No network/client test is included.
Native tests qualify phase/record/wrapper bookkeeping and original call/return
preservation; they do not reproduce dual-core device lock scheduling or prove
that a observed TaskWDT was caused by a mutex.

The generated-source guards require exact selected ESPHome2026.6.5 hashes. A
reset hook removes only our exact known insertions before the existing API patch
hook runs again. No global toolchain/vendor file is modified. Unknown or partial
source fails closed. A real compile and linked-call readback are required before
any decision to use this diagnostic image. Deployment is outside this patch.

## RTOS review

The selected task getter masks only local interrupts to read `pxCurrentTCBs`;
it acquires no kernel or queue lock. This adds a brief interrupt-masked read in
normal wrapper context, never in panic. Core/caller/owner fields can span a
handoff and are advisory; phase2 and same-task reentry are the principal signals.
Selected vendor `sys_arch.c`, `queue.c` and `tasks.c` hashes are checked read-only
at build time. Unknown vendor source is rejected; no toolchain files are patched.

The ordinary firmware replay covers controller decision code, not RTOS lock
scheduling. Zero replay divergence does not certify the diagnostic's runtime
perturbation or prove that it will resolve a TaskWDT.
