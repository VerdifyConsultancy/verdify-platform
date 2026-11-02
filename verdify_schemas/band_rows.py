"""Crop band rows: which ``crop_band_anchors`` rows a band setting reads and writes (VD-67).

``crop_band_anchors`` is the band's single source of truth (architecture §5.3). The dispatcher overlays it on
the registry defaults (``ingestor/tasks/band_anchors.py``), the settings services write it in the same
transaction as the setting's ``desired_state`` row (``verdify_services.settings``), and the band preview (#1170)
reads it. They share the rules here, so a setting maps to the same rows everywhere:

* ``row_season_score``: a row for the current season beats an ``all``, ``any`` or empty one; a row for another
  season scores 0 and is never read.
* ``ZONE_CROP`` and ``CROP_TO_ZONE``: which crop each zone's VPD curve rides, with the tolerated aliases.
* ``WIDTH_COLUMN_CANDIDATES``: the width columns the dispatcher reads; ``WIDTH_COLUMNS`` the two a width write
  updates (the only width columns the runtime logins may UPDATE).
* ``BAND_PARAM_ROWS``: each band setting's crop types, series, anchor and column, and ``rows_in_force`` the
  ids of the rows it reads at the best season score present.

Pure: no database, clock or file reads. The api and MCP images carry ``verdify_schemas`` but not ``ingestor/``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

# ── Zone / crop / series shape (firmware-v2 contract §B2, §B7) ───────────────
ZONES = ("center", "south", "west", "east")
ANCHOR_KEYS = ("sr", "sm", "ss", "mid")
HOUSE_SERIES = ("temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high")
HOUSE_CROP = "house"
# A zone's crop carries only its VPD target curve; its widths ride the same rows.
ZONE_SERIES = "vpd_target"

# zone → crop_type in crop_band_anchors. center→orchid, south→cannabis,
# west→citrus, east→pepper; the house curve rides crop_type='house'.
ZONE_CROP = {"center": "orchid", "south": "cannabis", "west": "citrus", "east": "pepper"}

# Tolerated crop_type aliases (contract/docs use vanda/lime/cannabis-veg).
CROP_TO_ZONE = {
    "orchid": "center",
    "vanda": "center",
    "cannabis": "south",
    "cannabis-veg": "south",
    "citrus": "west",
    "lime": "west",
    "pepper": "east",
}

# The width columns the dispatcher reads, first non-null wins (§B7).
WIDTH_COLUMN_CANDIDATES = {
    "below": ("width_below", "vpd_width_below", "half_width_below"),
    "above": ("width_above", "vpd_width_above", "half_width_above"),
}
# The columns a width write updates: the first candidate of each side, which the runtime logins' column
# grants cover (UPDATE value, width_below and width_above on crop_band_anchors; migration 280 and M2).
WIDTH_COLUMNS = MappingProxyType({side: candidates[0] for side, candidates in WIDTH_COLUMN_CANDIDATES.items()})

BandColumn = Literal["value", "width_below", "width_above"]


def row_season_score(record: Mapping[str, Any], current_season: str | None) -> int:
    """How well one ``crop_band_anchors`` row's season matches ``current_season``.

    2 for the current season, 1 for ``all``, ``any`` or empty, 0 for another season. Without a current season
    every row scores 1. The dispatcher overlays rows in ascending score, so the best score present wins, and a
    row scoring 0 is never read.
    """
    season = str(record.get("season") or "").strip().lower()
    if current_season and season == current_season:
        return 2
    if season in ("", "all", "any"):
        return 1
    return 0 if current_season else 1


def _normalized(value: object) -> str:
    return str(value or "").strip().lower()


@dataclass(frozen=True)
class BandRows:
    """The ``crop_band_anchors`` rows one band setting reads and writes.

    ``crop_types`` holds every crop_type the dispatcher maps to the setting (the zone crop and its aliases, or
    ``house``); ``anchor`` is None for a width, which rides all four anchor rows of the zone crop's
    ``vpd_target`` series; ``column`` is the column the setting is.
    """

    param: str
    crop_types: tuple[str, ...]
    series: str
    anchor: str | None
    column: BandColumn
    zone: str | None = None

    def matches(self, record: Mapping[str, Any]) -> bool:
        """Is ``record`` one of this setting's rows (any season)? Matched as the dispatcher matches it."""
        if _normalized(record.get("crop_type")) not in self.crop_types:
            return False
        if _normalized(record.get("series")) != self.series:
            return False
        return self.anchor is None or _normalized(record.get("anchor")) == self.anchor


def _zone_crop_types(zone: str) -> tuple[str, ...]:
    return tuple(crop for crop, mapped in CROP_TO_ZONE.items() if mapped == zone)


def _band_param_rows() -> dict[str, BandRows]:
    rows: dict[str, BandRows] = {}
    for series in HOUSE_SERIES:
        for anchor in ANCHOR_KEYS:
            name = f"band_{series}_{anchor}"
            rows[name] = BandRows(name, (HOUSE_CROP,), series, anchor, "value")
    for zone in ZONES:
        crops = _zone_crop_types(zone)
        for anchor in ANCHOR_KEYS:
            name = f"zone_vpd_target_{zone}_{anchor}"
            rows[name] = BandRows(name, crops, ZONE_SERIES, anchor, "value", zone)
        for side, column in WIDTH_COLUMNS.items():
            name = f"zone_vpd_width_{side}_{zone}"
            rows[name] = BandRows(name, crops, ZONE_SERIES, None, column, zone)
    return rows


# The 48 band settings that live in crop_band_anchors: 24 house anchors, 16 zone VPD targets and 8 widths. The
# other crop_band rows (the zone priorities and the window values) have no crop_band_anchors row.
BAND_PARAM_ROWS: Mapping[str, BandRows] = MappingProxyType(_band_param_rows())
assert len(BAND_PARAM_ROWS) == 48, f"band row map drifted: {len(BAND_PARAM_ROWS)} != 48"


def band_rows(param: str) -> BandRows | None:
    """The rows ``param`` reads and writes, or None when it has no ``crop_band_anchors`` row."""
    return BAND_PARAM_ROWS.get(param)


def rows_in_force(rows: BandRows, records: Iterable[Mapping[str, Any]], current_season: str | None) -> tuple[int, ...]:
    """Ids of the rows the dispatcher reads for one setting: its rows at the best season score present.

    ``records`` are ``crop_band_anchors`` rows (``id``, ``crop_type``, ``series``, ``anchor``, ``season``).
    Empty when the setting has no row with a score above 0: then a write would change nothing the controller
    reads.
    """
    scored = [(row_season_score(record, current_season), record) for record in records if rows.matches(record)]
    best = max((score for score, _ in scored), default=0)
    if best <= 0:
        return ()
    return tuple(sorted(int(record["id"]) for score, record in scored if score == best))
