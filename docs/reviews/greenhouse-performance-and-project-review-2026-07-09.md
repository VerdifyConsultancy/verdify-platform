# Verdify greenhouse performance and project review — 2026-07-09

**Quantitative evidence cutoff:** 2026-07-09 13:10 MDT (19:10 UTC)

**Final read-only live recheck:** 2026-07-09 13:47 MDT (19:47 UTC)

**Operator corrections incorporated:** 2026-07-09 — center drip is intentionally disabled and unconnected; center mist is a VPD-cycle actuator; fertilizer is wall-drip-only; the interior light sensor is broken; physical additions are outside the current software plan.

**Primary analysis window:** 2026-06-09 12:45 MDT through 2026-07-09 12:30 MDT

**Historical context:** up to 2025-08-05 for climate and 2024-11 for relay state

**Method:** read-only production telemetry, repository and Git history, live Kubernetes/ArgoCD state, GitHub state, equipment manuals, orchid research, and counterfactual engineering calculations

**Safety:** no database writes, setpoint changes, firmware OTA, ArgoCD sync, device access, or public-infrastructure changes were performed

## Executive verdict

Verdify is keeping the greenhouse alive and its deterministic on-device controller is the strongest part of the system. Temperature control is broadly suitable for the strap-leaf Vandas. The public services, database, telemetry stream, and core Kubernetes workloads are available. That is the good news.

The greenhouse is **not yet operating as a trustworthy closed-loop optimization system**, but absent center-drip runtime is not a crop-care failure. verify the exact target and prerequisites that the center drip is intentionally disabled and physically unconnected. The center clean mister is a separate climate actuator used by the VPD cycle, and fertilizer is intended to run only through the wall drips.

The two highest-confidence software defects are:

1. **The valid wall-fertilizer path is disabled by stale timing while dead/disallowed paths remain armed.** Both wall and center schedules are 10:30, after the 06:00–09:00 feed window. The wall schedule queues wall drip plus south/west fertilizer-mister jobs, and the center schedule queues center fertilizer; all are dropped. No drip, fertilizer, or fertilizer-master relay ran in 30 days. Historical evidence shows wall fertilizer and master/flow did work through May 30, immediately before the feed-window guard made the stale schedule ineligible.
2. **Interior DLI is unknown, not merely miscalculated.** The interior light sensor is broken. The firmware also credits a one-second loop as five seconds, and downstream consumers apply additional double counting, but correcting that arithmetic would still not create a defensible interior crop DLI without a valid interior sensor. Qualified-light-minute actuation can remain independently usable; DLI dashboards, scoring, and AI context must report unavailable rather than infer an interior value from broken or proxy evidence.

The recent held-temperature overnight dehumidification change is directionally reasonable but **has not solved wet nights**. Only three of five complete hold-enabled nights reached a median 0.78 kPa VPD, and the latest night fell to about 0.65 kPa. Weather-adjusted analysis found no measurable post-change effect (`-0.002 kPa`, `p=0.94`, 20 nights); the sample is small and changes were confounded, so this is evidence of uncertainty rather than proof of no physical effect. On July 9, sustained vent-and-electric-reheat commands coincided with declining absolute humidity and greenhouse temperature, consistent with ventilation heat loss exceeding available electric reheat.

The AI planner is currently **operationally degraded, not an autonomous optimization layer**, but the leading failure is infrastructure rather than model reasoning. In 142 of 144 mapped timed-out deliveries since June 17, GPT-5.5 completed its session but could not use the disconnected Verdify MCP tool; all 43 mapped acknowledgements succeeded when MCP was connected. Hermes can remain TCP-healthy after its MCP keepalive dies, exhaust five retries, and never reconnect. A separate materializer bug preserves `band_track_fraction=0.25` using broad firmware bounds before rejecting it against the planner’s fixed-zero registry. Firmware safety still bounds the result, but the planner is not presently delivering a dependable control product.

The best next move is a software-reliability recovery sequence:

1. stop the five-minute mislabeled reconnect/reconcile and unchanged 69-value repushes while preserving the currently stable ESPHome transport;
2. restore planner trigger, materialization, expiry, and plan-journal reliability;
3. enforce a clear irrigation contract: center mist is clean VPD control, fertilizer is wall-drip-only, and the dead 10:30 center-drip schedule/path is disabled;
4. make interior DLI explicitly unavailable everywhere until Jason replaces the sensor, while preserving independent qualified-light-minute control;
5. continue bounded overnight dry-out experiments using the existing temperature, RH, outdoor-moisture, and actuator evidence;
6. address heap/replay reliability before spending the separately gated OTA and bake budget.

## Status at a glance

| Area | Status | Evidence-backed conclusion |
|---|---|---|
| Plant survival and temperature | Yellow-green | Deep night averaged 66.8°F and core day about 81°F, both reasonable for strap-leaf Vandas. Crop identity and direct crop-zone sensing remain weak. |
| Irrigation and fertilizer contract | Red | Center-drip inactivity is expected, but dead center/non-wall fertilizer states remain armed and the valid wall schedule is also at 10:30, so all fertilizer has been rejected since the May 30 feed-window guard. |
| Night root-drying climate | Yellow-red | The five-night hold bake passed only 3/5 nights; latest deep-night median VPD was about 0.65 kPa. No root-wetness evidence exists. |
| Light | Red for evidence, yellow for actuation | The interior sensor is broken, so interior DLI is unknown. Photoperiod/qualified-minutes control is a separate usable signal, but DLI-dependent reporting and AI conclusions are invalid. |
| Physical uniformity | Accepted current limitation | Zone VPD spread is large and no independent HAF exists, but physical additions are outside the current software plan. |
| Firmware safety loop | Yellow | Deterministic controller and safety gates are substantial, but the deployed build has a 3.789 kB lifetime heap floor and a Task WDT reset history. Replay is blind to one outdoor-data path. |
| Setpoint delivery | Red | The ESPHome transport is stable, but ordinary configuration drift is mislabeled as reconnect, clears the push cache every five minutes, misses 56 valid readbacks because of wrong wire IDs, and repushes 69 values. |
| Planner/AI | Red | GPT runs usually complete, but Hermes loses MCP permanently after five retries while health stays green; no full plan since June 25, materialization inherits an invalid stale value, forecasts are dry-biased, and the canonical VPD accuracy view compares incompatible locations. |
| Data/platform | Yellow-green | Fresh production climate/diagnostic data and healthy public endpoints; ArgoCD is Healthy but OutOfSync and several data-quality alerts are open. |
| Utilities/cost evidence | Yellow | Electricity and water are partially attributable, but there is no whole-building electric/gas accounting and 2.2–2.6 kgal of water is unexplained. |
| Delivery/project state | Yellow | Main is active and platform services are running. The sole open PR, #409, is 61 main commits behind and its July 3 framing is partly superseded. Canonical project-definition artifacts were missing at review start. |

## Persona verdicts

- **Orchid grower:** temperature is broadly reasonable. The review cannot infer a missing Vanda watering program from the intentionally disabled center drip, and it cannot claim interior DLI while the sensor is broken.
- **Plant pathologist:** humid nights plus root/crown wetting without independent air movement create avoidable disease risk. House VPD is not a root- or leaf-wetness measurement.
- **Controls engineer:** the deterministic local safety controller is the correct high-frequency authority. Held dehumidification is constrained by heater capacity and needs realized temperature/AH supervision, not a stronger open-loop assumption.
- **Mechanical engineer:** the restrictive intake, absent HAF, absent external shade, and lack of proportional ventilation are binding constraints. Software cannot ventilate away solar heat when outdoor air is as hot as the house.
- **Reliability engineer:** the 3.789 kB lifetime heap floor, Task WDT history, replay blind spot, stale plan, and repeated 69-value pushes block a clean firmware-reliability claim.
- **Climatologist/data scientist:** outdoor temperature, absolute humidity, solar load, wind, and open-window epoch dominate raw firmware comparisons. Several canonical-looking metrics—DLI, forecast VPD accuracy, and reboot counts—also have known semantic defects.
- **AI/ML validator:** the model and gateway usually work; Hermes-to-MCP liveness, action accounting, materialization, plan lifecycle, and forecast semantics do not. The 33.6% nominal resolved rate also counts one-shot fallbacks as plan success, so the planner should remain bounded or shadowed after transport repair.
- **Owner/operator:** the house is running and public surfaces are up. The immediate return is in software reliability—stable device delivery without redundant writes, a working planner, an explicit irrigation contract, honest DLI availability, and measured night dry-out—not new physical equipment in the current plan.

## Priority recommendations

### P0 — restore the software control plane

1. **Repair reconnect/reconcile semantics and readback identity.** Do not clear the setpoint cache for generic configuration drift; map the 56 anchor readbacks to their actual ESPHome wire IDs; compare values after registry clamping; and label true transport reconnects separately. The transport is currently continuous—the failure is redundant reconciliation, not network loss.
2. **Recover the AI planner transport and materializer.** Make Hermes MCP health real rather than TCP-only, retry indefinitely with bounded backoff or restart a dead client, surface tool-disconnected failures distinctly, clamp against the intersection of firmware and planner bounds, and distinguish full plans from one-shot fallbacks. Then repair singular active-plan expiry, required horizon, and forecast semantics; keep optimization bounded or shadowed until it meets an validated reliability threshold.
3. **Make irrigation intent explicit and fail closed.** Preserve center clean mist for VPD control; disable the purposeless 10:30 center-drip schedule and disallow fertilizer on center or mister paths; prove the wall-drip clean/fertilizer sequence, fertilizer-master interlock, and outcome ledger. Any behavior-changing firmware or live-device step remains separately gated.
4. **Make DLI unavailable rather than fabricated.** Add validity and provenance to firmware telemetry, database views, dashboards, alerts, and planner context. Suppress DLI-dependent recommendations and scoring until the interior sensor is replaced and validated.
5. **Keep night dehumidification as a measured experiment.** The current five-night result is 3/5, with the latest night failing. Use existing indoor/outdoor temperature and absolute-humidity response to bound ventilation/reheat and stop conditions.

### P1 — harden evidence, reliability, and wear

1. Add durable requested/admitted/started/completed/feedback/dropped accounting for connected irrigation paths; dead paths should be unrepresentable rather than silently rejected.
2. Make long device-write batches deadline-safe and non-starving. The sequential task loop and dispatcher lock currently let a 4.5-minute batch delay alerts, forecasts, planner heartbeat, and every other device write; cancellation must not leave rows or cache entries falsely marked pushed.
3. Repair forecast evaluation semantics and distinguish plan intent, clamped value, effective readback, and physical outcome.
4. Resolve the firmware heap floor, Task WDT history, and replay blind spot before a simplification OTA.
5. Correct the equipment registry and reduce avoidable fog, fan, and light cycling only after response tests prove the change is safe.
6. Improve water and energy attribution with existing telemetry where possible; do not make the software recovery depend on new meters.

### Deferred physical context — not part of the current software plan

The sensing, HAF, airflow, shade, intake, metering, and dehumidifier analyses below remain useful future context, but they are not current implementation prerequisites. Jason owns replacement of the broken interior light sensor; no additional physical sensors or equipment are proposed in the active software wave.

## Evidence and limitations

The analysis bundle is stored outside the product repository, consistent with the handoff rule for one-off evidence:

`/Users/jason/Orbit/context_dump/verdify-platform/greenhouse-analysis-2026-07-09/`

It contains the read-only collector, analysis script, query manifest, source CSVs, derived tables, firmware-to-Git map, model diagnostics, and three figures. The principal dataset includes:

- 42,848 climate samples and 2,880 15-minute buckets for the trailing 30 days;
- 345,643 climate rows back to 2025-08-05;
- 184,193 relay-state rows back to 2024-11;
- 218,533 diagnostic rows back to February 2026;
- 160,198 climate-action rows back to May 25;
- 558,445 setpoint-change rows and 540,837 energy rows back to August 2025;
- 13.85 million setpoint-readback snapshots back to April 2026;
- 77 observed firmware-version strings, of which 76 resolve to a Git commit prefix; 15 are `.dirty` builds and therefore identify only an approximate base commit.

Important limits:

- House-average VPD is not a crop-zone measurement. There is no center T/RH probe.
- There is no root moisture, leaf wetness/temperature, canopy PAR, canopy airspeed, or center runoff EC/pH evidence.
- Manual watering and feeding may have occurred but are not durably recorded.
- Relay runtime is reconstructed from state transitions. It identifies commands, not necessarily water delivery, airflow, combustion, or valve feedback.
- The Shelly channels are not a whole-building meter. Lighting and gas estimates contain assumptions.
- Firmware-era comparisons are observational and confounded by weather, open-window state, bands, and simultaneous tuning.
- The greenhouse entered an open-screen/window mechanical epoch around June 19. Summer coefficients should not be reused after fall closure without re-identification.
- Published Vanda guidance does not provide a validated cultivar-specific VPD curve. The VPD targets below are cautious engineering ranges, not claims of a cultivar-established standard.

## Orchid target card

The crop record says “mixed Vanda” and has a count of one, while operating notes and the request describe multiple strap-leaf Vandas. That distinction matters: strap-leaf types generally prefer somewhat less light and cooler conditions than terete types, while exact tolerance depends on parentage. Each plant should be inventoried separately by cultivar or best-known parentage.

| Variable | Provisional center-canopy target | Why and confidence |
|---|---:|---|
| Night temperature | 65–72°F; favor 66–70°F until parentage is known | Consistent with AOS strap-leaf guidance and current deep-night performance. Moderate confidence. |
| Day temperature | 78–90°F; investigate sustained leaf/canopy >95°F | Current core day mean is suitable, but north-zone air can be much hotter. Moderate confidence. |
| Mean day-night DIF | about +6 to +12°F | Use mean scheduled day/night values, not daily max-minus-min. Low-to-moderate confidence. |
| Night center VPD | 0.65–0.95 kPa; aim 0.75–0.85 | Derived from orchid humidity/disease guidance and local response; not a Vanda trial result. Low confidence. |
| Day center VPD | 0.8–1.4 kPa; aim 0.9–1.3 | Avoid sustained >1.5 until hydration is demonstrated. Low-to-moderate confidence. |
| Root watering | Thorough morning wetting until velamen is green; visibly silver/dry at the surface before evening | Stronger evidence than house VPD as a root-dry proxy. Moderate confidence. |
| Light | 12–13 hours; no current interior-intensity target can be validated | The interior light sensor is broken; qualified-light minutes and photoperiod remain operational signals but are not crop-intensity measurements. |
| Interior DLI | unavailable | Do not publish or optimize a crop DLI until Jason replaces and validates the interior sensor. Historical horticultural ranges are not measurements of this greenhouse. |
| Feed | complete orchid fertilizer around 1/4 strength initially, with periodic plain-water flushing | Verify input and runoff EC/pH and plant response. Moderate confidence. |
| Water quality | tepid, low-salt water; roughly <175 ppm TDS where practical | Aligns with AOS water guidance; record actual input/runoff. Moderate confidence. |
| Air movement | gentle leaf/root movement continuously, especially during humid nights | High confidence as a disease and drying control principle. |

The [American Orchid Society Vanda sheet](https://www.aos.org/orchid-care/care-sheets/vanda-culture-sheet) supports 55–72°F nights, 70–95°F days, daily watering in warm bright conditions, fast wet/dry cycles, continuous air movement, and low-strength regular feed. The AOS [humidity and air movement guidance](https://www.aos.org/orchid-care/orchid-care-basics/humidity-and-air-movement) specifically warns that high humidity requires airflow and that fans should not simply be shut off at night. A primary [Vanda photosynthesis study](https://www.sciencedirect.com/science/article/pii/S0176161720300778) and an epiphytic-orchid [velamen water study](https://pubmed.ncbi.nlm.nih.gov/23292456/) provide useful mechanisms, but each has limited cultivar/generalization scope.

## Thirty-day climate performance

### Whole-house and deep-night outcomes

| Metric | 30-day result |
|---|---:|
| Whole-window mean temperature | 75.3°F |
| Whole-window mean RH | 69.6% |
| Whole-window mean VPD | 0.953 kPa |
| Deep night, 02:00–06:00 mean temperature | 66.8°F |
| Deep night mean RH | 70.1% |
| Deep night mean VPD | 0.681 kPa |
| Mean measured cross-house VPD spread | 0.863 kPa |
| 95th-percentile cross-house VPD spread | 2.098 kPa |

Temperature is broadly credible for the crop. Humidity uniformity is not. A period-specific analysis is more revealing than the all-day spread:

- 02:00–06:00 cross-house VPD spread: mean 0.34 kPa, p95 0.65 kPa;
- 12:00–16:00 spread: mean 1.66 kPa, p95 2.68 kPa;
- daytime mean VPD by probe: north 2.24, east 1.31, south 0.97, west 0.63 kPa;
- deep-night mean VPD by probe: north 0.80, east 0.82, south 0.56, west 0.54 kPa.

The center Vanda value is currently an average/proxy. It could be materially different from every listed zone.

Plant-outcome evidence is also stale and mostly machine-derived. The latest image observation was July 4 UTC; all 16 trailing-30-day structured observations were sourced from Gemini vision. Across the full observations table, 1,186 of 1,188 records are Gemini-derived, with only two older non-Gemini records. Recent vision notes described green roots, healthy foliage, and blooms, but they are not a substitute for a current human-grounded root, crown, disease, and flower assessment.

### Held-temperature dehumidification bake

The deployed firmware is `2026.7.3.1931.ab18fe8`. Five complete hold-enabled nights were available:

| Local date | 02:00–06:00 median VPD | Meets 0.78 kPa? |
|---|---:|---|
| July 5 | 0.720 | No |
| July 6 | 0.857 | Yes |
| July 7 | 1.049 | Yes |
| July 8 | 0.856 | Yes |
| July 9 | 0.649 | No |

Across all five nights, 52.5% of samples were below 0.78 kPa and 36.3% had RH above 70%. The earlier two-night PASS report is therefore stale.

The most informative failure was July 9. During 02:00–06:00, median/mean VPD was roughly 0.65/0.68 kPa, RH was about 70.5–70.9%, vent/fan duty was roughly 62–65%, and electric-heat duty was roughly 16–19%. One 88-minute vent-and-reheat command episode coincided with a 1.8 g/m³ absolute-humidity decline and about 2.4°F of cooling, producing only a 0.06 kPa VPD increase. Even a shorter interval with heat commanded at 100% still cooled.

The leading mechanical explanation is that the 1.5 kW heater provides only about 5,100 BTU/h while plausible exhaust heat loss is several times larger at useful airflow and temperature difference. Actual exhaust airflow has not been measured, so this is a consistency check rather than an identified causal coefficient. The control also stops heating once the served temperature target is satisfied. At constant moisture on July 9, approximately 69.4°F—about 2°F warmer—would have produced about 0.83 kPa VPD. That is a possible bounded fallback, but it gives up some cool-night/DIF benefit and does not prove root dryness.

Weather-adjusted regression over the open-window epoch found a post-S8 coefficient of `-0.0024 kPa` (`SE 0.0328`, `p=0.94`, `n=20`). A more detailed model found `+0.0107 kPa` (`p=0.83`). Nearest-weather matching varied around zero. The honest conclusion is: **no isolatable effect yet; the experiment is underpowered and confounded**.

## Irrigation, climate mist, and fertilizer

### Center-drip inactivity is expected; the stale 10:30 program also disables wall fertilizer

Live readback showed:

- feed window: 06:00–09:00;
- wall irrigation scheduled: 10:30;
- center irrigation scheduled: 10:30;
- wall and center irrigation enabled;
- center fertilizer-day mask: `127`, meaning every day;
- wall clean/fertilizer and center clean/fertilizer masks: `127`, meaning every day.

At 10:30, firmware selects wall fertilizer plus south/west fertilizer-mister jobs and also selects center fertilizer, then rejects all four outside the feed window. Thirty-day telemetry recorded zero wall drip, center drip, fertilizer-mister, fertilizer-master, or other fertilizer runtime; the irrigation-accountability view returned no rows. The `vanda_orchid_active` nutrient recipe exists but is not active.

The wall path is not merely hypothetical. From May 19 through May 30, telemetry shows 12 wall-fertilizer starts of about 5.9 minutes each, matching fertilizer-master overlap. Eleven runs had positive meter movement totaling 78 gallons. The feed-window guard deployed May 30 after that day's 10:30 run; no wall fertilizer has run since. This strongly identifies stale schedule versus admission-window logic as the regression, while not proving current mixture or unchanged plumbing.

The original review interpreted this as a missing center watering event. Jason corrected that interpretation: the center drip is intentionally disabled and physically unconnected, so zero runtime is the expected outcome. The software defect is the stale and misleading control surface—the dead line still has a schedule, clean/fertilizer job states, and manual paths. Its out-of-window rejection currently fails closed, but it is not a durable expression of product intent.

The proposed software contract is:

1. center clean mister remains an automatic VPD-cycle actuator;
2. center drip and center-fertilizer schedule/manual paths are disabled or removed;
3. fertilizer may actuate only the connected wall drips, with the fertilizer master sequenced and interlocked there;
4. the validated wall schedule lies inside the feed window and queues only wall drip, not south/west fertilizer misters;
5. scheduled and manual wall-drip jobs receive durable terminal dispositions and physical feedback where existing hardware exposes it;
6. any obsolete job request is rejected with an explicit configuration reason rather than silently disappearing or refilling the queue throughout the scheduled minute.

The exact wall clean/fertilizer schedule and whether the separately named south/west fertilizer-mister relays are also permanently disallowed still require operator confirmation before implementation.

### Center climate mist is expected behavior

Observed climate-driven wetting over 30 days was approximately:

| Wetting asset | Runtime | Starts/day | Typical on episode |
|---|---:|---:|---:|
| Center clean mister | 38.7 h | 52.4 | 77 s |
| South clean/fert mister | 16.7 h | 24.9 | 72 s |
| West clean/fert mister | 4.0 h | 7.5 | 72 s |
| Fogger | 71.7 h | 54.8 | 2.0 min |

The center mister therefore averaged about 77 minutes/day of command time, spread across many short climate episodes, including late-evening and occasional post-midnight periods. This is expected VPD control, not evidence for or against a deliberate Vanda irrigation program. The controller and its reporting should keep climate mist, connected wall-drip irrigation, and wall-drip fertilizer as separate intents.

## Light and DLI

Interior DLI is currently **unavailable**. verify the exact target and prerequisites that the interior light sensor is broken and owns its eventual replacement. No software correction or outdoor proxy can establish what DLI the crop currently receives inside the greenhouse.

The primary firmware interval in [`firmware/greenhouse/controls.yaml`](../../firmware/greenhouse/controls.yaml) runs every one second, but the DLI accumulator still calls `lighting_dli_increment(..., 5.0f)`. That inflates every component of the firmware total approximately fivefold. The function already applies a 3.5× correction to indoor LDR lux, chooses the stronger of corrected indoor or Tempest-derived natural light, and adds separate main/grow supplemental-light credit. [`scripts/gather-plan-context.sh`](../../scripts/gather-plan-context.sh) then multiplies that combined total by 3.5 and adds a grow-light estimate again; `v_estimated_plant_dli` repeats the same conceptual double count. Recent raw values of roughly 70–148 mol/m²/day, and corrected-view values above 100, are not credible for this greenhouse.

These arithmetic errors do not currently drive the main lighting relay directly; the active controller relies on qualified light minutes and photoperiod. They compound the broken-sensor problem and currently corrupt:

- plant-light KPI reporting;
- planner input and any AI light recommendation;
- comparisons of supplemental-light value;
- the evidence needed to safely add external shade.

The immediate software repair is to publish availability and provenance, return `unknown` for interior/crop DLI, remove DLI from planner recommendations and outcome scoring, and annotate the affected historical interval as invalid. Qualified-light minutes, photoperiod, outdoor solar, and individual relay runtimes can remain available under their own names; none should be relabeled as interior DLI. After Jason replaces and validates the sensor, the accumulator should use real elapsed time, publish natural/main/grow/total components, remove downstream double counts, and pass cadence and source-combination tests before DLI-dependent features are re-enabled.

## Physical greenhouse and equipment model

### Envelope and solar load

Documented geometry is approximately 367 ft² of floor, 3,614 ft³ of volume, a 143-inch peak, and 785–810 ft² of 6 mm opal multiwall polycarbonate. Site elevation is about 5,090 ft. The repository reference uses 0.66 as an SHGC-style solar-gain factor, but the exact installed panel is not identified. [Gallina product properties](https://gallinausa.com/what-is-polycarbonate/product-properties/) provide generic manufacturer context, not proof of that factor for this glazing.

A useful first-order projected-roof solar load, using the current **unverified** 0.66 solar-gain proxy, is:

`367 ft² = 34.1 m²`

`Q_solar = 950 W/m² × 34.1 m² × 0.66 = 21.4 kW = 72,900 BTU/h`

That central assumption gives about 100,000 BTU/h at the observed 1,302 W/m² peak. A 0.50–0.75 sensitivity range gives roughly 55,000–83,000 BTU/h at 950 W/m² and 76,000–114,000 BTU/h at 1,302 W/m². The linked Gallina page distinguishes solar factor and shading coefficient but does not identify the installed panel or prove SHGC 0.66; the exact panel datasheet must replace this proxy. This remains a projected-roof engineering bound rather than a full view-factor/envelope model.

Under the central 0.66 proxy, external 25–35% shade would prevent roughly 18,000–25,500 BTU/h at 950 W/m². It should be exterior, staged, and commissioned against actual leaf-level PAR because glazing identity and current DLI evidence cannot protect against over-shading.

### Ventilation and intake

Two nominal 2,450 CFM 18-inch exhaust fans share one 24×24-inch intake, only 4 ft². At 4,900 CFM nominal free-air flow, intake face velocity would be 1,225 ft/min. A more practical 400–600 ft/min requires roughly 8.2–12.3 ft² of total intake area.

At altitude, a first-order sensible-cooling estimate is:

`Q_fan ≈ 1.08 × 0.83 × 4,900 × ΔT ≈ 4,390 × ΔT BTU/h`

That is about 35,000 BTU/h at an 8°F indoor-outdoor difference and 44,000 BTU/h at 10°F, before intake/static-pressure losses. When outdoor air is as warm as or warmer than indoor air, ventilation provides no sensible cooling. Shade and evaporation must carry the load.

Do not enlarge the intake from calculation alone. Measure actual fan airflow and static pressure first, then use staged or proportional openings to avoid one large cold-air slug in winter.

### Heat, fog, and water equipment

- Electric heater: about 1,500 W nominal and 1,430–1,440 W measured, roughly 5,100 BTU/h.
- Gas heater: Lennox LF24-75A-5, 75,000 BTU/h input and 60,000 BTU/h nominal output. At this elevation, treat field output as roughly mid/high-50 kBTU/h until combustion input is measured; do not apply the unit-efficiency loss twice. See the [Lennox LF24 manual](https://www.lennox.com/dA/f121c96b54/Lennox_LF24_IOM.pdf).
- Fogger: AquaFog XE direct-feed, 1/2 hp motor, continuous-duty design, official maximum around 16 GPH and 127,500 BTU/h evaporative capacity. Actual cooling is limited by air state and evaporation, not the nameplate maximum. See [AquaFog XE](https://jaybird-mfg.com/products/turbo-xe-series/turbo-xe-direct-feed/).
- Misters: approximately 25 center, 30 south, and 15 west nozzles, around 1 GPM per active zone, with a one-zone-at-a-time pressure constraint.
- Water heater: Rinnai RE140iN, 140,000 BTU/h maximum and UEF 0.81. See [Rinnai RE140iN](https://www.rinnai.us/professional/product-detail/re140in).
- Lighting: approximately 630 W main plus 816 W grow/shelf nominal.

At 1 GPM, the theoretical latent load of a mister zone is roughly `60 gal/h × 8.34 lb/gal × 1,060 BTU/lb = 530,000 BTU/h` if every drop evaporates. That is not an attainable cooling rating: much of the water wets roots, plants, floor, and structure, evaporation is state-limited, and only one zone has pressure at a time. It does show why runtime is not convertible to useful BTU without an evaporation fraction and moisture balance.

Observed median episode responses provide a more grounded—but still selection-confounded—comparison:

| Commanded action | Median episode | Air-temperature change | VPD change |
|---|---:|---:|---:|
| Fog | 2.0 min | -0.14°F | -0.052 kPa |
| Center mister | 1.4 min | -0.27°F | -0.097 kPa |
| South mister | 1.3 min | -0.45°F | -0.104 kPa |
| West mister | 1.3 min | -0.34°F | -0.132 kPa |
| Electric heat | 4.5 min | +0.14°F | approximately 0 kPa |
| Gas heat | 5.0 min | +1.62°F | +0.042 kPa |
| Vent | 4.5 min | -1.58°F | +0.163 kPa |

These are before/after associations during controller-selected conditions, not isolated equipment tests. Use calibrated flow plus absolute-humidity change to estimate actually evaporated water by zone.

### Thermal mass

Existing documentation contains incompatible simplifications: one analysis describes an approximately three-hour frequency lag, while another uses 7,300 BTU/°F of effective mass and an 11.5-hour time constant. Fresh passive-cooling regressions had `R² < 0.09` and an unphysical coefficient sign. Median daily solar-to-indoor-temperature peak lag was around two hours across 299 usable days, but that is not an envelope time constant.

The correct conclusion is not to choose one number. Current telemetry cannot identify a reliable single-state thermal constant because solar load, slab/bench mass, controls, ventilation, window state, and weather are confounded. Add slab/bench temperature and explicit actuator/input logging, then fit at least a two-state model by operating epoch.

## Relay map and equipment use

All PCF8574 relay outputs are active-low and boot off.

| Board/pin range | Connected loads |
|---|---|
| `0x20`, pins 0–7 | west clean mister; west fertilizer mister; south fertilizer mister; south clean mister; wall clean drip; center clean mister; center fertilizer drip; center clean drip |
| `0x21`, pins 0–7 | wall fertilizer drip; fertilizer master; gas heat; exhaust fan 1; exhaust fan 2; intake vent; fogger; electric heat |

Thirty-day reconstructed use:

| Asset | Runtime | Starts/day | Median on episode | Interpretation |
|---|---:|---:|---:|---|
| Exhaust fan 1 | 138.9 h | ~32 | ~4.2 min | High cycling; measure airflow and motor duty before tuning. |
| Exhaust fan 2 | 138.9 h | ~32 | ~4.2 min | Same as fan 1; lead alternation should be audited. |
| Intake vent | 191.2 h | ~29 | 4.5 min | Long total exposure; open-window epoch complicates response. |
| Fogger | 71.7 h | 54.8 | 2.0 min | Highest start count; distinguish motor starts from water-solenoid pulses. |
| Center mister | 38.7 h | 52.4 | 1.3 min | Climate wetting, not a deliberate logged drench. |
| South mister | 16.7 h | 24.9 | 1.2 min | Substantial water/cycle load. |
| West mister | 4.0 h | 7.5 | 1.2 min | Lower use than other zones. |
| Electric heat | 70.8 h | 7.6 | 4.5 min | Appropriate trim actuator but too small to offset sustained exhaust. |
| Gas heat | 1.0 h | 0.37 | 5.0 min | Coarse, high-capacity recovery; little summer use. |
| Main/grow lighting | 162/204 h | ~8/day each | ~8 min observed episode | The apparent episode shape suggests relay/telemetry chatter and merits audit. |

The asset registry is materially wrong for some electrical loads. Multivariate meter analysis suggests roughly 102–124 W per exhaust fan rather than 52 W, about 315–620 W for the fog circuit rather than 1,644 W depending on overlap/model, and about 1,436 W for electric heat. Registry and circuit-level measurement should be reconciled separately.

## Utilities and operating cost

The two-channel Shelly recorded 186.6 kWh and $20.72 at the configured rate during the 30-day window. It does not cover the whole building. Adding nominal unmetered lighting and using measured equipment coefficients yields a plausible total of roughly **426–509 kWh, or $47–57**, not a billing-grade answer.

Water accounting gives two scopes:

- climate-attributed mister/fog water: about 4,142–4,152 gallons, roughly $20 at the configured rate;
- positive totalizer growth: about 6,300–6,800 gallons, roughly $31–33.

That leaves approximately 2,200–2,600 gallons unexplained by climate-wetting attribution. Possible causes include manual use, unlogged delivery, meter resets/noise, or attribution defects. The absence of drip-relay runtime makes the gap more important, not less.

If all metered water is heated to 86°F, upstream Rinnai use could be roughly 14–27 therms, approximately $11–22 at assumed rates. Gas heat itself ran only about one hour in this summer window. Neither figure is currently captured in a trustworthy whole-facility cost ledger.

The utility program should therefore prioritize measurement rather than premature optimization: whole-building electric, lighting circuits, fogger, Rinnai gas, and per-manifold water/feedback.

## Firmware history and behavior

Firmware versions were correlated to Git commit prefixes from diagnostics. Dirty builds are marked as approximate lineage. The principal 30-day eras are:

| Runtime version / Git base | First–last UTC | Observed span | Principal behavior context |
|---|---|---:|---|
| `aa6518c` | June 8 19:10–June 11 01:36 | 54.43 h | Pre-v2 Vanda backlog closeout. The interval begins at the fixed 31-day cutoff. |
| `292ed09` | June 11 01:37–June 15 23:42 | 118.09 h | Offline-first solar-band controller. |
| `c247bf6` | June 15 23:43–June 16 00:32 | 0.82 h | Database-driven on-chip anchors. |
| `6f95e7e` | June 16 00:33–04:53 | 4.33 h | Harmonic band curve. |
| `564cf5c` | June 16 04:54–08:25 | 3.51 h | Night-VPD lever and zone arbiter. |
| `cc1bb19` | June 16 08:26–22:14 | 13.80 h | Curve-only fog/wet-gate removal. |
| `c5455ac` | June 16 22:15–June 17 07:34 | 9.32 h | Deploy-script-only change. |
| `6a3b35a` | June 17 07:35–17:19 | 9.73 h | Manual-button repair; automatic path neutral. |
| `2a48ec8` | June 17 17:20–June 18 02:42 | 9.38 h | Pinch support, compiled default zero. |
| `dcc6078` | June 18 02:43–June 20 16:27 | 61.72 h | Compiled pinch 0.30; runtime briefly 0.50, then 0.25. |
| `b7a531b` | June 20 16:28–June 23 07:46 | 63.31 h | Qualified-light and lighting changes. |
| `995c9b3` | June 23 07:47–July 4 01:32 | 257.75 h | Mister re-fire dwell fence. |
| `ab18fe8` | July 4 01:33–July 9 19:09 | 137.60 h | Orchestration build containing floating-corridor/S8 behavior. Relevant contained changes include `fb57246` for the moisture/deploy gate and `adbb772` for held-temperature vent-plus-reheat. |

There were at least twelve version transitions consistent with intentional flashes during the 30-day period, plus one real Task-WDT reset. The exact version field is unavailable before April 15. This deployment density is another reason not to infer causality from raw era averages.

Raw era averages should not be interpreted as causal rankings. Outdoor moisture and temperature, open-window state, served bands, planner state, and multiple simultaneous changes dominate these comparisons.

### Current device reliability

Latest diagnostics reported:

- firmware `2026.7.3.1931.ab18fe8`;
- current free heap around 74 kB;
- largest free block 68 kB;
- lifetime minimum free heap 3.789 kB;
- uptime about 99 hours;
- reset reason `Task WDT`.

Current free heap is reassuring only as a momentary state. It does not erase the lifetime minimum or watchdog reset. The current uptime spans no new OTA, so heap improvement cannot yet be attributed to the July ingestor change.

The controlled-restart/backstop work from PR #429 has merged to `main`, but it is not present in the currently running pre-merge `ab18fe8` firmware. It should not be counted as a live protection until a separately validated OTA and runtime proof occur.

Two validation gaps remain important:

- replay issue #419: `outdoor_data_age_s` is absent from the corpus, so outdoor-aware estimator paths are replay-blind;
- the current firmware improvement issue #410 is framed around too few nights, while issue #424’s original band-divergence framing was later partly identified as an evidence/view artifact.

### Setpoint “Tier 1” is not producing its intended outcome

The ingestor image with digest prefix `175e5ec` started July 9 at 18:11 UTC. A deeper log and code audit corrects the original interpretation: the production pod has zero restarts, one `Connecting`, one `Connected`, and no connection-lost, keepalive, or lease-loss event. The ESPHome transport is not reconnecting every five to six minutes.

Instead, 22 periodic jobs were mislabeled `reconnect reconcile`. Ordinary configuration changes set a shared force-push flag; in a recent two-hour sample, `outdoor_dewpoint_f` alone crossed its one-percent threshold 45 times. The five-minute dispatcher consumes that flag, clears `_last_pushed`, and performs a broad reconcile.

The reconcile then fails to match 56 anchor readbacks because the registry expects simple `cfg_<name>` identifiers while `aioesphomeapi` exposes the actual friendly-name-derived wire IDs. Home Assistant proves all 56 sensors exist. With those anchors treated as unconfirmed, the dispatcher emits 56 anchor writes, 8 served-band writes, and 4 lux writes—68 redundant band settings. The stale `band_track_fraction=0.25` adds the 69th because drift is compared before the value is clamped to the live fixed bound of zero.

Each paced 69-write batch occupies the single dispatcher for about 4 minutes 32 seconds; one current-pod batch reached the 300-second task timeout and was cancelled. Production recorded 15,443 `source=band` setpoint rows in the latest 24 hours, of which 12,600 were superseded. Current heap near 72–74 kB and a 68 kB largest block does not show an active low-heap throttle, but the device-write and dispatcher churn are unambiguous.

The database contains many superseded ledger rows, so counting confirmed changes alone understated the churn. Direct logs prove the push collapse has not occurred, but they do not show transport instability. Existing tests encode the wrong wire contract by asserting `cfg_<name>` or accepting the YAML C++ `id`; all 56 anchor IDs deterministically mismatch the actual friendly-name-derived API identifiers. Treat Tier 1 as deployed but failed: separate true reconnect events from generic drift, preserve the cache, correct the 56 wire IDs, and compare effective post-clamp values before judging heap effects or later optimization tiers.

A final 19:47 UTC read-only recheck found the same redundant behavior continuing: the 19:36 and 19:41 reconcile batches seeded 147 readbacks, found 68 band changes plus the stale plan value, and logged `direct-pushed 69/69`. This was a continuity check, not added to the fixed-cutoff models.

## Planner and AI assessment

### Current operating state

The AI planner architecture is conceptually sound: an LLM proposes bounded plans through MCP, the tunable registry validates values, and firmware retains deterministic safety. The live outcome is currently weak, with two identified software faults:

- **Hermes-to-MCP liveness:** since June 17, 142 of 144 mapped timed-out deliveries correspond to completed GPT-5.5 sessions whose tool result reports the Verdify MCP server disconnected. All 43 mapped acknowledged successes occurred while MCP was connected. The current Hermes process recovered after restart, then its MCP keepalive died at 18:11 UTC; TCP health stayed green, five retries were exhausted, and the client did not recover.
- **Materializer bound ordering:** `_clamp_tier1_value` prefers firmware bounds `[0,1]` over the planner registry’s fixed bound `[0,0]`. It therefore preserves the live/operator `band_track_fraction=0.25` and only rejects it during final validation instead of clamping to the legal intersection.

- only about one-third of recent required trigger paths have produced a successful plan/ack outcome; timeouts dominate;
- the latest full `plan_journal` entry is 2026-06-25 despite 22 entries in the trailing 30 days;
- a critical `planner_required_plan_missed` alert remains open;
- a `planner_plan_horizon_missing` warning remains open;
- since early July, full-plan materialization ingests `band_track_fraction=0.25`, clamps it against the wrong bound source, then fails final validation and leaves static/one-shot fallback behavior;
- the stale plan contributes one repeatedly clamped value to each 69-value push batch; it does not cause the transport connection or five-minute reconcile event;
- forecast-deviation alerts are open.

Thirty-day delivery outcomes were:

| Outcome | Count |
|---|---:|
| Timed out | 180 |
| Plan written | 40 |
| Acknowledged | 51 |
| Pending at cutoff | 3 |

That is 91 nominal plan/ack dispositions out of 271 completed deliveries, or 33.6%. July 7 and July 8 had no successful triggers. The July 3–6 `plan_written` required-cycle results were `iris-oneshot` `set_tunable` fallbacks rather than complete `set_plan` products, so the nominal percentage overstates full-plan health. The separately deployed `planner_graph` path had zero recorded runs, so it is not a proven replacement.

Plan identity is not singular or easy to audit: supersession is currently per parameter, leaving 8,015 active rows across 99 Iris plan IDs and 337 active rows across 201 preemptive plan IDs at the cutoff. Using a 30-day value-change collapse that carries the last pre-window state into the boundary, the local planner path produced 1,984 changes across 29 parameters and 37 triggers; Opus produced 400 across 23 parameters and two triggers. A window-local `LAG` definition gives slightly different counts, so the boundary rule must accompany any churn metric. Under either definition, the tactical surface is excessive for a planner whose delivery and outcome evidence are not reliable.

The learning signal is also optimistic rather than independent: twenty scored plans averaged a planner self-score of 5.50 versus a deterministic anchor score of 2.60, an average `+2.90` gap; 15 of 20 differed by more than two points, and every plan received the same three-point guardrail penalty. This cannot validate planner benefit.

Independent 14-day forecast evaluation, deduplicated by fetch hour and target horizon and joined to observed **outdoor** conditions, found systematic bias at longer horizons:

| Horizon | Temperature bias | RH bias | VPD bias |
|---|---:|---:|---:|
| 0–6 h | +0.11°F | -8.6 percentage points | +0.351 kPa |
| 6–24 h | +0.61°F | -11.4 percentage points | +0.472 kPa |
| 24–48 h | +1.34°F | -14.8 percentage points | +0.625 kPa |
| 48–72 h | +1.39°F | -17.9 percentage points | +0.742 kPa |

The forecast is therefore much drier than observed outdoor air. Existing `v_forecast_accuracy` VPD logic appears to compare that outdoor forecast field to indoor `vpd_avg`, so its score should not be used until semantics are corrected.

### Recommended AI role

The AI should not try to replace the one-second, elapsed-time-based deterministic controller. It should become a slower, auditable policy and experiment layer:

1. forecast weather and solar load with calibrated uncertainty;
2. choose bounded daily/night policy parameters and explicit experiments;
3. state a hypothesis, expected crop/environment result, limits, and stop condition;
4. let firmware execute and enforce safety locally;
5. score the outcome against available house-climate, action-ledger, utility, and operator-observation evidence without inventing missing crop-zone signals;
6. retain lessons only when data quality and counterfactual confidence are adequate.

Before that is useful, repair the basics in this order:

1. materializer/registry compatibility and active-plan cleanup;
2. trigger SLA, acknowledgement, retry, and plan-horizon reliability;
3. forecast-target semantics and horizon-specific calibration;
4. action accounting that distinguishes requested, clamped, pushed, confirmed, superseded, and physically completed actions;
5. outcome signals with explicit availability: existing house climate and actuator evidence now, plus operator crop observations when recorded; do not synthesize missing center climate, PAR, root dry-down, or EC/pH;
6. only then use Bayesian or constrained optimization over a small number of parameters per experiment.

AI recommendations based on interior DLI, center VPD, or root dryness should be explicitly suppressed because those evidence channels are invalid or absent. The planner can still reason from correctly named house-level climate, outdoor conditions, equipment state, and bounded setpoints.

## What-if analysis

These are decision-support bounds, not predictions.

### What if the night target is warmed 2°F for four hours?

Using the documented effective mass and plausible envelope UA range, the intervention demand is roughly 5.8–6.9 kWh, or $0.64–0.77 at the configured electric rate. The measured 1.436 kW electric heater can supply only about 5.74 kWh in four hours at 100% duty, so the upper bound—and even the lower bound with losses and cycling—is not reliably feasible as a four-hour electric-only step. A real trial would need a longer preheat/ramp or carefully bounded gas staging. At constant moisture, reaching 69.4°F would have placed July 9 house VPD near 0.83 kPa, but the intervention also reduces cool-night/DIF benefit. Treat it as a bounded house-level dry-out experiment using existing sensors, realized temperature/absolute-humidity response, energy, and explicit stop conditions—not as proof of root dryness.

### What if ventilation is made more aggressive at night?

When outdoor absolute humidity is favorable, more ventilation can remove water. At plausible flow, its heat loss can exceed what the electric heater replaces; July 9 commands coincided with a cooler house and only a small VPD gain. Do not increase vent aggression without an absolute-humidity advantage test, existing-sensor temperature-slope supervision, and explicit temperature and runtime bounds.

### What if independent HAF is added?

HAF cannot remove water from the building, but it can reduce boundary-layer humidity, spatial gradients, condensation, and root/leaf dry-time without throwing conditioned air outdoors. Given the large zone spread and sealed-mist behavior, it is the best low-energy mechanical addition. Verify gentle canopy airspeed rather than aiming a high-velocity fan directly at roots.

### What if the intake is expanded?

If static-pressure testing confirms restriction, moving from 4 ft² toward 8–12 ft² should increase actual fan delivery and reduce motor/system pressure. This can improve hot-day exchange but also increases cold-air shock potential. Stage the opening and re-identify all response coefficients after the change.

### What if 25–35% external shade is installed?

Under the central unverified glazing proxy, at 950 W/m² it could prevent about 18–26 kBTU/h, a large fraction of the ventilation sensible-cooling capacity at an 8–10°F temperature difference. It is likely the highest-leverage summer cooling measure, but the exact glazing factor and installed shade performance must be measured. It must be commissioned against calibrated PAR because strap-leaf Vandas still need high light and the current DLI signal is unusable.

### What if a dehumidifier is added?

A dehumidifier is the only listed option that decouples moisture removal from ventilation heat loss when outdoor air is not dry enough. It also adds sensible heat and electric load. Size it only after measuring water added by fog/mist, crop evapotranspiration, leakage/vent exchange, and desired overnight removal rate.

### What if firmware is simplified now?

Reducing on-device YAML/entity surface is likely directionally correct for heap reliability. An immediate OTA is not justified while a critical planner alert is open, Tier 1 still churns device writes, replay is blind to an outdoor path, dead irrigation states remain, and interior DLI lacks a valid sensor. Complete the non-OTA recovery work, then produce a narrowly scoped firmware change with heap map, replay, band replay where applicable, invariants, unit tests, and a controlled bake.

## Overall project health

### Live platform snapshot

At the quantitative evidence cutoff, with continuity rechecked at 19:47 UTC:

- `https://api.verdify.ai/health`, `https://lab.verdify.ai`, and `https://graphs.verdify.ai` returned HTTP 200 in 13–26 ms from the laptop;
- climate and diagnostic data were about 36 seconds fresh;
- core API, DB, Grafana, Hermes, ingestor, lab, MCP, MQTT, planner, setpoint-server, and Traefik pods were Running;
- two old July 8 lab-publisher jobs were Failed, while the three newest July 9 runs Succeeded;
- the latest DB backup and pre-storage-move backup jobs Succeeded;
- ArgoCD app `verdify-prod-dark` was **Healthy / OutOfSync** at Git revision `0a9a19a`, with drift on a Grafana ConfigMap, Namespace, ingestor PVC, HA-gap-backfill CronJob, and migration Job;
- ArgoCD also carried a shared-resource warning because the `verdify-prod` Namespace is referenced by `agent-sessions-local-staging`; ownership should be reconciled before anyone treats a broad sync as cleanup;
- the only environment remains production and its sync is correctly safety-checked.

Open alert inventory included one critical planner-required-plan-missed alert, one heap warning, one plan-horizon warning, five forecast-deviation warnings, three irrigation-feedback gaps, and eighteen sensor-offline warnings. Some sensor warnings may be stale/schema artifacts, but that itself is an observability-quality problem.

### Delivery and backlog state

`main` was at `0a9a19a` and actively changed on July 9. The sole open PR was [#409](https://github.com/VerdifyConsultancy/verdify-platform/pull/409), “Vanda care: deeper night DIF, vision watchdog, k3s vision-revival prep.” Its branch was 61 main commits behind and three commits ahead. Its checks were green at the July 3 base, but several claims and changes are now superseded or conflict with the newer dry-root/held-dehumidification direction. It should be reconciled or closed, not merged wholesale.

Relevant open tracking includes firmware heap [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428), simplification epic [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430), overnight Vanda drying [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410), replay coverage [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419), moisture-dependent VPD tuning [#383](https://github.com/VerdifyConsultancy/verdify-platform/issues/383), and irrigation epic [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350). Current issue framing must be corrected so the 10:30 schedule is treated as dead center-drip configuration, fertilizer is wall-drip-only, and interior DLI is unavailable rather than merely awaiting a numerical correction.

### Architecture and process assessment

What is working:

- strong device-write gating and explicit safety boundaries;
- deterministic local safety controller separated from AI intent;
- substantial firmware invariants, replay, readback, migration-safety, and CI gates;
- single production environment with digest-pinned promotion and manual live sync;
- rich historical telemetry and an improving action/evidence model;
- durable repo documentation and operational runbooks;
- recent storage/backup work completed without loss of current data freshness.

What is not working:

- accepted sensor limitations are not represented honestly in DLI-dependent software;
- dead center irrigation/fertilizer states remain representable and connected wall-drip outcomes are not proven end to end;
- major KPIs can be dimensionally wrong and then amplified downstream;
- planner success, horizon, forecast semantics, and learning journal are not reliable;
- setpoint intent, effective readback, and actual push behavior are easy to miscount;
- physical limits exist, but hardware additions are intentionally deferred from the current software plan;
- utility accounting cannot yet support rigorous cost optimization;
- old review/issue claims are not automatically invalidated when later evidence changes the conclusion;
- the repository lacked a concise project definition at review start, so the review proceeded from repository evidence and live read-only observations.

## Recommended execution sequence

### Wave A — read-only reconciliation and human decisions

This wave changes no production, database, device, firmware, setpoint, or hardware state.

1. Correct issue and design framing: center drip is intentionally absent, center mist is climate control, fertilizer is wall-drip-only, and interior DLI is unknown.
2. Confirm the remaining irrigation boundaries: whether all south/west fertilizer-mister outputs are also forbidden, the intended wall-drip schedule/manual behavior, and authorization to disable every center drip/fertilizer entry point.
3. Define planner trigger/horizon reliability and bounded night-dry-out acceptance before selecting implementation lanes.
4. Reconcile stale claims in #409, #410, #424, the early bake report, and Tier-1 rollout notes against the fixed-cutoff evidence bundle and the operator corrections.
5. Write implementation acceptance tests and neutral/rollback behavior for each bounded change.

### Wave B — gated software, data, and live-control recovery

Each item needs its own issue/change surface and the applicable CI, migration, promotion, production-sync, service-restart, device-write, or OTA gate. Do not treat the wave as one ungated deployment.

1. Repair Tier-1 connection ownership, cache/readback/no-op semantics, and reconnect handling. This changes the sole live device-writer path and needs ingestor deployment plus direct connection and push-count proof.
2. Repair Hermes-to-MCP liveness and health, poll terminal runs, require `set_plan` for required-plan success, clamp materialization to the intersection of all bounds, restore a real future horizon, and define singular active-plan expiry; separately and atomically neutralize the invalid stale plan after validation. Service changes require normal promotion/sync proof, while the live-intent cleanup requires its own gate.
3. Disable the dead 10:30 center schedule and center drip/fertilizer commands, preserve center clean VPD misting, enforce wall-drip-only fertilizer, and add outcome accounting for connected paths. Firmware changes require replay/invariants/tests and an OTA; device-affecting execution remains gated.
4. Add DLI availability/provenance, suppress invalid consumers, and annotate historical values. Arithmetic cleanup can be tested now, but interior DLI remains unavailable until sensor replacement and validation.
5. Repair forecast evaluation semantics—forecast outdoor VPD must be compared with observed outdoor VPD, not observed indoor VPD—plus planner observability through validated database/service changes and the applicable migration and production promotion/sync.

### Wave C — bounded existing-sensor climate experiments

1. Establish an overnight house dry-out objective and safety limits for minimum temperature, ventilation duration, and heat use.
2. Admit ventilation only when outdoor absolute humidity offers a drying advantage; supervise realized indoor absolute humidity and temperature slope.
3. Test one bounded reheat/ventilation change at a time and preserve the deterministic fallback.
4. Compare valid nights across weather-matched conditions; do not call house VPD root dryness.
5. Feed only stable, proven outcomes back into planner experiments.

### Deferred — physical additions and broader optimization

Sensor additions, HAF, intake, shade, airflow measurement, utility meters, and a dehumidifier are outside the current plan. Jason separately owns replacement of the broken interior light sensor. None of these is a prerequisite for fixing reconnects, planner reliability, irrigation semantics, honest DLI availability, or existing-sensor night control.

## Software recovery acceptance criteria

- the sole device writer remains continuously connected for an agreed observation period, without a five-to-six-minute cadence or unchanged bulk repush;
- planner triggers meet the validated SLA and produce one registry-valid expiring plan or a neutral fallback, with a durable journal result;
- center clean mist continues to operate through the VPD cycle while center drip/fertilizer schedule and manual paths are unrepresentable or explicitly disabled;
- fertilizer can reach only operator-validated wall-drip outputs and its master/zone sequence is covered by tests and observable terminal outcomes;
- interior DLI is `unknown` everywhere the sensor is invalid, with no DLI-dependent AI recommendation or score;
- bounded night-dry-out experiments record indoor/outdoor absolute humidity, realized temperature, actuator duty, stop reason, and weather-matched outcome;
- firmware has no open deploy-blocking alert and any OTA passes replay, band replay where applicable, invariants, unit tests, weekly-limit, and bake gates.

## Direct-execution safeguards and decisions still needed

1. Confirm whether wall drips should run automatically or operator-only. If automatic, specify days/cadence, start time inside 06:00–09:00, fertilizer duration, and clean-flush duration. “Wall-drip-only” is otherwise treated as forbidding every center, south-mister, and west-mister fertilizer output.
2. Confirm authorization to remove or hard-disable the 10:30 center schedule plus every center drip/fertilizer manual and planner entry point while preserving the center clean VPD mister.
3. Decide whether the existing 90-minute post-fertilizer absorption hold should block center mist/fog. With wall-only fertilizer, the recommendation is a wall-local hold so climate mist can continue unless the night-dry-out policy separately suppresses it.
4. For a later gated wall-path proof, confirm whether the fertilizer tank is safely mixed or whether the first device test must be clean-water-only.
5. Define the overnight dry-out objective and hard bounds: time window, preferred house VPD or RH range, minimum temperature, whether center mist is normally suppressed overnight, and whether bounded gas heat is allowed after electric reheat proves insufficient.
6. Confirm whether `band_track_fraction=0` is authoritative and the stale June 18 `0.25` operator experiment may be retired.
7. Define the planner service level and authority: required full-plan cadence/deadline, shadow observation period, and whether the first repaired phase remains proposal-only or may progress to a separately validated bounded canary.
8. Decide the disposition of PR #409 after extracting any still-useful vision work; reconcile/close is safer than merging the stale branch wholesale.
9. Approve any firmware OTA, prod ArgoCD sync, live-intent database change, or device-VLAN action separately under the existing gates.

## Core repository references

- [README](../../README.md)
- [k3s agent handoff](../handoff/k3s-agent-handoff.md)
- [greenhouse reference](../planner/greenhouse-reference.md)
- [firmware FSM specification](../firmware-fsm-spec.md)
- [floating-corridor ADR](../adr/0004-floating-corridor-control.md)
- [greenhouse physics review](greenhouse-physics-model-floating-control-2026-06-18.md)
- [mechanical response matrix](mechanical-response-matrix-overnight-dehum-2026-06-22.md)
- [prior five-night bake source](s8-vanda-night-dehum-bake-report.md)
- [firmware simplification proposal](firmware-simplification-proposal-2026-07-06.md)

This report is an analysis and prioritization artifact. It does not authorize a firmware OTA, production sync, device write, fertilizer activation, destructive database action, or hardware change.
