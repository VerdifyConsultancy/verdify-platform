# Physical crop-band evidence boundary (#371)

`fn_planner_scorecard` and `v_daily_kpi` retain two historical measurements:
contract-2 `compliance_pct` is a binary fraction of house-average readings
against historical desired setpoints, while `compliance_v2_attributable_pct`
is graded controller credit. Neither is a physical crop-band outcome.

Migration 250 adds an append-only, owner-written daily revision and a bounded
reader for a third, separate measurement. Its `fixed-panel-crop-band-v1`
diagnostic requires immutable crop targets as of each evaluated bin, a fixed
north/east/west panel of fresh probes, a complete Denver local-day 15-minute
window, axis and joint eligible denominators, high/low miss counts and mean
outside distances, and the worst measured zone. The typed API/MCP reader
rejects inconsistent revisions. The public snapshot labels these as sampled
bins, never continuous exposure. A missing or malformed latest revision is
explicitly unavailable; it cannot fall back to an older revision or to a
legacy metric.

There is deliberately no automatic writer or historical backfill. A future
qualified producer must retain the immutable target, fixed panel, input, and
calculation manifests identified by the diagnostic hashes; independently
verify their contents, revision timing and per-probe freshness; and write a
complete daily revision as the database owner. A hash or `verified` flag is a
claim, not independent proof. The September 4 fixed-panel analysis in #823
used a hypothetical house reference because historical crop targets and probe
freshness were unavailable; its 28.125% figure is counterfactual and must not
be inserted as a physical revision. The September 4 and 25 observed-minute
backfills also had zero jointly eligible minutes because their latest
setpoint-log bounds were invalid; they do not imply zero physical compliance.

The center probe, DLI, gas and resource-cost outcomes remain unavailable.
The physical result is not an experiment endpoint or a planner reward term.
