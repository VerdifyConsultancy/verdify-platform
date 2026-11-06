"""The one position parser (verdify_services.crops.resolve_positions) on the live location tree's shape (#1327).

DB-free: the tree is built here as migration 293 leaves it (60 hydro cells on rails A-C in two rows, shelves T/B by
bay, floor pots and hanging hooks)."""

import pytest

from verdify_services.crops import Cell, Shelf, Tree, Zone, compress_labels, resolve_positions
from verdify_services.problems import ServiceError

CENTER, EAST, NORTH, SOUTH, WEST = (
    Zone(1, "center", "Center"),
    Zone(2, "east", "East"),
    Zone(3, "north", "North"),
    Zone(4, "south", "South"),
    Zone(5, "west", "West"),
)
SHELVES = (
    Shelf(1, "center_hang", "Hanging", "hang", None, CENTER),
    Shelf(2, "center_floor", "Center floor", "floor", 0, CENTER),
    Shelf(3, "east_hydro", "Hydro", "nft", None, EAST),
    Shelf(4, "east_shelf_top", "East top", "shelf", 1, EAST),
    Shelf(5, "east_shelf_bottom", "East bottom", "shelf", 0, EAST),
    Shelf(6, "south_shelf_top", "South top", "shelf", 1, SOUTH),
    Shelf(7, "south_shelf_bottom", "South bottom", "shelf", 0, SOUTH),
    Shelf(8, "south_floor", "South floor pots", "floor", 0, SOUTH),
    Shelf(9, "west_shelf_top", "West top", "shelf", 1, WEST),
    Shelf(10, "west_shelf_bottom", "West bottom", "shelf", 0, WEST),
)


def _tree() -> Tree:
    by_slug = {s.slug: s for s in SHELVES}
    cells, next_id = [], 1

    def add(label, shelf, group=None, row=0, col=None):
        nonlocal next_id
        cells.append(Cell(next_id, label, by_slug[shelf], None, group, row, col))
        next_id += 1

    for n in range(1, 61):
        col = (n - 1) % 30 + 1
        add(f"EAST-HYDRO-{n}", "east_hydro", "ABC"[(col - 1) // 10], int(n > 30), col)
    for shelf, prefix, bays in (("east", "EAST", 3), ("south", "SOUTH", 4), ("west", "WEST", 6)):
        for tier, letter in (("top", "T"), ("bottom", "B")):
            for bay in range(1, bays + 1):
                add(f"{prefix}-SHELF-{letter}{bay}", f"{shelf}_shelf_{tier}", row=int(letter == "B"), col=bay)
    for n in range(1, 5):
        add(f"SOUTH-FLOOR-{n}", "south_floor", col=n)
        add(f"CENTER-FLOOR-{n}", "center_floor", col=n)
    for n in (1, 2):
        add(f"CENTER-HANG-{n}", "center_hang", col=n)
    return Tree((CENTER, EAST, NORTH, SOUTH, WEST), SHELVES, tuple(cells))


TREE = _tree()


def labels(text):
    return [t.label for t in resolve_positions(TREE, text)]


def problem_of(text):
    with pytest.raises(ServiceError) as caught:
        resolve_positions(TREE, text)
    return caught.value.problem


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("EAST-HYDRO-12", ["EAST-HYDRO-12"]),
        ("east-hydro-12", ["EAST-HYDRO-12"]),
        ("EAST-HYDRO-1..3", ["EAST-HYDRO-1", "EAST-HYDRO-2", "EAST-HYDRO-3"]),
        ("hydro 1-3", ["EAST-HYDRO-1", "EAST-HYDRO-2", "EAST-HYDRO-3"]),
        ("hydro 3, hydro 1", ["EAST-HYDRO-3", "EAST-HYDRO-1"]),
        ("west top 1-3", ["WEST-SHELF-T1", "WEST-SHELF-T2", "WEST-SHELF-T3"]),
        ("south shelf bottom 2", ["SOUTH-SHELF-B2"]),
        ("south floor", ["south_floor"]),
        ("hanging", ["center_hang"]),
        ("center_hang", ["center_hang"]),
        ("center", ["center"]),
        ("north", ["north"]),
    ],
)
def test_reads_each_form(text, expected):
    assert labels(text) == expected


def test_hydro_ranges_and_rails():
    assert len(labels("hydro 1-30")) == 30
    assert labels("rail A top") == [f"EAST-HYDRO-{n}" for n in range(1, 11)]
    assert labels("rail C bottom") == [f"EAST-HYDRO-{n}" for n in range(51, 61)]
    assert len(labels("all hydro")) == 60
    assert len(labels("all south shelves")) == 8


def test_places_resolve_to_their_zone_and_shelf():
    (target,) = resolve_positions(TREE, "SOUTH-FLOOR-2")
    assert (target.zone.slug, target.shelf.slug, target.cell.label) == ("south", "south_floor", "SOUTH-FLOOR-2")
    (target,) = resolve_positions(TREE, "center")
    assert target.shelf is None and target.cell is None


def test_lists_and_repeats():
    assert labels(["hydro 1", "EAST-HYDRO-1", "hydro 2"]) == ["EAST-HYDRO-1", "EAST-HYDRO-2"]


@pytest.mark.parametrize("text", ["top 1", "south shelves", "floor"])
def test_ambiguous_input_names_its_candidates(text):
    found = problem_of(text)
    assert found.code == "ambiguous_position"
    assert found.current


@pytest.mark.parametrize("text", ["", "greenhouse", "hydro 61", "EAST-HYDRO-59..62", "hydro 1-999", "east south"])
def test_unreadable_input_is_refused(text):
    assert problem_of(text).code == "invalid_request"


def test_compress_labels():
    got = compress_labels(["EAST-HYDRO-3", "EAST-HYDRO-1", "EAST-HYDRO-2", "EAST-HYDRO-12", "WEST-SHELF-T1"])
    assert got == "EAST-HYDRO-1..3, EAST-HYDRO-12, WEST-SHELF-T1"
