"""VD-72: the leak alert outlives the flag (US-WAT-18, J6; an alert only, Q-05).

Runs the owning leak read and envelope and _sync_monitor_alerts from
ingestor/tasks/alerts.py (see alert_lifecycle_harness) against an in-memory
alert_log, equipment_state flag and site meter. No database, network or device.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from alert_lifecycle_harness import AlertLog, load_alert_lifecycle, local

KEY = ("leak_detected", "equipment.leak_detected")


def leak_cycle(ns, log, when, *, before_sync=None):
    """One alert_monitor pass of the leak section and the shared sync."""
    log.now = when
    leak, refresh_only = asyncio.run(ns["_leak_cycle_alert"](log, now=when))
    if before_sync is not None:
        before_sync()  # something that happens between the leak read and the sync
    return asyncio.run(
        ns["_sync_monitor_alerts"](
            log,
            [leak] if leak else [],
            component_status_available=True,
            component_closure_resolutions={},
            refresh_only_keys=refresh_only,
            now=when,
        )
    )


def flag(log, when, state):
    log.equipment.append({"ts": when, "equipment": "leak_detected", "state": state})


def meter(log, start, *gpm):
    """One meter reading a minute from ``start``."""
    log.flow.extend((start + timedelta(minutes=i), value) for i, value in enumerate(gpm))


def stopped(row):
    value = row["details"]["stopped_at"]
    return None if value is None else datetime.fromisoformat(value)


def test_flag_on_off_then_two_quiet_readings_read_flow_stopped_and_stay_open():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    meter(log, local(0, 2, 50), *[1.2] * 28)  # 02:50 to 03:17
    flag(log, local(0, 3, 2), True)
    leak_cycle(ns, log, local(0, 3, 5))
    [row] = log.of(*KEY)
    assert row["severity"] == "critical" and row["category"] == "water"
    assert row["message"] == "Water flowing with every Verdify valve closed since 03:02"  # America/Denver
    assert datetime.fromisoformat(row["details"]["since"]) == local(0, 3, 2) and stopped(row) is None

    flag(log, local(0, 3, 18), False)
    meter(log, local(0, 3, 18), 0.05)  # one quiet reading is not yet "stopped"
    leak_cycle(ns, log, local(0, 3, 18))
    assert row["resolved_at"] is None and stopped(row) is None
    assert row["message"] == "Water flowing with every Verdify valve closed since 03:02"

    meter(log, local(0, 3, 19), 0.0)
    leak_cycle(ns, log, local(0, 3, 20))
    assert log.of(*KEY) == [row] and row["resolved_at"] is None
    assert stopped(row) == local(0, 3, 19) and row["message"] == "Flow stopped at 03:19"
    assert datetime.fromisoformat(row["details"]["since"]) == local(0, 3, 2)  # carried over
    assert row["severity"] == "critical"
    [read] = [args for sql, args in log.reads if "FROM climate WHERE ts >= $1" in sql][-1:]
    assert read == (row["ts"], 0.1, 2)  # bounded: since the open row, at most the quiet run's first two


def test_flow_that_starts_again_clears_stopped_at_on_the_same_row():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    meter(log, local(0, 3), 1.0, 1.0, 1.0)
    flag(log, local(0, 3, 1), True)
    leak_cycle(ns, log, local(0, 3, 2))
    flag(log, local(0, 3, 3), False)
    meter(log, local(0, 3, 3), 0.0, 0.0)
    leak_cycle(ns, log, local(0, 3, 6))
    [row] = log.of(*KEY)
    assert stopped(row) == local(0, 3, 4)

    meter(log, local(0, 3, 5), 0.6)  # the hose comes back on; the flag has not tripped again
    leak_cycle(ns, log, local(0, 3, 7))
    assert log.of(*KEY) == [row] and stopped(row) is None
    assert row["message"] == "Water flowing with every Verdify valve closed since 03:01"

    meter(log, local(0, 3, 6), 0.08, 0.0)  # under 0.1 twice more
    leak_cycle(ns, log, local(0, 3, 9))
    assert stopped(row) == local(0, 3, 7) and row["message"] == "Flow stopped at 03:07"

    flag(log, local(0, 3, 30), True)  # the flag trips again: flowing, stopped_at back to null
    leak_cycle(ns, log, local(0, 3, 31))
    assert log.of(*KEY) == [row] and stopped(row) is None
    assert row["message"] == "Water flowing with every Verdify valve closed since 03:30"


def test_acknowledgement_keeps_it_open_and_only_a_resolve_closes_it():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    meter(log, local(0, 3), 1.0, 1.0, 0.0, 0.0)
    flag(log, local(0, 3, 1), True)
    leak_cycle(ns, log, local(0, 3, 1))
    flag(log, local(0, 3, 2), False)
    leak_cycle(ns, log, local(0, 3, 5))
    [row] = log.of(*KEY)
    log.now = local(0, 3, 10)
    log.acknowledge(row["id"])
    for hour in range(4, 9):  # hours of cycles with the flag off and the meter quiet
        leak_cycle(ns, log, local(0, hour))
        assert row["resolved_at"] is None and row["disposition"] == "acknowledged"
        assert row["message"] == "Flow stopped at 03:03"
    assert log.open_view() == [row] and log.one_count() == 0  # acknowledged: still open, not counted

    log.now = local(0, 9)
    log.resolve(row["id"], by="jason", resolution="hose left on")
    leak_cycle(ns, log, local(0, 9, 5))
    leak_cycle(ns, log, local(0, 10))
    assert log.of(*KEY) == [row] and row["resolved_by"] == "jason" and log.open_view() == []


def test_auto_resolve_never_closes_the_leak_row():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    row = log.seed(
        alert_type="leak_detected",
        severity="critical",
        category="water",
        sensor_id="equipment.leak_detected",
        message="Water flowing with every Verdify valve closed since 03:00",
        details={"since": local(0, 3).isoformat(), "stopped_at": None},
    )
    asyncio.run(
        ns["_sync_monitor_alerts"](
            log, [], component_status_available=True, component_closure_resolutions={}, now=local(0, 4)
        )
    )
    assert row["resolved_at"] is None and log.writes == []


def test_a_resolve_while_water_still_flows_opens_a_new_episode():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    meter(log, local(0, 3), *[1.0] * 30)
    flag(log, local(0, 3), True)
    leak_cycle(ns, log, local(0, 3, 5))
    [first] = log.of(*KEY)
    log.now = local(0, 3, 6)
    log.resolve(first["id"])
    leak_cycle(ns, log, local(0, 3, 10))
    old, new = log.of(*KEY)
    assert old is first and new["resolved_at"] is None


def test_no_flag_and_no_open_row_raise_nothing():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    assert asyncio.run(ns["_leak_cycle_alert"](log)) == (None, frozenset())
    flag(log, local(0, 3), False)
    meter(log, local(0, 3), 0.0, 0.0)
    assert asyncio.run(ns["_leak_cycle_alert"](log)) == (None, frozenset())
    assert not [sql for sql, _ in log.reads if "FROM climate" in sql]  # no open row: no meter read


def test_a_resolve_between_the_leak_read_and_the_sync_inserts_nothing():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    meter(log, local(0, 3), 1.0, 1.0, 0.0, 0.0)
    flag(log, local(0, 3, 1), True)
    leak_cycle(ns, log, local(0, 3, 1))
    [row] = log.of(*KEY)
    flag(log, local(0, 3, 2), False)
    # The flag is off: the section reads the open row, then a person resolves it before the sync.
    leak_cycle(ns, log, local(0, 3, 10), before_sync=lambda: log.resolve(row["id"], by="jason"))
    assert log.of(*KEY) == [row] and row["resolved_by"] == "jason"
    assert len(log.of(*KEY)) == 1  # no second critical row for water that stopped

    flag(log, local(0, 4), True)  # with the flag on, a resolved leak is a new episode, as before
    leak_cycle(ns, log, local(0, 4, 1))
    assert len(log.of(*KEY)) == 2


def test_a_long_open_row_reads_only_the_start_of_the_quiet_run():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 3))
    meter(log, local(0, 3), 1.0, 1.0)
    flag(log, local(0, 3, 1), True)
    leak_cycle(ns, log, local(0, 3, 1))
    flag(log, local(0, 3, 2), False)
    meter(log, local(0, 3, 2), *[0.0] * (3 * 24 * 60))  # nobody resolves it for three quiet days
    log.reads.clear()
    leak_cycle(ns, log, local(3, 3))
    [row] = log.of(*KEY)
    assert stopped(row) == local(0, 3, 3) and row["message"] == "Flow stopped at 03:03"
    [(sql, args)] = [(sql, args) for sql, args in log.reads if "FROM climate" in sql]
    assert args == (row["ts"], 0.1, 2) and sql.endswith("LIMIT $3")
