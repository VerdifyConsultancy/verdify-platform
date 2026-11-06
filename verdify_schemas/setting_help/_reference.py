"""Operator text for the reference rows of the settings catalog: retired and readback-context settings.

See ``verdify_schemas.setting_help``. Every row here is read-only, so none carries raise or lower text.
"""

from __future__ import annotations

from verdify_schemas.setting_help._entry import HelpEntry

_REG = "verdify_schemas/tunable_registry.py"
_CONTROLS = "firmware/greenhouse/controls.yaml"
_LOGIC = "firmware/lib/greenhouse_logic.h"
_TYPES = "firmware/lib/greenhouse_types.h"

_IRRIGATION_RETIRED = (
    "The controller ignores it and it stays at zero. Scheduled dawn and midday watering is retired; "
    "the center misters now follow only the normal VPD-driven misting."
)
_IRRIGATION_REFS = (f"{_CONTROLS}::Deliberate dawn/midday center watering is retired",)
_MICROPULSE_RETIRED = (
    "The overnight micro-pulse path was removed from the controller, so nothing reads this value. "
    "Night wetting now follows the night VPD band and the Night Stress Min Dew Margin."
)
_MICROPULSE_REFS = (
    "firmware/greenhouse/globals.yaml::CYC-4 overnight micro-pulse path DELETED",
    f"{_CONTROLS}::night emergency wetting (replaces CYC-4 micropulse)",
)

ENTRIES: dict[str, HelpEntry] = {
    # ── Retired ──────────────────────────────────────────────────────────────
    "d_heat_stage_2": HelpEntry(
        summary="Old gap below the heating line at which the gas heater (heat2) joined.",
        effect=(
            "The controller no longer uses it. The gas heater (heat2, the backup) now latches on when the house "
            "drops below the low edge of the temperature band and stays on until it is back to the heating target."
        ),
        refs=(f'{_REG}::"d_heat_stage_2"', f"{_LOGIC}::heat2 = LOW side"),
    ),
    "d_cool_stage_2": HelpEntry(
        summary="Old margin above the cooling line at which fan 2 joined.",
        effect=(
            "The controller no longer uses it for staging. Fan 2 now starts at the band high edge plus "
            "Cool Stage 2 Over High and stops at that point minus Cool Stage 2 Exit Hyst."
        ),
        refs=(f'{_REG}::"d_cool_stage_2"', f"{_LOGIC}::fan2_entry = temp_high + sp.cool_stage2_over_high_f"),
    ),
    "mister_on_s": HelpEntry(
        summary="Old fixed ON time of the mister duty cycle.",
        effect="The controller no longer uses it. Misting now runs in pulses set by Mister Pulse ON and Mister Pulse Gap.",
        refs=(f"{_REG}::Current pulse controller does not consume it",),
    ),
    "mister_off_s": HelpEntry(
        summary="Old fixed OFF time of the mister duty cycle.",
        effect="The controller no longer uses it. Misting now runs in pulses set by Mister Pulse ON and Mister Pulse Gap.",
        refs=(f"{_REG}::Current pulse controller does not consume it",),
    ),
    "mister_all_on_s": HelpEntry(
        summary="Old fixed ON time when every mister zone ran together.",
        effect=(
            "The controller no longer uses it. All-zone misting uses the same pulses as single-zone misting "
            "(Mister Pulse ON and Mister Pulse Gap)."
        ),
        refs=(f"{_REG}::Deprecated legacy all-zone duty-cycle ON duration",),
    ),
    "mister_all_off_s": HelpEntry(
        summary="Old fixed OFF time when every mister zone ran together.",
        effect=(
            "The controller no longer uses it. All-zone misting uses the same pulses as single-zone misting "
            "(Mister Pulse ON and Mister Pulse Gap)."
        ),
        refs=(f"{_REG}::Deprecated legacy all-zone duty-cycle OFF duration",),
    ),
    "sw_arbiter_zone_enabled": HelpEntry(
        summary="Old switch that turned zone ranking for misting on or off.",
        effect=(
            "The controller no longer reads it. Whenever more than one mister zone may run, it always picks the "
            "zone by the Zone Priority settings and how far each zone's VPD is above its target."
        ),
        refs=("firmware/lib/irrigation_program.h::whatever sw_arbiter_zone_enabled says",),
    ),
    "fog_time_window_start": HelpEntry(
        label="Fog Window Start",
        summary="Start hour of the old daily window in which fog was allowed.",
        effect=(
            "The controller has no such setting any more; fog is not limited to a clock window. "
            "Fog RH Ceiling and Fog Min Temp now decide when fog may run."
        ),
        refs=(f"{_REG}::start hour of the old fog eligibility window",),
    ),
    "fog_time_window_end": HelpEntry(
        label="Fog Window End",
        summary="End hour of the old daily window in which fog was allowed.",
        effect=(
            "The controller has no such setting any more; fog is not limited to a clock window. "
            "Fog RH Ceiling and Fog Min Temp now decide when fog may run."
        ),
        refs=(f"{_REG}::end hour of the old fog eligibility window",),
    ),
    "mist_vent_close_lead_s": HelpEntry(
        summary="Reserved lead time for closing the vent before misting.",
        effect=(
            "The controller reports this value but its control loop never reads it. Whether misting closes the "
            "vent is set by Mister Closes Vent and limited by Mist Max Closed Vent."
        ),
        refs=(f"{_REG}::Exposed/read back but not consumed by the control loop",),
    ),
    "mist_vent_reopen_delay_s": HelpEntry(
        summary="Reserved delay before reopening the vent after misting.",
        effect=(
            "The controller reports this value but its control loop never reads it. Whether misting closes the "
            "vent is set by Mister Closes Vent and limited by Mist Max Closed Vent."
        ),
        refs=(f"{_REG}::Exposed/read back but not consumed by the control loop",),
    ),
    "direct_wet_stress_latest_hour": HelpEntry(
        label="Direct Wet Stress Latest Hour",
        summary="Latest hour of the day at which the dry-stress override could allow direct wetting.",
        effect=(
            "The controller never had this setting, so it could not change what the greenhouse does. "
            "The row is kept for the history of past plans."
        ),
        refs=(f"{_REG}::ZERO firmware presence",),
    ),
    "summer_vent_min_runtime_s": HelpEntry(
        summary="Reserved minimum time to keep venting once the summer vent opens.",
        effect=(
            "The controller checks the value is in range but the summer vent never reads it. "
            "Venting ends when the outdoor air stops being cooler and drier (Summer Vent Enabled)."
        ),
        refs=(f"{_TYPES}::min VENTILATE dwell after gate fires (s; reserved)",),
    ),
    "bias_heat": HelpEntry(
        summary="Old offset that moved the heating line up or down.",
        effect=(
            "The controller no longer uses it. The electric heater (heat1, the primary) now holds the house at "
            "the heating target, a quarter of the way up the temperature band; the band itself sets that line."
        ),
        refs=(f"{_REG}::Heating uses the crop-band lower quartile target", f"{_LOGIC}::BAND_HEAT_TARGET_FRACTION"),
    ),
    "bias_cool": HelpEntry(
        summary="Old offset that moved the cooling line up or down.",
        effect=(
            "The controller no longer uses it. Venting and fan 1 now start when the house goes above the high "
            "edge of the temperature band."
        ),
        refs=(f"{_REG}::Cooling enters at the crop-band high edge",),
    ),
    "sw_fsm_controller_enabled": HelpEntry(
        summary="Old switch between the legacy controller and the current band-first controller.",
        effect=(
            "The controller always forces this ON and runs the band-first controller. Turning it off has no "
            "effect; going back to the old controller would need a controller software rollback."
        ),
        refs=(f"{_CONTROLS}::setpts.sw_fsm_controller_enabled = true;",),
    ),
    "feed_start_hour": HelpEntry(
        label="Feed Start Hour",
        summary="Start hour of the old fertigation window.",
        effect="The controller ignores it and it stays at zero. Watering and feeding now run from the irrigation program.",
        refs=(f'{_REG}::"feed_start_hour"',),
    ),
    "feed_end_hour": HelpEntry(
        label="Feed End Hour",
        summary="End hour of the old fertigation window.",
        effect="The controller ignores it and it stays at zero. Watering and feeding now run from the irrigation program.",
        refs=(f'{_REG}::"feed_end_hour"',),
    ),
    "sw_dawn_rehydrate_enabled": HelpEntry(
        label="Dawn Rehydrate Enabled",
        summary="Old switch for a scheduled center watering at dawn.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "dawn_rehydrate_start_minute": HelpEntry(
        label="Dawn Rehydrate Start Minute",
        summary="Start minute of the old scheduled dawn watering.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "dawn_rehydrate_window_min": HelpEntry(
        label="Dawn Rehydrate Window",
        summary="Length of the old scheduled dawn watering window.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "dawn_rehydrate_on_s": HelpEntry(
        label="Dawn Rehydrate ON",
        summary="Pulse length of the old scheduled dawn watering.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "dawn_rehydrate_gap_s": HelpEntry(
        label="Dawn Rehydrate Gap",
        summary="Gap between pulses of the old scheduled dawn watering.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "sw_midday_drench_enabled": HelpEntry(
        label="Midday Drench Enabled",
        summary="Old switch for a scheduled center drench around midday.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "midday_drench_hour": HelpEntry(
        label="Midday Drench Hour",
        summary="Hour of the old scheduled midday drench.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "midday_drench_start_minute": HelpEntry(
        label="Midday Drench Start Minute",
        summary="Start minute of the old scheduled midday drench.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "midday_drench_window_min": HelpEntry(
        label="Midday Drench Window",
        summary="Length of the old scheduled midday drench window.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "midday_drench_on_s": HelpEntry(
        label="Midday Drench ON",
        summary="Pulse length of the old scheduled midday drench.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "midday_drench_gap_s": HelpEntry(
        label="Midday Drench Gap",
        summary="Gap between pulses of the old scheduled midday drench.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "dawn_boost_offset_min": HelpEntry(
        summary="Old offset from sunrise for the scheduled dawn watering.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    "midday_boost_offset_min": HelpEntry(
        summary="Old offset from solar noon for the scheduled midday drench.",
        effect=_IRRIGATION_RETIRED,
        refs=_IRRIGATION_REFS,
    ),
    # ── Readback context ─────────────────────────────────────────────────────
    "sw_onchip_band_enabled": HelpEntry(
        summary="Whether the controller computes the climate band itself from the crop band anchors.",
        effect=(
            "ON (normal): the controller draws the temperature and VPD band from the Crop band anchors and the sun, "
            "so it keeps running the full daily program even offline. OFF: it falls back to the legacy wide "
            "limits (Set Temp Low/High, Set VPD Low/High)."
        ),
        caution="Read-only here because it decides which side owns the band. An alert fires when it is OFF.",
        refs=(f"{_CONTROLS}::if (id(sw_onchip_band_enabled)) {{", f"{_REG}::Decides which side owns the band"),
    ),
    "fallback_window_s": HelpEntry(
        label="Probe Watchdog Window",
        summary="How long every climate probe may be silent before the controller reboots itself.",
        effect=(
            "Checked once a minute: if no external probe has reported for longer than this, the controller "
            "restarts to recover the sensors. It ships at one hour and nothing sets it."
        ),
        refs=(f"{_CONTROLS}::> id(fallback_window_s)", f"{_CONTROLS}::esp_restart();"),
    ),
    "outdoor_temp_f": HelpEntry(
        label="Outdoor Temp",
        summary="Outdoor air temperature the controller uses, from the weather station.",
        effect=(
            "Used to judge venting: the summer vent opens only when outside is cooler, the cold-vent guard "
            "holds back venting when outside is far below the band low edge, and the moisture estimate mixes "
            "in outdoor air."
        ),
        refs=(f"{_LOGIC}::outdoor_cold_for_vent", f"{_LOGIC}::const bool outdoor_cooler"),
    ),
    "outdoor_dewpoint_f": HelpEntry(
        label="Outdoor Dew Point",
        summary="Outdoor dew point the controller works out from outdoor temperature and humidity.",
        effect=(
            "Used to tell whether outside air is drier than inside. The summer vent opens only when the "
            "outdoor dew point is lower than inside by at least Vent Prefer DP Delta."
        ),
        refs=(f"{_LOGIC}::const bool outdoor_drier_dp", f"{_CONTROLS}::.outdoor_dewpoint_f = outdoor_dp,"),
    ),
    "sw_overnight_micropulse_enabled": HelpEntry(
        label="Overnight Micropulse Enabled",
        summary="Old master switch for short overnight mister pulses.",
        effect=_MICROPULSE_RETIRED,
        refs=_MICROPULSE_REFS,
    ),
    "sw_night_humidity_source_present": HelpEntry(
        label="Night Humidity Source Present",
        summary="Old flag that would switch off overnight micro-pulses once a night humidifier was installed.",
        effect=_MICROPULSE_RETIRED,
        refs=_MICROPULSE_REFS,
    ),
    "micropulse_vpd_ceiling": HelpEntry(
        label="Micropulse VPD Ceiling",
        summary="Old overnight VPD above which a micro-pulse could fire.",
        effect=_MICROPULSE_RETIRED,
        refs=_MICROPULSE_REFS,
    ),
    "micropulse_max_on_s": HelpEntry(
        label="Micropulse Max ON",
        summary="Old cap on the length of one overnight micro-pulse.",
        effect=_MICROPULSE_RETIRED,
        refs=_MICROPULSE_REFS,
    ),
    "micropulse_min_gap_s": HelpEntry(
        label="Micropulse Min Gap",
        summary="Old minimum gap between overnight micro-pulses.",
        effect=_MICROPULSE_RETIRED,
        refs=_MICROPULSE_REFS,
    ),
    "micropulse_min_dew_margin_f": HelpEntry(
        label="Micropulse Min Dew Margin",
        summary="Old minimum gap between air temperature and dew point for an overnight micro-pulse.",
        effect=_MICROPULSE_RETIRED,
        refs=_MICROPULSE_REFS,
    ),
}
