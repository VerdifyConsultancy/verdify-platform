# Experiment v2 Gate R activation

This is the recovery-only migration-239 surface for M8.1. It cannot create a
new proof attempt, open aggressive admission, or award proof credit. The
committed default is suspended and contains invalid placeholders.

After the recovery-mode readiness packet passes, create one activation commit:

1. Supply the exact retained authorization/aggressive/recovery work IDs,
   recovery-evidence and preflight-verified migration-ledger digests,
   experiment axes, source/pin
   identities, readiness packet digest, retained experiment-runtime generation,
   current live connection generation and writer runtime reference, and a fresh
   three-minute-to-twelve-hour Gate R window.
2. Set `spec.suspend: false`, render this directory, and inspect the full diff.
3. Reconcile the complete render without pruning or resource selection.
4. Preserve the completed Job and attach its secret-free JSON receipt.
5. Return Argo to ordinary production source after recording the receipt.

The hook opens `baseline_recovery` only inside the same database transaction as
the migration-239 resolver. A mismatch rolls back to the retained no-exposure
hold. Gate R authority must never be reused for Gate P.
