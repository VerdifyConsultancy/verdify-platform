"""Bounded route-only public reader; physical crop proof remains separate."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import timedelta
from pathlib import Path

import asyncpg

from verdify_schemas.physical_crop_band import RouteOnlyCropBandEvidence
from verdify_schemas.route_only_crop_band_reader import (
    parse_route_only_crop_band_row,
    read_route_only_crop_band_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("route_fixture", ROOT / "tests/test_route_only_crop_band.py")
assert spec and spec.loader
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
DAY, END = fixture_module.DAY, fixture_module.END


def row():
    diagnostic = fixture_module.route.build(*fixture_module.fixture()).model_dump(mode="json")
    return {
        "day": DAY,
        "greenhouse_id": "vallery",
        "served_at": END + timedelta(hours=1),
        "revision_id": 7,
        "recorded_at": END + timedelta(minutes=1),
        "unavailable_reason": None,
        "diagnostic": diagnostic,
    }


def test_reader_keeps_low_route_comparison_separate_from_physical_proof():
    evidence = parse_route_only_crop_band_row(row(), DAY)
    assert evidence.availability == "observational"
    assert evidence.diagnostic.joint.in_band_pct == 0
    assert evidence.diagnostic.physical_proof_eligible is False
    assert evidence.revision_id == 7


def test_invalid_latest_publication_fails_closed_without_raw_echo():
    bad = row()
    bad["diagnostic"]["physical_proof_eligible"] = True
    bad["diagnostic"]["private_value"] = "never echo"
    evidence = parse_route_only_crop_band_row(bad, DAY)
    assert evidence.availability == "unavailable"
    assert evidence.diagnostic is None
    assert "never echo" not in evidence.model_dump_json()
    assert parse_route_only_crop_band_row(row(), DAY + timedelta(days=1)).availability == "unavailable"


def test_reader_rejects_missing_or_rebound_route_lineage():
    missing = row()
    del missing["diagnostic"]["panel_routes"]
    assert parse_route_only_crop_band_row(missing, DAY).unavailable_reason == "invalid_diagnostic"
    rebound = row()
    rebound["diagnostic"]["panel_routes"][0]["zone"] = "west"
    assert parse_route_only_crop_band_row(rebound, DAY).unavailable_reason == "invalid_diagnostic"


def test_missing_day_is_unavailable_and_physical_field_is_not_promoted():
    absent = {
        **row(),
        "revision_id": None,
        "recorded_at": None,
        "diagnostic": None,
        "unavailable_reason": "not_published",
    }
    evidence = parse_route_only_crop_band_row(absent, DAY)
    assert evidence == RouteOnlyCropBandEvidence(
        day=DAY, greenhouse_id="vallery", unavailable_reason="not_published", served_at=absent["served_at"]
    )


def test_site_labels_route_observation_and_keeps_physical_block_unavailable():
    spec = importlib.util.spec_from_file_location("route_publisher", ROOT / "scripts/update-evidence-snapshots.py")
    assert spec and spec.loader
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    evidence = parse_route_only_crop_band_row(row(), DAY)
    rendered = publisher.route_only_crop_band_block(evidence.model_dump(mode="json"), DAY.isoformat())
    assert "Route-only fixed-panel observation" in rendered
    assert "0.0% joint" in rendered
    assert "not continuous exposure, physical crop efficacy" in rendered
    assert publisher.route_only_crop_band_block(evidence.model_dump(mode="json"), "2026-11-03") == ""
    assert "Unavailable" in publisher.physical_crop_band_block(None, DAY.isoformat())


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class Conn:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    def transaction(self):
        return Transaction()

    async def execute(self, sql):
        self.calls.append(sql)

    async def fetchrow(self, sql, *args, **kwargs):
        self.calls.append((sql, args, kwargs))
        if self.error:
            raise self.error
        return self.value


def test_async_reader_is_bounded_and_fails_closed_before_migration():
    conn = Conn(row())
    evidence = asyncio.run(read_route_only_crop_band_evidence(conn, DAY))
    assert evidence.availability == "observational"
    assert conn.calls[0] == "SET LOCAL statement_timeout = '3000ms'"
    assert conn.calls[1][1] == (DAY, "vallery")
    assert conn.calls[1][2] == {"timeout": 3.5}
    assert asyncio.run(read_route_only_crop_band_evidence(conn, DAY, "other")).availability == "unavailable"
    assert len(conn.calls) == 2
    unavailable = Conn(error=asyncpg.UndefinedFunctionError("missing"))
    assert asyncio.run(read_route_only_crop_band_evidence(unavailable, DAY)).unavailable_reason == "reader_unavailable"
    timeout = Conn(error=asyncpg.QueryCanceledError("timeout"))
    assert asyncio.run(read_route_only_crop_band_evidence(timeout, DAY)).unavailable_reason == "db_statement_timeout"
