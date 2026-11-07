"""Time-bounded alert silences (migration 289): matching, expiry and the monitor's close path.

Runs the owning silence helpers, the soil scan and _sync_monitor_alerts from
ingestor/tasks/alerts.py (see alert_lifecycle_harness) against an in-memory alert_log
held to the ingestor login's grants. No database, network or device:
tests/test_dashboard_db_alert_silences.py runs the read and the close path on PostgreSQL.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
from alert_lifecycle_harness import DENVER, ROOT, AlertLog, load_alert_lifecycle, soil_counts

UNTIL = datetime(2026, 11, 12, 3, tzinfo=UTC)  # 289's seeds
DRYOUT = ("soil_dryout", "soil.south_1.soil_moisture_south_1")
LEAK = ("leak_detected", "equipment.leak_detected")
WEST = ("soil_moisture_west", "soil_temp_west")
SOUTH_2 = ("soil_moisture_south_2", "soil_temp_south_2")


def silence(id_=1, alert_type="soil_dryout", pattern=None, until=UNTIL, reason="probes out of the soil"):
    return {"id": id_, "alert_type": alert_type, "sensor_pattern": pattern, "until": until, "reason": reason}


SEEDS = [silence(1, "soil_dryout"), silence(2, "soil_sensor_offline")]


def denver(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=DENVER).astimezone(UTC)


def dryout_alert():
    """The envelope alert_monitor section 9a builds for the south_1 probe below wilt."""
    return {
        "alert_type": "soil_dryout",
        "severity": "critical",
        "category": "sensor",
        "sensor_id": DRYOUT[1],
        "zone": "south",
        "message": "SOIL DRYOUT: `soil_moisture_south_1` (south) below wilt (25%) for 2.6h",
        "details": {
            "column": "soil_moisture_south_1",
            "sensor": "soil.south_1",
            "zone": "south",
            "wilt_pct": 25.0,
            "latest_pct": 3.1,
            "min_pct": 2.9,
            "max_pct": 3.4,
            "duration_h": 2.6,
            "samples": 150,
            "occupancy": "occupied",
        },
        "metric_value": 3.1,
        "threshold_value": 25.0,
    }


def sync(ns, log, when, alerts, *, silences=SEEDS, silent=(), keep_scan=False):
    """One unscoped monitor sync at ``when``; ``silent`` soil columns run the soil scan first."""
    log.now = when
    keep_open, unpotted = set(), set()
    if silent or keep_scan:
        scanned, keep_open, unpotted = ns["_soil_probe_offline_scan"](
            soil_counts(ns, silent), {"south", "west"}, when.astimezone(DENVER).hour
        )
        alerts = [*scanned, *alerts]
    return asyncio.run(
        ns["_sync_monitor_alerts"](
            log,
            alerts,
            component_status_available=True,
            component_closure_resolutions={},
            keep_open_keys=keep_open,
            unpotted_keys=unpotted,
            silences=silences,
            now=when,
        )
    )


def seed_probe(log, sensor_id, columns, *, acknowledged=True, **values):
    zone = "west" if sensor_id == "soil.west" else "south"
    details = {"sensor": sensor_id, "columns": list(columns), "occupancy": "occupied", "count": 1}
    details["last_seen_at"] = log.now.isoformat()
    row = {
        "alert_type": "soil_sensor_offline",
        "sensor_id": sensor_id,
        "zone": zone,
        "message": f"Soil probe `{sensor_id}` ({zone} zone) has no data for 30 min",
        "details": details,
        "disposition": "acknowledged" if acknowledged else "open",
        "acknowledged_at": log.now if acknowledged else None,
        "acknowledged_by": "jason" if acknowledged else None,
    }
    return log.seed(**{**row, **values})


# ── Matching and expiry ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern, value, expected",
    [
        ("soil.%", "soil.west", True),
        ("soil.%", "soil", False),
        ("%", "", True),
        ("", "", True),
        ("", "x", False),
        ("soil._est", "soil.west", True),
        ("soil._est", "soil.wwest", False),
        ("soil.south\\_1", "soil.south_1", True),
        ("soil.south\\_1", "soil.southX1", False),
        ("100\\%", "100%", True),
        ("100\\%", "1000", False),
        ("Soil.%", "soil.west", False),
        ("a%b%c", "aXXbYYc", True),
        ("a%b%c", "aXXcYYb", False),
        ("%south_1%", "soil.south_1.soil_moisture_south_1", True),
        ("soil.west", "soil.west.soil_temp_west", False),
    ],
)
def test_like_matches_as_postgresql_does(pattern, value, expected):
    assert load_alert_lifecycle()["_like"](pattern, value) is expected


def test_a_silence_holds_its_type_and_pattern_until_it_expires():
    ns = load_alert_lifecycle()
    held = ns["_alert_silence"]
    now = UNTIL - timedelta(days=6)
    assert held(SEEDS, DRYOUT, now) is SEEDS[0]
    assert held(SEEDS, ("soil_sensor_offline", "soil.west"), now) is SEEDS[1]
    assert held(SEEDS, ("sensor_offline", "climate.temp_avg"), now) is None  # another type
    west, south_1 = silence(pattern="soil.west%"), silence(pattern="soil.south\\_1.%")
    assert held([west], DRYOUT, now) is None and held([west, south_1], DRYOUT, now) is south_1
    # NULL means every sensor, a key without one included; a pattern never matches a missing sensor.
    assert held([silence(alert_type="x")], ("x", None), now) is not None
    assert held([silence(alert_type="x", pattern="%")], ("x", None), now) is None
    # Held strictly before `until`; at it the rules apply again.
    assert held(SEEDS, DRYOUT, UNTIL - timedelta(microseconds=1)) is SEEDS[0]
    assert held(SEEDS, DRYOUT, UNTIL) is None and held(SEEDS, DRYOUT, UNTIL + timedelta(days=1)) is None
    # The first that holds names the close (the read orders the longest first).
    longer = silence(7, until=UNTIL + timedelta(days=3))
    assert held([longer, SEEDS[0]], DRYOUT, now) is longer
    assert ns["_silence_resolution"](SEEDS[0]) == (
        "suppressed: alert_silence 1 until 2026-11-12 03:00 UTC (probes out of the soil)"
    )


def test_leak_detected_is_never_silenced():
    ns = load_alert_lifecycle()
    now = UNTIL - timedelta(days=1)
    leak_silence = silence(9, "leak_detected")
    assert ns["_alert_silence"]([leak_silence], LEAK, now) is None
    log = AlertLog(now)
    open_row = log.seed(alert_type=LEAK[0], sensor_id=LEAK[1], severity="critical", category="water", ts=now)
    leak = {
        "alert_type": LEAK[0],
        "severity": "critical",
        "category": "water",
        "sensor_id": LEAK[1],
        "zone": None,
        "message": "Water flowing with every Verdify valve closed since 03:02",
        "details": {"since": now.isoformat(), "stopped_at": None},
        "metric_value": 1.2,
        "threshold_value": None,
    }
    result = sync(ns, log, now, [leak], silences=[leak_silence])
    assert open_row["resolved_at"] is None and open_row["disposition"] == "open"
    assert result == {"new": 0, "escalated": 0, "resolved": 0, "suppressed": 0}
    open_row.update(disposition="resolved", resolved_at=now, resolved_by="jason")  # a person resolves it
    result = sync(ns, log, now, [leak], silences=[leak_silence])
    assert result["new"] == 1 and len(log.of(*LEAK)) == 2  # a new episode is still raised
    assert "CHECK (alert_type <> 'leak_detected')" in (ROOT / "db/migrations/289-alert-silences.sql").read_text()


# ── The monitor's close path ────────────────────────────────────────────────


def test_a_silenced_alert_is_not_raised_and_its_open_row_closes_as_suppressed():
    ns = load_alert_lifecycle()
    morning = denver(11, 6, 7)
    log = AlertLog(morning)
    row = log.seed(alert_type=DRYOUT[0], sensor_id=DRYOUT[1], zone="south", severity="critical")
    result = sync(ns, log, morning, [dryout_alert()])
    assert result == {"new": 0, "escalated": 0, "resolved": 0, "suppressed": 1}
    assert log.of(*DRYOUT) == [row]  # nothing raised
    assert row["disposition"] == "suppressed" and row["resolved_by"] == "system" and row["resolved_at"] == morning
    assert row["resolution"] == "suppressed: alert_silence 1 until 2026-11-12 03:00 UTC (probes out of the soil)"
    assert log.open_view() == []
    writes = len(log.writes)
    for hour in (8, 13, 21):  # the rule keeps firing all day: nothing is written
        assert sync(ns, log, denver(11, 6, hour), [dryout_alert()]) == {
            "new": 0,
            "escalated": 0,
            "resolved": 0,
            "suppressed": 0,
        }
    assert len(log.writes) == writes


def test_the_offline_soil_probes_close_by_night_and_stay_quiet_by_day():
    ns = load_alert_lifecycle()
    night = denver(11, 4, 23)
    log = AlertLog(night)
    west = seed_probe(log, "soil.west", WEST)
    south_2 = seed_probe(log, "soil.south_2", SOUTH_2)
    result = sync(ns, log, night, [], silent=(*WEST, *SOUTH_2))  # kept open at night without the silence
    assert result["suppressed"] == 2 and result["new"] == 0
    for row in (west, south_2):
        assert row["disposition"] == "suppressed" and row["resolved_at"] == night
        assert row["resolution"].startswith("suppressed: alert_silence 2 until 2026-11-12 03:00 UTC")
    day = denver(11, 5, 9)
    assert sync(ns, log, day, [], silent=(*WEST, *SOUTH_2))["new"] == 0
    assert log.open_view() == [] and len(log.rows) == 2


def test_the_rules_apply_again_once_the_silence_ends():
    ns = load_alert_lifecycle()
    before = denver(11, 11, 8)
    log = AlertLog(before)
    west = seed_probe(log, "soil.west", WEST)
    sync(ns, log, before, [dryout_alert()], silent=WEST)
    assert west["disposition"] == "suppressed" and log.of(*DRYOUT) == []
    after = UNTIL + timedelta(hours=12)  # 2026-11-12 08:00 Denver: daytime, the silence is over
    result = sync(ns, log, after, [dryout_alert()], silent=WEST)
    [dryout] = log.of(*DRYOUT)
    assert result["new"] == 1 and dryout["disposition"] == "open" and dryout["resolved_at"] is None
    # The probe row the system closed re-opens in place, as any system close does; the ack sticks.
    assert log.of("soil_sensor_offline", "soil.west") == [west]
    assert west["resolved_at"] is None and west["disposition"] == "acknowledged" and west["details"]["count"] == 2


def test_a_long_offline_mark_stays_while_the_probe_is_silenced():
    ns = load_alert_lifecycle()
    day = denver(11, 6, 10)
    log = AlertLog(day)
    marked = seed_probe(
        log,
        "soil.west",
        WEST,
        resolved_at=day - timedelta(days=1),
        resolved_by="system",
        disposition="resolved",
        resolution="auto-resolved: probe offline longer than 7 days",
    )
    marked["details"]["long_offline"] = True
    sync(ns, log, day, [], silent=WEST)  # still silent, but held back: not "reporting again"
    assert marked["details"]["long_offline"] is True
    after = UNTIL + timedelta(hours=12)
    sync(ns, log, after, [], silent=WEST)  # after the silence, still offline: it writes nothing
    assert log.of("soil_sensor_offline", "soil.west") == [marked] and marked["resolved_at"] is not None
    sync(ns, log, after + timedelta(minutes=5), [])  # the probe returns: the mark clears as before
    assert marked["details"]["long_offline"] is False


def test_a_scoped_component_cycle_refuses_silences():
    ns = load_alert_lifecycle()
    with pytest.raises(ValueError, match="lifecycle_keys_require_unscoped_monitor"):
        asyncio.run(
            ns["_sync_monitor_alerts"](
                AlertLog(UNTIL),
                [],
                component_status_available=True,
                component_closure_resolutions={},
                scope=("component_experiment_integrity", "experiment.v2.x"),
                scope_greenhouse_id="synthetic-test",
                silences=SEEDS,
            )
        )


# ── The read and its wiring ─────────────────────────────────────────────────


class Rows:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.sql = rows or [], error, []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        if self.error is not None:
            raise self.error
        return self.rows


def test_the_read_takes_only_unexpired_silences_and_fails_open(caplog):
    ns = load_alert_lifecycle()
    conn = Rows(SEEDS)
    assert asyncio.run(ns["_active_alert_silences"](conn)) == SEEDS
    [sql] = conn.sql
    assert "FROM alert_silence" in sql and "until > now()" in sql and "ORDER BY until DESC, id" in sql
    assert "alert_type <> 'leak_detected'" in sql
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(ns["_active_alert_silences"](Rows(error=RuntimeError("relation does not exist")))) == []
    assert "alert silences unavailable, none applied: RuntimeError" in caplog.text


def test_alert_monitor_reads_the_silences_each_cycle_and_passes_them_to_the_sync():
    tree = ast.parse((ROOT / "ingestor/tasks/alerts.py").read_text())
    monitor = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "alert_monitor")
    calls = [n for n in ast.walk(monitor) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    [read] = [n for n in calls if n.func.id == "_active_alert_silences"]
    assert [ast.unparse(arg) for arg in read.args] == ["conn"]
    [sync_call] = [n for n in calls if n.func.id == "_sync_monitor_alerts"]
    assert {k.arg: ast.unparse(k.value) for k in sync_call.keywords}["silences"] == "silences"
