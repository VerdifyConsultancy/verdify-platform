"""Qualified same-response source conversion and read-only wire boundary; no device I/O."""

import struct
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from ingestor import shelly_modbus as energy

NOW = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
MAC = struct.unpack("<6H", b"AC15186DAAFC")


def block(a=0, b=-123.5, at=NOW):
    result = [0] * 40
    for index, power in enumerate([a, b]):
        offset = index * 20
        hi, lo = struct.unpack(">HH", struct.pack(">I", int(at.timestamp())))
        result[offset : offset + 2] = [lo, hi]
        hi, lo = struct.unpack(">HH", struct.pack(">f", power))
        result[offset + 7 : offset + 9] = [lo, hi]
    return result


def test_authentic_zero_advances_signed_values_and_revision_stays_separate():
    first = energy.build_sample(MAC, block(), NOW)
    later = NOW + timedelta(seconds=300)
    second = energy.build_sample(MAC, block(at=later), later, (first.ch0_source_ts, first.ch1_source_ts))
    assert first.ch0_power_w == second.ch0_power_w == 0
    assert first.watts_total == second.watts_total == -123.5
    assert second.ch0_source_ts > first.ch0_source_ts
    assert second.measurement_revision == "shelly_modbus_source_v2"
    assert second.kwh_today is second.watts_heat is second.watts_fans is second.watts_other is None


@pytest.mark.parametrize("delta,quality", [(0, "ok"), (-299, "ok"), (-300, "stale"), (1, "future")])
def test_clock_negative_boundaries(delta, quality):
    sample = energy.build_sample(MAC, block(at=NOW + timedelta(seconds=delta)), NOW)
    assert sample.ch0_quality == sample.ch1_quality == quality
    assert (sample.watts_total is not None) == (quality == "ok")


@pytest.mark.parametrize("invalid", ["identity", "truncated", "error", "nan", "infinity", "skew", "retrograde"])
def test_bad_source_cannot_become_usable(invalid):
    identity, registers, previous = MAC, block(), None
    if invalid == "identity":
        identity = (0,) * 6
    if invalid == "truncated":
        registers = registers[:-1]
    if invalid == "error":
        registers[2] = 1
    if invalid in ["nan", "infinity"]:
        registers = block(a=float("nan") if invalid == "nan" else float("inf"))
    if invalid == "skew":
        registers[20:22] = block(at=NOW - timedelta(seconds=6))[20:22]
    if invalid == "retrograde":
        previous = (NOW + timedelta(seconds=1), NOW)
    sample = energy.build_sample(identity, registers, NOW, previous)
    assert sample.watts_total is None
    assert sample.ch0_quality != "ok" or sample.ch1_quality != "ok"


def test_unavailable_never_refreshes_an_old_source_clock():
    sample = energy.unavailable_sample(NOW)
    assert sample.ch0_source_ts is sample.ch1_source_ts is sample.watts_total is None
    assert sample.ch0_quality == sample.ch1_quality == "unavailable"


class Connection:
    def __init__(self, data):
        self.data, self.requests = data, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        assert 0 < timeout <= 3

    def sendall(self, payload):
        self.requests.append(payload)

    def recv(self, size):
        out, self.data = self.data[: min(size, 3)], self.data[min(size, 3) :]
        return out


def response(transaction, words, unit=1, function=4):
    body = bytes([function, len(words) * 2]) + struct.pack(">" + "H" * len(words), *words)
    return struct.pack(">HHHB", transaction, 0, len(body) + 1, unit) + body


def test_wire_only_emits_the_two_qualified_function_four_reads():
    connection = Connection(response(1, MAC) + response(2, block()))
    with patch("socket.create_connection", return_value=connection):
        identity, registers = energy.read_registers("fixture.invalid")
    assert identity == MAC and tuple(block()) == registers
    assert [struct.unpack(">HHHBBHH", x) for x in connection.requests] == [
        (1, 0, 6, 1, 4, 0, 6),
        (2, 0, 6, 1, 4, 2000, 40),
    ]


@pytest.mark.parametrize("payload", [b"", response(2, MAC), response(1, MAC, unit=2), response(1, MAC, function=3)])
def test_wrong_or_incomplete_wire_response_fails_closed(payload):
    connection = Connection(payload)
    with patch("socket.create_connection", return_value=connection), pytest.raises(ValueError):
        energy.read_registers("fixture.invalid")


def test_unqualified_register_range_cannot_be_requested():
    connection = Connection(b"")
    with pytest.raises(ValueError):
        energy._read_input(connection, 1, 100, 1)
    assert not connection.requests
