"""verdify_services.settings (VD-67, #1180): validation, snapping, the read-only list, readback states and the catalog.

Everything here runs without a database: the services run against FakeDB, an in-memory stand-in that answers
each statement the module sends (matched against the module's own SQL constants) the way PostgreSQL would, and
keeps committed rows apart from a transaction's, so a refusal really leaves nothing behind.
tests/test_dashboard_db_settings_alerts.py proves the same paths on PostgreSQL. The models are checked against
the committed OpenAPI file.
"""

import asyncio
import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
import yaml

from verdify_schemas import irrigation
from verdify_schemas.band_rows import BAND_PARAM_ROWS
from verdify_schemas.policy_vector import wire_fields
from verdify_schemas.setting_help import setting_help, setting_label
from verdify_schemas.tunable_registry import (
    LEGACY_WIDE_LIMIT_PARAMS,
    READ_ONLY_REASONS,
    REGISTRY,
    SWITCH_READBACK_EQUIPMENT,
    ZONE_VPD_TARGET_PARAMS,
    desired_write_rule,
)
from verdify_services import activity, reads, settings
from verdify_services.actor import Actor
from verdify_services.irrigation import program
from verdify_services.problems import ServiceError

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
PERSON = Actor.person("jason", "operator")
IRIS = Actor.iris()
AGENT = Actor.agent("steward")
KPA = "mister_engage_kpa"  # planner_policy, a policy field, step 0.05, bounds 0.5-2.5 kPa, cfg readback
DELAY = "mister_engage_delay_s"  # planner_policy, step 30 s
PRESSURE = "site_pressure_hpa"  # controller_safety, not a policy field, step 1 hPa
ECON = "sw_economiser_enabled"  # a switch read back through its equipment_state mirror


def run(coroutine):
    return asyncio.run(coroutine)


def ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


def openapi_errors(name: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [error.message for error in validator.iter_errors(instance)]


def refusal(excinfo):
    return excinfo.value.problem


# ── FakeDB: the statements verdify_services.settings sends, answered in memory ─────────────────────────────


class FakeTransaction:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.depth += 1
        self.saved = copy.deepcopy(self.db.state())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.depth -= 1
        if exc_type is not None:
            self.db.restore(self.saved)
        return False


class FakeDB:
    """In-memory greenhouse: desired_state, plans, readbacks, pushes, clamps, crop_band_anchors and activity_log."""

    def __init__(self):
        self.at = NOW
        self.heartbeat_at: datetime | None = ago(seconds=30)
        self.climate_at: datetime | None = ago(seconds=60)
        self.armed: uuid.UUID | None = None
        self.season = "fall"
        self.desired: list[dict] = []
        self.plans: list[dict] = []
        self.snapshots: list[tuple[str, datetime, float]] = []
        self.equipment: list[tuple[str, datetime, bool]] = []
        self.changes: list[dict] = []
        self.clamps: list[dict] = []
        self.anchors: list[dict] = []
        self.activity: list[dict] = []
        self.system_state: dict[str, tuple[str, datetime]] = {}  # the newest irr_engine and irr_program_id rows
        self.pushes: list[dict] = []
        self.sql: list[str] = []
        self.next_id = 1000
        self.depth = 0

    # transactions
    def state(self):
        return {key: getattr(self, key) for key in ("desired", "anchors", "activity", "next_id")}

    def restore(self, saved):
        for key, value in saved.items():
            setattr(self, key, value)

    def is_in_transaction(self):
        return self.depth > 0

    def transaction(self, **_kwargs):
        return FakeTransaction(self)

    # fixtures
    def add_desired(self, param, value, *, manual=False, actor=PERSON, at=None, reason=None):
        self.next_id += 1
        row = {
            "id": self.next_id,
            "param": param,
            "value": value,
            "manual": manual,
            "source": actor.source,
            "actor": actor.name,
            "reason": reason,
            "created_at": at or self.at,
            "greenhouse_id": "vallery",
        }
        self.desired.append(row)
        return row

    def add_plan(self, param, value, *, created_at, ts=None, plan_id="iris-oneshot-test"):
        self.plans.append(
            {"parameter": param, "value": value, "ts": ts or created_at, "plan_id": plan_id, "created_at": created_at}
        )

    def add_anchor(self, crop_type, series, anchor, season, value, *, width_below=None, width_above=None):
        self.next_id += 1
        row = {
            "id": self.next_id,
            "crop_type": crop_type,
            "series": series,
            "anchor": anchor,
            "season": season,
            "value": value,
            "width_below": width_below,
            "width_above": width_above,
        }
        self.anchors.append(row)
        return row

    # helpers
    def newest_desired(self):
        newest = {}
        for row in sorted(self.desired, key=lambda r: (r["created_at"], r["id"])):
            newest[row["param"]] = row
        return newest

    def newest(self, rows, key=lambda row: row["ts"]):
        return max(rows, key=key) if rows else None

    # asyncpg surface
    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return self.answer(sql, args)

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        rows = self.answer(sql, args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if sql == activity.ARMED_EXPERIMENT_SQL:
            return self.armed
        if sql == settings._SEASON_SQL:
            return self.season
        if sql.startswith("INSERT INTO public.activity_log"):
            assert self.depth > 0
            gh, source, actor, action, target, detail, reason, request_id, digest, experiment_id = args
            self.next_id += 1
            self.activity.append(
                {
                    "id": self.next_id,
                    "greenhouse_id": gh,
                    "ts": self.at,
                    "source": source,
                    "actor": actor,
                    "action": action,
                    "target": target,
                    "detail": json.loads(detail),
                    "reason": reason,
                    "request_id": request_id,
                    "request_sha256": digest,
                    "experiment_id": experiment_id,
                }
            )
            return self.next_id
        if sql == settings._HISTORY_NEXT_SQL:
            _gh, param, end = args
            later = sorted(
                (r for r in self.desired if r["param"] == param and r["created_at"] >= end),
                key=lambda r: (r["created_at"], r["id"]),
            )
            return later[0]["created_at"] if later else None
        raise AssertionError(f"unexpected fetchval: {sql}")

    def answer(self, sql, args):  # noqa: C901 - one branch per statement
        if sql == reads._REPORTING_SQL:
            return [{"at": self.at, "heartbeat_at": self.heartbeat_at, "climate_at": self.climate_at}]
        if sql == program._STATES_SQL:
            entities, _gh, _window = args
            return [
                {"entity": name, "value": self.system_state[name][0], "ts": self.system_state[name][1]}
                if name in self.system_state
                else {"entity": name, "value": None, "ts": None}
                for name in entities
            ]
        if sql == program._PUSHES_OF_HASH_SQL:
            _gh, hash16 = args
            found = [row for row in self.pushes if row["sha256"].startswith(hash16)]
            return sorted(found, key=lambda row: row["revision"], reverse=True)
        if sql == program._FIRST_PUSH_SQL:
            _gh, params, sinces = args
            out = []
            for param, since in zip(params, sinces, strict=True):
                later = [
                    row
                    for row in sorted(self.pushes, key=lambda row: row["revision"])
                    if row["site_desired_at"] is not None
                    and row["site_desired_at"] >= since
                    and row["pushed_at"] is not None
                ]
                if later:
                    out.append({"param": param, "pushed_at": later[0]["pushed_at"]})
            return out
        if sql == settings._DESIRED_SQL:
            _gh, names = args
            newest = self.newest_desired()
            return [newest[name] for name in names if name in newest]
        if sql == settings._ACTIVE_PLAN_SQL:
            (names,) = args
            return [row for row in self.plans if row["parameter"] in names]
        if sql == settings._SNAPSHOT_SQL:
            _gh, names, window = args
            out = []
            for name in names:
                rows = [(ts, v) for p, ts, v in self.snapshots if p == name and ts > self.at - window]
                if rows:
                    ts, value = max(rows)
                    out.append({"param": name, "value": value, "ts": ts})
            return out
        if sql == settings._EQUIPMENT_SQL:
            _gh, params, equipments, window = args
            out = []
            for param, equipment in zip(params, equipments, strict=True):
                rows = [(ts, s) for e, ts, s in self.equipment if e == equipment and ts > self.at - window]
                if rows:
                    ts, state = max(rows)
                    out.append({"param": param, "state": state, "ts": ts})
            return out
        if sql == settings._LAST_PUSH_SQL:
            _gh, names, window, observed = args
            out = []
            for name in names:
                rows = [
                    c
                    for c in self.changes
                    if c["parameter"] == name and c["ts"] > self.at - window and c["source"] != observed
                ]
                if rows:
                    row = self.newest(rows)
                    out.append({"param": name, "value": row["value"], "ts": row["ts"]})
            return out
        if sql == settings._REQUEST_FACTS_SQL:
            _gh, params, sinces, observed = args
            out = []
            for param, since in zip(params, sinces, strict=True):
                push = self.newest(
                    [
                        c
                        for c in self.changes
                        if c["parameter"] == param and c["ts"] >= since and c["source"] != observed
                    ]
                )
                clamp = self.newest([k for k in self.clamps if k["parameter"] == param and k["ts"] >= since])
                out.append(
                    {
                        "param": param,
                        "push_at": push and push["ts"],
                        "delivery_status": push and push["delivery_status"],
                        "clamp_at": clamp and clamp["ts"],
                        "applied": clamp and clamp["applied"],
                        "reason": clamp and clamp["reason"],
                    }
                )
            return out
        if sql == settings._FIRST_MATCH_SQL:
            _gh, keys, params, sinces, untils, values, tols, equipments = args
            out = []
            for key, param, since, until, value, tol, equipment in zip(
                keys, params, sinces, untils, values, tols, equipments, strict=True
            ):

                def inside(ts, since=since, until=until):
                    return ts >= since and (until is None or ts < until)

                snap = sorted(ts for p, ts, v in self.snapshots if p == param and inside(ts) and abs(v - value) <= tol)
                equip = sorted(
                    ts
                    for e, ts, s in self.equipment
                    if equipment is not None and e == equipment and inside(ts) and s == (value >= 0.5)
                )
                out.append(
                    {"key": key, "snapshot_at": snap[0] if snap else None, "equipment_at": equip[0] if equip else None}
                )
            return out
        if sql == settings._ANCHOR_ROWS_SQL:
            return [{k: row[k] for k in ("id", "crop_type", "series", "anchor", "season")} for row in self.anchors]
        if sql.startswith("UPDATE crop_band_anchors SET "):
            assert self.depth > 0
            column = sql.removeprefix("UPDATE crop_band_anchors SET ").split(" ", 1)[0]
            assert column in {"value", "width_below", "width_above"}
            value, ids = args
            out = []
            for row in self.anchors:
                if row["id"] in ids:
                    row[column] = value
                    out.append({"id": row["id"]})
            return out
        if sql == settings._INSERT_DESIRED_SQL:
            assert self.depth > 0
            gh, param, value, manual, source, actor, reason = args
            self.next_id += 1
            row = {
                "id": self.next_id,
                "param": param,
                "value": value,
                "manual": manual,
                "source": source,
                "actor": actor,
                "reason": reason,
                "created_at": self.at,
                "greenhouse_id": gh,
            }
            self.desired.append(row)
            return [row]
        if sql == settings._DESIRED_BY_ID_SQL:
            (row_id,) = args
            return [row for row in self.desired if row["id"] == row_id]
        if sql == settings._DESIRED_ROWS_SQL:  # VD-55: named rows, each with its setting's next row's time
            _gh, ids = args
            ordered = sorted(self.desired, key=lambda r: (r["created_at"], r["id"]))
            out = []
            for row in ordered:
                if row["id"] in ids:
                    key = (row["created_at"], row["id"])
                    later = [r for r in ordered if r["param"] == row["param"] and (r["created_at"], r["id"]) > key]
                    out.append({**row, "until": later[0]["created_at"] if later else None})
            return out
        if sql == settings._HISTORY_DESIRED_SQL:
            _gh, param, start, end = args
            rows = sorted((r for r in self.desired if r["param"] == param), key=lambda r: (r["created_at"], r["id"]))
            before = [r for r in rows if r["created_at"] < start]
            inside = [r for r in rows if start <= r["created_at"] < end]
            return ([before[-1]] if before else []) + inside
        if sql == settings._READBACK_BEFORE_SQL:
            _gh, param, start, window = args
            rows = sorted((ts, v) for p, ts, v in self.snapshots if p == param and start - window < ts < start)
            return [{"ts": rows[-1][0], "value": rows[-1][1]}] if rows else []
        if sql == settings._READBACK_POINTS_SQL:
            _gh, param, start, end = args
            rows = sorted((ts, v) for p, ts, v in self.snapshots if p == param and start <= ts < end)
            points, previous = [], object()
            for ts, value in rows:
                if value != previous:
                    points.append({"ts": ts, "value": value})
                previous = value
            return points
        if "FROM public.activity_log WHERE source = $1 AND request_id = $2" in sql:
            source, key = args
            return [
                {**row, "detail": json.dumps(row["detail"])}
                for row in self.activity
                if row["source"] == source and row["request_id"] == key
            ]
        raise AssertionError(f"unexpected statement: {sql}")


def orchid_rows(db, *, season_value=0.80, all_value=0.70):
    """The center zone's crop rows: an 'all' set and a current-season set, each with widths."""
    rows = {}
    for anchor in ("sr", "sm", "ss", "mid"):
        rows[("all", anchor)] = db.add_anchor(
            "orchid", "vpd_target", anchor, "all", all_value, width_below=0.20, width_above=0.35
        )
        rows[("fall", anchor)] = db.add_anchor(
            "orchid", "vpd_target", anchor, "fall", season_value, width_below=0.20, width_above=0.35
        )
    return rows


# ── §5.3 step 1: known and writable ──────────────────────────────────────────


def test_every_registry_row_gets_a_write_decision_with_a_reason_when_refused():
    for name in REGISTRY:
        writable, reason = desired_write_rule(name)
        assert writable is (reason is None), name
    assert desired_write_rule("not_a_setting") == (False, "not a registered setting")


@pytest.mark.parametrize(
    "param",
    sorted(LEGACY_WIDE_LIMIT_PARAMS | ZONE_VPD_TARGET_PARAMS | {"sw_onchip_band_enabled"}),
)
def test_the_read_only_list_is_refused_read_only_with_its_reason(param):
    db = FakeDB()
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(db, PERSON, param, 1))
    problem = refusal(excinfo)
    assert (problem.code, problem.status, problem.retryable, problem.field) == ("read_only", 422, False, "param")
    assert problem.detail == f"{param} is read-only: {desired_write_rule(param)[1]}"


def test_the_band_owner_switch_carries_its_own_reason():
    db = FakeDB()
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(db, PERSON, "sw_onchip_band_enabled", 1))
    problem = refusal(excinfo)
    assert problem.code == "read_only"
    assert "decides which side owns the band" in problem.detail


@pytest.mark.parametrize("param", ["sw_wall_fertigation_commissioned", "wall_commissioning_revision", "wall_flow_lpm"])
def test_the_retired_weekly_feed_rows_read_unknown_target(param):
    # #1162 deleted the weekly-feed commissioning rows from REGISTRY.
    assert param not in REGISTRY
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(FakeDB(), PERSON, param, 1))
    assert refusal(excinfo).code == "unknown_target"


@pytest.mark.parametrize(
    "param",
    ["irrig_wall_duration_min", "irrig_center_duration_min", "sw_irrig_enabled", "sw_irrig_wall_enabled",
     "sw_irrigation_enabled", "sw_irrigation_weather_skip"],
)  # fmt: skip
def test_the_retired_l01_bridge_rows_read_unknown_target(param):
    # #1165's follow-up deleted the L01 bridge's rows once the irrigation engine was last-good.
    assert param not in REGISTRY
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(FakeDB(), PERSON, param, 1))
    assert refusal(excinfo).code == "unknown_target"


@pytest.mark.parametrize("control_class", ["readback_context", "retired"])
def test_readback_context_and_retired_rows_are_read_only(control_class):
    names = [n for n, spec in REGISTRY.items() if spec.control_class == control_class]
    assert names
    for name in names:
        writable, reason = desired_write_rule(name)
        assert writable is False and reason, name


def test_unknown_settings_are_unknown_target_without_touching_the_database():
    db = FakeDB()
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(db, PERSON, "no_such_setting", 1))
    assert (refusal(excinfo).code, refusal(excinfo).field) == ("unknown_target", "param")
    assert db.sql == []


@pytest.mark.parametrize("param", ["gl_main_lux_threshold", "gl_grow_sunrise_hour", "sw_gl_main_auto_mode"])
def test_the_lighting_rows_stay_writable(param):
    assert desired_write_rule(param) == (True, None)


@pytest.mark.parametrize("param", ["gl_lux_threshold", "gl_sunrise_hour", "gl_main_dli_target", "sw_gl_auto_mode"])
def test_the_lighting_rows_no_light_logic_reads_left_the_registry(param):
    """#1477: they left the firmware and the registry, so a write names an unknown setting."""
    assert param not in REGISTRY
    assert desired_write_rule(param) == (False, READ_ONLY_REASONS["unknown"])


def test_the_irrigation_site_rows_are_writable_before_the_engine():
    for name in ("mister_daily_volume_max_gal", "sched_mist_rh_max_pct", "climate_yield_min"):
        assert desired_write_rule(name) == (True, None)


def test_a_crop_band_setting_is_writable_only_while_crop_band_anchors_holds_its_rows():
    db = FakeDB()
    for param in ("zone_priority_center", "manual_override_timeout_min"):
        with pytest.raises(ServiceError) as excinfo:
            run(settings.validate(db, PERSON, param, 2))
        assert refusal(excinfo).code == "read_only" and settings.NO_ANCHOR_ROW in refusal(excinfo).detail
    # A band setting whose rows are all another season's is read-only too: the dispatcher reads none of them.
    db.add_anchor("orchid", "vpd_target", "sm", "summer", 0.9)
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(db, PERSON, "zone_vpd_target_center_sm", 0.9))
    assert refusal(excinfo).code == "read_only"
    db.add_anchor("orchid", "vpd_target", "sm", "all", 0.9)
    assert run(settings.validate(db, PERSON, "zone_vpd_target_center_sm", 0.9)).value == 0.9


# ── §5.3 step 2: type and range ──────────────────────────────────────────────


def test_out_of_range_names_the_bounds_the_current_value_and_is_not_retryable():
    db = FakeDB()
    db.snapshots.append((KPA, ago(minutes=1), 1.6))
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(db, PERSON, KPA, 2.6))
    problem = refusal(excinfo)
    assert problem.body() == {
        "type": "about:blank",
        "title": "Out of range",
        "status": 422,
        "detail": "Must be 0.50-2.50 kPa",
        "code": "out_of_range",
        "retryable": False,
        "field": "value",
        "min": 0.5,
        "max": 2.5,
        "current": 1.6,
    }
    assert openapi_errors("Problem", problem.body()) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "1.5", None])
def test_a_value_that_is_not_a_finite_number_is_out_of_range(value):
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(FakeDB(), PERSON, KPA, value))
    assert (refusal(excinfo).code, refusal(excinfo).field) == ("out_of_range", "value")


def test_a_switch_takes_only_0_or_1():
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(FakeDB(), PERSON, ECON, 0.5))
    assert (refusal(excinfo).code, refusal(excinfo).min, refusal(excinfo).max) == ("out_of_range", 0, 1)
    assert run(settings.validate(FakeDB(), PERSON, ECON, 1)).value == 1.0


# ── §5.3 step 3: manual ──────────────────────────────────────────────────────


def test_iris_is_refused_on_a_manual_setting_and_everyone_else_may_overwrite_it():
    db = FakeDB()
    db.add_desired(KPA, 1.3, manual=True, at=ago(hours=1))
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(db, IRIS, KPA, 1.4))
    problem = refusal(excinfo)
    assert (problem.code, problem.status, problem.retryable, problem.current) == ("manual", 422, False, 1.3)
    assert run(settings.validate(db, AGENT, KPA, 1.4)).manual_before is True
    assert run(settings.validate(db, PERSON, KPA, 1.4)).value == 1.4
    db.add_desired(KPA, 1.3, manual=False, at=ago(minutes=30))  # a non-manual row on top: Iris may write again
    assert run(settings.validate(db, IRIS, KPA, 1.4)).manual_before is False


def test_the_first_failure_wins_range_before_manual():
    db = FakeDB()
    db.add_desired(KPA, 1.3, manual=True, at=ago(hours=1))
    with pytest.raises(ServiceError) as excinfo:
        run(settings.validate(db, IRIS, KPA, 9.0))
    assert refusal(excinfo).code == "out_of_range"


# ── §5.3 step 4: snap ────────────────────────────────────────────────────────


def test_1_53_stores_1_55_with_snapped_from():
    checked = run(settings.validate(FakeDB(), PERSON, KPA, 1.53))
    assert (checked.value, checked.requested, checked.snapped_from) == (1.55, 1.53, 1.53)


def test_snapping_is_half_to_even_and_an_on_grid_value_is_unchanged():
    assert run(settings.validate(FakeDB(), PERSON, KPA, 1.525)).value == 1.5
    assert run(settings.validate(FakeDB(), PERSON, KPA, 1.575)).value == 1.6
    checked = run(settings.validate(FakeDB(), PERSON, KPA, 1.55))
    assert (checked.value, checked.snapped_from) == (1.55, None)


def test_taking_iris_s_plan_value_manual_keeps_it_as_is():
    db = FakeDB()
    db.add_plan(DELAY, 48.75, created_at=ago(hours=2))
    db.snapshots.append((DELAY, ago(minutes=1), 42.0))
    checked = run(settings.validate(db, PERSON, DELAY, 48.75))
    assert (checked.value, checked.snapped_from, checked.current) == (48.75, None, 48.75)
    assert run(settings.validate(db, PERSON, DELAY, 50.0)).value == 60.0  # off the 30 s grid: snapped


def test_the_device_value_is_kept_as_is_within_its_binary32_ulp_not_half_a_step():
    db = FakeDB()
    db.snapshots.append((KPA, ago(minutes=1), 1.2719999551773071))  # float32(1.272) as the controller reports it
    assert run(settings.validate(db, PERSON, KPA, 1.272)).value == 1.272
    assert run(settings.validate(db, PERSON, KPA, 1.28)).value == 1.3  # within half a step, not one ULP: snapped


# ── Readback states (§5.5) ───────────────────────────────────────────────────


def status(db, param):
    return run(settings.status(db, param))


def test_released_comes_first():
    db = FakeDB()
    db.add_desired(KPA, None, at=ago(minutes=1))
    db.clamps.append({"parameter": KPA, "ts": ago(seconds=30), "applied": 1.2, "reason": "guard"})
    result = status(db, KPA)
    assert (result.state, result.state_reason, result.desired.value) == ("released", None, None)


def test_overridden_when_a_newer_plan_wins_a_non_manual_row_and_never_a_manual_one():
    db = FakeDB()
    db.add_desired(KPA, 1.3, manual=False, at=ago(hours=2))
    db.add_plan(KPA, 1.45, created_at=ago(hours=1))
    result = status(db, KPA)
    assert (result.state, result.applied_from, result.desired.state) == ("overridden", "plan", "overridden")
    db.desired[-1]["manual"] = True  # the same row, manual: a newer plan never wins it
    result = status(db, KPA)
    assert (result.state, result.applied_from) == ("not_confirmed", "manual")


def test_a_clamp_after_the_request_holds_it_with_the_applied_value_since_the_clamp_row():
    db = FakeDB()
    db.add_desired(PRESSURE, 845.0, at=ago(minutes=10))
    clamp_at = ago(minutes=9)
    db.clamps.append({"parameter": PRESSURE, "ts": clamp_at, "applied": 840.0, "reason": "guard_x"})
    db.snapshots.append((PRESSURE, ago(minutes=1), 840.0))
    result = status(db, PRESSURE)
    assert result.state == "held"
    assert result.state_reason.model_dump() == {
        "code": "clamped",
        "detail": "guard_x",
        "applied_value": 840.0,
        "since": clamp_at,
    }


def test_a_clamp_before_the_request_does_not_count_and_one_the_device_moved_past_reads_confirmed():
    db = FakeDB()
    db.add_desired(PRESSURE, 845.0, at=ago(minutes=10))
    db.clamps.append({"parameter": PRESSURE, "ts": ago(minutes=11), "applied": 840.0, "reason": "guard_x"})
    db.snapshots.append((PRESSURE, ago(minutes=1), 840.0))
    assert status(db, PRESSURE).state == "not_confirmed"
    db.clamps.append({"parameter": PRESSURE, "ts": ago(minutes=9), "applied": 840.0, "reason": "guard_x"})
    db.snapshots.append((PRESSURE, ago(seconds=30), 845.0))  # the guardrail let go and the controller took it
    result = status(db, PRESSURE)
    assert (result.state, result.state_reason) == ("confirmed", None)


def test_an_armed_experiment_holds_the_48_policy_fields_only():
    db = FakeDB()
    db.armed = uuid.uuid4()
    db.add_desired(KPA, 1.55, at=ago(minutes=10))
    db.snapshots.append((KPA, ago(minutes=1), 1.55))
    result = status(db, KPA)
    assert (result.state, result.state_reason.code, result.state_reason.since) == ("held", "experiment", None)
    assert KPA in {spec.name for spec in wire_fields()}
    db.add_desired(PRESSURE, 845.0, at=ago(minutes=10))
    db.snapshots.append((PRESSURE, ago(minutes=1), 845.0))
    assert status(db, PRESSURE).state == "confirmed"


def test_confirmed_by_a_matching_readback_at_or_after_the_request_with_its_first_match():
    db = FakeDB()
    created = ago(minutes=10)
    db.add_desired(PRESSURE, 845.0, at=created)
    db.snapshots += [
        (PRESSURE, ago(minutes=11), 845.0),  # before the request: does not count
        (PRESSURE, ago(minutes=9), 840.0),
        (PRESSURE, ago(minutes=8), 845.0),
        (PRESSURE, ago(minutes=1), 845.0),
    ]
    result = status(db, PRESSURE)
    assert (result.state, result.confirmed_at, result.desired.confirmed_at) == (
        "confirmed",
        ago(minutes=8),
        ago(minutes=8),
    )
    assert result.readback.model_dump() == {"value": 845.0, "observed_at": ago(minutes=1), "reason": None}


def test_a_one_step_change_is_a_difference_not_a_match():
    db = FakeDB()
    db.add_desired(PRESSURE, 845.0, at=ago(minutes=10))
    db.snapshots.append((PRESSURE, ago(minutes=1), 840.0))  # within the 1 % dead-band, a whole step away
    assert status(db, PRESSURE).state == "not_confirmed"


def test_requested_under_5_minutes_then_not_confirmed():
    db = FakeDB()
    db.add_desired(PRESSURE, 845.0, at=ago(minutes=2))
    result = status(db, PRESSURE)
    assert (result.state, result.state_reason.code, result.state_reason.since) == ("requested", "no_match", None)
    db.desired[-1]["created_at"] = ago(minutes=6)
    assert status(db, PRESSURE).state == "not_confirmed"


def test_a_switch_confirms_from_its_equipment_mirror():
    db = FakeDB()
    db.add_desired(ECON, 1.0, at=ago(minutes=10))
    db.snapshots.append((ECON, ago(minutes=20), 0.0))
    db.equipment.append((SWITCH_READBACK_EQUIPMENT[ECON], ago(minutes=9), True))
    result = status(db, ECON)
    assert (result.state, result.confirmed_at, result.readback_via) == ("confirmed", ago(minutes=9), "entity")


def test_no_readback_only_for_a_setting_with_no_readback_route():
    db = FakeDB()
    result = status(db, "fog_time_window_start")
    assert (result.state, result.readback_via, result.readback) == ("no_readback", "none", None)


def test_a_setting_nobody_requested_with_no_readback_is_no_readback_never_not_confirmed():
    """#1333: not_confirmed means a request at least 5 min old that the readback does not match. A cfg-routed
    setting with no desired row, plan or push and no readback is no_readback; with a readback it is confirmed."""
    db = FakeDB()
    result = status(db, "safety_vpd_min")
    assert (result.readback_via, result.desired, result.state, result.state_reason) == (
        "cfg",
        None,
        "no_readback",
        None,
    )
    db.snapshots.append(("safety_vpd_min", ago(minutes=1), 0.3))
    assert status(db, "safety_vpd_min").state == "confirmed"
    db = FakeDB()
    db.add_desired("safety_vpd_min", 0.3, at=ago(minutes=6))
    assert status(db, "safety_vpd_min").state == "not_confirmed"


def test_an_irrigation_site_write_stays_requested_before_the_engine():
    db = FakeDB()
    write = ago(hours=3)
    db.add_desired("sched_mist_rh_max_pct", 90.0, at=write)
    result = status(db, "sched_mist_rh_max_pct")
    assert (result.state, result.readback_via) == ("requested", "program")
    assert result.readback.reason == settings.NO_PROGRAM_READBACK
    assert result.state_reason.code == "no_engine"
    assert result.state_reason.detail == settings.NO_PROGRAM_READBACK
    assert result.state_reason.since == write
    assert result.desired.state_reason.code == "no_engine"


def test_a_site_write_never_reads_not_confirmed_with_irr_engine_false_or_absent():
    for engine_row in (("false", ago(minutes=1)), None):
        db = FakeDB()
        if engine_row:
            db.system_state["irr_engine"] = engine_row
        db.add_desired("climate_yield_min", 20.0, at=ago(days=2))
        assert status(db, "climate_yield_min").state == "requested"


def site_record(*, saf4=360, rh=90, climate_yield=15):
    """A compiled program with these three site values, as irrigation_push stores it."""
    lines = [irrigation.LineSpec(line_id=line_id) for line_id in irrigation.LINE_IDS]
    site = irrigation.SiteValues(saf4_gal=saf4, rh_ceiling_pct=rh, climate_yield_min=climate_yield)
    return irrigation.compile_program(lines, [], site)


def engine_on(db, record, *, revision=42, compiled_at=None, pushed_at=None, confirmed_at=None, src="nvs"):
    """The engine on the controller, reporting ``record``'s hash, with its push row."""
    db.system_state["irr_engine"] = ("true", ago(hours=6))
    db.system_state["irr_program_id"] = (f"h={record.hash16} src={src}", ago(minutes=1))
    db.pushes.append(
        {
            "revision": revision,
            "sha256": record.sha256,
            "bytes": record.record,
            "site_desired_at": compiled_at,
            "pushed_at": pushed_at,
            "confirmed_at": confirmed_at,
            "reject_code": None,
        }
    )


def test_program_rev_42_confirmed_after_a_site_write_reads_confirmed_with_360_as_its_device_value():
    db = FakeDB()
    write = ago(minutes=30)
    db.add_desired("mister_daily_volume_max_gal", 360.0, at=write)
    engine_on(db, site_record(saf4=360), compiled_at=write, pushed_at=ago(minutes=29), confirmed_at=ago(minutes=28))
    result = status(db, "mister_daily_volume_max_gal")
    assert result.state == "confirmed"
    assert result.state_reason is None
    assert result.confirmed_at == ago(minutes=28)
    assert (result.readback.value, result.readback.observed_at) == (360.0, ago(minutes=28))


def test_each_site_row_reads_its_own_header_field():
    db = FakeDB()
    engine_on(db, site_record(saf4=400, rh=85, climate_yield=20), compiled_at=ago(hours=1), confirmed_at=ago(hours=1))
    values = {
        param: status(db, param).readback.value
        for param in ("mister_daily_volume_max_gal", "sched_mist_rh_max_pct", "climate_yield_min")
    }
    assert values == {"mister_daily_volume_max_gal": 400.0, "sched_mist_rh_max_pct": 85.0, "climate_yield_min": 20.0}
    # With no write the applied program's value is what applies.
    assert status(db, "climate_yield_min").state == "confirmed"


def test_a_program_compiled_before_the_write_does_not_confirm_it():
    db = FakeDB()
    write = ago(minutes=3)
    db.add_desired("sched_mist_rh_max_pct", 85.0, at=write)
    engine_on(db, site_record(), compiled_at=ago(hours=2), pushed_at=ago(hours=2), confirmed_at=ago(hours=2))
    result = status(db, "sched_mist_rh_max_pct")
    assert result.state == "requested"
    assert result.state_reason.code == "no_match"
    assert result.state_reason.since is None  # no push has been compiled at or after the write
    assert result.readback.value == 90.0  # the device still runs the old one


def test_a_site_write_the_applied_record_already_holds_confirms_at_once():
    """Writing the value the controller runs leaves the hash as it is, so no push will ever be compiled after it."""
    db = FakeDB()
    write = ago(minutes=20)
    db.add_desired("mister_daily_volume_max_gal", 360.0, at=write)
    engine_on(db, site_record(saf4=360), compiled_at=None, pushed_at=ago(hours=2), confirmed_at=ago(hours=2))
    result = status(db, "mister_daily_volume_max_gal")
    assert (result.state, result.state_reason, result.confirmed_at) == ("confirmed", None, write)
    assert (result.readback.value, result.readback.observed_at) == (360.0, ago(hours=2))
    # Another site row's write can have compiled the record: the rule is the value, not whose write it was.
    db = FakeDB()
    db.add_desired("mister_daily_volume_max_gal", 360.0, at=write)
    engine_on(db, site_record(saf4=360), compiled_at=ago(hours=1), pushed_at=ago(hours=1), confirmed_at=ago(hours=1))
    assert status(db, "mister_daily_volume_max_gal").state == "confirmed"


def test_a_site_write_reads_not_confirmed_at_5_min_and_no_match_names_the_first_push_after_it():
    db = FakeDB()
    write = ago(minutes=20)
    db.add_desired("sched_mist_rh_max_pct", 85.0, at=write)
    engine_on(db, site_record(), compiled_at=ago(hours=2), pushed_at=ago(hours=2), confirmed_at=ago(hours=2))
    db.pushes.append(
        {
            "revision": 43,
            "sha256": "ab" * 32,
            "bytes": site_record(rh=85).record,
            "site_desired_at": write,
            "pushed_at": ago(minutes=19),
            "confirmed_at": None,
            "reject_code": None,
        }
    )
    db.pushes.append({**db.pushes[-1], "revision": 44, "pushed_at": ago(minutes=2)})
    result = status(db, "sched_mist_rh_max_pct")
    assert result.state == "not_confirmed"
    assert (result.state_reason.code, result.state_reason.since) == ("no_match", ago(minutes=19))


def test_a_site_write_with_the_engine_takes_the_shared_reasons():
    db = FakeDB()
    db.add_desired("sched_mist_rh_max_pct", 85.0, at=ago(minutes=20))
    engine_on(db, site_record(), compiled_at=ago(hours=2), confirmed_at=ago(hours=2))
    db.heartbeat_at = ago(minutes=4)
    result = status(db, "sched_mist_rh_max_pct")
    assert (result.state_reason.code, result.state_reason.since) == ("writer_down", db.heartbeat_at)
    db.heartbeat_at = ago(seconds=30)
    db.climate_at = ago(minutes=6)
    result = status(db, "sched_mist_rh_max_pct")
    assert (result.state_reason.code, result.state_reason.since) == ("controller_not_reporting", db.climate_at)


def test_a_controller_back_on_the_built_in_program_confirms_nothing():
    db = FakeDB()
    db.add_desired("sched_mist_rh_max_pct", 85.0, at=ago(minutes=20))
    db.system_state["irr_engine"] = ("true", ago(minutes=1))
    db.system_state["irr_program_id"] = ("h=0000000000000000 src=rom", ago(minutes=1))
    result = status(db, "sched_mist_rh_max_pct")
    assert result.state == "not_confirmed"
    assert result.readback.reason == settings.NO_CONFIRMED_PROGRAM


def test_an_old_confirmed_program_counts_for_nothing_once_the_engine_is_gone():
    db = FakeDB()
    write = ago(minutes=30)
    db.add_desired("sched_mist_rh_max_pct", 90.0, at=write)
    engine_on(db, site_record(), compiled_at=write, pushed_at=write, confirmed_at=ago(minutes=29))
    db.system_state["irr_engine"] = ("false", ago(minutes=1))  # a rollback to pre-engine firmware
    result = status(db, "sched_mist_rh_max_pct")
    assert (result.state, result.state_reason.code) == ("requested", "no_engine")
    assert result.readback.value is None


# ── Each reason's since ──────────────────────────────────────────────────────


def pending(db, param=PRESSURE, *, minutes=10):
    db.add_desired(param, 845.0, at=ago(minutes=minutes))
    db.snapshots.append((param, ago(minutes=1), 840.0))


def test_a_heap_deferral_is_deferred_since_its_push():
    db = FakeDB()
    pending(db)
    db.changes.append(
        {"parameter": PRESSURE, "ts": ago(minutes=9), "value": 845.0, "source": "manual", "delivery_status": "deferred_heap_pressure"}
    )  # fmt: skip
    reason = status(db, PRESSURE).state_reason
    assert (reason.code, reason.since, reason.detail) == ("deferred", ago(minutes=9), None)


def test_a_failed_push_is_push_failed_with_the_status_since_its_push():
    db = FakeDB()
    pending(db)
    db.changes.append(
        {"parameter": PRESSURE, "ts": ago(minutes=9), "value": 845.0, "source": "manual", "delivery_status": "failed"}
    )
    reason = status(db, PRESSURE).state_reason
    assert (reason.code, reason.since, reason.detail) == ("push_failed", ago(minutes=9), "failed")


def test_a_stale_or_missing_heartbeat_is_writer_down_since_the_heartbeat():
    db = FakeDB()
    pending(db)
    db.heartbeat_at = ago(minutes=4)
    reason = status(db, PRESSURE).state_reason
    assert (reason.code, reason.since) == ("writer_down", ago(minutes=4))
    db.heartbeat_at = None
    assert status(db, PRESSURE).state_reason.model_dump() == {
        "code": "writer_down",
        "detail": None,
        "applied_value": None,
        "since": None,
    }


def test_a_fresh_heartbeat_and_climate_6_minutes_old_is_controller_not_reporting_since_that_row():
    db = FakeDB()
    db.add_desired(PRESSURE, 845.0, at=ago(minutes=2))  # a pending write
    db.heartbeat_at, db.climate_at = ago(seconds=30), ago(minutes=6)
    result = status(db, PRESSURE)
    assert (result.state, result.state_reason.code, result.state_reason.since) == (
        "requested",
        "controller_not_reporting",
        ago(minutes=6),
    )


def test_no_match_is_since_the_push_the_request_went_out_in():
    db = FakeDB()
    pending(db)
    db.changes += [
        {"parameter": PRESSURE, "ts": ago(minutes=12), "value": 840.0, "source": "band", "delivery_status": "confirmed"},
        {"parameter": PRESSURE, "ts": ago(minutes=9), "value": 845.0, "source": "manual", "delivery_status": "sent"},
        {"parameter": PRESSURE, "ts": ago(minutes=8), "value": 840.0, "source": "esp32", "delivery_status": "observed"},
    ]  # fmt: skip
    reason = status(db, PRESSURE).state_reason
    assert (reason.code, reason.since) == ("no_match", ago(minutes=9))


def test_the_reasons_come_in_their_order():
    db = FakeDB()
    pending(db)
    db.heartbeat_at = None
    db.changes.append(
        {"parameter": PRESSURE, "ts": ago(minutes=9), "value": 845.0, "source": "manual", "delivery_status": "failed"}
    )
    assert status(db, PRESSURE).state_reason.code == "push_failed"  # before writer_down
    db.clamps.append({"parameter": PRESSURE, "ts": ago(minutes=8), "applied": 840.0, "reason": "guard"})
    assert status(db, PRESSURE).state_reason.code == "clamped"  # first of all


# ── applied_from (§5.2) ──────────────────────────────────────────────────────


def test_applied_from_the_plan_when_there_is_no_desired_row():
    db = FakeDB()
    db.add_plan(DELAY, 48.75, created_at=ago(hours=1))
    db.snapshots.append((DELAY, ago(minutes=1), 48.75))
    result = status(db, DELAY)
    assert (result.applied_from, result.state, result.desired) == ("plan", "confirmed", None)
    db.snapshots.append((DELAY, ago(seconds=10), 42.0))
    assert status(db, DELAY).state == "not_confirmed"  # the plan value against the readback, 1 % dead-band


def test_applied_from_desired_manual_band_and_default():
    db = FakeDB()
    db.add_plan(KPA, 1.45, created_at=ago(hours=2))
    db.add_desired(KPA, 1.3, manual=False, at=ago(hours=1))
    assert status(db, KPA).applied_from == "desired"
    db.desired[-1]["manual"] = True
    assert status(db, KPA).applied_from == "manual"
    db.add_desired(KPA, None, at=ago(minutes=1))  # released: the plan applies again
    assert status(db, KPA).applied_from == "plan"
    assert status(db, "band_temp_low_sr").applied_from == "band"  # push_owner band
    assert status(db, "gl_main_sunrise_hour").applied_from == "band"  # push_owner schedule
    assert status(db, PRESSURE).applied_from == "default"


def test_without_a_desired_row_or_plan_a_band_value_is_judged_against_the_newest_push():
    db = FakeDB()
    db.changes.append(
        {"parameter": "band_temp_low_sr", "ts": ago(hours=1), "value": 62.5, "source": "band", "delivery_status": "confirmed"}
    )  # fmt: skip
    db.snapshots.append(("band_temp_low_sr", ago(minutes=1), 62.5))
    assert status(db, "band_temp_low_sr").state == "confirmed"
    db.snapshots.append(("band_temp_low_sr", ago(seconds=5), 60.0))
    assert status(db, "band_temp_low_sr").state == "not_confirmed"


def test_status_of_an_unknown_setting_is_not_found():
    with pytest.raises(ServiceError) as excinfo:
        status(FakeDB(), "no_such_setting")
    assert (refusal(excinfo).code, refusal(excinfo).status) == ("not_found", 404)


# ── Writes ───────────────────────────────────────────────────────────────────


def write(db, actor, param, value, manual, *, reason=None, request_id=None, extra_detail=None):
    return run(settings.write(db, actor, param, value, manual, reason, request_id, extra_detail))


def test_a_write_snaps_stores_manual_as_passed_and_logs_one_activity_row():
    db = FakeDB()
    db.snapshots.append((KPA, ago(minutes=1), 1.6))
    result = write(db, PERSON, KPA, 1.53, True, reason="dry afternoon")
    desired = result.value
    assert (desired.value, desired.snapped_from, desired.manual, desired.manual_before, desired.state) == (
        1.55,
        1.53,
        True,
        False,
        "requested",
    )
    assert (desired.actor.source, desired.actor.name, desired.reason) == ("dashboard", "jason", "dry afternoon")
    assert openapi_errors("DesiredSetting", result.rest_json()) == []
    [stored] = db.desired
    assert (stored["value"], stored["manual"], stored["source"], stored["actor"]) == (1.55, True, "dashboard", "jason")
    [logged] = db.activity
    assert (logged["action"], logged["target"], logged["reason"]) == ("setting.write", KPA, "dry afternoon")
    assert logged["detail"] == {
        "ref": {"table": "desired_state", "id": stored["id"]},
        "desired_id": stored["id"],
        "before": 1.6,
        "value": 1.55,
        "requested": 1.53,
        "manual": {"before": False, "after": True},
    }
    assert not any("setpoint_changes" in sql and ("INSERT" in sql or "UPDATE" in sql) for sql in db.sql)


def test_manual_has_no_default_and_an_agent_s_false_clears_a_manual_flag():
    db = FakeDB()
    db.add_desired(KPA, 1.3, manual=True, at=ago(hours=1))
    with pytest.raises(ServiceError) as excinfo:
        write(db, AGENT, KPA, 1.4, None)
    assert (refusal(excinfo).code, refusal(excinfo).field) == ("invalid_request", "manual")
    result = write(db, AGENT, KPA, 1.4, False)
    assert (result.value.manual, result.value.manual_before) == (False, True)
    assert db.desired[-1]["manual"] is False
    assert db.activity[-1]["detail"]["manual"] == {"before": True, "after": False}


def test_an_iris_write_to_a_manual_setting_is_refused_and_stores_nothing():
    db = FakeDB()
    db.add_desired(KPA, 1.3, manual=True, at=ago(hours=1))
    with pytest.raises(ServiceError) as excinfo:
        write(db, IRIS, KPA, 1.4, False)
    assert refusal(excinfo).code == "manual"
    assert len(db.desired) == 1 and db.activity == []


def test_taking_iris_s_48_75_manual_stores_48_75():
    db = FakeDB()
    db.add_plan(DELAY, 48.75, created_at=ago(hours=1))
    result = write(db, PERSON, DELAY, 48.75, True)
    assert (result.value.value, result.value.snapped_from, db.desired[-1]["value"]) == (48.75, None, 48.75)


def test_a_band_value_write_moves_the_current_season_rows_only_and_names_them():
    db = FakeDB()
    rows = orchid_rows(db)
    result = write(db, PERSON, "zone_vpd_target_center_sm", 0.913, True)
    assert result.value.value == 0.91  # the 0.01 kPa push quantum
    assert rows[("fall", "sm")]["value"] == 0.91
    assert {key: row["value"] for key, row in rows.items() if key != ("fall", "sm")} == {
        **{("all", a): 0.70 for a in ("sr", "sm", "ss", "mid")},
        **{("fall", a): 0.80 for a in ("sr", "ss", "mid")},
    }
    assert db.activity[-1]["detail"]["anchor_rows"] == [rows[("fall", "sm")]["id"]]


def test_a_width_write_moves_that_column_on_all_four_rows_at_the_best_score():
    db = FakeDB()
    rows = orchid_rows(db)
    write(db, AGENT, "zone_vpd_width_below_center", 0.25, False)
    assert {key: row["width_below"] for key, row in rows.items()} == {
        **{("all", a): 0.20 for a in ("sr", "sm", "ss", "mid")},
        **{("fall", a): 0.25 for a in ("sr", "sm", "ss", "mid")},
    }
    assert all(row["width_above"] == 0.35 and row["value"] in (0.70, 0.80) for row in rows.values())
    assert db.activity[-1]["detail"]["anchor_rows"] == sorted(
        rows[("fall", a)]["id"] for a in ("sr", "sm", "ss", "mid")
    )


def test_a_house_anchor_write_maps_to_the_house_curve():
    db = FakeDB()
    house = db.add_anchor("house", "temp_low", "sr", "all", 62.5)
    write(db, PERSON, "band_temp_low_sr", 63.04, True)
    assert house["value"] == 63.0
    assert BAND_PARAM_ROWS["band_temp_low_sr"].crop_types == ("house",)


def test_extra_detail_merges_into_the_activity_row_and_never_replaces_a_write_key():
    db = FakeDB()
    trigger = uuid.uuid4()
    write(db, IRIS, KPA, 1.5, False, extra_detail={"trigger_id": trigger, "planner_instance": "iris-a"})
    detail = db.activity[-1]["detail"]
    assert (detail["trigger_id"], detail["planner_instance"], detail["value"]) == (str(trigger), "iris-a", 1.5)
    with pytest.raises(ValueError, match="manual"):
        write(db, IRIS, KPA, 1.5, False, extra_detail={"manual": "no"})


def test_a_retried_write_replays_the_stored_row():
    db = FakeDB()
    key = uuid.uuid4()
    first = write(db, PERSON, KPA, 1.53, True, request_id=key)
    again = write(db, PERSON, KPA, 1.53, True, request_id=key)
    assert (first.replayed, again.replayed) == (False, True)
    assert again.value.id == first.value.id and again.value.snapped_from == 1.53 and again.value.manual_before is False
    assert len(db.desired) == 1 and len(db.activity) == 1
    with pytest.raises(ServiceError) as excinfo:
        write(db, PERSON, KPA, 1.6, True, request_id=key)
    assert refusal(excinfo).code == "request_id_reused"


def test_a_refused_write_stores_nothing_and_a_viewer_is_forbidden_first():
    db = FakeDB()
    with pytest.raises(ServiceError) as excinfo:
        write(db, PERSON, "temp_low", 50.0, True)
    assert refusal(excinfo).code == "read_only" and db.desired == [] and db.activity == []
    with pytest.raises(ServiceError) as excinfo:
        write(db, Actor.person("caretaker", "viewer"), KPA, float("nan"), True)
    assert refusal(excinfo).code == "forbidden"


# ── Release ──────────────────────────────────────────────────────────────────


def release(db, actor, param, *, reason=None, request_id=None, extra_detail=None):
    return run(settings.release(db, actor, param, reason, request_id, extra_detail))


def test_a_release_appends_a_null_row_and_logs_the_cleared_flag():
    db = FakeDB()
    db.add_desired(KPA, 1.3, manual=True, at=ago(hours=1))
    db.snapshots.append((KPA, ago(minutes=1), 1.3))
    result = release(db, PERSON, KPA, reason="back to Iris")
    assert (result.value.value, result.value.manual, result.value.state, result.value.manual_before) == (
        None,
        False,
        "released",
        True,
    )
    assert openapi_errors("DesiredSetting", result.rest_json()) == []
    assert (db.desired[-1]["value"], db.desired[-1]["manual"]) == (None, False)
    logged = db.activity[-1]
    assert (logged["action"], logged["reason"]) == ("setting.release", "back to Iris")
    assert logged["detail"]["manual"] == {"before": True, "after": False} and logged["detail"]["value"] is None
    assert logged["detail"]["before"] == 1.3


def test_iris_cannot_release_a_manual_setting_and_read_only_rows_cannot_be_released():
    db = FakeDB()
    db.add_desired(KPA, 1.3, manual=True, at=ago(hours=1))
    with pytest.raises(ServiceError) as excinfo:
        release(db, IRIS, KPA)
    assert refusal(excinfo).code == "manual" and len(db.desired) == 1
    with pytest.raises(ServiceError) as excinfo:
        release(db, PERSON, "sw_onchip_band_enabled")
    assert refusal(excinfo).code == "read_only"


# ── Catalog ──────────────────────────────────────────────────────────────────


def catalog(db=None):
    return {row.param: row for row in run(settings.catalog(db or FakeDB()))}


def test_the_catalog_lists_every_registry_row_with_the_registry_s_label_and_unit():
    """The registry label, except where it is the raw name: then the setting_help label (#1332)."""
    rows = catalog()
    assert list(rows) == list(REGISTRY)
    for name, spec in REGISTRY.items():
        assert (rows[name].label, rows[name].unit) == (setting_label(name), spec.unit), name
        assert rows[name].label != name, name
        if spec.label != name:
            assert rows[name].label == spec.label, name
    assert (rows[KPA].label, rows[KPA].unit) == ("VPD Mister Engage", "kPa")


def test_every_catalog_row_carries_its_help_and_a_read_only_row_its_kind():
    """#1332 and #1333: help on every row; read_only_kind on exactly the read-only rows, named by the rule."""
    rows = catalog()
    for name, row in rows.items():
        assert row.help is not None and row.help.summary and row.help.effect, name
        assert row.help == setting_help(name), name
        assert (row.read_only_kind is None) is row.writable, name
    assert rows["temp_high"].read_only_kind == "legacy_wide_limit"
    assert rows["mister_on_s"].read_only_kind == "retired"
    assert rows["outdoor_temp_f"].read_only_kind == "readback_context"
    assert rows["sw_onchip_band_enabled"].read_only_kind == "band_owner"
    assert "activity_start_hour" not in rows  # #1477: retired with its firmware entity
    assert rows["safety_max"].help.guardrail and rows["safety_max"].writable
    assert rows[KPA].model_dump(mode="json")["help"]["raise"] == setting_help(KPA).raise_


def test_catalog_groups_and_read_only_reasons():
    db = FakeDB()
    orchid_rows(db)
    rows = catalog(db)
    assert (rows[KPA].group, rows[KPA].writable, rows[KPA].read_only_reason) == ("planner_policy", True, None)
    assert rows["sched_mist_rh_max_pct"].group == "site_water_caps"
    assert rows["zone_vpd_target_center_sm"].group == "crop_band"
    assert rows["zone_vpd_target_east_sm"].group == "read_only"  # no pepper rows in this fixture
    assert rows["zone_priority_center"].read_only_reason == settings.NO_ANCHOR_ROW
    for name in ("temp_low", "sw_onchip_band_enabled"):
        assert (rows[name].group, rows[name].writable) == ("read_only", False)
        assert rows[name].read_only_reason == desired_write_rule(name)[1]
    for row in rows.values():
        assert row.writable is (row.group != "read_only"), row.param
        assert (row.min, row.max) == settings.bounds(row.param)


def test_catalog_readback_via_and_rows_match_the_openapi_setting():
    db = FakeDB()
    db.snapshots.append((KPA, ago(minutes=1), 1.6))
    db.add_desired(KPA, 1.55, manual=True, at=ago(minutes=2))
    rows = catalog(db)
    assert {rows[name].readback_via for name in (KPA, ECON, "sched_mist_rh_max_pct", "fog_time_window_start")} == {
        "cfg",
        "entity",
        "program",
        "none",
    }
    assert (rows[KPA].desired.value, rows[KPA].applied_from, rows[KPA].state) == (1.55, "manual", "requested")
    for row in rows.values():
        assert openapi_errors("Setting", row.model_dump(mode="json")) == [], row.param


def test_the_models_carry_the_openapi_fields():
    schemas = OPENAPI["components"]["schemas"]
    for model, name in (
        (settings.Setting, "Setting"),
        (settings.DesiredSetting, "DesiredSetting"),
        (settings.StateReason, "StateReason"),
        (settings.Reading, "Reading"),
        (settings.TimedValue, "TimedValue"),
        (settings.PlanWaypoint, "PlanWaypoint"),
    ):
        assert {field.alias or key for key, field in model.model_fields.items()} == set(schemas[name]["properties"])
    history = {field.alias or key for key, field in settings.SettingHistory.model_fields.items()}
    assert history == set(schemas["SettingHistory"]["properties"])


# ── History ──────────────────────────────────────────────────────────────────


def test_history_returns_the_rows_from_the_one_in_force_and_the_readback_steps():
    db = FakeDB()
    start, end = ago(hours=6), ago(hours=1)
    old = db.add_desired(PRESSURE, 840.0, at=ago(hours=8))
    mid = db.add_desired(PRESSURE, 845.0, at=ago(hours=5))
    db.add_desired(PRESSURE, None, at=ago(hours=4))
    db.add_desired(PRESSURE, 850.0, at=ago(minutes=30))  # after the window: not listed
    db.snapshots += [
        (PRESSURE, ago(hours=7), 840.0),
        (PRESSURE, ago(hours=5, minutes=-1), 845.0),
        (PRESSURE, ago(hours=4, minutes=30), 845.0),
        (PRESSURE, ago(hours=3), 840.0),
    ]
    result = run(settings.history(db, PRESSURE, start, end))
    body = result.model_dump(mode="json")
    assert body["from"] == start.isoformat().replace("+00:00", "Z") and "from_" not in body
    assert [(row.id, row.value, row.state) for row in result.desired] == [
        (old["id"], 840.0, "confirmed"),
        (mid["id"], 845.0, "confirmed"),
        (mid["id"] + 1, None, "released"),
    ]
    assert result.desired[1].confirmed_at == ago(hours=5, minutes=-1)
    assert [(point.at, point.value) for point in result.readback] == [
        (ago(hours=7), 840.0),
        (ago(hours=5, minutes=-1), 845.0),
        (ago(hours=3), 840.0),
    ]
    assert (result.unit, result.min, result.max, result.plan) == ("hPa", 700.0, 1100.0, [])
    assert openapi_errors("SettingHistory", body) == []


def test_history_refuses_a_backwards_or_naive_window():
    for start, end in ((NOW, ago(hours=1)), (NOW.replace(tzinfo=None), NOW)):
        with pytest.raises(ServiceError) as excinfo:
            run(settings.history(FakeDB(), PRESSURE, start, end))
        assert refusal(excinfo).code == "invalid_request"


# ── Batched reads for API-07 and API-17 (VD-55) ─────────────────────────────


def test_statuses_is_status_for_each_setting_in_one_batch_and_leaves_unknown_names_out():
    db = FakeDB()
    db.add_desired(KPA, 1.55, manual=True, at=ago(minutes=10))
    db.snapshots.append((KPA, ago(minutes=1), 1.55))
    db.add_plan(DELAY, 48.75, created_at=ago(hours=1))
    db.snapshots.append((DELAY, ago(minutes=1), 48.75))
    db.add_desired(PRESSURE, 845.0, at=ago(minutes=10))
    db.clamps.append({"parameter": PRESSURE, "ts": ago(minutes=9), "applied": 840.0, "reason": "guard_x"})
    db.snapshots.append((PRESSURE, ago(minutes=1), 840.0))
    expected = {param: status(db, param) for param in (KPA, DELAY, PRESSURE)}
    db.sql.clear()
    batched = run(settings.statuses(db, [KPA, DELAY, "no_such_setting", PRESSURE, KPA]))
    assert batched == expected
    assert [result.state for result in batched.values()] == ["confirmed", "confirmed", "held"]
    assert db.sql.count(reads._REPORTING_SQL) == 1  # one gather for all three
    assert run(settings.statuses(db, ["no_such_setting"])) == {}


def test_desired_states_give_each_named_row_the_state_history_gives_it():
    db = FakeDB()
    old = db.add_desired(PRESSURE, 840.0, at=ago(hours=8))
    mid = db.add_desired(PRESSURE, 845.0, at=ago(hours=5))
    released = db.add_desired(PRESSURE, None, at=ago(hours=4))
    unconfirmed = db.add_desired(PRESSURE, 850.0, at=ago(hours=3))
    newest = db.add_desired(PRESSURE, 855.0, at=ago(minutes=2))
    other = db.add_desired(KPA, 1.55, at=ago(minutes=30))
    db.snapshots += [
        (PRESSURE, ago(hours=7), 840.0),
        (PRESSURE, ago(hours=5, minutes=-1), 845.0),
        (PRESSURE, ago(hours=1), 845.0),  # 850 never read back before the next row
        (KPA, ago(minutes=29), 1.55),
    ]
    ids = [row["id"] for row in (old, mid, released, unconfirmed, newest, other)]
    states = run(settings.desired_states(db, [*ids, 999_999, True, "7", 2**70]))
    assert set(states) == set(ids)  # an id naming no row, or not an id, is left out
    assert {row_id: (states[row_id].state, states[row_id].confirmed_at) for row_id in ids} == {
        old["id"]: ("confirmed", ago(hours=7)),
        mid["id"]: ("confirmed", ago(hours=5, minutes=-1)),
        released["id"]: ("released", None),
        unconfirmed["id"]: ("not_confirmed", None),
        newest["id"]: ("requested", None),  # the newest row reads its full status
        other["id"]: ("confirmed", ago(minutes=29)),
    }
    assert states[newest["id"]] == status(db, PRESSURE).desired
    history = run(settings.history(db, PRESSURE, ago(hours=9), NOW))
    assert {row.id: row for row in history.desired} == {row_id: states[row_id] for row_id in ids[:5]}
    assert run(settings.desired_states(db, [])) == {} and run(settings.desired_states(db, [999_999])) == {}
