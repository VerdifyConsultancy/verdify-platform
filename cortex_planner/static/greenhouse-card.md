Greenhouse card (card-v1, 2026-11-05)

Structure: hobby greenhouse at a high-altitude Mountain-time site (gas furnace derated ~20%). Four zones: north, south (hottest, exhaust fans), east, west, plus center hanging rails. One north intake vent, 24x24 in, undersized. Thermal time constant about 3 h.

Heating:
- heat1, 1,500 W electric heater: PRIMARY heat (Jason, 2026-11-05). Firmware stage HEAT_S1 runs it first.
- heat2, Lennox 75,000 BTU/h natural-gas furnace (~48-54k actual at altitude): BACKUP heat for deep cold, stage HEAT_S2 below the band floor. Overshoot 1-2 °F. Never short-cycle it.

Cooling and air: two 2,450 CFM exhaust fans (lead/lag), the intake vent with an economiser that opens only when outdoor air is usefully cooler or drier (enthalpy and dew-point gates). Outdoor air may dry the house when it is confirmed drier: compare outdoor and indoor dew point, not RH (decisions 2026-11-05).

Wetting: zone misters (south 6 heads strongest, west, center, wall), one AquaFog XE 2000 (~1.6 kW, 15.8 GPH, forces the vent closed). Misters add humidity, not cooling. Water 1 GPM per zone, one zone at a time.

Dew-margin rule: never wet the air when the dew margin (air temp minus dew point) is under the dew_margin_floor_f; condensation feeds disease on the orchids.

Season now: cold. Heat runs most of the night; fog and mist are mostly idle. The main miss is night VPD below band (air too humid at night). The lever is night_vpd_bias_kpa (0-0.25 kPa added to the overnight VPD band, peaking at solar midnight), which keeps the dryer path working on humid nights without moving the crop curve. The door screen-window is closed for winter.

Crops: hanging Vanda orchids in the center; hydro rails empty. Vandas want bright light, moving air, a dry-down at night but not desiccation.
