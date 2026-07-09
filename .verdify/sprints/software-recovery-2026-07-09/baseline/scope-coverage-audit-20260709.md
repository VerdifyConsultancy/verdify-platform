# Software-recovery scope coverage audit

Date: 2026-07-09

Verdict: **AMEND EXISTING CONTRACTS BEFORE DOWNSTREAM DISPATCH**. The approved 17-issue/eight-lane allocation remains valid, but four acceptance surfaces were weaker than Jason's stated outcome and could have produced false completion.

## Fresh read-only runtime evidence

- The sole ingestor repeatedly entered reconnect reconciliation about every 5m22s, seeded 147 cfg readbacks, compared stale `band_track_fraction=0.25` against device readback `0.0`, and delivered a 69-value batch. This is the pre-fix #433 baseline; no production action was taken.
- Both `verdify-planner` HTTP processes stayed Ready while their `planner-worker` threads exited after transient `verdify-db` DNS failure. The run store contained zero `planner_graph_runs`, confirming this deployed workload is non-authoritative and not evidence of the active Hermes/MCP miss. It is nevertheless false green and must become truthful or be removed from desired state.
- Critical alert 7676, `planner_required_plan_missed`, remained open. It may close only from a valid Hermes/MCP terminal plan action, never from a planner_graph synthetic run.

## Coverage corrections

1. **Planner runtime truth.** The planner-delivery lane now owns the narrow planner_graph worker/health/test/manifest surface required to recover from DB/DNS loss or explicitly decommission the unused workload. Production trigger routing, plan authority, and device writes through planner_graph remain prohibited.
2. **Irrigation exactness.** Firmware acceptance now requires climate-only center-mist origins, zero eligible legacy 10:30 jobs, restart/missed-window-safe weekly solar eligibility, and positive calibrated-flow liters-to-duration conversion that fails closed.
3. **Dry-out outcome honesty.** Evidence-core must emit exactly one `effective`, `ineffective`, `blocked`, or `insufficient_evidence` disposition. Firmware cannot freeze until the controller accepts that disposition; ineffective evidence is not a completed control fix.
4. **Executable release phases.** Release tooling/manifest preparation starts after reviewed implementation merges. The controller then produces live schema/service/writer/DLI/planner/resource evidence, retires stale intent, performs the exact gated OTA, and verifies the settled state. Credential rotation remains a separate hard gate before any production phase.

## GitHub authority reconciliation

The bodies of #299, #383, #386, #410, #390, and #377 predated the July 9 decisions and still requested superseded behavior. They must be rewritten to match the approved preservation, evidence, topology, and release contracts before their lanes dispatch. Issue #427 must distinguish the active Hermes/MCP path from the zero-run non-authoritative planner_graph health defect.

## Intentional limits

- Physical DLI sensor replacement remains Jason-owned; software reports unavailable.
- Wall-feed chemistry, product, injector, flow, distribution, and flush commissioning remain physical inputs. Software may become weekly, wall-only, liters-based, exact-once, and fail-closed, but fertilizer stays disabled until commissioning passes.
- No fixed-clock Vanda watering, speculative dwell/shoulder/post-wet tunables, second writer, credential rotation, production sync, or OTA is authorized by this audit.
