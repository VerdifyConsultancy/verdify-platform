# Dormant randomized day-1 activation

This component is intentionally absent from every overlay. It is a separate
GitOps stage from both `experiment-v2-design-lock` and
`experiment-v2-randomized-day1-authorization` and must not be combined with
either one. It contains no approval command. Migration 238 requires the prior
API audit identity before any randomized selector context, choice, or work can
be inserted.

Only a later, separately reviewed selection of this component enables the
lifecycle, selector, freezer, API consumer, and the existing bounded component
executor for the exact experiment ID. Generalized vector mode stays off
everywhere.

Before selection, require the exact design/source/image/Argo pins, a retained
metadata-only OpenAI preflight receipt, an `armed / randomized / closed`
database state, exactly one randomization receipt, zero open exposures, and the
retained receipt from the separate attended #642 authorization component. The
selector and outcome identity ConfigMaps must be the exact hash-bound artifacts
consumed by the design lock.

Rollback is the sibling `experiment-v2-randomized-day1-rollback` component. It
first uses the audited API emergency-hold command, whose database function
closes exposure in the same transaction, and then rolls every workload back to
coarse-off/empty-ID while preserving generalized vectors off. Baseline recovery
after facility yield remains a separate facility-authorized action.
