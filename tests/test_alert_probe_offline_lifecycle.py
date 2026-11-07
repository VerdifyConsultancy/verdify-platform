"""VD-72: one alert_log row per offline probe (Round 2b C).

Runs the owning soil scan and _sync_monitor_alerts from ingestor/tasks/alerts.py
(see alert_lifecycle_harness) against an in-memory alert_log that holds every
write to the ingestor login's migration-217 grants. No database, network or device.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from alert_lifecycle_harness import (
    DENVER,
    AlertLog,
    forecast_stale,
    ingestor_alert_log_grants,
    load_alert_lifecycle,
    local,
    soil_counts,
    stale_sensor,
)

PROBE = ("soil_sensor_offline", "soil.west")
WEST = ("soil_moisture_west", "soil_temp_west")


def cycle(ns, log, when, *, silent=(), zones=("south", "west"), extra=()):
    """One alert_monitor pass: the soil section's scan, any extra envelopes, then the sync."""
    log.now = when
    alerts, keep_open, unpotted = ns["_soil_probe_offline_scan"](
        soil_counts(ns, silent), set(zones), when.astimezone(DENVER).hour
    )
    return asyncio.run(
        ns["_sync_monitor_alerts"](
            log,
            [*alerts, *extra],
            component_status_available=True,
            component_closure_resolutions={},
            keep_open_keys=keep_open,
            unpotted_keys=unpotted,
            now=when,
        )
    )


def seen(row) -> datetime:
    return datetime.fromisoformat(row["details"]["last_seen_at"])


def test_thirty_days_of_flaps_leave_one_row_with_count_and_last_seen():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 8))
    for day in range(30):
        cycle(ns, log, local(day, 8), silent=WEST)  # raised: inserted, then re-opened
        cycle(ns, log, local(day, 14), silent=WEST)  # refreshed in place
        cycle(ns, log, local(day, 23), silent=WEST)  # night: kept open
        if day == 0:
            [first] = log.of(*PROBE)
        if day < 29:
            cycle(ns, log, local(day + 1, 3))  # a stray reading resolves it
            assert first["resolution"] == "auto-resolved: probe reporting again"

    assert log.of(*PROBE) == [first]
    assert first["resolved_at"] is None and first["disposition"] == "open"
    assert first["details"]["count"] == 30 and seen(first) == local(29, 8)
    assert first["ts"] == local(0, 8) and first["updated_at"] == local(29, 14)  # first ts kept
    assert first["zone"] == "west" and first["details"]["columns"] == list(WEST)
    assert first["details"]["long_offline"] is False
    assert [row for row in log.open_view() if row["alert_type"] == "soil_sensor_offline"] == [first]
    assert log.one_count() == 1  # the probe's flaps add one to the one count


def test_night_gate_keeps_the_row_open_and_raises_nothing_new():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 21))
    cycle(ns, log, local(0, 21), silent=WEST)
    [row] = log.of(*PROBE)
    for when in (local(0, 22, 1), local(0, 23, 30), local(1, 2), local(1, 5, 55)):
        cycle(ns, log, when, silent=WEST)
        assert row["resolved_at"] is None
    cycle(ns, log, local(1, 6, 5), silent=WEST)  # 06:00 again: the same row, refreshed
    assert log.of(*PROBE) == [row]
    assert row["details"]["count"] == 1 and seen(row) == local(0, 21)

    quiet_night = AlertLog(local(0, 23))
    cycle(ns, quiet_night, local(0, 23), silent=WEST)  # a probe that fails at night raises nothing yet
    assert quiet_night.rows == []

    cycle(ns, log, local(1, 23))  # a reading at night is a return, as by day
    assert row["disposition"] == "resolved" and row["resolution"] == "auto-resolved: probe reporting again"


def test_scan_classifies_each_probe_by_occupancy_hour_and_silent_columns():
    ns = load_alert_lifecycle()
    scan = ns["_soil_probe_offline_scan"]
    counts = soil_counts(ns, silent=("soil_ec_south_1", "soil_moisture_west", "soil_temp_west"))

    alerts, keep_open, unpotted = scan(counts, {"south", "west"}, 6)
    assert {a["sensor_id"]: a["details"]["columns"] for a in alerts} == {
        "soil.south_1": ["soil_ec_south_1"],
        "soil.west": ["soil_moisture_west", "soil_temp_west"],
    }
    assert {a["zone"] for a in alerts} == {"south", "west"} and not keep_open and not unpotted
    assert all(a["severity"] == "warning" and a["details"]["occupancy"] == "occupied" for a in alerts)

    alerts, keep_open, unpotted = scan(counts, {"south", "west"}, 22)
    assert alerts == [] and keep_open == {("soil_sensor_offline", "soil.south_1"), PROBE} and not unpotted

    alerts, keep_open, unpotted = scan(counts, {"south"}, 12)
    assert [a["sensor_id"] for a in alerts] == ["soil.south_1"] and unpotted == {PROBE} and not keep_open


def test_unpotted_zone_raises_nothing_and_resolves_an_open_row_at_once():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 9))
    cycle(ns, log, local(0, 9), silent=WEST, zones=("south",))
    assert log.rows == []  # no active crop in the west zone: nothing, not even an info row

    cycle(ns, log, local(0, 10), silent=WEST)
    [row] = log.of(*PROBE)
    cycle(ns, log, local(0, 23), silent=WEST, zones=("south",))  # emptied at night: resolves at once
    assert row["disposition"] == "resolved" and row["resolution"] == "auto-resolved: zone unpotted"
    cycle(ns, log, local(1, 9), silent=WEST, zones=("south",))
    assert log.of(*PROBE) == [row] and row["resolved_at"] is not None

    acknowledged = AlertLog(local(0, 10))
    cycle(ns, acknowledged, local(0, 10), silent=WEST)
    [row] = acknowledged.of(*PROBE)
    acknowledged.acknowledge(row["id"])
    cycle(ns, acknowledged, local(0, 11), silent=WEST, zones=("south",))
    assert row["disposition"] == "suppressed"  # the acknowledged-suppressible rule is unchanged


@pytest.mark.parametrize("days", [7, 3])
def test_long_offline_resolves_and_its_return_lets_the_next_failure_reopen(days):
    ns = load_alert_lifecycle(resolve_days=days)
    log = AlertLog(local(0, 8))
    cycle(ns, log, local(0, 8), silent=WEST)
    [row] = log.of(*PROBE)
    for day in range(1, days):
        cycle(ns, log, local(day, 8), silent=WEST)
        cycle(ns, log, local(day, 23), silent=WEST)
        assert row["resolved_at"] is None
    cycle(ns, log, local(days, 8, 5), silent=WEST)
    assert row["resolution"] == f"auto-resolved: probe offline longer than {days} days"
    assert row["resolved_by"] == "system" and row["details"]["long_offline"] is True
    assert log.one_count() == 0 and log.open_view() == []

    writes = len(log.writes)
    for day in range(days + 1, days + 5):  # still offline: nothing is written, nothing re-opens
        cycle(ns, log, local(day, 8), silent=WEST)
        cycle(ns, log, local(day, 23), silent=WEST)
    assert len(log.writes) == writes and log.of(*PROBE) == [row]

    cycle(ns, log, local(days + 5, 12))  # the probe returns: the mark clears
    assert row["details"]["long_offline"] is False and row["resolved_at"] is not None
    cycle(ns, log, local(days + 6, 12), silent=WEST)  # its next failure re-opens the same row
    assert log.of(*PROBE) == [row]
    assert row["resolved_at"] is None and row["resolved_by"] is None and row["resolution"] is None
    assert row["disposition"] == "open" and row["details"]["count"] == 2 and seen(row) == local(days + 6, 12)


def test_long_offline_also_resolves_a_row_kept_open_at_night():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 21))
    cycle(ns, log, local(0, 21), silent=WEST)
    [row] = log.of(*PROBE)
    cycle(ns, log, local(7, 21, 30), silent=WEST)  # raised by day, over the period
    assert row["resolution"] == "auto-resolved: probe offline longer than 7 days"
    night = AlertLog(local(0, 21))
    cycle(ns, night, local(0, 21), silent=WEST)
    [kept] = night.of(*PROBE)
    cycle(ns, night, local(7, 23), silent=WEST)  # kept open at night, and over the period
    assert kept["resolution"] == "auto-resolved: probe offline longer than 7 days"
    assert kept["details"]["long_offline"] is True


def test_first_cycle_after_deploy_replaces_column_rows_and_resolves_old_probe_rows():
    ns = load_alert_lifecycle()
    now = local(0, 7)
    log = AlertLog(now)
    column_detail = {"column": "soil_moisture_west", "sensor": "soil.west", "occupancy": "occupied"}
    column_open = log.seed(
        alert_type="soil_sensor_offline",
        sensor_id="soil.west.soil_moisture_west",
        ts=now - timedelta(hours=1),
        message="Soil sensor `soil_moisture_west` has no data for 30 min",
        details=column_detail,
    )
    column_acknowledged = log.seed(
        alert_type="soil_sensor_offline",
        sensor_id="soil.west.soil_temp_west",
        ts=now - timedelta(days=30),
        message="Soil sensor `soil_temp_west` has no data for 30 min",
        details={**column_detail, "column": "soil_temp_west"},
        disposition="acknowledged",
        acknowledged_at=now - timedelta(days=29),
    )
    old_stale = log.seed(
        alert_type="sensor_offline",
        sensor_id="climate.rh_south",
        ts=now - timedelta(days=90),
        message="Sensor `climate.rh_south` offline",
        details={"type": "humidity", "staleness_ratio": None},
        disposition="acknowledged",
        acknowledged_at=now - timedelta(days=89),
    )
    young_stale = log.seed(
        alert_type="sensor_offline",
        sensor_id="climate.hydro_ph",
        ts=now - timedelta(days=2),
        message="Sensor `climate.hydro_ph` offline",
        details={"type": "hydroponic", "staleness_ratio": None},
    )
    forecast = log.seed(
        alert_type="sensor_offline",
        sensor_id="system.weather_forecast",
        category="system",
        ts=now - timedelta(days=30),
        message="Forecast data stale: last fetch 300min ago",
        details={"type": "forecast_sync", "staleness_ratio": 2.5},
    )
    raised = [stale_sensor("climate.rh_south", "humidity"), stale_sensor("climate.hydro_ph", "hydroponic")]
    cycle(ns, log, now, silent=WEST, extra=[*raised, forecast_stale()])

    assert column_open["disposition"] == "resolved"
    assert column_open["resolution"] == "auto-resolved: replaced by the per-probe alert"
    assert column_acknowledged["disposition"] == "suppressed"  # acknowledged + suppressible, as before
    [probe] = log.of(*PROBE)
    assert probe["details"]["count"] == 1 and seen(probe) == now and probe["zone"] == "west"
    assert old_stale["resolution"] == "auto-resolved: probe offline longer than 7 days"
    assert old_stale["details"]["long_offline"] is True and old_stale["acknowledged_at"] is not None
    assert young_stale["resolved_at"] is None and young_stale["details"]["count"] == 1
    assert seen(young_stale) == young_stale["ts"]  # carried over: the row's first raise
    assert forecast["resolved_at"] is None and forecast["details"]["last_seen_at"] is None

    cycle(ns, log, now + timedelta(minutes=5), silent=WEST, extra=[*raised, forecast_stale()])
    assert len(log.rows) == 6 and old_stale["resolved_at"] is not None
    open_probe_rows = [row for row in log.open_view() if row["alert_type"] in ns["PROBE_OFFLINE_ALERT_TYPES"]]
    assert not [row for row in open_probe_rows if row["sensor_id"].count(".") == 2]  # none per column
    stale_cut = log.now - timedelta(days=7)
    assert all(
        (seen(row) if row["details"]["last_seen_at"] else row["ts"]) >= stale_cut
        for row in open_probe_rows
        if row["sensor_id"] != "system.weather_forecast"
    )


def test_acknowledgement_sticks_across_reopen_and_a_person_resolve_starts_a_new_row():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 8))
    cycle(ns, log, local(0, 8), silent=WEST)
    [row] = log.of(*PROBE)
    log.now = local(0, 9)
    log.acknowledge(row["id"])
    acknowledged_at = row["acknowledged_at"]

    cycle(ns, log, local(0, 10))  # reports again: acknowledged + suppressible closes as suppressed
    assert row["disposition"] == "suppressed" and row["resolved_by"] == "system"
    cycle(ns, log, local(0, 11), silent=WEST)  # the next failure re-opens it, still acknowledged
    assert log.of(*PROBE) == [row] and row["resolved_at"] is None
    assert row["disposition"] == "acknowledged" and row["acknowledged_at"] == acknowledged_at
    assert row["details"]["count"] == 2 and log.one_count() == 0

    log.now = local(0, 12)
    log.resolve(row["id"], by="jason")
    cycle(ns, log, local(0, 13), silent=WEST)  # a person resolved it: a new episode, a new row
    first, second = log.of(*PROBE)
    assert first is row and first["resolved_by"] == "jason"
    assert second["disposition"] == "open" and second["details"]["count"] == 1 and seen(second) == local(0, 13)


def test_stale_sensor_flaps_keep_one_row_but_the_forecast_feed_keeps_its_lifecycle():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 8))
    for day in range(5):
        cycle(ns, log, local(day, 8), extra=[stale_sensor("climate.hydro_ph", "hydroponic"), forecast_stale()])
        cycle(ns, log, local(day, 9))
    [hydro] = log.of("sensor_offline", "climate.hydro_ph")
    assert hydro["details"]["count"] == 5 and seen(hydro) == local(4, 8)
    assert hydro["resolution"] == "auto-resolved: probe reporting again"
    forecast = log.of("sensor_offline", "system.weather_forecast")
    assert len(forecast) == 5 and {row["resolution"] for row in forecast} == {"auto-resolved"}


def test_lookup_reads_only_when_a_raised_probe_has_no_open_row():
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 8))
    cycle(ns, log, local(0, 8))  # nothing raised: the open read and the return read only
    assert len(log.reads) == 2
    cycle(ns, log, local(0, 9), silent=WEST)  # raised, no open row: one newest-row lookup
    assert sum("CROSS JOIN LATERAL" in sql for sql, _ in log.reads) == 1
    cycle(ns, log, local(0, 10), silent=WEST)  # open row: refreshed without a lookup
    assert sum("CROSS JOIN LATERAL" in sql for sql, _ in log.reads) == 1


def test_reopen_writes_stay_inside_the_ingestor_grants():
    grants = ingestor_alert_log_grants()
    assert {"details", "disposition", "message", "resolution", "resolved_at", "resolved_by"} <= grants["UPDATE"]
    assert not {"zone", "ts", "acknowledged_at", "updated_at"} & grants["UPDATE"]
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 8))
    cycle(ns, log, local(0, 8), silent=WEST)
    cycle(ns, log, local(0, 9))
    cycle(ns, log, local(0, 10), silent=WEST)
    reopen = [sql for sql, _ in log.writes if "resolved_at = NULL" in sql]
    assert len(reopen) == 1  # AlertLog asserted every SET column against migration 217


@pytest.mark.parametrize("lifecycle", ["keep_open_keys", "unpotted_keys", "refresh_only_keys"])
def test_a_scoped_component_call_refuses_lifecycle_keys_before_any_read(lifecycle):
    ns = load_alert_lifecycle()
    log = AlertLog(local(0, 8))
    with pytest.raises(ValueError, match="lifecycle_keys_require_unscoped_monitor"):
        asyncio.run(
            ns["_sync_monitor_alerts"](
                log,
                [],
                component_status_available=True,
                component_closure_resolutions={},
                scope=("component_experiment_integrity", "experiment.v2.test"),
                scope_greenhouse_id="synthetic-test",
                **{lifecycle: {PROBE}},
            )
        )
    assert log.reads == [] and log.writes == []


@pytest.mark.parametrize(
    ("raw", "days"), [(None, 7), ("", 7), ("3", 3), ("14", 14), ("0", 7), ("-2", 7), ("seven", 7), ("2.5", 7)]
)
def test_resolve_days_come_from_the_environment_with_a_safe_default(raw, days):
    assert load_alert_lifecycle()["_probe_offline_resolve_days"](raw) == days
