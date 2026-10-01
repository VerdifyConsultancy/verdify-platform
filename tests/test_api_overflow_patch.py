"""Compile selected upstream queue and caller with bounded allocator/transport faults."""

import hashlib
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/esphome_api_batch"
spec = importlib.util.spec_from_file_location("patch", ROOT / "firmware/build_api_batch_patch.py")
PATCH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PATCH)

PRELUDE = r"""
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <type_traits>
#include <vector>
#include <new>
#include <cerrno>
#include <sys/uio.h>
#define USE_API
#define API_MAX_SEND_QUEUE 8
#define ESPHOME_ALWAYS_INLINE inline
namespace esphome::socket {
struct Socket {
  std::vector<uint8_t> bytes;
  ssize_t next = 99999;
  ssize_t write(const void *p, size_t n) {
    if (next <= 0) { errno = EAGAIN; return next; }
    size_t sent = std::min(n, static_cast<size_t>(next));
    auto *s = static_cast<const uint8_t *>(p);
    bytes.insert(bytes.end(), s, s + sent);
    return sent;
  }
};
}
static int fail_at = 0, calls = 0, outstanding = 0;
void *fault_malloc(size_t n) {
  if (++calls == fail_at) return nullptr;
  void *p = ::malloc(n); assert(p); ++outstanding; return p;
}
void fault_free(void *p) { if (p) { --outstanding; ::free(p); } }
"""


def queue_source(patched):
    h = (FIXTURE / "api_overflow_buffer.h").read_text()
    cpp = (FIXTURE / "api_overflow_buffer.cpp").read_text()
    if patched:
        h = PATCH.transform("api_overflow_buffer.h", h)
        cpp = PATCH.transform("api_overflow_buffer.cpp", cpp)
    # Dependency-only stubs; the complete queue class/method bodies stay selected upstream.
    h = "\n".join(x for x in h.splitlines() if not x.startswith('#include "') and x != "#pragma once")
    cpp = cpp.replace('#include "api_overflow_buffer.h"', "")
    return (h + "\n" + cpp).replace("::malloc(", "::fault_malloc(").replace("::free(", "::fault_free(")


def compile_native(tmp_path, source, name):
    src = tmp_path / (name + ".cpp")
    src.write_text(source)
    binary = tmp_path / name
    subprocess.run(
        [
            "c++",
            "-std=c++20",
            "-fno-exceptions",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-g",
            str(src),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    return binary


def test_actual_upstream_disabled_exception_allocator_aborts(tmp_path):
    # Simulate the selected no-exceptions throwing-new failure contract. This is
    # an offline allocator reproduction, not attribution of a controller reboot.
    allocator = r"""
void *operator new(size_t n) {
  if (++calls == fail_at) std::abort();
  auto *p = std::malloc(n); if (!p) std::abort(); return p;
}
void *operator new[](size_t n) { return ::operator new(n); }
void operator delete(void *p) noexcept { std::free(p); }
void operator delete[](void *p) noexcept { std::free(p); }
"""
    main = r"""
int main(int argc, char **) {
  calls = 0; fail_at = argc;
  esphome::api::APIOverflowBuffer q;
  char data[] = "abcd"; iovec v{data, 4};
  return q.enqueue_iov(&v, 1, 4, 0) ? 0 : 1;
}
"""
    binary = compile_native(tmp_path, PRELUDE + allocator + queue_source(False) + main, "upstream")
    for args in ([], ["second"]):
        result = subprocess.run([str(binary), *args], capture_output=True)
        assert result.returncode < 0, result.stderr.decode()


def test_actual_queue_allocation_faults_backpressure_and_full_capacity(tmp_path):
    main = r"""
using esphome::api::APIOverflowBuffer;
struct Inspect : APIOverflowBuffer {
  auto head() { return head_; } auto tail() { return tail_; }
  auto front() { return queue_[head_]; }
};
int main() {
  char a[] = "ab", b[] = "cdef"; iovec v[]{{a,2},{b,4}};
  {
    Inspect q;
    assert(q.enqueue_iov(v,2,6,1)); // retain bcdef
    auto *entry = q.front(); auto *data = entry->data;
    auto head = q.head(), tail = q.tail(); int live = outstanding;
    for (int allocation : {1,2}) {
      calls=0; fail_at=allocation;
      assert(!q.enqueue_iov(v,2,6,0));
      assert(q.count()==1 && q.head()==head && q.tail()==tail);
      assert(q.front()==entry && q.front()->data==data && q.front()->offset==0);
      assert(std::memcmp(data,"bcdef",5)==0 && outstanding==live);
    }
    fail_at=0; calls=0;
    esphome::socket::Socket socket;
    socket.next=-1; assert(q.try_drain(&socket)==-1 && q.count()==1 && entry->offset==0);
    socket.next=0; assert(q.try_drain(&socket)==0 && q.count()==1 && entry->offset==0);
    socket.next=2; assert(q.try_drain(&socket)==2 && q.count()==1 && entry->offset==2);
    socket.next=999; assert(q.try_drain(&socket)==0 && q.empty());
    assert(std::string(socket.bytes.begin(),socket.bytes.end())=="bcdef");
    // Wrap the ring repeatedly and retain all eight accepted packets.
    std::string expected="bcdef";
    for(int cycle=0;cycle<20;cycle++) {
      for(int i=0;i<API_MAX_SEND_QUEUE;i++) assert(q.enqueue_iov(v,2,6,0));
      int allocs=calls; assert(q.full() && !q.enqueue_iov(v,2,6,0) && calls==allocs);
      assert(q.try_drain(&socket)==0 && q.empty());
      for(int i=0;i<API_MAX_SEND_QUEUE;i++) expected += "abcdef";
      assert(std::string(socket.bytes.begin(),socket.bytes.end())==expected);
    }
    assert(outstanding==0);
    assert(q.enqueue_iov(v,2,6,3)); // header+part payload already sent, retain def
    assert(std::memcmp(q.front()->data,"def",3)==0);
  }
  assert(outstanding==0); // destructor frees a pending unsent packet
}
"""
    binary = compile_native(tmp_path, PRELUDE + "#include <string>\n" + queue_source(True) + main, "patched")
    subprocess.run([str(binary)], check=True, capture_output=True)


def test_selected_real_frame_caller_marks_failed_on_enqueue_oom(tmp_path):
    fixture = FIXTURE / "api_frame_helper.cpp"
    assert (
        hashlib.sha256(fixture.read_bytes()).hexdigest()
        == "9d594b8e620e8ab4958471d5e2b1daa57973a66a3205017cd67104a6a4079a7d"
    )
    text = fixture.read_text()
    start, end = PATCH.function_span(text, "APIError APIFrameHelper::write_raw_iov_")
    caller = text[start:end]
    scaffold = r"""
namespace esphome::api {
enum class APIError { OK, SOCKET_WRITE_FAILED };
class APIFrameHelper {
 public:
  enum class State { DATA, FAILED };
  State state_=State::DATA;
  APIOverflowBuffer overflow_buf_;
  static constexpr ssize_t WRITE_NOT_ATTEMPTED=-2, WRITE_FAILED=-1;
  APIError drain_overflow_and_handle_errors_() { return APIError::OK; }
  ssize_t write_iov_to_socket_(const iovec *,int) { return -1; }
  APIError write_raw_iov_(const iovec *,int,uint16_t,ssize_t);
};
#define HELPER_LOG(...) do {} while(0)
"""
    main = r"""
}
int main() {
  using namespace esphome::api;
  char bytes[]="abcdef"; iovec v{bytes,6};
  for(int alloc : {1,2}) {
    { APIFrameHelper h;
      assert(h.overflow_buf_.enqueue_iov(&v,1,6,0));
      calls=0; fail_at=alloc;
      // A partial transport send must report failure, never OK for unsaved suffix.
      assert(h.write_raw_iov_(&v,1,6,2)==APIError::SOCKET_WRITE_FAILED);
      assert(h.state_==APIFrameHelper::State::FAILED && h.overflow_buf_.count()==1);
      fail_at=0;
    }
    assert(outstanding==0);
  }
  APIFrameHelper h;
  errno=EAGAIN; assert(h.write_raw_iov_(&v,1,6,-1)==APIError::OK);
  assert(h.overflow_buf_.count()==1 && h.state_==APIFrameHelper::State::DATA);
}
"""
    binary = compile_native(tmp_path, PRELUDE + queue_source(True) + scaffold + caller + main, "caller")
    subprocess.run([str(binary)], check=True, capture_output=True)
