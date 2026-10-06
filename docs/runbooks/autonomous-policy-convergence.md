# Source-bound ordinary policy convergence — #949

Production opts in with `VERDIFY_AUTONOMOUS_POLICY_STAGING=1`; the default remains
off. A broad desired delta is admitted from a fresh running-writer preview,
without an external approval step. Admission requires an exact 40-character
source revision, immutable active plan rows/expiry, current connection generation,
all48 fresh atomic cfg snapshots matching the existing native subscription and
a strictly held sole-writer lease. No new device connection is opened.

The admission file includes the exact preview fingerprint, source, session,
generation, candidate fields, plan rows and readback baseline. Each stage stays
at or below12 commands, waits for actual terminal delivery confirmation and
current-generation equivalent readback, and expires within30 minutes and before
plan expiry. Quantized seconds retain the existing comparator semantics. Crop
VPD motion and existing moisture guardrails may rebase only through their
explicit source-bound paths; fixed fields and plan identity cannot drift.

Completed receipts are archived before admitting a new broad policy. Failed,
cancelled, uncertain or halted stages remain terminal history: autonomous mode
does not replace their approval, replay requests or clear failed receipts.
Snapshot lag before admission is a bounded recheck, not fabricated confirmation.
A changed generation/plan/source or missing readback remains a bounded hold.
`writer-policy-hold.json` records the reason, affected desired fields, immutable
plan provenance where applicable and current-generation readbacks. The dispatcher
heartbeat continues and the ordinary global12-command fence remains unchanged.

Verify the exact running digest/source and sole writer, retain native admission,
stage and completed files plus delivery rows, observe a real plan transition and
two steady hours, and independently prove one ESP32 connection. Check task age,
heap constraints, timely confirmation, validation errors and repeat broad holds.
Disable the prod opt-in to restore preview-only admission; retain terminal files
and database history before any recovery. A rollout is not convergence proof.


A declared live moisture guardrail can become a candidate after admission even
when it originally matched the controller. It must be one of the seven listed
source-owned fields, present in the unchanged approved plan and original native
baseline, and equal the current dispatcher guardrail value. New fixed fields,
changed plan rows, source/generation changes and lost readbacks still halt.

The explicit `prepare-writer-stage.py --confirmed-two-stage` archive contract
(version3) covers only a halted run with13..24 unique confirmed effects over
two stages, each at most12 commands. Original state/approval/Pod custody is
retained. Every first-stage request must occur after the original preview and
confirm before the final stage starts; final-stage requests must confirm before
the archive cutoff. Missing, duplicate, failed, sent-only, expired, changed or
later requests reject recovery. Existing version2 single-stage custody retains
its original ceiling and semantics. The owner supplies actual raw custody and
independent native database request receipts through the existing preparation
CLI; no receipt is inferred from a plan or command count alone.

Archival grants no setter authority and leaves the original halt active. After
the archive is persisted, capture a fresh preview under the reviewed running
source, current generation and complete current effective plan. Prepare a
distinct normal approval with the existing CLI and submit it through the sole
writer's ordinary stage path. Never reset historical requests or replace their
confirmation status. A successor rollout does not itself authorize an old
approval. Restart the two-hour acceptance window only after truthful convergence
under the successor source and digest.
