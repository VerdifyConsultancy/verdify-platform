"""The sensor-health sweep against an offline fixture: no false pass, no false failure.

The fixture answers the sweep's psql calls from a small world description. It never contacts a service. The healthy world
is the one the engine firmware lives in: three zone probes, the south triplet absent since 2026-08.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "scripts/sensor-health-sweep.sh"
LIVE = "2026.11.3.0435.5e24d07c"

FIXTURE_DB = r"""
import json, os, re, sys
query = sys.argv[-1]
world = json.loads(os.environ["WORLD"])
MARKERS = {
    "provenance": "climate_source_events",
    "modbus": "FROM esp32_logs",
    "probes": "active_probe_count",
    "continuity": "lag(uptime_s)",
    "diagnostics": "FROM diagnostics",
    "alerts": "FROM alert_log",
    "edges": "FROM equipment_state",
    "overrides": "FROM override_events",
    "baseline": "count(*), count(",
    "flow": "max(flow_gpm)",
}
name = next(key for key, marker in MARKERS.items() if marker in query)
if name in world["fail"]:
    print("psql: error: connection to server failed", file=sys.stderr)
    sys.exit(2)
if name == "provenance":
    for column in re.findall(r"'([a-z0-9_]+)'", query.split("ARRAY[")[1].split("]")[0]):
        age = world["ages"].get(column, 10)
        print(f"{column}|{'' if age is None else 1}|{'' if age is None else age}")
else:
    for row in world[name]:
        print(row)
"""


def run(tmp_path, *args, ages=None, fail=(), **rows):
    script = tmp_path / "sensor-health-sweep.sh"
    shutil.copy(SWEEP, script)
    (tmp_path / "lib").mkdir(exist_ok=True)
    db = tmp_path / "fixture_db.py"
    db.write_text("#!" + sys.executable + "\n" + FIXTURE_DB)
    db.chmod(0o700)
    (tmp_path / "lib/psql-verdify.sh").write_text('verdify_psql_cmd() { printf "%s\\n" "$FIXTURE_DB"; }\n')
    world = {
        "ages": {"temp_south": None, "rh_south": None, "vpd_south": None, **(ages or {})},
        "fail": list(fail),
        "modbus": [],
        "probes": ["3|9000"],
        "continuity": [],
        "diagnostics": [f"60|Software reset|{LIVE}|-58|9000"],
        "alerts": [],
        "edges": [],
        "overrides": [],
        "baseline": ["1000|0"],
        "flow": [],
        **rows,
    }
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "FIXTURE_DB": str(db), "WORLD": json.dumps(world), "EXPECTED_FW_VERSION": "", "SINCE": ""},
    )


def test_the_healthy_world_passes(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "temp_avg = 1" in result.stdout and "flow_gpm = 1" in result.stdout
    assert "temp_south — absent in last 5 min and throughout populated pre-OTA 24-hour baseline" in result.stdout
    assert "Uptime continuous in window" in result.stdout and "No wet relay opened in window" in result.stdout


@pytest.mark.parametrize("reason", ["Guru/Panic", "Task WDT", "Interrupt WDT", "Other WDT"])
def test_a_recent_crash_fails_and_a_sticky_one_only_warns(tmp_path, reason):
    recent = run(tmp_path, diagnostics=[f"60|{reason}|{LIVE}|-58|300"])
    assert recent.returncode == 1 and f"reset_reason = {reason} with uptime 300s" in recent.stdout
    sticky = run(tmp_path, diagnostics=[f"60|{reason}|{LIVE}|-58|21600"])
    assert sticky.returncode == 0 and "sticky reset cause, not a recent reboot" in sticky.stdout


def test_a_power_event_warns_and_an_unknown_reason_does_not_pass_silently(tmp_path):
    power = run(tmp_path, diagnostics=[f"60|Brown-out|{LIVE}|-58|300"])
    assert power.returncode == 0 and "⚠ reset_reason = Brown-out" in power.stdout
    unknown = run(tmp_path, diagnostics=[f"60|13|{LIVE}|-58|9000"])
    assert unknown.returncode == 0 and "⚠ reset_reason = 13 (not a known reason)" in unknown.stdout


def test_diagnostics_read_the_latest_non_null_of_each_column():
    source = SWEEP.read_text()
    # A NULL reset_reason in the newest row once concatenated to a NULL row: "No diagnostics row".
    assert "reset_reason || '|'" not in source
    assert "AND $1 IS NOT NULL ORDER BY ts DESC LIMIT 1" in source
    for column in ("reset_reason", "firmware_version", "wifi_rssi", "uptime_s"):
        assert f"$(latest_diag {column})" in source


def test_rows_with_no_value_are_not_a_missing_row_and_no_rows_are(tmp_path):
    partial = run(tmp_path, diagnostics=["60|||-58|"])
    assert partial.returncode == 1
    assert "No diagnostics row" not in partial.stdout
    for line in ("reset_reason — no non-null", "firmware_version — no non-null", "uptime_s — no non-null"):
        assert line in partial.stdout
    empty = run(tmp_path, diagnostics=["0||||"])
    assert empty.returncode == 1 and "No diagnostics row in last 5 min" in empty.stdout


@pytest.mark.parametrize(
    "query,label",
    [
        ("provenance", "Climate provenance"),
        ("modbus", "Modbus timeout query unavailable"),
        ("probes", "Active probe count"),
        ("diagnostics", "ESP32 diagnostics"),
        ("continuity", "Uptime continuity"),
        ("alerts", "Deploy-window alerts"),
        ("edges", "Wet relay edges"),
        ("overrides", "Override events"),
    ],
)
def test_a_failed_query_fails_with_its_error_and_claims_nothing_healthy(tmp_path, query, label):
    result = run(tmp_path, fail=[query])
    assert result.returncode == 1, result.stdout
    assert f"{label}" in result.stdout and "query failed: psql: error: connection to server failed" in result.stdout
    healthy = {
        "alerts": "No new sensor_offline",
        "continuity": "Uptime continuous",
        "edges": "No wet relay opened",
        "overrides": "0 override events",
        "probes": "zone probes active",
        "modbus": "No Modbus timeouts",
    }
    if query in healthy:
        assert healthy[query] not in result.stdout


def test_every_query_goes_through_the_error_surfacing_wrapper():
    source = SWEEP.read_text()
    assert "2>/dev/null" not in source
    assert source.count('$("${DB[@]}"') == 1


@pytest.mark.parametrize(
    "age,message",
    [(400, "temp_avg — last received 400s ago"), (None, "temp_avg — no value received")],
)
def test_a_sensor_the_row_only_retains_fails(tmp_path, age, message):
    result = run(tmp_path, ages={"temp_avg": age})
    assert result.returncode == 1 and message in result.stdout
    # The climate row is not asked: it re-uses the last value for 600 s.
    assert "FROM climate WHERE ts > now() - interval '5 minutes'" not in SWEEP.read_text()


def test_a_newly_lost_south_reading_still_fails(tmp_path):
    result = run(tmp_path, baseline=["1000|900"])
    assert result.returncode == 1
    assert "temp_south — no value received (previously present or baseline unavailable)" in result.stdout


def test_uptime_under_six_minutes_says_the_sweep_is_not_decisive(tmp_path):
    result = run(tmp_path, diagnostics=[f"60|Software reset|{LIVE}|-58|302"])
    assert result.returncode == 0 and "uptime under 360 s" in result.stdout


FLASH_BOOT = "2026-11-03 12:36:20+00|397028|2|69|t"
SAME_IMAGE_NO_SILENCE = "2026-11-03 13:10:00+00|2000|3|61|f"
LINK_EVENT = "2026-10-29 22:23:34+00|2362830|362|688|f"


def test_the_flash_itself_is_not_an_unexplained_reset(tmp_path):
    result = run(tmp_path, continuity=[FLASH_BOOT])
    assert result.returncode == 0 and "Uptime continuous in window (1 flash boot(s))" in result.stdout


def test_a_same_image_reset_fails_even_as_a_software_reset(tmp_path):
    result = run(tmp_path, continuity=[FLASH_BOOT, SAME_IMAGE_NO_SILENCE])
    assert result.returncode == 1
    assert "1 reset(s) of the same image in window" in result.stdout and "uptime 2000s to 3s" in result.stdout


def test_one_reset_after_silence_is_a_link_event_and_a_second_one_fails(tmp_path):
    result = run(tmp_path, continuity=[LINK_EVENT])
    assert result.returncode == 0 and "fits reboot_timeout" in result.stdout
    again = run(tmp_path, continuity=[LINK_EVENT, LINK_EVENT])
    assert again.returncode == 1 and "2 reset(s) of the same image" in again.stdout


def test_continuity_skips_backfill_and_partial_rows():
    source = SWEEP.read_text()
    # Gap-backfill rows land exactly on the minute with old uptimes (2026-09: 36304 s between 65165 s and 66305 s).
    assert "ts <> date_trunc('minute', ts)" in source
    assert "uptime_s IS NOT NULL AND firmware_version IS NOT NULL" in source
    # A fast crash loop never falls 120 s behind: a plain drop counts too.
    assert "uptime_s < prev_up OR uptime_s + 120 < prev_up + extract(epoch FROM ts - prev_ts)" in source


MISMATCH = "firmware_version_mismatch :: diag.firmware_version"


def test_the_mismatch_is_pin_lag_when_live_equals_the_expected_version(tmp_path):
    result = run(tmp_path, "--expected-fw", LIVE, alerts=[MISMATCH])
    assert result.returncode == 0, result.stdout
    assert f"⚠ Pin lags: {MISMATCH}, while live firmware_version = {LIVE} (expected)" in result.stdout


def test_the_mismatch_fails_when_nothing_proves_the_live_version(tmp_path):
    unpinned = run(tmp_path, alerts=[MISMATCH])
    assert unpinned.returncode == 1 and f"NEW alert: {MISMATCH}" in unpinned.stdout
    wrong = run(tmp_path, "--expected-fw", "2026.10.2.0637.620a218a-wifi-bound", alerts=[MISMATCH])
    assert wrong.returncode == 1 and f"NEW alert: {MISMATCH}" in wrong.stdout
    unread = run(tmp_path, "--expected-fw", LIVE, alerts=[MISMATCH], fail=["diagnostics"])
    assert unread.returncode == 1 and f"NEW alert: {MISMATCH}" in unread.stdout


def test_the_skip_covers_only_the_mismatch(tmp_path):
    result = run(tmp_path, "--expected-fw", LIVE, alerts=[MISMATCH, "esp32_reboot :: diag.uptime_s"])
    assert result.returncode == 1 and "NEW alert: esp32_reboot :: diag.uptime_s" in result.stdout
    assert f"NEW alert: {MISMATCH}" not in result.stdout


def test_a_reopened_sensor_counts_by_last_seen_not_by_its_old_ts():
    source = SWEEP.read_text()
    assert (
        "(alert_type = 'sensor_offline' AND COALESCE(sensor_id, '') NOT LIKE 'climate.soil_%' "
        "AND (details ->> 'last_seen_at')::timestamptz >= now() - interval '$SINCE')"
    ) in source


def test_a_lost_water_column_fails(tmp_path):
    result = run(tmp_path, ages={"water_total_gal": 601})
    assert result.returncode == 1 and "water_total_gal — last received 601s ago" in result.stdout


def test_a_wet_relay_edge_warns_and_fails_under_no_water(tmp_path):
    edge = "drip_wall|1|2026-11-03 13:00:00+00"
    assert run(tmp_path, edges=[edge]).returncode == 0
    result = run(tmp_path, "--no-water", edges=[edge])
    assert result.returncode == 1 and "drip_wall opened 1 time(s) in window" in result.stdout


@pytest.mark.parametrize("relay", ["fog", "mister_center"])
def test_climate_demand_relays_only_warn_even_under_no_water(tmp_path, relay):
    result = run(tmp_path, "--no-water", edges=[f"{relay}|2|2026-11-03 13:00:00+00"])
    assert result.returncode == 0 and "allowed under climate demand" in result.stdout


def test_meter_flow_with_every_relay_closed_warns(tmp_path):
    result = run(tmp_path, flow=["2.10"])
    assert result.returncode == 0
    assert "Meter read up to 2.10 gpm with no wet relay edge in window" in result.stdout
    explained = run(tmp_path, flow=["2.10"], edges=["fog|1|2026-11-03 13:00:00+00"])
    assert "no wet relay edge" not in explained.stdout


def test_overrides_keep_their_thresholds(tmp_path):
    assert "all are vent_mist_assist/summer_vent" in run(tmp_path, overrides=["summer_vent|3"]).stdout
    many = run(tmp_path, overrides=["vent_mist_assist|5", "mister_lockout|25"])
    assert many.returncode == 0 and "30 override events in window (5 vent/summer assist, 25 other)" in many.stdout
