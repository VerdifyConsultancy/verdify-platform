"""firmware-v2 anchors-mode: DB→device band sync via the set_band_anchor service.

Unit coverage for the settable-anchor path
(docs/design/firmware-v2-settable-anchors-2026-06-15.md): the dispatcher writes
the on-chip NVS band/zone anchors through a single heap-safe API service rather
than 56 number entities. These tests need no DB or device.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

INGESTOR_PATH = str(Path(__file__).resolve().parent.parent / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

import esp32_push  # noqa: E402
import shared  # noqa: E402
from tasks import band_anchors  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_esp32(monkeypatch):
    monkeypatch.setattr(shared, "esp32", {"client": None, "keys": {}, "services": {}}, raising=False)
    monkeypatch.setattr(shared, "recently_pushed", {}, raising=False)
    monkeypatch.setattr(shared, "recently_pushed_values", {}, raising=False)
    monkeypatch.setattr(shared, "cfg_readback", {}, raising=False)
    yield


def test_anchors_supported_true_when_service_present():
    shared.esp32["services"] = {"set_band_anchor": object()}
    shared.esp32["keys"] = {}  # no per-anchor number entities — service is enough
    assert band_anchors.anchors_supported() is True


def test_anchors_supported_false_without_service_or_anchor_entities():
    shared.esp32["services"] = {}
    shared.esp32["keys"] = {"some_other_entity": 1}
    assert band_anchors.anchors_supported() is False


def test_service_name_contract_matches_firmware_and_push():
    # The dispatcher push path and the support check must agree on the service id.
    assert band_anchors.BAND_ANCHOR_SERVICE == esp32_push._BAND_ANCHOR_SERVICE == "set_band_anchor"


def test_every_anchor_param_is_band_owned_and_synced():
    # All 56 sync params route to the service (object_id == param name).
    assert len(band_anchors.ANCHOR_SYNC_PARAMS) == 56
    assert "band_vpd_target_mid" in band_anchors.ANCHOR_SYNC_PARAMS
    assert "zone_vpd_target_center_mid" in band_anchors.ANCHOR_SYNC_PARAMS


@pytest.mark.asyncio
async def test_push_to_esp32_routes_service_via_execute_service(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "is_shadow_mode", lambda: False, raising=False)
    monkeypatch.setattr(shared, "writer_lease_held", lambda: True, raising=False)

    async def _no_pace():
        return None

    monkeypatch.setattr(esp32_push, "_pace_command", _no_pace)

    svc = object()
    client = MagicMock()
    client.execute_service = MagicMock(return_value=None)
    shared.esp32["client"] = client
    shared.esp32["keys"] = {}  # no entity key for the anchor — service path must not need one
    shared.esp32["services"] = {"set_band_anchor": svc}

    pushed = await esp32_push.push_to_esp32([("band_vpd_target_mid", 1.05, "service")])

    assert pushed == 1
    client.execute_service.assert_called_once_with(svc, {"anchor_key": "band_vpd_target_mid", "value": 1.05})
    # never falls through to number_command for a service change
    client.number_command.assert_not_called()
    assert shared.recently_pushed_values["band_vpd_target_mid"] == 1.05


@pytest.mark.asyncio
async def test_push_to_esp32_service_unavailable_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "is_shadow_mode", lambda: False, raising=False)
    monkeypatch.setattr(shared, "writer_lease_held", lambda: True, raising=False)

    async def _no_pace():
        return None

    monkeypatch.setattr(esp32_push, "_pace_command", _no_pace)

    client = MagicMock()
    shared.esp32["client"] = client
    shared.esp32["keys"] = {}
    shared.esp32["services"] = {}  # service not yet enumerated (pre-OTA / pre-reconnect)

    pushed = await esp32_push.push_to_esp32([("band_vpd_target_mid", 1.05, "service")])

    assert pushed == 0
    client.execute_service.assert_not_called()
