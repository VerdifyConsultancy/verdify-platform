"""#1184: the PAR quantum sensor on the probe bus, and its absent-meter behavior.

The meter is telemetry only. Until it is fitted (or while it is silent) the
controller skips its polls, the sensor reads unavailable after its timeout, and
the post-OTA sensor sweep warns instead of failing, so an unfitted meter never
rolls back a flash.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARDWARE = (ROOT / "firmware/greenhouse/hardware.yaml").read_text()
SENSORS = (ROOT / "firmware/greenhouse/sensors.yaml").read_text()
SWEEP = (ROOT / "scripts/sensor-health-sweep.sh").read_text()


def _controllers() -> list[dict[str, str]]:
    block = HARDWARE[HARDWARE.index("\nmodbus_controller:\n") : HARDWARE.index("\nswitch:\n")]
    entries = re.split(r"\n  - id: ", block)[1:]
    out = []
    for entry in entries:
        lines = entry.splitlines()
        fields = {"id": lines[0].strip()}
        for line in lines[1:]:
            match = re.match(r"    (\w+): (\S+)", line)
            if match:
                fields[match.group(1)] = match.group(2)
        out.append(fields)
    return out


def _sensor(entity_id: str) -> str:
    start = SENSORS.index(f"    id: {entity_id}\n")
    end = SENSORS.find("\n  - platform:", start)
    return SENSORS[start:end]


def test_par_probe_holds_address_10_alone_on_the_probe_bus():
    controllers = _controllers()
    par = [c for c in controllers if c["id"] == "par_probe"]
    assert len(par) == 1
    assert par[0]["address"] == "10"
    assert par[0]["modbus_id"] == "modbus_main"
    assert par[0]["update_interval"] == "10s"
    # A silent meter is retried about every 2 minutes and never crowds the bus.
    assert par[0]["offline_skip_updates"] == "12"
    on_bus = [c["address"] for c in controllers if c.get("modbus_id") == "modbus_main"]
    assert on_bus.count("10") == 1
    assert len(on_bus) == len(set(on_bus))


def test_canopy_ppfd_reads_the_par_probe_and_times_out_when_absent():
    sensor = _sensor("canopy_ppfd")
    assert 'name: "Canopy PPFD"' in sensor
    assert "modbus_controller_id: par_probe" in sensor
    assert "register_type: holding" in sensor
    assert "address: 0x0000" in sensor
    assert "value_type: U_WORD" in sensor
    assert 'unit_of_measurement: "µmol/m²/s"' in sensor
    assert "- timeout: 300s" in sensor


def test_no_control_logic_reads_the_par_meter():
    for path in [
        ROOT / "firmware/greenhouse/controls.yaml",
        ROOT / "firmware/greenhouse/tunables.yaml",
        ROOT / "firmware/greenhouse/globals.yaml",
        *sorted((ROOT / "firmware/lib").glob("*.h")),
    ]:
        assert "canopy_ppfd" not in path.read_text(), path
        assert "par_probe" not in path.read_text(), path


def test_sensor_sweep_names_address_10_and_only_warns():
    case = SWEEP[SWEEP.index('case "$addr" in') :]
    case = case[: case.index("esac")]
    arm = case[case.index("10)") : case.index(";;", case.index("10)"))]
    assert "par_probe" in arm
    assert "warn " in arm
    assert "fail " not in arm
    assert "continue" in arm
    assert "addresses 1-10" in SWEEP
