"""Operator text for the controller safety and site water cap rows (see ``verdify_schemas.setting_help``)."""

from __future__ import annotations

from verdify_schemas.setting_help._entry import HelpEntry

_LOGIC = "firmware/lib/greenhouse_logic.h"
_TYPES = "firmware/lib/greenhouse_types.h"
_CONTROLS = "firmware/greenhouse/controls.yaml"
_TUNABLES = "firmware/greenhouse/tunables.yaml"
_SENSORS = "firmware/greenhouse/sensors.yaml"
_IRR = "firmware/lib/irrigation_program.h"
_DISPATCHER = "ingestor/tasks/dispatcher.py"

ENTRIES: dict[str, HelpEntry] = {
    "safety_min": HelpEntry(
        summary="Hard cold guardrail: the house temperature at which the controller goes into emergency heating.",
        effect=(
            "At or below this temperature the controller enters safety heating: both heaters run (electric heat1 "
            "and gas heat2), the vent stays shut and the lead fan stirs the air. It overrides every other mode. "
            "There is no hardware thermostat, so this is the last line of defense."
        ),
        raise_="Emergency heating starts at a warmer temperature: more protection for the plants, more gas use.",
        lower="Emergency heating waits until the house is colder: the plants can get colder before the backstop acts.",
        caution=(
            "Intended value 40 °F. The band never moves it: the controller keeps the band's low edge at least 5 °F "
            "above it. A value outside 30-70 °F, or too close to Safety Max, falls back to 40 °F."
        ),
        guardrail=True,
        refs=(
            f"{_LOGIC}::const bool safety_heat = in.temp_f <= sp.safety_min;",
            f"{_LOGIC}::out.heat1 = true; out.heat2 = true;",
            f"{_TYPES}::if (!(sp.safety_min >= 30.0f && sp.safety_min <= 70.0f)) sp.safety_min = kSafetyMinF;",
            f'{_DISPATCHER}::"safety_min": 40.0',
        ),
    ),
    "safety_max": HelpEntry(
        summary="Hard heat guardrail: the house temperature at which the controller goes into emergency cooling.",
        effect=(
            "At or above this temperature the controller enters safety cooling: the vent opens, both fans run and "
            "fog may assist when the air is dry. It overrides every other mode. It also sets where sealed misting "
            "must stop (see Safety Seal Margin)."
        ),
        raise_="Emergency cooling starts at a hotter temperature: the house may run hotter before the backstop acts.",
        lower="Emergency cooling starts sooner: more venting and fan time on hot days, less heat stress.",
        caution=(
            "Intended value 100 °F. The band never moves it: the controller keeps the band's high edge at least 5 °F "
            "below it. A value outside 70-120 °F, or too close to Safety Min, falls back to 100 °F."
        ),
        guardrail=True,
        refs=(
            f"{_LOGIC}::const bool safety_cool = in.temp_f >= sp.safety_max;",
            f"{_LOGIC}::out.fan1 = true; out.fan2 = true;",
            f"{_TYPES}::if (!(sp.safety_max >= 70.0f && sp.safety_max <= 120.0f)) sp.safety_max = kSafetyMaxF;",
            f'{_DISPATCHER}::"safety_max": 100.0',
        ),
    ),
    "safety_vpd_min": HelpEntry(
        summary="Hard wet-air guardrail: the VPD below which the controller forces drying.",
        effect=(
            "When VPD falls below this (air close to saturation) and the house is idle or misting, the controller "
            "forces a drying vent cycle, or runs the electric heater (heat1) when heat is the better way to dry the "
            "air. It does not act while the economiser blocks venting."
        ),
        raise_="Forced drying starts earlier, in less humid air: less condensation risk, more venting or heating.",
        lower="The air can get wetter before forced drying starts: higher condensation and disease risk.",
        caution=(
            "The controller keeps it at least 0.05 kPa below the VPD band's low edge (and at least 0.05 kPa), so "
            "the normal band acts first."
        ),
        guardrail=True,
        refs=(
            f"{_LOGIC}::vpd_min_safe_rescue",
            f"{_LOGIC}::vpd_min_safe_heat_rescue",
            f"{_TYPES}::sp.vpd_min_safe = std::max(0.05f, std::min(sp.vpd_low - 0.05f, sp.vpd_min_safe));",
        ),
    ),
    "safety_vpd_max": HelpEntry(
        summary="Hard dry-air guardrail: the VPD above which the controller forces sealed misting.",
        effect=(
            "When VPD rises above this (very dry air) and the house is not hot enough to need cooling, the "
            "controller closes the vent and humidifies with fog and the misters (a dry override), whatever the "
            "dwell timers say."
        ),
        raise_="The air can get drier before the override seals the house: more plant water stress on dry days.",
        lower="The dry override seals and mists sooner: less dry stress, more water use and more closed-vent time.",
        caution=(
            "The controller keeps it at least 0.1 kPa above the VPD band's high edge (and at most 8 kPa), so the "
            "normal band acts first."
        ),
        guardrail=True,
        refs=(
            f"{_LOGIC}::dry_override",
            f"{_TYPES}::sp.vpd_max_safe = std::max(sp.vpd_high + 0.1f, std::min(8.0f, sp.vpd_max_safe));",
        ),
    ),
    "safety_max_seal_margin_f": HelpEntry(
        summary="How far below Safety Max sealed misting must stop.",
        effect=(
            "Sealed misting (vent shut, fog and misters on) may not start once the house is within this many °F of "
            "Safety Max, and a running seal is cut short into a venting relief cycle at that point."
        ),
        raise_="Sealed misting stops at a cooler temperature: less humidifying on hot days, less overheating risk.",
        lower="Sealed misting may run closer to Safety Max: more humidifying when hot, higher risk of overheating.",
        refs=(
            f"{_LOGIC}::in.temp_f < (sp.safety_max - sp.safety_max_seal_margin_f)",
            f"{_LOGIC}::thermal_relief_forced",
        ),
    ),
    "mister_hysteresis_kpa": HelpEntry(
        summary="Minimum VPD drop before the misters step down a stage.",
        effect=(
            "The misters step down (both zones to one, then off) only once house VPD falls this far below VPD "
            "Mister All or VPD Mister Engage. The controller uses at least a quarter of VPD Mister Engage, and never "
            "more than the VPD hysteresis."
        ),
        raise_="The misters keep running longer after VPD falls: fewer on/off cycles, more water per episode.",
        lower="The misters stop sooner once VPD falls: less water, more on/off cycling.",
        refs=(
            f"{_CONTROLS}::id(mister_hysteresis_kpa)",
            f"{_CONTROLS}::S1→off",
        ),
    ),
    "night_stress_min_dew_margin_f": HelpEntry(
        summary="Night condensation guard: the minimum gap between air temperature and dewpoint for wetting at night.",
        effect=(
            "At night the misters and fog may only wet when the air is at least this many °F above its dewpoint "
            "(or the daytime Direct Wet Stress Min Dew Margin, if larger), so leaves do not stay wet in the dark."
        ),
        raise_="Night wetting is blocked more often: drier leaves and less disease risk, but less night humidifying.",
        lower="Night wetting is allowed closer to the dewpoint: more humidifying at night, more condensation risk.",
        refs=(
            f"{_LOGIC}::std::max(sp.direct_wet_stress_min_dew_margin_f, sp.night_stress_min_dew_margin_f)",
            f"{_LOGIC}::min_dew_margin_for_wetting",
        ),
    ),
    "sw_night_econ_heat_suppress_enabled": HelpEntry(
        summary="Never heat at night just to dry the air.",
        effect=(
            "When on, the electric heater's humidity rescue (heating to lift a low VPD while the economiser blocks "
            "venting) is off for the whole night half of the solar day. Normal heating and emergency heating are "
            "not affected."
        ),
        raise_="On: no heating to chase humidity at night, keeping the night cool for the plants and saving energy.",
        lower="Off: the electric heater may run at night to dry humid air, warming the night and using power.",
        refs=(
            f"{_LOGIC}::return sp.sw_night_econ_heat_suppress_enabled && is_night_phase(in);",
            f"{_LOGIC}::!night_econ_heat_suppressed(in, sp)",
        ),
    ),
    "sw_wet_taper_enabled": HelpEntry(
        summary="Former switch for stopping routine wetting before sunset; it has no effect now.",
        effect=(
            "The controller now decides wetting from VPD against the band curve, so a properly shaped evening "
            "band keeps foliage dry. This switch is still stored and reported, but nothing reads it."
        ),
        refs=(
            f"{_LOGIC}::inline bool past_wet_taper(const SensorInputs&, const Setpoints&) noexcept {{",
            f"{_CONTROLS}::.sw_wet_taper_enabled = id(sw_wet_taper_enabled),",
        ),
    ),
    "sw_night_stress_wet_enabled": HelpEntry(
        summary="Former switch for emergency wetting at night; it has no effect now.",
        effect=(
            "Night wetting now follows VPD against the night band and the night condensation guard (Night Stress "
            "Min Dew Margin). This switch is still stored and reported, but nothing reads it."
        ),
        refs=(
            f"{_LOGIC}::inline bool stress_wet_override_permitted(const SensorInputs&, const Setpoints&) noexcept {{",
            f"{_CONTROLS}::.sw_night_stress_wet_enabled = id(sw_night_stress_wet_enabled),",
        ),
    ),
    "east_adjacency_factor": HelpEntry(
        summary="How much east-zone dryness counts toward misting the south and center zones.",
        effect=(
            "The east zone has no mister. In the fallback zone scoring (used when the zone ranking picks no zone), "
            "east's dryness and heat are added to the south and center scores, scaled by this factor."
        ),
        raise_="A dry east zone pulls the misters toward south and center more often in the fallback scoring.",
        lower="East dryness counts less; at 0 it is ignored when choosing a mister zone.",
        refs=(f"{_CONTROLS}::e_stress * id(east_adjacency_factor)",),
    ),
    "lead_rotate_s": HelpEntry(
        summary="How often the lead fan swaps between fan 1 and fan 2 when their run times are even.",
        effect=(
            "The controller picks the fan with less run time as the lead. When the two are within a small margin "
            "and no fan is running, it swaps the lead after this many seconds (at least 60 s)."
        ),
        raise_="The lead fan swaps less often when run times are close.",
        lower="The lead fan swaps more often, spreading wear more evenly.",
        refs=(
            f"{_CONTROLS}::LEAD_ROTATE_TIMEOUT_MS",
            f"{_LOGIC}::if (!any_fan_running && wall_clock_rotate_due) {{",
        ),
    ),
    "fog_rh_ceiling_pct": HelpEntry(
        summary="Fog is blocked when indoor humidity is above this.",
        effect="The fogger will not run while indoor relative humidity is above this percentage.",
        raise_="Fog may run in more humid air: more humidifying, more risk of saturated air and wet leaves.",
        lower="Fog stops earlier as humidity rises: less wetting risk, less humidifying.",
        refs=(
            f"{_LOGIC}::return (in.rh_pct  <= sp.fog_rh_ceiling)",
            f"{_CONTROLS}::.fog_rh_ceiling = (float)id(fog_rh_ceiling_pct),",
        ),
    ),
    "fog_min_temp_f": HelpEntry(
        summary="Fog is blocked when the house is colder than this.",
        effect="The fogger will not run while indoor temperature is below this, because fog cools the air further.",
        raise_="Fog needs a warmer house to run: less fog on cool mornings and evenings.",
        lower="Fog may run in a cooler house: more humidifying, more chilling of cold air.",
        refs=(
            f"{_LOGIC}::&& (in.temp_f  >= sp.fog_min_temp);",
            f"{_CONTROLS}::.fog_min_temp = (float)id(fog_min_temp_f),",
        ),
    ),
    "max_relief_cycles": HelpEntry(
        summary="How many sealed-mist and relief cycles in a row before the controller forces the vent open.",
        effect=(
            "Each time sealed misting has to vent to shed heat it counts one relief cycle. After this many in a "
            "row, while the air is still too dry, the controller stops sealing and holds the vent open (see Vent "
            "Latch Timeout)."
        ),
        raise_="More sealing attempts before the forced vent: more humidifying, more heat build-up and cycling.",
        lower="The forced vent comes sooner: less heat build-up, less humidifying on hot dry days.",
        refs=(
            f"{_LOGIC}::state.relief_cycle_count < sp.max_relief_cycles",
            f"{_LOGIC}::relief_cycle_breaker",
        ),
    ),
    "dehum_aggressive_kpa": HelpEntry(
        summary="How far below the VPD band the air must be before drying uses both fans.",
        effect=(
            "While the controller vents to dry the air, it runs only the lead fan. When VPD is more than this "
            "below the lower quarter of the VPD band, it runs both fans."
        ),
        raise_="Both fans come on only for wetter air: quieter, gentler drying.",
        lower="Both fans come on sooner: faster drying, more air exchange and heat loss.",
        caution="The controller keeps it at least 0.05 kPa below the VPD band's low edge.",
        refs=(
            f"{_LOGIC}::if (in.vpd_kpa < vpd_low_eff - sp.dehum_aggressive_kpa) {{",
            f"{_TYPES}::sp.dehum_aggressive_kpa = std::max(0.05f, std::min(sp.vpd_low - 0.05f, sp.dehum_aggressive_kpa));",
        ),
    ),
    "sw_dehum_vent_hold_enabled": HelpEntry(
        summary="Lets the controller vent humid night air while the electric heater holds the temperature.",
        effect=(
            "When on, at night, if venting alone would cool the house too much to dry it, the controller may vent "
            "and run the electric heater (heat1) to hold the temperature, up to the band target. The gas heater "
            "never joins. Only when the house is above the heating line."
        ),
        raise_="On: humid nights can be dried by venting with reheat: drier air, more electric heat use.",
        lower="Off: no vent-and-reheat drying; humid nights dry only by plain venting or closed-vent heating.",
        caution="Operator switch; Iris does not change it.",
        refs=(
            f"{_LOGIC}::const bool can_hold = sp.dehum_vent_hold_enabled",
            f"{_LOGIC}::(mx.hold_required && in.temp_f < sp.temp_target)",
        ),
    ),
    "vent_latch_timeout_ms": HelpEntry(
        summary="How long the forced vent holds after too many relief cycles before sealing may be tried again.",
        effect=(
            "After Max Relief Cycles trips, the vent is held open. Once it has been held this long (default 30 min) "
            "with the air still too dry, the cycle count resets and sealed misting may try again."
        ),
        raise_="Longer forced venting before the controller retries sealed misting.",
        lower="Sealed misting is retried sooner after a forced vent.",
        refs=(f"{_LOGIC}::if (state.vent_latch_timer_ms >= sp.vent_latch_timeout_ms) {{",),
    ),
    "sw_gl_main_out_of_service": HelpEntry(
        summary="Marks the main grow-light fixture as dead so its on-time is not counted as light.",
        effect=(
            "When on, time the main circuit is switched on no longer counts toward its daily light minutes or the "
            "light total; only real daylight does. Use it while the fixture is broken or unplugged."
        ),
        raise_="On: the main circuit's switch-on time stops counting as delivered light.",
        lower="Off: the main circuit's switch-on time counts as light again; use once the fixture works.",
        refs=(
            f"{_LOGIC}::const bool switch_on_qualifies = current_on && !sp.out_of_service;",
            f"{_CONTROLS}::.out_of_service = id(gl_main_out_of_service)",
        ),
    ),
    "sw_gl_grow_out_of_service": HelpEntry(
        summary="Marks the grow-light fixture as dead so its on-time is not counted as light.",
        effect=(
            "When on, time the grow circuit is switched on no longer counts toward its daily light minutes or the "
            "light total; only real daylight does. Use it while the fixture is broken or unplugged."
        ),
        raise_="On: the grow circuit's switch-on time stops counting as delivered light.",
        lower="Off: the grow circuit's switch-on time counts as light again; use once the fixture works.",
        refs=(
            f"{_LOGIC}::const bool switch_on_qualifies = current_on && !sp.out_of_service;",
            f"{_CONTROLS}::.out_of_service = id(gl_grow_out_of_service)",
        ),
    ),
    "econ_heat_margin_f": HelpEntry(
        summary="How far below the band's high edge the electric heater may run to dry humid air.",
        effect=(
            "When the air is too humid but the economiser blocks venting, the idle controller may run the electric "
            "heater (heat1) to lift VPD, only while the house is more than this many °F below the band's high edge."
        ),
        raise_="The heater dries air only when the house is well below the high edge: less heating, wetter air.",
        lower="The heater may dry air closer to the high edge: drier air, more electric heat and warmer house.",
        refs=(f"{_LOGIC}::&& in.temp_f < Thigh - sp.econ_heat_margin_f",),
    ),
    "site_pressure_hpa": HelpEntry(
        summary="Barometric pressure at the greenhouse's elevation, a site constant.",
        effect=(
            "Used to compute the heat content (enthalpy) of indoor and outdoor air, which the economiser compares "
            "to decide whether venting would bring in useful air. It is not used for VPD."
        ),
        raise_="Slightly lowers the computed moisture content of both airs; the economiser decision barely moves.",
        lower="Slightly raises the computed moisture content of both airs; the economiser decision barely moves.",
        caution="Set it once for the site's elevation; it is not a tuning knob.",
        refs=(f"{_SENSORS}::float P  = id(site_pressure_hpa);",),
    ),
    "sw_economiser_enabled": HelpEntry(
        summary="Lets outdoor air conditions block venting for humidity control.",
        effect=(
            "When on, the controller compares indoor and outdoor air heat content. If outside air would add too much "
            "heat and moisture (above Enthalpy Close, or the intake sensor is offline) it blocks drying vents until "
            "the difference falls below Enthalpy Open."
        ),
        raise_="On: no drying vents while outside air is unhelpful; the electric heater may dry the air instead.",
        lower="Off: drying vents run whatever the outside air, and the heater never runs just to dry the air.",
        refs=(
            f"{_CONTROLS}::if(!id(economiser_enabled)){{",
            f"{_LOGIC}::bool vpd_too_low_enter = vpd_trusted && in.vpd_kpa < (vpd_low_eff - HV) && !sp.econ_block;",
        ),
    ),
    "sw_occupancy_inhibit": HelpEntry(
        summary="Stops misting, fog and the fans while someone is in the greenhouse.",
        effect=(
            "When on and the cameras confirm someone inside, the controller blocks misting, fog and sealed misting, "
            "and turns the fans off. Emergency heating and cooling still run."
        ),
        raise_="On: people inside do not get misted or blown on; humidity control pauses while they are there.",
        lower="Off: misting, fog and fans run as normal with people inside.",
        refs=(
            f"{_LOGIC}::return sp.occupancy_inhibit && in.occupied;",
            f"{_LOGIC}::inline bool air_blocked_by_occupancy(",
        ),
    ),
    "mister_daily_volume_max_gal": HelpEntry(
        summary="Hard daily water ceiling for misting and fog.",
        effect=(
            "Sent to the controller in the irrigation program. Once the day's mist and fog water reaches this many "
            "gallons, all misting and fog stop for the rest of the day; only emergency cooling fog may still run."
        ),
        raise_="More water may be used for misting and fog on a hot, dry day before everything stops.",
        lower="Misting and fog stop sooner on a heavy day: less water used, more risk of dry stress late in the day.",
        refs=(
            f"{_IRR}::return e.rtc->water_centigal >= uint32_t(e.prog.saf4_gal) * 100u;",
            f"{_LOGIC}::mister_volume_hard_block",
        ),
    ),
    "sched_mist_rh_max_pct": HelpEntry(
        summary="Scheduled mist pulses are skipped when indoor humidity is at or above this.",
        effect=(
            "Sent to the controller in the irrigation program. A scheduled mist pulse is skipped while indoor "
            "relative humidity is at or above this percentage."
        ),
        raise_="Scheduled pulses run in more humid air: more wetting, more risk of wet leaves.",
        lower="Scheduled pulses are skipped more often in humid air: less water, drier leaves.",
        refs=(f"{_IRR}::e.in.rh_pct >= float(e.prog.rh_ceiling_pct)",),
    ),
    "climate_yield_min": HelpEntry(
        summary="How long a drip run waits before giving way to misting or fog.",
        effect=(
            "Sent to the controller in the irrigation program. When climate control keeps asking for water during "
            "a drip run, the drip pauses after this many continuous minutes for one mist pulse or up to 2 min of "
            "fog, then resumes."
        ),
        raise_="Drip runs finish with fewer interruptions; humidity control waits longer for water.",
        lower="Drip runs give way to misting or fog sooner; drip runs take longer to finish.",
        refs=(f"{_IRR}::if (r.yield_accum_ms >= uint32_t(e.prog.climate_yield_min) * 60000u) {{",),
    ),
    "sw_night_dryout_enabled": HelpEntry(
        summary="Master switch for night dry-out: short vent pulses with drier outdoor air while heat1 holds warmth.",
        effect=(
            "At night, when VPD is below the band low edge (never below 0.74 kPa) and the outdoor dewpoint is "
            "Night Dryout DP Margin below indoor, the controller opens the vent with the lead fan for a pulse, then "
            "rests closed while the electric heater (heat1) reheats. The gas heater (heat2) never runs for this."
        ),
        raise_="On: humid nights are dried out with vent pulses and electric reheat; more heat1 energy at night.",
        lower="Off: no night dry-out pulses; the house can stay humid overnight.",
        caution="It stops for occupancy, a gas-heat latch, cold within 1 °F of the band low edge, and the nightly "
        "energy cap (Night Dryout Cap).",
        refs=(
            'firmware/lib/greenhouse_logic.h::if (!sp.night_dryout_enabled) return "disabled";',
            "firmware/lib/greenhouse_logic.h::NIGHT_DRYOUT_MIN_FLOOR_KPA",
        ),
    ),
    "night_dryout_pulse_s": HelpEntry(
        summary="How long each night dry-out pulse runs the vent and lead fan, in seconds.",
        effect=(
            "During a dry-out pulse the vent is open and the lead fan runs for this many seconds while the electric "
            "heater (heat1) holds the temperature; a closed rest follows."
        ),
        raise_="Longer pulses exchange more air each time: faster drying, more heat lost and reheated.",
        lower="Shorter pulses: gentler drying with smaller temperature dips.",
        caution="Pulse plus rest is kept at least 15 min, so there are at most 4 pulses an hour.",
        refs=("firmware/lib/greenhouse_types.h::sp.night_dryout_pulse_s = std::max<uint32_t>(60u",),
    ),
    "night_dryout_rest_s": HelpEntry(
        summary="Closed rest after each night dry-out pulse while heat1 reheats, in seconds.",
        effect=(
            "After a pulse the vent stays closed for this many seconds while the electric heater (heat1) brings the "
            "house back to its hold temperature; then the next pulse may start if every condition still holds."
        ),
        raise_="Fewer pulses an hour: slower drying, less reheat energy.",
        lower="More pulses an hour, down to the 15 min pulse-plus-rest minimum: faster drying, more energy.",
        caution="The controller lengthens the rest so pulse plus rest is at least 15 min.",
        refs=("firmware/lib/greenhouse_types.h::sp.night_dryout_rest_s = 900u - sp.night_dryout_pulse_s;",),
    ),
    "night_dryout_dp_margin_f": HelpEntry(
        summary="How much drier (by dewpoint) outdoor air must be than indoor before a dry-out pulse, in °F.",
        effect=(
            "A night dry-out pulse starts only with fresh weather data and an outdoor dewpoint at least this far "
            "below the indoor dewpoint, so the exchange really removes moisture."
        ),
        raise_="Pulses only when outdoor air is clearly drier: fewer pulses, each more effective.",
        lower="Pulses on smaller dewpoint gaps: more pulses, some of which dry the house less.",
        refs=(
            "firmware/lib/greenhouse_logic.h::in.outdoor_dewpoint_f <= (in.dew_point_f - sp.night_dryout_dp_margin_f)",
        ),
    ),
    "night_dryout_kwh_cap": HelpEntry(
        summary="Nightly limit on the electric reheat the dry-out may use, in kWh.",
        effect=(
            "The controller estimates heat1's reheat energy for dry-out pulses each night; once it reaches this cap, "
            "no new pulse starts until the next night. 0 turns new pulses off."
        ),
        raise_="More dry-out pulses allowed per night: drier nights, more electricity.",
        lower="Dry-out stops earlier in the night: less electricity, the house may stay humid.",
        refs=("firmware/lib/greenhouse_logic.h::state.dryout_reheat_wh < sp.night_dryout_kwh_cap * 1000.0f",),
    ),
}
