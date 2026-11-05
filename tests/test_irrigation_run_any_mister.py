"""#1343: the API keeps ``not_schedule_member`` for a mister's run-now until the controller runs the firmware that
drops it, read from its ``fw_version`` stamp (``YYYY.M.D.HHMM.<sha>``). DB-free; the DB-backed refusal and acceptance
are in test_dashboard_db_irrigation_writes.py."""

from __future__ import annotations

import pytest

from verdify_services.irrigation.writes import RUN_ANY_MISTER_FIRMWARE_FROM, firmware_runs_any_mister


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
