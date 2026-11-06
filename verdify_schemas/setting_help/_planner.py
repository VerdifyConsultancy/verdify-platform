"""Operator text for the planner rows of the settings catalog (see ``verdify_schemas.setting_help``)."""

from __future__ import annotations

from verdify_schemas.setting_help._entry import HelpEntry

ENTRIES: dict[str, HelpEntry] = {
    # Fan 2 latches at the band high edge plus this (stage2_escalation_latch, high side).
    "cool_stage2_over_high_f": HelpEntry(
        summary="How far above the band high edge the house must get before fan 2 joins fan 1.",
        effect=(
            "While ventilating, the controller runs the lead fan at the band high edge and latches the second fan on "
            "once the house is this many °F above that edge. Fan 2 stays on until the house cools below that point "
            "by Cool Stage 2 Exit Hyst."
        ),
        raise_="Fan 2 starts later: the house runs hotter above the band before both fans move air, with less fan run time.",
        lower="Fan 2 starts sooner (at 0 °F, as soon as cooling starts): faster cooling and more fan run time.",
        caution="Cool All Fans At High overrides this and runs both fans as soon as the house is above the band.",
        refs=(
            "firmware/lib/greenhouse_logic.h::const float fan2_entry = temp_high + sp.cool_stage2_over_high_f;",
            "firmware/lib/greenhouse_logic.h::state.fan2_latched",
        ),
    ),
    # VENTILATE exits at temp_high - cooling_exit_hysteresis (floored at 3 °F in the cold-vent regime).
    "cool_exit_hysteresis_f": HelpEntry(
        summary="How far below the band high edge the house must cool before ventilation stops.",
        effect=(
            "Cooling starts when the house rises above the band high edge and keeps the vent and fans running until "
            "it falls this many °F below that edge. When outdoor air is cold the controller uses at least 3 °F."
        ),
        raise_="Each cooling run lasts longer and leaves the house cooler, with fewer, longer vent cycles.",
        lower="Cooling stops closer to the high edge: shorter runs, a warmer house, and more frequent vent cycles.",
        refs=(
            "firmware/lib/greenhouse_logic.h::? in.temp_f > (temp_high - cooling_exit_hysteresis)",
            "firmware/lib/greenhouse_logic.h::COLD_VENT_COOL_EXIT_HYST_MIN_F",
        ),
    ),
    # fan2_exit = fan2_entry - cool_stage2_exit_hysteresis_f.
    "cool_stage2_exit_hysteresis_f": HelpEntry(
        summary="How far the house must cool below the fan 2 start point before fan 2 turns off.",
        effect=(
            "Once fan 2 has latched on above the band high edge plus Cool Stage 2 Over High, it stays on until the "
            "house falls this many °F below that start point, so it cannot flicker at the threshold."
        ),
        raise_="Fan 2 runs longer once started, cooling the house further before it drops back to one fan.",
        lower="Fan 2 drops out sooner after the peak, with more chance of starting and stopping near the threshold.",
        refs=(
            "firmware/lib/greenhouse_logic.h::const float fan2_exit  = fan2_entry - sp.cool_stage2_exit_hysteresis_f;",
        ),
    ),
    # estimate_moisture_exchange: k blends indoor toward outdoor for the projected vent mix.
    "vent_exchange_fraction": HelpEntry(
        summary="How much outdoor air the controller assumes one venting cycle mixes into the house (0 to 1).",
        effect=(
            "The controller predicts what venting would do to VPD by mixing this fraction of outdoor air into the "
            "house air. It vents to dry or to add moisture only when that prediction beats a small margin; when the "
            "mixed air would fall below the band low edge it heats instead."
        ),
        raise_=(
            "Venting looks stronger: the controller chooses the vent to dry or humidify more readily, and also "
            "predicts over-cooling sooner on cold days, which favors heat."
        ),
        lower="Venting looks weaker: the controller vents for moisture less often and leans on heat or does nothing.",
        refs=(
            "firmware/lib/greenhouse_logic.h::const float k = sp.vent_exchange_fraction;",
            "firmware/lib/greenhouse_logic.h::vent_overcools = T_mix < sp.temp_low;",
        ),
    ),
    # apply_band_track_pinch: f=0 is a no-op; the planner bounds are 0..0.
    "band_track_fraction": HelpEntry(
        summary="How tightly the controller holds the band target instead of acting only at the band edges.",
        effect=(
            "At 0 the controller lets temperature and VPD float anywhere inside the crop band and acts only at its "
            "edges. A value above 0 narrows the band the controller acts on toward its target. Only 0 is accepted "
            "today; the safety limits never move."
        ),
        raise_=(
            "Values above 0 are refused. Above 0, heating, cooling, misting and drying would start closer to the "
            "target, using more energy, water and equipment cycles."
        ),
        lower="0 is the lowest value and the normal one: the controller floats inside the crop band.",
        refs=(
            "firmware/lib/greenhouse_logic.h::inline Setpoints apply_band_track_pinch",
            "firmware/lib/greenhouse_logic.h::if (f <= 0.0f) return sp;",
        ),
    ),
    # outdoor_cold_for_vent: outdoor < temp_low - delta widens the cooling entry and exit margins.
    "cold_vent_guard_delta_f": HelpEntry(
        summary="How far below the band low edge outdoor air must be before venting is treated as cold-air venting.",
        effect=(
            "When outdoor air is colder than the band low edge minus this many °F, the controller waits until the "
            "house is further above the high edge before it vents, cools at least 3 °F past the edge before stopping, "
            "and will not vent in much colder air to add moisture."
        ),
        raise_="The cold-air caution applies only on colder days, so the vent opens more readily in cool weather.",
        lower="The caution starts on milder days: fewer, longer vent cycles in cool weather and less risk of chilling.",
        refs=(
            "firmware/lib/greenhouse_logic.h::in.outdoor_temp_f < (sp.temp_low - sp.cold_vent_guard_delta_f)",
            "firmware/lib/greenhouse_logic.h::cold_vent_cooling_entry_margin_f",
        ),
    ),
    # resolve_equipment VENTILATE: needs_both |= cool_all_fans_at_high_enabled && temp > high edge.
    "sw_cool_all_fans_at_high_enabled": HelpEntry(
        summary="Runs both fans as soon as the house rises above the band high edge.",
        effect=(
            "Normally ventilation starts with one fan and adds fan 2 only past Cool Stage 2 Over High. With this on, "
            "both fans run whenever the house is above the band high edge while ventilating."
        ),
        raise_="On: both fans run at once above the band, cooling faster at the cost of more fan power and wear.",
        lower="Off: one fan first, fan 2 only past Cool Stage 2 Over High.",
        refs=("firmware/lib/greenhouse_logic.h::(sp.cool_all_fans_at_high_enabled && in.temp_f > Thigh)",),
    ),
    # Band-first uses temp_hysteresis as the margin below temp_high for the dry override and the heat-dehum nudge.
    "temp_hysteresis": HelpEntry(
        summary="Temperature margin below the band high edge that the house must be under for some drying actions.",
        effect=(
            "The controller seals the house to humidify in an extreme dry spell, or runs the electric heater (heat1) "
            "with the vent closed to dry the air at night, only while the house is at least this many °F below the "
            "band high edge."
        ),
        raise_="Those actions need a cooler house, so they run less often near the top of the band.",
        lower="Those actions may run closer to the band high edge, with more risk of then needing to cool.",
        refs=(
            "firmware/lib/greenhouse_logic.h::(in.temp_f + GH_HEAT_ASSIST_PROBE_DF) <= (sp.temp_high - sp.temp_hysteresis)",
            "firmware/lib/greenhouse_logic.h::&& (in.temp_f < (sp.temp_high - sp.temp_hysteresis));",
        ),
    ),
    # needs_heating_s1 = temp < band_heat_target_f + heat_hysteresis.
    "heat_hysteresis": HelpEntry(
        summary="How far above the heating target the electric heater keeps running.",
        effect=(
            "The heating target sits a quarter of the way up the temperature band. The electric heater (heat1, the "
            "primary heat) runs while the house is below that target plus this many °F. The same line gates drying "
            "with heat at night."
        ),
        raise_="Heat1 runs to a warmer temperature: a warmer lower band and more electric heating.",
        lower="Heat1 stops closer to the heating target: a cooler lower band and less heating.",
        caution="The gas heater (heat2, the backup) is separate: it latches below the band low edge and stops at the target.",
        refs=(
            "firmware/lib/greenhouse_logic.h::const bool needs_heating_s1 = in.temp_f < (heat_target + sp.heat_hysteresis);",
            "firmware/lib/greenhouse_logic.h::BAND_HEAT_TARGET_FRACTION = 0.25f",
        ),
    ),
    # band_vpd_hysteresis: min(requested, 33 % of band width); used for seal exit and dehum entry.
    "vpd_hysteresis": HelpEntry(
        summary="VPD margin inside the band edges that ends humidifying and starts drying.",
        effect=(
            "Humidifying stops once VPD falls this far below the band high edge, and drying by vent starts only once "
            "VPD falls this far below the band low edge. The controller caps it at a third of the VPD band width."
        ),
        raise_="Humidifying runs longer and drying starts later: fewer, longer cycles and a wider VPD swing.",
        lower="Humidifying stops sooner and drying starts closer to the edge: tighter VPD with more cycling.",
        refs=(
            "firmware/lib/greenhouse_logic.h::inline float band_vpd_hysteresis",
            "firmware/lib/greenhouse_logic.h::const bool dehum_enter = in.vpd_kpa < (sp.vpd_low - dehum_hysteresis);",
        ),
    ),
    # controls.yaml: global_stressed = avg_vpd > mister_engage_kpa; S1 off below engage - hysteresis.
    "mister_engage_kpa": HelpEntry(
        summary="House VPD above which the misters may start pulsing once humidifying has escalated to them.",
        effect=(
            "When the controller is humidifying past the fog stage, the misters pulse if the house VPD is above this "
            "value or any zone is drier than its crop target. They stop once no zone is dry and VPD falls below this "
            "value minus the mister hysteresis."
        ),
        raise_="The misters wait for drier air and stop sooner: less mister water, a drier house.",
        lower="The misters start in less dry air and run longer: more water and humidity.",
        caution="While the house is dry-stressed, a value far above the VPD band high edge is pulled back toward that edge.",
        refs=(
            "firmware/greenhouse/controls.yaml::bool global_stressed = (avg_vpd > pol(verdify_policy::kPF_mister_engage_kpa",
            "firmware/greenhouse/controls.yaml::&& avg_vpd < (pol(verdify_policy::kPF_mister_engage_kpa, id(mister_engage_kpa)) - MISTER_HYST)",
        ),
    ),
    # controls.yaml: S1 -> S2 when avg_vpd > mister_all_kpa after Mister All Delay; S2 -> S1 below it minus hysteresis.
    "mister_all_kpa": HelpEntry(
        summary="House VPD above which the misters move from one zone at a time to rotating through all zones.",
        effect=(
            "After single-zone pulsing has run for Mister All Delay, the misters rotate through every mister zone if "
            "the house VPD is above this value. They drop back to single-zone pulsing below it minus the mister "
            "hysteresis."
        ),
        raise_="All-zone misting needs drier air: less water, slower recovery from dry spells.",
        lower="All-zone misting starts in less dry air: more water and faster humidity recovery.",
        caution="While the house is dry-stressed, a value that would block all-zone misting is pulled back near the band.",
        refs=(
            "firmware/greenhouse/controls.yaml::&& avg_vpd > pol(verdify_policy::kPF_mister_all_kpa, id(mister_all_kpa))",
        ),
    ),
    # controls.yaml: S1 engages after humid_s1_duration_ms >= MISTER_ENGAGE_DELAY_MS.
    "mister_engage_delay_s": HelpEntry(
        summary="Seconds of continuous humidifying demand before the first mister pulse.",
        effect=(
            "Once humidifying has escalated to the misters, the controller waits this long before the first pulse, "
            "so a brief dry blip does not start them."
        ),
        raise_="Misters start later in each dry spell: less water, slower response.",
        lower="Misters start sooner: faster response and more mister water.",
        refs=("firmware/greenhouse/controls.yaml::const uint32_t MISTER_ENGAGE_DELAY_MS",),
    ),
    # mist_s2_delay_ms: the fog-only dwell before misters and the S1 -> S2 dwell, in the FSM and in controls.yaml.
    "mister_all_delay_s": HelpEntry(
        summary="Seconds each humidifying stage runs before the next, heavier one may start.",
        effect=(
            "Humidifying starts with fog. If VPD stays above the band high edge plus Fog Escalation for this long, "
            "the misters join one zone at a time; after this long again, they rotate through all zones if VPD is "
            "above VPD Mister All."
        ),
        raise_="Each step waits longer: gentler, lower-water humidifying and slower recovery from dry spells.",
        lower="Misters join fog sooner and all zones run sooner: faster recovery and more water.",
        refs=(
            "firmware/lib/greenhouse_logic.h::(state.mist_stage_timer_ms >= sp.mist_s2_delay_ms || fog_gated)",
            "firmware/greenhouse/controls.yaml::uint32_t(pol(verdify_policy::kPF_mister_all_delay_s, id(mister_all_delay_s))) * 1000UL",
        ),
    ),
    # controls.yaml: climate_water_budget_block; irrigation engine soft_budget_centigal.
    "mister_water_budget_gal": HelpEntry(
        summary="Daily soft cap on mister water, in gallons.",
        effect=(
            "Once today's mister water reaches this cap, the controller stops climate misting and holds fog, unless "
            "VPD anywhere is above Safety VPD Max. The irrigation program uses the same cap. A separate hard daily "
            "ceiling stops all wetting even in an emergency."
        ),
        raise_="More misting is allowed each day before the cap stops it.",
        lower="The cap stops misting and fog earlier on dry days, saving water but risking dry stress.",
        refs=(
            "firmware/greenhouse/controls.yaml::id(mister_water_today) >= pol(verdify_policy::kPF_mister_water_budget_gal",
            "firmware/greenhouse/controls.yaml::irr_in.soft_budget_centigal",
        ),
    ),
    # controls.yaml: PULSE_ON_MS for climate mister pulses.
    "mister_pulse_on_s": HelpEntry(
        summary="Seconds each climate mister pulse runs on one zone.",
        effect=(
            "When the controller mists for VPD, each pulse opens one zone's misters for this long, then waits Mister "
            "Pulse Gap before the next pulse. In all-zone rotation a very dry zone's pulse can be lengthened by "
            "Mister VPD Weight."
        ),
        raise_="Longer pulses put more water down per pulse: faster humidity rise and wetter foliage.",
        lower="Shorter pulses: less water per pulse, gentler humidifying.",
        refs=(
            "firmware/greenhouse/controls.yaml::uint32_t(pol(verdify_policy::kPF_mister_pulse_on_s, id(mister_pulse_on_s))) * 1000UL",
        ),
    ),
    # controls.yaml: PULSE_GAP_MS between climate mister pulses.
    "mister_pulse_gap_s": HelpEntry(
        summary="Seconds of pause between climate mister pulses, so the mist can evaporate.",
        effect="Between VPD mister pulses the controller keeps every mister off for this long before the next pulse.",
        raise_="Longer pauses: less water per hour and more time for the mist to evaporate.",
        lower="Shorter pauses: more water per hour and faster humidifying.",
        refs=(
            "firmware/greenhouse/controls.yaml::uint32_t(pol(verdify_policy::kPF_mister_pulse_gap_s, id(mister_pulse_gap_s))) * 1000UL",
        ),
    ),
    # controls.yaml mister_zone_can_on; irrigation engine mister_min_off_s fence.
    "mister_min_off_s": HelpEntry(
        summary="Shortest time a mister zone must stay off before it may pulse again.",
        effect=(
            "A mister zone may not reopen within this many seconds of its own last pulse. The controller picks "
            "another ready zone or waits. The irrigation program applies the same fence to its mist runs."
        ),
        raise_="Each zone rests longer between pulses: fewer valve cycles and less water on any one zone.",
        lower="A zone may pulse again sooner: more water on the driest zone and more valve cycling.",
        refs=(
            "firmware/greenhouse/controls.yaml::const uint32_t MISTER_MIN_OFF_MS",
            "firmware/lib/irrigation_program.h::mister_min_off_s",
        ),
    ),
    # controls.yaml: weighted_ms = PULSE_ON_MS * mister_vpd_weight for a very dry zone in S2.
    "mister_vpd_weight": HelpEntry(
        summary="Pulse-length multiplier for a very dry zone while the misters rotate through all zones.",
        effect=(
            "In all-zone rotation, a pulse lasts Mister Pulse ON times this value when its zone is the driest (the "
            "first pulse) or more than 20% above its zone VPD target (later pulses); other pulses keep the normal "
            "length. It never changes which zone is chosen."
        ),
        raise_="The driest zones get longer pulses: more water where it is driest.",
        lower="Pulses stay closer to the normal length (below 1 they are shorter than normal).",
        refs=(
            "firmware/greenhouse/controls.yaml::weighted_ms = uint32_t(PULSE_ON_MS * pol(verdify_policy::kPF_mister_vpd_weight, id(mister_vpd_weight)));",
            "firmware/greenhouse/controls.yaml::zone_vpd > zone_target * 1.2f",
        ),
    ),
    # controls.yaml: nbias * sin^2 added to the VPD band between sunset and sunrise.
    "night_vpd_bias_kpa": HelpEntry(
        summary="Extra VPD added to the night VPD band, most at solar midnight, without changing the crop band.",
        effect=(
            "Between sunset and sunrise the controller raises the low edge, target and high edge of the VPD band by "
            "up to this amount, rising smoothly to the full amount at solar midnight and back to 0 at sunrise."
        ),
        raise_="Drier nights: the controller dries the air at a higher VPD and humidifies only in drier air.",
        lower="Back toward the crop band's own night VPD (0 adds nothing): more humid nights.",
        refs=("firmware/greenhouse/controls.yaml::const float nbias = pol(verdify_policy::kPF_night_vpd_bias_kpa",),
    ),
    # controls.yaml select_most_stressed_zone: center score * mister_center_penalty; rank arbiter decides first.
    "mister_center_penalty": HelpEntry(
        summary="Weight on the center zone's dryness when the controller scores mister zones.",
        effect=(
            "Center has no VPD sensor of its own, so its score uses the house VPD times this weight. The zone "
            "priority ranking decides first whenever two or more zones may mist, so this rarely changes the choice."
        ),
        raise_="Center scores closer to its full dryness and can win a pulse more often.",
        lower="Center scores lower; at 0 it is chosen only when no other zone qualifies.",
        refs=(
            "firmware/greenhouse/controls.yaml::* pol(verdify_policy::kPF_mister_center_penalty, id(mister_center_penalty))",
        ),
    ),
    # Relay table R[4] min_on.
    "min_fog_on_s": HelpEntry(
        summary="Shortest time the fogger runs once it starts.",
        effect="Once the fogger turns on, the controller keeps it on for at least this long unless a safety rule cuts it.",
        raise_="Longer fog bursts: fewer starts, more water per burst.",
        lower="Fog can stop sooner after starting: shorter bursts and more starts.",
        refs=("firmware/greenhouse/controls.yaml::R[4].min_on_ms = MIN_FOG_ON_MS;",),
    ),
    # Relay table R[4] min_off.
    "min_fog_off_s": HelpEntry(
        summary="Shortest time the fogger stays off before it may start again.",
        effect="After the fogger turns off, the controller keeps it off for at least this long before it can restart.",
        raise_="Longer rests between fog bursts: fewer starts, slower humidifying.",
        lower="Fog can restart sooner: faster response and more cycling.",
        refs=("firmware/greenhouse/controls.yaml::R[4].min_off_ms = MIN_FOG_OFF_MS;",),
    ),
    # Relay table R[0]/R[1] min_on (both heaters).
    "min_heat_on_s": HelpEntry(
        summary="Shortest time a heater runs once it starts (electric heat1 and gas heat2).",
        effect="Once a heater turns on, the controller keeps it on for at least this long, which protects the relays.",
        raise_="Longer minimum heater runs: fewer starts and a bit more overshoot above the heating target.",
        lower="Heaters may stop sooner: tighter temperature with more relay starts.",
        refs=("firmware/greenhouse/controls.yaml::R[0].min_on_ms = R[1].min_on_ms = MIN_HEAT_ON_MS;",),
    ),
    # Relay table R[2]/R[3] min_on (both fans).
    "min_fan_on_s": HelpEntry(
        summary="Shortest time a fan runs once it starts.",
        effect="Once fan 1 or fan 2 turns on, the controller keeps it on for at least this long.",
        raise_="Longer minimum fan runs: fewer starts and more cooling or air exchange per start.",
        lower="Fans may stop sooner: less run time and more starts.",
        refs=("firmware/greenhouse/controls.yaml::R[2].min_on_ms = R[3].min_on_ms = MIN_FAN_ON_MS;",),
    ),
    # Relay table R[2]/R[3] min_off.
    "min_fan_off_s": HelpEntry(
        summary="Shortest time a fan stays off before it may start again.",
        effect="After a fan turns off, the controller keeps it off for at least this long. A manual fan press skips this wait.",
        raise_="Longer rests between fan runs: fewer starts, slower response to rising heat.",
        lower="Fans can restart sooner: faster response and more cycling.",
        refs=("firmware/greenhouse/controls.yaml::R[2].min_off_ms = R[3].min_off_ms = MIN_FAN_OFF_MS;",),
    ),
    # Relay table R[5] min_on.
    "min_vent_on_s": HelpEntry(
        summary="Shortest time the vent stays open once it opens.",
        effect="Once the vent opens, the controller keeps it open for at least this long, giving the actuator time to travel.",
        raise_="Longer minimum vent openings: fewer actuator cycles, more air exchange per opening.",
        lower="The vent may close sooner: less exchange per opening and more actuator cycles.",
        refs=("firmware/greenhouse/controls.yaml::R[5].min_on_ms = MIN_VENT_ON_MS;",),
    ),
    # Relay table R[5] min_off.
    "min_vent_off_s": HelpEntry(
        summary="Shortest time the vent stays closed before it may open again.",
        effect="After the vent closes, the controller keeps it closed for at least this long before it can reopen.",
        raise_="Longer closed periods: fewer actuator cycles, slower response to heat or humidity.",
        lower="The vent may reopen sooner: faster response and more actuator cycles.",
        refs=("firmware/greenhouse/controls.yaml::R[5].min_off_ms = MIN_VENT_OFF_MS;",),
    ),
    # humidify_ready = vpd_high && vpd_watch_timer_ms >= vpd_watch_dwell_ms.
    "vpd_watch_dwell_s": HelpEntry(
        summary="Seconds VPD must stay above the band high edge before the controller starts humidifying.",
        effect=(
            "The controller seals the house and starts fog (then misters) only after VPD has been above the band "
            "high edge continuously for this long. A drop back into the band resets the wait."
        ),
        raise_="Humidifying starts later in each dry spell: fewer short cycles, more time spent dry.",
        lower="Humidifying starts sooner: faster response and more on and off cycles.",
        refs=(
            "firmware/lib/greenhouse_logic.h::const bool humidify_ready = vpd_high && state.vpd_watch_timer_ms >= sp.vpd_watch_dwell_ms;",
        ),
    ),
    # sealed_timer_ms >= sealed_max_ms -> IDLE with mist_backoff.
    "mist_max_closed_vent_s": HelpEntry(
        summary="Longest time the controller keeps the house sealed to humidify before it gives up for a while.",
        effect=(
            "After humidifying with the vent closed for this long, the controller stops, returns to normal control "
            "and waits Mist Backoff before sealing again, so heat cannot build up behind a closed vent."
        ),
        raise_="Longer sealed humidifying runs: more humidity gain per run, more heat build-up.",
        lower="Shorter sealed runs: less heat build-up, but dry spells may not be fully corrected.",
        refs=("firmware/lib/greenhouse_logic.h::if (state.sealed_timer_ms >= sp.sealed_max_ms) {",),
    ),
    # relief_duration_ms only feeds the legacy THERMAL_RELIEF path; band-first never selects it.
    "mist_thermal_relief_s": HelpEntry(
        summary="Length of the forced vent-open relief after a sealed misting run (older controller path only).",
        effect=(
            "The current controller does not use this value: when a sealed run times out it returns to normal "
            "control and waits Mist Backoff instead of forcing a relief burst."
        ),
        raise_="No effect on the current controller.",
        lower="No effect on the current controller.",
        refs=("firmware/lib/greenhouse_logic.h::if (state.relief_timer_ms >= sp.relief_duration_ms) {",),
    ),
    # stress_wet_override_permitted is a no-op shim on the curve-only controller.
    "sw_direct_wet_stress_override_enabled": HelpEntry(
        summary="Older switch that let misting run past drying windows during dry stress.",
        effect=(
            "The current controller does not use it: misting follows VPD against the band directly, held back only "
            "by occupancy, a fertilizer soak and the dew-point margin."
        ),
        raise_="No effect on the current controller.",
        lower="No effect on the current controller.",
        refs=(
            "firmware/lib/greenhouse_logic.h::inline bool stress_wet_override_permitted(const SensorInputs&, const Setpoints&) noexcept {",
        ),
    ),
    # Vent mist assist candidate needs dry_excess >= direct_wet_stress_vpd_margin_kpa.
    "direct_wet_stress_vpd_margin_kpa": HelpEntry(
        summary="How far VPD must be above the band high edge before misting may assist ventilation.",
        effect=(
            "While cooling with the vent open, the controller adds mister pulses only when VPD is at least this far "
            "above the band high edge."
        ),
        raise_="Misting during ventilation needs drier air: less water, hotter and drier cooling runs.",
        lower="Misting assists ventilation sooner: more evaporative cooling and water use.",
        refs=("firmware/lib/greenhouse_logic.h::(needs_cooling && dry_excess >= sp.direct_wet_stress_vpd_margin_kpa)",),
    ),
    # min_dew_margin_for_wetting (day); also the irrigation engine dew-breach margin.
    "direct_wet_stress_min_dew_margin_f": HelpEntry(
        summary="Minimum gap between air temperature and dew point before misting or fog may run by day.",
        effect=(
            "The controller blocks misting and fog while the house is within this many °F of its dew point, so "
            "water does not condense on leaves. At night it uses the larger of this and Night Stress Min Dew Margin. "
            "The irrigation program uses the same margin."
        ),
        raise_="Wetting stops in moister air: less condensation risk, but drier spells go uncorrected longer.",
        lower="Wetting is allowed closer to the dew point: more humidifying, more risk of wet foliage.",
        refs=(
            "firmware/lib/greenhouse_logic.h::inline float min_dew_margin_for_wetting",
            "firmware/greenhouse/controls.yaml::pol(verdify_policy::kPF_direct_wet_stress_min_dew_margin_f, id(direct_wet_stress_min_dew_margin_f)));",
        ),
    ),
    # controls.yaml econ gate: dH <= enthalpy_open unblocks.
    "enthalpy_open": HelpEntry(
        summary="Outdoor-minus-indoor air energy (kJ per kg) at or below which outside air may be used for drying.",
        effect=(
            "The economiser compares the heat and moisture energy of outdoor and indoor air. When outdoor air is "
            "this much lower or more, it allows venting to dry the house again and stops electric heat used only to "
            "dry the air."
        ),
        raise_="Outside air is allowed for drying sooner, even when it is closer in energy to indoor air.",
        lower="Outdoor air must be clearly lower in energy before it is used to dry the house.",
        caution="Keep it below Enthalpy Close, or the economiser will flip back and forth.",
        refs=(
            "firmware/greenhouse/controls.yaml::if(dH <= pol(verdify_policy::kPF_enthalpy_open, id(enthalpy_open_kjkg)))  econ_block = false;",
        ),
    ),
    # controls.yaml econ gate: dH >= enthalpy_close blocks.
    "enthalpy_close": HelpEntry(
        summary="Outdoor-minus-indoor air energy (kJ per kg) at or above which outside air is not used for drying.",
        effect=(
            "When outdoor air carries this much more heat and moisture energy than indoor air, the economiser blocks "
            "venting to dry the house; the controller may run the electric heater (heat1) by day to dry instead."
        ),
        raise_="Outside air keeps being used for drying until it is much more energetic than indoor air.",
        lower="Drying by vent is blocked sooner as outdoor air warms or moistens.",
        caution="Keep it above Enthalpy Open, or the economiser will flip back and forth.",
        refs=(
            "firmware/greenhouse/controls.yaml::if(dH >= pol(verdify_policy::kPF_enthalpy_close, id(enthalpy_close_kjkg))) econ_block = true;",
            "firmware/lib/greenhouse_logic.h::&& in.vpd_kpa < vpd_low_eff && sp.econ_block",
        ),
    ),
    # override_summer_vent only relabels VENT_COOL as summer_vent on the band-first controller.
    "sw_summer_vent_enabled": HelpEntry(
        summary="Marks cooling runs where cooler, drier outdoor air made venting the better choice.",
        effect=(
            "When the house needs cooling and is too dry, and outdoor air is cooler and drier by Vent Prefer Temp "
            "Delta and Vent Prefer DP Delta, the controller labels the run a summer vent. The current controller "
            "already cools before it humidifies, so this changes only the label."
        ),
        raise_="On: those cooling runs are labelled summer vent in the controller's reported reason.",
        lower="Off: the same runs are labelled ordinary high-temperature cooling. Equipment behaves the same.",
        refs=(
            "firmware/lib/greenhouse_logic.h::state.override_summer_vent = sp.sw_summer_vent_enabled",
            'firmware/lib/greenhouse_logic.h::state.override_summer_vent ? "summer_vent" : "temp_high"',
        ),
    ),
    "vent_prefer_temp_delta_f": HelpEntry(
        summary="How much cooler outdoor air must be (°F) for a cooling run to count as a summer vent.",
        effect=(
            "One of the conditions for the summer vent label: outdoor air at least this many °F cooler than indoor. "
            "On the current controller it changes only the reported reason, not the equipment."
        ),
        raise_="Fewer cooling runs are labelled summer vent. Equipment behaves the same.",
        lower="More cooling runs are labelled summer vent. Equipment behaves the same.",
        refs=("firmware/lib/greenhouse_logic.h::in.outdoor_temp_f < (in.temp_f - sp.vent_prefer_temp_delta_f)",),
    ),
    "vent_prefer_dp_delta_f": HelpEntry(
        summary="How much lower the outdoor dew point must be (°F) for a cooling run to count as a summer vent.",
        effect=(
            "One of the conditions for the summer vent label: outdoor dew point at least this many °F below indoor. "
            "On the current controller it changes only the reported reason, not the equipment."
        ),
        raise_="Fewer cooling runs are labelled summer vent. Equipment behaves the same.",
        lower="More cooling runs are labelled summer vent. Equipment behaves the same.",
        refs=("firmware/lib/greenhouse_logic.h::in.outdoor_dewpoint_f < (in.dew_point_f - sp.vent_prefer_dp_delta_f)",),
    ),
    # Outdoor freshness gates the moisture-exchange estimator and the summer vent label.
    "outdoor_staleness_max_s": HelpEntry(
        summary="Oldest outdoor weather reading, in seconds, the controller still trusts.",
        effect=(
            "Outdoor temperature and humidity older than this are ignored: the controller then will not vent to dry "
            "or humidify based on outdoor air, and falls back to heat or does nothing for moisture."
        ),
        raise_="Older outdoor readings still count: venting for moisture continues through short weather-data gaps.",
        lower="Outdoor readings expire sooner: moisture venting stops quickly when weather data lags.",
        refs=(
            "firmware/lib/greenhouse_logic.h::&& in.outdoor_data_age_s < sp.outdoor_staleness_max_s;",
            "firmware/lib/greenhouse_logic.h::const bool outdoor_data_fresh = in.outdoor_data_age_s < sp.outdoor_staleness_max_s;",
        ),
    ),
    # controls.yaml: willFog = false while the vent is open, except vent fog assist.
    "sw_fog_closes_vent": HelpEntry(
        summary="Holds fog off while the vent is physically open, except when fog assists cooling.",
        effect=(
            "With this on, the fogger waits until the vent reports closed, so fog is not blown outside. Fog that "
            "assists ventilation in hot, dry air may still run with the vent open."
        ),
        raise_="On: fog waits for the vent to close, so less fog water is lost outdoors.",
        lower="Off: fog may run while the vent is still open, adding humidity sooner but losing some outdoors.",
        refs=(
            "firmware/greenhouse/controls.yaml::if (polb(verdify_policy::kPF_sw_fog_closes_vent, id(fog_closes_vent)) && vent_is_open && !open_vent_fog_assist) {",
        ),
    ),
    # controls.yaml mister_vent_ok.
    "sw_mister_closes_vent": HelpEntry(
        summary="Holds ordinary mister pulses off while the vent is physically open.",
        effect=(
            "With this on, humidifying mister pulses wait until the vent reports closed, and a pulse in progress "
            "stops if the vent opens. Misting that assists ventilation in hot, dry air is not affected."
        ),
        raise_="On: misters wait for the vent to close, so less mist is lost outdoors, but humidifying starts later.",
        lower="Off: misters may pulse while the vent is still open, humidifying sooner but losing some mist.",
        refs=(
            "firmware/greenhouse/controls.yaml::const bool mister_vent_ok = open_vent_mister_assist || !polb(verdify_policy::kPF_sw_mister_closes_vent",
        ),
    ),
    # Only feeds the moisture block-reason label in controls.yaml.
    "sw_direct_wet_gate_enabled": HelpEntry(
        summary="Older master switch for the daytime direct-wetting windows.",
        effect=(
            "The current controller does not use it to decide anything: it only changes the reason the controller "
            "reports when no mister zone is available. Wetting windows now belong to the irrigation program."
        ),
        raise_="No effect on equipment; only the reported block reason changes.",
        lower="No effect on equipment; only the reported block reason changes.",
        refs=(
            "firmware/greenhouse/controls.yaml::} else if(!polb(verdify_policy::kPF_sw_direct_wet_gate_enabled, id(direct_wet_gate_enabled))) {",
        ),
    ),
    # Relay table R[0]/R[1] min_off.
    "min_heat_off_s": HelpEntry(
        summary="Shortest time a heater stays off before it may start again (electric heat1 and gas heat2).",
        effect="After a heater turns off, the controller keeps it off for at least this long before it can restart.",
        raise_="Longer rests between heater runs: fewer starts, more temperature dip before heat returns.",
        lower="Heaters can restart sooner: tighter temperature with more relay starts.",
        refs=("firmware/greenhouse/controls.yaml::R[0].min_off_ms = R[1].min_off_ms = MIN_HEAT_OFF_MS;",),
    ),
    # Dwell gate: hold the previous mode while in_dwell unless safety or a band error preempts.
    "sw_dwell_gate_enabled": HelpEntry(
        summary="Holds the controller in its current mode for a minimum time after each mode change.",
        effect=(
            "With this on, after a mode change the controller keeps the same mode for Dwell Gate before it changes "
            "again. Safety modes, and any temperature or VPD outside the band, override the hold at once."
        ),
        raise_="On: fewer quick mode switches while conditions sit inside the band.",
        lower="Off: the controller changes mode whenever its decision changes.",
        refs=("firmware/lib/greenhouse_logic.h::if (sp.sw_dwell_gate_enabled",),
    ),
    "dwell_gate_ms": HelpEntry(
        summary="How long, in milliseconds, the dwell gate holds a mode after it changes (300000 ms is 5 min).",
        effect=(
            "While Dwell Gate Enabled is on, the controller keeps a new mode for at least this long unless safety or "
            "an out-of-band temperature or VPD forces a change. It has no effect while that switch is off."
        ),
        raise_="Longer holds: fewer mode changes inside the band.",
        lower="Shorter holds: the controller can change mode sooner.",
        refs=(
            "firmware/lib/greenhouse_logic.h::const bool in_dwell = state.last_transition_tick_ms < sp.dwell_gate_ms;",
        ),
    ),
    # mist_backoff_timer_ms blocks a new sealed humidify until mist_backoff_ms passes or VPD recovers.
    "mist_backoff_s": HelpEntry(
        summary="Seconds the controller waits before sealing to humidify again after a sealed run times out.",
        effect=(
            "When a sealed humidifying run hits Mist Max Closed Vent, the controller will not seal to humidify again "
            "for this long. The wait ends early if VPD drops back into the band."
        ),
        raise_="Longer pauses after a timeout: less heat build-up, but dry spells last longer.",
        lower="The controller tries sealed humidifying again sooner: more humidity, more heat build-up.",
        refs=(
            "firmware/lib/greenhouse_logic.h::} else if (state.mist_backoff_timer_ms >= sp.mist_backoff_ms) {",
            'firmware/lib/greenhouse_logic.h::(sealed_backoff ? "mist_backoff" : wet_block_reason)',
        ),
    ),
    # Fog-first ladder: misters join above vpd_high + fog_escalation_kpa; fog candidates need dry_excess >= it.
    "fog_escalation_kpa": HelpEntry(
        summary="How far VPD must be above the band high edge before the controller steps up its humidifying.",
        effect=(
            "Sealed humidifying starts with fog. When VPD stays this far above the band high edge for Mister All "
            "Delay, the misters join. While cooling, fog assists the vent once VPD is this far above the band."
        ),
        raise_="Misters and cooling fog wait for drier air: less water, larger dry excursions.",
        lower="Misters and cooling fog join sooner: more water and faster humidity recovery.",
        caution="While the house is dry-stressed, a large value is pulled back so humidifying can still step up.",
        refs=(
            "firmware/lib/greenhouse_logic.h::const float escalate_at   = sp.vpd_high + sp.fog_escalation_kpa;",
            "firmware/lib/greenhouse_logic.h::if (in.vpd_kpa > (vpd_high_eff + sp.fog_escalation_kpa)) {",
        ),
    ),
}
