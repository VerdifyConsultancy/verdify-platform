# September 4 fixed-column counterfactual sensitivity

This packet is a database-flush-snapshot sensitivity for #371, using the
existing `fixed_panel.py` calculator. It does **not** establish historical crop
targets, sensor serial identities, probe freshness, controller consumption,
continuous exposure, or a physical crop outcome. The result cannot replace the
locked experiment endpoint or close #371.

## Input and reference

- Window: September 4, 2026 in America/Denver, exactly
  `2026-09-04T06:00:00Z` through `2026-09-05T06:00:00Z`.
- The private, read-only production export contains 1,369 `climate` rows and
  only `ts`, `greenhouse_id`, and the six north/east/west temperature and VPD
  columns. Its SHA-256 is recorded in `summary.json`; raw rows and the full
  per-bin report are not in Git.
- `panel-source.json` binds database columns and source routes. The three
  `contributor_id` values in `contract.json` identify **database columns**, not
  historically authenticated physical probes. Their validity interval asserts
  only the identity of the selected columns over this exported day. The current
  source route names match the Git source at the end of the day, but Git does not
  prove the running source, a probe serial, calibration or every callback.
- `target-source.json` binds the canonical house band anchor and solar algorithm
  source hashes. The 96 target records in `contract.json` evaluate that curve at
  each 15-minute bin start, rounded to six decimals. This is a **fixed
  counterfactual crop reference** numerically derived from a source-defined
  house control band. It is not a recovered historical crop definition, desired
  write, confirmed readback, or firmware-consumed band. The declared 12/15
  complete-minute threshold is a sensitivity choice, not trial admission.

Both source sets are byte-identical between Git commit
`963ea818aad09b02259509cffa6bfdafb48d1702` (last main commit by the end
of this day) and the analysis base
`b638e4d688cefbd93cf75617deb6eb5e9023db16`. Neither commit proves what
was running at the greenhouse. The source and route hashes are preserved in the
two evidence manifests; their hash strings are not independent authentication.

## Result

The deterministic analyzer found 1,369 complete six-field database minutes and
71 missing minutes out of 1,440. Every one of 96 bins had 14 or 15 complete
minutes, so all passed the **declared** 12/15 threshold; no bin-level unavailable
reason fired. This is database field completeness, not per-probe freshness:
`write_climate` can reuse cached sensor values for up to 600 seconds.

Against this counterfactual reference, the panel's bin-mean in-band fractions
are 68.75% for temperature, 31.25% for VPD, and 28.125% jointly. The bounded
distance and worst-zone summaries are in `summary.json`. A bin-mean comparison
cannot detect within-bin extremes or establish elapsed hot/dry exposure. The
28.125% joint figure uses different fields, time weighting and a hypothetical
reference; it is not a correction factor for the legacy 6.1% reading fraction
or the 85.8 controller-attributable grade. The
public summary preserves the exact export, contract, calculator, and private
report hashes and marks all physical/experiment/causal eligibility false.

## Reproduce without a production write

Generate and check the counterfactual inputs from pinned source:

```sh
python3 research/planner-efficacy/sep4_fixed_panel_counterfactual.py --check
```

The exact read-only SQL was emitted with:

```sh
python3 research/planner-efficacy/fixed_panel.py emit-sql \
  --start 2026-09-04T06:00:00Z --end 2026-09-05T06:00:00Z
```

The private export was obtained through the existing authorized DB Pod with a
repeatable-read, read-only transaction and 30-second statement/2-second lock
timeouts. To replay, provide that preserved private export to:

```sh
python3 research/planner-efficacy/fixed_panel.py analyze \
  --input /private/raw-export.json \
  --contract research/planner-efficacy/sep4-fixed-panel/contract.json \
  --output /private/new-report.json
```

The analyzer refuses output overwrite. Two independently created reports from
the same private bytes were byte-identical, SHA-256
`da277d5f8f9022fea3eeaad7d5bbc3e6908f27edb34d31d2c2796c13b231e85c`.
The raw export is retained outside the repository with mode `0600`; its SHA-256
is `82d3d85cead818a0d9d217fe07995e359062091f809058f61a7a501aebd3f4ce`.

## Remaining #371 boundary

Use an immutable, effective-time crop-target and authenticated contributor
record for future production measurement. Historical September 4 target and
hardware identity remain unverified, so this packet stays a counterfactual
database snapshot. Source-bound current/historical API, MCP, planner and public
comparisons must keep controller credit and actual crop measurement separate.
