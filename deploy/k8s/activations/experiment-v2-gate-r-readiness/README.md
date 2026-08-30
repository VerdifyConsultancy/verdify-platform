# Gate R recovery-readiness activation

This isolated render collects and guards one recovery-mode packet before the
migration-239 Gate R activation. The committed values are invalid and the Job
is suspended.

For the attended recovery-readiness commit, add this component and its exact
activation patch to the canonical production overlay, replace both values,
and unsuspend only the readiness Job. Keep Gate R absent. Reconcile the complete
production application without pruning or resource selection, retain the Job
logs, and bind the passing `packet_sha256` into the later Gate R activation.

The GitOps pin is deliberately not embedded in its own activation commit. The
read-only collector obtains the exact current revision from the Argo Application,
records it in the packet, and the guard requires every provenance and full-sync
operation copy to match that exact 40-character SHA. The application-source SHA
remains an explicit activation value and must match the running image attestation.
