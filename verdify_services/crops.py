"""Plantings on the location tree: the one read and write path for the Crops page, the operator API and MCP.

Migration 293 (#1327, #1328). A planting is one ``crops`` row (a batch). ``crop_occupancy`` says where it is over
time: a zone, a shelf in that zone, or one cell on that shelf. A cell is optional (Jason, 2026-11-05: a zone or a
shelf holds many plantings in planters); a cell holds one planting at a time, which the database enforces.

- ``resolve_positions`` is the one position parser. It reads exact labels (``EAST-HYDRO-12``), label ranges
  (``EAST-HYDRO-1..10``), hydro numbers (``hydro 1-30``), rails (``rail A top``), shelf cells (``west top 1-3``,
  ``south shelf bottom 2``), every cell of a place (``all hydro``, ``all south shelves``), a whole shelf (``south
  floor``, ``hanging``, ``center_hang``) and a zone (``center``). Input that could mean two places is refused with
  ``ambiguous_position`` and the candidates; nothing is written.
- Reads: ``locations``, ``plantings``, ``planting``, ``position_history``, ``catalog`` and ``resolve`` (the preview:
  the places a text means and what holds them, writing nothing).
- Writes, each one ``activity.recorded_write`` transaction: ``plant``, ``move``, ``harvest``, ``end``, ``update``
  and ``annotate``. A requested cell that holds another planting is refused with ``position_occupied`` and the cells,
  unless ``replace``: the occupant's open range on that cell then ends at the new planting's start, and an occupant
  left with no place ends as ``removed``.

Plant-agnostic boundary: these functions write records only. They never touch ``crop_target_profiles``,
``crop_band_anchors``, ``desired_state`` or any setting, and no control function reads ``crop_occupancy``
(tests/test_crops_control_boundary.py). One control path does read active ``crops`` rows today:
``fn_lighting_policy`` sets the grow-light window from the active crop with the highest DLI target (the Vandas: DLI
14, 07:00-17:00). Recording a planting can change that window; that is unchanged here and is a decision for Jason.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from verdify_schemas.activity import ActivityLogRow
from verdify_schemas.crops import (
    PlantingCreate,
    PlantingEnd,
    PlantingHarvest,
    PlantingMove,
    PlantingNote,
    PlantingPatch,
)
from verdify_services import activity
from verdify_services.actor import Actor, ActorRef
from verdify_services.problems import ServiceError, problem

GREENHOUSE_ID = "vallery"
LOCAL = ZoneInfo("America/Denver")
# A planting's times may lie in the past (backdating); at most this far ahead of the database clock.
FUTURE_SKEW = timedelta(minutes=5)
LIMIT_MAX = 500
_LOCK_SQL = "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('plantings:' || $1))"
_CATALOG_SUFFIX = re.compile(r"\s+in an Automated Greenhouse$", re.I)
_NUMBER = re.compile(r"(\d+)$")


# ── The location tree ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Zone:
    id: int
    slug: str
    name: str


@dataclass(frozen=True)
class Shelf:
    id: int
    slug: str
    name: str
    kind: str
    tier: int | None
    zone: Zone
    wall: str | None = None
    display_order: int | None = None
    label_short: str | None = None


@dataclass(frozen=True)
class Cell:
    id: int
    label: str
    shelf: Shelf
    mount_type: str | None = None
    layout_group: str | None = None
    layout_row: int | None = None
    layout_col: int | None = None

    @property
    def number(self) -> int | None:
        match = _NUMBER.search(self.label)
        return int(match.group(1)) if match else None


@dataclass(frozen=True)
class Tree:
    zones: tuple[Zone, ...]
    shelves: tuple[Shelf, ...]
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class Target:
    """One place a planting can be: a zone, a shelf in it, or a cell on that shelf."""

    zone: Zone
    shelf: Shelf | None = None
    cell: Cell | None = None

    @property
    def key(self) -> tuple[int, int | None, int | None]:
        return (self.zone.id, self.shelf.id if self.shelf else None, self.cell.id if self.cell else None)

    @property
    def label(self) -> str:
        if self.cell is not None:
            return self.cell.label
        return self.shelf.slug if self.shelf is not None else self.zone.slug


_ZONES_SQL = "SELECT id, slug, name FROM public.zones WHERE greenhouse_id = $1 ORDER BY id"
_SHELVES_SQL = (
    "SELECT id, slug, name, kind, tier, zone_id, wall, display_order, label_short FROM public.shelves "
    "WHERE greenhouse_id = $1 ORDER BY display_order NULLS LAST, id"
)
_CELLS_SQL = (
    "SELECT id, label, shelf_id, mount_type, layout_group, layout_row, layout_col FROM public.positions "
    "WHERE greenhouse_id = $1 AND is_active ORDER BY shelf_id, layout_row NULLS LAST, layout_col NULLS LAST, id"
)


async def load_tree(conn: asyncpg.Connection) -> Tree:
    zones = {r["id"]: Zone(r["id"], r["slug"], r["name"]) for r in await conn.fetch(_ZONES_SQL, GREENHOUSE_ID)}
    shelves = {}
    for r in await conn.fetch(_SHELVES_SQL, GREENHOUSE_ID):
        shelves[r["id"]] = Shelf(
            r["id"],
            r["slug"],
            r["name"],
            r["kind"],
            r["tier"],
            zones[r["zone_id"]],
            r["wall"],
            r["display_order"],
            r["label_short"],
        )
    cells = tuple(
        Cell(
            r["id"],
            r["label"],
            shelves[r["shelf_id"]],
            r["mount_type"],
            r["layout_group"],
            r["layout_row"],
            r["layout_col"],
        )
        for r in await conn.fetch(_CELLS_SQL, GREENHOUSE_ID)
        if r["shelf_id"] in shelves
    )
    return Tree(tuple(zones.values()), tuple(shelves.values()), cells)


# ── The one position parser ─────────────────────────────────────────────────

# Words a place is described with. Each kind word picks the shelves of that kind; top and bottom also pick the hydro
# rows on the rack. Filler words carry nothing.
_KIND_WORDS = {
    "hydro": "nft",
    "hydroponic": "nft",
    "hydroponics": "nft",
    "nft": "nft",
    "rack": "nft",
    "rail": "nft",
    "rails": "nft",
    "floor": "floor",
    "pot": "floor",
    "pots": "floor",
    "hang": "hang",
    "hanging": "hang",
    "hook": "hang",
    "hooks": "hang",
    "basket": "hang",
    "baskets": "hang",
}
_TIER_WORDS = {"top": 1, "upper": 1, "bottom": 0, "lower": 0}
_SHELF_WORDS = {"shelf", "shelves"}
_FILLER = {
    "cell",
    "cells",
    "slot",
    "slots",
    "position",
    "positions",
    "port",
    "ports",
    "bay",
    "bays",
    "row",
    "rows",
    "wall",
    "zone",
    "the",
    "in",
    "on",
    "at",
    "of",
    "number",
    "no",
}
_SPLIT = re.compile(r"\s*(?:,|;|\+|\band\b)\s*", re.I)
_TOKEN = re.compile(r"\d+\s*(?:-|–|\.\.|to)\s*\d+|\d+|[a-z_]+", re.I)
_LABEL_RANGE = re.compile(
    r"^(?P<left>[A-Z][A-Z0-9_-]*?-?)(?P<a>\d+)\s*\.\.\s*(?:(?P<right>[A-Z][A-Z0-9_-]*?-?))?(?P<b>\d+)$"
)
MAX_RANGE = 200


def _refuse(detail: str, *, field: str = "positions", current: str | None = None) -> ServiceError:
    return ServiceError(problem("invalid_request", detail, field=field, current=current))


def _ambiguous(term: str, candidates: Iterable[str], field: str) -> ServiceError:
    names = sorted(set(candidates))
    return ServiceError(
        problem(
            "ambiguous_position",
            f"'{term}' could mean {', '.join(names)}. Name one of them; nothing was written",
            field=field,
            current=", ".join(names),
        )
    )


def _numbers(token: str) -> list[int]:
    parts = re.split(r"\s*(?:-|–|\.\.|to)\s*", token)
    if len(parts) == 1:
        return [int(parts[0])]
    low, high = int(parts[0]), int(parts[1])
    if low > high:
        low, high = high, low
    if high - low + 1 > MAX_RANGE:
        raise _refuse(f"'{token}' is more than {MAX_RANGE} places")
    return list(range(low, high + 1))


def _label_range(tree: Tree, upper: str, term: str, field: str) -> list[Target] | None:
    match = _LABEL_RANGE.match(upper)
    if match is None:
        return None
    prefix = match["left"]
    # The right side may repeat the whole prefix (EAST-SHELF-T1..EAST-SHELF-T2) or just its
    # tail (EAST-SHELF-T1..T2), as people write shelf slots.
    if match["right"] and not prefix.endswith(match["right"]):
        raise _refuse(f"'{term}' runs between two different racks or shelves", field=field)
    by_label = {cell.label: cell for cell in tree.cells}
    found, missing = [], []
    for n in _numbers(f"{match['a']}..{match['b']}"):
        cell = by_label.get(f"{prefix}{n}")
        (found if cell else missing).append(cell or f"{prefix}{n}")
    if missing:
        raise _refuse(f"No such cell: {', '.join(missing[:5])}", field=field)
    return [Target(cell.shelf.zone, cell.shelf, cell) for cell in found]


def _parse_words(tree: Tree, term: str, field: str) -> list[Target]:
    tokens = [t.lower() for t in _TOKEN.findall(term)]
    zones = {zone.slug: zone for zone in tree.zones}
    zone_words, kinds, tiers, numbers, rails = [], set(), set(), [], []
    every = shelf_word = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token[0].isdigit():
            numbers.extend(_numbers(token))
        elif token in zones:
            zone_words.append(zones[token])
        elif token in ("rail", "rails") and i + 1 < len(tokens) and re.fullmatch(r"[a-c]", tokens[i + 1]):
            kinds.add("nft")
            rails.append(tokens[i + 1].upper())
            i += 1
        elif token in _KIND_WORDS:
            kinds.add(_KIND_WORDS[token])
        elif token in _TIER_WORDS:
            tiers.add(_TIER_WORDS[token])
        elif token in _SHELF_WORDS:
            shelf_word = True
        elif token in ("all", "every", "whole", "entire"):
            every = True
        elif token in _FILLER:
            pass
        else:
            raise _refuse(
                f"'{term}': I don't know '{token}'. Use a cell label (EAST-HYDRO-12), a range (hydro 1-30, "
                "EAST-HYDRO-1..10), a rail (rail A top), a shelf (west top 1-3, south floor, hanging) or a zone "
                "(center)",
                field=field,
            )
        i += 1
    if len(kinds) > 1 or len(set(zone_words)) > 1 or len(tiers) > 1:
        raise _refuse(f"'{term}' names more than one place; separate places with a comma", field=field)
    zone = zone_words[0] if zone_words else None
    kind = next(iter(kinds), None)
    tier = next(iter(tiers), None)
    if kind is None and (tier is not None or shelf_word):
        kind = "shelf"
    if kind is None and not numbers:
        if zone is None:
            raise _refuse(f"'{term}' names no place", field=field)
        if not every:
            return [Target(zone)]
    shelves = [
        shelf
        for shelf in tree.shelves
        if (zone is None or shelf.zone.id == zone.id)
        and (kind is None or shelf.kind == kind or (kind == "nft" and shelf.kind == "hydro"))
        and (kind != "shelf" or tier is None or shelf.tier == tier)
    ]
    if not shelves:
        raise _refuse(f"'{term}': no shelf or rack matches", field=field)
    row = None
    if kind == "nft" and tier is not None:
        row = 0 if tier == 1 else 1
    cells = [cell for cell in tree.cells if cell.shelf in shelves]
    if rails:
        cells = [cell for cell in cells if cell.layout_group in rails]
    if row is not None:
        cells = [cell for cell in cells if cell.layout_row == row]
    if numbers:
        wanted = set(numbers)
        cells = [cell for cell in cells if cell.number in wanted]
        missing = sorted(wanted - {cell.number for cell in cells})
        if missing:
            raise _refuse(f"'{term}': no cell numbered {', '.join(map(str, missing[:5]))}", field=field)
        owners = {cell.shelf.slug for cell in cells}
        if len(owners) > 1:
            raise _ambiguous(term, owners, field)
        return [Target(cell.shelf.zone, cell.shelf, cell) for cell in cells]
    if rails or row is not None or every:
        if not cells:
            raise _refuse(f"'{term}': no cell matches", field=field)
        return [Target(cell.shelf.zone, cell.shelf, cell) for cell in cells]
    if len(shelves) > 1:
        raise _ambiguous(term, (shelf.slug for shelf in shelves), field)
    return [Target(shelves[0].zone, shelves[0])]


def resolve_positions(tree: Tree, terms: str | Sequence[str], *, field: str = "positions") -> list[Target]:
    """The places ``terms`` names, in order and without repeats. Raises ``invalid_request`` for a term it cannot read
    and ``ambiguous_position`` (with the candidates in ``current``) for one that could mean two places."""
    pieces = [terms] if isinstance(terms, str) else list(terms)
    targets: dict[tuple[int, int | None, int | None], Target] = {}
    by_label = {cell.label: cell for cell in tree.cells}
    shelves = {shelf.slug: shelf for shelf in tree.shelves}
    zones = {zone.slug: zone for zone in tree.zones}
    for piece in pieces:
        if not isinstance(piece, str):
            raise _refuse("Each place is text", field=field)
        for term in (part.strip() for part in _SPLIT.split(piece)):
            if not term:
                continue
            if "\x00" in term or len(term) > 200:
                raise _refuse("A place is at most 200 characters of text", field=field)
            upper = term.upper()
            lower = term.lower()
            if upper in by_label:
                found = [Target(by_label[upper].shelf.zone, by_label[upper].shelf, by_label[upper])]
            elif lower in shelves:
                found = [Target(shelves[lower].zone, shelves[lower])]
            elif lower in zones:
                found = [Target(zones[lower])]
            else:
                found = _label_range(tree, upper.replace(" ", ""), term, field)
                if found is None:
                    found = _parse_words(tree, term, field)
            for target in found:
                targets.setdefault(target.key, target)
    if not targets:
        raise _refuse("Name at least one place", field=field)
    return list(targets.values())


def compress_labels(labels: Iterable[str]) -> str:
    """``EAST-HYDRO-1..10, EAST-HYDRO-12``: consecutive numbers on one prefix as a range."""
    keyed = []
    for label in set(labels):
        match = _NUMBER.search(label)
        keyed.append((label[: match.start()], int(match.group(1))) if match else (label, None))
    keyed.sort(key=lambda item: (item[0], -1 if item[1] is None else item[1]))
    out: list[str] = []
    run: list[tuple[str, int]] = []

    def flush() -> None:
        if run:
            prefix, first = run[0]
            last = run[-1][1]
            out.append(f"{prefix}{first}" if first == last else f"{prefix}{first}..{last}")
            run.clear()

    for prefix, number in keyed:
        if number is None:
            flush()
            out.append(prefix)
        elif run and run[-1][0] == prefix and run[-1][1] + 1 == number:
            run.append((prefix, number))
        else:
            flush()
            run.append((prefix, number))
    flush()
    return ", ".join(out)


# ── Models ──────────────────────────────────────────────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Place(_Model):
    """One occupancy range: the zone, the shelf and the cell (each null above the level given), and when."""

    zone: str
    shelf: str | None = None
    cell: str | None = None
    since: AwareDatetime
    until: AwareDatetime | None = None
    count: int | None = None


class Planting(_Model):
    """A planting as the Crops page and the agents show it. ``cells`` are its open cells; ``places`` its open
    occupancy at every level; ``count`` null is unknown."""

    id: int
    crop: str
    variety: str | None = None
    crop_catalog_slug: str | None = None
    count: int | None = None
    stage: str | None = None
    planted_date: date
    active: bool
    ended_at: AwareDatetime | None = None
    end_reason: str | None = None
    notes: str | None = None
    recorded_by: ActorRef | None = None
    zones: list[str] = Field(default_factory=list)
    shelves: list[str] = Field(default_factory=list)
    cells: list[str] = Field(default_factory=list)
    where: str | None = None
    places: list[Place] = Field(default_factory=list)


class PlantingEvent(_Model):
    id: int
    ts: AwareDatetime | None = None
    event_type: str
    count: int | None = None
    notes: str | None = None
    source: str | None = None
    operator: str | None = None
    detail: dict[str, Any] | None = None


class HarvestRecord(_Model):
    id: int
    ts: AwareDatetime | None = None
    unit_count: int | None = None
    weight_kg: float | None = None
    notes: str | None = None
    operator: str | None = None


class PlantingDetail(Planting):
    """A planting with every place it has been, its events (newest first) and its harvests."""

    history: list[Place] = Field(default_factory=list)
    events: list[PlantingEvent] = Field(default_factory=list)
    harvests: list[HarvestRecord] = Field(default_factory=list)


class PlantingList(_Model):
    items: list[Planting]


class Occupant(_Model):
    planting_id: int
    crop: str
    variety: str | None = None
    since: AwareDatetime
    count: int | None = None


class WaterLine(_Model):
    line_id: str
    name: str
    note: str | None = None


class CellView(_Model):
    label: str
    number: int | None = None
    mount_type: str | None = None
    layout_group: str | None = None
    layout_row: int | None = None
    layout_col: int | None = None
    occupant: Occupant | None = None


class ShelfView(_Model):
    slug: str
    name: str
    kind: str
    tier: int | None = None
    wall: str | None = None
    display_order: int | None = None
    label_short: str | None = None
    water_lines: list[WaterLine] = Field(default_factory=list)
    plantings: list[Occupant] = Field(default_factory=list)
    cells: list[CellView] = Field(default_factory=list)


class ZoneView(_Model):
    slug: str
    name: str
    plantings: list[Occupant] = Field(default_factory=list)
    shelves: list[ShelfView] = Field(default_factory=list)


class Locations(_Model):
    """The location tree with what each place holds now: zone- and shelf-level plantings and each cell's occupant."""

    zones: list[ZoneView]
    cell_count: int
    occupied_cells: int
    active_plantings: int
    server_time: AwareDatetime


class Tenure(_Model):
    """One planting's time in a cell; ``until`` null while it is there."""

    planting_id: int
    crop: str
    variety: str | None = None
    since: AwareDatetime
    until: AwareDatetime | None = None
    count: int | None = None


class PositionHistory(_Model):
    """Who has held one cell: the current occupant first, then the rest newest first."""

    label: str
    zone: str
    shelf: str
    items: list[Tenure]


class CatalogEntry(_Model):
    slug: str
    name: str
    category: str | None = None


class Catalog(_Model):
    items: list[CatalogEntry]


class ResolvedPlace(_Model):
    zone: str
    shelf: str | None = None
    cell: str | None = None


class Conflict(_Model):
    cell: str
    planting_id: int
    crop: str
    since: AwareDatetime
    until: AwareDatetime | None = None


class Resolution(_Model):
    """What a place text means and what holds those cells at ``at``. Nothing is written."""

    places: list[ResolvedPlace]
    cells: list[str]
    where: str
    at: AwareDatetime
    conflicts: list[Conflict] = Field(default_factory=list)


# ── Shared reads ────────────────────────────────────────────────────────────

_PLANTING_COLUMNS = (
    "c.id, c.name, c.variety, cc.slug AS crop_catalog_slug, c.count, c.stage, c.planted_date, "
    "coalesce(c.is_active, false) AS is_active, c.cleared_at, c.end_reason, c.notes, c.source, c.actor"
)
_PLANTING_FROM = "FROM public.crops c LEFT JOIN public.crop_catalog cc ON cc.id = c.crop_catalog_id"
_PLACES_SQL = (
    "SELECT o.id, o.crop_id, o.zone_id, o.shelf_id, o.position_id, z.slug AS zone_slug, s.slug AS shelf_slug, "
    "p.label AS cell, lower(o.during) AS since, upper(o.during) AS until, o.count "
    "FROM public.crop_occupancy o JOIN public.zones z ON z.id = o.zone_id "
    "LEFT JOIN public.shelves s ON s.id = o.shelf_id LEFT JOIN public.positions p ON p.id = o.position_id "
    "WHERE o.crop_id = ANY($1::int[]) {open} ORDER BY lower(o.during), o.id"
)


def _cell_order(label: str | None) -> tuple[str, int]:
    if label is None:
        return ("", -1)
    match = _NUMBER.search(label)
    return (label[: match.start()], int(match.group(1))) if match else (label, -1)


def _place(row: Mapping[str, Any]) -> Place:
    return Place(
        zone=row["zone_slug"],
        shelf=row["shelf_slug"],
        cell=row["cell"],
        since=row["since"],
        until=row["until"],
        count=row["count"],
    )


def _planting(row: Mapping[str, Any], places: Sequence[Place]) -> Planting:
    cells = sorted({p.cell for p in places if p.cell}, key=_cell_order)
    shelves = list(dict.fromkeys(p.shelf for p in places if p.shelf))
    zones = list(dict.fromkeys(p.zone for p in places))
    plain = [p.shelf or p.zone for p in places if not p.cell]
    where = ", ".join(x for x in (compress_labels(cells) if cells else "", *dict.fromkeys(plain)) if x) or None
    source, actor = row["source"], row["actor"]
    return Planting(
        id=row["id"],
        crop=row["name"],
        variety=row["variety"],
        crop_catalog_slug=row["crop_catalog_slug"],
        count=row["count"],
        stage=row["stage"],
        planted_date=row["planted_date"],
        active=row["is_active"],
        ended_at=row["cleared_at"],
        end_reason=row["end_reason"],
        notes=row["notes"],
        recorded_by=ActorRef(source=source, name=actor) if source and actor else None,
        zones=zones,
        shelves=shelves,
        cells=cells,
        where=where,
        places=list(places),
    )


async def _open_places(conn: asyncpg.Connection, ids: Sequence[int]) -> dict[int, list[Place]]:
    out: dict[int, list[Place]] = {i: [] for i in ids}
    for row in await conn.fetch(_PLACES_SQL.format(open="AND upper_inf(o.during)"), list(ids)):
        out[row["crop_id"]].append(_place(row))
    return out


async def _read_planting(conn: asyncpg.Connection, planting_id: int) -> Planting | None:
    row = await conn.fetchrow(
        f"SELECT {_PLANTING_COLUMNS} {_PLANTING_FROM} WHERE c.id = $1 AND c.greenhouse_id = $2",
        planting_id,
        GREENHOUSE_ID,
    )
    if row is None:
        return None
    return _planting(row, (await _open_places(conn, [planting_id]))[planting_id])


def _display_name(common_name: str | None, slug: str) -> str:
    return _CATALOG_SUFFIX.sub("", common_name or "").strip() or slug.replace("_", " ").capitalize()


async def _snapshot(conn: asyncpg.Connection):
    if conn.is_in_transaction():
        raise RuntimeError("crops reads open their own snapshot; call them outside a transaction")
    return conn.transaction(isolation="repeatable_read", readonly=True)


def _planting_id(value: object, field: str = "planting_id") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**31 - 1:
        raise ServiceError(problem("invalid_request", f"{field} is a planting id", field=field))
    return value


# ── Reads ───────────────────────────────────────────────────────────────────


async def locations(conn: asyncpg.Connection, *, zone: str | None = None) -> Locations:
    """API-CROP-01 and MCP ``crop_locations``: every zone, shelf and cell with its layout, its water lines and
    what it holds now. ``zone`` keeps one zone (``unknown_target`` for another name)."""
    async with await _snapshot(conn):
        tree = await load_tree(conn)
        now = await conn.fetchval("SELECT now()")
        if zone is not None and zone not in {z.slug for z in tree.zones}:
            raise ServiceError(problem("unknown_target", f"No zone '{zone}'", field="zone"))
        rows = await conn.fetch(
            "SELECT o.zone_id, o.shelf_id, o.position_id, lower(o.during) AS since, o.count, c.id, c.name, c.variety "
            "FROM public.crop_occupancy o JOIN public.crops c ON c.id = o.crop_id "
            "WHERE upper_inf(o.during) AND o.greenhouse_id = $1 ORDER BY lower(o.during), o.id",
            GREENHOUSE_ID,
        )
        lines = await conn.fetch(
            "SELECT w.shelf_id, w.line_id, l.name, w.note FROM public.shelf_water_lines w "
            "JOIN public.irrigation_line l ON l.line_id = w.line_id ORDER BY w.shelf_id, w.line_id"
        )
        active = await conn.fetchval(
            "SELECT count(*) FROM public.crops WHERE is_active AND greenhouse_id = $1", GREENHOUSE_ID
        )
    by_cell: dict[int, Occupant] = {}
    by_shelf: dict[int, list[Occupant]] = {}
    by_zone: dict[int, list[Occupant]] = {}
    for row in rows:
        occupant = Occupant(
            planting_id=row["id"], crop=row["name"], variety=row["variety"], since=row["since"], count=row["count"]
        )
        if row["position_id"] is not None:
            by_cell[row["position_id"]] = occupant
        elif row["shelf_id"] is not None:
            by_shelf.setdefault(row["shelf_id"], []).append(occupant)
        else:
            by_zone.setdefault(row["zone_id"], []).append(occupant)
    water: dict[int, list[WaterLine]] = {}
    for row in lines:
        water.setdefault(row["shelf_id"], []).append(
            WaterLine(line_id=row["line_id"], name=row["name"], note=row["note"])
        )
    zones = []
    cell_count = occupied = 0
    for z in tree.zones:
        if zone is not None and z.slug != zone:
            continue
        shelves = []
        for shelf in tree.shelves:
            if shelf.zone.id != z.id:
                continue
            cells = [
                CellView(
                    label=cell.label,
                    number=cell.number,
                    mount_type=cell.mount_type,
                    layout_group=cell.layout_group,
                    layout_row=cell.layout_row,
                    layout_col=cell.layout_col,
                    occupant=by_cell.get(cell.id),
                )
                for cell in tree.cells
                if cell.shelf.id == shelf.id
            ]
            cell_count += len(cells)
            occupied += sum(1 for cell in cells if cell.occupant is not None)
            shelves.append(
                ShelfView(
                    slug=shelf.slug,
                    name=shelf.name,
                    kind=shelf.kind,
                    tier=shelf.tier,
                    wall=shelf.wall,
                    display_order=shelf.display_order,
                    label_short=shelf.label_short,
                    water_lines=water.get(shelf.id, []),
                    plantings=by_shelf.get(shelf.id, []),
                    cells=cells,
                )
            )
        zones.append(ZoneView(slug=z.slug, name=z.name, plantings=by_zone.get(z.id, []), shelves=shelves))
    return Locations(
        zones=zones, cell_count=cell_count, occupied_cells=occupied, active_plantings=active, server_time=now
    )


async def plantings(
    conn: asyncpg.Connection,
    *,
    active: bool | None = True,
    zone: str | None = None,
    since: date | None = None,
    limit: int = 200,
) -> PlantingList:
    """API-CROP-02: plantings newest first. ``active`` true (default) keeps the active ones, false the ended ones,
    None both; ``zone`` keeps those that were ever in that zone; ``since`` those planted or ended on or after it."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIMIT_MAX:
        raise ServiceError(problem("invalid_request", f"limit must be 1-{LIMIT_MAX}", field="limit"))
    clauses, args = ["c.greenhouse_id = $1"], [GREENHOUSE_ID]
    if active is not None:
        args.append(active)
        clauses.append(f"coalesce(c.is_active, false) = ${len(args)}")
    if zone is not None:
        args.append(zone)
        clauses.append(
            f"(c.zone = ${len(args)} OR EXISTS (SELECT 1 FROM public.crop_occupancy o JOIN public.zones z "
            f"ON z.id = o.zone_id WHERE o.crop_id = c.id AND z.slug = ${len(args)}))"
        )
    if since is not None:
        args.append(since)
        clauses.append(
            f"(c.planted_date >= ${len(args)} OR (c.cleared_at AT TIME ZONE 'America/Denver')::date >= ${len(args)})"
        )
    args.append(limit)
    sql = (
        f"SELECT {_PLANTING_COLUMNS} {_PLANTING_FROM} WHERE {' AND '.join(clauses)} "
        f"ORDER BY c.is_active DESC NULLS LAST, c.planted_date DESC, c.id DESC LIMIT ${len(args)}"
    )
    async with await _snapshot(conn):
        rows = await conn.fetch(sql, *args)
        places = await _open_places(conn, [row["id"] for row in rows])
    return PlantingList(items=[_planting(row, places[row["id"]]) for row in rows])


async def planting(conn: asyncpg.Connection, planting_id: int) -> PlantingDetail:
    """API-CROP-03: one planting with every place it has been, its events and its harvests; 404 for an unknown id."""
    key = _planting_id(planting_id)
    async with await _snapshot(conn):
        current = await _read_planting(conn, key)
        if current is None:
            raise ServiceError(problem("not_found", f"No planting {key}", field="planting_id"))
        history = [_place(row) for row in await conn.fetch(_PLACES_SQL.format(open=""), [key])]
        events = await conn.fetch(
            "SELECT id, ts, event_type, count, notes, source, operator, detail::text AS detail "
            "FROM public.crop_events WHERE crop_id = $1 ORDER BY ts DESC NULLS LAST, id DESC LIMIT 200",
            key,
        )
        harvests = await conn.fetch(
            "SELECT id, ts, unit_count, weight_kg, notes, operator FROM public.harvests WHERE crop_id = $1 "
            "ORDER BY ts DESC NULLS LAST, id DESC LIMIT 200",
            key,
        )
    return PlantingDetail(
        **current.model_dump(),
        history=history,
        events=[
            PlantingEvent(**{**dict(row), "detail": json.loads(row["detail"]) if row["detail"] else None})
            for row in events
        ],
        harvests=[HarvestRecord(**dict(row)) for row in harvests],
    )


async def position_history(conn: asyncpg.Connection, label: str) -> PositionHistory:
    """API-CROP-04: who has held one cell, current first; 404 for an unknown label."""
    async with await _snapshot(conn):
        tree = await load_tree(conn)
        cell = next((c for c in tree.cells if c.label == str(label).upper()), None)
        if cell is None:
            raise ServiceError(problem("not_found", f"No cell '{label}'", field="label"))
        rows = await conn.fetch(
            "SELECT o.count, lower(o.during) AS since, upper(o.during) AS until, c.id, c.name, c.variety "
            "FROM public.crop_occupancy o JOIN public.crops c ON c.id = o.crop_id WHERE o.position_id = $1 "
            "ORDER BY lower(o.during) DESC, o.id DESC LIMIT 200",
            cell.id,
        )
    items = [
        Tenure(
            planting_id=r["id"],
            crop=r["name"],
            variety=r["variety"],
            since=r["since"],
            until=r["until"],
            count=r["count"],
        )
        for r in rows
    ]
    items.sort(key=lambda t: t.until is not None)  # stable: the open one first, the rest newest first
    return PositionHistory(label=cell.label, zone=cell.shelf.zone.slug, shelf=cell.shelf.slug, items=items)


async def catalog(conn: asyncpg.Connection) -> Catalog:
    """API-CROP-05: the crop catalog with plain names (Lettuce, not the lab page title)."""
    rows = await conn.fetch("SELECT slug, common_name, category FROM public.crop_catalog ORDER BY slug")
    return Catalog(
        items=[
            CatalogEntry(slug=r["slug"], name=_display_name(r["common_name"], r["slug"]), category=r["category"])
            for r in rows
        ]
    )


async def _conflicts(
    conn: asyncpg.Connection, targets: Sequence[Target], at: datetime, exclude: int | None = None
) -> list[Conflict]:
    cells = [t.cell.id for t in targets if t.cell is not None]
    if not cells:
        return []
    rows = await conn.fetch(
        "SELECT p.label, o.crop_id, c.name, lower(o.during) AS since, upper(o.during) AS until "
        "FROM public.crop_occupancy o JOIN public.positions p ON p.id = o.position_id "
        "JOIN public.crops c ON c.id = o.crop_id "
        "WHERE o.position_id = ANY($1::int[]) AND o.during && tstzrange($2, NULL, '[)') "
        "AND ($3::int IS NULL OR o.crop_id <> $3) ORDER BY p.label, lower(o.during)",
        cells,
        at,
        exclude,
    )
    return [
        Conflict(cell=r["label"], planting_id=r["crop_id"], crop=r["name"], since=r["since"], until=r["until"])
        for r in rows
    ]


def _moment(value: datetime | date | None, now: datetime, field: str) -> datetime:
    if value is None:
        return now
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ServiceError(problem("invalid_request", f"{field} needs its UTC offset", field=field))
        moment = value
    else:
        moment = datetime.combine(value, time(0), tzinfo=LOCAL)
    if moment > now + FUTURE_SKEW:
        raise ServiceError(problem("invalid_request", f"{field} is in the future", field=field))
    return moment


async def resolve(
    conn: asyncpg.Connection,
    positions: str | Sequence[str],
    *,
    at: datetime | date | None = None,
    planting_id: int | None = None,
) -> Resolution:
    """API-CROP-06 and the MCP dry runs: the places ``positions`` names and the plantings holding those cells at
    ``at`` (default now), leaving ``planting_id`` out (a move's own cells). Writes nothing."""
    async with await _snapshot(conn):
        tree = await load_tree(conn)
        now = await conn.fetchval("SELECT now()")
        moment = _moment(at, now, "at")
        targets = resolve_positions(tree, positions)
        conflicts = await _conflicts(conn, targets, moment, planting_id)
    return _resolution(targets, moment, conflicts)


def _resolution(targets: Sequence[Target], at: datetime, conflicts: Sequence[Conflict]) -> Resolution:
    cells = sorted((t.cell.label for t in targets if t.cell), key=_cell_order)
    plain = [t.label for t in targets if t.cell is None]
    return Resolution(
        places=[
            ResolvedPlace(
                zone=t.zone.slug, shelf=t.shelf.slug if t.shelf else None, cell=t.cell.label if t.cell else None
            )
            for t in targets
        ],
        cells=cells,
        where=", ".join(x for x in (compress_labels(cells) if cells else "", *plain) if x),
        at=at,
        conflicts=list(conflicts),
    )


# ── Writes ──────────────────────────────────────────────────────────────────


def _parsed[B: BaseModel](model: type[B], body: B | Mapping[str, Any]) -> B:
    if isinstance(body, model):
        return body
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error.get("loc", ())) or None
        raise ServiceError(problem("invalid_request", f"{field}: {error.get('msg')}", field=field)) from exc


async def _lock(conn: asyncpg.Connection) -> None:
    await conn.execute(_LOCK_SQL, GREENHOUSE_ID)


async def _write_context(conn: asyncpg.Connection) -> tuple[Tree, datetime]:
    return await load_tree(conn), await conn.fetchval("SELECT now()")


async def _active(conn: asyncpg.Connection, planting_id: int) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, name, is_active, count, zone, position FROM public.crops "
        "WHERE id = $1 AND greenhouse_id = $2 FOR UPDATE",
        planting_id,
        GREENHOUSE_ID,
    )
    if row is None:
        raise ServiceError(problem("unknown_target", f"No planting {planting_id}", field="planting_id"))
    if not row["is_active"]:
        raise ServiceError(
            problem("planting_ended", f"Planting {planting_id} ({row['name']}) has ended", field="planting_id")
        )
    return row


async def _open_rows(conn: asyncpg.Connection, planting_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT o.id, o.zone_id, o.shelf_id, o.position_id, lower(o.during) AS since, o.count, "
        "z.slug AS zone_slug, s.slug AS shelf_slug, p.label AS cell "
        "FROM public.crop_occupancy o JOIN public.zones z ON z.id = o.zone_id "
        "LEFT JOIN public.shelves s ON s.id = o.shelf_id LEFT JOIN public.positions p ON p.id = o.position_id "
        "WHERE o.crop_id = $1 AND upper_inf(o.during) ORDER BY o.id FOR UPDATE OF o",
        planting_id,
    )


def _row_label(row: Mapping[str, Any]) -> str:
    return row["cell"] or row["shelf_slug"] or row["zone_slug"]


async def _close(conn: asyncpg.Connection, rows: Sequence[Mapping[str, Any]], at: datetime, field: str) -> None:
    for row in rows:
        if row["since"] >= at:
            raise ServiceError(
                problem(
                    "invalid_request",
                    f"{field} is not after the planting reached {_row_label(row)} ({row['since'].isoformat()})",
                    field=field,
                )
            )
    if rows:
        await conn.execute(
            "UPDATE public.crop_occupancy SET during = tstzrange(lower(during), $2, '[)') WHERE id = ANY($1::bigint[])",
            [row["id"] for row in rows],
            at,
        )


async def _open(
    conn: asyncpg.Connection, planting_id: int, targets: Sequence[Target], at: datetime, count: int | None
) -> None:
    try:
        await conn.executemany(
            "INSERT INTO public.crop_occupancy (greenhouse_id, crop_id, zone_id, shelf_id, position_id, during, count) "
            "VALUES ($1, $2, $3, $4, $5, tstzrange($6, NULL, '[)'), $7)",
            [
                (
                    GREENHOUSE_ID,
                    planting_id,
                    t.zone.id,
                    t.shelf.id if t.shelf else None,
                    t.cell.id if t.cell else None,
                    at,
                    count if t.cell is not None else None,
                )
                for t in targets
            ],
        )
    except asyncpg.exceptions.ExclusionViolationError as exc:
        raise ServiceError(
            problem("position_occupied", "A requested cell is already held at that time; nothing was written")
        ) from exc


async def _sync_anchor(conn: asyncpg.Connection, planting_id: int) -> None:
    """Keep ``crops.position_id`` (the anchor: the lowest-numbered open cell), ``zone_id``, ``zone`` and the
    free-text ``position`` in step with the open occupancy, for the readers that predate 293."""
    rows = await conn.fetch(
        "SELECT o.zone_id, z.slug AS zone_slug, o.position_id, p.label AS cell, s.slug AS shelf_slug "
        "FROM public.crop_occupancy o JOIN public.zones z ON z.id = o.zone_id "
        "LEFT JOIN public.shelves s ON s.id = o.shelf_id LEFT JOIN public.positions p ON p.id = o.position_id "
        "WHERE o.crop_id = $1 AND upper_inf(o.during) ORDER BY o.id",
        planting_id,
    )
    if not rows:
        return
    cells = sorted((r for r in rows if r["cell"]), key=lambda r: _cell_order(r["cell"]))
    anchor = cells[0] if cells else None
    first = anchor or rows[0]
    where = compress_labels([r["cell"] for r in cells]) if cells else ""
    plain = [r["shelf_slug"] or r["zone_slug"] for r in rows if not r["cell"]]
    text = ", ".join(x for x in (where, *dict.fromkeys(plain)) if x)
    await conn.execute(
        "UPDATE public.crops SET position_id = $2, zone_id = $3, zone = $4, position = $5, updated_at = now() "
        "WHERE id = $1 AND (position_id IS DISTINCT FROM $2 OR zone_id IS DISTINCT FROM $3 "
        "OR zone IS DISTINCT FROM $4 OR position IS DISTINCT FROM $5)",
        planting_id,
        anchor["position_id"] if anchor else None,
        first["zone_id"],
        first["zone_slug"],
        text[:500],
    )


async def _end_row(conn: asyncpg.Connection, planting_id: int, at: datetime, reason: str) -> None:
    await conn.execute(
        "UPDATE public.crops SET is_active = false, cleared_at = $2, end_reason = $3, updated_at = now() "
        "WHERE id = $1 AND is_active",
        planting_id,
        at,
        reason,
    )


async def _vacate(
    conn: asyncpg.Connection, conflicts: Sequence[Conflict], at: datetime, field: str
) -> list[dict[str, Any]]:
    """``replace``: end each occupant's open range on a requested cell at ``at``; an occupant with no place left
    ends as ``removed``. A range that starts at or after ``at``, or one already closed, cannot be replaced."""
    vacated = []
    for conflict in conflicts:
        if conflict.until is not None or conflict.since >= at:
            raise ServiceError(
                problem(
                    "position_occupied",
                    f"{conflict.cell} holds planting {conflict.planting_id} ({conflict.crop}) from "
                    f"{conflict.since.isoformat()}, which replace cannot shorten; choose a later time",
                    field=field,
                    current=conflict.cell,
                )
            )
    for planting_id in dict.fromkeys(c.planting_id for c in conflicts):
        cells = [c.cell for c in conflicts if c.planting_id == planting_id]
        rows = [r for r in await _open_rows(conn, planting_id) if r["cell"] in cells]
        await _close(conn, rows, at, field)
        left = await conn.fetchval(
            "SELECT count(*) FROM public.crop_occupancy WHERE crop_id = $1 AND upper_inf(during)", planting_id
        )
        if left:
            await _sync_anchor(conn, planting_id)
        else:
            await _end_row(conn, planting_id, at, "removed")
        vacated.append({"planting_id": planting_id, "cells": cells, "ended": not left})
    return vacated


def _occupied(conflicts: Sequence[Conflict], field: str) -> ServiceError:
    cells = compress_labels(c.cell for c in conflicts)
    held = "; ".join(
        f"{compress_labels(c.cell for c in conflicts if c.planting_id == pid)}: planting {pid} "
        f"({next(c.crop for c in conflicts if c.planting_id == pid)})"
        for pid in dict.fromkeys(c.planting_id for c in conflicts)
    )
    return ServiceError(
        problem(
            "position_occupied",
            f"Already planted: {held}. Send replace to end those first; nothing was written",
            field=field,
            current=cells,
        )
    )


async def _event(
    conn: asyncpg.Connection,
    actor: Actor,
    planting_id: int,
    event_type: str,
    at: datetime,
    *,
    count: int | None = None,
    notes: str | None = None,
    detail: Mapping[str, Any] | None = None,
    request_id: UUID | None = None,
) -> int:
    anchor = await conn.fetchval("SELECT position_id FROM public.crops WHERE id = $1", planting_id)
    return await conn.fetchval(
        "INSERT INTO public.crop_events (ts, crop_id, event_type, count, operator, source, notes, greenhouse_id, "
        "position_id, request_id, detail) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::text::jsonb) "
        "RETURNING id",
        at,
        planting_id,
        event_type,
        count,
        actor.name,
        actor.source,
        notes,
        GREENHOUSE_ID,
        anchor,
        request_id,
        None if detail is None else json.dumps(detail, sort_keys=True, default=str),
    )


def _request_key(request_id: UUID | str | None) -> UUID | None:
    if request_id is None or isinstance(request_id, UUID):
        return request_id
    try:
        return UUID(str(request_id))
    except ValueError:
        return None  # recorded_write refuses it with invalid_request


async def _load_ref(conn: asyncpg.Connection, row: ActivityLogRow) -> Planting:
    ref = row.detail.get("ref") or {}
    stored = await _read_planting(conn, int(ref.get("id", 0)))
    if stored is None:
        raise RuntimeError(f"planting {ref.get('id')} named by activity row {row.id} is gone")
    return stored


def _iso(value: datetime) -> str:
    return value.isoformat()


async def plant(
    conn: asyncpg.Connection,
    actor: Actor | None,
    body: PlantingCreate | Mapping[str, Any],
    *,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """API-CROP-07 and MCP ``record_planting``: one planting across every place ``positions`` names. A cell held at
    ``planted_at`` is ``position_occupied`` (with the cells) unless ``replace``. Returns ``WriteResult[Planting]``."""
    body = _parsed(PlantingCreate, body)
    request = body.model_dump(mode="json", exclude_none=True)

    async def perform(conn: asyncpg.Connection) -> tuple[Planting, dict[str, Any]]:
        tree, now = await _write_context(conn)
        at = _moment(body.planted_at, now, "planted_at")
        targets = resolve_positions(tree, body.positions)
        conflicts = await _conflicts(conn, targets, at)
        if conflicts and not body.replace:
            raise _occupied(conflicts, "positions")
        vacated = await _vacate(conn, conflicts, at, "positions") if conflicts else []
        crop = body.crop
        catalog_id = None
        key = crop.lower().strip()
        for entry in await conn.fetch("SELECT id, slug, common_name FROM public.crop_catalog"):
            name = _display_name(entry["common_name"], entry["slug"])
            names = {entry["slug"].lower(), name.lower()}
            if key in names or key.rstrip("s") in {n.rstrip("s") for n in names}:
                crop, catalog_id = name, entry["id"]
                break
        first = targets[0]
        columns = {
            "name": crop,
            "variety": body.variety,
            "zone": first.zone.slug,
            "position": first.label,
            "planted_date": at.astimezone(LOCAL).date(),
            "count": body.count,
            "notes": body.notes,
            "greenhouse_id": GREENHOUSE_ID,
            "crop_catalog_id": catalog_id,
            "zone_id": first.zone.id,
            "source": actor.source,
            "actor": actor.name,
            "is_active": True,
        }
        if body.stage is not None:
            columns["stage"] = body.stage
        names = ", ".join(columns)
        marks = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
        planting_id = await conn.fetchval(
            f"INSERT INTO public.crops ({names}) VALUES ({marks}) RETURNING id", *columns.values()
        )
        await _open(conn, planting_id, targets, at, body.count_per_cell)
        await _sync_anchor(conn, planting_id)
        stored = await _read_planting(conn, planting_id)
        detail = {
            "ref": {"table": "crops", "id": planting_id},
            "crop": crop,
            "variety": body.variety,
            "where": stored.where,
            "cells": stored.cells,
            "planted_at": _iso(at),
            "replaced": vacated,
        }
        return stored, detail

    return await activity.recorded_write(
        conn,
        actor,
        op="API-CROP-07",
        action="planting.create",
        target="new",
        request=request,
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        load=_load_ref,
        lock=_lock,
        target_of=lambda value: str(value.id),
    )


def _matching(rows: Sequence[Mapping[str, Any]], targets: Sequence[Target], field: str) -> list[Mapping[str, Any]]:
    """The open rows at ``targets``: a cell matches its cell row; a shelf or zone every open row inside it."""
    chosen: dict[int, Mapping[str, Any]] = {}
    for target in targets:
        found = [
            row
            for row in rows
            if row["zone_id"] == target.zone.id
            and (target.shelf is None or row["shelf_id"] == target.shelf.id)
            and (target.cell is None or row["position_id"] == target.cell.id)
        ]
        if not found:
            raise ServiceError(problem("invalid_request", f"The planting is not at {target.label}", field=field))
        for row in found:
            chosen[row["id"]] = row
    return list(chosen.values())


async def move(
    conn: asyncpg.Connection,
    actor: Actor | None,
    planting_id: int,
    body: PlantingMove | Mapping[str, Any],
    *,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """API-CROP-08 and MCP ``move_planting``: close ``from_positions`` (default: every open place) at ``moved_at``
    and open ``to_positions`` then; both ranges stay in history. Returns ``WriteResult[Planting]``."""
    key = _planting_id(planting_id)
    body = _parsed(PlantingMove, body)
    request = body.model_dump(mode="json", exclude_none=True)

    async def perform(conn: asyncpg.Connection) -> tuple[Planting, dict[str, Any]]:
        await _active(conn, key)
        tree, now = await _write_context(conn)
        at = _moment(body.moved_at, now, "moved_at")
        rows = await _open_rows(conn, key)
        leaving = (
            _matching(rows, resolve_positions(tree, body.from_positions, field="from_positions"), "from_positions")
            if body.from_positions
            else rows
        )
        targets = resolve_positions(tree, body.to_positions, field="to_positions")
        staying = {row["position_id"] for row in rows if row not in leaving and row["position_id"] is not None}
        if any(t.cell is not None and t.cell.id in staying for t in targets):
            raise ServiceError(
                problem("invalid_request", "The planting already holds a destination cell", field="to_positions")
            )
        conflicts = await _conflicts(conn, targets, at, key)
        if conflicts and not body.replace:
            raise _occupied(conflicts, "to_positions")
        vacated = await _vacate(conn, conflicts, at, "to_positions") if conflicts else []
        counts = {row["count"] for row in leaving if row["position_id"] is not None}
        await _close(conn, leaving, at, "moved_at")
        await _open(conn, key, targets, at, counts.pop() if len(counts) == 1 else None)
        await _sync_anchor(conn, key)
        moved = {
            "from": [_row_label(row) for row in leaving],
            "to": [t.label for t in targets],
            "replaced": vacated,
        }
        await _event(
            conn, actor, key, "moved", at, notes=body.reason, detail=moved, request_id=_request_key(request_id)
        )
        stored = await _read_planting(conn, key)
        return stored, {"ref": {"table": "crops", "id": key}, "moved_at": _iso(at), **moved}

    return await activity.recorded_write(
        conn,
        actor,
        op="API-CROP-08",
        action="planting.move",
        target=str(key),
        request=request,
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        load=_load_ref,
        lock=_lock,
    )


async def harvest(
    conn: asyncpg.Connection,
    actor: Actor | None,
    planting_id: int,
    body: PlantingHarvest | Mapping[str, Any],
    *,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """API-CROP-09 and MCP ``harvest_planting``: one ``harvests`` row and a ``harvested`` event. Partial by default;
    the cells in ``positions`` are cleared, and ``final`` (or clearing its last place) ends the planting as harvested.
    Returns ``WriteResult[Planting]``."""
    key = _planting_id(planting_id)
    body = _parsed(PlantingHarvest, body)
    request = body.model_dump(mode="json", exclude_none=True)

    async def perform(conn: asyncpg.Connection) -> tuple[Planting, dict[str, Any]]:
        crop = await _active(conn, key)
        tree, now = await _write_context(conn)
        at = _moment(body.harvested_at, now, "harvested_at")
        rows = await _open_rows(conn, key)
        clearing = rows if body.final else []
        if body.positions and not body.final:
            clearing = _matching(rows, resolve_positions(tree, body.positions), "positions")
        await _close(conn, clearing, at, "harvested_at")
        ended = body.final or (bool(clearing) and len(clearing) == len(rows))
        cells = [_row_label(row) for row in clearing]
        harvest_id = await conn.fetchval(
            "INSERT INTO public.harvests (ts, crop_id, unit_count, weight_kg, zone, operator, notes, greenhouse_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
            at,
            key,
            body.count,
            body.weight_kg,
            crop["zone"],
            actor.name,
            body.notes,
            GREENHOUSE_ID,
        )
        detail = {"harvest_id": harvest_id, "cleared": cells, "final": ended, "weight_kg": body.weight_kg}
        await _event(
            conn,
            actor,
            key,
            "harvested",
            at,
            count=body.count,
            notes=body.notes,
            detail=detail,
            request_id=_request_key(request_id),
        )
        if ended:
            await _close(conn, [row for row in rows if row not in clearing], at, "harvested_at")
            await _end_row(conn, key, at, "harvested")
        else:
            await _sync_anchor(conn, key)
        stored = await _read_planting(conn, key)
        return stored, {"ref": {"table": "crops", "id": key}, "harvested_at": _iso(at), "count": body.count, **detail}

    return await activity.recorded_write(
        conn,
        actor,
        op="API-CROP-09",
        action="planting.harvest",
        target=str(key),
        request=request,
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        load=_load_ref,
        lock=_lock,
    )


async def end(
    conn: asyncpg.Connection,
    actor: Actor | None,
    planting_id: int,
    body: PlantingEnd | Mapping[str, Any],
    *,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """API-CROP-10 and MCP ``remove_planting``: every open place closes at ``at`` and the planting ends with
    ``end_reason`` (failed, removed or reconciled). Returns ``WriteResult[Planting]``; undo by planting again."""
    key = _planting_id(planting_id)
    body = _parsed(PlantingEnd, body)
    request = body.model_dump(mode="json", exclude_none=True)

    async def perform(conn: asyncpg.Connection) -> tuple[Planting, dict[str, Any]]:
        await _active(conn, key)
        now = await conn.fetchval("SELECT now()")
        at = _moment(body.at, now, "at")
        rows = await _open_rows(conn, key)
        await _close(conn, rows, at, "at")
        places = [_row_label(row) for row in rows]
        if body.note or body.end_reason == "reconciled":
            await _event(
                conn,
                actor,
                key,
                "reconciled" if body.end_reason == "reconciled" else "note",
                at,
                notes=body.note,
                detail={"end_reason": body.end_reason, "places": places},
                request_id=_request_key(request_id),
            )
        await _end_row(conn, key, at, body.end_reason)
        stored = await _read_planting(conn, key)
        return stored, {
            "ref": {"table": "crops", "id": key},
            "end_reason": body.end_reason,
            "at": _iso(at),
            "places": places,
        }

    return await activity.recorded_write(
        conn,
        actor,
        op="API-CROP-10",
        action="planting.end",
        target=str(key),
        request=request,
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        load=_load_ref,
        lock=_lock,
    )


_PATCH_FIELDS = ("variety", "count", "stage", "notes", "expected_harvest")


async def update(
    conn: asyncpg.Connection,
    actor: Actor | None,
    planting_id: int,
    body: PlantingPatch | Mapping[str, Any],
    *,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """API-CROP-11: change the fields sent (variety, count, stage, notes, expected_harvest); ``count`` null is
    unknown. An ended planting's record can still be corrected. Returns ``WriteResult[Planting]``."""
    key = _planting_id(planting_id)
    body = _parsed(PlantingPatch, body)
    fields = [name for name in _PATCH_FIELDS if name in body.model_fields_set]
    if not fields:
        raise ServiceError(problem("invalid_request", "Send at least one field to change", field="body"))
    request = body.model_dump(mode="json", include={*fields, "reason"})

    async def perform(conn: asyncpg.Connection) -> tuple[Planting, dict[str, Any]]:
        before = await conn.fetchrow(
            f"SELECT {', '.join(fields)} FROM public.crops WHERE id = $1 AND greenhouse_id = $2 FOR UPDATE",
            key,
            GREENHOUSE_ID,
        )
        if before is None:
            raise ServiceError(problem("unknown_target", f"No planting {key}", field="planting_id"))
        sets = ", ".join(f"{name} = ${i}" for i, name in enumerate(fields, start=2))
        await conn.execute(
            f"UPDATE public.crops SET {sets}, updated_at = now() WHERE id = $1",
            key,
            *(getattr(body, name) for name in fields),
        )
        stored = await _read_planting(conn, key)
        after = body.model_dump(mode="json", include=set(fields))
        return stored, {
            "ref": {"table": "crops", "id": key},
            "before": {name: _json_value(before[name]) for name in fields},
            "after": after,
        }

    return await activity.recorded_write(
        conn,
        actor,
        op="API-CROP-11",
        action="planting.update",
        target=str(key),
        request=request,
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        load=_load_ref,
        lock=_lock,
    )


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


async def annotate(
    conn: asyncpg.Connection,
    actor: Actor | None,
    planting_id: int,
    body: PlantingNote | Mapping[str, Any],
    *,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """MCP ``annotate_planting``: a ``note`` event on the planting's history (ended plantings too). Returns
    ``WriteResult[PlantingEvent]``."""
    key = _planting_id(planting_id)
    body = _parsed(PlantingNote, body)
    request = body.model_dump(mode="json", exclude_none=True)

    async def perform(conn: asyncpg.Connection) -> tuple[PlantingEvent, dict[str, Any]]:
        exists = await conn.fetchval(
            "SELECT true FROM public.crops WHERE id = $1 AND greenhouse_id = $2", key, GREENHOUSE_ID
        )
        if not exists:
            raise ServiceError(problem("unknown_target", f"No planting {key}", field="planting_id"))
        now = await conn.fetchval("SELECT now()")
        event_id = await _event(conn, actor, key, "note", now, notes=body.note, request_id=_request_key(request_id))
        return await _load_event(conn, event_id), {"ref": {"table": "crop_events", "id": event_id}, "planting_id": key}

    async def load(conn: asyncpg.Connection, row: ActivityLogRow) -> PlantingEvent:
        return await _load_event(conn, int(row.detail["ref"]["id"]))

    return await activity.recorded_write(
        conn,
        actor,
        op="MCP-annotate_planting",
        action="planting.note",
        target=str(key),
        request=request,
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        load=load,
        lock=_lock,
    )


async def _load_event(conn: asyncpg.Connection, event_id: int) -> PlantingEvent:
    row = await conn.fetchrow(
        "SELECT id, ts, event_type, count, notes, source, operator, detail::text AS detail "
        "FROM public.crop_events WHERE id = $1",
        event_id,
    )
    return PlantingEvent(**{**dict(row), "detail": json.loads(row["detail"]) if row["detail"] else None})
