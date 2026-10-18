# #639 / #587 source qualification packet — October 18, 2026

This packet implements a bounded source repair and records the real current-prefix gap. It does **not** accept either issue, qualify a new order/grid, renew the historical risk exception, or authorize a device, database or message operation. The unchanged six acceptance clauses and their residual ownership are in [residual-clauses.json](residual-clauses.json).

The [execution checkpoint and next actions](lane-status-and-next-actions.md) retain all existing dependencies and scope the next runnable source packet to the actual fog/vent consumer with a causal negative test.

The prefix qualifier previously hard-coded 27 fields, claimed a future full-48 harness would qualify without tool changes, and read the entire compressed corpus before applying the pilot row limit. The repair binds the binary's declared assignments to the actual injection source, requires exact nonzero per-row coverage and explicit zero invariant breaches, streams only the requested pilot rows, and reports missing path evidence separately. The compiled harness remains unable to return `safe`, including when a hypothetical source declares 48 assignments. Existing external-HIL qualification is a separate protocol and is unchanged.

`coverage-source` scans actual consumers, including the runtime snapshot read sites, rather than trusting the committed manifest or matching field names alone. It joins those sites to `policy_injection.h` and hashes the actual quoted C++ include closure and firmware source files. [The source report](coverage-source.json) records 27 assignments: 25 direct Setpoints inputs and two modeled economizer thresholds. Its source notes retain the declaration's historical line references; `control_read_sites` are fresh locations. No source scan establishes native exercised-path coverage.

The missing 21 inputs are eight relay duration fields; nine downstream mister threshold, timing, weighting and budget fields; the direct-wet gate and two vent interlocks; and night VPD bias. Night VPD cannot be re-applied to already-biased corpus columns. The actual relay-wrapper scenario work under #367 may be reused as source evidence, but it is a separate harness and must not increase this replay's coverage count. A complete runner must execute the real consumers, hold each prefix on every evaluated row, preserve state across ticks, and exercise recovery and negative branches. It needs authentic pre-bias/weather/timing inputs; synthetic defaults are not substitutes for missing empirical inputs.

The source report is bound to this branch. After composition with the independent firmware lane, regenerate it from the composed tree before qualifying that source:

```sh
python scripts/component_prefix_replay.py coverage-source > docs/research/639-587-source-qualification-2026-10-18/coverage-source.json
```

That command is a small source scan. It neither compiles nor reads the disturbance corpus.

## Supported native qualification

The normal registered `.agent-fleet/ci.yaml` platform-tests step already runs `tests/test_component_prefix_replay.py`; use the existing native PR cohort and its exact commit status. Do not add another CI pipeline or change the generated registry file. Firmware logic/secretless compilation belongs to the independent firmware lane; this patch changes no firmware bytes.

After ROOT verifies the authorized `vallery` context, existing supported runner/namespace, exact source checkout, toolchain, current placement and owner admission, run [native-prefix-pilot.py](native-prefix-pilot.py) once in that admitted native environment. It compiles one existing replay harness, reads at most 128 corpus rows, runs one baseline witness and one missing-field refusal. No socket, PostgreSQL, Secret, PVC, device or Slack access is required. The tracked corpus and committed profiles are its only data inputs. Use one CPU, at most 1 GiB memory, private scratch, a 600-second total deadline and concurrency one; actual scheduler requests, working sets, quotas and reservations must be refreshed by ROOT before release. These are proposed workload bounds, not a capacity reservation or submission approval.

```sh
python docs/research/639-587-source-qualification-2026-10-18/native-prefix-pilot.py
```

Seal source inputs and attempt metadata before execution; retain stdout/stderr and failures. The pilot emits each compiler/template/baseline/refusal result, including raw stdout/stderr, before later assertions can fail. There are two behavior invocations, three total harness processes (including the policy template), and one compiler process. The compiled binary itself has digest-only custody and is removed with private scratch. Verify exact source/binary/consumer hashes, terminal exit, 128 rows, 27/48 assignments, the `unproven` baseline verdict with zero declared invariant breaches, missing-field exit 64, and `order_revision: null`. Any compiler, corpus, invariant, identity or resource failure stops the pilot; do not treat a generic `unproven` result as a passed pilot or expand the workload. This pilot checks the source repair's native contract only. It deliberately provides no all-prefix or recovery/negative-path qualification.

## Remaining runtime authority

The current source grid `f2ce…` differs from the historical accepted-risk grid `c10f…`. `physical_execution_qualified()` therefore denies active work with the unchanged historical order revision. The retained contained fixture cannot acquire a legitimate active path by copying a genuine experiment identity, changing clock functions, overriding qualification or fabricating a scoped/physical approval. Draft shadow preview and the useful named lifecycle/alert subset are already accepted; repeating them does not advance the residual clauses.

The accepted begin-bundle point, lifecycle rollback and named expired-work alert receipts remain in `planning/reviews/2026-10-17-s3-execution`. In particular, the two authorized alert messages were sent once, the same-attempt reconciliation accepted actual completion/custody, and original bridge/collector failures remain failed. Historical checkpoints that predate those approvals remain immutable history. This packet makes no new Slack request.

After actual consumer/scenario coverage exists, admit full current-grid prefix qualification over all required treatment and authentic recovery starts. Only its genuine successful evidence can support a separately reviewed source revision. Then the runtime lane can qualify exposure, exclusivity, generation/freshness, lease loss and recovery against an authentic current identity. #587's populated analyst/freezer blinding and locked/armed no-reopen need their real source-locked lifecycle/data prerequisites; #783 interface preparation can proceed without pretending those clauses passed. #641 retains physical epochs, rescue and successful safe handoff. No acceptance text, state, dependency or estimate changes are made here.

Local validation: 55 focused pure Python tests and Ruff check/format passed; no compiler, corpus replay, database or device work ran on the Mac. Independent source review found no blocker. The [bounded native pilot](native-f48c889e-01/README.md) subsequently passed on composed source `f48c889e` at 07:52:55 UTC. Its complete process outputs and independent source/input/evidence-hash checks are retained separately. The proposed 1 CPU / 1 GiB limits above were not applied: ROOT admitted the unchanged registered runner's main-container limits of 2 CPU / 4 GiB. This is successful source-contract verification with an **unproven 27/48** safety verdict. The full registered CI cohort is a separate result, and neither issue is complete.
