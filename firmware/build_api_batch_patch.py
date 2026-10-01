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
    "api_overflow_buffer.cpp": "81f7be6e141f043660ca87b7a1a81918caab4105d8968782880e3d02664e7cd6",
    "api_overflow_buffer.h": "d0692231e9f63d237836c3a784f092a2abeeacef45fa04856768d0336cce8b94",
    "api_connection.h": "43e5f3718d37a0aa153f49ece2f96e96de60ef839363fc9c9c97b6d1525cfdd3",
    "api_connection.cpp": "dc923e04b1e18e43661ce02e1af98d959cb9414c51587c872478fc0b8d524517",
}


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
    for path, data in prepared.items():
        path.write_bytes(data)
    print(f"Verified Verdify fallible DeferredBatch/overflow patch for ESPHome{UPSTREAM_VERSION}")


# Bound to the exact reviewed patch bytes. Updated only with focused tests.
PATCHED_HASHES = {
    "api_overflow_buffer.cpp": "370db5e67ad5c4145e6f1f7d93558a1502cb9680064f4cf28bbdae511d289003",
    "api_overflow_buffer.h": "93524c629fbde8de099834586acd65a7e6351f818bad1a16ab9989cebc7b6359",
    "api_connection.h": "eb5bd763e06e73ad50c6f96e1c0563315892628d2f8e713d97863198e821bfb1",
    "api_connection.cpp": "485c6250d2c9960204904ef8c093ab252b4d72d49ebd98f5693546031da7a4a6",
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
