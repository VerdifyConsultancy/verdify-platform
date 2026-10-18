"""Extract one real downstream firmware consumer; no compiler or live I/O.

The generated scenario adapts storage, switch readback and time only. It does
not claim row-wise prefix coverage or derive pre-tick state from telemetry.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = "firmware/greenhouse/controls.yaml"
LOGIC = "firmware/lib/greenhouse_logic.h"
TEMPLATE = "tests/fixtures/fog_vent_consumer.cpp.in"
VARIANTS = ("original", "deleted_consumer", "inverted_consumer")
SELECTIONS = ("snapshot", "legacy")
# These are synthetic inputs, not a telemetry or production admission schema.
INPUTS = {
    "now_ms": "uint32 monotonic milliseconds; wrap is deliberate in one scenario",
    "experiment_active": "bool; chooses snapshot or legacy storage",
    "snapshot/legacy.sw_fog_closes_vent": "bool; unselected value is opposite",
    "snapshot/legacy.min_{heat,fan,vent,fog}_{on,off}_s": "eight wire-valid durations; selected 60, unselected 120 seconds",
    "mode": "actual Mode enum",
    "relay_out.fog": "bool; upstream controller request used by actual exemption helper",
    "willFog": "bool; request entering the extracted safety and fog consumer",
    "willVent": "bool; desired vent before actual relay application",
    "vent_rly.state/fog_rly.state": "bool; actual pre-apply storage readback, never same-row telemetry output",
    "RelayMeta[4,5]": "persistent last_on_ms,last_off_ms,on_stamp_ms,runtime_ms; initialized once per process and advanced by actual relay code",
    "manual_fog_force/fan_requires_vent": "bool; upstream authority decisions, not reconstructed here",
    "controller_time_valid": "bool",
    "leak_block/occupancy_moisture_block/irrigation_water_conflict/climate_water_budget_block/mister_volume_hard_block/fert_master_on": "six explicit bool upstream safety decisions",
    "sensor_fault_relay_lock": "actual extracted expression mode == SENSOR_FAULT",
}
SCENARIOS = (
    "gate_true_open_close",
    "gate_true_open_open",
    "gate_false_open_close",
    "gate_false_open_open",
    "gate_true_closed_close",
    "gate_true_closed_open",
    "gate_false_closed_close",
    "gate_false_closed_open",
    "ventilate_requested",
    "safety_cool_requested",
    "ventilate_manual_only",
    "unrelated_mode",
    "preblocked_true_open",
    "preblocked_false_open",
    "preblocked_true_closed",
    "preblocked_false_closed",
    "minimum_on",
    "minimum_on_wrap",
    "minimum_off",
    "manual_force_on",
    "blocked_clock",
    "blocked_leak",
    "blocked_occupancy",
    "blocked_irrigation",
    "blocked_budget",
    "blocked_hard_limit",
    "blocked_fertilizer",
    "sensor_fault_preblocked",
)
NEGATIVE_WITNESSES = ("gate_true_open_close", "gate_true_open_open")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique(text: str, marker: str) -> int:
    if text.count(marker) != 1:
        raise ValueError(f"missing or ambiguous source marker: {marker!r}")
    return text.index(marker)


def _span(text: str, start: str, end: str) -> tuple[int, int]:
    first, last = _unique(text, start), _unique(text, end)
    if last <= first:
        raise ValueError("source markers out of order")
    return first, last


def _block(text: str, start: str) -> tuple[int, int]:
    first = _unique(text, start)
    opening = text.index("{", first)
    depth = 1
    for index in range(opening + 1, len(text)):
        depth += (text[index] == "{") - (text[index] == "}")
        if depth == 0:
            return first, index + 1
    raise ValueError(f"unclosed source block: {start!r}")


def _statement(text: str, start: str) -> tuple[int, int]:
    first = _unique(text, start)
    return first, text.index(";", first) + 1


def extract_fragments(root: Path = ROOT, controls_text: str | None = None) -> dict:
    root = Path(root).resolve()
    controls = (root / CONTROLS).read_text() if controls_text is None else controls_text
    logic = (root / LOGIC).read_text()
    spans = {
        "policy_selection": _span(controls, "const auto pol =", "// Journal-visible"),
        "dwell_constants": _span(controls, "const uint32_t MIN_HEAT_ON_MS", "const float    EXTRA_COOL_S3_DELTA_F"),
        "relay_helpers": _span(controls, "struct RelayMeta {", "/**************** 3"),
        "sensor_fault_lock": _statement(controls, "const bool sensor_fault_relay_lock = mode == SENSOR_FAULT"),
        "vent_readback": _statement(controls, "const bool vent_is_open = id(vent_rly)->state"),
        "fog_assist": _statement(controls, "const bool open_vent_fog_assist ="),
        "safety_suppression": _block(
            controls,
            "if (!controller_time_valid || leak_block || occupancy_moisture_block || irrigation_water_conflict || climate_water_budget_block || mister_volume_hard_block || fert_master_on)",
        ),
        "fog_consumer": _block(
            controls,
            "if (polb(verdify_policy::kPF_sw_fog_closes_vent, id(fog_closes_vent)) && vent_is_open && !open_vent_fog_assist)",
        ),
        "vent_apply": _statement(controls, "set_relay(R[5], willVent, fan_requires_vent, sensor_fault_relay_lock)"),
        "fog_apply": _span(controls, "set_relay(\n              R[4],", "/**************** 12"),
    }
    # Refuse reordering that would change pre-apply readback semantics.
    order = [spans[name][0] for name in spans]
    if order != sorted(order):
        raise ValueError("consumer source order changed")
    fragments = {}
    for name, (first, last) in spans.items():
        value = controls[first:last]
        fragments[name] = {
            "source_path": CONTROLS,
            "first_line": controls.count("\n", 0, first) + 1,
            "last_line": controls.count("\n", 0, last - 1) + 1,
            "sha256": sha256(value.encode()),
            "text": value,
        }
    first, last = _block(logic, "inline bool open_vent_fog_assist_permitted(")
    value = logic[first:last]
    fragments["actual_helper"] = {
        "source_path": LOGIC,
        "first_line": logic.count("\n", 0, first) + 1,
        "last_line": logic.count("\n", 0, last - 1) + 1,
        "sha256": sha256(value.encode()),
        "text": value,
    }
    files = {CONTROLS: sha256(controls.encode())}

    def include(path: Path) -> None:
        path = path.resolve()
        relative = str(path.relative_to(root))
        if relative in files:
            return
        content = path.read_bytes()
        files[relative] = sha256(content)
        for header in re.findall(r'^\s*#include\s+"([^"]+)"', content.decode(), re.M):
            include(path.parent / header)

    include(root / LOGIC)
    include(root / "firmware/lib/policy_rom_baseline_generated.h")
    return {
        "format": "verdify-fog-vent-consumer-v1",
        "fragments": fragments,
        "source_files": [{"path": path, "sha256": digest} for path, digest in sorted(files.items())],
        "inputs": INPUTS,
        "scenario_names": list(SCENARIOS),
        "selection_modes": list(SELECTIONS),
        "negative_witnesses": list(NEGATIVE_WITNESSES),
        "scope": "synthetic single-consumer scenarios; upstream inputs explicit; actual helper and relay code",
        "corpus_prefix_coverage": "27/48 UNPROVEN; no row-wise credit added",
        "physical_acceptance": False,
        "firmware_changed": False,
    }


def generate_cpp(root: Path = ROOT, variant: str = "original") -> tuple[str, dict]:
    if variant not in VARIANTS:
        raise ValueError("unknown causal variant")
    root = Path(root)
    manifest = extract_fragments(root)
    source = (root / TEMPLATE).read_text()
    for name, item in manifest["fragments"].items():
        if name == "actual_helper":  # Compiled by including its actual full header.
            continue
        fragment = item["text"]
        if name == "fog_consumer" and variant == "deleted_consumer":
            fragment = "/* causal negative: extracted consumer deleted */"
        elif name == "fog_consumer" and variant == "inverted_consumer":
            if fragment.count("if (polb(") != 1:
                raise ValueError("ambiguous consumer inversion")
            fragment = fragment.replace("if (polb(", "if (!polb(", 1)
        marker = "@@" + name + "@@"
        if source.count(marker) != 1:
            raise ValueError(f"missing or ambiguous template marker: {name}")
        source = source.replace(marker, fragment)
    if "@@" in source:
        raise ValueError("unresolved template marker")
    manifest["variant"] = variant
    manifest["template_sha256"] = sha256((root / TEMPLATE).read_bytes())
    manifest["generated_cpp_sha256"] = sha256(source.encode())
    return source, manifest
