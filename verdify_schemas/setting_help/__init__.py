"""Operator text for every setting (#1332): what it is, what the controller does with it, and what raising or
lowering it does.

One source for three surfaces: the dashboard (API-09 ``Setting.help``, the Settings rows, drawer and glossary),
MCP ``settings`` (which serves API-09's rows) and the lab ai-tunables reference page
(``scripts/generate-ai-tunables-page.py``). ``TunableDef.notes`` stays the developer rationale.

* ``HELP``: a ``HelpEntry`` for every ``REGISTRY`` row. The hand-written parts are one module per group
  (``_planner``, ``_safety``, ``_scheduled``, ``_reference``); the 48 crop-band anchor rows are generated here from
  their series, anchor and zone (``verdify_schemas.band_rows``), so the 48 read alike.
* ``setting_help(name)``: the served ``SettingHelp``; ``setting_label(name)``: the label the catalog shows (the
  registry label, or the entry's where the registry label is the raw name).
* ``band_in_force_help(zone, role)`` and ``band_in_force_sources(zone, role)``: text and source settings for the
  30 read-only Band in force rows, which have no registry row.
* ``source_params(name)``: the writable settings a derived registry row is computed from.

``verdify_schemas/tests/test_setting_help.py`` keeps every row covered and every ``refs`` item pointing at code
that exists. Each entry's ``refs`` cite the code path; the served text never does.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from verdify_schemas.band_rows import ANCHOR_KEYS, BAND_PARAM_ROWS, HOUSE_SERIES, ZONE_CROP, ZONES
from verdify_schemas.setting_help import _planner, _reference, _safety, _scheduled
from verdify_schemas.setting_help._entry import HelpEntry
from verdify_schemas.tunable_registry import REGISTRY

__all__ = [
    "HELP",
    "LAB_REFERENCE_URL",
    "HelpEntry",
    "SettingHelp",
    "band_in_force_help",
    "band_in_force_sources",
    "lab_reference_url",
    "setting_help",
    "setting_label",
    "source_params",
]

# The generated lab page; each setting's entry carries its name as its HTML id.
LAB_REFERENCE_URL = "https://lab.verdify.ai/reference/ai-tunables/"


class SettingHelp(BaseModel):
    """A setting's operator text as API-09 and MCP serve it (OpenAPI ``SettingHelp``).

    ``raise`` and ``lower`` are null for a read-only row; for a switch they say what turning it on and off does.
    ``guardrail`` marks a hard safety limit (Safety Min, Safety Max and the VPD pair): the dashboard shows
    ``caution`` and asks for a confirmation before it saves one. ``reference_url`` is the lab entry, null for a row
    the lab page does not list.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True)

    summary: str
    effect: str
    raise_: str | None = Field(default=None, alias="raise")
    lower: str | None = None
    caution: str | None = None
    guardrail: bool = False
    reference_url: str | None = None


# ── The 48 crop-band anchor rows, generated ──────────────────────────────────

_ANCHOR_WHEN = {"sr": "sunrise", "sm": "solar noon", "ss": "sunset", "mid": "solar midnight"}
_CURVE = (
    "The controller draws one smooth daily curve through four anchors (sunrise, solar noon, sunset and solar "
    "midnight), so a change here also bends the curve in the hours on either side of {when}."
)
_ZONE_WORDS = {
    "center": "center zone (orchids)",
    "south": "south zone",
    "west": "west zone (citrus)",
    "east": "east zone (peppers)",
}
assert set(_ZONE_WORDS) == set(ZONES) and set(ZONE_CROP) == set(ZONES), "zone words drifted from band_rows.ZONES"
_SOLAR = "verdify_schemas/solar.py"
_LOGIC = "firmware/lib/greenhouse_logic.h"
_SOLAR_H = "firmware/lib/greenhouse_solar.h"
_BAND_REFS = (f"{_SOLAR_H}::band_value_at_phase", "verdify_schemas/band_rows.py::BAND_PARAM_ROWS")


def _house_anchor(series: str, anchor: str) -> HelpEntry:
    when = _ANCHOR_WHEN[anchor]
    curve = _CURVE.format(when=when)
    if series == "temp_low":
        return HelpEntry(
            summary=f"Low edge of the house temperature band at {when}.",
            effect=(
                f"{curve} Below the low edge the gas heater (heat2, the backup) latches on until the house is back "
                "at the heating target, a quarter of the way up the band; the electric heater (heat1, the primary "
                "heat) runs below that target."
            ),
            raise_=f"Both heaters start warmer around {when}: the house is kept warmer then, and heating runs more.",
            lower=f"The house may run cooler around {when} before heat1, then gas heat2, start: less heating energy.",
            caution="Keep it below the band target and high edge at the same anchor. The controller keeps it at "
            "least 5 °F above Safety Min.",
            refs=(*_BAND_REFS, f"{_LOGIC}::band_heat_target_f", f"{_LOGIC}::heat2_latched = stage2_escalation_latch"),
        )
    if series == "temp_target":
        return HelpEntry(
            summary=f"The temperature the house band is centred on at {when}.",
            effect=(
                f"{curve} The target is the middle the band is graded against. While venting to dry the air with "
                "the vent hold on, the electric heater (heat1) reheats toward it; with band tracking above zero the "
                "edges are pulled toward it."
            ),
            raise_=f"The middle of the band moves up around {when}; reheat during a drying vent aims warmer.",
            lower=f"The middle of the band moves down around {when}; reheat during a drying vent aims cooler.",
            caution="Keep it between the low and high edges at the same anchor; the controller clamps it into the band.",
            refs=(*_BAND_REFS, f"{_LOGIC}::sp.temp_target", f"{_LOGIC}::apply_band_track_pinch"),
        )
    if series == "temp_high":
        return HelpEntry(
            summary=f"High edge of the house temperature band at {when}.",
            effect=(
                f"{curve} Above the high edge the controller cools: it opens the vent with fan 1, and adds fan 2 "
                "once the house is Cool Stage 2 Over High above the edge."
            ),
            raise_=f"Cooling starts later around {when}: the house runs warmer and the vent and fans run less.",
            lower=f"Cooling starts sooner around {when}: the house runs cooler, with more venting and fan time.",
            caution="Keep it above the band target and low edge at the same anchor. The controller keeps it at "
            "least 5 °F below Safety Max.",
            refs=(*_BAND_REFS, f"{_LOGIC}::needs_cooling", f"{_LOGIC}::fan2_entry = temp_high"),
        )
    if series == "vpd_low":
        return HelpEntry(
            summary=f"Low edge of the house VPD band at {when} (below it the air is too humid).",
            effect=(
                f"{curve} Below the low edge the air is too wet: the controller dehumidifies by venting when outdoor "
                "air is drier, or by holding warmth with the electric heater (heat1) on cold nights."
            ),
            raise_=f"Drying starts sooner around {when}: less humid air, more venting or reheat.",
            lower=f"The house may stay more humid around {when} before drying starts: less venting and heat.",
            caution="Keep it below the VPD target and high edge at the same anchor.",
            refs=(*_BAND_REFS, f"{_LOGIC}::vpd_dehum_exit", f"{_LOGIC}::closed_heat_dehum_wanted"),
        )
    if series == "vpd_target":
        return HelpEntry(
            summary=f"The VPD the house band is centred on at {when}.",
            effect=(
                f"{curve} The controller weighs venting against outdoor air by whether it moves VPD toward this "
                "target, and grades time in band against it. With band tracking above zero the VPD edges are pulled "
                "toward it."
            ),
            raise_=f"The house aims drier around {when}: venting that dries the air is favoured.",
            lower=f"The house aims more humid around {when}: venting that adds moisture is favoured.",
            caution="Keep it between the VPD low and high edges at the same anchor.",
            refs=(*_BAND_REFS, f"{_LOGIC}::sp.vpd_target", f"{_LOGIC}::estimate_moisture_exchange"),
        )
    return HelpEntry(  # vpd_high
        summary=f"High edge of the house VPD band at {when} (above it the air is too dry).",
        effect=(
            f"{curve} Above the high edge the air is too dry: once it has stayed there for the VPD watch dwell, the "
            "controller humidifies with fog and, as VPD climbs further, the misters."
        ),
        raise_=f"Humidifying starts later around {when}: drier air, less water and fog use.",
        lower=f"Humidifying starts sooner around {when}: more humid air, more fog and mister water.",
        caution="Keep it above the VPD target and low edge at the same anchor. The misters' own thresholds "
        "(VPD Mister Engage, VPD Mister All) sit above this edge.",
        refs=(*_BAND_REFS, f"{_LOGIC}::in.vpd_kpa > sp.vpd_high", f"{_LOGIC}::humidify_ready"),
    )


def _zone_target(zone: str, anchor: str) -> HelpEntry:
    when = _ANCHOR_WHEN[anchor]
    where = _ZONE_WORDS[zone]
    curve = _CURVE.format(when=when)
    mister = (
        "East has no mister; west and center serve it."
        if zone == "east"
        else "Its misters run when this zone wins the turn."
    )
    return HelpEntry(
        summary=f"The VPD the {where} aims for at {when}.",
        effect=(
            f"{curve} When the zone's VPD is above this target, the zone asks to be wetted; the misters go to the "
            f"zone with the best priority, ties broken by how far above target each is. {mister}"
        ),
        raise_=f"The zone tolerates drier air around {when}: it asks for misting later and less often.",
        lower=f"The zone asks for misting sooner around {when}: more humid air there and more water.",
        caution="The zone band runs from this target minus the width below to the target plus the width above.",
        refs=(*_BAND_REFS, f"{_SOLAR_H}::zone_wet_intent", f"{_SOLAR_H}::arbitrate_wet_zone"),
    )


def _zone_width(side: str, zone: str) -> HelpEntry:
    where = _ZONE_WORDS[zone]
    if side == "above":
        return HelpEntry(
            summary=f"How far above its VPD target the {where} may drift before it counts as stressed.",
            effect=(
                "The zone's high edge is its target plus this width, all day. Above the high edge the zone is "
                "stressed; between target and high edge its urgency for misting grows toward that edge."
            ),
            raise_="A wider tolerance: the zone reads stressed later, and its misting urgency builds more slowly.",
            lower="A tighter tolerance: the zone reads stressed sooner, and its urgency for misting builds faster.",
            caution="The controller never uses a width below 0.05 kPa.",
            refs=(f"{_SOLAR_H}::zone_band_at_phase", f"{_SOLAR_H}::const bool stressed = zone_vpd_kpa > band.high"),
        )
    return HelpEntry(
        summary=f"How far below its VPD target the {where} band reaches (its humid edge).",
        effect=(
            "The zone's low edge is its target minus this width, all day. It sets the humid side of the zone band "
            "shown on the dashboard and used to grade time in band; misting decisions use the target and high edge."
        ),
        raise_="The zone band reaches further into humid air before the zone reads out of band on the humid side.",
        lower="The zone reads out of band on the humid side sooner.",
        caution="The controller never uses a width below 0.05 kPa.",
        refs=(f"{_SOLAR_H}::zone_band_at_phase", "ingestor/tasks/band_anchors.py::zone_vpd_width_below_"),
    )


def _band_entries() -> dict[str, HelpEntry]:
    out: dict[str, HelpEntry] = {}
    for name, rows in BAND_PARAM_ROWS.items():
        if rows.zone is None:
            out[name] = _house_anchor(rows.series, rows.anchor or "")
        elif rows.anchor is not None:
            out[name] = _zone_target(rows.zone, rows.anchor)
        else:
            out[name] = _zone_width("above" if rows.column == "width_above" else "below", rows.zone)
    return out


def _merged() -> dict[str, HelpEntry]:
    merged: dict[str, HelpEntry] = {}
    for part in (_planner.ENTRIES, _safety.ENTRIES, _scheduled.ENTRIES, _reference.ENTRIES, _band_entries()):
        overlap = set(merged) & set(part)
        assert not overlap, f"setting help defined twice: {sorted(overlap)}"
        merged.update(part)
    return {name: merged[name] for name in REGISTRY if name in merged}


HELP: Mapping[str, HelpEntry] = MappingProxyType(_merged())


def lab_reference_url(name: str) -> str:
    return f"{LAB_REFERENCE_URL}#{name}"


def _served(entry: HelpEntry, reference_url: str | None) -> SettingHelp:
    return SettingHelp(
        summary=entry.summary,
        effect=entry.effect,
        raise_=entry.raise_,
        lower=entry.lower,
        caution=entry.caution,
        guardrail=entry.guardrail,
        reference_url=reference_url,
    )


_SERVED: Mapping[str, SettingHelp] = MappingProxyType(
    {name: _served(entry, lab_reference_url(name)) for name, entry in HELP.items()}
)


def setting_help(name: str) -> SettingHelp | None:
    """The served text of a registry row; None for a name without an entry."""
    return _SERVED.get(name)


def setting_label(name: str) -> str:
    """The catalog label: the registry's, or the entry's where the registry label is missing or the raw name."""
    spec = REGISTRY.get(name)
    label = spec.label if spec is not None else None
    if label and label != name:
        return label
    entry = HELP.get(name)
    return entry.label if entry is not None and entry.label else (label or name)


# ── Derived rows: what they are computed from ────────────────────────────────

# No registry row is derived from other settings since #1477 retired the activity mirror (it followed the main
# grow-light schedule); the map stays so the catalog's source field keeps its shape.
_SOURCE_PARAMS: Mapping[str, tuple[str, ...]] = MappingProxyType({})


def source_params(name: str) -> tuple[str, ...]:
    """The writable settings a derived registry row is computed from; empty for any other row."""
    return _SOURCE_PARAMS.get(name, ())


def band_in_force_sources(zone: str, role: str) -> tuple[str, ...]:
    """The crop-band settings a Band in force row (``band_<zone>_<role>``) is computed from.

    The temperature roles of every zone, and every house role, are the house curve's four anchors; a zone's VPD
    target is its own four anchors, and its VPD low and high add the zone's width below or above
    (``ingestor/tasks/band_anchors.py`` ``zone_band_values``).
    """
    axis, _, side = role.partition("_")
    if zone == "house" or axis == "temp":
        return tuple(f"band_{role}_{anchor}" for anchor in ANCHOR_KEYS)
    targets = tuple(f"zone_vpd_target_{zone}_{anchor}" for anchor in ANCHOR_KEYS)
    if side == "low":
        return (*targets, f"zone_vpd_width_below_{zone}")
    if side == "high":
        return (*targets, f"zone_vpd_width_above_{zone}")
    return targets


_ROLE_WORDS = {
    "temp_low": ("low edge of the {what} temperature band", "°F"),
    "temp_target": ("middle of the {what} temperature band", "°F"),
    "temp_high": ("high edge of the {what} temperature band", "°F"),
    "vpd_low": ("low (humid) edge of the {what} VPD band", "kPa"),
    "vpd_target": ("VPD target of the {what}", "kPa"),
    "vpd_high": ("high (dry) edge of the {what} VPD band", "kPa"),
}
assert set(_ROLE_WORDS) == set(HOUSE_SERIES), "Band in force role words drifted from band_rows.HOUSE_SERIES"


def band_in_force_help(zone: str, role: str) -> SettingHelp:
    """Text for one Band in force row, generated from its source (#1332): what the value is now and where to edit it."""
    what = "house" if zone == "house" else _ZONE_WORDS.get(zone, f"{zone} zone")
    words, _unit = _ROLE_WORDS.get(role, (role.replace("_", " "), ""))
    edge = words.format(what=what)
    axis = role.partition("_")[0]
    if zone == "house" or axis == "temp":
        source = "the house band anchors"
        if zone != "house":
            source += " (temperature is one curve for the whole house, so every zone shares it)"
    elif role == "vpd_target":
        source = f"the {what} VPD target anchors"
    else:
        side = "below" if role == "vpd_low" else "above"
        source = f"the {what} VPD target anchors and its width {side}"
    return SettingHelp(
        summary=f"The {edge} the controller is using now.",
        effect=(
            f"Computed, not set: the value of today's band curve at this moment, drawn from {source}. The controller "
            "computes the same value on the chip; this row shows what it serves, refreshed each cycle."
        ),
        caution="Edit it under Crop band: change the anchors it is computed from.",
        reference_url=None,
    )
