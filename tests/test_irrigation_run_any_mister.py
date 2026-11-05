"""#1343: the API keeps ``not_schedule_member`` for a mister's run-now until the controller runs the firmware that
drops it, read from its ``fw_version`` stamp (``YYYY.M.D.HHMM.<sha>``). DB-free; the DB-backed refusal and acceptance
are in test_dashboard_db_irrigation_writes.py."""

from __future__ import annotations

import pytest

from verdify_services.irrigation.writes import (
    FOG_RUN_FIRMWARE_FROM,
    RUN_ANY_MISTER_FIRMWARE_FROM,
    _resolve_target,
    firmware_runs_any_mister,
    firmware_runs_fog,
)
from verdify_services.problems import ServiceError


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (None, False),
        ("", False),
        ("unknown", False),
        ("2026.11.5.0131.57fe2a2e", False),  # live before #1343
        ("2026.7.3.1931.ab18fe8", False),
        ("2026.13.1.0000.abc", False),  # not a date
        ("2026.11.6.0000.abcdef1", True),
        ("2026.11.6.0000.abcdef1.dirty", True),
        ("2027.1.2.1200.abcdef1", True),
    ],
)
def test_the_firmware_stamp_decides(version, expected):
    assert firmware_runs_any_mister(version) is expected


def test_the_cutoff_is_after_the_last_firmware_without_the_change():
    assert RUN_ANY_MISTER_FIRMWARE_FROM.isoformat() == "2026-11-06"


# #1343 follow-up and #1336: fog runs once only on the firmware that takes it (a new cutoff, after #1343's).
@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (None, False),
        ("2026.11.6.0000.abcdef1", False),  # #1343's firmware day: not yet
        ("2026.11.7.0000.abcdef1", True),
        ("2027.1.2.1200.abcdef1", True),
    ],
)
def test_the_fog_run_once_firmware_stamp_decides(version, expected):
    assert firmware_runs_fog(version) is expected


def test_the_fog_cutoff_follows_the_mister_cutoff():
    assert FOG_RUN_FIRMWARE_FROM > RUN_ANY_MISTER_FIRMWARE_FROM


def test_a_physical_line_and_variant_name_their_relay():
    assert _resolve_target(None, "south_mister", "fertilized") == "L08"
    assert _resolve_target("L08", "south_mister", "fertilized") == "L08"
    assert _resolve_target(None, None, "clean", current_line="L08") == "L07"  # an edit switches variant in place
    assert _resolve_target("L01", None, None) == "L01"
    for args, code, field in (
        ((None, "center_mister", "fertilized"), "unknown_target", "variant"),  # not wired
        ((None, "south_mister", None), "invalid_request", "variant"),
        (("L07", "south_mister", "fertilized"), "invalid_request", "line_id"),
        ((None, None, "clean"), "invalid_request", "circuit_id"),
    ):
        with pytest.raises(ServiceError) as refused:
            _resolve_target(*args)
        assert (refused.value.problem.code, refused.value.problem.field) == (code, field)
