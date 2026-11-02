"""Irrigation compiler, bounds, submit validation and agenda tests (VD-37, #1152).

The bounds per line kind, the M3 seed rows and the OpenAPI contract are written out by hand here,
so the module is checked against the design (irrigation §3, §4.1-§4.3) rather than against itself.
The compiler must reproduce VD-36's golden vectors byte for byte
(``fixtures/irrigation_program_vectors.json``): the seed program from the rows M3 (#1153) seeds,
and every vector from the rows its record describes.
"""

from __future__ import annotations

import ast
import json
import os
import random
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError

from verdify_schemas import irrigation as irr
from verdify_schemas import solar as schemas_solar
from verdify_schemas.irrigation import (
    Agenda,
    CompiledProgram,
    DripRunArgs,
    FertDripRunArgs,
    FertMisterRunArgs,
    LineSpec,
    MisterRunArgs,
    ProgramRecord,
    Refusal,
    ScheduleSpec,
    SiteValues,
    Start,
    agenda,
    bounds_by_kind,
    compile_program,
    local_start,
    run_args,
    site_values,
    validate_line,
    validate_run,
    validate_schedule,
)
from verdify_schemas.solar import compute_solar_times
from verdify_schemas.tunable_registry import IRRIGATION_SITE_PARAMS, REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "irrigation_program_vectors.json"
OPENAPI = REPO_ROOT / "docs" / "design" / "dashboard" / "openapi" / "dashboard-api.v1.yaml"
DENVER = ZoneInfo("America/Denver")
MONDAY = date(2026, 11, 2)  # a Monday, MST

# irrigation §3 and §4.3, by hand.
LINE_KIND = {
    "L01": "drip",
    "L02": "fert_drip",
    "L03": "master",
    "L04": "mister",
    "L05": "drip",
    "L06": "fert_drip",
    "L07": "mister",
    "L08": "fert_mister",
    "L09": "mister",
    "L10": "fert_mister",
    "L11": "fog",
}
KIND_BOUNDS = {
    "drip": {"on_s": (10, 7200), "count": (1, 1), "gap_s": (0, 0), "prewet_s": (0, 0), "flush_s": (0, 0)},
    "fert_drip": {"on_s": (10, 7200), "count": (1, 1), "gap_s": (0, 0), "prewet_s": (0, 1800), "flush_s": (0, 1800)},
    "mister": {"on_s": (10, 90), "count": (1, 60), "gap_s": (30, 600), "prewet_s": (0, 0), "flush_s": (0, 0)},
    "fert_mister": {
        "on_s": (10, 90),
        "count": (1, 60),
        "gap_s": (30, 600),
        "prewet_s": (0, 1800),
        "flush_s": (0, 1800),
    },
}
RUN_BOUNDS = {
    "drip": {"minutes": (1, 120)},
    "fert_drip": {"minutes": (1, 120), "prewet_s": (0, 1800), "flush_s": (0, 1800)},
    "mister": {"on_s": (10, 90), "count": (1, 60), "gap_s": (30, 600)},
    "fert_mister": {
        "on_s": (10, 90),
        "count": (1, 60),
        "gap_s": (30, 600),
        "prewet_s": (0, 1800),
        "flush_s": (0, 1800),
    },
}
EVERY_DAY = list(irr.WEEKDAYS)


def _seed_line_rows() -> list[dict]:
    """The 11 ``irrigation_line`` rows M3 (#1153) seeds, as database rows with their other columns."""
    seeds = {
        "L01": {"kind": "drip", "enabled": True, "daily_cap_min": 60},
        "L02": {"kind": "fert_drip", "clean_partner": "L01"},
        "L03": {"kind": "master"},
        "L04": {
            "kind": "mister",
            "enabled": True,
            "daily_cap_min": 120,
            "vpd_reserve_min": 60,
            "vpd_member": True,
            "schedule_member": False,
        },
        "L05": {"kind": "drip", "reserved": True, "daily_cap_min": 30},
        "L06": {"kind": "fert_drip", "clean_partner": "L05", "reserved": True},
        "L07": {
            "kind": "mister",
            "daily_cap_min": 45,
            "vpd_reserve_min": 15,
            "vpd_member": False,
            "schedule_member": False,
        },
        "L08": {"kind": "fert_mister", "clean_partner": "L07"},
        "L09": {
            "kind": "mister",
            "daily_cap_min": 45,
            "vpd_reserve_min": 15,
            "vpd_member": False,
            "schedule_member": False,
        },
        "L10": {"kind": "fert_mister", "clean_partner": "L09"},
        "L11": {"kind": "fog"},
    }
    rows = []
    for number, (line_id, seed) in enumerate(seeds.items(), start=1):
        rows.append(
            {
                "greenhouse_id": "vallery",
                "line_id": line_id,
                "name": f"line {line_id}",
                "zone": "wall",
                "relay": "relay",
                "equipment": "equipment",
                "water_system": None,
                "device_idx": number - 1,
                "clean_partner": None,
                "reserved": False,
                "enabled": False,
                "vpd_member": None,
                "schedule_member": None,
                "daily_cap_min": 0,
                "vpd_reserve_min": None,
                "paused_until": None,
                "manual": False,
                "checked_by": None,
                "checked_at": None,
                "check_note": None,
                "source": "system",
                "updated_by": "migration",
                "updated_at": datetime(2026, 11, 2, tzinfo=UTC),
                "reason": None,
            }
            | seed
        )
    return rows


def _seed_slot_rows() -> list[dict]:
    """The three ``vanda-degraded`` ``irrigation_slot`` rows M3 seeds (D-IRP-12)."""
    return [
        {
            "greenhouse_id": "vallery",
            "slot_id": slot_id,
            "line_id": "L04",
            "enabled": True,
            "days": 127,
            "start_kind": "solar_noon",
            "start_min": offset,
            "on_s": 45,
            "count": 4,
            "gap_s": 60,
            "prewet_s": 0,
            "flush_s": 0,
            "degraded_only": True,
            "label": "vanda-degraded",
            "manual": False,
            "source": "system",
            "updated_by": "migration",
            "updated_at": datetime(2026, 11, 2, tzinfo=UTC),
            "reason": None,
            "created_at": datetime(2026, 11, 2, tzinfo=UTC),
        }
        for slot_id, offset in ((1, -120), (2, 0), (3, 120))
    ]


def _seed_lines(**changes: dict) -> list[LineSpec]:
    rows = _seed_line_rows()
    return [LineSpec.from_row(row | changes.get(row["line_id"], {})) for row in rows]


def _seed_slots() -> list[ScheduleSpec]:
    return [ScheduleSpec.from_row(row) for row in _seed_slot_rows()]


def _sched(slot_id: int | None = None, line_id: str = "L01", **fields: object) -> ScheduleSpec:
    base: dict[str, object] = {"days": ["mon"], "start": {"kind": "clock", "clock": "07:00"}, "on_s": 600}
    if LINE_KIND.get(line_id) in ("mister", "fert_mister"):
        base |= {"on_s": 45, "count": 4, "gap_s": 60}
    return ScheduleSpec(slot_id=slot_id, line_id=line_id, **(base | fields))


def _clock(text: str) -> dict[str, str]:
    return {"kind": "clock", "clock": text}


@pytest.fixture(scope="module")
def vectors() -> dict[str, dict]:
    return {vector["name"]: vector for vector in json.loads(FIXTURE_PATH.read_text())["vectors"]}


@pytest.fixture(scope="module")
def schemas() -> dict:
    return yaml.safe_load(OPENAPI.read_text())["components"]["schemas"]


# ── Bounds and fixed codes ───────────────────────────────────────────────────


def test_bounds_by_kind_is_the_design_table() -> None:
    assert {kind: dict(fields) for kind, fields in irr.BOUNDS_BY_KIND.items()} == KIND_BOUNDS
    assert {kind: dict(fields) for kind, fields in irr.RUN_BOUNDS_BY_KIND.items()} == RUN_BOUNDS
    assert dict(irr.LINE_BOUNDS) == {"daily_cap_min": (0, 480), "vpd_reserve_min": (0, 480)}
    assert (irr.MAX_SCHEDULES, irr.MIN_SPACING_MIN) == (24, 30)
    assert dict(irr.LINE_KIND) == LINE_KIND
    assert dict(irr.CLEAN_PARTNER) == {"L02": "L01", "L06": "L05", "L08": "L07", "L10": "L09"}
    assert irr.LINE_KINDS == ("drip", "mister", "fert_drip", "fert_mister", "master", "fog")
    schedulable = {irr.DEVICE_IDX[line_id] for line_id, kind in LINE_KIND.items() if kind in KIND_BOUNDS}
    assert schedulable == irr.SCHEDULABLE_IDX
    assert {line_id for line_id, kind in LINE_KIND.items() if kind == "mister"} == set(irr.MISTER_MASK_BIT)
    assert bounds_by_kind() == {
        kind: {field: {"min": lo, "max": hi} for field, (lo, hi) in fields.items()}
        for kind, fields in KIND_BOUNDS.items()
    }
    assert dict(irr.SITE_PARAM_FIELDS) == {
        "mister_daily_volume_max_gal": "saf4_gal",
        "sched_mist_rh_max_pct": "rh_ceiling_pct",
        "climate_yield_min": "climate_yield_min",
    }
    assert set(irr.SITE_PARAM_FIELDS) == IRRIGATION_SITE_PARAMS


def test_golden_vector_slots_stay_inside_bounds_by_kind(vectors: dict[str, dict]) -> None:
    for vector in vectors.values():
        record = ProgramRecord.model_validate(vector["program"])
        for slot in record.slots:
            for field, (lo, hi) in irr.BOUNDS_BY_KIND[LINE_KIND[irr.LINE_IDS[slot.line]]].items():
                assert lo <= getattr(slot, field) <= hi, (vector["name"], slot.slot_id, field)


def test_models_and_bounds_match_the_openapi_contract(schemas: dict) -> None:
    start = schemas["ScheduleStart"]["properties"]
    assert start["kind"]["enum"] == list(get_args(irr.StartKind))
    assert (start["offset_min"]["minimum"], start["offset_min"]["maximum"]) == irr.START_RANGE[1]
    assert schemas["ScheduleDays"]["items"]["enum"] == list(irr.WEEKDAYS)
    schedule = schemas["ScheduleInput"]["properties"]
    for field in ("on_s", "count", "gap_s", "prewet_s", "flush_s"):
        hull = (min(b[field][0] for b in KIND_BOUNDS.values()), max(b[field][1] for b in KIND_BOUNDS.values()))
        assert (schedule[field]["minimum"], schedule[field]["maximum"]) == hull, field
    run = schemas["RunRequest"]["properties"]
    for field in ("minutes", "on_s", "count", "gap_s", "prewet_s", "flush_s"):
        ranges = [b[field] for b in RUN_BOUNDS.values() if field in b]
        hull = (min(r[0] for r in ranges), max(r[1] for r in ranges))
        assert (run[field]["minimum"], run[field]["maximum"]) == hull, field
    line = schemas["LineUpdate"]["properties"]
    assert (line["daily_cap_min"]["minimum"], line["daily_cap_min"]["maximum"]) == irr.LINE_BOUNDS["daily_cap_min"]
    assert (
        schemas["ScheduleAgenda"]["properties"]["counts"]["properties"]["max_schedules"]["const"] == irr.MAX_SCHEDULES
    )
    assert set(get_args(irr.RefusalCode)) <= set(schemas["ProblemCode"]["enum"])
    occurrence = schemas["Occurrence"]
    assert {"slot_id", "line_id", "planned_start", "status", "reason"} <= set(occurrence["properties"])
    assert {"planned", "skipped"} <= set(occurrence["properties"]["status"]["enum"])
    assert set(schemas["ScheduleAgenda"]["properties"]["solar"]["items"]["required"]) == set(irr.SolarDay.model_fields)


def test_refusal_is_a_problem_body(schemas: dict) -> None:
    refusal = Refusal(code="out_of_range", field="on_s", min=10, max=90, current=45, detail="on_s must be 10-90 s")
    body = refusal.problem()
    assert body == {
        "type": "about:blank",
        "title": "Out of range",
        "status": 422,
        "code": "out_of_range",
        "detail": "on_s must be 10-90 s",
        "retryable": False,
        "field": "on_s",
        "min": 10,
        "max": 90,
        "current": 45,
    }
    problem = schemas["Problem"]
    assert set(problem["required"]) <= set(body)
    assert set(body) <= set(problem["properties"])
    minimal = Refusal(code="too_many_schedules", current=24, max=24, detail="24 stored").problem()
    assert "field" not in minimal and "min" not in minimal and minimal["current"] == 24
    with pytest.raises(ValidationError):
        Refusal(code="line_busy", detail="not a submit code")


# ── Compiler ─────────────────────────────────────────────────────────────────


def test_seed_rows_compile_to_the_seed_vector(vectors: dict[str, dict]) -> None:
    """Acceptance: M3's seed rows with the site values at the registry defaults give the seed vector."""
    site = site_values([])
    assert site == SiteValues(saf4_gal=360, rh_ceiling_pct=90, climate_yield_min=15)
    compiled = compile_program(_seed_lines(), _seed_slots(), site)
    assert isinstance(compiled, CompiledProgram)
    assert compiled.record.hex() == vectors["seed"]["hex"]
    assert compiled.sha256 == vectors["seed"]["sha256"]
    assert compiled.hash16 == vectors["seed"]["sha256"][:16]
    assert compiled.slot_ids == (1, 2, 3)
    assert compiled.not_on_controller == ()
    assert irr.unpack_program(compiled.record) == ProgramRecord.model_validate(vectors["seed"]["program"])


def _specs_from_record(record: ProgramRecord) -> tuple[list[LineSpec], list[ScheduleSpec], SiteValues]:
    """The line, schedule and site rows a record describes."""
    lines = []
    for idx, line_id in enumerate(irr.LINE_IDS):
        entry = record.lines[idx]
        flags = {}
        if line_id in irr.MISTER_MASK_BIT:
            bit = irr.MISTER_MASK_BIT[line_id]
            flags = {
                "vpd_member": bool(record.vpd_member_mask >> bit & 1),
                "schedule_member": bool(record.schedule_member_mask >> bit & 1),
            }
        lines.append(
            LineSpec(
                line_id=line_id,
                enabled=bool(record.enabled_mask >> idx & 1),
                daily_cap_min=entry.daily_cap_min,
                vpd_reserve_min=entry.vpd_reserve_min,
                paused_until=None if entry.paused_until == 0 else datetime.fromtimestamp(entry.paused_until, UTC),
                **flags,
            )
        )
    kinds = {code: kind for kind, code in irr.START_KIND.items()}
    slots = [
        ScheduleSpec(
            slot_id=slot.slot_id,
            line_id=irr.LINE_IDS[slot.line],
            days=slot.weekday_mask,
            start=Start(kind=kinds[slot.start_kind], minute=slot.start),
            on_s=slot.on_s,
            count=slot.count,
            gap_s=slot.gap_s,
            prewet_s=slot.prewet_s,
            flush_s=slot.flush_s,
            degraded_only=slot.degraded_only,
        )
        for slot in record.slots
    ]
    site = SiteValues(
        saf4_gal=record.saf4_gal, rh_ceiling_pct=record.rh_ceiling_pct, climate_yield_min=record.climate_yield_min
    )
    return lines, slots, site


@pytest.mark.parametrize("name", ["empty", "seed", "full"])
def test_compile_reproduces_every_golden_vector(vectors: dict[str, dict], name: str) -> None:
    lines, slots, site = _specs_from_record(ProgramRecord.model_validate(vectors[name]["program"]))
    compiled = compile_program(lines, slots, site)
    assert compiled.record.hex() == vectors[name]["hex"]
    assert compiled.sha256 == vectors[name]["sha256"]
    assert compiled.slot_ids == tuple(slot.slot_id for slot in slots)
    assert compiled.not_on_controller == ()


def test_compile_is_deterministic_and_ignores_what_the_record_does_not_carry(vectors: dict[str, dict]) -> None:
    lines, slots, site = _specs_from_record(ProgramRecord.model_validate(vectors["full"]["program"]))
    reference = compile_program(lines, slots, site)
    rng = random.Random(1152)  # noqa: S311 - deterministic test data, not security
    for _ in range(5):
        shuffled_lines, shuffled_slots = lines[:], slots[:]
        rng.shuffle(shuffled_lines)
        rng.shuffle(shuffled_slots)
        assert compile_program(shuffled_lines, shuffled_slots, site) == reference
    # Labels, manual flags, reserved and the line manual flag are not in the record.
    relabeled = [slot.model_copy(update={"label": "x", "manual": True}) for slot in slots]
    remarked = [line.model_copy(update={"manual": True, "reserved": True}) for line in lines]
    assert compile_program(remarked, relabeled, site).sha256 == reference.sha256
    # Disabled schedules are left out, and are not "not on the controller".
    disabled = [slot.model_copy(update={"enabled": False, "slot_id": 1000 + n}) for n, slot in enumerate(slots[:5])]
    with_disabled = compile_program(_seed_lines(), _seed_slots() + disabled, site_values())
    assert with_disabled.sha256 == vectors["seed"]["sha256"]
    assert with_disabled.not_on_controller == ()


def test_compile_keeps_the_24_lowest_enabled_schedules() -> None:
    """Acceptance: with 25 enabled schedules slots 1-24 compile and the 25th is not on the controller."""
    slots = [
        _sched(slot_id, days=EVERY_DAY, start={"kind": "clock", "clock": f"{slot_id - 1:02d}:00"})
        for slot_id in range(1, 25)
    ]
    slots.append(_sched(25, days=EVERY_DAY, start={"kind": "clock", "clock": "23:30"}))
    random.Random(25).shuffle(slots)  # noqa: S311 - deterministic test data, not security
    compiled = compile_program(_seed_lines(), slots, site_values())
    assert compiled.slot_ids == tuple(range(1, 25))
    assert compiled.not_on_controller == (25,)
    record = irr.unpack_program(compiled.record)
    assert [slot.slot_id for slot in record.slots] == list(range(1, 25))
    # A disabled schedule takes no place: disable slot 3 and slot 25 goes on.
    slots = [slot.model_copy(update={"enabled": False}) if slot.slot_id == 3 else slot for slot in slots]
    compiled = compile_program(_seed_lines(), slots, site_values())
    assert compiled.slot_ids == (1, 2, *range(4, 26))
    assert compiled.not_on_controller == ()


def test_compile_leaves_out_a_row_the_controller_would_refuse() -> None:
    """A row written around validate_schedule (a mister pulse of 120 s) never blocks the program."""
    good = _seed_slots()
    bad = _sched(9, "L04", on_s=120)
    compiled = compile_program(_seed_lines(), [*good, bad], site_values())
    assert compiled.not_on_controller == (9,)
    assert compiled.slot_ids == (1, 2, 3)
    assert compiled.sha256 == compile_program(_seed_lines(), good, site_values()).sha256


def test_compile_header_lines_and_pauses() -> None:
    paused = datetime(2026, 11, 2, 14, 0, 0, 500_000, tzinfo=DENVER)  # a pause never ends early: rounded up
    lines = _seed_lines(
        L03={"enabled": True},
        L07={"enabled": True, "vpd_member": True, "schedule_member": True},
        L09={"schedule_member": True},
        L01={"paused_until": paused},
        L05={"paused_until": datetime(2200, 1, 1, tzinfo=UTC)},  # past the u32 range: held until 2106
    )
    site = SiteValues(saf4_gal=420, rh_ceiling_pct=85, climate_yield_min=20)
    record = irr.unpack_program(compile_program(lines, [], site).record)
    assert (record.saf4_gal, record.rh_ceiling_pct, record.climate_yield_min) == (420, 85, 20)
    assert record.enabled_mask == 0b1001101  # L01, L03, L04, L07
    assert record.vpd_member_mask == 0b011  # L04, L07
    assert record.schedule_member_mask == 0b110  # L07, L09
    assert record.lines[0].paused_until == int(paused.timestamp()) + 1
    assert record.lines[4].paused_until == 0xFFFF_FFFF
    assert record.lines[3] == irr.RecordLine(daily_cap_min=120, vpd_reserve_min=60)
    assert record.lines[0].vpd_reserve_min == 0  # null reserve
    assert record.slots == ()


def test_compile_rejects_incomplete_input() -> None:
    lines, slots, site = _seed_lines(), _seed_slots(), site_values()
    with pytest.raises(ValueError, match="missing L11"):
        compile_program(lines[:-1], slots, site)
    with pytest.raises(ValueError, match="twice"):
        compile_program([*lines, lines[0]], slots, site)
    with pytest.raises(ValueError, match="slot_id"):
        compile_program(lines, [*slots, _sched(None)], site)
    with pytest.raises(ValueError, match="slot_id twice"):
        compile_program(lines, [*slots, slots[0]], site)
    with pytest.raises(TypeError):
        compile_program(lines, slots, {"saf4_gal": 360})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        compile_program(_seed_line_rows(), slots, site)  # type: ignore[arg-type]


# ── Site values ──────────────────────────────────────────────────────────────


def test_site_values_take_the_newest_row_and_fall_back_to_the_registry() -> None:
    defaults = SiteValues(
        saf4_gal=int(REGISTRY["mister_daily_volume_max_gal"].default),
        rh_ceiling_pct=int(REGISTRY["sched_mist_rh_max_pct"].default),
        climate_yield_min=int(REGISTRY["climate_yield_min"].default),
    )
    assert (
        site_values()
        == site_values([])
        == defaults
        == SiteValues(saf4_gal=360, rh_ceiling_pct=90, climate_yield_min=15)
    )
    t0 = datetime(2026, 11, 2, 12, tzinfo=UTC)
    rows = [
        {"param": "mister_daily_volume_max_gal", "value": 400.0, "created_at": t0, "id": 1},
        {"param": "mister_daily_volume_max_gal", "value": 480.0, "created_at": t0 + timedelta(minutes=1), "id": 2},
        {"param": "mister_daily_volume_max_gal", "value": 300.0, "created_at": t0, "id": 3},
        {"param": "sched_mist_rh_max_pct", "value": 85.0, "created_at": t0, "id": 4},
        {"param": "mister_engage_kpa", "value": 1.2, "created_at": t0, "id": 5},
    ]
    assert site_values(rows) == SiteValues(saf4_gal=480, rh_ceiling_pct=85, climate_yield_min=15)
    # A released value (null) falls back to the default; so does a non-finite one.
    released = [
        {"param": "climate_yield_min", "value": None},
        {"param": "sched_mist_rh_max_pct", "value": float("nan")},
    ]
    assert site_values(released) == defaults
    # Values snap to the registry step, half to even, inside the registry bounds.
    snapped = site_values(
        [
            {"param": "mister_daily_volume_max_gal", "value": 355},
            {"param": "sched_mist_rh_max_pct", "value": 120},
            {"param": "climate_yield_min", "value": 7.5},
        ]
    )
    assert snapped == SiteValues(saf4_gal=360, rh_ceiling_pct=98, climate_yield_min=8)
    assert site_values([{"param": "mister_daily_volume_max_gal", "value": 345}]).saf4_gal == 340
    # Rows without created_at: the last one given wins.
    assert (
        site_values(
            [{"param": "climate_yield_min", "value": 10}, {"param": "climate_yield_min", "value": 12}]
        ).climate_yield_min
        == 12
    )


def test_site_values_bounds_match_the_registry_and_the_record() -> None:
    for param, field in irr.SITE_PARAM_FIELDS.items():
        spec = REGISTRY[param]
        bounds = SiteValues.model_fields[field].metadata
        lo = next(m.ge for m in bounds if hasattr(m, "ge"))
        hi = next(m.le for m in bounds if hasattr(m, "le"))
        assert (spec.min, spec.max) == (lo, hi)
        record_bounds = ProgramRecord.model_fields[field].metadata
        assert lo == next(m.ge for m in record_bounds if hasattr(m, "ge"))
        assert hi == next(m.le for m in record_bounds if hasattr(m, "le"))


# ── Submit validation ────────────────────────────────────────────────────────


def test_mister_pulse_of_120_s_is_out_of_range_in_a_schedule_and_a_run() -> None:
    """Acceptance: on_s 120 on a mister is out_of_range 10-90, in a run on L08 as in a schedule."""
    lines = _seed_lines()
    for line_id in ("L04", "L07", "L08", "L09", "L10"):
        refusal = validate_schedule(_sched(None, line_id, on_s=120), [], lines, today=MONDAY)
        assert refusal is not None
        assert (refusal.code, refusal.field, refusal.min, refusal.max, refusal.current) == (
            "out_of_range",
            "on_s",
            10,
            90,
            None,
        )
        assert not refusal.retryable
    by_id = {line.line_id: line for line in lines}
    args = run_args("fert_mister", {"on_s": 120, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240})
    refusal = validate_run(by_id["L08"], args)
    assert refusal is not None
    assert (refusal.code, refusal.field, refusal.min, refusal.max) == ("out_of_range", "on_s", 10, 90)
    assert validate_run(by_id["L07"], MisterRunArgs(on_s=120, count=4, gap_s=60)) == Refusal(
        code="out_of_range",
        field="on_s",
        min=10,
        max=90,
        detail="on_s must be 10-90 s for a run on a mister line (L07)",
    )


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        ({"days": ["mon"], "start": _clock("07:20")}, {"days": ["mon"], "start": _clock("07:00")}),
        ({"days": ["tue"], "start": _clock("00:10")}, {"days": ["mon"], "start": _clock("23:50")}),
        ({"days": ["mon"], "start": _clock("23:50")}, {"days": ["tue"], "start": _clock("00:10")}),
        ({"days": ["sun"], "start": _clock("23:45")}, {"days": ["mon"], "start": _clock("00:05")}),
        (
            {"days": EVERY_DAY, "start": {"kind": "sunrise", "offset_min": 0}},
            {"days": EVERY_DAY, "start": _clock("07:00")},
        ),
    ],
)
def test_two_starts_on_one_line_under_30_min_apart_are_too_close(ours: dict, theirs: dict) -> None:
    """Acceptance: two L01 schedules 20 min apart are too_close, Monday 23:50 against Tuesday 00:10 included."""
    lines = _seed_lines()
    existing = [_sched(7, **theirs)]
    refusal = validate_schedule(_sched(None, **ours), existing, lines, today=MONDAY)
    assert refusal is not None and refusal.code == "too_close"
    assert refusal.field == "start"
    assert "schedule 7 on L01" in refusal.detail and "30 min apart" in refusal.detail
    # An edit of another stored schedule is checked the same way.
    edited = validate_schedule(
        _sched(8, **ours), [*existing, _sched(8, days=["fri"], start=_clock("12:00"))], lines, today=MONDAY
    )
    assert edited is not None and edited.code == "too_close"


def test_the_too_close_detail_names_the_other_slot_and_both_starts() -> None:
    refusal = validate_schedule(
        _sched(None, days=["tue"], start=_clock("00:10")),
        [_sched(4, days=["mon"], start=_clock("23:50"))],
        _seed_lines(),
        today=MONDAY,
    )
    assert refusal is not None
    assert refusal.detail.startswith(
        "starts 20 min from schedule 4 on L01 (Tue 2026-11-03 00:10 and Mon 2026-11-02 23:50)"
    )


@pytest.mark.parametrize(
    ("ours", "theirs", "other_line"),
    [
        ({"start": _clock("07:30")}, {"start": _clock("07:00")}, "L01"),  # exactly 30 min apart
        ({"start": _clock("07:10")}, {"start": _clock("07:00"), "days": ["tue"]}, "L01"),  # other weekdays
        ({"start": _clock("07:10")}, {"start": _clock("07:00")}, "L05"),  # another line
        ({"start": _clock("07:10")}, {"start": _clock("07:00"), "enabled": False}, "L01"),  # disabled other
        ({"start": _clock("07:10"), "enabled": False}, {"start": _clock("07:00")}, "L01"),  # disabled candidate
        (
            {"days": EVERY_DAY, "start": {"kind": "sunrise", "offset_min": 0}},
            {"days": EVERY_DAY, "start": _clock("04:00")},
            "L01",
        ),
    ],
)
def test_starts_30_min_or_more_apart_are_accepted(ours: dict, theirs: dict, other_line: str) -> None:
    other = _sched(7, other_line, **theirs)
    assert validate_schedule(_sched(None, **ours), [other], _seed_lines(), today=MONDAY) is None


def test_an_edit_is_not_too_close_to_itself() -> None:
    stored = _sched(5, start=_clock("07:00"))
    moved = _sched(5, start=_clock("07:10"))
    assert validate_schedule(moved, [stored], _seed_lines(), today=MONDAY) is None


def test_the_25th_schedule_is_too_many() -> None:
    """Acceptance: a 25th schedule returns too_many_schedules with current and max."""
    lines = _seed_lines()
    stored = [_sched(slot_id, "L01", days=["sat"], start=_clock(f"{slot_id - 1:02d}:00")) for slot_id in range(1, 25)]
    refusal = validate_schedule(_sched(None, "L05"), stored, lines, today=MONDAY)
    assert refusal == Refusal(
        code="too_many_schedules",
        current=24,
        max=24,
        detail="24 schedules are stored and the controller holds at most 24",
    )
    assert validate_schedule(_sched(None, "L05"), stored[:23], lines, today=MONDAY) is None
    # Disabled schedules count too; an edit at 24 is fine.
    stored = [slot.model_copy(update={"enabled": False}) for slot in stored]
    assert validate_schedule(_sched(None, "L05"), stored, lines, today=MONDAY).code == "too_many_schedules"
    assert validate_schedule(_sched(3, "L05"), stored, lines, today=MONDAY) is None


def test_unknown_targets() -> None:
    lines = _seed_lines()
    refusal = validate_schedule(_sched(42), [_sched(1)], lines, today=MONDAY)
    assert (refusal.code, refusal.field) == ("unknown_target", "slot_id")
    assert "schedule 42 does not exist" in refusal.detail
    for line_id in ("L03", "L11"):
        refusal = validate_schedule(_sched(None, line_id), [], lines, today=MONDAY)
        assert (refusal.code, refusal.field) == ("unknown_target", "line_id")
        assert "takes no schedules" in refusal.detail
    refusal = validate_schedule(
        _sched(None, "L02"), [], [line for line in lines if line.line_id != "L02"], today=MONDAY
    )
    assert (refusal.code, refusal.field) == ("unknown_target", "line_id")
    with pytest.raises(ValidationError):
        _sched(None, "L12")


@pytest.mark.parametrize(
    ("line_id", "fields", "field", "lo", "hi"),
    [
        ("L01", {"on_s": 9}, "on_s", 10, 7200),
        ("L01", {"on_s": 7201}, "on_s", 10, 7200),
        ("L01", {"count": 2}, "count", 1, 1),
        ("L05", {"gap_s": 30}, "gap_s", 0, 0),
        ("L01", {"prewet_s": 60}, "prewet_s", 0, 0),
        ("L02", {"flush_s": 1801}, "flush_s", 0, 1800),
        ("L04", {"gap_s": 20}, "gap_s", 30, 600),
        ("L09", {"count": 61}, "count", 1, 60),
        ("L07", {"flush_s": 30}, "flush_s", 0, 0),
        ("L10", {"prewet_s": 1801}, "prewet_s", 0, 1800),
        ("L01", {"start": {"kind": "sunrise", "offset_min": 241}}, "start.offset_min", -240, 240),
        ("L04", {"start": {"kind": "sunset", "offset_min": -300}}, "start.offset_min", -240, 240),
        ("L01", {"start": {"kind": "clock", "minute": 1440}}, "start.clock", 0, 1439),
        ("L01", {"degraded_only": True}, "degraded_only", 0, 0),
        ("L08", {"degraded_only": True}, "degraded_only", 0, 0),
    ],
)
def test_out_of_range_names_the_field_and_its_bounds(line_id: str, fields: dict, field: str, lo: int, hi: int) -> None:
    refusal = validate_schedule(_sched(None, line_id, **fields), [], _seed_lines(), today=MONDAY)
    assert refusal is not None
    assert (refusal.code, refusal.field, refusal.min, refusal.max, refusal.current) == (
        "out_of_range",
        field,
        lo,
        hi,
        None,
    )
    # On an edit the stored value is current.
    stored = _sched(6, line_id)
    edit = validate_schedule(_sched(6, line_id, **fields), [stored], _seed_lines(), today=MONDAY)
    expected_current = {
        "start.offset_min": None,  # the stored start is a clock start
        "start.clock": "07:00",
        "degraded_only": False,
    }.get(field, getattr(stored, field, None))
    assert (edit.code, edit.field, edit.current) == ("out_of_range", field, expected_current)


def test_degraded_only_is_allowed_on_the_clean_misters() -> None:
    for line_id in ("L04", "L07", "L09"):
        assert validate_schedule(_sched(None, line_id, degraded_only=True), [], _seed_lines(), today=MONDAY) is None


def test_values_snap_to_whole_seconds_and_minutes() -> None:
    spec = _sched(None, "L04", on_s=45.5, gap_s=59.5, count=4.0, start={"kind": "sunrise", "offset_min": 15.4})
    assert (spec.on_s, spec.gap_s, spec.count, spec.start.minute) == (46, 60, 4, 15)
    assert _sched(None, on_s=44.5).on_s == 44  # half to even
    assert Start(kind="solar_noon", minute=-0.4).minute == 0
    assert validate_schedule(_sched(None, "L04", on_s=90.4), [], _seed_lines(), today=MONDAY) is None
    refusal = validate_schedule(_sched(None, "L04", on_s=90.6), [], _seed_lines(), today=MONDAY)
    assert (refusal.code, refusal.field) == ("out_of_range", "on_s")
    for bad in (float("nan"), float("inf"), True, "lots"):
        with pytest.raises(ValidationError):
            _sched(None, on_s=bad)


def test_what_settles_at_run_time_is_accepted() -> None:
    """A schedule on a disabled line, mist with Schedule off, and a run past the cap are stored."""
    lines = _seed_lines()
    assert validate_schedule(_sched(None, "L07"), [], lines, today=MONDAY) is None  # L07 disabled
    assert validate_schedule(_sched(None, "L04"), [], lines, today=MONDAY) is None  # L04 schedule_member off
    assert validate_schedule(_sched(None, "L01", on_s=7200), [], lines, today=MONDAY) is None  # 120 min, cap 60
    assert validate_schedule(_sched(None, "L05"), [], lines, today=MONDAY) is None  # reserved and disabled
    paused = _seed_lines(L01={"paused_until": datetime(2027, 1, 1, tzinfo=UTC)})
    assert validate_schedule(_sched(None, "L01"), [], paused, today=MONDAY) is None


def test_validate_line_caps() -> None:
    stored = _seed_lines()[0]  # L01, cap 60
    assert validate_line(stored) is None
    assert validate_line(LineSpec(line_id="L01", daily_cap_min=480)) is None
    refusal = validate_line(LineSpec(line_id="L01", daily_cap_min=481), before=stored)
    assert (refusal.code, refusal.field, refusal.min, refusal.max, refusal.current) == (
        "out_of_range",
        "daily_cap_min",
        0,
        480,
        60,
    )
    refusal = validate_line(LineSpec(line_id="L04", daily_cap_min=45, vpd_reserve_min=60))
    assert (refusal.code, refusal.field, refusal.min, refusal.max) == ("out_of_range", "vpd_reserve_min", 0, 45)
    assert validate_line(LineSpec(line_id="L04", daily_cap_min=45, vpd_reserve_min=45)) is None
    assert validate_line(LineSpec(line_id="L04", daily_cap_min=0, vpd_reserve_min=None)) is None


# ── Run-now arguments ────────────────────────────────────────────────────────


def test_run_args_take_exactly_their_kinds_fields() -> None:
    assert run_args("drip", {"minutes": 5, "reason": "flush the line"}) == DripRunArgs(minutes=5)
    assert run_args("fert_drip", {"minutes": 5, "prewet_s": 120, "flush_s": 240}) == FertDripRunArgs(
        minutes=5, prewet_s=120, flush_s=240
    )
    assert run_args("mister", {"on_s": 30, "count": 4, "gap_s": 60}) == MisterRunArgs(on_s=30, count=4, gap_s=60)
    l08 = run_args("fert_mister", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240})
    assert l08 == FertMisterRunArgs(on_s=30, count=4, gap_s=60, prewet_s=120, flush_s=240)
    assert l08.model_dump() == {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240}
    with pytest.raises(ValidationError) as extra:
        run_args("fert_mister", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240, "minutes": 5})
    assert extra.value.errors()[0]["loc"] == ("minutes",)
    with pytest.raises(ValidationError) as missing:
        run_args("fert_drip", {"minutes": 5, "prewet_s": 120})
    assert missing.value.errors()[0]["loc"] == ("flush_s",)
    with pytest.raises(ValidationError):
        run_args("drip", {"on_s": 30})
    for kind in ("master", "fog"):
        with pytest.raises(ValueError, match="takes no runs"):
            run_args(kind, {"minutes": 5})
    assert run_args("drip", {"minutes": 4.5}).minutes == 4


def test_validate_run_bounds_per_kind() -> None:
    by_id = {line.line_id: line for line in _seed_lines()}
    assert validate_run(by_id["L01"], DripRunArgs(minutes=120)) is None
    refusal = validate_run(by_id["L01"], DripRunArgs(minutes=121))
    assert (refusal.code, refusal.field, refusal.min, refusal.max) == ("out_of_range", "minutes", 1, 120)
    refusal = validate_run(by_id["L02"], FertDripRunArgs(minutes=5, prewet_s=1801, flush_s=0))
    assert (refusal.field, refusal.min, refusal.max) == ("prewet_s", 0, 1800)
    refusal = validate_run(by_id["L04"], MisterRunArgs(on_s=45, count=4, gap_s=20))
    assert (refusal.field, refusal.min, refusal.max) == ("gap_s", 30, 600)
    assert validate_run(by_id["L08"], FertMisterRunArgs(on_s=30, count=4, gap_s=60, prewet_s=120, flush_s=240)) is None
    for line_id in ("L03", "L11"):
        refusal = validate_run(by_id[line_id], DripRunArgs(minutes=5))
        assert (refusal.code, refusal.field) == ("unknown_target", "line_id")
    with pytest.raises(TypeError):
        validate_run(by_id["L08"], MisterRunArgs(on_s=30, count=4, gap_s=60))


# ── Start, days and rows ─────────────────────────────────────────────────────


def test_start_maps_the_openapi_schedule_start() -> None:
    assert Start.from_api({"kind": "clock", "clock": "07:30"}) == Start(kind="clock", minute=450)
    assert Start.from_api({"kind": "clock", "clock": "00:00", "offset_min": 0}) == Start(kind="clock", minute=0)
    assert Start.from_api({"kind": "sunrise", "offset_min": -15}) == Start(kind="sunrise", minute=-15)
    assert Start.from_api({"kind": "sunset"}) == Start(kind="sunset", minute=0)
    assert Start.from_api({"kind": "solar_noon", "offset_min": 300}).minute == 300  # refused on submit
    for start in (
        Start(kind="clock", minute=450),
        Start(kind="clock", minute=1439),
        Start(kind="sunrise", minute=60),
        Start(kind="solar_noon", minute=-120),
        Start(kind="sunset", minute=0),
    ):
        assert Start.from_api(start.to_api()) == start
    assert Start(kind="clock", minute=450).to_api() == {"kind": "clock", "clock": "07:30"}
    assert Start(kind="sunrise", minute=60).to_api() == {"kind": "sunrise", "offset_min": 60}
    for bad in (
        {"kind": "clock", "clock": "7:30"},
        {"kind": "clock", "clock": "24:00"},
        {"kind": "clock"},
        {"kind": "clock", "clock": "07:30", "offset_min": 15},
        {"kind": "sunrise", "clock": "07:30"},
        {"kind": "dusk", "offset_min": 0},
        {"kind": "clock", "clock": "07:30", "minute": 3},
    ):
        with pytest.raises(ValueError):
            Start.from_api(bad)
        with pytest.raises(ValidationError):  # the same shapes inside a schedule
            _sched(None, start=bad)
    assert Start(kind="clock", minute=450).start_kind == irr.START_KIND["clock"]
    # A clock start without its time is refused, never read as midnight; a bare solar kind is offset 0.
    with pytest.raises(ValidationError):
        Start.model_validate({"kind": "clock"})
    assert _sched(None, start={"kind": "sunset"}).start == Start(kind="sunset", minute=0)
    assert _sched(None, start={"kind": "clock", "minute": 450}).start == Start(kind="clock", minute=450)  # row shape


def test_days_map_the_openapi_schedule_days() -> None:
    assert irr.weekday_mask(["mon"]) == 1 and irr.weekday_mask(["sun"]) == 64
    assert irr.weekday_mask(EVERY_DAY) == 127
    assert irr.days_from_mask(0b1010101) == frozenset({"mon", "wed", "fri", "sun"})
    assert irr.days_to_api({"sun", "wed", "mon"}) == ["mon", "wed", "sun"]
    for mask in range(1, 128):
        assert irr.weekday_mask(irr.days_from_mask(mask)) == mask
    for bad in (0, 128, True):
        with pytest.raises(ValueError):
            irr.days_from_mask(bad)
    with pytest.raises(ValueError):
        irr.weekday_mask(["monday"])
    spec = _sched(None, days=["sun", "mon", "mon"])
    assert spec.days == frozenset({"mon", "sun"}) and spec.weekday_mask == 0b1000001
    assert _sched(None, days=0b0011111).days == frozenset({"mon", "tue", "wed", "thu", "fri"})
    for bad in ([], ["funday"], 0):
        with pytest.raises(ValidationError):
            _sched(None, days=bad)


def test_schedule_spec_round_trips_the_openapi_input() -> None:
    body = {
        "line_id": "L02",
        "label": "Sunday feed",
        "enabled": True,
        "days": ["sun"],
        "start": {"kind": "sunrise", "offset_min": 60},
        "on_s": 300,
        "count": 1,
        "gap_s": 0,
        "prewet_s": 120,
        "flush_s": 240,
        "degraded_only": False,
        "manual": True,
    }
    spec = ScheduleSpec(**body)
    assert spec.to_api() == body
    stored = spec.model_copy(update={"slot_id": 12})
    assert stored.to_api() == {"slot_id": 12} | body
    assert "label" not in _sched(None).to_api()
    assert (spec.open_s, spec.minutes) == (300, 5.0)
    assert _sched(None, "L04", on_s=45, count=4).minutes == 3.0
    with pytest.raises(ValidationError):
        ScheduleSpec(**body, reason="not a schedule field")
    with pytest.raises(ValidationError):
        ScheduleSpec(**(body | {"label": "x" * 61}))


def test_edits_apply_through_validation() -> None:
    """An edit carries only its changed fields; with_changes snaps and validates the merged spec."""
    stored = _sched(5, "L04", start=_clock("07:00"), label="dawn")
    edited = stored.with_changes({"on_s": 45.6, "start": {"kind": "clock", "clock": "08:15"}, "days": ["tue", "sat"]})
    assert (edited.slot_id, edited.on_s, edited.start, edited.days, edited.label) == (
        5,
        46,
        Start(kind="clock", minute=495),
        frozenset({"tue", "sat"}),
        "dawn",
    )
    assert stored.with_changes({}) == stored
    with pytest.raises(ValidationError):
        stored.with_changes({"reason": "not a schedule field"})
    with pytest.raises(ValidationError):
        stored.with_changes({"days": []})
    line = _seed_lines()[0]
    assert line.with_changes({"daily_cap_min": 90.4}).daily_cap_min == 90
    assert line.with_changes({"paused_until": "2026-11-03T08:00:00-07:00"}).paused_until == datetime(
        2026, 11, 3, 15, 0, tzinfo=UTC
    )
    with pytest.raises(ValidationError):
        line.with_changes({"vpd_member": True})


def test_rows_build_specs_and_line_topology_is_fixed() -> None:
    lines = _seed_lines()
    assert [line.line_id for line in lines] == list(irr.LINE_IDS)
    assert [line.kind for line in lines] == [LINE_KIND[line_id] for line_id in irr.LINE_IDS]
    assert [line.device_idx for line in lines] == list(range(11))
    assert LineSpec(line_id="L08") == LineSpec(line_id="L08", kind="fert_mister", device_idx=7, clean_partner="L07")
    for bad in (
        {"line_id": "L01", "kind": "mister"},
        {"line_id": "L01", "device_idx": 3},
        {"line_id": "L02", "clean_partner": None},
        {"line_id": "L01", "vpd_member": True},
        {"line_id": "L08", "schedule_member": True},
        {"line_id": "L01", "colour": "blue"},
    ):
        with pytest.raises(ValidationError):
            LineSpec(**bad)
    assert LineSpec(line_id="L01", vpd_member=False).vpd_member is False
    with pytest.raises(ValidationError):
        LineSpec(line_id="L01", paused_until=datetime(2026, 11, 2, 12))  # naive
    l04 = lines[3]
    assert (l04.schedule_budget_min, lines[0].schedule_budget_min, lines[1].schedule_budget_min) == (60, 60, None)
    slot = _seed_slots()[0]
    assert slot == ScheduleSpec(
        slot_id=1,
        line_id="L04",
        days=EVERY_DAY,
        start={"kind": "solar_noon", "offset_min": -120},
        on_s=45,
        count=4,
        gap_s=60,
        degraded_only=True,
        label="vanda-degraded",
    )


# ── Local time and solar starts ──────────────────────────────────────────────


@pytest.mark.parametrize(("day", "offset"), [(date(2026, 3, 8), -360), (date(2026, 11, 1), -420)])
def test_agenda_solar_starts_use_the_dates_noon_offset(day: date, offset: int) -> None:
    """Acceptance: offset −360 on 2026-03-08 and −420 on 2026-11-01, matching compute_solar_times."""
    assert irr.utc_offset_min(day) == offset
    expected = compute_solar_times(day.timetuple().tm_yday, day.year, utc_offset_min=offset)
    weekday = irr.WEEKDAYS[day.weekday()]
    kinds = {
        10: ("sunrise", expected.sunrise_min),
        11: ("solar_noon", expected.solar_noon_min),
        12: ("sunset", expected.sunset_min),
    }
    slots = [
        _sched(slot_id, days=[weekday], start={"kind": kind, "offset_min": 15}) for slot_id, (kind, _) in kinds.items()
    ]
    week = agenda(_seed_lines(), slots, today=day, days=1)
    assert [occ.slot_id for occ in week.occurrences] == [10, 11, 12]
    for occ in week.occurrences:
        minute = kinds[occ.slot_id][1] + 15
        assert occ.planned_start == datetime.combine(day, time(minute // 60, minute % 60), tzinfo=DENVER)
        assert occ.planned_start.utcoffset() == timedelta(minutes=offset)
        assert occ.local_day == day
    (solar,) = week.solar
    assert solar.date == day
    for attr, minute in (
        ("sunrise", expected.sunrise_min),
        ("solar_noon", expected.solar_noon_min),
        ("sunset", expected.sunset_min),
    ):
        moment = getattr(solar, attr)
        assert (moment.hour * 60 + moment.minute, moment.utcoffset()) == (minute, timedelta(minutes=offset))
    assert irr.solar_times(day) == expected


def test_local_start_follows_the_wall_clock_across_dst() -> None:
    # Spring forward: 02:30 never shows on the clock; it runs when the clock jumps to 03:00 MDT.
    assert local_start(date(2026, 3, 8), 150) == datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
    assert local_start(date(2026, 3, 8), 179) == datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
    assert local_start(date(2026, 3, 8), 180) == datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
    assert local_start(date(2026, 3, 8), 119) == datetime(2026, 3, 8, 8, 59, tzinfo=UTC)
    # Fall back: 01:30 shows twice; it runs the first time (MDT).
    assert local_start(date(2026, 11, 1), 90) == datetime(2026, 11, 1, 7, 30, tzinfo=UTC)
    assert local_start(date(2026, 11, 1), 180) == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    # Past midnight (sunset + 240 in June) is early the next morning; below zero is the evening before.
    assert local_start(date(2026, 6, 21), 1472) == datetime(2026, 6, 22, 0, 32, tzinfo=DENVER)
    assert local_start(date(2026, 6, 21), -30) == datetime(2026, 6, 20, 23, 30, tzinfo=DENVER)
    assert local_start(MONDAY, 450) == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_a_sunset_start_past_midnight_belongs_to_its_day() -> None:
    day = date(2026, 6, 21)  # Sunday
    slot = _sched(5, days=["sun"], start={"kind": "sunset", "offset_min": 240})
    week = agenda(_seed_lines(), [slot], today=day, days=1)
    (occ,) = week.occurrences
    assert occ.local_day == day
    assert occ.planned_start.date() == date(2026, 6, 22)
    sunset = irr.solar_times(day).sunset_min
    assert occ.planned_start == local_start(day, sunset + 240)


# ── Agenda ───────────────────────────────────────────────────────────────────


def test_agenda_marks_a_disabled_lines_occurrences_skipped() -> None:
    """Acceptance: a schedule on a disabled line is accepted and its occurrences read 'line disabled'."""
    lines = _seed_lines()
    candidate = _sched(None, "L07", days=EVERY_DAY, start={"kind": "sunrise", "offset_min": 30})
    assert validate_schedule(candidate, _seed_slots(), lines, today=MONDAY) is None
    stored = candidate.model_copy(update={"slot_id": 4})
    week = agenda(lines, [*_seed_slots(), stored], today=MONDAY)
    l07 = [occ for occ in week.occurrences if occ.line_id == "L07"]
    assert len(l07) == 7
    assert {(occ.status, occ.reason, occ.reason_code) for occ in l07} == {("skipped", "line disabled", "dis")}
    assert irr.REASON_WORDS["dis"] == "line disabled"


def test_agenda_skip_reasons_follow_the_flags_and_the_pause() -> None:
    pause_end = datetime(2026, 11, 3, 8, 0, tzinfo=DENVER)
    lines = _seed_lines(
        L01={"paused_until": pause_end},
        L07={"enabled": True, "vpd_member": True, "schedule_member": False},
        L09={"enabled": True, "vpd_member": False, "schedule_member": True},
        L08={"enabled": True},
    )
    slots = [
        _sched(1, "L01", days=EVERY_DAY, start=_clock("07:00")),
        _sched(2, "L07", days=["mon"]),  # mist schedule, Schedule off
        _sched(3, "L07", days=["mon"], start=_clock("12:00"), degraded_only=True),  # follows vpd_member: on
        _sched(4, "L09", days=["mon"], start=_clock("09:00")),  # Schedule on
        _sched(5, "L09", days=["mon"], start=_clock("13:00"), degraded_only=True),  # vpd_member off
        _sched(6, "L08", days=["mon"], start=_clock("15:00")),  # fertilizer mister: enabled is enough
        _sched(7, "L05", days=["mon"], start=_clock("16:00")),  # disabled beats everything
    ]
    week = agenda(lines, slots, today=MONDAY, days=3)
    got = {(occ.slot_id, occ.local_day): (occ.status, occ.reason_code, occ.reason) for occ in week.occurrences}
    assert got[(1, MONDAY)] == ("skipped", "pau", "paused")
    assert got[(1, date(2026, 11, 3))] == ("skipped", "pau", "paused")  # 07:00 is before 08:00
    assert got[(1, date(2026, 11, 4))] == ("planned", None, None)
    assert got[(2, MONDAY)] == ("skipped", "nsm", "not a schedule member")
    assert got[(3, MONDAY)] == ("planned", None, "only while VPD sensing is degraded")
    assert got[(4, MONDAY)] == ("planned", None, None)
    assert got[(5, MONDAY)] == ("skipped", "nvm", "not a VPD member")
    assert got[(6, MONDAY)] == ("planned", None, None)
    assert got[(7, MONDAY)] == ("skipped", "dis", "line disabled")
    for code in ("pau", "nsm", "nvm", "dis"):
        assert code in irr.REASON_WORDS


def test_agenda_notes_the_daily_cap() -> None:
    lines = _seed_lines(
        L07={"enabled": True, "schedule_member": True},  # cap 45, reserve 15: 30 min for schedules
        L02={"enabled": True},  # no cap
    )
    slots = [
        _sched(1, "L01", start=_clock("06:00"), on_s=2400),  # 40 min
        _sched(2, "L01", start=_clock("12:00"), on_s=1200),  # 60 min: at the cap, not past it
        _sched(3, "L01", start=_clock("18:00"), on_s=60),  # 61 min: past the 60 min cap
        _sched(4, "L07", start=_clock("06:00"), on_s=60, count=20),  # 20 min
        _sched(5, "L07", start=_clock("07:00"), on_s=60, count=15),  # 35 min: past 45 - 15
        _sched(6, "L02", start=_clock("09:00"), on_s=7200),  # no cap; no prewet or flush on L01
        _sched(7, "L01", days=["tue"], start=_clock("06:00"), on_s=600),  # a new day starts at 0
    ]
    week = agenda(lines, slots, today=MONDAY, days=2)
    reasons = {occ.slot_id: occ.reason for occ in week.occurrences}
    assert reasons == {
        1: None,
        2: None,
        3: "ends at the daily cap",
        4: None,
        5: "ends at the daily cap",
        6: None,
        7: None,
    }
    assert all(occ.status == "planned" for occ in week.occurrences)
    # Skipped occurrences use none of the cap.
    paused = _seed_lines(L01={"paused_until": datetime(2026, 11, 2, 9, 0, tzinfo=DENVER)})
    week = agenda(paused, slots[:3], today=MONDAY, days=1)
    assert [(occ.status, occ.reason) for occ in week.occurrences] == [
        ("skipped", "paused"),
        ("planned", None),
        ("planned", None),
    ]
    # A zone whose whole cap is reserve leaves schedules nothing.
    reserved = _seed_lines(L07={"enabled": True, "schedule_member": True, "vpd_reserve_min": 45})
    (occ,) = agenda(reserved, [slots[3]], today=MONDAY, days=1).occurrences
    assert occ.reason == "ends at the daily cap"
    degraded = _seed_lines(L04={"vpd_reserve_min": 119})  # 1 min left for schedules
    notes = [occ.reason for occ in agenda(degraded, _seed_slots(), today=MONDAY, days=1).occurrences]
    assert notes == ["only while VPD sensing is degraded; ends at the daily cap"] * 3


def test_agenda_cap_counts_what_the_controllers_cap_counts() -> None:
    """irrigation §4.5: a drip cap counts the prewet and flush a feed runs on it, and a mister zone's
    duty counts every pulse on the zone, its fertilizer mister's feed included."""
    lines = _seed_lines(
        L02={"enabled": True},
        L07={"enabled": True, "schedule_member": True},  # cap 45, reserve 15: 30 min for schedules
        L08={"enabled": True},
    )
    feed = _sched(2, "L02", start=_clock("09:00"), on_s=300, prewet_s=120, flush_s=240)  # 6 min on L01
    at_cap = [
        _sched(1, "L01", start=_clock("06:00"), on_s=3240),
        feed,
        _sched(3, "L01", start=_clock("12:00"), on_s=60),
    ]
    reasons = {occ.slot_id: occ.reason for occ in agenda(lines, at_cap, today=MONDAY, days=1).occurrences}
    assert reasons == {1: None, 2: None, 3: "ends at the daily cap"}  # 54 + 6 = 60, then 61
    # The feed's own rinse takes L01 past its cap: the feed is the run that reaches it.
    past_cap = [at_cap[0].model_copy(update={"on_s": 3360}), feed]
    reasons = {occ.slot_id: occ.reason for occ in agenda(lines, past_cap, today=MONDAY, days=1).occurrences}
    assert reasons == {1: None, 2: "ends at the daily cap"}
    # A feed with no prewet or flush puts nothing on L01, even after L01 has passed its cap.
    over = [_sched(1, "L01", start=_clock("06:00"), on_s=3700), feed.model_copy(update={"prewet_s": 0, "flush_s": 0})]
    reasons = {occ.slot_id: occ.reason for occ in agenda(lines, over, today=MONDAY, days=1).occurrences}
    assert reasons == {1: "ends at the daily cap", 2: None}
    # South zone: 20 min on L07, then an L08 feed of 5 min with 5 min of prewet and flush on L07 (30 min),
    # then one more L07 pulse (31 min).
    south = [
        _sched(4, "L07", start=_clock("06:00"), on_s=60, count=20),
        _sched(5, "L08", start=_clock("08:00"), on_s=60, count=5, gap_s=60, prewet_s=120, flush_s=180),
        _sched(6, "L07", start=_clock("12:00"), on_s=60, count=1),
    ]
    reasons = {occ.slot_id: occ.reason for occ in agenda(lines, south, today=MONDAY, days=1).occurrences}
    assert reasons == {4: None, 5: None, 6: "ends at the daily cap"}
    # A VPD reserve holds back mister minutes only; on a drip it changes nothing.
    assert LineSpec(line_id="L01", daily_cap_min=60, vpd_reserve_min=30).schedule_budget_min == 60
    assert LineSpec(line_id="L07", daily_cap_min=45, vpd_reserve_min=15).schedule_budget_min == 30


def test_agenda_window_order_and_shape() -> None:
    lines = _seed_lines(L09={"enabled": True, "schedule_member": True})
    slots = [
        *_seed_slots(),
        _sched(4, "L01", days=["mon", "wed"], start=_clock("05:00")),
        _sched(5, "L09", days=EVERY_DAY),
    ]
    week = agenda(lines, slots, today=MONDAY)
    assert isinstance(week, Agenda)
    assert (week.first_day, week.last_day) == (MONDAY, MONDAY + timedelta(days=6))
    assert [day.date for day in week.solar] == [MONDAY + timedelta(days=n) for n in range(7)]
    stamps = [occ.planned_start.timestamp() for occ in week.occurrences]
    assert stamps == sorted(stamps)
    assert len(week.occurrences) == 3 * 7 + 2 + 7
    assert all(occ.planned_start.tzinfo is not None for occ in week.occurrences)
    l01 = [occ for occ in week.occurrences if occ.slot_id == 4]
    assert [occ.local_day for occ in l01] == [MONDAY, MONDAY + timedelta(days=2)]
    assert (l01[0].minutes, l01[0].pulses) == (10.0, None)
    l09 = next(occ for occ in week.occurrences if occ.slot_id == 5)
    assert (l09.minutes, l09.pulses) == (3.0, 4)
    past = agenda(lines, slots, today=MONDAY, days=1, past_days=7)
    assert (past.first_day, past.last_day) == (MONDAY - timedelta(days=7), MONDAY)
    assert len(past.solar) == 8
    disabled = [slot.model_copy(update={"enabled": False}) for slot in slots]
    assert agenda(lines, disabled, today=MONDAY).occurrences == ()
    for kwargs in ({"days": 0}, {"days": 8}, {"past_days": -1}, {"past_days": 8}, {"days": True}):
        with pytest.raises(ValueError):
            agenda(lines, slots, today=MONDAY, **kwargs)
    with pytest.raises(TypeError):
        agenda(lines, slots, today=datetime(2026, 11, 2, 8, tzinfo=UTC))
    with pytest.raises(ValueError, match="not in lines"):
        agenda(lines[:3], slots, today=MONDAY)
    with pytest.raises(ValueError, match="slot_id"):
        agenda(lines, [_sched(None)], today=MONDAY)


def test_agenda_serializes_to_the_openapi_occurrence_fields() -> None:
    week = agenda(_seed_lines(), _seed_slots(), today=MONDAY, days=1)
    first = week.occurrences[0].model_dump(mode="json")
    assert first == {
        "slot_id": 1,
        "line_id": "L04",
        "local_day": "2026-11-02",
        "planned_start": "2026-11-02T09:44:00-07:00",
        "minutes": 3.0,
        "pulses": 4,
        "status": "planned",
        "reason": "only while VPD sensing is degraded",
        "reason_code": None,
    }
    assert week.solar[0].model_dump(mode="json") == {
        "date": "2026-11-02",
        "sunrise": "2026-11-02T06:30:00-07:00",
        "solar_noon": "2026-11-02T11:44:00-07:00",
        "sunset": "2026-11-02T16:58:00-07:00",
    }


# ── Module boundaries ────────────────────────────────────────────────────────


def _imported_modules(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, "no relative imports"
            imported.add(node.module)
    return imported


def test_solar_moved_into_verdify_schemas_and_imports_nothing_from_ingestor() -> None:
    solar_imports = _imported_modules(REPO_ROOT / "verdify_schemas" / "solar.py")
    assert solar_imports <= {"__future__", "math", "dataclasses"}
    # No verdify_schemas module imports from ingestor/, nor the ingestor's bare-name ``solar``.
    for path in sorted((REPO_ROOT / "verdify_schemas").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in {"ingestor", "solar"}, (path.name, name)
    assert (schemas_solar.GREENHOUSE_LAT_DEG, schemas_solar.GREENHOUSE_LON_DEG, schemas_solar._ZENITH_DEG) == (
        40.167,
        -105.102,
        90.833,
    )


def test_importing_the_module_needs_no_time_zone_data() -> None:
    """Every verdify_schemas consumer imports irrigation.py (the package re-exports it), so the
    America/Denver zone is resolved on first use, never at import."""
    tree = ast.parse((REPO_ROOT / "verdify_schemas" / "irrigation.py").read_text())
    top_level = [node for node in tree.body if not isinstance(node, ast.FunctionDef | ast.ClassDef)]
    calls = [node for stmt in top_level for node in ast.walk(stmt) if isinstance(node, ast.Call)]
    assert not [call for call in calls if isinstance(call.func, ast.Name) and call.func.id == "ZoneInfo"]
    assert irr.local_tz() is ZoneInfo(irr.LOCAL_TZ_NAME) is DENVER


def test_ingestor_solar_reexports_the_moved_ephemeris(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(REPO_ROOT / "ingestor"))
    monkeypatch.delitem(sys.modules, "solar", raising=False)
    import solar  # noqa: PLC0415 - the ingestor's bare-name module

    assert Path(solar.__file__).resolve() == REPO_ROOT / "ingestor" / "solar.py"
    for name in (
        "GREENHOUSE_LAT_DEG",
        "GREENHOUSE_LON_DEG",
        "_ZENITH_DEG",
        "SolarTimes",
        "_days_in_year",
        "compute_solar_times",
    ):
        assert getattr(solar, name) is getattr(schemas_solar, name), name
    for name in ("BandAnchors", "solar_phase", "band_value_at_phase"):
        assert callable(getattr(solar, name))
        assert not hasattr(schemas_solar, name), name
    times = solar.compute_solar_times(172, 2026, utc_offset_min=-360)
    assert 0.0 <= solar.solar_phase(times.solar_noon_min, times) < 4.0


def test_ingestor_solar_imports_with_only_the_ingestor_dir_on_the_path(tmp_path: Path) -> None:
    """``ingestor/solar.py`` stays importable as before: a caller that adds only ``ingestor/`` to
    ``sys.path`` (research/planner-efficacy/sep4_fixed_panel_counterfactual.py does) still gets it."""
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(REPO_ROOT / 'ingestor')!r}); "
        "import solar; "
        "print(solar.compute_solar_times(172, 2026, utc_offset_min=-360).sunrise_min)"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) == compute_solar_times(172, 2026, utc_offset_min=-360).sunrise_min


SCHEDULE_REEXPORTED = (
    "BOUNDS_BY_KIND",
    "LINE_BOUNDS",
    "MAX_SCHEDULES",
    "MIN_SPACING_MIN",
    "RUN_BOUNDS_BY_KIND",
    "Agenda",
    "AgendaOccurrence",
    "CompiledProgram",
    "DripRunArgs",
    "FertDripRunArgs",
    "FertMisterRunArgs",
    "LineSpec",
    "MisterRunArgs",
    "Refusal",
    "ScheduleSpec",
    "SiteValues",
    "SolarDay",
    "Start",
    "agenda",
    "bounds_by_kind",
    "compile_program",
    "run_args",
    "site_values",
    "validate_line",
    "validate_run",
    "validate_schedule",
)


def test_package_reexports_the_schedule_model() -> None:
    import verdify_schemas  # noqa: PLC0415

    for name in SCHEDULE_REEXPORTED:
        assert getattr(verdify_schemas, name) is getattr(irr, name), name
        assert name in verdify_schemas.__all__ and name in irr.__all__, name
    assert set(irr.__all__) <= set(dir(irr))
