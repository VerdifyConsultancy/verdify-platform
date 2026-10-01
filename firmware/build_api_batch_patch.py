"""Guarded source correction for ESPHome2026.6.5 DeferredBatch allocation abort.

Patch only reviewed generated API batching/overflow sources before compilation. Unknown source
fails closed, including partially patched files; original/patched hashes identify
exact provenance. This is not a global allocator override or protocol change.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_DIR = Path(__file__).parent if "__file__" in globals() else None
UPSTREAM_VERSION = "2026.6.5"
UPSTREAM_HASHES = {
    "api_server.h": "bae9b6416cd3e64310a1129d5518d8d987bd9191dc9c23f8fb7824f5c1e141a5",
    "api_frame_helper.h": "a6f3ca95be6bdebc3a4c5c3bdf552af299b90a43cb22c19ad32e13923d6fb196",
    "api_buffer.h": "bcd26cc82a5009c02276eff38dde24dd92e72890b696552492adc24a64aad7e9",
    "api_overflow_buffer.cpp": "81f7be6e141f043660ca87b7a1a81918caab4105d8968782880e3d02664e7cd6",
    "api_overflow_buffer.h": "d0692231e9f63d237836c3a784f092a2abeeacef45fa04856768d0336cce8b94",
    "api_connection.h": "43e5f3718d37a0aa153f49ece2f96e96de60ef839363fc9c9c97b6d1525cfdd3",
    "api_connection.cpp": "dc923e04b1e18e43661ce02e1af98d959cb9414c51587c872478fc0b8d524517",
}


CORE_UPSTREAM_HASH = "46bbeb1c1c6ac0e688677da9317a16a6293fe0ba6720d7cd26585b892a764615"
CORE_PATCHED_HASH = "d3f6d94a8e7b8f0cde0a107cba885e3f433cc0971b8e712220f8e3117a921220"


def transform_iterator(text: str) -> str:
    return replace_once(
        text,
        "  bool completed() const { return this->state_ == IteratorState::NONE; }",
        "  bool completed() const { return this->state_ == IteratorState::NONE; }\n"
        "  // State+index observes progress without changing layout or completion semantics.\n"
        "  uint32_t progress_token() const { return (static_cast<uint32_t>(this->state_) << 16) | this->at_; }",
    )


def replacement_batch() -> str:
    return (SOURCE_DIR / "patches/deferred_batch.inc").read_text()


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("ESPHome API patch context mismatch")
    return text.replace(old, new, 1)


def function_span(text: str, signature: str) -> tuple[int, int]:
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return start, end


def guard_entry(text: str, signature: str, result: str = "") -> str:
    start, end = function_span(text, signature)
    body = text[start:end]
    brace = body.index("{") + 1
    body = body[:brace] + f"\n  if (this->flags_.remove)\n    return{result};\n" + body[brace:]
    return text[:start] + body + text[end:]


def guard_after_call(text: str, signature: str, call: str) -> str:
    start, end = function_span(text, signature)
    body = text[start:end]
    indent = call[: len(call) - len(call.lstrip())]
    body = replace_once(body, call, call + f"\n{indent}if (this->flags_.remove)\n{indent}  return;")
    return text[:start] + body + text[end:]


def transform(name: str, text: str) -> str:
    if name == "api_buffer.h":
        return replace_once(
            text,
            "  size_t size() const { return this->size_; }",
            "  size_t capacity_bytes() const { return this->capacity_; }\n"
            "  size_t size() const { return this->size_; }",
        )
    if name == "api_frame_helper.h":
        return replace_once(
            text,
            "  // Release excess memory from internal buffers after initial sync",
            "  size_t rx_capacity_bytes() const { return this->rx_buf_.capacity_bytes(); }\n"
            "  size_t overflow_allocated_bytes() const { return this->overflow_buf_.allocated_bytes(); }\n"
            "  size_t overflow_pending_bytes() const { return this->overflow_buf_.pending_bytes(); }\n"
            "  size_t overflow_count() const { return this->overflow_buf_.count(); }\n"
            "  // Release excess memory from internal buffers after initial sync",
        )
    if name == "api_server.h":
        return replace_once(
            text,
            "  // Get reference to shared buffer for API connections",
            (SOURCE_DIR / "patches/api_heap_snapshot.inc").read_text()
            + "\n  // Get reference to shared buffer for API connections",
        )
    if name == "api_connection.h":
        text = replace_once(text, "#include <vector>", "#include <cstdlib>\n#include <cstring>\n#include <type_traits>")
        start = text.index("  // Generic batching mechanism for both state updates and entity info")
        end = text.index("\n  };", start) + len("\n  };")
        text = text[:start] + replacement_batch().rstrip() + text[end:]
        return replace_once(
            text,
            "    this->deferred_batch_.add_item(entity, message_type, estimated_size, aux_data_index);",
            "    if (this->flags_.remove ||\n"
            "        !this->deferred_batch_.add_item(entity, message_type, estimated_size, aux_data_index)) {\n"
            "      this->on_fatal_error();\n"
            "      return false;  // No completion/freshness may be inferred from an unqueued state.\n"
            "    }",
        )
    if name == "api_connection.cpp":
        text = replace_once(
            text,
            "  this->deferred_batch_.add_item_front(entity, message_type, estimated_size);",
            "  if (this->flags_.remove || !this->deferred_batch_.add_item_front(entity, message_type, estimated_size)) {\n"
            "    this->on_fatal_error();\n"
            "    return false;\n"
            "  }",
        )
        text = replace_once(
            text,
            "  while (!iterator.completed() && (this->deferred_batch_.size() - initial_size) < max_batch) {",
            "  while (!this->flags_.remove && !iterator.completed() &&\n"
            "         (this->deferred_batch_.size() - initial_size) < max_batch) {",
        )
        text = replace_once(
            text,
            "    iterator.advance();",
            "    const uint32_t before = iterator.progress_token();\n"
            "    iterator.advance();\n"
            "    // A direct service/completion send may return false without enqueueing.\n"
            "    // Preserve its index and return to controls/WDT instead of retrying forever.\n"
            "    if (iterator.progress_token() == before)\n"
            "      break;",
        )
        # OOM is terminal for this peer: no later callback, iterator flush,
        # completion, or connection-loop phase may encode/allocate afterwards.
        for signature, result in [
            ("bool APIConnection::send_message_smart_", " false"),
            ("void APIConnection::loop()", ""),
            ("void APIConnection::process_active_iterator_()", ""),
            ("void APIConnection::finalize_iterator_sync_()", ""),
            ("void APIConnection::process_iterator_batch_", ""),
            ("void APIConnection::process_batch_()", ""),
        ]:
            text = guard_entry(text, signature, result)
        text = guard_after_call(text, "void APIConnection::loop()", "    this->process_batch_();")
        text = guard_after_call(text, "void APIConnection::loop()", "    this->process_active_iterator_();")
        text = guard_after_call(text, "void APIConnection::finalize_iterator_sync_()", "    this->process_batch_();")
        text = replace_once(
            text,
            "  // If the batch is full, process it immediately",
            "  if (this->flags_.remove)\n    return;\n\n  // If the batch is full, process it immediately",
        )
        return text
    if name == "api_overflow_buffer.h":
        text = replace_once(
            text,
            "  uint8_t count() const { return this->count_; }",
            "  uint8_t count() const { return this->count_; }\n"
            + (SOURCE_DIR / "patches/api_overflow_snapshot.inc").read_text(),
        )
        text = replace_once(
            text, "#include <cstdint>", "#include <cstdint>\n#include <cstdlib>\n#include <type_traits>"
        )
        text = replace_once(
            text,
            "      delete[] entry->data;\n      delete entry;  // NOLINT(cppcoreguidelines-owning-memory)",
            "      ::free(entry->data);\n      ::free(entry);",
        )
        return replace_once(
            text, "Returns false if the queue is full", "Returns false if the queue is full or allocation fails"
        )
    if name == "api_overflow_buffer.cpp":
        text = replace_once(
            text,
            "  // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)\n"
            "  auto *entry = new Entry{new uint8_t[buffer_size], buffer_size, 0};\n"
            "  this->queue_[this->tail_] = entry;",
            "  // Fallible POD storage; neither allocation failure may change the backlog.\n"
            "  static_assert(std::is_trivial_v<Entry>);\n"
            "  auto *entry = static_cast<Entry *>(::malloc(sizeof(Entry)));\n"
            "  if (entry == nullptr)\n"
            "    return false;\n"
            "  auto *data = static_cast<uint8_t *>(::malloc(buffer_size));\n"
            "  if (data == nullptr) {\n"
            "    ::free(entry);\n"
            "    return false;\n"
            "  }\n"
            "  *entry = Entry{data, buffer_size, 0};",
        )
        return replace_once(
            text,
            "  this->tail_ = (this->tail_ + 1) % API_MAX_SEND_QUEUE;",
            "  this->queue_[this->tail_] = entry;\n  this->tail_ = (this->tail_ + 1) % API_MAX_SEND_QUEUE;",
        )
    raise ValueError("unexpected ESPHome patch target")


def apply(project: Path) -> None:
    version = (project / "src/esphome/core/version.h").read_text()
    if f'#define ESPHOME_VERSION "{UPSTREAM_VERSION}"' not in version.splitlines():
        raise ValueError("ESPHome version mismatch; review the API correction before upgrading")
    targets = project / "src/esphome/components/api"
    prepared = {}
    for name, expected in UPSTREAM_HASHES.items():
        path = targets / name
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        # Repeated PlatformIO compile may see our exact prior output. Never
        # accept an arbitrary marker or unknown upstream/partially edited file.
        patched_digest = PATCHED_HASHES[name]
        if actual == patched_digest:
            continue
        if actual != expected:
            raise ValueError(f"ESPHome{UPSTREAM_VERSION} API upstream hash mismatch: {name}")
        patched = transform(name, data.decode()).encode()
        if hashlib.sha256(patched).hexdigest() != patched_digest:
            raise ValueError("source-owned API patch output hash mismatch")
        prepared[path] = patched
    iterator = project / "src/esphome/core/component_iterator.h"
    current = iterator.read_bytes()
    digest = hashlib.sha256(current).hexdigest()
    if digest != CORE_PATCHED_HASH:
        if digest != CORE_UPSTREAM_HASH:
            raise ValueError("ESPHome iterator upstream hash mismatch")
        patched = transform_iterator(current.decode()).encode()
        if hashlib.sha256(patched).hexdigest() != CORE_PATCHED_HASH:
            raise ValueError("source-owned iterator output hash mismatch")
        prepared[iterator] = patched
    for path, data in prepared.items():
        path.write_bytes(data)
    print(f"Verified Verdify fallible DeferredBatch/overflow patch for ESPHome{UPSTREAM_VERSION}")


# Bound to the exact reviewed patch bytes. Updated only with focused tests.
PATCHED_HASHES = {
    "api_buffer.h": "6c5b083ae0b491eea76d01c71b1f3fbb55d73e8dadcb26559dd6e237261f1948",
    "api_frame_helper.h": "5c16bfe0f51ec1c3afe233d0e54abb716193f2fcf4756ea6d41fd34779bd7966",
    "api_server.h": "7c3a515f1f551c30ce058eda114ec76c2d783d5439e40a4ed5e5bc2ea53f1c8e",
    "api_overflow_buffer.cpp": "370db5e67ad5c4145e6f1f7d93558a1502cb9680064f4cf28bbdae511d289003",
    "api_overflow_buffer.h": "efe581f47eacaa5edd12914cacc112b5f02f99c0f3df89f6b65fe950bce266f8",
    "api_connection.h": "eb5bd763e06e73ad50c6f96e1c0563315892628d2f8e713d97863198e821bfb1",
    "api_connection.cpp": "29c45da79cf5d6a8c3db0eb1c91f4997ed6c04fc0bc83687d4e200a542fbde9d",
}

if "Import" in globals():
    Import("env")  # noqa: F821 — PlatformIO/SCons supplied, no runtime device API.
    project = Path(env.subst("$PROJECT_DIR"))  # noqa: F821
    SOURCE_DIR = project.resolve().parents[2]  # firmware/.esphome/build/greenhouse
    apply(project)
elif __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    apply(parser.parse_args().project)
