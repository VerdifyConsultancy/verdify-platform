"""capture_component_current_state.py is a pure/typed offline core around one
read-only, session-pinned-read-only database read. These tests exercise the
pure helpers directly, the async read/orchestration functions against a fake
asyncpg connection (no real database), and — the key contract — round-trip
one produced artifact through the REAL scripts/prepare_component_prefix_
replay.py validator, not a reimplementation of its checks.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = _load_module(ROOT / "scripts" / "capture_component_current_state.py", "capture_component_current_state")
prep = _load_module(ROOT / "scripts" / "prepare_component_prefix_replay.py", "prepare_component_prefix_replay")

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER  # noqa: E402
from verdify_schemas.policy_vector import canonical_json_bytes, decode_policy_vector  # noqa: E402
from verdify_schemas.tunable_registry import REGISTRY, wire_value_bounds  # noqa: E402

PROFILE_PATH = ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"
MANIFEST_PATH = ROOT / "firmware/policy_consumer_manifest.json"
COMPILED_DEFAULT_FIXTURE = (
    ROOT / "tests/fixtures/component_prefix_replay/esphome-2026.6.5-compiled-default-constructors.cpp"
)

GRID_REVISION = "live-entity-grid-v1:sha256:" + "b" * 64
SOURCE_REVISION = "c" * 40
FIRMWARE_REVISION = "2026.8.26.0301.deadbee"
DEVICE_ID = "esp32-vallery"
GREENHOUSE_ID = "vallery"
NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)

BOOL_FIELDS = frozenset(
    name for name, definition in REGISTRY.items() if name in CANONICAL_FIELD_ORDER and definition.wire_kind == "bool"
)


def _baseline_values() -> dict[str, bool | float]:
    """A real, on-grid, complete 48-field state (decoded from the committed profile artifact)."""
    artifact = json.loads(PROFILE_PATH.read_text())
    return decode_policy_vector(bytes.fromhex(artifact["profiles"]["baseline"]["wire_hex"]))


def _raw_rows_for(values: dict[str, bool | float], ts: datetime) -> list[dict[str, object]]:
    """Project typed values back into raw setpoint_snapshot rows (double precision)."""
    return [
        {
            "parameter": field,
            "value": (1.0 if value is True else 0.0) if isinstance(value, bool) else float(value),
            "ts": ts,
        }
        for field, value in values.items()
    ]


class FakeConnection:
    """Duck-types the subset of asyncpg.Connection this tool calls."""

    def __init__(self, *, batch_rows: list[dict[str, object]], firmware_row: dict[str, object] | None) -> None:
        self.batch_rows = batch_rows
        self.firmware_row = firmware_row
        self.executed: list[str] = []
        self.closed = False

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append(query)

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        return self.batch_rows

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        return self.firmware_row

    async def close(self) -> None:
        self.closed = True


# ── Pure helpers ──────────────────────────────────────────────────────────


def test_schema_constant_matches_the_downstream_validator() -> None:
    assert capture.CURRENT_STATE_SCHEMA == prep.CURRENT_STATE_SCHEMA


def test_grid_revision_accepts_the_qualified_form_and_rejects_everything_else() -> None:
    assert capture._grid_revision(GRID_REVISION) == GRID_REVISION
    for bad in (
        "source-grid-parity/main-and-historical-09ee886-live-unverified-v1",  # the provisional source constant
        "live-entity-grid-v1:sha256:" + "b" * 63,  # short hex
        "live-entity-grid-v1:sha256:" + "B" * 64,  # uppercase hex
        "live-entity-grid-v0:sha256:" + "b" * 64,  # v0 not allowed (v[1-9]...)
        "",
    ):
        with pytest.raises(capture.CaptureError):
            capture._grid_revision(bad)


def test_observed_at_text_requires_timezone_aware_and_formats_with_z() -> None:
    assert capture._observed_at_text(NOW) == "2026-08-26T12:00:00.000000Z"
    with pytest.raises(capture.CaptureError):
        capture._observed_at_text(datetime(2026, 8, 26, 12, 0, 0))  # naive


def test_typed_value_projects_exact_bool_for_switch_fields() -> None:
    bool_field = next(iter(BOOL_FIELDS))
    assert capture._typed_value(bool_field, 0.0) is False
    assert capture._typed_value(bool_field, 1.0) is True
    with pytest.raises(capture.CaptureError, match="not an exact 0.0/1.0"):
        capture._typed_value(bool_field, 0.5)


def test_typed_value_rejects_bool_input_and_enforces_wire_bounds_for_numeric_fields() -> None:
    numeric_field = next(name for name in CANONICAL_FIELD_ORDER if name not in BOOL_FIELDS)
    lower, upper = wire_value_bounds(numeric_field)
    assert capture._typed_value(numeric_field, lower) == lower
    with pytest.raises(capture.CaptureError, match="not a numeric value"):
        capture._typed_value(numeric_field, True)
    with pytest.raises(capture.CaptureError, match="outside its wire bounds"):
        capture._typed_value(numeric_field, upper + 1_000_000.0)
    with pytest.raises(capture.CaptureError, match="not finite"):
        capture._typed_value(numeric_field, math.inf)


def test_build_current_state_shape_matches_the_downstream_schema_exactly() -> None:
    values = _baseline_values()
    packet = capture.build_current_state(
        device_id=DEVICE_ID,
        firmware_revision=FIRMWARE_REVISION,
        grid_revision=GRID_REVISION,
        observed_at=NOW,
        values=values,
    )
    assert set(packet) == {"schema", "device_id", "firmware_revision", "grid_revision", "observed_at", "values"}
    assert packet["schema"] == prep.CURRENT_STATE_SCHEMA
    assert set(packet["values"]) == set(CANONICAL_FIELD_ORDER)


def test_build_current_state_refuses_an_incomplete_values_mapping() -> None:
    values = _baseline_values()
    del values[next(iter(values))]
    with pytest.raises(capture.CaptureError, match="missing="):
        capture.build_current_state(
            device_id=DEVICE_ID,
            firmware_revision=FIRMWARE_REVISION,
            grid_revision=GRID_REVISION,
            observed_at=NOW,
            values=values,
        )


# ── Async read/orchestration against a fake connection (no real database) ──


@pytest.mark.asyncio
async def test_read_complete_batch_returns_typed_values_and_the_shared_ts() -> None:
    values = _baseline_values()
    conn = FakeConnection(batch_rows=_raw_rows_for(values, NOW), firmware_row=None)
    read_values, observed_at = await capture._read_complete_batch(conn, greenhouse_id=GREENHOUSE_ID, max_age_s=180)
    assert read_values == values
    assert observed_at == NOW


@pytest.mark.asyncio
async def test_read_complete_batch_refuses_rows_that_do_not_share_one_ts() -> None:
    values = _baseline_values()
    rows = _raw_rows_for(values, NOW)
    rows[0] = dict(rows[0], ts=datetime(2026, 8, 26, 11, 59, 0, tzinfo=UTC))
    conn = FakeConnection(batch_rows=rows, firmware_row=None)
    with pytest.raises(capture.CaptureError, match="do not share one ts"):
        await capture._read_complete_batch(conn, greenhouse_id=GREENHOUSE_ID, max_age_s=180)


@pytest.mark.asyncio
async def test_read_complete_batch_refuses_conflicting_duplicate_rows() -> None:
    values = _baseline_values()
    rows = _raw_rows_for(values, NOW)
    target_field = CANONICAL_FIELD_ORDER[0]
    original_row = next(row for row in rows if row["parameter"] == target_field)
    conflicting = dict(original_row)
    # Guaranteed to differ from original_row["value"] regardless of its sign/kind.
    conflicting["value"] = 0.0 if original_row["value"] != 0.0 else 1.0
    rows.append(conflicting)
    conn = FakeConnection(batch_rows=rows, firmware_row=None)
    with pytest.raises(capture.CaptureError, match="conflicting duplicate"):
        await capture._read_complete_batch(conn, greenhouse_id=GREENHOUSE_ID, max_age_s=180)


@pytest.mark.asyncio
async def test_read_complete_batch_refuses_an_empty_or_incomplete_result() -> None:
    conn = FakeConnection(batch_rows=[], firmware_row=None)
    with pytest.raises(capture.CaptureError, match="no setpoint_snapshot batch"):
        await capture._read_complete_batch(conn, greenhouse_id=GREENHOUSE_ID, max_age_s=180)

    values = _baseline_values()
    rows = _raw_rows_for(values, NOW)[:-1]  # drop one canonical field
    conn = FakeConnection(batch_rows=rows, firmware_row=None)
    with pytest.raises(capture.CaptureError, match="missing canonical fields"):
        await capture._read_complete_batch(conn, greenhouse_id=GREENHOUSE_ID, max_age_s=180)


@pytest.mark.asyncio
async def test_read_firmware_revision_requires_a_fresh_nonempty_row() -> None:
    conn = FakeConnection(batch_rows=[], firmware_row=None)
    with pytest.raises(capture.CaptureError, match="no diagnostics.firmware_version"):
        await capture._read_firmware_revision(conn, greenhouse_id=GREENHOUSE_ID, max_age_s=120)

    conn = FakeConnection(batch_rows=[], firmware_row={"ts": NOW, "firmware_version": FIRMWARE_REVISION})
    assert await capture._read_firmware_revision(conn, greenhouse_id=GREENHOUSE_ID, max_age_s=120) == FIRMWARE_REVISION


@pytest.mark.asyncio
async def test_capture_sets_the_session_read_only_before_any_select(monkeypatch: pytest.MonkeyPatch) -> None:
    values = _baseline_values()
    fake = FakeConnection(
        batch_rows=_raw_rows_for(values, NOW),
        firmware_row={"ts": NOW, "firmware_version": FIRMWARE_REVISION},
    )

    async def fake_connect(dsn: str) -> FakeConnection:
        assert dsn == "postgresql://example/verdify"
        return fake

    monkeypatch.setattr(capture.asyncpg, "connect", fake_connect)

    packet = await capture.capture(
        dsn="postgresql://example/verdify",
        greenhouse_id=GREENHOUSE_ID,
        device_id=DEVICE_ID,
        grid_revision=GRID_REVISION,
        max_batch_age_s=180,
        firmware_max_age_s=120,
    )

    assert fake.executed == ["SET default_transaction_read_only = on"]
    assert fake.closed is True
    assert packet["device_id"] == DEVICE_ID
    assert packet["firmware_revision"] == FIRMWARE_REVISION
    assert packet["grid_revision"] == GRID_REVISION
    assert packet["values"] == values


# ── Atomic, private, no-overwrite output (mirrors the sibling script) ──────


def test_output_is_atomic_private_and_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "private" / "current-state.json"
    raw = canonical_json_bytes({"a": 1})
    capture._write_current_state_output(raw, output)
    assert output.read_bytes() == raw
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.parent.stat().st_mode & 0o077 == 0
    assert list(output.parent.iterdir()) == [output]

    with pytest.raises(capture.CaptureError, match="overwrite refused"):
        capture._write_current_state_output(b"replacement", output)
    assert output.read_bytes() == raw


def test_output_refuses_symlink_targets(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.write_bytes(b"unchanged")
    symlink = tmp_path / "symlink"
    os.symlink(existing.name, symlink)
    with pytest.raises(capture.CaptureError, match="overwrite refused"):
        capture._write_current_state_output(b"packet", symlink)
    assert existing.read_bytes() == b"unchanged"


# ── Device-id default resolution ────────────────────────────────────────


def test_resolve_device_id_defaults_via_policy_device_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERDIFY_POLICY_DEVICE_ID", raising=False)
    assert capture._resolve_device_id(None, "vallery") == "esp32-vallery"
    assert capture._resolve_device_id("esp32-explicit", "vallery") == "esp32-explicit"


# ── CLI fail-fast: bad --grid-revision never reaches the database ─────────


def test_main_rejects_a_bad_grid_revision_before_touching_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fail_connect(dsn: str) -> None:
        raise AssertionError("must not connect when --grid-revision is malformed")

    monkeypatch.setattr(capture.asyncpg, "connect", fail_connect)
    monkeypatch.setenv("VERDIFY_DSN", "postgresql://unused/verdify")

    with pytest.raises(SystemExit) as excinfo:
        capture.main(
            [
                "--grid-revision",
                "not-a-real-grid-revision",
                "--output",
                str(tmp_path / "out.json"),
            ]
        )
    assert excinfo.value.code == 2
    assert "current-state capture refused" in capsys.readouterr().err


# ── The key contract: round-trip through the REAL downstream validator ────


def _profiles_and_manifest() -> tuple[bytes, dict, str, bytes, dict, str]:
    profile_raw = PROFILE_PATH.read_bytes()
    manifest_raw = MANIFEST_PATH.read_bytes()
    return (
        profile_raw,
        json.loads(profile_raw),
        hashlib.sha256(profile_raw).hexdigest(),
        manifest_raw,
        json.loads(manifest_raw),
        hashlib.sha256(manifest_raw).hexdigest(),
    )


def test_captured_artifact_round_trips_through_prepare_component_prefix_replay(tmp_path: Path) -> None:
    values = _baseline_values()
    packet = capture.build_current_state(
        device_id=DEVICE_ID,
        firmware_revision=FIRMWARE_REVISION,
        grid_revision=GRID_REVISION,
        observed_at=NOW,
        values=values,
    )
    output_path = tmp_path / "current-state-observed.json"
    capture._write_current_state_output(canonical_json_bytes(packet), output_path)

    current_state_json = json.loads(output_path.read_bytes())

    (_profile_raw, profiles, profile_sha, _manifest_raw, manifest, manifest_sha) = _profiles_and_manifest()

    result = prep.build_preparation_packet(
        profile_artifact=profiles,
        profile_artifact_sha256=profile_sha,
        consumer_manifest=manifest,
        consumer_manifest_sha256=manifest_sha,
        generated_main_cpp=COMPILED_DEFAULT_FIXTURE.read_bytes(),
        firmware_binary=b"test-compiled-firmware-binary",
        source_revision=SOURCE_REVISION,
        firmware_revision=FIRMWARE_REVISION,
        grid_revision=GRID_REVISION,
        current_states={"observed-20260826t120000z": current_state_json},
    )

    assert result["status"] == "prepared_not_qualified"
    assert result["summary"]["current_start_count"] == 1
    assert "observed_current_state_missing" not in result["qualification_blockers"]
    captured_start = result["starts"]["observed-20260826t120000z"]
    assert captured_start["kind"] == "observed_current"
    assert captured_start["device_id"] == DEVICE_ID
    assert captured_start["identity"]["entity_grid_valid"] is True
    assert captured_start["values"] == values


def test_a_switch_field_captured_as_a_bare_0_1_float_is_rejected_by_the_real_validator(tmp_path: Path) -> None:
    """Prove _typed_value's bool-exactness requirement is load-bearing, not decorative.

    Bypasses capture.py's own typing on purpose to show what the downstream
    validator does with the mistake this tool exists to prevent.
    """
    values = dict(_baseline_values())
    bool_field = next(iter(BOOL_FIELDS))
    values[bool_field] = 1.0  # a bare float, not True — what a naive DB dump would produce

    broken = {
        "schema": prep.CURRENT_STATE_SCHEMA,
        "device_id": DEVICE_ID,
        "firmware_revision": FIRMWARE_REVISION,
        "grid_revision": GRID_REVISION,
        "observed_at": capture._observed_at_text(NOW),
        "values": values,
    }
    output_path = tmp_path / "broken-current-state.json"
    output_path.write_bytes(canonical_json_bytes(broken))
    current_state_json = json.loads(output_path.read_bytes())

    (_profile_raw, profiles, profile_sha, _manifest_raw, manifest, manifest_sha) = _profiles_and_manifest()

    with pytest.raises(prep.PrefixPreparationError, match="must be an exact boolean"):
        prep.build_preparation_packet(
            profile_artifact=profiles,
            profile_artifact_sha256=profile_sha,
            consumer_manifest=manifest,
            consumer_manifest_sha256=manifest_sha,
            generated_main_cpp=COMPILED_DEFAULT_FIXTURE.read_bytes(),
            firmware_binary=b"test-compiled-firmware-binary",
            source_revision=SOURCE_REVISION,
            firmware_revision=FIRMWARE_REVISION,
            grid_revision=GRID_REVISION,
            current_states={"observed-broken": current_state_json},
        )
