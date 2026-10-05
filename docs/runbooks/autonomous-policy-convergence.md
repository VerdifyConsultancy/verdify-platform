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
