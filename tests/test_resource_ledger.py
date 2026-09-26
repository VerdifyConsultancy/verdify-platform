"""Offline ledger contract tests; no HA/DB/network access or implicit live fixtures."""

from __future__ import annotations

import ast
import asyncio
import copy
import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research/planner-efficacy/resource_ledger.py"
spec = importlib.util.spec_from_file_location("resource_ledger", SCRIPT)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def snapshot(day="2026-08-14"):
    return {
        "date": day,
        "greenhouse_id": "vallery",
        "water": {
            "date": day,
            "greenhouse_id": "vallery",
            "quality_filtered_meter_gal": 86,
            "attributed_gal": 18,
            "ambiguous_gal": 66,
            "manual_or_unattributed_gal": 2,
            "climate_wetting_gal": 18,
            "wall_irrigation_gal": 0,
            "wall_fertigation_gal": 0,
            "unsupported_path_gal": 0,
            "conservation_error_gal": 0,
            "available_for_scoring": True,
            "ledger_quality": "ok",
            "resource_quality": "ok",
            "command_only_runs": 5,
        },
        "energy": {
            "date": day,
            "greenhouse_id": "vallery",
            "measured_scope": ledger.MEASURED_SCOPE,
            "modeled_scope": ledger.MODELED_SCOPE,
            "measured_kwh": Decimal("5.847"),
            "kwh_estimated": None,
            "measured_available_for_scoring": True,
            "modeled_available_for_scoring": False,
            "measured_quality": "ok",
            "model_quality": "incomplete_runtime_evidence",
            "meter_coverage_pct": Decimal("99.8"),
            "runtime_coverage_pct": Decimal("85.7"),
            "estimate_delta_kwh": "SHOULD_NOT_BE_OUTPUT",
        },
        "health": [{"note": "SHOULD_NOT_BE_OUTPUT"}],
    }


@pytest.fixture
def extraction(tmp_path):
    manifest = {"completed_at": "2026-09-05T15:11:16+00:00", "requests": []}
    for day in ("2026-08-14", "2026-08-15"):
        # JSON encoder below must preserve numeric quantities as JSON numbers.
        raw = json.dumps(snapshot(day), default=float).encode()
        name = f"resources-{day}.json"
        (tmp_path / name).write_bytes(raw)
        manifest["requests"].append(
            {
                "file": name,
                "url": f"https://api.verdify.ai/api/v1/resources/daily?date={day}",
                "requested_at": "2026-09-05T15:10:34+00:00",
                "status": 200,
                "content_type": "application/json",
                "bytes": len(raw),
                "sha256": ledger.sha256(raw),
            }
        )
    manifest_path = tmp_path / "manifest.json"

    def run(change=None):
        if change:
            change(manifest)
        raw = json.dumps(manifest).encode()
        manifest_path.write_bytes(raw)
        return ledger.build(tmp_path, manifest_path, ledger.sha256(raw), "2026-08-14", "2026-08-16")

    return tmp_path, manifest, run


def test_exact_conservation_and_scope_aggregation(extraction):
    _, _, run = extraction
    report = run()
    water = report["summary"]["water"]
    assert water["quality_filtered_meter_gal"]["complete_total"] == Decimal(172)
    assert (
        sum(water[key]["complete_total"] for key in ("attributed_gal", "ambiguous_gal", "manual_or_unattributed_gal"))
        == 172
    )
    assert report["summary"]["partial_electricity"]["complete_total"] == Decimal("11.694")
    assert report["summary"]["modeled_electricity"]["complete_total"] is None
    assert report["summary"]["modeled_electricity"]["observed_subtotal"] is None
    assert report["summary"]["days_with_audit_issues"] == 0
    assert report["eligibility"]["water_commissioned"] is False
    assert report["eligibility"]["partial_electricity_commissioned"] is False
    assert report["eligibility"]["resource_cost"] is None
    assert report["eligibility"]["gas_therms"] is None
    assert report["eligibility"]["interior_dli"] is None
    assert "SHOULD_NOT_BE_OUTPUT" not in ledger.encode(report).decode()
    assert ledger.encode(report) == ledger.encode(run())


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["requests"].pop(),
        lambda m: m["requests"].append(copy.deepcopy(m["requests"][0])),
        lambda m: m["requests"][0].update(file="../resources-2026-08-14.json"),
        lambda m: m["requests"][0].update(status=503),
        lambda m: m["requests"][0].update(bytes=True),
        lambda m: m["requests"][0].update(sha256="0" * 64),
        lambda m: m["requests"][0].update(content_type="text/html"),
        lambda m: m["requests"][0].update(url="https://example.com/"),
        lambda m: m["requests"][0].update(requested_at="2026-09-05T16:00:00+00:00"),
        lambda m: m["requests"][0].update(requested_at="2026-08-14T23:00:00+00:00"),
        lambda m: m.update(completed_at="2026-09-05T15:11:16"),
    ],
)
def test_manifest_fail_closed(extraction, change):
    _, _, run = extraction
    with pytest.raises(ledger.ContractError):
        run(change)


def test_missing_file_and_tampering_and_symlink(extraction):
    directory, _, run = extraction
    target = directory / "resources-2026-08-14.json"
    original = target.read_bytes()
    target.unlink()
    with pytest.raises(ledger.ContractError):
        run()
    target.write_bytes(original.replace(b'"ambiguous_gal": 66', b'"ambiguous_gal": 67'))
    with pytest.raises(ledger.ContractError, match="hash mismatch"):
        run()
    target.unlink()
    other = directory / "other.json"
    other.write_bytes(original)
    target.symlink_to(other)
    with pytest.raises(ledger.ContractError, match="non-symlink"):
        run()


@pytest.mark.parametrize("raw", [b'{"date":1,"date":2}', b'{"n":NaN}', b'{"n":Infinity}', b"\xff"])
def test_json_parser_rejects_ambiguous_or_nonfinite(raw):
    with pytest.raises(ledger.ContractError):
        ledger.parse(raw)


@pytest.mark.parametrize("value", [True, "12", Decimal("NaN"), Decimal("Infinity"), -1])
def test_water_invalid_quantities(value):
    data = snapshot()
    data["water"]["ambiguous_gal"] = value
    with pytest.raises(ledger.ContractError):
        ledger.daily(data, data["date"])


def test_absent_rows_or_fields_remain_missing_not_zero():
    data = snapshot()
    del data["water"]["ambiguous_gal"]
    first = ledger.daily(data, data["date"])
    assert first["water"]["ambiguous_gal"] is None
    assert first["water"]["computed_conservation_error_gal"] is None
    assert "source_eligible_water_requires_review" in first["audit_issues"]
    second = ledger.daily(snapshot("2026-08-15"), "2026-08-15")
    total = ledger.aggregate([first, second], "water", "ambiguous_gal")
    assert total == {
        "selected_days": 2,
        "observed_days": 1,
        "missing_days": 1,
        "observed_subtotal": Decimal(66),
        "complete_total": None,
    }
    data["water"] = None
    data["energy"] = None
    unavailable = ledger.daily(data, data["date"])
    assert unavailable["water"]["source_available_for_scoring"] is None
    assert unavailable["energy"]["measured_kwh"] is None
    assert unavailable["water"]["section_missing"] is True


def test_zero_is_observed_and_signed_electricity_preserved():
    data = snapshot()
    data["water"]["ambiguous_gal"] = 0
    data["energy"]["measured_kwh"] = Decimal("-0.123")
    row = ledger.daily(data, data["date"])
    assert ledger.aggregate([row], "water", "ambiguous_gal")["complete_total"] == 0
    assert row["energy"]["measured_kwh"] == Decimal("-0.123")
    assert "computed_conservation_error_gal" in row["audit_issues"]
    assert "reported_conservation_error_mismatch" in row["audit_issues"]


def test_source_flags_are_not_recomputed():
    data = snapshot()
    data["water"]["available_for_scoring"] = False
    row = ledger.daily(data, data["date"])
    assert row["water"]["computed_conservation_error_gal"] == 0
    assert row["water"]["source_available_for_scoring"] is False
    assert (
        ledger.aggregate([row], "water", "quality_filtered_meter_gal", "source_available_for_scoring")["complete_total"]
        is None
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(date="2026-08-15"),
        lambda d: d.update(greenhouse_id="other"),
        lambda d: d["water"].update(greenhouse_id="other"),
        lambda d: d["energy"].update(date="2026-08-15"),
        lambda d: d["energy"].update(measured_scope="whole_facility"),
        lambda d: d["energy"].update(meter_coverage_pct=101),
        lambda d: d["energy"].update(measured_available_for_scoring=1),
        lambda d: d["water"].update(command_only_runs=Decimal("1.1")),
    ],
)
def test_identity_scope_and_type_contracts(change):
    data = snapshot()
    change(data)
    with pytest.raises(ledger.ContractError):
        ledger.daily(data, "2026-08-14")


def test_scope_missing_and_invalid_model_bounds_are_visible():
    data = snapshot()
    data["energy"].update(measured_scope=None, kwh_estimated=20, modeled_kwh_low=25, modeled_kwh_high=30)
    row = ledger.daily(data, data["date"])
    assert "measured_value_without_scope" in row["audit_issues"]
    assert "modeled_bounds_do_not_bracket_point" in row["audit_issues"]
    scoped = ledger.aggregate([row], "energy", "measured_kwh", scope=("measured_scope", ledger.MEASURED_SCOPE))
    assert scoped["observed_days"] == 0
    assert scoped["complete_total"] is None
    assert "source_eligible_water_requires_review" not in row["audit_issues"]


def test_cli_exclusive_output_and_bad_manifest_pin(extraction, capsys):
    directory, _, run = extraction
    run()
    manifest = directory / "manifest.json"
    output = directory / "output.json"
    args = [
        "--evidence-dir",
        str(directory),
        "--manifest",
        str(manifest),
        "--manifest-sha256",
        ledger.sha256(manifest.read_bytes()),
        "--start",
        "2026-08-14",
        "--end",
        "2026-08-16",
        "--output",
        str(output),
    ]
    assert ledger.main(args) == 0
    raw = output.read_bytes()
    assert ledger.main(args) == 2
    assert output.read_bytes() == raw
    args[5] = "0" * 64
    assert ledger.main(args) == 2
    assert output.read_bytes() == raw
    assert "SHOULD_NOT_BE_OUTPUT" not in capsys.readouterr().err


def test_committed_22_day_baseline_integrity():
    """Checks the published projection, not a substitute for replaying raw inputs."""
    path = ROOT / "research/planner-efficacy/resources-2026-08-14_2026-09-05.baseline.json"
    raw = path.read_bytes()
    assert ledger.sha256(raw) == "aa7ef5097efc3a44e6dd15e7ce851fba872af8b3792ab7faff93cf70e8f7d6a3"
    report = ledger.parse(raw)
    assert report["provenance"]["tool_sha256"] == ledger.sha256(SCRIPT.read_bytes())
    assert len(report["days"]) == len(report["provenance"]["inputs"]) == 22
    assert report["days"][0]["date"] == "2026-08-14"
    assert report["days"][-1]["date"] == "2026-09-04"
    water = report["summary"]["water"]
    expected = {
        "quality_filtered_meter_gal": 5047,
        "attributed_gal": 1361,
        "ambiguous_gal": 3465,
        "manual_or_unattributed_gal": 221,
    }
    for key, total in expected.items():
        assert Decimal(water[key]["complete_total"]) == total
        assert sum(Decimal(row["water"][key]) for row in report["days"]) == total
    for row in report["days"]:
        assert Decimal(row["water"]["computed_conservation_error_gal"]) == 0
        assert Decimal(row["water"]["computed_attribution_scope_error_gal"]) == 0
        assert row["energy"]["measured_scope"] == ledger.MEASURED_SCOPE
        assert row["energy"]["modeled_scope"] == ledger.MODELED_SCOPE
    assert report["summary"]["source_eligible_water"]["selected_days"] == 13
    assert report["summary"]["source_measured_eligible_days"] == 22
    assert report["summary"]["source_modeled_eligible_days"] == 0
    assert Decimal(report["summary"]["partial_electricity"]["complete_total"]) == Decimal("124.384")
    assert Decimal(report["summary"]["modeled_electricity"]["observed_subtotal"]) == Decimal("342.025")
    assert report["summary"]["modeled_electricity"]["complete_total"] is None


def test_invalid_input_cli_creates_no_output(extraction, capsys):
    directory, _, run = extraction
    run()
    manifest = directory / "manifest.json"
    output = directory / "absent.json"
    args = [
        "--evidence-dir",
        str(directory),
        "--manifest",
        str(manifest),
        "--manifest-sha256",
        "0" * 64,
        "--start",
        "2026-08-14",
        "--end",
        "2026-08-16",
        "--output",
        str(output),
    ]
    assert ledger.main(args) == 2
    assert not output.exists()
    assert "no input values disclosed" in capsys.readouterr().err


def test_byte_limit(extraction):
    directory, _, run = extraction
    run()
    with pytest.raises(ledger.ContractError, match="size limit"):
        ledger.read_bytes(directory / "manifest.json", 10)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-08-14", "2026-08-14"),
        ("2026-08-15", "2026-08-14"),
        ("2026-01-01", "2026-04-01"),
        ("20260814", "2026-08-16"),
    ],
)
def test_window_refusal(extraction, start, end):
    directory, _, run = extraction
    run()
    manifest = directory / "manifest.json"
    with pytest.raises(ledger.ContractError):
        ledger.build(directory, manifest, ledger.sha256(manifest.read_bytes()), start, end)


@pytest.mark.parametrize(
    "states,expected",
    [
        ({"a": 120}, (120, 0, 120)),
        ({"b": 80}, (80, 80, 0)),
        ({"counter": 12}, (0, 0, 0)),
        ({"a": 120, "b": 80}, (200, 80, 120)),
        ({}, None),
    ],
)
def test_current_shelly_writer_baseline_counterexample(states, expected):
    """Execute the actual function with I/O doubles, documenting—not endorsing—its defect.

    A future producer correction must intentionally revise/supersede this baseline.
    AST isolation avoids importing task startup, projected tokens, or HA clients.
    """
    tree = ast.parse((ROOT / "ingestor/tasks/ha.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "shelly_sync")
    assert not function.decorator_list
    namespace = {
        "asyncio": asyncio,
        "datetime": datetime,
        "UTC": UTC,
        "HA_TOKEN_FILE": "unused-test-placeholder",
        "_load_token": lambda _: "noncredential-test-placeholder",
        "_fetch_ha_batch": lambda *_: states,
        "_SHELLY_ENTITIES": {
            "a": ("ch0_power_w", None),
            "b": ("ch1_power_w", None),
            "counter": ("ch0_energy_kwh", None),
        },
        "_ha_state": lambda rows, name: SimpleNamespace(as_float=lambda: rows[name]) if name in rows else None,
        "ValidationError": ValueError,
        "log": SimpleNamespace(info=lambda *_: None, debug=lambda *_: None),
    }
    from verdify_schemas.telemetry import EnergySample

    namespace["EnergySample"] = EnergySample
    exec(compile(ast.Module(body=[function], type_ignores=[]), "actual-shelly-sync", "exec"), namespace)  # noqa: S102
    writes = []

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, sql, *values):
            assert "INSERT INTO v_runtime_energy_write" in sql
            writes.append(values)

    pool = SimpleNamespace(acquire=Connection)
    asyncio.run(namespace["shelly_sync"](pool))
    if expected is None:
        assert not writes
    else:
        assert len(writes) == 1
        values = writes[0]
        assert (values[1], values[2], values[4]) == expected
        assert values[0].tzinfo is UTC
