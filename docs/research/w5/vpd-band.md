# W5 research: a VPD band the greenhouse can hold, and a simpler band editor

Track `vpd-band`. Issues: #1365 (VPD band research sprint) and #1334 (band math and editor). Written 2026-11-05 against `origin/main` cf21fa421. Read-only research: no device, DB, Argo or Git writes.

## Questions

1. How achievable is today's band, by hour of day, season and outdoor conditions?
2. What can each actuator physically do? Measured effect sizes for fog and misters, vent and fans, heat1 and heat2, and the economiser.
3. Which levers widen what the house can reach, and what do they cost in kWh? This includes outdoor-air dehumidification with a confirmed-drier gate.
4. What band should be the plant-agnostic default, and what in-band rates should Jason expect?
5. How does the band math work today, and what is a simpler editor model with a 24 h preview?

## Bottom line

- **Temperature is not the problem.** It is in band 97–100% of the time from September onward, and 98.6% over the last 30 days.
- **Night VPD is the problem, and it is a spring and fall problem, not a winter one.**
  - Against today's band, nights were in band 24% of the time over the last 30 days, 43% across fall and 32% across spring.
  - Last winter (Dec–Feb), nights were in band 67% of the time, and the house was more often *too dry* (28% above `vpd_high`) than too humid.
  - The cause is cold glazing: on cold nights it condenses water out of the air. The indoor dewpoint follows the outdoor dewpoint and the cover temperature.
- **Today's night floor of 0.74 kPa is drier than the plant guidance.** At 62–66 °F it means RH 61–66%. The Vanda guidance is RH ≥ 65%, ideally 70–80%, at 60–70 °F nights. Extension guidance puts the disease floor near 0.4–0.5 kPa.
  - Recommended plant-agnostic night band: **VPD 0.50 / 0.65 / 1.20 kPa** (low / target / high). That is RH of about 74% at the floor.
  - Keep the day VPD band (about 0.86 / 1.04 / 1.29) and the temperature band unchanged.
- **Heat alone is a weak, expensive way to raise night VPD.** As the house warms, wet surfaces re-evaporate: the indoor dewpoint rises 0.5–0.85 °F for every 1 °F of heat.
  - So reaching 0.74 kPa needs about +4–6 °F, not the +2.2 °F estimated in #1365.
  - That costs about 10–15 kWh per night, at a measured heat-loss coefficient of about 190 W/°F.
  - Heat1 has only about 0.4 kW of spare capacity on November nights, so the rest would come from gas.
- **Outdoor air is the effective lever, and it is available almost every humid night.**
  - Outdoor air was drier in absolute terms in 98–100% of below-band night hours from September to May.
  - Measured effect of a vent-and-fan event at night: −2.7 g/m³ of moisture and +0.18 kPa VPD within 10 minutes, at a cost of −1.5 °F.
  - The moisture comes back about 1.5 g/m³ within 30 minutes of closing. So this has to run as **short, repeated exchanges with heat1 holding the temperature** ("vent + reheat"), not as one long purge.
  - Estimated cost: about 3–5 kWh per night, roughly a third of heat-only for more effect.
- **Expected in-band rates with the recommended band:**

  | period | today's control | after vent + reheat ships |
  |---|---|---|
  | last 30 nights | 85% | about 97–99% |
  | fall nights | 90% | about 97–99% |
  | winter nights | 79% (the misses are *too dry*) | about 79% |
  | spring nights | 60% | about 95% (modelled) |

  The winter misses are a fog policy question. Temperature should stay at 95% or better.
- **Band math.** Firmware, SQL and Python agree within 0.014 °F / 0.001 kPa on the live device, so there is no parity bug.
  - The harmonic overshoots the anchors only when the sunrise and sunset anchors are not the midpoint of noon and midnight. Today that is 1.54 °F on the temperature edges.
  - A simple editor that sets **day value, night value and width**, and derives the shoulders as the midpoint, produces a pure sinusoid with **zero overshoot**. No firmware change is needed for the editor.
- **Achievable-envelope model.** Use a rolling 14-night regression of indoor dewpoint on indoor temperature, outdoor temperature and outdoor dewpoint. Backtested on 355 nights:
  - median-night VPD MAE of 0.10 kPa;
  - 89% agreement on "will the night hold 0.6 kPa".

  It is good enough to shade an "expected" ribbon in the 24 h preview and to grade against.

## Evidence from our data

### Window and method

- **Main window:** the full climate history, 2025-10-30 → 2026-11-05 (8,651 valid hours), which gives seasonality.
  - Every hour is graded against **today's band**, so the question is "how achievable is the band we have now". That band is `fn_band_setpoints(hour + 30 min)` with the current `crop_band_anchors` and the solar phase of that date.
  - Control policy, band and door-window state changed across the year: a static 66.5–75 °F / 0.8–1.5 kPa band last winter, migration 188 night anchors from July, and the door window open June to fall. So the day and temperature rows show what the house did under the policy of the time. The **night VPD** rows are mostly physics, because the controller barely acts on night humidity in any period (vent/fan night duty ≤ 1% outside summer).
- **Recent window:** the last 30 days (2026-10-06 → 11-05) at 5-minute resolution, graded against the band the device served (`band_house_*` audit rows). This is the #1365 analysis, which this report reuses.
- **Excluded:** CO2, PAR and the south probe (`vpd_south` is null; being replaced). Hours with fewer than 15 climate rows or out-of-range temperature/RH are dropped.
- **Night** means solar altitude ≤ 0. "Deep night" means altitude < −12°.
- **Formulas:** VPD = es(T) − e, with Magnus es. Dewpoints are inverted from e. Absolute humidity (AH) = 2167·e/T_K in g/m³.
- **Equipment duty:** % of each hour on, from `equipment_state` intervals (capped at 6 h).

### A. Today's band applied to a year of history (night = solar altitude ≤ 0)

| month | day VPD below/in/above % | night VPD below/in/above % | night median T °F / VPD / indoor dewpt / outdoor T / outdoor dewpt | night heat1 / heat2 / vent+fan duty % |
|---|---|---|---|---|
| 2025-11 | 4/53/43 | 12/78/10 | 62.6 / 0.96 / 44.2 / 44.7 / 27.6 | 63/25/0 |
| 2025-12 | 0/45/55 | 3/69/28 | 62.4 / 1.10 / 39.8 / 40.3 / 24.5 | 52/35/0 |
| 2026-01 | 2/56/42 | 5/53/42 | 64.0 / 1.14 / 41.7 / 32.0 / 18.0 | 64/52/0 |
| 2026-02 | 0/46/54 | 7/81/12 | 60.9 / 0.99 / 41.7 / 41.5 / 21.4 | 55/33/0 |
| 2026-03 | 12/22/66 | 26/62/13 | 60.9 / 1.00 / 41.2 / 52.3 / 27.4 | 38/16/4 |
| 2026-04 | 27/44/29 | **83**/17/0 | 62.1 / 0.52 / 52.3 / 47.1 / 27.7 | 57/22/12 |
| 2026-05 | 32/54/13 | **90**/10/0 | 63.7 / 0.49 / 56.1 / 53.9 / 41.5 | 64/14/20 |
| 2026-06 | 15/56/29 | 44/39/16 | 69.0 / 0.79 / 56.9 / 64.4 / 46.4 | 8/0/35 |
| 2026-07 | 11/63/26 | 25/58/17 | 72.9 / 1.00 / 60.4 / 71.9 / 51.9 | 2/0/57 |
| 2026-08 | 12/60/28 | 28/55/17 | 70.9 / 0.92 / 58.5 / 69.3 / 51.6 | 3/0/52 |
| 2026-09 | 9/67/24 | **68**/27/5 | 68.8 / 0.70 / 57.7 / 64.0 / 49.2 | 18/0/27 |
| 2026-10 | 15/82/3 | **76**/24/0 | 64.9 / 0.65 / 55.4 / 53.9 / 40.0 | 49/5/1 |
| 2026-11 (5 d) | 8/92/0 | **79**/21/0 | 63.3 / 0.59 / 54.1 / 46.2 / 35.9 | 73/7/0 |

- **Winter nights are dry.** The indoor dewpoint was 40–44 °F against 18–28 °F outside. Night VPD was about 1.0–1.14, and 28–42% of December–January night hours were *above* `vpd_high`.
- **Spring and fall nights are humid.** The indoor dewpoint is 52–58 °F against 28–49 °F outside.
- **Winter days were too dry (42–55% above band) because fog was not used then** (0% midday fog duty, November–January). In October 2026, fog ran in sealed mode and day VPD was in band 82% of the time.
- The day temperature rows from last winter are not comparable: that band was 66.5–75 °F, not today's 74–84 °F.

### B. Night hours below `vpd_low`: what it would take (constant-vapour model)

| month | night below % | ΔT needed °F, median / p75 / p90 | reachable by heat ≤ temp_target | ≤ temp_high | outdoor AH drier by ≥ 0.5 g/m³ | indoor − outdoor AH, g/m³ |
|---|---|---|---|---|---|---|
| 2026-03 | 26 | 3.4 / 4.5 / 5.5 | 56% | 93% | 100% | 6.3 |
| 2026-04 | 83 | 3.6 / 4.5 / 5.4 | 79% | 100% | 100% | 5.8 |
| 2026-05 | 90 | 3.7 / 4.6 / 5.2 | 42% | 100% | 98% | 4.9 |
| 2026-09 | 68 | 1.2 / 1.9 / 2.8 | 18% | 96% | 98% | 3.1 |
| 2026-10 | 76 | 1.9 / 2.8 / 3.6 | 46% | 99% | 100% | 4.7 |
| 2026-11 | 79 | 2.7 / 3.3 / 3.7 | 75% | 100% | 100% | 4.5 |

The constant-vapour ΔT is a **lower bound**; see E. Outdoor air is drier almost every time it is needed.

### C. Achievability by local hour and season

Each cell is the % of hours with VPD in today's band as observed / the % in band or reachable by heating to ≤ `temp_target` (constant-vapour model, so optimistic).

| hr | fall | winter | spring | summer |
|---|---|---|---|---|
| 00 | 41/52 | 69/69 | 31/65 | 58/58 |
| 02 | 40/47 | 57/57 | 23/43 | 48/48 |
| 04 | 28/44 | 48/48 | 24/47 | 45/49 |
| 06 | 31/65 | 55/55 | 21/79 | 35/46 |
| 08 | 77/94 | 55/55 | 54/91 | 70/78 |
| 10 | 85/86 | 62/62 | 62/71 | 86/86 |
| 12 | 66/67 | 27/27 | 46/49 | 62/62 |
| 13 | 47/48 | 27/27 | 26/30 | 34/34 |
| 14 | 49/51 | 37/38 | 25/28 | 26/26 |
| 16 | 83/89 | 79/82 | 34/53 | 61/61 |
| 18 | 73/95 | 72/96 | 41/73 | 68/70 |
| 20 | 59/85 | 84/92 | 37/92 | 64/67 |
| 22 | 43/71 | 81/83 | 37/97 | 52/54 |

- **The hardest hours are 00–06 in fall and spring** (21–41% in band) and **13–14 h in summer and winter**. Summer afternoons are too hot and dry, limited by cooling; winter afternoons are too dry with no fog.
- Over the last 30 days (#1365), the miss is entirely at night: 76% of night buckets were too humid, 0% too dry.

### D. Night achievability by outdoor conditions (whole year)

| outdoor dewpoint | night hours | VPD in band | below band | indoor dewpoint | AH surplus g/m³ |
|---|---|---|---|---|---|
| < 20 °F | 567 | 48% | 3% | 38.0 | 3.4 |
| 20–30 °F | 1,212 | 66% | 24% | 43.4 | 3.5 |
| 30–40 °F | 908 | 42% | 53% | 52.2 | 4.8 |
| 40–50 °F | 907 | 31% | **61%** | 56.4 | 3.8 |
| 50–60 °F | 589 | 46% | 41% | 58.9 | 1.8 |

| outdoor temp | VPD in band | median house T / VPD | heat1 / heat2 duty |
|---|---|---|---|
| < 20 °F | 16% (above band, too dry) | 62.3 / 1.29 | 20% / 74% |
| 32–40 °F | 66% | 62.3 / 1.00 | 56% / 38% |
| 40–50 °F | 53% | 62.6 / 0.84 | 72% / 22% |
| 50–60 °F | **34%** | 63.5 / 0.69 | 58% / 7% |

- The band is hardest on **mild, humid nights (outdoor 40–60 °F, dewpoint 30–50 °F)**. There the glazing is too warm to condense much and the heater runs little.
- Below about 32 °F outdoors, the risk flips to too dry.

### E. Indoor dewpoint follows indoor temperature (why heat-only underdelivers)

- **Model fits over deep-night hours with no vent, fan, fog or mist (n = 2,402):**
  - Outdoor dewpoint alone: R² 0.63.
  - Indoor T + outdoor T: R² 0.59.
  - **All three: R² 0.75, RMSE 4.5 °F.** Fitted: Td_in = −22.9 + 0.82·T_in + 0.11·T_out + 0.43·Td_out.
- **Hour-to-hour first differences**, which remove slow confounders: ΔTd_in = 0.53·ΔT_in over the whole year (n = 2,016) and **0.84·ΔT_in for September–November** (n = 349).
- **Binned, September 15 – November 5, outdoor dewpoint about 40 °F:**

  | house temp °F | 61 | 63 | 65 | 67 | 69 | 71 | 73 |
  |---|---|---|---|---|---|---|---|
  | VPD kPa | 0.44 | 0.55 | 0.62 | 0.65 | 0.69 | 0.77 | 0.85 |

  A warmer house at night barely gains VPD, because its dewpoint rises with it.
- **Heat2 events at night** (n = 306, drift-corrected, 10 min): +2.7 °F but only +0.04 kPa, with AH **+1.07 g/m³**. A constant-vapour model predicts +0.20 kPa.
- **What heat-only reaches, from the median night state** (63.6 °F, dewpoint 54.3 °F), with k = the dewpoint rise per °F of heat:

  | k | VPD at +2 °F | +4 °F | +6 °F | ΔT for 0.74 kPa |
  |---|---|---|---|---|
  | 0 (constant vapour, #1365) | 0.71 | 0.87 | 1.03 | 2.4 °F |
  | 0.5 | 0.66 | 0.76 | 0.87 | 3.7 °F |
  | 0.84 | 0.62 | 0.68 | 0.74 | 5.9 °F |

- **Physical reading:**
  - The house has a large wet-surface moisture reservoir (slab, benches, media, plants, condensate).
  - Its inner glazing surface sits near the dewpoint. Using the NGMA U-value, the inner surface is about 56 °F at 63 °F inside and 48 °F outside, against an indoor dewpoint of about 54 °F.
  - Warming the air warms the surfaces and re-evaporates water. That is also why cold winter nights are dry: the cover condenses the water out.

### F. Actuator effect sizes

On-transition events, 2026-03 → 2026-11, 1-minute climate. The effect is the 10-minute change minus the pre-event 10-minute drift. The median is shown, with p25..p75. Heat, vent and fan events lasting under 8 minutes are excluded.

| actuator | when | n | median on-time | ΔT °F | ΔVPD kPa | ΔAH g/m³ |
|---|---|---|---|---|---|---|
| vent + fans | night, outdoor 50–70 °F | 151 | 20 min | −1.26 (−1.76..−0.60) | +0.15 (+0.02..+0.36) | −1.9 |
| vent + fans | night, all | 228 | 28 min | −1.48 (−2.17..−0.80) | **+0.18** (+0.03..+0.37) | **−2.7** |
| vent + fans | day, all | 666 | 19 min | −1.26 | +0.32 | −3.5 |
| heat2 (gas) | night, outdoor < 50 °F | 248 | 11 min | **+2.72** (+2.10..+4.12) | +0.05 | +0.97 |
| heat2 (gas) | day, outdoor < 50 °F | 223 | 13 min | +3.92 | +0.15 | +0.95 |
| fog | day, outdoor 50–70 °F | 1,063 | 1.1 min | −0.48 | −0.16 (−0.44..+0.12) | +0.5 |
| fog | day, outdoor < 50 °F | 41 | 1.0 min | −1.74 | −0.37 | +1.6 |
| fog | day, outdoor ≥ 70 °F (vent often open) | 6,699 | 1.5 min | −0.27 | ±0.00 | −0.4 |
| misters (any zone) | day, outdoor 50–70 °F | 2,579 | 1.0 min | −0.37 | −0.13 | +0.6 |
| misters | day, outdoor ≥ 70 °F | 9,739 | 1.0 min | −0.46 | −0.09 | +0.2 |

- **Heat1 (electric).** The event method fails for heat1: it switches on just as heat2 stops, so the drift correction is confounded.
  - Use the steady-state numbers instead. The Shelly CT reads **1.44 kW when heat1 is on** (median of 2,954 samples with heat1 on vs 10 W off, last 14 days). So it is a full 1.5 kW heater, not 460 W.
  - With heat1 at 100% and heat2 off, deep night, the house holds a median **11.7 °F** above outdoors (p90 19.4 °F; n = 180).
- **Heat-loss coefficient:**
  - Regression of heater input on indoor-minus-outdoor temperature over 1,924 cold-season deep-night hours, vent shut: **about 200 W/°F calm, about 280 W/°F at 5 mph wind**.
  - Physics check (NGMA): 6 mm twin-wall U = 0.72 Btu/h·ft²·°F × about 800 ft² = 169 W/°F, plus 1 ACH of infiltration at about 19 W/°F, gives about 190 W/°F.
  - This report uses **190 W/°F**. October–November ratios read lower (about 80 W/°F), because the slab releases daytime solar heat.
- **Vent rebound.** After a night vent closes on a mild night (n = 202), AH rises **+0.8 g/m³ in 10 min, +1.5 in 30 min and +1.8 in 60 min**, and VPD loses 0.23 kPa within the hour. On cold nights (n = 34) there is no rebound: the cold glazing holds AH down.
  - The initial rebound rate implies a night moisture release of roughly 0.3–0.5 kg/h. Steady state with about 1 ACH infiltration and a 4.5 g/m³ surplus implies about 0.46 kg/h. Both are about 4–6 L per night.
- **Economiser (enthalpy gate).** It blocks venting when outdoor enthalpy is 1 kJ/kg or more above indoor (`controls.yaml:366-385`). On a cool, dry night outdoor enthalpy is lower than indoor, so it does not block night dehumidification. It is not a limiter for this problem.

### G. Moisture budget (what we can and cannot say)

- Water into the house over the last 30 days is about 9.5 gal/day: fog 9.1, center mister 0.2, wall run-now 0.2 (`v_irrigation_minutes_daily`). By the water meter it is about 16.5 gal/day in October and 9 in November, against 4 gal/day last November and December.
- **Afternoon fog does not explain night humidity.** Over 52 nights (Sep 14 – Nov 4), the correlation between fog minutes from 12:00–18:00 and that night's AH surplus is **r = −0.03**. Nights after little fog (< 5 min) had a *higher* surplus (3.9 g/m³) than nights after a lot (≥ 15 min, 3.3 g/m³).
- The surplus is about 3–6 g/m³ in every season (A, B), and last winter matched this fall. The house's own source-to-exchange balance is stable; outdoor dewpoint and cover temperature set the level.
- **Cannot isolate the dominant source.** No per-source metering exists. The suspects are:
  - the wet slab and floor;
  - benches and media;
  - the hydro reservoir surface (if it holds water);
  - drip runoff;
  - `leak_detected`, which went true 28 times in the last 30 days.

  Settling this needs a controlled experiment (#1365 research item 1).

### H. Band math verification (#1334 research item 1)

- **Device vs SQL.** Over the last 24 h, the device `band_house_*` audit rows (484 per series) vs `fn_band_setpoints(ts)`: max |Δ| 0.014 °F for temperature and 0.001 kPa for VPD.
- **Python vs SQL.** `ingestor/solar.py` `solar_phase` + `band_value_at_phase` against `fn_solar_phase`/`fn_band_setpoints` at 11 times today: phase 0.0024, temp_low 0.028 °F, vpd_low 0.0003 kPa. The small differences come from minute rounding.
- **Overshoot with the live anchors:**
  - temp_low curve runs 59.17–75.33 °F against anchors of 60.71–73.79 (±1.54 °F);
  - vpd_low curve runs 0.723–0.877 against 0.738–0.862.
- **Cause.** The curve is c0 + c1·cos θ + s1·sin θ + c2·cos 2θ. With sr = 62.5 and ss = 72, c1 = (sr − ss)/2 = −4.75 and c2 = 0, so the amplitude is √(c1² + s1²) = 8.08 against s1 = 6.54.
- **With symmetric shoulders** (sr = ss = (sm + mid)/2), c1 = c2 = 0 and the curve is exactly bounded: 60.71–73.79.
- The overshoot is the same every day, because the curve lives in solar-phase space. So a year of NOAA sun times only moves *when* it happens, not its size.

## Public research

Sources marked [unverified] could not be opened and are cited from a secondary summary.

- **Night VPD floor and disease.** Bottom, "Humidity and Vapor Pressure Deficit", *Orchids* (American Orchid Society), Oct 2025, https://www.aos.org/orchids/articles/humidity-and-vapor-pressure-deficit
  - Summarizes MSU extension guidance: above about 0.5 kPa for finishing crops so they transpire and resist foliar disease.
  - Disease pressure rises below about 0.4 kPa; Botrytis favours cool, humid nights.
  - Suggests about 0.5–1.2 kPa for thin-leaved orchids; thick-leaved types such as Vanda tolerate more.
  - No authoritative source found requires 0.74 kPa at night.
- **Ohio State AEX-800** (Prenger & Ling, "Greenhouse Condensation Control"): a disease-prevention floor around 0.2 kPa. [unverified]
- **Vanda culture.**
  - AOS Vanda culture sheet, https://www.aos.org/orchids/culture-sheets/vanda.aspx: nights 55–72 °F, high humidity because the roots are exposed, and constant air movement to prevent fungal and bacterial disease.
  - New York Botanical Garden Vanda guide, https://libguides.nybg.org/vanda: RH at least 65%, ideally 70–80%; nights 60–70 °F, about 10 °F below days; gentle constant airflow.
  - So the recommended 0.50 kPa floor (about 74% RH at 62–64 °F) sits inside this guidance. Today's 0.74 (61–64% RH) sits at or below it.
- **Dehumidification practice.** Bartok (UConn), "Reducing Humidity in the Greenhouse", UMass Extension, https://www.umass.edu/agriculture-food-environment/greenhouse-floriculture/fact-sheets/reducing-humidity-in-greenhouse
  - The standard method is heat-and-vent: exchange the air 2–3 times an hour after sunset and around sunrise, then reheat.
  - Colder outside air holds far less water (50 °F saturated holds about half of 70 °F at 95% RH).
  - Gives disease-safe RH limits by temperature (about 89% at 61 °F) and recommends horizontal airflow (HAF) fans.
  - UConn IPM, https://ipm.cahnr.uconn.edu/reduce-greenhouse-humidity/: RH inside a dense canopy runs 20–40% above the air; run HAF continuously except when the exhaust fans run.
- **Energy for humidity control.**
  - Maslak & Nimmermark 2017, *Agricultural and Food Science* 26(1):56–66, https://journal.fi/afs/article/view/58936: dehumidification took 23–29% of thermal energy in a modelled tomato greenhouse; an air-to-air heat exchanger saved 15–17%.
  - Amani & Bahrami 2021, *Applied Thermal Engineering* 183:116178: 20–30% of climate-control heat goes to ventilation for dehumidification.
  - Kempkes et al. 2017, *Acta Horticulturae* 1164:445–452, https://research.wur.nl/en/publications/heating-and-dehumidification-in-production-greenhouses-at-norther: no single best method; heat exchangers recover sensible but not latent heat.
  - Campen, Bot & de Zwart 2003, *Biosystems Engineering* 86:487–493 (abstract): only forced ventilation with a heat exchanger competes on cost with plain ventilation. Condensing and desiccant units lose mainly on investment.
  - WUR trials: a plug-in dehumidifier removed about 1.9 L/kWh, and a heat-pump air unit about 3 L/kWh. [unverified]
- **Using outdoor air only when it is drier.**
  - Bartok/UMass (above) and Argus Controls, "Dehumidification Strategies" (2008, vendor): the test must be absolute humidity, not RH, because cooler air at the same RH still dries the house once reheated.
  - No extension standard specifies the gate logic. The AH/dewpoint-margin gate below is our own design.
- **Heat loss and infiltration.** NGMA "Heat Loss" guideline, https://new.ngma.com/heat-loss/
  - 6 mm structured polycarbonate U = 0.72 Btu/h·ft²·°F.
  - Twin-wall houses design to about 1.0 air change per hour.
  - Infiltration loss = 0.018·ACH·V·ΔT.
- **Night evaporation scale.** WUR / Het Nieuwe Telen reports night evaporation of 15–25 g/m²·h in cucumber, and a target of about 10 g/m²·h. [unverified] Our estimate of about 0.46 kg/h over 34 m² is about 13 g/m²·h. That is commercial-crop scale for a house holding only hanging Vandas, which supports a wet-surface source rather than transpiration.

## Options considered

| # | option | kind | night VPD gain (last 30 nights, ≥ 0.50 / ≥ 0.60 / ≥ 0.74) | energy | capex | risk | OTA |
|---|---|---|---|---|---|---|---|
| 0 | today | — | 85 / 59 / 28% | — | — | — | — |
| 1 | **Re-anchor the night VPD band to 0.50 / 0.65 / 1.20** | anchors | in band 24% → **85%**; physical state unchanged | 0 | 0 | none known; keeps RH within plant guidance | no |
| 2 | **Vent + reheat with a confirmed-drier gate** (pulsed exchange, heat1 holds temperature, electric only, nightly kWh cap) | firmware | → 99 / 97 / 75% (modelled: surplus cut 1.5 g/m³ when outdoor is ≥ 2 g/m³ drier) | about 3–5 kWh/night (0.25–0.4 kW) | 0 | vent chatter, temperature dips, Tempest staleness | yes |
| 3 | Heat for VPD (relax ENV-2, heat1 headroom only) | firmware or tunable | → 91 / 71 / 50% (k = 0.6) | about 2.4 kWh/night per °F; 10–15 kWh for the full effect, mostly gas once heat1 saturates | 0 | weak; burns heat2 | partly |
| 4 | Narrow the dehumidify dead zone (`vpd_hysteresis` 0.21 → about 0.08) | tunable | none alone (no effective actuator fires); a multiplier on option 2 | 0 | 0 | vent chatter | no |
| 5 | Raise the night temperature anchors (+3 °F) | anchors | about +0.05–0.10 kPa (k = 0.5–0.85) | about 7 kWh/night | 0 | changes DIF; poor value | no |
| 6 | HAF circulation fans (2 × 16–20 in) | hardware | house VPD about 0; cuts probe spread (0.15 kPa at night) and canopy wetness | about 0.4–1 kWh/night | about $100–250 | low | no |
| 7 | Low-temperature-rated compressor dehumidifier (about 30–50 pint/day) | hardware | removing 0.15 kg/h cuts the surplus about a third (about +0.2 kPa), so it can hold 0.74 | about 0.5 kW, but in heating season nearly all of it returns as heat and displaces heat1 | about $300–500 plus a drain | coil frosting below about 65 °F unless rated for it | no |
| 8 | Find and cut the moisture source | housekeeping or experiment | unknown until measured | 0 | 0 | none | no |
| 9 | Thermal night screen or extra insulation | hardware | likely *negative* for humidity: a warmer cover condenses less | saves heat | high | raises dewpoint | no |

- **Option 3 is weaker than #1365 assumed.** The constant-vapour estimate (+2.2 °F, 3–5 kWh) does not hold, because the dewpoint follows the temperature (E).
- **Option 2 is also the standard extension method** (heat-and-vent).
- **Option 7 is the only lever that holds 0.74 without venting.** It deserves a cheap pilot if Jason wants the drier night band back for future crops.

## Recommended design

### 1. Band (plant-agnostic default)

| | sunrise | solar noon | sunset | solar midnight |
|---|---|---|---|---|
| VPD low | 0.68 | 0.86 | 0.68 | **0.50** |
| VPD target | 0.85 | 1.04 | 0.85 | **0.65** |
| VPD high | 1.25 | 1.29 | 1.25 | **1.20** |
| Temp low / target / high | unchanged (60.7/65.7/70.7 at midnight, 73.8/78.8/83.8 at noon); shoulders derived as midpoints when the editor saves | | | |

- The shoulders are midpoints, so the curve is a pure sinusoid with no overshoot (H).
- The night high of 1.20 accepts winter's dry nights without asking for night fog.
- **Expected in-band rates to publish next to the KPI.** They come from section A data re-graded against this band (night 0.50–1.20, day unchanged):

  | | night | all hours |
  |---|---|---|
  | last 30 days | 85% (today 24%) | 85% (today 51%) |
  | fall | 90% | 81% |
  | winter | 79% (21% too dry) | 66% |
  | spring | 60% | 50% |
  | summer | 78% | 67% |

  After option 2, expect night ≥ 95% in fall and spring. Winter's day and night "too dry" share depends on running fog in winter. Summer afternoons stay limited by cooling: 26–37% at 13–14 h.

### 2. Night dehumidification by vent + reheat (firmware, option 2)

This replaces the #410 hold gate, which cannot arm at night (#1365 §How the controller reacts, item 3).

- **Gate (all must hold):**
  - night phase;
  - VPD < `vpd_low` (entry at `vpd_low − 0.05`, exit at `vpd_target`);
  - Tempest data fresh (`outdoor_data_age_s` < `outdoor_staleness_max_s`, 600 s today);
  - **outdoor AH ≤ indoor AH − `dehum_out_ah_margin_gm3` (default 1.0 g/m³)**;
  - temperature ≥ `temp_low + 1 °F`;
  - heat2 not latched;
  - the nightly reheat budget (`dehum_reheat_kwh_cap`, default 5 kWh) not exhausted.
- **Action:**
  - Lead fan plus vent for `dehum_pulse_s` (default 60 s) every `dehum_pulse_period_s` (default 900 s). The rebound in F says about 15 minutes.
  - Heat1 runs as needed to hold the temperature at or above the heat line during and after the pulse.
  - Heat2 is never used for humidity (invariant). The pulse aborts if the temperature reaches `temp_low`.
- **Telemetry:** the pulse count, an estimate of reheat kWh (heat1 on-seconds × 1.44 kW, attributed to the pulse window), and the gate reason. These feed the dashboard's "why out of band" line.
- **ENV-2** (no night heat for humidity) **stays** for pure heat-chasing, since E shows heat-only is weak. Heat1 runs here only to hold the temperature against the vent.
- Uses the existing `estimate_moisture_exchange` structure, adding a fifth candidate, `MX_VENT_REHEAT`. Replay before the OTA with invariants:
  - DIF ≥ 10 °F;
  - heat2 never for humidity;
  - at most 4 vent pulses per hour;
  - temperature never below `temp_low`.

### 3. Achievable envelope (read model, consumed by #1334, #1358 and #1360)

- **Model:** Td_in = a + b·T_in + c·T_out + e·Td_out, refitted nightly on the trailing 14 nights of deep-night hours with no vent, fan, fog or mist. Store the coefficients and the fit window in a small table.
- **Expected VPD at hour h:** VPD_exp(h) = es(T_band_target(h)) − es(Td_in(h)), using the band temperature target and the forecast (Open-Meteo) or Tempest outdoor values.
  - Shade p25–p75 using the fit RMSE (about 4.5 °F of dewpoint).
  - Add a second ribbon for "with vent + reheat" once option 2 exists.
- **Backtest:** 355 nights, median-night MAE 0.10 kPa, bias +0.01, 89% class agreement at 0.6 kPa.
- **Heat cost shown in the editor:** 0.19 kW/°F × (T_target − T_out) per hour. Changing the night temperature target by 1 °F costs about 2.4 kWh per night.

### 4. Simple band editor (#1334)

- **Parameters** (Option A from #1334):
  - Temperature: day value (solar noon), night value (solar midnight), half-width (±°F; one value, with an optional day/night split).
  - VPD: day and night targets, plus the range below and above.
  - Guardrails: 40/100 °F (decided), VPD 0.3/3.0.
  - Night dry-out bias: planner-owned, shown dotted.
- **Shoulders.** Sunrise and sunset are derived as midpoints, which removes the overshoot by construction.
  - An "Advanced" toggle exposes the raw 4 × 6 anchors and shows the overshoot in the preview, with a warning when it exceeds 0.5 °F / 0.03 kPa.
  - No firmware interpolation change now. Revisit only if Advanced asymmetric shapes are used.
- **Compile and save.** A pure function in `verdify_schemas` compiles the parameters to the 24 anchors. One atomic write to `crop_band_anchors` and the desired rows, with one activity row. The existing dispatcher sync (`ingestor/tasks/band_anchors.py`) delivers it.
- **Preview.** `POST /v1/band/preview` returns the curve as the device runs it (night bias included), the achievable ribbon, the estimated night kWh, and feasibility warnings. Example warning: `vpd_low` at `temp_low` needs a dewpoint below the 14-night p10 indoor dewpoint.

## Implementation plan

Few, larger PRs:

1. **PR-1: band defaults and decision row** (#1365, #1334). No OTA.
   - Update `verdify_schemas/band_defaults.yaml` night VPD anchors and the derived shoulders; generate the migration; update `crop_band_anchors` house rows.
   - Add the decision row to `docs/design/dashboard/decisions.md`. Amend ADR-0003/0004 with the energy policy (outdoor-air dehumidification with reheat; heat2 never for humidity).
   - Grading already follows the served band once PR #1372 (#1358) lands.
2. **PR-2: achievable envelope plus band editor** (#1334; consumed by #1358 and #1360).
   - Covers section 3 and section 4 above.
   - Parts: model-fit table and the fit added to the existing `verdify-band-curve-refresh` CronJob; `verdify_schemas` band-profile compiler with unit tests; API preview/profile; dashboard Control → Band; MCP `band` read.
   - Also write the `docs/design/dashboard/band.md` explainer from section H.
3. **PR-3: firmware vent + reheat** (new firmware sub-issue under #1365).
   - Covers section 2 above: new tunables in the registry, `cfg_*` readbacks (rule 6), estimator candidate, invariants, `make firmware-replay` on the October–November corpus, then OTA.
   - The planner lever set (#1366, #1350) gains `dehum_reheat_kwh_cap`, next to `night_vpd_bias_kpa`.
4. **Experiment E-1 (no code): moisture source.** One week, with Jason's go-ahead:
   - fog cutoff at 14:00 on alternate days;
   - check `leak_detected` and the hydro reservoir;
   - floor wet/dry log.

   Compare nightly AH surplus (query in Appendix Q7).
5. **Optional hardware pilots after PR-3 data:** HAF fans, and a low-temperature-rated dehumidifier on a smart plug measured by the Shelly.

## Decisions Jason must make

1. **Night VPD floor.** Recommended: **0.50 kPa** (target 0.65, high 1.20). Alternatives: 0.55–0.60 after option 2 ships, or keep 0.74 and accept about 75% night compliance at 3–5 kWh/night even with option 2.
2. **Energy policy for night VPD.** Recommended: **vent + reheat, electric heat1 only, capped at 5 kWh per night; heat2 never for humidity; ENV-2 kept for pure heat-chasing.**
3. **Winter dry nights** (VPD above 1.2 at night in December–January). Recommended: accept for now and revisit with December data. Alternative: allow night fog above `vpd_high` with the existing dew-margin guard.
4. **Editor shape.** Recommended: **Option A** (day/night values and width, derived shoulders, Advanced raw anchors), with no firmware interpolation change.
5. **Moisture-source experiment E-1.** Recommended: yes, one week.
6. **Hardware.** Recommended: HAF fans now (cheap, supported by extension guidance). Decide on a dehumidifier pilot after PR-3 data.
7. **Planner A/B scoring** (#1360). Recommended: score against both the served band and the achievable envelope, and give both arms the same lever set.

## Open risks

- The dewpoint-follows-temperature coefficient (0.5–0.85) comes from observational data. Heating and the evening moisture decline are correlated, so the true causal k is uncertain. Option 2's design does not depend on it; option 3's cost does.
- The vent + reheat kWh estimate (3–5 kWh/night) is a model, not a measurement. Surfaces rebuffer quickly (F), so pulses may need to be longer or more frequent. The kWh cap and telemetry bound this, and the first week of PR-3 data replaces the estimate.
- Tempest freshness is a single point of failure for the confirmed-drier gate. With no fresh data, the gate stays shut and the house behaves as today.
- Last winter's data was under a different band, fog policy and possibly different plant load. The winter "too dry" finding should be rechecked in December 2026 with the door window closed.
- The energy table has data problems: `ch1_power_w` is negative (CT reversed), and `watts_total` averages −824 W in October and −1,214 W in November. Any kWh dashboard built on `watts_total` is wrong. Use `abs(ch1)` for heat1.
- Hourly duty from `equipment_state` treats gaps as off. Hours with sparse state rows slightly understate duty.

## Appendix: queries

Every query was run read-only through `kubectl exec` psql with `default_transaction_read_only=on`, on 2026-11-05 between about 20:30 and 22:00 UTC. Physics and aggregation were done in plain Python on the hourly outputs, using the Magnus formulas listed under Method.

**Q1. Hourly climate with today's band, full history** (8,668 rows):
```sql
WITH g AS (SELECT time_bucket('1 hour', ts) b, count(*) n, avg(temp_avg) t, avg(rh_avg) rh, avg(vpd_avg) vpd,
    avg(vpd_north) vn, avg(vpd_east) ve, avg(vpd_west) vw, avg(outdoor_temp_f) ot, avg(outdoor_rh_pct) orh,
    avg(solar_altitude_deg) alt, avg(wind_speed_mph) ws
  FROM climate WHERE greenhouse_id='vallery' AND ts >= '2025-10-30' AND temp_avg BETWEEN 20 AND 130 AND rh_avg BETWEEN 1 AND 100 GROUP BY 1)
SELECT g.*, f.* FROM g CROSS JOIN LATERAL fn_band_setpoints(g.b + interval '30 min') f ORDER BY g.b;
```

**Q2. Hourly equipment duty** (7,398 rows):
```sql
WITH s AS (SELECT equipment, ts, state, lead(ts) OVER (PARTITION BY equipment ORDER BY ts) nts FROM equipment_state
  WHERE equipment IN ('heat1','heat2','vent','fan1','fan2','fog','mister_center','mister_south','mister_west') AND ts >= '2025-10-29'),
iv AS (SELECT equipment, ts a, LEAST(COALESCE(nts, now()), ts + interval '6 hours') z FROM s WHERE state),
h AS (SELECT equipment, gs b, GREATEST(0, extract(epoch FROM LEAST(z, gs+interval '1 hour') - GREATEST(a, gs))) sec
      FROM iv CROSS JOIN LATERAL generate_series(date_trunc('hour',a), date_trunc('hour',z), interval '1 hour') gs)
SELECT b, sum(sec) FILTER (WHERE equipment='heat1')/36 h1, ... FROM h GROUP BY b;
```

**Q3. Heat1 power from the Shelly CT, last 14 days:**
```sql
WITH e AS (SELECT ts, ch1_power_w c1 FROM energy WHERE ts > now()-interval '14 days' AND ch1_power_w IS NOT NULL)
SELECT h1, h2, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY c1) FROM
 (SELECT e.*, (SELECT state FROM equipment_state s WHERE s.equipment='heat1' AND s.ts<=e.ts ORDER BY s.ts DESC LIMIT 1) h1,
             (SELECT state FROM equipment_state s WHERE s.equipment='heat2' AND s.ts<=e.ts ORDER BY s.ts DESC LIMIT 1) h2 FROM e) x GROUP BY 1,2;
```
→ heat1 off: +10 W (n = 528); heat1 on: −1,401 to −1,449 W (n = 3,245).

**Q4. Actuator on-events with drift windows** (45,377 events):
```sql
WITH s AS (SELECT <eq>, ts, state, lag(state) OVER w ps, lead(ts) OVER w nts FROM equipment_state WHERE ... WINDOW w AS (PARTITION BY equipment ORDER BY ts)),
ev AS MATERIALIZED (SELECT eq, time_bucket('1 min', ts) b, extract(epoch FROM nts-ts)/60 dur FROM s WHERE state AND ps IS FALSE),
c AS MATERIALIZED (SELECT time_bucket('1 min', ts) b, avg(temp_avg) t, avg(vpd_avg) v, avg(outdoor_temp_f) ot, avg(outdoor_rh_pct) orh, avg(solar_altitude_deg) alt
     FROM climate WHERE greenhouse_id='vallery' AND ts >= '2025-10-29' GROUP BY 1)
SELECT ev.*, c0.*, cm.t, c5.t, c10.t, ... FROM ev JOIN c c0 ON c0.b=ev.b-interval '1 min' JOIN c cm ON cm.b=ev.b-interval '11 min'
  JOIN c c5 ON c5.b=ev.b+interval '5 min' JOIN c c10 ON c10.b=ev.b+interval '10 min';
```
Effect = (x₊₁₀ − x₀) − (x₀ − x₋₁₀). The equipment history with clean off→on transitions starts in 2026-03.

**Q5. Night vent-close rebound** (same structure as Q4, on vent on→off transitions with altitude < −6° and an off period of at least 60 min, sampled at +10, +30 and +60 min): mild n = 202, cold n = 34.

**Q6. Water into the house, last 30 days:**
```sql
SELECT zone, cause, sum(gal)/30.0 FROM v_irrigation_minutes_daily WHERE local_day > (now()-interval '30 days')::date GROUP BY 1,2;
```
→ fog/vpd 9.1 gal/day; center/vpd 0.2; wall/run_now 0.2.

**Q7. Night surplus vs afternoon fog:** from Q1 and Q2. For each night, take the 22:00–05:00 median of indoor − outdoor AH, against fog minutes from 12:00–18:00 that day (52 nights, r = −0.03).

**Q8. Band parity, device vs SQL, last 24 h:**
```sql
SELECT parameter, count(*), max(abs(value - <matching fn_band_setpoints column>)) FROM setpoint_snapshot a
  CROSS JOIN LATERAL fn_band_setpoints(a.ts) f WHERE ts > now()-interval '24 hours' AND parameter LIKE 'band_house_%' GROUP BY 1;
```
→ 484 rows per series; max 0.014 °F and 0.001 kPa.

**Q9. Historical served band** (`setpoint_changes` monthly means of temp_low, temp_high, vpd_low and vpd_high). Last winter's band was 62–66.5 / 75 °F and 0.80–0.95 / 1.50 kPa; it moved to the migration-188 anchors from June 2026.
