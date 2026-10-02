"""Synthetic custody regressions only; never assigned or physical outcome proof."""

import copy
import hashlib
import importlib.util
import json
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("capture_inputs", ROOT / "scripts/experiment-v2-capture-inputs.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"


def snapshot():
    cutoff = "2026-10-01T20:56:27+00:00"
    source = {"observed_at": "2026-10-01T20:00:00+00:00", "values": {}}
    source_text = json.dumps(source)
    source["source_row_sha256"] = hashlib.sha256(
        b"verdify-experiment-v2-selector-source-v1\0" + source_text.encode()
    ).hexdigest()
    payload = {
        "context_cutoff_at": cutoff,
        "boundary_at": "2026-10-02T12:00:00+00:00",
        "climate_observations": [source],
        "forecast_vintage": [],
    }
    raw = json.dumps(payload).encode()
    value = {
        "experiment_id": ID,
        "as_of": cutoff,
        "context_cutoff_at": cutoff,
        "context_boundary_at": payload["boundary_at"],
        "prior_completed_window_start": "2026-09-30T12:00:00+00:00",
        "prior_completed_window_end": "2026-10-01T06:00:00+00:00",
        "cutoff_kind": "actual_current_clock_not_preregistered_pre06",
        "claim_scope": m.CLAIM,
        "context": {
            "context_canonical_hex": raw.hex(),
            "context_payload_pg_text": raw.decode(),
            "context_payload": payload,
            "context_sha256": hashlib.sha256(raw).hexdigest(),
            "context_status": "frozen",
            "failure_reason": None,
            "source_max_at": source["observed_at"],
            "source_row_pg_texts": [source_text],
            "source_bundle_sha256": hashlib.sha256(
                b"verdify-experiment-v2-selector-source-bundle-v1\0" + source["source_row_sha256"].encode()
            ).hexdigest(),
        },
        "installed_context_functions": [],
        "equipment_receipts": [
            {
                "events_canonical": "\\x5b5d",
                "events_sha256": hashlib.sha256(b"[]").hexdigest(),
                "gap_before": True,
                "gap_reason": "initial_receipt",
            }
        ],
    }
    for key in [
        "climate_source_rows",
        "fixed_targets",
        "contributors",
        "profile_revisions",
        "direct_snapshots",
        "counter_samples",
        "native_source_groups",
        "native_frozen_day_receipts",
    ]:
        value[key] = []
    return value


def test_actual_clock_missing_inputs_never_become_warm_outcomes(tmp_path):
    value = snapshot()
    for minute in range(12):
        row = {"ts": f"2026-09-30T12:{minute:02}:00+00:00", "greenhouse_id": "vallery"}
        row.update({f"{field}_{zone}": 1.0 for field in ("temp", "vpd") for zone in ("north", "east", "west")})
        value["climate_source_rows"].extend([row, copy.deepcopy(row)])
    summary = m.summarize(value, ID)
    assert summary["fixed_panel_climate"]["distinct_complete_minutes"] == 12
    assert summary["fixed_panel_climate"]["eligible_bins_min12minutes"] == 1
    assert summary["fixed_panel_climate"]["longest_missing_complete_minute_run"] == 1068
    assert all(item is None for item in summary["scientific_inputs"].values())
    receipt = m.publish(tmp_path / "new", value, ID)
    for name, digest in receipt["files"].items():
        path = tmp_path / "new" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "new").stat().st_mode) == 0o700
    with pytest.raises(FileExistsError):
        m.publish(tmp_path / "new", value, ID)


@pytest.mark.parametrize(
    "case",
    [
        "study",
        "window",
        "cutoff",
        "pre06",
        "bytes",
        "source_text",
        "source_hash",
        "bundle",
        "future_source",
        "equipment",
        "duplicate",
    ],
)
def test_fail_closed_custody_and_cutoff(tmp_path, case):
    value = snapshot()
    c = value["context"]
    if case == "study":
        value["experiment_id"] = "00000000-0000-0000-0000-000000000001"
    elif case == "window":
        value["prior_completed_window_start"] = "2026-09-30T08:00:00+00:00"
    elif case == "cutoff":
        value["context_cutoff_at"] = "2026-10-01T12:00:00+00:00"
    elif case == "pre06":
        value["cutoff_kind"] = "qualified_pre06"
    elif case == "bytes":
        c["context_canonical_hex"] += "00"
    elif case == "source_text":
        c["source_row_pg_texts"][0] = "{}"
    elif case == "source_hash":
        c["context_payload"]["climate_observations"][0]["source_row_sha256"] = "0" * 64
        raw = json.dumps(c["context_payload"]).encode()
        c.update(
            context_canonical_hex=raw.hex(),
            context_payload_pg_text=raw.decode(),
            context_sha256=hashlib.sha256(raw).hexdigest(),
        )
    elif case == "bundle":
        c["source_bundle_sha256"] = "0" * 64
    elif case == "future_source":
        c["source_max_at"] = "2026-10-03T00:00:00+00:00"
    elif case == "equipment":
        value["equipment_receipts"][0]["events_canonical"] = "\\x5b315d"
    elif case == "duplicate":
        # Valid hash of raw bytes does not excuse duplicate keys, including nested source values.
        raw = c["context_payload_pg_text"].replace('"values": {}', '"values": {"x": 1, "x": 2}').encode()
        c.update(
            context_canonical_hex=raw.hex(),
            context_payload_pg_text=raw.decode(),
            context_sha256=hashlib.sha256(raw).hexdigest(),
        )
    with pytest.raises(ValueError):
        m.publish(tmp_path / "refused", value, ID)
    assert not (tmp_path / "refused").exists()


def test_query_uses_actual_clock_read_only_functions_and_no_calendar_substitution():
    sql = m.query(ID)
    assert "statement_timestamp()" in sql
    assert "fn_experiment_v2_build_selector_context" in sql
    assert "fn_experiment_v2_selector_cycle" not in sql
    assert "time '06:00'" in sql and "w.today-1" in sql
    assert "source_observed_through<=w.as_of" in sql
    with pytest.raises(ValueError):
        m.query("'; DELETE FROM experiments; --")


def test_unavailable_context_and_zero_null_reset_rows_are_preserved(tmp_path):
    value = snapshot()
    context = value["context"]
    payload = {
        "context_cutoff_at": value["as_of"],
        "boundary_at": value["context_boundary_at"],
        "reason": "no_usable_precutoff_climate_source",
    }
    raw = json.dumps(payload).encode()
    context.update(
        context_payload=payload,
        context_canonical_hex=raw.hex(),
        context_payload_pg_text=raw.decode(),
        context_sha256=hashlib.sha256(raw).hexdigest(),
        source_row_pg_texts=[],
        context_status="unavailable",
        failure_reason=payload["reason"],
        source_max_at=None,
        source_bundle_sha256=hashlib.sha256(
            b"verdify-experiment-v2-selector-source-unavailable-v1\0" + raw
        ).hexdigest(),
    )
    value["counter_samples"] = [{"counter": 0, "reset_epoch": 2}, {"counter": None, "reset_epoch": 3}]
    receipt = m.publish(tmp_path / "nulls", value, ID)
    original = json.loads((tmp_path / "nulls" / "snapshot.json").read_bytes())
    assert original["counter_samples"] == value["counter_samples"]
    assert receipt["summary"]["context_status"] == "unavailable"
    assert receipt["summary"]["scientific_inputs"]["joint_power"] is None


def test_oversize_refuses_without_truncation_or_output(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="do not truncate"):
        m.publish(tmp_path / "large", snapshot(), ID)
    assert not (tmp_path / "large").exists()
