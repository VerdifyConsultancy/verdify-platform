"""Native fault/backpressure qualification for the actual firmware POD buffer."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("batch_patch", ROOT / "firmware/build_api_batch_patch.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


def test_native_deferred_batch_faults_and_full_bursts(tmp_path):
    source = (
        r"""
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <type_traits>
#include <utility>
#include <vector>
static bool fail_next = false;
static size_t calls = 0, requested = 0;
void *fault_realloc(void *p, size_t bytes) {
  ++calls; requested = bytes;
  if (fail_next) { fail_next = false; return nullptr; }
  return ::realloc(p, bytes);
}
struct EntityBase { int key; };
struct EventResponse { static constexpr uint8_t MESSAGE_TYPE = 99; };
#define USE_EVENT
#define realloc fault_realloc
struct Owner {
"""
        + PATCH.replacement_batch()
        + r"""
};
#undef realloc
int main() {
  EntityBase entities[620];
  for (int i=0;i<620;++i) entities[i].key=i;
  using Batch = Owner::DeferredBatch;
  // A full registered-state burst is accepted without an arbitrary cap, even
  // while transport is blocked. Once writable, drain exact FIFO in34slices.
  Batch burst;
  for (int i=0;i<620;++i) assert(burst.add_item(&entities[i], 1, 24));
  assert(burst.size()==620);
  auto *pending=burst.items;
  for (int i=0;i<620;++i) assert(burst.add_item(&entities[i], 1, 24));
  assert(burst.size()==620 && burst.items==pending);
  int expected=0;
  while (!burst.empty()) {
    size_t n=burst.size()<34 ? burst.size() : 34;
    for (size_t i=0;i<n;++i) assert(burst[i].entity->key==expected++);
    burst.remove_front(n);
  }
  assert(expected==620);
  burst.release_buffer(); assert(burst.capacity==0 && burst.items==nullptr);
  // A subsequent routine620stateburst also survives and drains normally.
  for (int i=0;i<620;++i) assert(burst.add_item(&entities[i], 1, 24));
  burst.release_buffer(); assert(burst.size()==620); // no pending discard
  burst.clear(); burst.release_buffer();
  assert(burst.empty() && burst.capacity==0 && burst.batch_start_time==0);
  // InitialOOM leaves no phantom item. LaterOOM leaves original pointer,
  // count, values and priority order untouched; dedup succeeds withoutalloc.
  Batch oom;
  fail_next=true; assert(!oom.add_item(&entities[0],1,24));
  assert(oom.empty() && oom.items==nullptr && oom.capacity==0);
  assert(oom.add_item(&entities[0],1,24));
  auto *old=oom.items; auto cap=oom.capacity; auto c=calls;
  fail_next=true;
  assert(oom.add_item(&entities[0],1,24)); assert(calls==c);
  assert(!oom.add_item(&entities[1],1,24));
  assert(oom.items==old && oom.capacity==cap && oom.size()==1 && oom[0].entity==&entities[0]);
  fail_next=true; assert(!oom.add_item_front(&entities[2],3,8));
  assert(oom.items==old && oom.size()==1 && oom[0].entity==&entities[0]);
  assert(oom.add_item_front(&entities[2],3,8));
  assert(oom[0].entity==&entities[2] && oom[1].entity==&entities[0]);
  assert(oom.add_item(&entities[0],99,8,7));
  assert(oom.add_item(&entities[0],99,8,7));
  assert(oom.size()==4 && oom[2].aux_data_index==7 && oom[3].aux_data_index==7);
  // Arithmetic ceiling is addressablebytes, not an invented entity cap.
  Batch limits;
  limits.item_count=Batch::MAX_ITEMS; limits.capacity=Batch::MAX_ITEMS;
  c=calls; assert(!limits.append_({nullptr,0,0,0})); assert(calls==c);
  limits.item_count=Batch::MAX_ITEMS/2+1; limits.capacity=limits.item_count;
  fail_next=true; assert(!limits.append_({nullptr,0,0,0}));
  assert(requested==Batch::MAX_ITEMS*sizeof(Batch::BatchItem));
  limits.item_count=0; limits.capacity=0;
}
"""
    )
    cpp = tmp_path / "batch.cpp"
    cpp.write_text(source)
    compiler = os.environ.get("CXX") or shutil.which("g++" if sys.platform == "linux" else "clang++")
    native_flags = ["-fno-pie", "-no-pie"] if sys.platform == "linux" else []
    assert compiler
    binary = tmp_path / "batch"
    subprocess.run(
        [
            compiler,
            *native_flags,
            "-std=c++20",
            "-fno-exceptions",
            "-fsanitize=address,undefined",
            "-g",
            str(cpp),
            "-o",
            str(binary),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run([str(binary)], check=True, timeout=15)


def test_actual_callers_fail_closed_after_allocation_failure(tmp_path):
    fixture = ROOT / "tests/fixtures/esphome_api_batch"
    header = PATCH.transform("api_connection.h", (fixture / "api_connection.h").read_text())
    cpp = PATCH.transform("api_connection.cpp", (fixture / "api_connection.cpp").read_text())

    def extract(text, signature):
        start, end = PATCH.function_span(text, signature)
        return text[start:end]

    schedule = extract(header, "  bool schedule_message_(")
    methods = "\n".join(
        extract(cpp, name)
        for name in [
            "bool APIConnection::schedule_message_front_",
            "bool APIConnection::send_message_smart_",
            "void APIConnection::process_iterator_batch_",
            "void APIConnection::finalize_iterator_sync_",
        ]
    )
    source = (
        r"""
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <type_traits>
#include <utility>
static bool fail_next=false;
void *fault_realloc(void *p,size_t n) { if(fail_next){fail_next=false;return nullptr;}return ::realloc(p,n); }
struct EntityBase { int key; };
struct FakeBuffer {};
struct ProtoWriteBuffer { FakeBuffer *p; };
struct FakeHelper {
 int writes=0, releases=0;
 bool can_write_without_blocking(){++writes;return true;}
 void release_buffers(){++releases;}
};
struct FakeParent { FakeBuffer buffer; FakeBuffer &get_shared_buffer_ref(){return buffer;} };
struct ComponentIterator;
struct APIConnection {
#define realloc fault_realloc
"""
        + PATCH.replacement_batch()
        + r"""
#undef realloc
 struct {bool remove=false, should_try_send_immediately=false;} flags_;
 DeferredBatch deferred_batch_;
 FakeHelper helper_storage; FakeHelper *helper_=&helper_storage;
 FakeParent parent_storage; FakeParent *parent_=&parent_storage;
 int schedules=0, encodes=0, sends=0;
 bool immediate=false, fail_drain=false;
 static constexpr uint32_t MAX_BATCH_PACKET_SIZE=1390;
 void on_fatal_error(){flags_.remove=true;}
 bool schedule_batch_(){++schedules;return true;}
 size_t get_max_batch_size_(){return 34;}
 bool should_send_immediately_(uint8_t){return immediate;}
 void prepare_first_message_buffer(FakeBuffer &,uint8_t){++encodes;}
 bool dispatch_message_(const DeferredBatch::BatchItem &,uint32_t,bool){++encodes;return true;}
 bool send_buffer(ProtoWriteBuffer,uint8_t){++sends;return true;}
 // Transport sink: no packet is acknowledged while blocked. The actual
 // iterator and schedule methods below decide whether it may be called.
 void process_batch_(){++encodes;if(fail_drain)flags_.remove=true;}
 bool schedule_message_front_(EntityBase *,uint8_t,uint8_t);
 bool send_message_smart_(EntityBase *,uint8_t,uint8_t,uint8_t=DeferredBatch::AUX_DATA_UNUSED);
 void process_iterator_batch_(ComponentIterator &);
 void finalize_iterator_sync_();
"""
        + schedule
        + r"""
};
struct ComponentIterator {
 APIConnection &conn;EntityBase &next;int advances=0;bool done=false;
 bool completed(){return done;}
 uint32_t progress_token() const {return done?1:0;}
 void advance(){++advances;if(conn.schedule_message_(&next,1,24))done=true;}
};
"""
        + methods
        + r"""
int main(){
 EntityBase e[65];
 APIConnection c;
 for(int i=0;i<64;++i)assert(c.schedule_message_(&e[i],1,24));
 auto *original=c.deferred_batch_.items;
 fail_next=true;ComponentIterator iterator{c,e[64]};
 c.process_iterator_batch_(iterator);
 assert(c.flags_.remove && iterator.advances==1 && !iterator.completed());
 assert(c.encodes==0 && c.deferred_batch_.size()==64 && c.deferred_batch_.items==original);
 auto schedules=c.schedules;
 c.immediate=true;
 assert(!c.send_message_smart_(&e[64],1,24));
 assert(!c.schedule_message_front_(nullptr,3,8));
 c.process_iterator_batch_(iterator);c.finalize_iterator_sync_();
 assert(c.encodes==0 && c.sends==0 && c.helper_storage.writes==0 && c.schedules==schedules);
 assert(c.helper_storage.releases==0 && !c.flags_.should_try_send_immediately);
 // A transport failure during finalize must not advance completion/release.
 APIConnection transport;
 assert(transport.schedule_message_(&e[0],1,24));transport.fail_drain=true;
 transport.finalize_iterator_sync_();
 assert(transport.flags_.remove && transport.encodes==1);
 assert(!transport.flags_.should_try_send_immediately && transport.helper_storage.releases==0);
 // Healthy immediate publication remains functional.
 APIConnection healthy;healthy.immediate=true;
 assert(healthy.send_message_smart_(&e[0],1,24));
 assert(healthy.encodes==2 && healthy.sends==1 && !healthy.flags_.remove);
}
"""
    )
    path = tmp_path / "callers.cpp"
    path.write_text(source)
    compiler = os.environ.get("CXX") or shutil.which("g++" if sys.platform == "linux" else "clang++")
    native_flags = ["-fno-pie", "-no-pie"] if sys.platform == "linux" else []
    binary = tmp_path / "callers"
    subprocess.run(
        [
            compiler,
            *native_flags,
            "-std=c++20",
            "-fno-exceptions",
            "-fsanitize=address,undefined",
            str(path),
            "-o",
            str(binary),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run([str(binary)], check=True, timeout=15)


def test_exact_upstream_patch_and_repeat_build(tmp_path):
    import hashlib

    fixture = ROOT / "tests/fixtures/esphome_api_batch"
    api = tmp_path / "src/esphome/components/api"
    api.mkdir(parents=True)
    core = tmp_path / "src/esphome/core"
    core.mkdir()
    (core / "version.h").write_text('#define ESPHOME_VERSION "2026.6.5"\n')
    shutil.copyfile(ROOT / "tests/fixtures/esphome_api_batch/component_iterator.h", core / "component_iterator.h")
    for name, digest in PATCH.UPSTREAM_HASHES.items():
        data = (fixture / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        (api / name).write_bytes(data)
    PATCH.apply(tmp_path)
    assert hashlib.sha256((core / "component_iterator.h").read_bytes()).hexdigest() == PATCH.CORE_PATCHED_HASH
    for name, digest in PATCH.PATCHED_HASHES.items():
        assert hashlib.sha256((api / name).read_bytes()).hexdigest() == digest
    PATCH.apply(tmp_path)


def test_unknown_upstream_is_rejected_before_any_write(tmp_path):
    api = tmp_path / "src/esphome/components/api"
    api.mkdir(parents=True)
    core = tmp_path / "src/esphome/core"
    core.mkdir()
    (core / "version.h").write_text('#define ESPHOME_VERSION "2026.6.5"\n')
    shutil.copyfile(ROOT / "tests/fixtures/esphome_api_batch/component_iterator.h", core / "component_iterator.h")
    for name in PATCH.UPSTREAM_HASHES:
        (api / name).write_text("unreviewed upstream")
    try:
        PATCH.apply(tmp_path)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("unknown upstream accepted")
    assert all((api / name).read_text() == "unreviewed upstream" for name in PATCH.UPSTREAM_HASHES)


def test_unreviewed_esphome_version_is_rejected(tmp_path):
    core = tmp_path / "src/esphome/core"
    core.mkdir(parents=True)
    (core / "version.h").write_text('#define ESPHOME_VERSION "2026.7.0"\n')
    try:
        PATCH.apply(tmp_path)
    except ValueError as exc:
        assert "version mismatch" in str(exc)
    else:
        raise AssertionError("unreviewed ESPHome version accepted")


def test_platformio_scons_without_file_global(tmp_path):
    # SCons executes extra scripts without__file__; prove the real build entry.
    project = tmp_path / ".esphome/build/greenhouse"
    api = project / "src/esphome/components/api"
    api.mkdir(parents=True)
    core = project / "src/esphome/core"
    core.mkdir()
    (core / "version.h").write_text('#define ESPHOME_VERSION "2026.6.5"\n')
    shutil.copyfile(ROOT / "tests/fixtures/esphome_api_batch/component_iterator.h", core / "component_iterator.h")
    for name in PATCH.UPSTREAM_HASHES:
        shutil.copyfile(ROOT / "tests/fixtures/esphome_api_batch" / name, api / name)
    (tmp_path / "patches").mkdir()
    for snippet in (ROOT / "firmware/patches").glob("*.inc"):
        shutil.copyfile(snippet, tmp_path / "patches" / snippet.name)

    class BuildEnv:
        def subst(self, name):
            assert name == "$PROJECT_DIR"
            return str(project)

    namespace = {"Import": lambda name: None, "env": BuildEnv()}
    # Execute this repository-owned build script exactly as SCons does.
    exec(compile((ROOT / "firmware/build_api_batch_patch.py").read_text(), "extra_script", "exec"), namespace)  # noqa: S102
    import hashlib

    for name, digest in PATCH.PATCHED_HASHES.items():
        assert hashlib.sha256((api / name).read_bytes()).hexdigest() == digest
