# Physical crop-band evidence boundary (#371)

`fn_planner_scorecard` and `v_daily_kpi` retain two historical measurements:
contract-2 `compliance_pct` is a binary fraction of house-average readings
against historical desired setpoints, while `compliance_v2_attributable_pct`
is graded controller credit. Neither is a physical crop-band outcome.

The API and MCP scorecard now carry a **separate** typed physical crop-band
field, also projected to the public snapshot and planning page. It always
returns `publication_not_qualified` with no physical percentage. The API uses
the ordinary `verdify_api_runtime_login` role. The MCP source
at migration 259 selects distinct ordinary `verdify_mcp_runtime_login`; verify
that login in the live pod after the migration and Secret bootstrap sync.
No table grant, in-database validity flag, or hash can independently authenticate
a physical probe revision. This change adds
no physical evidence table, writer, DB read, historical backfill, or planner
reward term.

The typed `fixed-panel-crop-band-v1` diagnostic describes the future
qualification contract: immutable crop targets as of each evaluated bin, a
fixed north/east/west panel of fresh probes, a complete Denver local-day
15-minute window, axis and joint eligible denominators, high/low panel-mean
miss counts and mean outside distances, and the worst measured zone. Every
eligible axis panel mean is in band, low or high, so its high/low miss counts
must partition its out-of-band bins. The joint means are recalculated on the
shared six-field sample slots; their pass count can exceed either separate
axis pass count. A measured zone can miss while the three-zone panel mean
passes. The public projection reports each denominator, both directions of
severity and the zone separately from controller credit. This is a sampled-bin
measure, not continuous exposure. Validating a diagnostic's internal shape
does not authenticate its source.

The September 4 analysis in #823 used a hypothetical house reference because
historical crop targets and probe freshness were unavailable. Its 28.125%
figure is counterfactual, not physical compliance. The September 4 and 25
observed-minute backfills had zero jointly eligible minutes because their
latest setpoint-log bounds were invalid; they do not imply zero physical
compliance.

#371 remains open for a separately authenticated producer and source-to-live
validation before any physical result can be published. The center probe,
DLI, gas, resource-cost outcomes,
and experiment endpoint are unavailable.
