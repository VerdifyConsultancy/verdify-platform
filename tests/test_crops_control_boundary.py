"""Plantings never steer the greenhouse (#1327, migration 293): the plant-agnostic boundary.

A planting is a record. These checks fail when a control path starts reading plantings or the planting service starts
writing control state:

- No database function defined by db/migrations reads crop_occupancy, shelf_water_lines or v_crop_planting_current.
- The functions that read public.crops are exactly the classified set below. One is a control path today:
  fn_lighting_policy sets the grow-light window from the active crop with the highest DLI target (the Vandas: DLI 14,
  07:00-17:00). It is unchanged by 293 and is a decision for Jason; a new reader must be classified here.
- The dispatcher, band, policy and irrigation tasks never name a planting table.
- verdify_services.crops writes only planting records, never a setting, band, target or control column.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db/migrations"

_CREATE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:(\w+)\.)?(\w+)\s*\(.*?\bAS\s+(\$\w*\$)(.*?)\3", re.S | re.I
)
_DROP = re.compile(
    r"DROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?((?:\w+\.)?\w+(?:\s*\([^)]*\))?(?:\s*,\s*(?:\w+\.)?\w+(?:\s*\([^)]*\))?)*)",
    re.I,
)
_PLANTINGS = re.compile(r"\b(crop_occupancy|shelf_water_lines|v_crop_planting_current)\b")
_CROPS = re.compile(r"\bcrops\b")

# Every migration-defined function that reads public.crops, and why it may.
CROPS_READERS = {
    # Control: the grow-light window follows the active crops' DLI targets. Unchanged by 293; decision for Jason.
    "public.fn_lighting_policy": "control (decision for Jason)",
    # Control fallback only when the house crop_band_anchors are missing; it joins the orchid catalog row.
    "public.fn_center_band_setpoints": "control fallback (anchors missing)",
    # Grading and health reads; they steer nothing.
    "public.fn_zone_band": "grading",
    "public.fn_operational_health": "health score",
    # Migration 255's fixed-panel crop-assignment lineage triggers.
    "public.fn_capture_crop_assignment_revision": "lineage",
    "public.fn_guard_fixed_panel_source_history": "lineage",
    "public.fn_reject_crop_assignment_history_mutation": "lineage",
}

CONTROL_TASKS = (
    "ingestor/tasks/dispatcher.py",
    "ingestor/tasks/band_anchors.py",
    "ingestor/tasks/policy_arbiter.py",
    "ingestor/tasks/policy_delivery.py",
    "ingestor/tasks/irrigation.py",
    "ingestor/tasks/c1_overlay.py",
    "ingestor/tasks/bounded_reconcile.py",
)


def _live_functions() -> dict[str, str]:
    """The newest body of each function db/migrations creates and has not dropped, in file order."""
    live: dict[str, str] = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(errors="ignore")
        events = [(m.start(), "create", m) for m in _CREATE.finditer(text)]
        events += [(m.start(), "drop", m) for m in _DROP.finditer(text)]
        for _, kind, match in sorted(events, key=lambda event: event[0]):
            if kind == "create":
                live[f"{(match.group(1) or 'public').lower()}.{match.group(2).lower()}"] = match.group(4)
            else:
                for name in re.split(r"\s*,\s*(?![^()]*\))", match.group(1)):
                    bare = re.sub(r"\s*\(.*", "", name.strip()).lower()
                    live.pop(bare if "." in bare else f"public.{bare}", None)
    return live


def test_no_database_function_reads_plantings():
    readers = sorted(name for name, body in _live_functions().items() if _PLANTINGS.search(body))
    assert readers == []


def test_the_functions_that_read_crops_are_classified():
    readers = {name for name, body in _live_functions().items() if _CROPS.search(body)}
    assert readers == set(CROPS_READERS), (
        "a function reading crops was added or removed; classify it here, and a control path that reads plantings "
        "is a decision for Jason"
    )


def test_fn_lighting_policy_still_follows_the_active_crops_unchanged():
    body = _live_functions()["public.fn_lighting_policy"]
    assert "is_active" in body and "target_dli" in body


def test_control_tasks_never_name_a_planting_table():
    for rel in CONTROL_TASKS:
        text = (ROOT / rel).read_text()
        assert not re.search(r"\b(crops|crop_occupancy|v_crop_planting_current|shelf_water_lines)\b", text), rel


def test_the_planting_service_writes_records_only():
    text = (ROOT / "verdify_services/crops.py").read_text().split('"""', 2)[2]  # the code after the docstring
    written = set(re.findall(r"(?:INSERT\s+INTO|UPDATE)\s+public\.(\w+)", text))
    assert written == {"crops", "crop_occupancy", "crop_events", "harvests"}
    for name in ("crop_target_profiles", "crop_band_anchors", "desired_state", "setpoint_changes", "tunable"):
        assert name not in text, name
    inserted = re.search(r"columns = \{(.*?)\n        \}", text, re.S).group(1)
    for control in ("target_dli", "target_vpd_low", "target_vpd_high", "base_temp_f"):
        assert control not in inserted
