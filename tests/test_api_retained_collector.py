"""Collector custody checks; not hardware reset retention or cause proof."""

import importlib.util
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_collector_preserves_exact_record_and_rejects_unbound_payload():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        cpp = ROOT / "firmware/diagnostics/api_stall_trace.cpp"
        (p / "main.cpp").write_text(f'''#define VERDIFY_STALL_TRACE_HOST
#include "{cpp}"
#include <cassert>
int main(){{
 using namespace verdify_stall;
 Record original{{}}; original.magic=MAGIC; original.boot_nonce=0x12345678;
 memcpy(original.source,EXPECTED_SOURCE,sizeof(EXPECTED_SOURCE));
 original.phase=2; original.drain_core=1; original.owner_core=0;
 retained.record=original;
 auto* prefix=reinterpret_cast<unsigned char*>(&retained);
 for(unsigned i=0;i<32;++i)prefix[i]=i+1;
 boot(); assert(saved_valid); assert(memcmp(&saved,&original,128)==0);
 log_retained(); log_retained(); assert(memcmp(&retained.record,&original,128)==0);
 retained.record.magic=0;boot();assert(!saved_valid);
 retained.record=original;retained.record.source[0]='x';boot();assert(!saved_valid);
 retained.record=original;retained.record.source[64]='x';boot();assert(!saved_valid);
 retained.record=original;retained.record.boot_nonce=0;boot();assert(!saved_valid);
 retained.record=original;retained.record.phase=3;boot();assert(!saved_valid);
 retained.record=original;retained.record.drain_core=2;boot();assert(!saved_valid);
}}''')
        subprocess.run(
            ["c++", "-std=c++20", "-Wall", "-Wextra", "-Werror", str(p / "main.cpp"), "-o", str(p / "test")], check=True
        )
        subprocess.run([str(p / "test")], check=True)


def test_collector_generated_transform_only_bootread_and_subscription():
    spec = importlib.util.spec_from_file_location("collector_hook", ROOT / "firmware/build_api_stall_trace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.TARGETS) == {"components/api/api_connection.h", "components/esp32/crash_handler.cpp"}
    for name, data in [
        ("components/api/api_connection.h", "    this->flags_.log_subscription = msg.level;"),
        (
            "components/esp32/crash_handler.cpp",
            "void crash_handler_read_and_clear() {\n}\nvoid IRAM_ATTR __wrap_esp_panic_handler(panic_info_t *info) {\n}",
        ),
    ]:
        new = module.transform(name, data)
        assert "panic_capture" not in new and "drain_begin" not in new
        assert data in new.replace("\n  verdify_stall::boot();", "").replace(
            "\n    if (msg.level >= enums::LOG_LEVEL_ERROR) verdify_stall::log_retained();", ""
        )
