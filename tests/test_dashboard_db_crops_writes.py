"""#1327 and #1328 on a private PostgreSQL: the planting writes as the dashboard login (the service) and as the MCP
login (the tools).

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a fresh loopback PostgreSQL
and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``; without one this module skips. The database starts from
tests/test_dashboard_db_irrigation_writes.py's (M4, 282's dashboard login, 283's lines, 280's activity log and its
stand-in grants). This file adds the crop tables and their triggers from db/schema.sql, 255's assignment-revision
trigger (production's crops carries it), a small location tree and then 293's body, run as the owner, so the
occupancy table, its one-planting-per-cell exclusion and the dashboard and MCP grants are migration 293's own. The MCP
duty's pre-293 privileges on crops, crop_events, harvests, crop_catalog and zones are stand-ins, read from production.

What these tests hold: a held cell is refused with ``position_occupied`` and nothing is written; ``replace`` trims or
ends the occupant; a repeat with the same request id replays and writes nothing; a move closes the old ranges and
opens the new ones, both kept in history; a partial harvest clears only the cells named and keeps the planting.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import irrigation_db_support as support
import pytest
from test_dashboard_db_irrigation_writes import (
    DASHBOARD,
    DSN,
    MCP,
    PERSON,
    call,
    connect,
    refused,
    run,
    seeds,  # noqa: F401
    sql,
)
from test_mcp_audience_auth import clean_auth_env, mcp_server, mcp_types  # noqa: F401

from verdify_services import crops

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

LINEAGE = support.REPO_ROOT / "db/migrations/255-fixed-panel-crop-assignment-lineage.sql"
PLANTINGS = support.REPO_ROOT / "db/migrations/293-crop-plantings.sql"
CROP_TABLES = ("zones", "shelves", "positions", "crop_catalog", "crops", "crop_events", "harvests")
KIND_ORDER = ("TABLE", "SEQUENCE", "SEQUENCE OWNED BY", "DEFAULT", "CONSTRAINT", "FK CONSTRAINT")
TRIGGERS = ("crops_log_clear", "crops_log_planted", "crops_log_stage_change")
# 293 updates water_systems rows; the table alone is enough (no rows, no foreign keys).
WATER_SYSTEMS = (
    ("water_systems", "TABLE"),
    ("water_systems_id_seq", "SEQUENCE"),
    ("water_systems_id_seq", "SEQUENCE OWNED BY"),
    ("water_systems id", "DEFAULT"),
    ("water_systems water_systems_pkey", "CONSTRAINT"),
)
# Two zones: the east hydro rack (cells 1-12), the east top shelf (bays 1-4) and the center hanging spot. 293's
# layout updates and its layout check need these exact slugs and label shapes.
TREE = """
INSERT INTO public.zones (id, slug, name) VALUES (1, 'east', 'East'), (2, 'center', 'Center');
INSERT INTO public.shelves (id, zone_id, slug, name, kind, tier) VALUES
    (1, 1, 'east_hydro', 'East hydro rack', 'nft', NULL),
    (2, 1, 'east_shelf_top', 'East top shelf', 'shelf', 1),
    (3, 2, 'center_hang', 'Center hanging', 'hang', NULL);
INSERT INTO public.positions (shelf_id, label, slot_number, mount_type)
SELECT 1, 'EAST-HYDRO-' || n, n, 'nft_port' FROM generate_series(1, 12) n;
INSERT INTO public.positions (shelf_id, label, slot_number, mount_type)
SELECT 2, 'EAST-SHELF-T' || n, n, 'shelf_slot' FROM generate_series(1, 4) n;
INSERT INTO public.positions (shelf_id, label, slot_number, mount_type) VALUES (3, 'CENTER-HANG-1', 1, 'hanging_hook');
"""
# The MCP duty's privileges before 293 (production, information_schema, 2026-11-06); 293 adds the rest.
MCP_STAND_INS = """
GRANT SELECT, INSERT, UPDATE ON public.crops TO verdify_mcp_runtime;
GRANT SELECT, INSERT ON public.crop_events, public.harvests TO verdify_mcp_runtime;
GRANT SELECT ON public.crop_catalog, public.zones TO verdify_mcp_runtime;
GRANT USAGE ON SEQUENCE public.crops_id_seq, public.crop_events_id_seq, public.harvests_id_seq TO verdify_mcp_runtime;
"""
STEWARD = ("agent:steward", "steward")
DAY = dt.timedelta(days=1)


def crop_schema_sql() -> str:
    sections = support.schema_sections()
    keys = [
        key
        for kind in KIND_ORDER
        for key in sections
        if key[1] == kind
        and key[2] == "public"
        and any(key[0] == t or key[0].startswith(f"{t} ") or key[0] == f"{t}_id_seq" for t in CROP_TABLES)
    ]
    keys += [(f"{name}()", "FUNCTION", "public") for name in TRIGGERS]
    keys += [(f"crops trg_{name}", "TRIGGER", "public") for name in TRIGGERS]
    keys += [(name, kind, "public") for name, kind in WATER_SYSTEMS]
    return "\n".join(sections[key] for key in keys)


def lineage_sql() -> str:
    """255's revision table, its two trigger functions, owner, revokes and triggers; not its fixed-panel part."""
    text = LINEAGE.read_text()
    return text[text.index("CREATE TABLE public.crop_assignment_revisions") : text.index("ALTER TABLE public.fixed_")]


@pytest.fixture(scope="module")
def tree(seeds):  # noqa: F811
    async def setup():
        conn = await connect()
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
            await conn.execute("CREATE SCHEMA IF NOT EXISTS verdify_vision_runtime AUTHORIZATION verdify")
            await conn.execute("SET ROLE verdify")
            try:
                await conn.execute(crop_schema_sql())
                await conn.execute(TREE)
            finally:
                await conn.execute("RESET ROLE")
            await conn.execute(lineage_sql())
            await conn.execute("SET ROLE verdify")
            try:
                await conn.execute(
                    "SET search_path = pg_catalog, public, pg_temp;\n" + support.migration_body(PLANTINGS)
                )
            finally:
                await conn.execute("RESET search_path")
                await conn.execute("RESET ROLE")
            await conn.execute(MCP_STAND_INS)
        finally:
            await conn.close()

    run(setup())


@pytest.fixture(autouse=True)
def empty(tree):
    sql(
        "DELETE FROM public.crop_occupancy; DELETE FROM public.crop_events; DELETE FROM public.harvests; "
        "DELETE FROM public.crops; TRUNCATE public.activity_log"
    )


# ── Reads, as the owner ─────────────────────────────────────────────────────


def now() -> dt.datetime:
    return sql("SELECT now() AS now")[0]["now"]


def ranges(planting_id: int) -> list[tuple[str, dt.datetime, dt.datetime | None]]:
    """Every occupancy range of a planting: (cell or shelf or zone, since, until), oldest first."""
    rows = sql(
        "SELECT coalesce(p.label, s.slug, z.slug) AS place, lower(o.during) AS since, upper(o.during) AS until "
        "FROM public.crop_occupancy o JOIN public.zones z ON z.id = o.zone_id "
        "LEFT JOIN public.shelves s ON s.id = o.shelf_id LEFT JOIN public.positions p ON p.id = o.position_id "
        "WHERE o.crop_id = $1 ORDER BY lower(o.during), p.label",
        planting_id,
    )
    return [(r["place"], r["since"], r["until"]) for r in rows]


def crop_row(planting_id: int) -> dict:
    [row] = sql(
        "SELECT c.is_active, c.end_reason, c.cleared_at, c.source, c.actor, p.label AS anchor "
        "FROM public.crops c LEFT JOIN public.positions p ON p.id = c.position_id WHERE c.id = $1",
        planting_id,
    )
    return dict(row)


def totals() -> dict:
    [row] = sql(
        "SELECT (SELECT count(*) FROM public.crops) AS crops, (SELECT count(*) FROM public.crop_occupancy) AS ranges, "
        "(SELECT count(*) FROM public.crop_events) AS events, (SELECT count(*) FROM public.harvests) AS harvests, "
        "(SELECT count(*) FROM public.activity_log) AS activity"
    )
    return dict(row)


def history(label: str) -> list[tuple[int, dt.datetime, dt.datetime | None]]:
    """API-CROP-04 as the dashboard login: who has held the cell, current first."""
    found = call(DASHBOARD, crops.position_history, label)
    return [(t.planting_id, t.since, t.until) for t in found.items]


def plant(positions, *, login=DASHBOARD, actor=PERSON, request_id=None, **fields):
    body = {"crop": "W6 test greens", "positions": positions, **fields}
    return call(login, crops.plant, actor, body, request_id=request_id)


# ── The service, as the dashboard login ────────────────────────────────────


def test_a_held_cell_is_refused_and_nothing_is_written():
    first = plant(["EAST-HYDRO-1..3"]).value
    before = totals()

    problem = refused(DASHBOARD, crops.plant, PERSON, {"crop": "Basil", "positions": ["EAST-HYDRO-3..4"]})

    assert problem.code == "position_occupied"
    assert problem.current == "EAST-HYDRO-3"
    assert f"planting {first.id}" in problem.detail
    assert totals() == before
    assert crop_row(first.id)["is_active"] is True
    assert [place for place, _, until in ranges(first.id) if until is None] == [f"EAST-HYDRO-{n}" for n in (1, 2, 3)]


def test_replace_trims_the_occupant_and_ends_one_left_with_no_place():
    start = now() - 2 * DAY
    first = plant(["EAST-HYDRO-1..3"], planted_at=start).value
    lone = plant(["EAST-HYDRO-5"], planted_at=start).value

    second = plant(["EAST-HYDRO-3..5"], replace=True).value

    at = next(since for place, since, _ in ranges(second.id) if place == "EAST-HYDRO-3")
    assert second.cells == ["EAST-HYDRO-3", "EAST-HYDRO-4", "EAST-HYDRO-5"]
    # The trimmed occupant keeps cells 1-2; its cell-3 range ends where the new planting starts.
    assert crop_row(first.id)["is_active"] is True
    assert ranges(first.id) == [
        ("EAST-HYDRO-1", start, None),
        ("EAST-HYDRO-2", start, None),
        ("EAST-HYDRO-3", start, at),
    ]
    assert history("EAST-HYDRO-3") == [(second.id, at, None), (first.id, start, at)]
    # The occupant left with no place ends as removed, its range kept.
    assert crop_row(lone.id) | {"cleared_at": None} == {
        "is_active": False,
        "end_reason": "removed",
        "cleared_at": None,
        "source": "dashboard",
        "actor": "jason",
        "anchor": "EAST-HYDRO-5",
    }
    assert crop_row(lone.id)["cleared_at"] == at
    assert ranges(lone.id) == [("EAST-HYDRO-5", start, at)]
    [activity] = sql(
        "SELECT detail::text AS detail FROM public.activity_log WHERE action = 'planting.create' "
        "ORDER BY id DESC LIMIT 1"
    )
    replaced = json.loads(activity["detail"])["replaced"]
    assert replaced == [
        {"planting_id": first.id, "cells": ["EAST-HYDRO-3"], "ended": False},
        {"planting_id": lone.id, "cells": ["EAST-HYDRO-5"], "ended": True},
    ]


def test_replace_cannot_shorten_a_range_that_starts_later():
    later = plant(["EAST-HYDRO-6"]).value
    before = totals()

    problem = refused(
        DASHBOARD,
        crops.plant,
        PERSON,
        {"crop": "Basil", "positions": ["EAST-HYDRO-6"], "planted_at": now() - DAY, "replace": True},
    )

    assert problem.code == "position_occupied"
    assert totals() == before
    assert crop_row(later.id)["is_active"] is True


def test_a_repeat_with_the_same_request_id_replays_and_writes_nothing():
    key = uuid.uuid4()
    first = plant(["EAST-HYDRO-7..8"], request_id=key)
    before = totals()

    again = plant(["EAST-HYDRO-7..8"], request_id=key)

    assert first.replayed is False and again.replayed is True
    assert again.value.id == first.value.id
    assert totals() == before
    # The same key with another body is refused, not replayed.
    problem = refused(DASHBOARD, crops.plant, PERSON, {"crop": "Basil", "positions": ["EAST-HYDRO-9"]}, request_id=key)
    assert problem.code == "request_id_reused"
    assert totals() == before

    # A move repeated with its key moves once.
    move_key = uuid.uuid4()
    body = {"to_positions": ["EAST-HYDRO-11..12"], "reason": "W6 repeat"}
    moved = call(DASHBOARD, crops.move, PERSON, first.value.id, body, request_id=move_key)
    after_move = totals()
    repeat = call(DASHBOARD, crops.move, PERSON, first.value.id, body, request_id=move_key)
    assert moved.replayed is False and repeat.replayed is True
    assert repeat.value.cells == ["EAST-HYDRO-11", "EAST-HYDRO-12"]
    assert totals() == after_move


def test_a_move_keeps_both_ranges_in_history():
    start, moved_at = now() - 2 * DAY, now() - DAY
    planting = plant(["EAST-HYDRO-9..10"], planted_at=start, count_per_cell=3).value

    result = call(
        DASHBOARD,
        crops.move,
        PERSON,
        planting.id,
        {"to_positions": ["EAST-SHELF-T1..T2"], "moved_at": moved_at, "reason": "to the top shelf"},
    ).value

    assert result.cells == ["EAST-SHELF-T1", "EAST-SHELF-T2"]
    assert ranges(planting.id) == [
        ("EAST-HYDRO-10", start, moved_at),
        ("EAST-HYDRO-9", start, moved_at),
        ("EAST-SHELF-T1", moved_at, None),
        ("EAST-SHELF-T2", moved_at, None),
    ]
    assert history("EAST-HYDRO-9") == [(planting.id, start, moved_at)]
    assert history("EAST-SHELF-T1") == [(planting.id, moved_at, None)]
    assert crop_row(planting.id)["anchor"] == "EAST-SHELF-T1"
    [count] = sql("SELECT DISTINCT count FROM public.crop_occupancy WHERE crop_id = $1", planting.id)
    assert count["count"] == 3
    [event] = sql(
        "SELECT ts, detail::text AS detail, notes FROM public.crop_events WHERE crop_id = $1 AND event_type = 'moved'",
        planting.id,
    )
    assert event["ts"] == moved_at and event["notes"] == "to the top shelf"
    assert json.loads(event["detail"]) == {
        "from": ["EAST-HYDRO-9", "EAST-HYDRO-10"],
        "to": ["EAST-SHELF-T1", "EAST-SHELF-T2"],
        "replaced": [],
    }
    # The detail read shows both: the open places and the moved event.
    detail = call(DASHBOARD, crops.planting, planting.id)
    assert [p.cell for p in detail.places] == ["EAST-SHELF-T1", "EAST-SHELF-T2"]
    assert "moved" in [e.event_type for e in detail.events]
    # The cells it left are free from the move on.
    assert plant(["EAST-HYDRO-9"]).value.cells == ["EAST-HYDRO-9"]


def test_a_move_into_a_held_cell_is_refused_without_replace():
    held = plant(["EAST-HYDRO-11"]).value
    mover = plant(["EAST-HYDRO-12"]).value
    before = totals()

    problem = refused(DASHBOARD, crops.move, PERSON, mover.id, {"to_positions": ["EAST-HYDRO-11"]})

    assert problem.code == "position_occupied" and problem.current == "EAST-HYDRO-11"
    assert totals() == before
    assert crop_row(held.id)["is_active"] is True


def test_a_partial_harvest_clears_only_the_cells_named():
    planting = plant(["EAST-HYDRO-1..3"], planted_at=now() - DAY, count=6, count_per_cell=2).value

    partial = call(
        DASHBOARD,
        crops.harvest,
        PERSON,
        planting.id,
        {"positions": ["EAST-HYDRO-1"], "count": 2, "weight_kg": 0.3, "notes": "first cut"},
    ).value

    assert partial.active is True
    assert partial.cells == ["EAST-HYDRO-2", "EAST-HYDRO-3"]
    row = crop_row(planting.id)
    assert row["is_active"] is True and row["end_reason"] is None and row["anchor"] == "EAST-HYDRO-2"
    closed = [(place, until) for place, _, until in ranges(planting.id) if until is not None]
    assert [place for place, _ in closed] == ["EAST-HYDRO-1"]
    [harvest] = sql("SELECT unit_count, weight_kg, operator FROM public.harvests WHERE crop_id = $1", planting.id)
    assert dict(harvest) == {"unit_count": 2, "weight_kg": 0.3, "operator": "jason"}
    [event] = sql(
        "SELECT count, detail::text AS detail FROM public.crop_events WHERE crop_id = $1 AND event_type = 'harvested'",
        planting.id,
    )
    assert event["count"] == 2
    assert json.loads(event["detail"])["cleared"] == ["EAST-HYDRO-1"]
    assert json.loads(event["detail"])["final"] is False

    # The final harvest ends it; every range closes and both harvests stay.
    final = call(DASHBOARD, crops.harvest, PERSON, planting.id, {"count": 4, "final": True}).value
    assert final.active is False and final.end_reason == "harvested"
    assert all(until is not None for _, _, until in ranges(planting.id))
    assert sql("SELECT count(*) AS n FROM public.harvests WHERE crop_id = $1", planting.id)[0]["n"] == 2
    assert refused(DASHBOARD, crops.harvest, PERSON, planting.id, {"count": 1}).code == "planting_ended"


def test_the_writes_record_who_and_where_in_activity():
    key = uuid.uuid4()
    planting = plant(["EAST-HYDRO-4"], request_id=key, reason="W6 test").value
    call(DASHBOARD, crops.end, PERSON, planting.id, {"end_reason": "removed"})

    rows = sql("SELECT source, actor, action, target, request_id FROM public.activity_log ORDER BY id")
    assert [(r["source"], r["actor"], r["action"], r["target"]) for r in rows] == [
        ("dashboard", "jason", "planting.create", str(planting.id)),
        ("dashboard", "jason", "planting.end", str(planting.id)),
    ]
    assert rows[0]["request_id"] == key


# ── The tools, as the MCP login ────────────────────────────────────────────


@pytest.fixture()
def tools(mcp_server, mcp_types, monkeypatch):  # noqa: F811
    """The MCP tools, each on its own connection as the MCP login."""

    async def db():
        return await connect(MCP)

    monkeypatch.setattr(mcp_server, "_db", db)
    return mcp_server


def as_caller(server, coroutine, who: tuple[str, str] = STEWARD):
    token = server._REQUEST_ACTOR.set(server.McpActor(source=who[0], name=who[1]))
    try:
        return run(coroutine)
    finally:
        server._REQUEST_ACTOR.reset(token)


def answer(result) -> dict:
    assert result.isError is False, result.content[0].text
    return json.loads(result.content[0].text)


def refusal(result) -> dict:
    assert result.isError is True, result.content[0].text
    return json.loads(result.content[0].text)


def test_tool_record_planting_refuses_a_held_cell_and_replace_takes_it(tools):
    first = answer(as_caller(tools, tools.record_planting(crop="W6 test greens", positions=["EAST-HYDRO-1..2"])))
    before = totals()

    held = refusal(as_caller(tools, tools.record_planting(crop="Basil", positions=["EAST-HYDRO-2..3"])))
    assert held["code"] == "position_occupied" and held["current"] == "EAST-HYDRO-2"
    assert totals() == before

    preview = answer(as_caller(tools, tools.record_planting(crop="Basil", positions=["EAST-HYDRO-2..3"], dry_run=True)))
    assert [c["planting_id"] for c in preview["conflicts"]] == [first["id"]]
    assert totals() == before

    second = answer(as_caller(tools, tools.record_planting(crop="Basil", positions=["EAST-HYDRO-2..3"], replace=True)))
    assert second["cells"] == ["EAST-HYDRO-2", "EAST-HYDRO-3"]
    assert second["recorded_by"] == {"source": "agent:steward", "name": "steward"}
    assert [place for place, _, until in ranges(first["id"]) if until is None] == ["EAST-HYDRO-1"]
    assert crop_row(second["id"])["source"] == "agent:steward"


def test_tool_repeat_with_the_same_request_id_replays(tools):
    key = str(uuid.uuid4())
    first = answer(
        as_caller(tools, tools.record_planting(crop="W6 test greens", positions=["EAST-HYDRO-5"], request_id=key))
    )
    before = totals()

    again = answer(
        as_caller(tools, tools.record_planting(crop="W6 test greens", positions=["EAST-HYDRO-5"], request_id=key))
    )

    assert again["replayed"] is True and "replayed" not in first
    assert again["id"] == first["id"]
    assert totals() == before
    reused = refusal(as_caller(tools, tools.record_planting(crop="Basil", positions=["EAST-HYDRO-6"], request_id=key)))
    assert reused["code"] == "request_id_reused"
    assert totals() == before


def test_tool_move_keeps_both_ranges_and_history_shows_them(tools):
    start, moved_at = now() - 2 * DAY, now() - DAY
    planting = answer(
        as_caller(
            tools,
            tools.record_planting(crop="W6 test greens", positions=["EAST-HYDRO-7"], planted_at=start.isoformat()),
        )
    )

    moved = answer(
        as_caller(
            tools,
            tools.move_planting(
                planting_id=planting["id"], to_positions=["EAST-HYDRO-8"], moved_at=moved_at.isoformat()
            ),
        )
    )

    assert moved["cells"] == ["EAST-HYDRO-8"]
    assert ranges(planting["id"]) == [("EAST-HYDRO-7", start, moved_at), ("EAST-HYDRO-8", moved_at, None)]
    old = answer(as_caller(tools, tools.planting_history(position="EAST-HYDRO-7")))
    new = answer(as_caller(tools, tools.planting_history(position="EAST-HYDRO-8")))
    assert [(i["planting_id"], i["until"] is None) for i in old["items"]] == [(planting["id"], False)]
    assert dt.datetime.fromisoformat(old["items"][0]["until"]) == moved_at
    assert [(i["planting_id"], i["until"]) for i in new["items"]] == [(planting["id"], None)]
    detail = answer(as_caller(tools, tools.planting_history(planting_id=planting["id"])))
    assert "moved" in [e["event_type"] for e in detail["events"]]


def test_tool_partial_harvest_keeps_the_planting(tools):
    planting = answer(
        as_caller(tools, tools.record_planting(crop="W6 test greens", positions=["EAST-HYDRO-9..10"], count=4))
    )

    partial = answer(
        as_caller(
            tools,
            tools.harvest_planting(planting_id=planting["id"], count=2, weight_kg=0.2, positions=["EAST-HYDRO-9"]),
        )
    )

    assert partial["active"] is True and partial["cells"] == ["EAST-HYDRO-10"]
    [harvest] = sql("SELECT unit_count, operator FROM public.harvests WHERE crop_id = $1", planting["id"])
    assert dict(harvest) == {"unit_count": 2, "operator": "steward"}
    removed = answer(
        as_caller(tools, tools.remove_planting(planting_id=planting["id"], end_reason="removed", reason="W6 test"))
    )
    assert removed["active"] is False and removed["end_reason"] == "removed"
    assert all(until is not None for _, _, until in ranges(planting["id"]))


def test_a_tool_write_needs_a_resolved_caller(tools):
    before = totals()
    result = run(tools.record_planting(crop="W6 test greens", positions=["EAST-HYDRO-11"]))
    assert refusal(result)["code"] == "forbidden"
    assert totals() == before
