"""Read-only, same-response Shelly channel evidence (#981; HA source contract).

Only Modbus function 04 is emitted. A separate identity read verifies the meter;
clock, error and signed power are decoded together from one 40-register response.
This is diagnostic source freshness, never circuit/scientific commissioning.
"""

import socket
import struct
import time
from datetime import UTC, datetime
from math import isfinite

from verdify_schemas.telemetry import EnergySample

try:
    from shelly_energy import MAX_AGE_SECONDS, POWER_ENTITIES
except ImportError:  # package imports for bounded unit tests
    from .shelly_energy import MAX_AGE_SECONDS, POWER_ENTITIES

REVISION = "shelly_modbus_source_v2"
EXPECTED_MAC = "AC15186DAAFC"
MAX_CHANNEL_SKEW_SECONDS = 5


def _receive_exact(connection, count, deadline):
    data = bytearray()
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Modbus response deadline exceeded")
        connection.settimeout(remaining)
        chunk = connection.recv(count - len(data))
        if not chunk:
            raise ValueError("truncated Modbus response")
        data.extend(chunk)
    return bytes(data)


def _read_input(connection, transaction, offset, count):
    # Constants are bounded by the two exact qualified reads, never user input.
    if (offset, count) not in {(0, 6), (2000, 40)}:
        raise ValueError("unqualified Modbus register request")
    deadline = time.monotonic() + 3
    connection.sendall(struct.pack(">HHHBBHH", transaction, 0, 6, 1, 4, offset, count))
    header = _receive_exact(connection, 7, deadline)
    received, protocol, length, unit = struct.unpack(">HHHB", header)
    if received != transaction or protocol != 0 or unit != 1 or length != 3 + 2 * count:
        raise ValueError("invalid Modbus response identity or length")
    body = _receive_exact(connection, length - 1, deadline)
    if body[:2] != bytes([4, 2 * count]):
        raise ValueError("invalid Modbus input-register response")
    return struct.unpack(">" + "H" * count, body[2:])


def read_registers(host):
    """Two exact read-only requests, 3s socket bound, no retries or HA cache."""
    if not host:
        raise ValueError("source endpoint unconfigured")
    with socket.create_connection((host, 502), timeout=3) as connection:
        connection.settimeout(3)
        identity = _read_input(connection, 1, 0, 6)
        channels = _read_input(connection, 2, 2000, 40)
    return identity, channels


def _number(registers, offset, kind):
    return struct.unpack(kind, struct.pack(">HH", registers[offset + 1], registers[offset]))[0]


def build_sample(identity, registers, observed_at, previous=None):
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("energy observation requires an aware timestamp")
    values = []
    times = []
    qualities = []
    valid_identity = False
    try:
        mac = b"".join(struct.pack("<H", word) for word in identity).decode("ascii").upper()
        valid_identity = len(identity) == 6 and mac == EXPECTED_MAC and len(registers) == 40
    except (TypeError, ValueError, UnicodeError, struct.error):
        pass
    for index in range(2):
        value, source, quality = None, None, "invalid"
        if valid_identity:
            try:
                offset = index * 20
                source = datetime.fromtimestamp(_number(registers, offset, ">I"), UTC)
                power = _number(registers, offset + 7, ">f")
                age = (observed_at - source).total_seconds()
                prior = (previous or (None, None))[index]
                if registers[offset + 2] != 0:
                    quality = "unavailable"
                elif not isfinite(power):
                    quality = "nonfinite"
                else:
                    value = power
                    quality = "future" if age < 0 else "stale" if age >= MAX_AGE_SECONDS else "ok"
                    if prior is not None and source < prior:
                        quality = "invalid"
            except (ValueError, OverflowError, TypeError, IndexError, struct.error):
                quality = "invalid"
        values.append(value)
        times.append(source)
        qualities.append(quality)
    if all(times) and abs((times[0] - times[1]).total_seconds()) > MAX_CHANNEL_SKEW_SECONDS:
        qualities = ["invalid", "invalid"]
    total = sum(values) if qualities == ["ok", "ok"] else None
    return EnergySample(
        ts=observed_at,
        measurement_revision=REVISION,
        watts_total=total,
        ch0_power_w=values[0],
        ch1_power_w=values[1],
        ch0_source_ts=times[0],
        ch1_source_ts=times[1],
        ch0_entity_id=POWER_ENTITIES[0],
        ch1_entity_id=POWER_ENTITIES[1],
        ch0_quality=qualities[0],
        ch1_quality=qualities[1],
    )


def unavailable_sample(observed_at):
    """A failed read is an explicit gap, with no cached value or refreshed clock."""
    return EnergySample(
        ts=observed_at,
        measurement_revision=REVISION,
        ch0_entity_id=POWER_ENTITIES[0],
        ch1_entity_id=POWER_ENTITIES[1],
        ch0_quality="unavailable",
        ch1_quality="unavailable",
    )
