"""Operator text for the scheduled rows of the settings catalog (see ``verdify_schemas.setting_help``).

The scheduled-policy rows (the direct-wet clock offsets and the two grow-light circuits) and the crop-band rows
that are not band anchors (the legacy wide limits, the zone priorities, the wet taper and the manual override
timeout). The band anchors themselves are generated in the package ``__init__``.
"""

from __future__ import annotations

from verdify_schemas.setting_help._entry import HelpEntry

_CONTROLS = "firmware/greenhouse/controls.yaml"
_LOGIC = "firmware/lib/greenhouse_logic.h"
_SOLAR = "firmware/lib/greenhouse_solar.h"
_IRRIGATION = "firmware/lib/irrigation_program.h"
_DISPATCHER = "ingestor/tasks/dispatcher.py"

# ── Direct-wet clock offsets ─────────────────────────────────────────────────
# The controller stores and reports these, and the platform still sends their defaults
# (dispatcher.py _activity_defaults_from_lighting), but nothing in the control loop reads
# them: greenhouse_logic.h::direct_wet_window_open has no caller, and misting follows the
# band curve (climate_wet_assist_permitted) and the irrigation program instead.
_NO_WET_WINDOW = (
    "The controller stores and reports this value, but no control path reads it today: misting follows the "
    "VPD band and the irrigation program, not a clock window."
)
_NO_EFFECT = "No effect on equipment today; only the reported value changes."


def _wet_offset(where: str, start: bool) -> HelpEntry:
    if start:
        summary = f"Minutes after the daily activity window opens before {where} wetting was meant to start."
    else:
        summary = f"Minutes before the daily activity window closes when {where} wetting was meant to stop."
    return HelpEntry(
        summary=summary,
        effect=_NO_WET_WINDOW,
        raise_=_NO_EFFECT,
        lower=_NO_EFFECT,
        refs=(
            f"{_LOGIC}::direct_wet_window_open",
            f"{_DISPATCHER}::_activity_defaults_from_lighting",
            f"{_CONTROLS}::climate_wet_assist_permitted",
        ),
    )


# ── Grow-light circuits ──────────────────────────────────────────────────────
# One evaluate_lighting() state machine per circuit (greenhouse_logic.h), fed from controls.yaml
# block B6 (main_sp / grow_sp). Lux is the outdoor weather-station lux, the indoor sensor only
# when that is missing.
_CIRCUITS = {"main": "main (overhead)", "grow": "grow"}


def _light_refs(circuit: str, token: str) -> tuple[str, ...]:
    return (f"{_CONTROLS}::gl_{circuit}_{token}", f"{_LOGIC}::evaluate_lighting")


def _lighting(circuit: str) -> dict[str, HelpEntry]:
    lights = f"the {_CIRCUITS[circuit]} lights"
    return {
        f"gl_{circuit}_target_light_minutes": HelpEntry(
            summary=f"Daily minutes of light {lights} aim for, counting bright daylight and lamp time.",
            effect=(
                f"Inside the light window each minute counts once if outdoor light is above the lux threshold or "
                f"{lights} are on. While the day's count is below this target and it is dim, the controller turns "
                f"{lights} on; once the target is met they stay off. The count restarts each day."
            ),
            raise_=f"A longer photoperiod: {lights} run more on dim days, using more electricity.",
            lower=f"A shorter photoperiod: {lights} stop supplementing sooner and the plants get less light.",
            caution="The light window still caps it: lamps never run outside the window, whatever the target.",
            refs=_light_refs(circuit, "target_light_minutes"),
        ),
        f"gl_{circuit}_lux_threshold": HelpEntry(
            summary=f"Outdoor light level (lux) below which {lights} count it as dim and may turn on.",
            effect=(
                f"Below this lux, inside the window and short of the daily minutes, the controller turns {lights} "
                f"on. Daylight at or above it counts toward the daily minutes. Once on, the lights stay on until lux "
                f"passes this value plus the lux hysteresis."
            ),
            raise_=f"{lights.capitalize()} come on in brighter daylight, so they run more often and longer.",
            lower=f"{lights.capitalize()} come on only in darker conditions; more of the day relies on sunlight.",
            refs=_light_refs(circuit, "lux_threshold"),
        ),
        f"gl_{circuit}_lux_hysteresis": HelpEntry(
            summary=f"Extra lux above the threshold daylight must reach before {lights} turn back off.",
            effect=(
                f"Once on, {lights} stay on until outdoor lux exceeds the lux threshold plus this margin, so passing "
                f"clouds do not flick them on and off."
            ),
            raise_="Lights hold on longer as the sky brightens; fewer on/off cycles.",
            lower="Lights turn off sooner when it brightens; more cycling on partly cloudy days.",
            refs=_light_refs(circuit, "lux_hysteresis"),
        ),
        f"gl_{circuit}_sunrise_hour": HelpEntry(
            summary=f"Local clock hour when the light window for {lights} opens.",
            effect=(
                f"With solar phasing off, {lights} may run only from the start of this hour until the cutoff hour. "
                f"The daily light-minute count also restarts at this hour."
            ),
            raise_=f"The window opens later: {lights} cannot start as early in the morning.",
            lower=f"The window opens earlier: {lights} may start earlier in the morning.",
            caution="Ignored while solar phasing is on, except that the daily count still restarts at this hour.",
            refs=(*_light_refs(circuit, "sunrise_hour"), f"{_LOGIC}::lighting_hour_in_window"),
        ),
        f"gl_{circuit}_sunset_hour": HelpEntry(
            summary=f"Local clock hour when the light window for {lights} closes.",
            effect=(
                f"With solar phasing off, {lights} are not turned on from the start of this hour until the next "
                f"window opens; a light already on finishes its minimum on-time, then goes off."
            ),
            raise_=f"The window closes later: {lights} may run later into the evening.",
            lower="The window closes earlier: a longer dark period for the plants.",
            caution="Ignored while solar phasing is on.",
            refs=(*_light_refs(circuit, "sunset_hour"), f"{_LOGIC}::lighting_hour_in_window"),
        ),
        f"sw_gl_{circuit}_solar_phasing": HelpEntry(
            summary=f"Ties the light window for {lights} to today's sunrise and sunset instead of clock hours.",
            effect=(
                "On: the window runs from sunrise plus the sunrise offset to sunset plus the sunset offset, so it "
                "follows the seasons. Off: it runs between the fixed sunrise and cutoff hours."
            ),
            raise_=f"Turning it on makes the window for {lights} follow real sunrise and sunset, using the offsets.",
            lower=f"Turning it off returns {lights} to the fixed start and cutoff hours.",
            refs=(*_light_refs(circuit, "solar_phasing"), f"{_LOGIC}::lighting_in_window"),
        ),
        f"gl_{circuit}_sunrise_offset_min": HelpEntry(
            summary=f"Minutes from sunrise to when the solar-phased window for {lights} opens.",
            effect=(
                f"With solar phasing on, the window for {lights} opens at sunrise plus this many minutes "
                f"(negative opens before sunrise)."
            ),
            raise_="The window opens later in the morning.",
            lower="The window opens earlier, before sunrise if negative.",
            caution="Used only while solar phasing is on.",
            refs=(*_light_refs(circuit, "sunrise_offset_min"), f"{_LOGIC}::lighting_in_window"),
        ),
        f"gl_{circuit}_sunset_offset_min": HelpEntry(
            summary=f"Minutes from sunset to when the solar-phased window for {lights} closes.",
            effect=(
                f"With solar phasing on, the window for {lights} closes at sunset plus this many minutes "
                f"(positive extends past sunset)."
            ),
            raise_="The window closes later, extending the lit day past sunset.",
            lower="The window closes earlier, giving a longer dark period.",
            caution="Used only while solar phasing is on.",
            refs=(*_light_refs(circuit, "sunset_offset_min"), f"{_LOGIC}::lighting_in_window"),
        ),
        f"gl_{circuit}_min_on_s": HelpEntry(
            summary=f"Shortest time {lights} stay on once switched on.",
            effect=(
                f"After turning {lights} on, the controller keeps them on for at least this long even if daylight "
                f"recovers or the window ends. Switching auto mode off still turns them off at once."
            ),
            raise_="Fewer short on/off cycles; lights may stay on a little past the point they are needed.",
            lower="Lights can turn off sooner after coming on; more cycling.",
            refs=(*_light_refs(circuit, "min_on_s"), f"{_LOGIC}::min_on_hold"),
        ),
        f"gl_{circuit}_min_off_s": HelpEntry(
            summary=f"Shortest time {lights} stay off before they may turn on again.",
            effect=f"After {lights} turn off, the controller will not turn them back on until this time has passed.",
            raise_="Longer rest between cycles; lights respond more slowly when it turns dim again.",
            lower="Lights can come back on sooner after turning off; more cycling.",
            refs=(*_light_refs(circuit, "min_off_s"), f"{_LOGIC}::min_off_hold"),
        ),
    }


def _auto_mode(circuit: str) -> HelpEntry:
    lights = f"the {_CIRCUITS[circuit]} lights"
    return HelpEntry(
        summary=f"Lets the controller switch {lights} automatically.",
        effect=(
            f"On: the controller turns {lights} on and off from the light window, daylight and the daily "
            f"light-minute target. Off: the controller turns {lights} off at once and keeps them off."
        ),
        raise_=f"Turning it on hands {lights} back to the automatic schedule (Back to automatic).",
        lower=f"Turning it off switches {lights} off and keeps them off until auto mode is turned back on.",
        refs=(f"{_CONTROLS}::gl_{circuit}_auto_enabled", f"{_LOGIC}::auto_disabled"),
    )


# ── Legacy wide limits ───────────────────────────────────────────────────────
# controls.yaml: with sw_onchip_band_enabled the band edges come from the on-chip anchor curve
# (sv2_curve); the pushed scalars are only the fallback. dispatcher.py stops pushing the four
# house edges once every anchor is confirmed (anchors_live).
_LEGACY_TEMP_REFS = (
    f"{_CONTROLS}::sw_onchip_band_enabled",
    f"{_CONTROLS}::target_temp_low_f",
    f"{_DISPATCHER}::anchors_live",
)
_LEGACY_VPD_REFS = (f"{_CONTROLS}::sw_onchip_band_enabled", f"{_CONTROLS}::VPDhi", f"{_DISPATCHER}::anchors_live")


def _legacy_edge(axis: str, side: str, refs: tuple[str, ...]) -> HelpEntry:
    unit_word = "temperature" if axis == "temp" else "VPD"
    return HelpEntry(
        summary=(f"Old fixed {unit_word} {side} limit. Not a guardrail, and not the band the controller uses now."),
        effect=(
            f"While the on-chip band runs, the controller takes the {side} {unit_word} edge from the crop band curve "
            f"(see Band in force) and ignores this value. It is used only if the on-chip band is switched off; "
            f"the platform stops sending it once the band anchors are confirmed."
        ),
        caution=(
            "Not editable. Change the band under Crop band; the hard limits are Safety Min and Safety Max "
            "(temperature) and Safety VPD Min and Max."
        ),
        refs=refs,
    )


def _zone_target(zone: str, crop: str) -> HelpEntry:
    if zone == "east":
        use = (
            "The east zone has no mister: the controller compares east VPD with this value only to boost the "
            "south zone's misting score when the east side is dry."
        )
    else:
        use = (
            f"The controller still compares {zone} VPD with this value when it judges whether a zone needs misting, "
            f"which zone a pulse goes to, and whether to lengthen a pulse when VPD is 20% above it."
        )
    return HelpEntry(
        summary=f"Fixed VPD target for the {zone} zone ({crop}), sent by the platform from the crop data.",
        effect=(
            f"{use} The zone's band in force comes from its target curve under Crop band; this single value "
            f"is not editable."
        ),
        caution="Read-only. Edit the zone's VPD target curve under Crop band to change how the zone is misted.",
        refs=(f"{_CONTROLS}::vpd_target_{zone}", f"{_DISPATCHER}::fn_zone_vpd_targets"),
    )


def _zone_priority(zone: str, crop: str) -> HelpEntry:
    if zone == "east":
        return HelpEntry(
            summary=f"Misting rank of the east zone ({crop}); 1 is served first.",
            effect="The east zone has no mister, so the controller leaves it out when it ranks zones for a pulse.",
            raise_="No effect today: the east zone has no mister.",
            lower="No effect today: the east zone has no mister.",
            refs=(f"{_IRRIGATION}::irr_rank_vpd_zone", f"{_SOLAR}::arbitrate_wet_zone"),
        )
    return HelpEntry(
        summary=f"Misting rank of the {zone} zone ({crop}); 1 is served first.",
        effect=(
            "When more than one mister zone may run, each pulse goes to the best-ranked zone whose VPD is above "
            "its band target, ties going to the driest; if none is above target, the best-ranked zone gets it. "
            "A zone that has waited too long still gets a fairness turn."
        ),
        raise_=f"A larger number ranks the {zone} misters lower, so they lose more pulses to other zones.",
        lower=f"A smaller number ranks the {zone} misters higher, so they win more pulses when zones compete.",
        caution="Give each zone its own rank; a tie is decided by which zone is driest.",
        refs=(
            f"{_IRRIGATION}::irr_rank_vpd_zone",
            f"{_SOLAR}::arbitrate_wet_zone",
            f"{_CONTROLS}::zone_priority_{zone}",
        ),
    )


ENTRIES: dict[str, HelpEntry] = {
    "direct_wet_min_temp_f": HelpEntry(
        summary="House temperature below which automated wetting was meant to be blocked; 0 turns the check off.",
        effect=(
            "The controller only uses it to name the reason when misting is already blocked. Whether misters run "
            "is decided by the VPD band, the dew margin and the irrigation program."
        ),
        raise_="No effect on equipment today; a cold house may be reported as the blocking reason more often.",
        lower="No effect on equipment today; the cold-house reason is reported less often.",
        refs=(f"{_CONTROLS}::direct_wet_min_temp_f", f"{_CONTROLS}::direct_wet_temp"),
    ),
    "direct_wet_south_start_offset_min": _wet_offset("south zone", start=True),
    "direct_wet_wall_start_offset_min": _wet_offset("wall drip", start=True),
    "direct_wet_wall_drydown_before_off_min": _wet_offset("wall drip", start=False),
    "direct_wet_south_drydown_before_off_min": _wet_offset("south zone", start=False),
    "direct_wet_west_start_offset_min": _wet_offset("west zone", start=True),
    "direct_wet_west_drydown_before_off_min": _wet_offset("west zone", start=False),
    "direct_wet_center_start_offset_min": _wet_offset("center zone", start=True),
    "direct_wet_center_drydown_before_off_min": _wet_offset("center zone", start=False),
    **_lighting("main"),
    **_lighting("grow"),
    "sw_gl_main_auto_mode": _auto_mode("main"),
    "sw_gl_grow_auto_mode": _auto_mode("grow"),
    "temp_low": _legacy_edge("temp", "low", _LEGACY_TEMP_REFS),
    "temp_high": _legacy_edge("temp", "high", _LEGACY_TEMP_REFS),
    "vpd_low": _legacy_edge("vpd", "low", _LEGACY_VPD_REFS),
    "vpd_high": _legacy_edge("vpd", "high", _LEGACY_VPD_REFS),
    "vpd_target_south": _zone_target("south", "cannabis"),
    "vpd_target_west": _zone_target("west", "citrus"),
    "vpd_target_east": _zone_target("east", "pepper"),
    "vpd_target_center": _zone_target("center", "Vanda orchids"),
    "zone_priority_center": _zone_priority("center", "Vanda orchids"),
    "zone_priority_south": _zone_priority("south", "cannabis"),
    "zone_priority_west": _zone_priority("west", "citrus"),
    "zone_priority_east": _zone_priority("east", "pepper"),
    "wet_taper_before_sunset_min": HelpEntry(
        summary="Minutes before sunset when routine misting was meant to stop so leaves dry before dark.",
        effect=(
            "The controller stores and reports it, but the taper check is switched off in the control code: "
            "misting at dusk and at night now follows the VPD band curve, whose evening edge keeps nights dry."
        ),
        raise_=_NO_EFFECT,
        lower=_NO_EFFECT,
        caution="To keep evenings drier, raise the evening and night VPD band under Crop band instead.",
        refs=(f"{_LOGIC}::past_wet_taper", f"{_CONTROLS}::wet_taper_before_sunset_min"),
    ),
    "manual_override_timeout_min": HelpEntry(
        summary="How long a press of a panel button (fan burst, fog burst, vent bypass) lasts, in minutes.",
        effect=(
            "A press of a physical push button on the controller panel starts a fan burst, fog burst or vent "
            "bypass that runs this many minutes, then hands control back. A second press ends it early."
        ),
        raise_="Each button press runs the fans, fog or vent bypass longer before automatic control resumes.",
        lower="Button bursts end sooner and automatic control resumes sooner.",
        refs=(f"{_LOGIC}::manual_latch_toggle", "firmware/greenhouse.yaml::manual_override_timeout_min"),
    ),
}
