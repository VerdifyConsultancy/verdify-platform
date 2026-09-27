# August 2026 fixed-panel historical replay: bounded result

This is a read-only September 27 replay of the July 11–August 13 Denver-local
current-firmware archive for #779. The original 0.3079 kPa corridor-distance
and 42.1% nine-stream-runtime comparisons remain historical hypotheses. This
packet does not revise either effect or claim AI benefit.

## Reproduced population

The existing same-quarter-hour matcher used outdoor temperature, outdoor RH,
solar and wind, then its 0.35 control-SD per-axis caliper. It reproduced 384
stale bins, 2,496 candidate control bins, 93 retained pairs, 291 stale bins
without weather common support, 85 unique matched controls and maximum control
reuse of two. All selected weather values were raw measured; two wind values
were interpolated in the *candidate pool* by the original loader, so the
matching standardization itself was not wholly raw. The post-match standardized
mean differences were −0.101 temperature, −0.073 RH, −0.038 solar and −0.004
wind.

The independent six-field database export has 48,630 climate rows. Deduplicated
15-minute bins contain 47,951 complete north/east/west temperature-and-VPD
minutes out of 48,960 possible. At the declared 12/15 minimum, 3,263 of
3,264 bins are jointly eligible. **All 93 weather-matched pairs remain jointly
eligible**, with no additional panel exclusion; selected stale bins have at
least 15 complete minutes and selected control bins at least 14. Every panel
mean uses the same complete six-field minutes and the same three zones. South
never enters the new panel. The old changing house aggregate included south
VPD in 64/93 selected controls and 0/93 stale bins.

As a descriptive sensitivity only, fixed-panel raw VPD levels differ between
stale and matched control bins by +0.3581, +0.6108, +0.1165 and +0.1208 kPa
on August 6–9 respectively; the between-four-day descriptive SD is 0.2351 kPa.
These are **raw levels**, not distance from a verified crop target, a paired
randomized endpoint variance, an AI effect, or resource savings. Four sequential
days and 93 selected bins do not yield an independent-bin causal standard error.

## Source limits that prevent a corrected crop-band comparison

- The source-owned `fixed_panel_target_revisions` and
  `fixed_panel_contributor_revisions` tables each had **zero rows** in a
  September 27 read-only query. `crop_target_profile_revisions` had 288 rows
  beginning at 2026-09-27 13:37:49 UTC: its migration-time baseline cannot
  authenticate a July/August target. Migration 252 forbids backdated target or
  contributor declarations. The bounded export cannot recover a historical
  physical serial or target contract from source route names.
- All 93 selected stale and control bins have an executed house VPD target and
  a band calculated by the *current* target resolver. An executed setpoint is
  not a crop target, and today's resolver at an old timestamp is not a frozen
  historical definition. The executed house VPD target means were 0.9540 kPa
  stale and 0.9616 kPa control; this small observed difference does not resolve
  target version or crop-band confounding.
- All selected bins have a plan interval ID labeled `local`; an interval join
  does not prove delivery. The stale interval overlaps the documented
  dispatcher and band-delivery interruption. Firmware and inherited device
  state also overlap it. The replay cannot separate these causes.
- `climate.ts` is a database flush timestamp. A flush may reuse cached probe
  values for up to 600 seconds, so six populated fields do not prove six fresh
  per-probe observations. The six-field and original weather exports were each
  read-only, but came from separate transactions; hashes identify their exact
  bytes, not a shared MVCC snapshot.

Thus #779's matching and sensor-composition diagnostics are reproduced, while
its frozen historical crop-target outcome and causal interpretation remain
unavailable. A future prospective source revision under #371 can qualify new
windows; it cannot retroactively turn this archive into verified crop truth.

## Immutable input and output identities

Private raw files and detailed bin-level reports are retained outside Git at
`/Users/jason/Documents/Codex/verdify-fixed-panel-779-20260927/` (directory
mode 0700, raw/replay files mode 0600). Only these aggregate facts and hashes
are committed:

| Artifact | SHA-256 |
|---|---|
| Six-field July 11–August 14 UTC export | `65b66a5b0a80d5373042c5cd6c6133120a300bdb1e0e19c36b66559d9517d9e9` |
| Current-firmware `climate_15m.csv` export | `ddfb28f9e2c1ba9337cecbe8602cae6879c723207d52832a9faa715fa818971b` |
| Fixed-panel coverage report | `8272c9fa5fd6f0b1b98de68b84c64d59985997b8f37f30a3f957faada521fe09` |
| Matched historical support report | `5d2f6d675fb213a42428262b67c7e6ecfbc2367c50ca8dc7593ecbc933747d84` |

`historical_fixed_panel_replay.py` embeds the SHA-256 of both inputs and all
calculation modules in its output. Two separate invocations against the same
private bytes produced byte-identical reports. Reproduction from the preserved
inputs is one command:

```sh
uv run --project research/planner-efficacy python \
  research/planner-efficacy/historical_fixed_panel_replay.py \
  --climate /private/current-firmware-inputs/climate_15m.csv \
  --six-field /private/climate-six-field-export.json \
  --output /private/replayed-support.json
```

The six-field export query comes from `fixed_panel.py emit-sql` with UTC bounds
`2026-07-11T06:00:00Z` and `2026-08-14T06:00:00Z`; the weather export is from
`extract-current-firmware.sh`. Both are read-only. The report writer refuses
to overwrite an existing file.
