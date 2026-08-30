# Experiment v2 direct-proof activation

This directory is the deliberate, one-shot GitOps surface for a future attended
baseline → aggressive → baseline physical proof. It is not referenced by the
ordinary production overlay or any ArgoCD Application.

The committed render is intentionally dormant:

- the proof Job is suspended;
- the singleton ingestor remains component-off, active-ID empty, and vector-off;
- every authorization and bounded-window value is an invalid placeholder.

After Wave 0 convergence and a fresh facility window, create a new append-only
attempt by changing only `activation-values.patch.yaml` in a dedicated commit:

1. Supply the exact experiment UUID, new unique authorization/facility refs,
   UTC start/end (3 minutes through 12 hours), supervisor, rescue owner, and
   actor metadata, plus the exact current 12-character config revision.
2. Replace `dormant-no-authority` with a unique attempt marker, set the component
   to `enabled`, and bind the exact active experiment ID. Keep vector mode `off`.
3. Replace the invalid Job image with the exact current production API digest,
   add only any currently justified scheduling constraints, then set the Job's
   `spec.suspend` to `false`.
4. Add the component and exact activation patch to the canonical production
   overlay, render and inspect the complete diff, then reconcile the complete
   production Application without pruning or resource selection. Keep its Argo
   source path at `deploy/k8s/overlays/prod`.
5. After the immutable receipt (success or failure), return Argo to the ordinary
   component-off production overlay. Do not rewrite or reuse an earlier attempt.

The activation does not try to embed its own unknowable commit SHA. At every
boundary the read-only collector obtains the exact current GitOps revision from
the Argo Application; the guard then binds all provenance and the full-sync
operation revision to that exact SHA. The explicit application-source value must
still match the running image attestation throughout the attempt.

The database proof/attempt/recovery ledgers remain append-only. Removing an
expired manifest from ordinary desired state does not delete prior evidence.
