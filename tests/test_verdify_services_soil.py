"""#1345 (migration 303): the soil-target service's pure parts, with no database. The grants, the order CHECK and the
runtime logins' edits are qualified natively (db/qualification/303-soil-targets-fixture.sql)."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verdify_schemas.activity import ACTIVITY_ACTION_PATTERN
from verdify_schemas.soil import SoilTargetPatch, SoilTargetRow, order_problem
from verdify_services import soil
from verdify_services.problems import ServiceError

ROW = {
    "zone": "west",
    "wilt_pct": 18.0,
    "min_pct": 35.0,
    "max_pct": 55.0,
    "saturation_pct": 75.0,
    "notes": None,
    "probe_column": "soil_moisture_west",
    "sensor_id": "soil.west",
    "occupancy_zone": "west",
    "updated_at": None,
    "updated_by": None,
    "updated_source": None,
    "reason": None,
}


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *args):
        assert "FROM public.soil_moisture_targets" in sql and args == ("vallery",)
        return self.rows


def test_order_is_strict_and_bounded():
    assert order_problem({"wilt_pct": 18, "min_pct": 35, "max_pct": 55, "saturation_pct": 75}) is None
    assert order_problem({"wilt_pct": 0, "min_pct": 1, "max_pct": 2, "saturation_pct": 100}) is None
    for bad in ((35, 35, 55, 75), (18, 55, 35, 75), (18, 35, 55, 100.5), (-1, 35, 55, 75)):
        assert order_problem(dict(zip(("wilt_pct", "min_pct", "max_pct", "saturation_pct"), bad, strict=True)))
    with pytest.raises(ValidationError):
        SoilTargetRow.model_validate({**ROW, "wilt_pct": 40.0})


def test_a_patch_takes_numbers_only_and_at_least_one_field():
    assert SoilTargetPatch.model_validate({"wilt_pct": 20}).wilt_pct == 20.0
    for body in ({}, {"wilt_pct": "20"}, {"wilt_pct": True}, {"wilt_pct": 101.0}, {"crop": "lily"}):
        with pytest.raises(ValidationError):
            SoilTargetPatch.model_validate(body)


def test_a_bad_body_is_a_service_problem_naming_its_field():
    with pytest.raises(ServiceError) as raised:
        soil._parsed({"min_pct": 120.0})
    assert raised.value.problem.code == "out_of_range" and raised.value.problem.field == "min_pct"
    with pytest.raises(ServiceError) as raised:
        soil._parsed({"notes": 3})
    assert raised.value.problem.code == "invalid_request"


def test_targets_serve_the_probe_and_who_changed_it_and_no_crop():
    changed = {
        **ROW,
        "updated_at": datetime(2026, 11, 6, tzinfo=UTC),
        "updated_by": "jason",
        "updated_source": "dashboard",
    }
    result = asyncio.run(soil.targets(FakeConn([ROW, changed])))
    first, second = result.targets
    assert first.probe == "soil_moisture_west" and first.updated_by is None
    assert second.updated_by is not None and second.updated_by.name == "jason"
    assert "crop" not in first.model_dump()


def test_the_write_action_is_a_known_activity_action():
    import re

    assert re.fullmatch(ACTIVITY_ACTION_PATTERN, "soil_target.write")
