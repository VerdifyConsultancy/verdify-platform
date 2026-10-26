"""Small source-only checks. Native compilation/scenarios use the separate pilot."""

import importlib.util
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fog_vent_consumer", ROOT / "scripts/fog_vent_consumer.py")
fog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fog)


def test_fragments_are_exact_source_slices_and_include_real_helper():
    report = fog.extract_fragments()
    for item in report["fragments"].values():
        source = (ROOT / item["source_path"]).read_text()
        assert source.count(item["text"]) == 1
        assert fog.sha256(item["text"].encode()) == item["sha256"]
        assert source.count("\n", 0, source.index(item["text"])) + 1 == item["first_line"]
    paths = {entry["path"] for entry in report["source_files"]}
    assert {
        fog.CONTROLS,
        fog.LOGIC,
        "firmware/lib/greenhouse_types.h",
        "firmware/lib/greenhouse_solar.h",
        "firmware/lib/policy_vector_generated.h",
    } <= paths
    assert report["corpus_prefix_coverage"] == "27/48 UNPROVEN; no row-wise credit added"
    assert not report["physical_acceptance"]


@pytest.mark.parametrize("name", tuple(fog.extract_fragments()["fragments"])[:-1])
@pytest.mark.parametrize("mutation", ("missing", "duplicate"))
def test_missing_or_ambiguous_control_fragment_refuses(name, mutation):
    text = (ROOT / fog.CONTROLS).read_text()
    fragment = fog.extract_fragments()["fragments"][name]["text"]
    replacement = "/* missing */" if mutation == "missing" else fragment + "\n" + fragment
    with pytest.raises(ValueError, match="missing or ambiguous"):
        fog.extract_fragments(controls_text=text.replace(fragment, replacement, 1))


@pytest.mark.parametrize("mutation", ("missing", "duplicate"))
def test_missing_or_ambiguous_actual_helper_refuses(tmp_path, mutation):
    report = fog.extract_fragments()
    for item in report["source_files"]:
        path = tmp_path / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / item["path"], path)
    path = tmp_path / fog.LOGIC
    text = path.read_text()
    helper = report["fragments"]["actual_helper"]["text"]
    path.write_text(text.replace(helper, "" if mutation == "missing" else helper * 2))
    with pytest.raises(ValueError, match="missing or ambiguous"):
        fog.extract_fragments(tmp_path)


def test_preapply_readback_cannot_move_after_consumer():
    text = (ROOT / fog.CONTROLS).read_text()
    parts = fog.extract_fragments()["fragments"]
    readback, consumer = parts["vent_readback"]["text"], parts["fog_consumer"]["text"]
    text = text.replace(readback, "").replace(consumer, consumer + "\n" + readback)
    with pytest.raises(ValueError, match="source order"):
        fog.extract_fragments(controls_text=text)


def test_generated_original_and_mutants_change_only_consumer():
    original, manifest = fog.generate_cpp()
    branch = manifest["fragments"]["fog_consumer"]["text"]
    deleted, _ = fog.generate_cpp(variant="deleted_consumer")
    inverted, _ = fog.generate_cpp(variant="inverted_consumer")
    assert deleted == original.replace(branch, "/* causal negative: extracted consumer deleted */", 1)
    assert inverted == original.replace(branch, branch.replace("if (polb(", "if (!polb(", 1), 1)
    assert '#include "greenhouse_logic.h"' in original
    assert manifest["fragments"]["actual_helper"]["text"] not in original
    assert "@@" not in original
    assert len({fog.sha256(value.encode()) for value in (original, deleted, inverted)}) == 3


def test_fog_call_excludes_unrelated_following_counters():
    text = (ROOT / fog.CONTROLS).read_text()
    application = fog.extract_fragments()["fragments"]["fog_apply"]["text"]
    assert application.rstrip().endswith("|| fert_master_on);")
    assert "recently; force_off" in application
    changed = text.replace(application, application + "\n unrelated_counter += 1;", 1)
    assert fog.extract_fragments(controls_text=changed)["fragments"]["fog_apply"]["text"] == application


def test_scenario_inventory_is_bounded_and_matches_native_template():
    source, _ = fog.generate_cpp()
    assert len(fog.SCENARIOS) == len(set(fog.SCENARIOS)) == 28
    assert all(f'"{name}"' in source for name in fog.SCENARIOS)
    assert set(fog.NEGATIVE_WITNESSES) <= set(fog.SCENARIOS)
    assert len(fog.SCENARIOS) * len(fog.SELECTIONS) == 56
    assert len(fog.NEGATIVE_WITNESSES) * len(fog.SELECTIONS) * 2 == 8
    assert "BEHAVIOR_ASSERT" in source and "SCENARIO_COMPLETE" in source


def test_unknown_variant_refuses():
    with pytest.raises(ValueError, match="unknown causal variant"):
        fog.generate_cpp(variant="made_up")


@pytest.fixture
def pilot(monkeypatch):
    monkeypatch.setitem(sys.modules, "fog_vent_consumer", fog)
    spec = importlib.util.spec_from_file_location("fog_vent_pilot", ROOT / "scripts/qualify_fog_vent_consumer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_gate_refuses_mac_without_compiler_or_output(pilot, monkeypatch, tmp_path):
    monkeypatch.setattr(pilot.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("VERDIFY_NATIVE_QUALIFICATION", "1")
    monkeypatch.setattr(pilot.subprocess, "run", lambda *a, **k: pytest.fail("must not launch a process"))
    out = tmp_path / "no-output"
    with pytest.raises(SystemExit):
        pilot.main(["--expected-source", "a" * 40, "--output-dir", str(out)])
    assert not out.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"returncode": 2},
        {"returncode": 0},
        {"timed_out": True},
        {"stdout": ""},
        {"stderr": "compiler failure\n"},
        {"stderr": "BEHAVIOR_ASSERT unrelated_case line=1\n"},
    ],
)
def test_causal_negative_requires_completed_exact_behavior_failure(pilot, change):
    result = dict(
        returncode=1,
        timed_out=False,
        stdout="SCENARIO_COMPLETE name=gate_true_open_close selection=snapshot exit=1\n",
        stderr="BEHAVIOR_ASSERT selected_consumer_request line=42\n",
    )
    pilot.require_behavior(result, "gate_true_open_close", "snapshot", 1)
    result.update(change)
    with pytest.raises(ValueError):
        pilot.require_behavior(result, "gate_true_open_close", "snapshot", 1)


def test_timeout_retains_partial_native_output_before_refusal(pilot, monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1, output=b"partial stdout\n", stderr=b"partial stderr\n")

    monkeypatch.setattr(pilot.subprocess, "run", timeout)
    records = []
    result = pilot.run_process(
        ["not-executed"],
        cwd=tmp_path,
        out=tmp_path,
        name="timeout",
        timeout=1,
        deadline=time.monotonic() + 10,
        records=records,
    )
    assert result["timed_out"] and result["returncode"] is None
    assert (tmp_path / "timeout.stdout").read_bytes() == b"partial stdout\n"
    assert (tmp_path / "timeout.stderr").read_bytes() == b"partial stderr\n"
    assert records[0]["stdout_sha256"] == fog.sha256(b"partial stdout\n")
    assert (tmp_path / "process-results.json").is_file()
