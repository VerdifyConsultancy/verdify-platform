"""Migration-255 restored SQL freezer bytes consumed directly by the analyzer."""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from switchback.v2_analysis import (
    SQL_EVIDENCE_BUNDLE_DOMAIN,
    SQL_EXPORT_DOMAIN,
    analyze_revealed_sql_export,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_BYTES = (ROOT / "tests/fixtures/experiment_v2_restored_255_sql_export.json").read_bytes()
assert FIXTURE_BYTES.endswith(b"\n") and not FIXTURE_BYTES.endswith(b"\n\n")
RAW = FIXTURE_BYTES[:-1]  # The repo text-file newline is not part of PostgreSQL jsonb::text.
SHA256 = "f81d0bacb96cdf11faf4697ce76841530a18199390c24087e23aa638cc15759d"
EXPERIMENT_ID = "21421421-4214-4214-8214-214214214214"
ANALYZER_SHA256 = "e" * 64


def analyze(raw: bytes = RAW, digest: str = SHA256) -> dict:
    return analyze_revealed_sql_export(
        raw,
        digest,
        expected_experiment_id=EXPERIMENT_ID,
        expected_analyzer_environment_sha256=ANALYZER_SHA256,
        locked_pairs=2,
        ai_label="X",
    )


def reseal(payload: dict) -> tuple[bytes, str]:
    # A corrupted but rehashed fixture must still fail the structural contract.
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return raw, hashlib.sha256(SQL_EXPORT_DOMAIN + raw).hexdigest()


def test_exact_restored_sql_export_replays_twice_into_paired_decision():
    assert hashlib.sha256(SQL_EXPORT_DOMAIN + RAW).hexdigest() == SHA256
    assert b'"physical_arm"' not in RAW and b'"mapping"' not in RAW
    payload = json.loads(RAW)
    assert (
        hashlib.sha256(
            SQL_EVIDENCE_BUNDLE_DOMAIN
            + "".join(row["evidence_bundle_sha256"] for row in payload["rows"]).encode("ascii")
        ).hexdigest()
        == payload["evidence_bundle_sha256"]
    )
    first = analyze()
    second = analyze()
    assert first == second
    assert first["source_export_sha256"] == SHA256
    assert first["assigned_days"] == 4
    assert first["decision"] == "inconclusive_null_endpoint"
    assert first["missing"] == [
        (0, "vpd_corridor_distance_kpa"),
        (0, "temperature_corridor_distance_f"),
        (0, "nine_control_state_minutes"),
    ]
    assert first["no_pair_replacement"] is True


def test_sql_export_bytes_and_bound_identities_fail_closed():
    with pytest.raises(ValueError, match="byte/hash binding"):
        analyze(RAW + b" ")
    with pytest.raises(ValueError, match="experiment identity"):
        analyze_revealed_sql_export(
            RAW,
            SHA256,
            expected_experiment_id="00000000-0000-4000-8000-000000000000",
            expected_analyzer_environment_sha256=ANALYZER_SHA256,
            locked_pairs=2,
            ai_label="X",
        )
    with pytest.raises(ValueError, match="analyzer identity"):
        analyze_revealed_sql_export(
            RAW,
            SHA256,
            expected_experiment_id=EXPERIMENT_ID,
            expected_analyzer_environment_sha256="f" * 64,
            locked_pairs=2,
            ai_label="X",
        )


def test_rehashed_mapping_duplicate_and_endpoint_flag_corruption_fail_closed():
    payload = json.loads(RAW)
    payload["rows"][0]["physical_arm"] = "A"
    with pytest.raises(ValueError, match="row shape"):
        analyze(*reseal(payload))

    payload = json.loads(RAW)
    payload["rows"][1]["assignment_id"] = payload["rows"][0]["assignment_id"]
    with pytest.raises(ValueError, match="assignment"):
        analyze(*reseal(payload))

    payload = json.loads(RAW)
    payload["rows"][1]["null_value_retained"] = False
    with pytest.raises(ValueError, match="zero/null flags"):
        analyze(*reseal(payload))

    payload = json.loads(RAW)
    payload["rows"][0]["outcome"] = {"endpoint": 0}
    with pytest.raises(ValueError, match="outcome schema"):
        analyze(*reseal(payload))

    raw = RAW.replace(b'"rows": ', b'"rows": [], "rows": ', 1)
    assert raw != RAW
    with pytest.raises(ValueError, match="duplicate JSON key"):
        analyze(raw, hashlib.sha256(SQL_EXPORT_DOMAIN + raw).hexdigest())


def test_rehashed_corrupt_itt_range_and_evidence_bundle_fail_closed():
    payload = json.loads(RAW)
    payload["rows"][0]["itt_range"] = '["2026-09-29 13:00:00+00","2026-09-30 06:00:00+00")'
    with pytest.raises(ValueError, match="ITT range differs"):
        analyze(*reseal(payload))

    payload = json.loads(RAW)
    payload["rows"][0]["itt_range"] = '["2026-09-29 12:00:00","2026-09-30 06:00:00+00")'
    with pytest.raises(ValueError, match="lacks UTC offsets"):
        analyze(*reseal(payload))

    payload = json.loads(RAW)
    payload["rows"][0]["evidence_bundle_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="evidence bundle differs"):
        analyze(*reseal(payload))


@pytest.mark.parametrize("first_day", [date(2026, 3, 7), date(2026, 10, 31)])
def test_denver_itt_ranges_cover_dst_offsets(first_day: date):
    payload = json.loads(RAW)
    denver = ZoneInfo("America/Denver")
    for position, row in enumerate(payload["rows"]):
        local_day = first_day + timedelta(days=position)
        next_day = local_day + timedelta(days=1)
        start = datetime.combine(local_day, time(6), denver).astimezone(UTC)
        end = datetime.combine(next_day, time.min, denver).astimezone(UTC)
        row["assigned_local_date"] = local_day.isoformat()
        row["itt_range"] = f'["{start:%Y-%m-%d %H:%M:%S}+00","{end:%Y-%m-%d %H:%M:%S}+00")'
    assert analyze(*reseal(payload))["decision"] == "inconclusive_null_endpoint"
