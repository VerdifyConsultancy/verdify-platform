Playbook digest (digest-v1, 2026-11-05)

Hard rules:
1. Safety first: never ask for wetting below the dew-margin floor; never fight the firmware safety modes.
2. Band first: the objective is time in the served band; cost only after that.
3. Keep the incumbent value unless one of the field's drivers changed. Plan-to-plan noise with no driver is a defect.
4. Never raise mist intent to fight heat. Misters add humidity, not cooling.
5. fog_escalate_vpd_excess_kpa below 0.15 is exceptional; 0.10 is the floor.
6. all_zone_vpd_excess_kpa must be >= moisture_engage_vpd_excess_kpa (validator enforces it).
7. Do not decouple moisture thresholds from the band during live VPD-high stress with a healthy dew margin.
8. Do not use high thresholds to save water while VPD is above band; only a safety rail or the water cap justifies suppression.
9. On humid nights with VPD below band, prefer night_vpd_bias_kpa over wetting changes; step it 0.03-0.08 kPa at a time and keep it at 0 when nights already sit in band.
10. Gas heat (heat2) is backup: plans should not make it run more often to buy VPD.
11. Waypoints must use the offered anchor timestamps, in order.
12. Give a reason of at most 25 words for every field you change.
13. If no driver changed since the incumbent plan, return the incumbent values with an empty `changed` list.

Heuristics:
1. Forecast drives the wet-side fields: hotter, drier, sunnier days get a larger daily_mist_budget_gal, a lower fog_escalate_vpd_excess_kpa and more solar_precool_gain_f.
2. economizer_temp_advantage_f and economizer_dewpoint_advantage_f: smaller (vent sooner) when outdoor air is cool and dry; larger when outdoor air is humid.
3. thermal_lead_time_min tracks the overnight minimum: colder nights want more lead.
4. forecast_temp_bias_f / forecast_vpd_bias_kpa follow the bias-by-lead table, not intuition.
5. Hot/dry ventilation with a healthy dew margin: moisture_engage near 0.05, all_zone 0.20-0.30, fog 0.15-0.20.
6. VPD-low, condensation or disease risk: raise moisture thresholds just enough, lengthen gaps; do not widen bands.
7. resource_sensitivity and relay_churn_penalty change only when water use or relay cycling in the scorecard calls for it.
8. Cold season: most wet-side knobs are idle; spend the reasoning on the night VPD card and heat.
9. A forecast deviation trigger changes only the waypoints the deviation affects.
10. Weekly trigger: review the 14-day scorecard and adjust slow-moving fields only.
