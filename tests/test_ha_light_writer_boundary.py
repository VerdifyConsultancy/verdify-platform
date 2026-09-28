"""The HA light route must not become a second ESP32 climate writer (#399)."""

from __future__ import annotations

import copy
import json
import runpy
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def light_writer(monkeypatch):
    module = runpy.run_path(str(ROOT / "scripts/setpoint-server.py"), run_name="_test_light_writer")
    call = module["ha_call"]
    monkeypatch.setitem(call.__globals__, "_ha_token", "fixture-token")
    return module, call


def test_authorized_lutron_route_reaches_only_canonical_switches(light_writer, monkeypatch):
    module, call = light_writer
    assert {name: config["ha_entity"] for name, config in module["LIGHTS"].items()} == {
        "main": "switch.greenhouse_main",
        "grow": "switch.greenhouse_grow",
    }
    assert module["AUTHORIZED_LIGHT_ENTITIES"] == frozenset({"switch.greenhouse_main", "switch.greenhouse_grow"})

    sent = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        sent.append((request.full_url, json.loads(request.data), timeout))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for entity in module["AUTHORIZED_LIGHT_ENTITIES"]:
        for service in ("turn_on", "turn_off"):
            assert call(service, entity) is True
    assert {url for url, _, _ in sent} == {
        "http://192.168.30.107:8123/api/services/switch/turn_on",
        "http://192.168.30.107:8123/api/services/switch/turn_off",
    }
    assert {body["entity_id"] for _, body, _ in sent} == module["AUTHORIZED_LIGHT_ENTITIES"]


@pytest.mark.parametrize(
    ("service", "entity"),
    [
        ("turn_on", "switch.greenhouse_heat_1"),  # ESP32 climate actuator
        ("turn_off", "switch.greenhouse_grow_light_main"),  # ESPHome proxy
        ("turn_on", "light.greenhouse_main"),  # Alarm.com wrapper
        ("toggle", "switch.greenhouse_main"),
    ],
)
def test_competing_device_or_unapproved_entity_never_reaches_ha(light_writer, monkeypatch, service, entity):
    _, call = light_writer

    def forbidden_urlopen(*_args, **_kwargs):
        pytest.fail("out-of-scope HA command reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_urlopen)
    assert call(service, entity) is False


def test_firmware_light_calls_share_lutron_targets_without_climate_service_calls():
    hardware = yaml.safe_load((ROOT / "firmware/greenhouse/hardware.yaml").read_text())
    expected = {"main": "switch.greenhouse_main", "grow": "switch.greenhouse_grow"}
    for circuit, entity in expected.items():
        switch = next(item for item in hardware["switch"] if item.get("id") == f"grow_light_{circuit}")
        for action in ("on", "off"):
            calls = [
                step["homeassistant.service"]
                for step in switch[f"turn_{action}_action"]
                if "homeassistant.service" in step
            ]
            assert calls == [{"service": f"switch.turn_{action}", "data": {"entity_id": entity}}]


def test_both_prod_device_paths_keep_one_recreate_pod():
    # A negative manifest fixture catches accidental second pods or a rolling
    # overlap even if the live deployment currently looks healthy.
    import subprocess

    rendered = subprocess.run(
        ["kustomize", "build", "deploy/k8s/overlays/prod"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    deployments = {
        item["metadata"]["name"]: item
        for item in yaml.safe_load_all(rendered)
        if isinstance(item, dict) and item.get("kind") == "Deployment"
    }

    def assert_one_recreate(deployment):
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}

    for name in ("verdify-ingestor", "verdify-setpoint-server"):
        assert_one_recreate(deployments[name])
    competitor = copy.deepcopy(deployments["verdify-setpoint-server"])
    competitor["spec"]["replicas"] = 2
    with pytest.raises(AssertionError):
        assert_one_recreate(competitor)
    competitor["spec"]["replicas"] = 1
    competitor["spec"]["strategy"] = {"type": "RollingUpdate"}
    with pytest.raises(AssertionError):
        assert_one_recreate(competitor)
