"""Qualify diagnostic bookkeeping, not reproduction of the device watchdog cause."""

import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_trace_preserves_calls_and_discriminates_phase():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "stall_platform_stubs.h").write_text("""#pragma once
#include <cstdint>
#define DRAM_ATTR
#define RTC_NOINIT_ATTR
#define IRAM_ATTR
using TaskHandle_t=void*; using QueueHandle_t=void*; using sys_prot_t=unsigned;
using BaseType_t=int; using TickType_t=unsigned;
extern void* current_task; extern unsigned current_core, cycles;
inline TaskHandle_t xTaskGetCurrentTaskHandle(){return current_task;}
inline unsigned xPortGetCoreID(){return current_core;}
inline unsigned esp_cpu_get_cycle_count(){return ++cycles;}
inline unsigned esp_random(){return 0x12345678;}
""")
        source = ROOT / "firmware/diagnostics/api_stall_trace.cpp"
        (p / "main.cpp").write_text(f'''#define VERDIFY_STALL_TRACE_HOST
#define VERDIFY_STALL_SOURCE "{"a" * 64}"
#include "{source}"
#include <cassert>
void* current_task=(void*)0x1010; unsigned current_core=1, cycles=0;
unsigned real_take_calls=0, real_protect_calls=0, real_release_calls=0;
verdify_stall::Record waiting;
extern "C" BaseType_t __real_xQueueSemaphoreTake(QueueHandle_t q, TickType_t ticks){{
 assert(q==(void*)0x2020 && ticks==77); ++real_take_calls;
 waiting=verdify_stall::snapshot();
 verdify_stall::panic_capture(); assert(verdify_stall::retained.phase==2); return 9;
}}
extern "C" sys_prot_t __real_sys_arch_protect(){{
 ++real_protect_calls; assert(__wrap_xQueueSemaphoreTake((void*)0x2020,77)==9); return 6;
}}
extern "C" void __real_sys_arch_unprotect(sys_prot_t level){{assert(level==6);++real_release_calls;}}
int main(){{
 using namespace verdify_stall; boot(); assert(snapshot().phase==0);
 panic_capture(); assert(retained.phase==0);
 drain_begin(1024); assert(snapshot().phase==1 && snapshot().drain_bytes==1024);
 assert(__wrap_sys_arch_protect()==6);
 assert(waiting.phase==2 && waiting.protect_queue==0x2020 && waiting.self_reentry==0);
 assert(snapshot().phase==1 && snapshot().owner_task==0x1010);
 // Recognize same-task reentry by prior successful acquire, without pretending
 // this stub reproduces a real FreeRTOS deadlock or device cause.
 assert(__wrap_sys_arch_protect()==6);
 assert(waiting.phase==2 && waiting.self_reentry==1);
 assert(retained.phase==2 && retained.self_reentry==1 && retained.protect_queue==0x2020);
 panic_capture(); assert(retained.magic!=0 && retained.phase==1);
 assert(retained.boot_nonce==0x12345678 && retained.source[0]=='a' && retained.source[64]==0);
 assert(retained.owner_task==0x1010);
 __wrap_sys_arch_unprotect(6); assert(snapshot().owner_task==0);
 drain_end(); assert(snapshot().phase==0);
 assert(real_take_calls==2 && real_protect_calls==2 && real_release_calls==1);
 boot(); assert(saved_valid && saved.boot_nonce==0x12345678 && retained.magic!=0);
 assert(snapshot().phase==0); log_retained(); assert(retained.magic==0);
}}
''')
        compiler = os.environ.get("CXX") or shutil.which("c++")
        subprocess.run(
            [
                compiler,
                "-std=c++20",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(p),
                str(p / "main.cpp"),
                "-o",
                str(p / "trace"),
            ],
            check=True,
            timeout=60,
        )
        subprocess.run([str(p / "trace")], check=True, timeout=15)


def test_guarded_patch_unknown_context_rejected():
    spec = importlib.util.spec_from_file_location("trace_hook", ROOT / "firmware/build_api_stall_trace.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    import pytest

    with pytest.raises(ValueError, match="exact source context"):
        hook.transform("components/api/api_overflow_buffer.cpp", "unexpected upstream")
