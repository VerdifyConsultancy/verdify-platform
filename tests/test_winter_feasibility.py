"""Prospective calendar and immutable read-only extraction integrity."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "research/planner-efficacy/winter_feasibility.py"
ROOT = SOURCE.parents[2]
spec = importlib.util.spec_from_file_location("winter_feasibility", SOURCE)
assert spec and spec.loader
winter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(winter)


def instance() -> dict:
    return {
        "schema": winter.INSTANCE_SCHEMA,
        "study_id": winter.STUDY_ID,
        "greenhouse_id": "vallery",
        "timezone": "America/Denver",
        "start_local_date": "2026-11-02",
        "day_count": 60,
        "registered_at": "2026-09-27T16:00:00+00:00",
        "source_git_sha": "a" * 40,
        "extractor_sha256": "b" * 64,
        "protocol_sha256": "c" * 64,
        "panel_source_sha256": "d" * 64,
        "crop_target_source_sha256": "e" * 64,
        "cfg_schema_sha256": "f" * 64,
        "firmware_revision": "2026.7.10.1500.09ee886",
        "observer_role": "read_only_research",
        "archive_id": "winter-2026-27-v1",
    }


def target_source() -> dict:
    start = date(2026, 11, 2)
    bins = [
        {
            "bucket_start": (
                winter.bounds(start + timedelta(days=day))[0] + timedelta(minutes=15 * quarter)
            ).isoformat(),
            "temp_low": 60.0,
            "temp_high": 80.0,
            "vpd_low": 0.5,
            "vpd_high": 1.5,
        }
        for day in range(60)
        for quarter in range(72)
    ]
    return {
        "schema": winter.TARGET_SCHEMA,
        "study_id": winter.STUDY_ID,
        "greenhouse_id": "vallery",
        "timezone": "America/Denver",
        "start_local_date": "2026-11-02",
        "recorded_at": "2026-09-27T15:00:00+00:00",
        "effective_from": winter.bounds(start)[0].isoformat(),
        "effective_to": winter.bounds(start + timedelta(days=59))[1].isoformat(),
        "target_version": "test-prospective-crop-v1",
        "fixed_panel_target_revision_id": 1,
        "source_profile_state_sha256": "a" * 64,
        "profile_revision_ids": [1, 2],
        "crop_assignment_revision_sha256": "b" * 64,
        "target_bins_sha256": winter.digest(winter.canonical(bins)),
        "target_bins": bins,
    }


def test_winter_calendar_is_sixty_actual_eighteen_hour_windows():
    study = instance()
    assert winter.validate_instance(study, require_current_source=False)[0] == date(2026, 11, 2)
    first = winter.bounds(date(2026, 11, 2))
    last = winter.bounds(date(2026, 12, 31))
    assert first[0] == datetime(2026, 11, 2, 13, tzinfo=UTC)
    assert first[1] == datetime(2026, 11, 3, 7, tzinfo=UTC)
    assert last[1] == datetime(2027, 1, 1, 7, tzinfo=UTC)
    assert last[1] - first[0] == timedelta(days=59, hours=18)


def test_registration_and_dst_fail_closed():
    study = instance()
    study["registered_at"] = "2026-11-02T13:00:00+00:00"
    with pytest.raises(ValueError, match="not registered before"):
        winter.validate_instance(study, require_current_source=False)
    study = instance()
    study["start_local_date"] = "2027-03-01"
    with pytest.raises(ValueError, match="offset transition"):
        winter.validate_instance(study, require_current_source=False)


def test_register_binds_real_source_bytes_and_rejects_missed_start(tmp_path):
    files = [tmp_path / name for name in ("panel.json", "target.json", "cfg.json")]
    for index, path in enumerate(files):
        path.write_bytes(f"source-{index}\n".encode())
    files[1].write_bytes(winter.canonical(target_source()))
    expected = instance()
    with patch.object(
        winter,
        "_source_identity",
        return_value=(expected["source_git_sha"], expected["extractor_sha256"], expected["protocol_sha256"]),
    ):
        value = winter.register(
            start=date(2026, 11, 2),
            panel_source=files[0],
            crop_target_source=files[1],
            cfg_schema_source=files[2],
            firmware_revision=expected["firmware_revision"],
            observer_role=expected["observer_role"],
            archive_id=expected["archive_id"],
            now=datetime(2026, 9, 27, 16, tzinfo=UTC),
        )
        assert value["panel_source_sha256"] == winter.digest(files[0].read_bytes())
        assert value["crop_target_source_sha256"] == winter.digest(files[1].read_bytes())
        archive = tmp_path / "archive" / "sources"
        archive.mkdir(parents=True)
        for source, name in zip(files, winter.ARCHIVED_SOURCES.values(), strict=True):
            winter._write_exclusive(archive / name, source.read_bytes())
        winter.verify_archived_sources(value, archive.parent)
        (archive / "panel-source").write_bytes(b"tampered\n")
        with pytest.raises(ValueError, match="missing or changed"):
            winter.verify_archived_sources(value, archive.parent)
        with pytest.raises(ValueError, match="before registration and first observation"):
            winter.register(
                start=date(2026, 11, 2),
                panel_source=files[0],
                crop_target_source=files[1],
                cfg_schema_source=files[2],
                firmware_revision=expected["firmware_revision"],
                observer_role=expected["observer_role"],
                archive_id=expected["archive_id"],
                now=datetime(2026, 11, 2, 13, tzinfo=UTC),
            )
        files[1].write_bytes(
            (
                ROOT
                / "research/planner-efficacy/protocols/winter-2026-27-source-candidates/crop-target-reference-only.json"
            ).read_bytes()
        )
        with pytest.raises(ValueError, match="canonical frozen-target contract"):
            winter.register(
                start=date(2026, 11, 2),
                panel_source=files[0],
                crop_target_source=files[1],
                cfg_schema_source=files[2],
                firmware_revision=expected["firmware_revision"],
                observer_role=expected["observer_role"],
                archive_id=expected["archive_id"],
                now=datetime(2026, 9, 27, 16, tzinfo=UTC),
            )


def test_frozen_target_rejects_marker_only_missing_bin_and_tampering():
    now = datetime(2026, 9, 27, 16, tzinfo=UTC)
    start = date(2026, 11, 2)
    with pytest.raises(ValueError, match="canonical frozen-target contract"):
        winter.validate_target_source(
            winter.canonical(
                {"qualified_prospective_crop_target": True, "frozen_15_minute_target_bins_available": True}
            ),
            start=start,
            registered_at=now,
        )
    valid = target_source()
    winter.validate_target_source(winter.canonical(valid), start=start, registered_at=now)
    missing = target_source()
    missing["target_bins"].pop(23)
    with pytest.raises(ValueError, match="exactly 4,320"):
        winter.validate_target_source(winter.canonical(missing), start=start, registered_at=now)
    changed = target_source()
    changed["target_bins"][0]["temp_high"] = 81.0
    with pytest.raises(ValueError, match="bins hash mismatch"):
        winter.validate_target_source(winter.canonical(changed), start=start, registered_at=now)
    revised = target_source()
    revised["fixed_panel_target_revision_id"] = 0
    with pytest.raises(ValueError, match="positive fixed-panel target revision"):
        winter.validate_target_source(winter.canonical(revised), start=start, registered_at=now)


def test_all_queries_are_selects_and_day_coverage_discloses_raw_basis():
    assert set(winter.QUERIES) == {
        "climate_flush",
        "cfg_readback",
        "served_band",
        "forecast_asof",
        "outdoor_observed",
        "firmware_observed",
        "equipment_edges",
        "equipment_seed",
        "power_observed",
        "water_events",
        "safety_events",
    }
    assert all(query.strip().upper().startswith("SELECT ") for query in winter.QUERIES.values())
    start, end = winter.bounds(date(2026, 11, 2))
    sources = {name: [] for name in winter.QUERIES}
    sources["climate_flush"] = [
        {
            "ts": (start + timedelta(minutes=minute)).isoformat(),
            **{field: 1.0 for field in ("temp_north", "vpd_north", "temp_east", "vpd_east", "temp_west", "vpd_west")},
        }
        for minute in range(12)
    ]
    sources["cfg_readback"] = [{"ts": start.isoformat(), "parameter": field, "value": 1.0} for field in ("a", "b")]
    coverage = winter._coverage(sources, start, end, ("a", "b"))
    assert coverage["bins_meeting_12_of_15_database_minutes"] == 1
    assert coverage["complete_48_cfg_flush_batches"] == 1
    assert coverage["physical_panel_eligible"] is False


def test_source_candidates_bind_current_bytes_without_false_qualification():
    folder = ROOT / "research/planner-efficacy/protocols/winter-2026-27-source-candidates"
    panel = json.loads((folder / "route-only-panel.json").read_bytes())
    target = json.loads((folder / "crop-target-reference-only.json").read_bytes())
    cfg = json.loads((folder / "cfg-schema-source.json").read_bytes())
    for candidate in (panel, target, cfg):
        for relative, expected in candidate["source_file_sha256"].items():
            assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
    assert [member["zone"] for member in panel["members"]] == ["north", "east", "west"]
    assert panel["physical_hardware_identity_verified"] is False
    assert target["qualified_prospective_crop_target"] is False
    assert target["frozen_15_minute_target_bins_available"] is False
    from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER

    assert cfg["canonical_field_order"] == list(CANONICAL_FIELD_ORDER)
    assert cfg["field_count"] == 48


def test_manifest_accounts_for_every_day_and_rejects_tampering(tmp_path):
    study = instance()
    raw_instance = winter.canonical(study)
    day = date(2026, 11, 2)
    start, end = winter.bounds(day)
    _, instance_sha = winter.validate_instance(study, require_current_source=False)
    sources = {name: [] for name in winter.QUERIES}
    day_payload = {
        "schema": winter.DAY_SCHEMA,
        "instance_sha256": instance_sha,
        "local_day": day.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "extracted_at": "2026-11-03T08:00:00+00:00",
        "daily_collection_timely": True,
        "sources": sources,
    }
    days = tmp_path / "days"
    days.mkdir()
    artifact = days / "2026-11-02.json"
    winter._write_exclusive(artifact, winter.canonical(day_payload))
    with patch.object(
        winter,
        "_source_identity",
        return_value=(study["source_git_sha"], study["extractor_sha256"], study["protocol_sha256"]),
    ):
        result = winter.manifest(study, raw_instance, days, as_of=datetime(2026, 11, 4, 12, tzinfo=UTC))
        assert len(result["calendar"]) == 60
        assert result["calendar"][0]["status"] == "captured"
        assert result["calendar"][0]["missing_reason"] == "no_climate_flush_rows"
        assert result["calendar"][1]["status"] == "uncollected_completed"
        assert result["calendar"][-1]["status"] == "scheduled"
        with pytest.raises(FileExistsError):
            winter._write_exclusive(artifact, b"replacement\n")
        artifact.write_bytes(json.dumps(day_payload).encode())
        with pytest.raises(ValueError, match="canonical bytes mismatch"):
            winter.manifest(study, raw_instance, days, as_of=datetime(2026, 11, 4, 12, tzinfo=UTC))
