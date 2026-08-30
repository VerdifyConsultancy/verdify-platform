# Dormant randomized day-1 authorization command

This component is intentionally absent from every overlay. It contains only
the distinct, authenticated #642 approval command. It does not patch or enable
any workload and therefore cannot activate randomized operation.

Select it only after the design-lock stage has finalized exactly once and the
blinded launch status reports `awaiting_separate_day1_approval`, closed
admission, and zero open exposures. Retain the treatment-free API receipt,
remove this one-shot component, and review the separate
`experiment-v2-randomized-day1-activation` component in a later Git change.
