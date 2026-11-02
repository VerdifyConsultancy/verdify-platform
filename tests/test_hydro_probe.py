"""Direct YINMIK hydro reader (VD-70, #1183): decode, idle, failure, secrecy; no device or DB I/O.

The read path runs against fakes: a stand-in pool, a stand-in tinytuya module,
and (when tinytuya is installed, as in CI) a loopback socket that plays a
protocol-3.3 meter and records every command it receives.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import socket
import struct
import sys
import threading
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INGESTOR = ROOT / "ingestor"
if str(INGESTOR) not in sys.path:
    sys.path.insert(0, str(INGESTOR))

import hydro_probe  # noqa: E402
import tasks.hydro as hydro_task  # noqa: E402

CASES = json.loads((ROOT / "tests" / "fixtures" / "hydro-probe-dps.json").read_text())["cases"]
DEVICE_ID = "fixture-device-0001"
CANARY = "canary-canary-16"  # 16 characters like a real local key; planted to prove it is never logged
LATEST_TS = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)


def _config(port: int = hydro_probe.TUYA_PORT, version: str = "3.3") -> hydro_probe.ProbeConfig:
    return hydro_probe.ProbeConfig("127.0.0.1", version, DEVICE_ID, CANARY, port)


def _key_dir(tmp_path: Path, *, local_key: bool = True) -> Path:
    key_dir = tmp_path / "hydro-probe"
    key_dir.mkdir()
    (key_dir / "device-id").write_text(DEVICE_ID + "\n")
    if local_key:
        (key_dir / "local-key").write_text(CANARY + "\n")
    return key_dir


class _Conn:
    def __init__(self, latest):
        self.latest = latest
        self.executes: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        assert query.startswith("SELECT ts FROM climate WHERE ts > now() - interval '5 minutes'")
        return self.latest

    async def execute(self, query, *args):
        self.executes.append((query, args))
        return "UPDATE 1"


class _Pool:
    def __init__(self, latest=LATEST_TS):
        self.conn = _Conn(latest)
        self.acquired = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.acquired += 1
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


_real_load_config = hydro_probe.load_config


def _run_task(monkeypatch, tmp_path, pool, *, key_dir=None, polls=1):
    """Poll as the scheduler does, with the env and key files the prod patch provides."""
    key_dir = key_dir if key_dir is not None else _key_dir(tmp_path)
    env = {"VERDIFY_HYDRO_PROBE_HOST": "192.168.10.105", "VERDIFY_HYDRO_PROBE_TUYA_VERSION": "3.3"}
    monkeypatch.setattr(hydro_task, "_idle_logged", False)
    monkeypatch.setattr(hydro_probe, "load_config", lambda: _real_load_config(env, key_dir))
    for _ in range(polls):
        asyncio.run(hydro_task.hydro_probe_sync(pool))


def _all_log_text(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


# ── decode ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_decode_equals_the_fixture_exactly(case):
    assert hydro_probe.decode(case["dps"]) == case["expected"]


def test_orp_sentinel_and_alarm_limit_never_reach_a_column():
    columns = hydro_probe.decode({"10": 621, "11": 2484, "12": -1, "107": 1400})
    assert columns["hydro_orp_mv"] is None
    assert -1 not in columns.values()
    # DP 107 is the pH alarm limit; Home Assistant's template read it as EC (1400 x 1.77 = 2478).
    assert columns["hydro_ec_us_cm"] == 2484 and 2478 not in columns.values()


def test_decode_matches_the_app_reading_the_ha_corrections_were_fitted_to():
    case = next(case for case in CASES if case["name"] == "ha-calibration-note-2026-04-18")
    columns = hydro_probe.decode(case["dps"])
    assert abs(columns["hydro_ph"] - case["app_reading"]["hydro_ph"]) <= 0.01 + 1e-9
    assert round(columns["hydro_ec_us_cm"] / 1000, 2) == case["app_reading"]["hydro_ec_ms_cm"]


@pytest.mark.parametrize("bad", ["621", True, float("nan"), None])
def test_decode_refuses_a_non_numeric_measurement(bad):
    with pytest.raises(ValueError):
        hydro_probe.decode({"10": bad})


def test_decode_refuses_a_status_without_measurements():
    with pytest.raises(hydro_probe.HydroProbeError):
        hydro_probe.decode({"12": -1, "107": 1400, "101": "AsiaStandard"})


# ── config ─────────────────────────────────────────────────────────────────


def test_config_needs_host_and_both_key_files_and_hides_the_keys(tmp_path):
    env = {"VERDIFY_HYDRO_PROBE_HOST": "192.168.10.105", "VERDIFY_HYDRO_PROBE_TUYA_VERSION": "3.4"}
    key_dir = _key_dir(tmp_path)
    config = hydro_probe.load_config(env, key_dir)
    assert (config.host, config.version, config.device_id, config.local_key) == (
        "192.168.10.105",
        "3.4",
        DEVICE_ID,
        CANARY,
    )
    assert CANARY not in repr(config) and DEVICE_ID not in repr(config)
    assert hydro_probe.load_config({}, key_dir) is None
    (key_dir / "local-key").write_text("\n")
    assert hydro_probe.load_config(env, key_dir) is None
    assert hydro_probe.load_config(env, tmp_path / "absent") is None


@pytest.mark.parametrize("host", ["0.0.0.0", "Auto", "greenhouse.invalid"])  # noqa: S104 — refused, never bound
def test_reader_dials_only_a_literal_device_address(monkeypatch, host):
    monkeypatch.setitem(sys.modules, "tinytuya", None)  # importing it would fail the test
    with pytest.raises(hydro_probe.HydroProbeError):
        hydro_probe.read_status(hydro_probe.ProbeConfig(host, "3.3", DEVICE_ID, CANARY))


@pytest.mark.parametrize("version", ["", "3.2", "auto"])
def test_reader_refuses_an_unsupported_protocol_version(monkeypatch, version):
    monkeypatch.setitem(sys.modules, "tinytuya", None)
    with pytest.raises(hydro_probe.HydroProbeError):
        hydro_probe.read_status(_config(version=version))


# ── task: idle, failure, success ──────────────────────────────────────────


def test_missing_key_logs_one_line_over_two_polls_and_writes_nothing(monkeypatch, tmp_path, caplog):
    pool = _Pool()
    called = []
    monkeypatch.setattr(hydro_probe, "read_status", lambda config: called.append(config))
    caplog.set_level(logging.DEBUG)
    _run_task(monkeypatch, tmp_path, pool, key_dir=_key_dir(tmp_path, local_key=False), polls=2)
    lines = [record.getMessage() for record in caplog.records if "hydro key missing" in record.getMessage()]
    assert lines == ["hydro key missing: direct hydro reader idle"]
    assert pool.acquired == 0 and called == []


def _fake_tinytuya(status=None, raises=None):
    calls: list[str] = []

    class Device:
        def __init__(self, dev_id, **kwargs):
            calls.append("init")
            self.kwargs = kwargs
            self.disabledetect = False  # tinytuya's default: device22 auto-detection on

        def status(self):
            # With detection on, a "data unvalid" answer makes tinytuya resend as CONTROL_NEW.
            calls.append("status" if self.disabledetect else "status-with-device22-fallback")
            if raises is not None:
                raise raises
            return status

        def close(self):
            calls.append("close")

    return types.SimpleNamespace(Device=Device), calls


@pytest.mark.parametrize(
    "status,raises,error,code",
    [
        ({"Error": "Network Error: Unable to Connect", "Err": "901", "Payload": None}, None, "HydroProbeError", "901"),
        ({"Error": "Timeout Waiting for Device", "Err": "902", "Payload": None}, None, "HydroProbeError", "902"),
        (
            {"Error": "Unexpected Payload", "Err": "904", "Payload": f"{{'key': '{CANARY}'}}"},
            None,
            "HydroProbeError",
            "904",
        ),
        (None, ConnectionRefusedError(111, f"refused while using {CANARY}"), "ConnectionRefusedError", "None"),
        (None, TimeoutError(f"timed out with {CANARY}"), "TimeoutError", "None"),
        ({"devId": CANARY, "dps": {"10": "not-a-number"}}, None, "ValueError", "None"),
        ({"devId": CANARY, "dps": {"10": [CANARY]}}, None, "ValidationError", "None"),
        ({"dps": {"10": 1500}}, None, "ValidationError", "None"),  # pH 15.00 fails ClimateRow
    ],
)
def test_failed_read_writes_nothing_and_logs_one_warning_without_the_key(
    monkeypatch, tmp_path, caplog, status, raises, error, code
):
    fake, calls = _fake_tinytuya(status, raises)
    monkeypatch.setitem(sys.modules, "tinytuya", fake)
    pool = _Pool()
    caplog.set_level(logging.DEBUG)
    _run_task(monkeypatch, tmp_path, pool)
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == [f"hydro_probe source=tuya_local error={error} tuya_err={code}"]
    assert pool.acquired == 0 and pool.conn.executes == []
    assert calls[-1] == "close"
    text = _all_log_text(caplog)
    assert CANARY not in text and DEVICE_ID not in text


def test_reader_session_is_one_short_status_query(monkeypatch):
    fake, calls = _fake_tinytuya({"dps": {"10": 621}})
    created = []
    original = fake.Device

    class Recording(original):
        def __init__(self, dev_id, **kwargs):
            super().__init__(dev_id, **kwargs)
            created.append((dev_id, kwargs))

    fake.Device = Recording
    monkeypatch.setitem(sys.modules, "tinytuya", fake)
    assert hydro_probe.read_status(hydro_probe.ProbeConfig("192.168.10.105", "3.4", DEVICE_ID, CANARY)) == {"10": 621}
    assert calls == ["init", "status", "close"]
    ((dev_id, kwargs),) = created
    assert dev_id == DEVICE_ID
    assert kwargs == {
        "address": "192.168.10.105",
        "local_key": CANARY,
        "version": 3.4,
        "connection_timeout": 5,
        "persist": False,
        "connection_retry_limit": 1,
        "connection_retry_delay": 0,
        "port": 6668,
    }


def test_success_overlays_the_newest_row_with_orp_null_and_logs_values_only(monkeypatch, tmp_path, caplog):
    case = next(case for case in CASES if case["name"] == "full-model-status")
    monkeypatch.setattr(hydro_probe, "read_status", lambda config: dict(case["dps"]))
    pool = _Pool()
    caplog.set_level(logging.DEBUG)
    _run_task(monkeypatch, tmp_path, pool)
    ((query, args),) = pool.conn.executes
    prefix = "UPDATE v_runtime_climate_write SET "
    assignments, where = query.removeprefix(prefix).split(" WHERE ")
    assert query.startswith(prefix) and where == f"ts = ${len(args)}"
    assigned = [part.split(" = ")[0] for part in assignments.split(", ")]
    assert dict(zip(assigned, args[:-1], strict=True)) == case["expected"]
    assert args[-1] == LATEST_TS
    info = [record.getMessage() for record in caplog.records if record.getMessage().startswith("hydro_probe ")]
    assert info == ["hydro_probe source=tuya_local ph=6.21 ec=2484 tds=1240 temp_f=74.48 battery=100"]
    assert CANARY not in _all_log_text(caplog) and DEVICE_ID not in _all_log_text(caplog)


def test_no_recent_climate_row_writes_nothing(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(hydro_probe, "read_status", lambda config: {"10": 621})
    pool = _Pool(latest=None)
    caplog.set_level(logging.INFO)
    _run_task(monkeypatch, tmp_path, pool)
    assert pool.conn.executes == []
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [
        "hydro_probe source=tuya_local skipped: no recent climate row"
    ]


# ── read-only, by construction and on the wire ────────────────────────────


def test_the_reader_only_calls_status_and_close_on_the_device():
    tree = ast.parse((INGESTOR / "hydro_probe.py").read_text())
    device_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "device"
    }
    assert device_calls == {"status", "close"}


def _serve(body: bytes, received: list) -> socket.socket:
    """A loopback protocol-3.3 meter: records each request's command and answers it with ``body``.

    It keeps accepting connections, so a second session (a retry or a fallback
    frame) would be recorded too.
    """
    tinytuya = pytest.importorskip("tinytuya")
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(5)

    def answer():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:  # closed by the test, or no further session
                return
            with conn:
                conn.settimeout(5)
                request = conn.recv(4096)
                if not request:
                    continue
                header = tinytuya.parse_header(request)
                received.append(header.cmd)
                payload = struct.pack(">I", 0) + tinytuya.AESCipher(CANARY.encode()).encrypt(body, False)
                reply = tinytuya.TuyaMessage(header.seqno, header.cmd, None, payload, 0, True, 0x55AA, None)
                conn.sendall(tinytuya.pack_message(reply))

    threading.Thread(target=answer, daemon=True).start()
    return server


def test_real_tinytuya_session_sends_one_status_query_and_decodes(caplog):
    case = next(case for case in CASES if case["name"] == "full-model-status")
    received: list[int] = []
    server = _serve(json.dumps({"devId": DEVICE_ID, "dps": case["dps"]}).encode(), received)
    caplog.set_level(logging.DEBUG)
    with server:
        dps = hydro_probe.read_status(_config(port=server.getsockname()[1], version="3.3"))
    assert received == [10]  # DP_QUERY, the only command the meter ever receives
    assert hydro_probe.decode(dps) == case["expected"]
    # tinytuya logs the decrypted payload (with the device id) at DEBUG; the reader clamps it.
    text = _all_log_text(caplog)
    assert CANARY not in text and DEVICE_ID not in text


def test_real_tinytuya_never_falls_back_to_a_control_frame(caplog):
    # A "data unvalid" answer would switch tinytuya to device22 mode, which resends
    # the query as CONTROL_NEW (13) with a null DP 1. The reader keeps that off.
    received: list[int] = []
    server = _serve(b"json obj data unvalid", received)
    caplog.set_level(logging.DEBUG)
    with server, pytest.raises(hydro_probe.HydroProbeError) as failure:
        hydro_probe.read_status(_config(port=server.getsockname()[1], version="3.3"))
    assert received == [10]
    assert failure.value.code == 900  # tinytuya ERR_JSON: an unreadable answer, nothing resent
    text = _all_log_text(caplog)
    assert CANARY not in text and DEVICE_ID not in text
