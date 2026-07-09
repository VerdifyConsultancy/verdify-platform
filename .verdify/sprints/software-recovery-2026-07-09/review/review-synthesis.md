# Human review synthesis

## Dominant themes

Jason wants software to reflect the real planted topology instead of legacy infrastructure assumptions, wants missing physical evidence represented honestly, and wants the planner and night-dry-out loop operational rather than left in proposal state. Exact agronomy should be researched and commissioned, not guessed.

## Desired outcomes

- Automatic wall fertilization, approximately weekly as a pilot, with clean flushing.
- Center-only VPD climate mist; south/west mist becomes intentional irrigation.
- Dormant infrastructure remains available for future plants but disabled now.
- Night dry-out follows the established diurnal/solar loop, not fixed clock time.
- June `band_track_fraction=0.25` experiment is disabled.
- Repaired bounded planner becomes active immediately.
- All software fixes are delivered, including one OTA when required.

## Validated concerns

- Current climate code rotates into south/west and live relay history proves it.
- Current wall feed queues south/west fertilizer; moving its schedule earlier first would be unsafe.
- Center irrigation is live-enabled despite no connected center drip.
- Existing schedule is accidentally inert at 10:30 after the feed window.
- The 90-minute global hold is inconsistent with immediate wall-line flushing and blocks unrelated center climate mist.
- Interior DLI is not measured and current numeric values are fabricated from invalid/proxy arithmetic.
- Planner delivery is tool-dead despite healthy pods, and the stale 0.25 row remains effective in the DB view.
- Replay contains no source-backed outdoor age/freshness in the stock corpus, firmware has a chronic low lifetime heap floor, cycle truth must come from raw transitions, and the water-event ledger stopped in May.
- Equipment catalogs disagree on active lighting slugs and current water/energy consumers collapse unlike evidence scopes.

## Unvalidated hypotheses

- A single shared lime/cannabis nutrient concentration can meet both crops. Primary evidence does not support that assumption; commissioning remains required.
- Existing projected dry-out gain is sufficient in every weather regime. Realized episode evidence is missing.
- Fixing the writer storm alone restores a safe heap floor. It should help, but must be measured before OTA acceptance.
- Old firmware-counter claims justify new mister/light/dehum anti-chatter behavior. Fresh raw-edge evidence does not; preserve existing rails and prove behavior before any new tunable.

## Explicit decisions already made

- Weekly automatic wall feed is an operator-approved pilot cadence, not a universal optimum.
- Fertilizer is wall-only. Center/south/west fertilizer infrastructure is preserved but disabled.
- Climate mist is center-only. South/west clean irrigation is explicit and disabled by default while unplanted.
- Interior DLI stays unavailable until sensor replacement/calibration; physical additions are outside this recovery.
- Night behavior uses solar phase.
- The June 0.25 band experiment is retired; effective value must remain zero.
- Planner activates after acceptance without proposal-only soak.
- Implementation, production delivery, and this recovery's OTA are approved; deterministic gates remain.

## Contradictions and ambiguities

- “Once a week” is a preference with uncertainty. It is retained as pilot scheduler policy while exact volume/concentration remains commissioning data.
- Automatic wall feed is desired, but physical actuation cannot safely start until water/product/injector/flow/uniformity/flush measurements exist. The software still ships and fails closed.
- Existing firmware is already solar-phase based for dry-out; the missing implementation is evidence parity/outcome supervision, not a new clockless controller.

## Likely current sprint candidates

#438 credential hygiene/gate; #433 writer; #293/#424/#389/#410/#419 evidence; #437 resources; #435 DLI; #427 planner; #434/#428 plus preservation slices #299/#383/#386 firmware; #390 cycling gate; ordered #377 stale-plan retirement.

## Follow-up backlog rather than current sprint

- #436 vision source/image-pull recovery.
- Physical interior light sensor replacement and validation.
- Actual fertilizer product/water/emitter commissioning and any crop-specific second feed path.
- Optional cfg-entity collapse/firmware simplification beyond what runtime evidence requires.
- Broad #367 anti-chatter refactor and #371 outcome-UI redesign until trustworthy evidence proves need.

## Question counts

- Blocking product decisions: 0
- Blocking architecture decisions: 0
- Scope and priority decisions: 0
- Risk/deployment decisions: 1 — protected production DB credential rotation discovered during hygiene
- Non-blocking clarification: 1 — measured wall fertigation commissioning values
