"""Guard: outdoor weather has one source, the ESP32 Tempest UDP listener (VD-48, HA-07).

The Home Assistant copy of the Tempest data is gone: the ingestor's ``tempest_sync``
task, its ``_TEMPEST_MAP`` and the legacy ``scripts/tempest-sync.py``. The ESP32
listens for the Tempest's UDP broadcasts on port 50222 and the ingestor maps the
``tempest_*`` entities from the native API. This file keeps its name because the
generated ``platform-tests`` check in ``.agent-fleet/ci.yaml`` lists it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR = REPO_ROOT / "ingestor"
if str(INGESTOR) not in sys.path:
    sys.path.insert(0, str(INGESTOR))


def _tasks_package_sources() -> dict[str, str]:
    return {path.name: path.read_text() for path in sorted((INGESTOR / "tasks").glob("*.py"))}


def test_no_tempest_sync_task_is_defined_imported_or_scheduled():
    for name, src in _tasks_package_sources().items():
        defined = {
            node.name for node in ast.walk(ast.parse(src)) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert "tempest_sync" not in defined, name

    tree = ast.parse((INGESTOR / "ingestor.py").read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "tempest_sync" not in imported
    scheduled = {
        node.elts[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Tuple)
        and node.elts
        and isinstance(node.elts[0], ast.Constant)
        and isinstance(node.elts[0].value, str)
    }
    assert "tempest_sync" not in scheduled


def test_no_home_assistant_tempest_map():
    for name, src in _tasks_package_sources().items():
        assert "_TEMPEST_MAP" not in src, name
        assert "sensor.panorama_" not in src, name


def test_legacy_tempest_script_is_gone():
    assert not (REPO_ROOT / "scripts" / "tempest-sync.py").exists()


def test_outdoor_columns_come_from_the_esp32_tempest_listener():
    import entity_map

    assert entity_map.CLIMATE_MAP["tempest_temperature"] == "outdoor_temp_f"
    assert entity_map.CLIMATE_MAP["tempest_humidity"] == "outdoor_rh_pct"
    firmware = (REPO_ROOT / "firmware" / "greenhouse" / "external_sensors.yaml").read_text()
    assert "listen_port: 50222" in firmware
