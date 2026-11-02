"""Read-only YINMIK hydroponic meter over the Tuya local protocol (VD-70, #1183).

Replaces Home Assistant's LocalTuya relay of the meter. One short session per
poll: connect to the meter's reserved address on TCP 6668 (5 s timeout, no
retry loop, no persistent socket, tinytuya debug off), ``status()``, close.
The status query (after the session-key handshake of protocol 3.4+) is the
only command sent; no data point is ever written. tinytuya's "device22"
auto-detection is switched off, because on a "data unvalid" answer it would
resend the query as a CONTROL_NEW frame carrying a null DP 1.

The data points (DPs) follow the meter's Tuya model, "YINMIK Water Quality
Tester" (the name Home Assistant's cloud Tuya integration gives this device):

    DP 10  ph                  scale 2     621  -> pH 6.21
    DP 11  conductivity_value  mS/cm, scale 3, so the raw integer is µS/cm
    DP 1   tds_in              ppm
    DP 2   temp_current        degC, scale 1 (236 -> 23.6 degC -> 74.48 degF)
    DP 7   battery_percentage  %
    DP 12  orp_value           -1 on this SKU (no ORP probe): hydro_orp_mv is NULL
    DP 107 phmaxlimit          the pH alarm limit (1400 = 14.00), a setting

Home Assistant's 2026-04-18 template corrections were fitted to one app
reading with the DPs crossed: they read DP 107 as EC (x1.77, so hydro_ec_us_cm
has been 2478 ever since), DP 11 as pH (/400) and DP 10 as TDS (x2). Decoded
as the meter defines them, the same raw values match that app reading (pH 6.21
against 6.22, EC 2484 against 2.48 mS/cm).

The device id and local key are secrets. They arrive as files from the
optional ``verdify-hydro-probe`` Secret, are re-read on every poll, so a reseal
applies without a restart, and never reach a log line.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from verdify_schemas.external import TuyaDeviceStatus

KEY_DIR = Path("/etc/verdify/hydro-probe")
HOST_ENV = "VERDIFY_HYDRO_PROBE_HOST"
VERSION_ENV = "VERDIFY_HYDRO_PROBE_TUYA_VERSION"
TUYA_PORT = 6668
CONNECT_TIMEOUT_S = 5
# 3.2 is left out: for it tinytuya probes the device from its constructor.
SUPPORTED_VERSIONS = frozenset({"3.1", "3.3", "3.4", "3.5"})

# DP id -> (climate column, divisor, decimal places): round(raw / divisor, places).
DP_MAP = {
    "10": ("hydro_ph", 100, 2),
    "11": ("hydro_ec_us_cm", 1, 0),
    "1": ("hydro_tds_ppm", 1, 0),
    "2": ("hydro_water_temp_f", 10, 1),  # degC here; decode() converts to degF
    "7": ("hydro_battery_pct", 1, 0),
}
# Written NULL on every row the reader updates (DP 12 reports -1, no ORP probe).
NULL_COLUMNS = ("hydro_orp_mv",)

# tinytuya logs payloads and session keys at DEBUG; never let them through.
logging.getLogger("tinytuya").setLevel(logging.WARNING)


class HydroProbeError(Exception):
    """A failed read; ``code`` is tinytuya's numeric error code when it gave one."""

    def __init__(self, reason: str, code: int | None = None):
        super().__init__(reason)
        self.code = code


@dataclass(frozen=True)
class ProbeConfig:
    host: str
    version: str
    device_id: str = field(repr=False)
    local_key: str = field(repr=False)
    port: int = TUYA_PORT


def _read_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def load_config(environ: Mapping[str, str] | None = None, key_dir: Path = KEY_DIR) -> ProbeConfig | None:
    """The meter's address, protocol version and keys; None while the host or a key file is missing."""
    environ = os.environ if environ is None else environ
    host = environ.get(HOST_ENV, "").strip()
    device_id = _read_secret(key_dir / "device-id")
    local_key = _read_secret(key_dir / "local-key")
    if not (host and device_id and local_key):
        return None
    return ProbeConfig(host, environ.get(VERSION_ENV, "").strip(), device_id, local_key)


def _error_code(value: object) -> int | None:
    try:
        return int(str(value))
    except ValueError:
        return None


def read_status(config: ProbeConfig) -> dict[str, bool | int | float | str]:
    """One read-only session (connect, ``status()``, close); returns the validated DPs.

    A refused or unreachable meter costs one connection attempt. When the
    meter accepts the socket but never answers, tinytuya reconnects and asks
    once more, so a poll still ends within about 20 s.
    """
    try:
        address = ipaddress.ip_address(config.host)
    except ValueError:
        raise HydroProbeError("host is not an IP address") from None
    if address.is_unspecified:  # tinytuya would broadcast for the device instead
        raise HydroProbeError("host is not a device address")
    if config.version not in SUPPORTED_VERSIONS:
        raise HydroProbeError("unsupported Tuya protocol version")

    # Imported here so the decoder, the drift guard and the tests need no
    # tinytuya, and an idle reader never loads it into the writer process.
    import tinytuya  # noqa: PLC0415

    device = tinytuya.Device(
        config.device_id,
        address=str(address),
        local_key=config.local_key,
        version=float(config.version),
        connection_timeout=CONNECT_TIMEOUT_S,
        persist=False,
        connection_retry_limit=1,
        connection_retry_delay=0,
        port=config.port,
    )
    # Never fall back to device22 mode: its "query" is a CONTROL_NEW (0x0d) frame.
    device.disabledetect = True
    try:
        result = device.status()
    finally:
        device.close()
    if not isinstance(result, dict):
        raise HydroProbeError("no status payload")
    if "Err" in result:
        raise HydroProbeError("device error", code=_error_code(result.get("Err")))
    return TuyaDeviceStatus.model_validate(result).dps


def decode(dps: Mapping[str, object]) -> dict[str, float | None]:
    """Map a ``status()`` DP dict onto climate columns; absent DPs stay absent, ORP is NULL."""
    columns: dict[str, float | None] = {}
    for dp, (column, divisor, places) in DP_MAP.items():
        if dp not in dps:
            continue
        raw = dps[dp]
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
            raise ValueError(f"DP {dp} is not a finite number")
        value = round(raw / divisor, places)
        if column == "hydro_water_temp_f":
            value = round(value * 9 / 5 + 32, 2)
        columns[column] = value
    if not columns:
        raise HydroProbeError("status carried no measurement")
    columns.update(dict.fromkeys(NULL_COLUMNS))
    return columns
