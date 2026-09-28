# #782 bounded pre-draw seasonal and retention sensitivity

## Calendar decision from this audit

The separate [warm-season calendar preregistration](../../research/planner-efficacy/protocols/warm-season-calendar-prereg-2027-v1.json)
selects **June 1–July 30, 2027 inclusive** as the first future 60-local-day,
UTC-offset-stable window supported by the audited complete climate-eligible analogue:
the same 2026 dates yielded 60/60 eligible days and 30/30 necessary
climate-only adjacent pairs. Each prospective assigned-day endpoint remains
the template's local [06:00,24:00) 18-hour window. This calendar selection
supersedes the older hot/dry calendar uncertainty in
`research/planner-efficacy/protocols/seasonal-decision-2026-09-27.md` without
changing that frozen winter-observer protocol, the current direct-launch basis,
production bootstrap, or a device state. It does not lock the full design,
draw, arm, or authorize day 1. Actual target, selector, equipment, paired
three-endpoint, and source-faithful power inputs remain outstanding before
Gate P. The November–December winter observer remains a separate passive study.

The redacted [artifact](../../planning/evidence/c2-predraw-sensitivity-20260928.json)
is a reproducible audit of the accepted exploratory 30-adjacent-pair,
60-local-day design. It does not lock a date or design, replay a selector,
estimate treatment effects, or authorize a draw. Its calculation source is
`research/planner-efficacy/pre_draw_sensitivity.py`.

The exact private September 27 input export is retained under
`/Users/jason/Documents/Codex/verdify-c2-pretrial-20260927/` with mode 0600.
The script verifies the frozen CSV/summary/presence byte hashes, recomputes
every 15-minute bin and daily eligibility result directly from 8,640 exported
bins, checks the prior summary and provisional power artifact, and writes only
aggregate counts and hashes. It refuses an existing output; `--check` is
read-only and byte-compares the artifact:

```sh
python3 research/planner-efficacy/pre_draw_sensitivity.py \
  --input-dir /Users/jason/Documents/Codex/verdify-c2-pretrial-20260927 \
  --output planning/evidence/c2-predraw-sensitivity-20260928.json --check
```

The artifact SHA-256 is
`f88f225f0564209692af5f82a0d344127ac337d4f7919ea8a7641e4c3db036e8`;
calculator SHA-256 is
`de04c952c71e22a8b1f9ec6d7d6672b582000ddfbf625b789af6a4e54375dcc2`.
It binds the private climate CSV (`05c96485a34cf3cdce3a2955e62e7ed98580a1e21546162724a285443abce486`),
daily summary (`462d19ca78906f53cd72122e50537e3337e1b5a9edfa631d9e51125fcf927c49`),
and source-presence receipt (`2f9b27713efe3366cdda40b2648b0d404413c3a83e2233eff2f764d176ed3c83`).
No raw greenhouse rows are in Git.

| Historical analogue | 18-hour joint bins ≥12/15 complete minutes | Days meeting 66-bin/max-two-gap rule | Adjacent climate-only pairs | Six-field complete minutes |
| --- | ---: | ---: | ---: | ---: |
| Nov 2–Dec 31, 2025 | 0/4,320 | 0/60 | 0/30 | 16,827/64,800 |
| Jun 1–Jul 30, 2026 | 4,309/4,320 | 60/60 | 30/30 | 63,214/64,800 |

On the November 2 fall clock-change day, the complete local day is 25 elapsed
hours, while the analyzed [06:00,24:00) window is exactly 18 elapsed hours.
Both historical 60-day primary windows have one UTC offset. The 30/30 warm
pair count is only the **necessary climate-coverage ceiling**: crop target
lineage, as-of forecasts, selector contexts, direct equipment receipts and
the complete nine-state burden are missing for these historical windows. The
warm fixed-panel temperature/VPD bin-**level** correlation is 0.6960; it is
not the three-endpoint covariance of randomized pair contrasts.

| Assumed independent complete-pair probability | All 30 retained | Provisional model-only joint power after reweighting |
| ---: | ---: | ---: |
| 0.9995 | 0.985108 | 0.137760 |
| 0.99 | 0.739700 | 0.103442 |
| 0.95 | 0.214639 | 0.030016 |

All-pair retention is an absolute upper bound on joint advance probability
under each scenario. The right column divides the old 0.13776 Monte Carlo
result by its modeled `0.9995^30` retention, then multiplies by the new
retention. It holds the **provisional** conditional pass rate fixed. It is
not an empirical power estimate: the old model uses 22-hour historical
scales for an 18-hour endpoint, assumed selector frequencies, assumed
cross-endpoint covariance and assumed effects. Source-faithful paired effects,
variance, three-endpoint covariance, selector dilution, and joint power
remain `null` in the artifact.

The November interval is a separate observational winter feasibility
candidate. This historical climate audit neither promotes it to a hot/dry
efficacy trial nor qualifies a later warm-season calendar. It requires no
on-site inspection or physical check gate. Prospective source declarations
and actual 18-hour assigned-day rows are needed to make the narrower future
claims; no device or production state changed here.
