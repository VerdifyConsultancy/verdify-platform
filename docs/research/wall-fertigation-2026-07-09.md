# Wall fertigation research for the July 2026 recovery

## Decision supported

The automatic path is wall-drip-only. Once weekly is the operator-selected pilot cadence, not a universal agronomic optimum. Exact dose and concentration remain fail-closed commissioning data because the wall line serves lime/citrus and cannabis, whose published nutrient tolerances do not establish one shared full-strength recipe.

## Primary evidence

- [UF/IFAS Fertigation for Citrus Trees](https://edis.ifas.ufl.edu/publication/HS1306/pdf) supports small timed doses, backflow protection, compatibility testing, knowing system travel time, and clean-water flushing immediately after injection. Its example 30–45 minute injection and 30 minute flush are for much larger citrus microirrigation systems; Verdify must measure its own fill and distal flush volume instead of copying those times.
- [Virginia Tech fertilizer calculations for greenhouse crops](https://www.pubs.ext.vt.edu/content/pubs_ext_vt_edu/en/430/430-100/430-100.html) supports deriving delivered concentration from fertilizer analysis and injector calibration rather than relay duration alone.
- [UC ANR drip distribution-uniformity guidance](https://ucanr.edu/site/maintenance-microirrigation-systems/surface-drip-tubing-distribution-uniformity-measures) supports emitter catch testing and distribution-uniformity evidence before relying on an aggregate schedule.
- [Cannabis nutrient-concentration study](https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2025.1433985/full) reports that elevated nutrient concentration and root-zone phosphorus did not improve yield or cannabinoids in the tested medical-cannabis conditions, reinforcing that higher concentration is not a safe default.

## Software contract

Commissioning records source-water pH, EC, alkalinity and material ions; product guaranteed analysis; injector ratio and calibration; aggregate wall flow; distribution uniformity; line fill volume; distal flush endpoint; delivered pH/EC; and seasonal multiplier. The state machine accepts `prewet_l`, `fert_l`, and `postflush_l`, derives bounded durations from calibrated liters per minute, and executes clean prewet, injection, fertilizer-off, and immediate clean flush. Missing or stale commissioning fails closed. The obsolete 90-minute global hold is not part of this contract.

Center, south, and west fertilizer infrastructure remains represented but disabled until a future planted-zone decision explicitly commissions it. Center climate mist remains clean and can resume after the fertilizer master is confirmed off; it is not part of the fertilizer flush path.

## Remaining operator measurement

Jason still needs to choose the actual soluble product and collect the commissioning measurements. Until then, software can be shipped and dry-run validated, but automatic fertilizer actuation must remain disabled.
