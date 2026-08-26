@jvallery **P0 superseding final-SHA collective request — execute now.** PR #668 merged after full local CI, in-cluster PR CI, and independent audit all returned GO. Its source intentionally folds the live-grid attestor into the first feature-off image release, avoiding a second five-image build and singleton-ingestor restart.

The prior `a754e873` recovery request is now superseded because no Workflow/start/build/pin receipt ever appeared for it. **Do not create the old a754 collective now.** If preflight unexpectedly finds that it already exists, report it and stop before creating anything else so the controller can resolve the race.

Exact new source:
- revision: `e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf`
- before-revision / sole parent: `a754e873e8c42bfbb05a4c808bdf023c6462691a`
- commit subject: `experiment: prepare Gate-2 shadow and physical qualification evidence (#668)`
- fixed recovery name: `verdify-platform-ci-recovery-e0be4e05edbb`

Idempotent procedure:
1. Verify `main` is still exact `e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf` with the sole parent and subject above.
2. Search for any existing collective for **either** superseded revision `a754e873…` or current revision `e0be4e05…`, including normal merge-trigger names, both fixed recovery names, and same-prefix repo/revision labels.
3. If any a754 collective exists, do not create e0be and return its metadata immediately. If an e0be collective exists, do not duplicate it; return that receipt and let it continue.
4. Only when preflight proves neither exists, instantiate the current live `workflowtemplate/verdify-platform-ci` once as `verdify-platform-ci-recovery-e0be4e05edbb`, using exactly the revision, before-revision, and commit subject above plus current repo labels; do not override entrypoint or ServiceAccount.
5. On transport ambiguity, GET the fixed name; never resubmit blind.
6. Return metadata only: Workflow name/UID/phase; exact source identity; five exact-SHA image build results; and the single generated bot pin child. Verify planner, setpoint-server, and lab-publisher pins remain byte-identical. **Do not sync production.**

The separate protected Gate-2 Secret/SOPS reconciliation requested in comment `5418586882` still has no receipt and should run concurrently. The repo controller has the pin verifier, non-pruning Argo plan, hook/evidence harness, live snapshot, and rollback matrix ready and will take over as soon as the two Root receipts appear.
