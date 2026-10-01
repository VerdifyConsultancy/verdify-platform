"""Selected iterator + direct-service path: backpressure must return to main loop."""

import hashlib
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/esphome_api_batch"
SPEC = importlib.util.spec_from_file_location("patch", ROOT / "firmware/build_api_batch_patch.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


def test_actual_iterator_service_backpressure_resume_and_full_success(tmp_path):
    assert (
        hashlib.sha256((FIXTURE / "component_iterator.cpp").read_bytes()).hexdigest()
        == "b129ea9b4bd56b091bfbcc9f6178362d9a510d9439dd5ab33dac6ddccec7b8da"
    )
    assert (
        hashlib.sha256((FIXTURE / "list_entities.cpp").read_bytes()).hexdigest()
        == "696c5e4f197640e8657309e6563b85063793a47e513267512f98b848cdb4861a"
    )
    header = (FIXTURE / "component_iterator.h").read_text()
    assert hashlib.sha256(header.encode()).hexdigest() == PATCH.CORE_UPSTREAM_HASH
    core = tmp_path / "esphome/core"
    api = tmp_path / "esphome/components/api"
    core.mkdir(parents=True)
    api.mkdir(parents=True)
    (core / "component_iterator.h").write_text(PATCH.transform_iterator(header))
    for name in ["component.h", "controller.h", "entity_types.h"]:
        (core / name).write_text("#pragma once\n")
    (core / "helpers.h").write_text("#pragma once\n#include <cstdint>\n#include <vector>\n")
    (core / "application.h").write_text("#pragma once\n")
    (api / "api_server.h").write_text("#pragma once\n")
    (api / "user_services.h").write_text("#pragma once\n")
    selected = (FIXTURE / "api_connection.cpp").read_text()
    fixed = PATCH.transform("api_connection.cpp", selected)

    def method(text, name):
        a, b = PATCH.function_span(text, name)
        return text[a:b]

    on_service = method((FIXTURE / "list_entities.cpp").read_text(), "bool ListEntitiesIterator::on_service")
    prelude = r"""
#define USE_API
#define USE_API_USER_DEFINED_ACTIONS
#include <cassert>
#include <cstdint>
#include <vector>
#include "esphome/core/component_iterator.h"
namespace esphome::api {
class UserServiceDescriptor {public: int id; bool is_internal(){return false;} int encode_list_service_response(){return id;}};
struct Server {std::vector<UserServiceDescriptor*> entries; const auto& get_user_services(){return entries;}};
Server server; Server* global_api_server=&server;
struct APIConnection {
 struct {bool remove=false;}flags_;
 struct {size_t size(){return 0;}}deferred_batch_;
 bool blocked=true, end_blocked=true; int attempts=0, endings=0;
 std::vector<int> accepted;
 size_t get_max_batch_size_(){return 34;}
 void process_batch_(){assert(false);}
 bool send_message(int value){++attempts; if(blocked)return false;accepted.push_back(value);return true;}
 void process_iterator_batch_(ComponentIterator&);
};
struct ListEntitiesIterator:ComponentIterator {
 APIConnection*client_;
 explicit ListEntitiesIterator(APIConnection*c):client_(c){}
 bool on_service(UserServiceDescriptor*) override;
 bool on_end()override{++client_->endings;return !client_->end_blocked;}
};
"""
    base = prelude + on_service + "\n" + method(fixed, "void APIConnection::process_iterator_batch_") + "}\n"
    base += '#include "esphome/core/component_iterator.cpp"\n'
    (core / "component_iterator.cpp").write_bytes((FIXTURE / "component_iterator.cpp").read_bytes())
    main = r"""
int main(){using namespace esphome::api;
 UserServiceDescriptor services[620]; for(int i=0;i<620;++i){services[i].id=i;server.entries.push_back(&services[i]);}
 APIConnection client; ListEntitiesIterator iterator(&client); iterator.begin();
 client.process_iterator_batch_(iterator);assert(client.attempts==1&&!iterator.completed()&&client.accepted.empty());
 auto position=iterator.progress_token();
 client.process_iterator_batch_(iterator);assert(client.attempts==2&&iterator.progress_token()==position);
 client.blocked=false;client.process_iterator_batch_(iterator);
 assert(client.accepted.size()==620&&client.endings==1&&!iterator.completed());
 for(int i=0;i<620;++i)assert(client.accepted[i]==i);
 // Completion backpressure also leaves the iterator at the same response.
 position=iterator.progress_token();client.process_iterator_batch_(iterator);
 assert(client.endings==2&&iterator.progress_token()==position&&client.accepted.size()==620);
 client.end_blocked=false;client.process_iterator_batch_(iterator);assert(iterator.completed()&&client.endings==3);
 // Fully writable registration retains the normal full620service behavior in one call.
 APIConnection writable;writable.blocked=false;writable.end_blocked=false;
 ListEntitiesIterator full(&writable);full.begin();writable.process_iterator_batch_(full);
 assert(full.completed()&&writable.accepted.size()==620&&writable.attempts==620&&writable.endings==1);
}
"""
    (tmp_path / "test.cpp").write_text(base + main)
    subprocess.run(
        [
            "c++",
            "-std=c++20",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-parameter",
            "-I",
            str(tmp_path),
            str(tmp_path / "test.cpp"),
            "-o",
            str(tmp_path / "fixed"),
        ],
        check=True,
    )
    subprocess.run([str(tmp_path / "fixed")], check=True, timeout=5)
    # Reproduce the original retry loop from the exact selected method: a real
    # false send result repeats forever. No sleeps or invented scheduling delays.
    original_base = base.replace(
        method(fixed, "void APIConnection::process_iterator_batch_"),
        method(selected, "void APIConnection::process_iterator_batch_"),
    )
    original_main = "int main(){using namespace esphome::api; UserServiceDescriptor s; s.id=0; server.entries.push_back(&s); APIConnection c; ListEntitiesIterator i(&c); i.begin(); c.process_iterator_batch_(i); return 9;}"
    (tmp_path / "original.cpp").write_text(original_base + original_main)
    subprocess.run(
        ["c++", "-std=c++20", "-I", str(tmp_path), str(tmp_path / "original.cpp"), "-o", str(tmp_path / "original")],
        check=True,
    )
    try:
        subprocess.run([str(tmp_path / "original")], timeout=0.5, check=True)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("Selected original nonprogress loop unexpectedly returned")


def test_unknown_iterator_rejected_before_any_api_source_write(tmp_path):
    core = tmp_path / "src/esphome/core"
    api = tmp_path / "src/esphome/components/api"
    core.mkdir(parents=True)
    api.mkdir(parents=True)
    (core / "version.h").write_text('#define ESPHOME_VERSION "2026.6.5"\n')
    (core / "component_iterator.h").write_text("unreviewed iterator")
    originals = {}
    for name in PATCH.UPSTREAM_HASHES:
        originals[name] = (FIXTURE / name).read_bytes()
        (api / name).write_bytes(originals[name])
    try:
        PATCH.apply(tmp_path)
    except ValueError as exc:
        assert "iterator upstream hash mismatch" in str(exc)
    else:
        raise AssertionError("unknown iterator accepted")
    assert all((api / name).read_bytes() == content for name, content in originals.items())
