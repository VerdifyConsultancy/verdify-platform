"""Drift guards for the settings rules verdify_schemas shares with the ingestor (VD-67).

The api and MCP images carry verdify_schemas but not ingestor/, and the ingestor never imports
verdify_services, so the services keep their own copies of two ingestor rules. These tests rebuild the
ingestor's versions and keep the copies equal. They need no database, unlike test_drift_guards.py, whose
module skips without one, so they run in every CI pass.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from verdify_schemas.tunable_registry import REGISTRY, SWITCH_READBACK_EQUIPMENT

ROOT = Path(__file__).resolve().parents[2]


def _entity_map():
    for path in (str(ROOT), str(ROOT / "ingestor")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import entity_map  # noqa: PLC0415

    return entity_map


def _common_assignment(name: str) -> ast.expr:
    """The expression ingestor/tasks/_common.py assigns to ``name`` (read, not imported: it pulls in the tasks)."""
    tree = ast.parse((ROOT / "ingestor" / "tasks" / "_common.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    raise AssertionError(f"ingestor/tasks/_common.py no longer assigns {name}")


def test_switch_readback_equipment_equals_the_ingestor_switch_confirm_map():
    """The settings services confirm a switch from the equipment_state mirror the ingestor confirms it from.

    ingestor/tasks/_common.py builds SWITCH_CONFIRM_EQUIPMENT from entity_map.SWITCH_TO_ENTITY and
    EQUIPMENT_SWITCH_MAP; this checks it still does, rebuilds it the same way and keeps
    SWITCH_READBACK_EQUIPMENT equal to it.
    """
    entity_map = _entity_map()
    rebuilt = {
        param: entity_map.EQUIPMENT_SWITCH_MAP[entity_id]
        for param, entity_id in entity_map.SWITCH_TO_ENTITY.items()
        if entity_id in entity_map.EQUIPMENT_SWITCH_MAP
    }
    assert ast.unparse(_common_assignment("SWITCH_CONFIRM_EQUIPMENT")) == (
        "{param: EQUIPMENT_SWITCH_MAP[entity_id] for param, entity_id in SWITCH_TO_ENTITY.items() "
        "if entity_id in EQUIPMENT_SWITCH_MAP}"
    )
    assert SWITCH_READBACK_EQUIPMENT == rebuilt
    assert all(REGISTRY[param].kind == "switch" for param in SWITCH_READBACK_EQUIPMENT)
