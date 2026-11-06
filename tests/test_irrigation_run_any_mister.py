"""#1343: the API keeps ``not_schedule_member`` for a mister's run-now until the controller runs the firmware that
drops it, read from its ``fw_version`` stamp (``YYYY.M.D.HHMM.<sha>``). DB-free; the DB-backed refusal and acceptance
are in test_dashboard_db_irrigation_writes.py."""

from __future__ import annotations

import pytest

from verdify_services.irrigation.writes import (
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


# #1343 follow-up: fog runs once only while irr_caps names fog_run for the version the controller runs. No date gate.
V = "2026.11.5.2200.abcdef1"


@pytest.mark.parametrize(
    ("version", "caps", "expected"),
    [
        (V, f"fw={V} caps=fog_run,run_any_mister", True),
        (V, f"fw={V} caps=run_any_mister", False),  # a build without the capability
        ("2026.11.5.2300.0123456", f"fw={V} caps=fog_run", False),  # a rollback or another build since
        (V, None, False),  # never published: fails closed
        (None, f"fw={V} caps=fog_run", False),
        (V, "fog_run", False),  # outside the grammar
    ],
)
def test_the_fog_run_once_capability_decides(version, caps, expected):
    assert firmware_runs_fog(version, caps) is expected


def test_a_same_day_build_with_the_capability_runs_any_mister():
    assert firmware_runs_any_mister(V) is False  # its stamp is before the cutoff
    assert firmware_runs_any_mister(V, f"fw={V} caps=fog_run,run_any_mister") is True
    assert firmware_runs_any_mister(V, f"fw={V}.dirty caps=run_any_mister") is False


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


# #1344: VPD control on a disabled mister would read ON and do nothing (the firmware's effective membership needs the
# enable bit), so the write is refused unless the same change enables the line.
def _line_row(enabled: bool) -> dict:
    return {"enabled": enabled, "manual": False, "checked_at": None}


@pytest.mark.parametrize(
    ("enabled", "changes", "refused"),
    [
        (False, {"vpd_member": True}, True),
        (True, {"vpd_member": True, "enabled": False}, True),
        (False, {"vpd_member": True, "enabled": True}, False),  # needs the check, which the dashboard sends
        (True, {"vpd_member": True}, False),
        (False, {"vpd_member": False}, False),
    ],
)
def test_vpd_control_needs_the_line_enabled(enabled, changes, refused):
    from verdify_services.actor import Actor
    from verdify_services.irrigation.writes import _check_line_fields

    person = Actor.person("jason", "operator")
    fields = {**changes, **({"checked": True} if changes.get("enabled") else {})}
    if refused:
        with pytest.raises(ServiceError) as caught:
            _check_line_fields(person, "L07", _line_row(enabled), fields, records_check=bool(fields.get("checked")))
        assert (caught.value.problem.code, caught.value.problem.field) == ("line_disabled", "vpd_member")
    else:
        _check_line_fields(person, "L07", _line_row(enabled), fields, records_check=bool(fields.get("checked")))
