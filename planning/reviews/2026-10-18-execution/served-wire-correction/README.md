# Served-wire source declaration correction — 2026-10-18

The first combined native PR CI attempt failed. All 19 failures indexed in
[causal-index.json](causal-index.json) stop in `algorithm_revision()` because the
owning `controls.yaml` changed while its declared SHA-256 stayed at the prior
source. The original native trace is retained under the recorded custody locator
and hash; this correction does not relabel that attempt as successful.

The reviewed firmware delta refreshes relay minimum-on/off dwell limits each
control tick without resetting transition history. It does not change solar or
band expressions. The correction updates only the current controls-file hash and
its explanatory comment. The complete Python AST apart from `FIRMWARE_SOURCES`
is unchanged. The resulting algorithm revision is new and source-bound; the
previous declaration and previous algorithm revision are rejected.

The sensor diagnostic change has no additional active fixed hash declaration to
refresh: native probe tests extract the actual sensor lambdas and helper source.
`fixed_panel_source.ROUTE_SOURCE_SHA256` derives the unchanged route definitions.
Historical route-only artifacts and the explicitly historical fixed-panel
counterfactual source hashes remain frozen. No grid, `ORDER_REVISION`, measured
firmware/ELF identity, or accepted scientific artifact was regenerated.

Validation: 29 selected pure tests passed, including all 19 originally failing
cases, prior-declaration/revision rejection, owning-source mutation rejection,
and preserved epoch/clock projections. Focused Ruff, diff and commit guards
passed. No compiler, replay, broad test suite, native workload, firmware deployment
or device action ran for this correction. The combined native CI rerun remains
pending; this source correction is not live or scientific acceptance.
