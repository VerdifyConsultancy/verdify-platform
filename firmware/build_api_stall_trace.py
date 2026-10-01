"""Isolated diagnostic build hook; patches generated project sources only.

Guarded ESPHome2026.6.5 target hashes. Never modifies toolchain/vendor packages.
Diagnostic, not a firmware reliability correction. Original APIs/queue semantics
are retained, but instrumentation inevitably adds execution overhead.
"""

import hashlib
import subprocess
from pathlib import Path

TARGETS = {
    "components/api/api_overflow_buffer.cpp": "370db5e67ad5c4145e6f1f7d93558a1502cb9680064f4cf28bbdae511d289003",
    "components/esp32/crash_handler.cpp": "b028e4a22900e266621a2087a129daf045242559214453c893a66f0b53947b82",
}


VENDOR_HASHES = {
    "components/lwip/port/freertos/sys_arch.c": "304978233ec040c5f0e95e1a08209c038b5e03695638e1640301fa1f07c307b2",
    "components/freertos/FreeRTOS-Kernel/queue.c": "6794db86ab086372ea2789d043d83e0b1efd0534a0c0deb5d28cffb342feaa0e",
    "components/freertos/FreeRTOS-Kernel/tasks.c": "193c3d8379acc847097632da8e6d4dd2ee0bf8e069d78979d68dd26b653d48df",
}


def verify_vendor(framework):
    for name, expected in VENDOR_HASHES.items():
        if hashlib.sha256((framework / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Diagnostic selected ESP-IDF5.5.4 source mismatch: " + name)


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Diagnostic exact source context mismatch")
    return text.replace(old, new, 1)


def transform(name, text):
    text = '#include "esphome/core/api_stall_trace.h"\n' + text
    if name.endswith("api_overflow_buffer.cpp"):
        return once(
            text,
            "    ssize_t sent = socket->write(front->current_data(), front->remaining());",
            "    verdify_stall::drain_begin(front->remaining());\n"
            "    ssize_t sent = socket->write(front->current_data(), front->remaining());\n"
            "    verdify_stall::drain_end();",
        )
    text = once(
        text, "void crash_handler_read_and_clear() {", "void crash_handler_read_and_clear() {\n  verdify_stall::boot();"
    )
    text = once(
        text,
        '  ESP_LOGE(TAG, "*** CRASH DETECTED ON PREVIOUS BOOT ***");',
        '  verdify_stall::log_retained();\n  ESP_LOGE(TAG, "*** CRASH DETECTED ON PREVIOUS BOOT ***");',
    )
    return once(
        text,
        "void IRAM_ATTR __wrap_esp_panic_handler(panic_info_t *info) {",
        "void IRAM_ATTR __wrap_esp_panic_handler(panic_info_t *info) {\n  verdify_stall::panic_capture();",
    )


def original_bytes(name, current):
    original = current.decode().split("\n", 1)[1]
    if name.endswith("api_overflow_buffer.cpp"):
        original = once(original, "    verdify_stall::drain_begin(front->remaining());\n", "")
        original = once(original, "\n    verdify_stall::drain_end();", "")
    else:
        for extra in [
            "\n  verdify_stall::boot();",
            "  verdify_stall::log_retained();\n",
            "\n  verdify_stall::panic_capture();",
        ]:
            original = once(original, extra, "")
    if hashlib.sha256(original.encode()).hexdigest() != TARGETS[name] or transform(name, original).encode() != current:
        raise ValueError("Diagnostic patched-source hash mismatch")
    return original.encode()


def reset(project):
    prepared = {}
    for name in TARGETS:
        path = project / "src/esphome" / name
        current = path.read_bytes()
        if current.startswith(b'#include "esphome/core/api_stall_trace.h"\n'):
            prepared[path] = original_bytes(name, current)
    for path, data in prepared.items():
        path.write_bytes(data)


def apply(project, firmware):
    if '#define ESPHOME_VERSION "2026.6.5"' not in (project / "src/esphome/core/version.h").read_text():
        raise ValueError("Diagnostic selected ESPHome version mismatch")
    prepared = {}
    for name, expected in TARGETS.items():
        path = project / "src/esphome" / name
        current = path.read_bytes()
        # Idempotence requires exact transformed bytes, not merely a marker.
        if current.startswith(b'#include "esphome/core/api_stall_trace.h"\n'):
            original_bytes(name, current)
            continue
        if hashlib.sha256(current).hexdigest() != expected:
            raise ValueError("Diagnostic upstream hash mismatch: " + name)
        prepared[path] = transform(name, current.decode()).encode()
    source_files = [
        "diagnostics/api_stall_trace.cpp",
        "diagnostics/api_stall_trace.h",
        "build_api_stall_trace.py",
        "build_api_stall_reset.py",
        "greenhouse.yaml",
    ]
    head = subprocess.check_output(["git", "-C", str(firmware), "rev-parse", "HEAD"]).strip()
    identity = hashlib.sha256(head + b"".join((firmware / x).read_bytes() for x in source_files)).hexdigest()
    for path, data in prepared.items():
        path.write_bytes(data)
    for ext in ["cpp", "h"]:
        (project / ("src/esphome/core/api_stall_trace." + ext)).write_bytes(
            (firmware / ("diagnostics/api_stall_trace." + ext)).read_bytes()
        )
    return identity


if "Import" in globals():
    Import("env")  # noqa: F821 — PlatformIO/SCons supplied
    project = Path(env.subst("$PROJECT_DIR"))  # noqa: F821
    verify_vendor(Path(env.PioPlatform().get_package_dir("framework-espidf")))  # noqa: F821
    identity = apply(project, project.resolve().parents[2])
    env.Append(CPPDEFINES=[("VERDIFY_STALL_SOURCE", '\\"' + identity + '\\"')])  # noqa: F821
    env.Append(  # noqa: F821 — PlatformIO/SCons supplied
        LINKFLAGS=["-Wl,--wrap=sys_arch_protect", "-Wl,--wrap=sys_arch_unprotect", "-Wl,--wrap=xQueueSemaphoreTake"]
    )  # noqa: F821
    print("Diagnostic stall tracing source token " + identity)
