# #371 metric-path acceptance — migration 274

The three literal #371 acceptance criteria are met. This closes the executable
metric and consumer path; it does not qualify production physical commissioning.
Production has **zero physical revisions**, a null diagnostic and
`publication_not_qualified` for both current and historical dates. #749 remains
on hold. No physical publication, experiment, model/plan run or OTA was performed.

## Literal acceptance

- [x] One isolated PostgreSQL fixture publishes and reads qualified synthetic
  physical evidence through the real owner-only SQL functions, actual API HTTP
  route, registered FastMCP scorecard tool and existing public planning renderer.
  **2/96 physical joint bins = 2.1%**, **85.8% controller attribution credit** and
  **6.1% legacy binary reading fraction** remain separate. The fixture appends a
  correction, preserves revision history, rejects unauthorized raw access and
  publication, and rejects malformed, unqualified or unfinished-day evidence.
  Four reviewed artifact files must match the existing strict model's immutable
  target, panel, input and calculation-source hashes. See the
  [actual private SQL/transport fixture](https://github.com/VerdifyConsultancy/verdify-platform/blob/325558d4d8f730b0c42afff0de87e08eda3b5aa1/tests/test_physical_crop_band_sql_reader.py),
  [private PostgreSQL focused output](evidence/physical-reader-private-postgres-focused.log)
  and [post-repair focused output](evidence/physical-reader-repaired-focused.log).
- [x] Hand-calculated means, high/low distances, worst measured zone, independent
  axis and shared-slot joint denominators, missing probes, empty bins and
  conflicting duplicate samples pass. Missing members do not renormalize a
  panel, and zero eligible bins do not become zero-percent compliance. Separate
  axis means and joint shared-slot means can legitimately differ. The existing
  [fixed-panel tests](https://github.com/VerdifyConsultancy/verdify-platform/blob/325558d4d8f730b0c42afff0de87e08eda3b5aa1/tests/test_fixed_panel.py)
  and [strict physical model tests](https://github.com/VerdifyConsultancy/verdify-platform/blob/325558d4d8f730b0c42afff0de87e08eda3b5aa1/tests/test_physical_crop_band_evidence.py)
  identify immutable crop targets separately from historical desired controller
  setpoints. Hash/model consistency does not authenticate hardware placement or
  historical sampling; those facts require the trusted owner's genuine evidence.
- [x] Current **2026-10-05** and historical **2026-09-29** bounded SQL brackets
  agree across actual new API HTTP, both new MCP replicas, public projections and
  the actual Iris pod's authenticated registered `scorecard` tool. All four API/MCP
  pods pass fresh ordinary TCP startup attestation with current user equal to
  session user and the exact search path. They have no raw physical table
  SELECT/INSERT or producer EXECUTE. History is append-only; the private fixture
  proves revisions rather than a silent favorable fallback. See
  [qualified live capture](evidence/physical-reader-live-qualified.json), its
  [unchanged original capture](evidence/physical-reader-live-original.json) and
  [exact original operator capture code](evidence/physical-reader-live-capture.py.txt).

## Genuine live values and denominator limits

| Date | Legacy binary reading fraction | Controller attribution credit | Native route-only joint bins | Qualified physical result |
|---|---:|---:|---:|---|
| 2026-10-05 | 2.2% | 74.1% | 0/5 eligible; 5/72 window bins eligible | Unavailable |
| 2026-09-29 | 11.8% | 77.5% | 47/72 eligible = 65.3% | Unavailable |

The current native window explicitly withholds 44 bins outside the prospective
target, three with source discontinuity and 20 not elapsed. All 72 expected bins
are accounted for; missing bins are not a favorable denominator. Native route
diagnostics have `physical_proof_eligible=false` and
`experiment_endpoint_eligible=false`. Legacy binary fields remain contract 2:
house-average scored-reading fractions against historical desired setpoints,
with unverified coverage and no confirmed firmware-consumed target lineage.
They are not duration-weighted physical exposure. Controller credit is separately
named, and nominal stress axis-hours can overlap. No center measurement, DLI,
gas or resource-cost eligibility is inferred.

The actual Iris pod UID is `f82f6550-816e-443f-8cd8-d1c6f0e06f50`. Its existing
bearer stayed in that consuming process; `tools/list` returned the registered
scorecard among 23 tools, followed by current/historical read-only tool calls.
This proves authenticated planner context accessibility, not a new LLM action.
The standalone scalar DB planner adapter is not claimed as a physical consumer.

The naturally served Lab physical row matches the new public API's unavailable
projection. [Natural public rendering evidence](evidence/physical-reader-natural-public-render.json)
retains the page hash, publication status and snapshot time, separately from
current-now native data and source renderer output. Static publication has its
own as-of generation; a newer native bin does not authorize forcing the publisher.

## Source, CI, build and deployment identities

[PR #955](https://github.com/VerdifyConsultancy/verdify-platform/pull/955) merged
as source `325558d4d8f730b0c42afff0de87e08eda3b5aa1`. Its first head
`5a3939d32e1e6ef4fac56f086f7621d23020bf12` failed the existing public wildcard
projection policy. The repair explicitly selects the eight reader columns in
API/MCP without weakening policy or changing SQL seals, models or ingestor source.
Repaired head `fb7256d87fa0d326fb9b01a144f0da8c7331b424` passed enforced full Linux
PR CI; source325 then passed full Linux validation and all eight registered builds.

- [Original failure and repair receipt](evidence/physical-reader-first-linux-failure-repair.json),
  [failed full stdout](evidence/physical-reader-first-failed-linux-full.log).
- [Repaired PR CI identity receipt](evidence/physical-reader-repaired-pr-linux-ci.json),
  [repaired full stdout](evidence/physical-reader-repaired-linux-full.log).
- [Main325 full stdout](evidence/physical-reader-main325-linux-full.log),
  [all-eight build provenance](evidence/source325-all-eight-build-provenance.json),
  [candidate actuator proof](evidence/source325-actuator-candidate-proof.json).
- [ROOT live release proof](evidence/source325-live-release-9b4f.json): full sync
  `9b4f8023839ffda05b9d0fd1a70fe63e62c6d9b4`, Synced/Healthy, 127 resources,
  six fresh normal bootstrap hooks, correct ledger271–274, smoke9 and unchanged
  source81 writer UID/generation with immediate actual TCP/capability attestation.

Only API `3d5d02…`, MCP `3092da…` and migrate `548c40…` were promoted. Migration274
hash is `0b939df1a79e3ce71835e59aedb5d1898805cd43cae72b79fa905f77067a8e4f`.
Applied migration273 and earlier hashes remain unchanged. Fingerprints change
because private catalog objects were added; the ingestor gains no privileges.

## Forward-safe rollback and preserved limitations

After274, retain source325 C0/bootstrap configuration, applied ledger274 and
migrate `sha256:548c40acb0b77bdb3f129ce36e7fbd3e5a8b8189fad59a04fa0cb4ea9e232585`.
A practical consumer rollback re-pins only API to
`sha256:09a7e0ea6f87fcea9536df19a09d95c3ac2575493bb3f63bffea6cae77eafb98`
and MCP to
`sha256:c7a4044b824f9f0ae2945ce1cc0f601965cacd25ee8791fecaf00fd47e6cf20a`.
Do not revert the full release to642/c8 or restore old bootstrap/migrate against274.

[Oldc8/new325 startup SQL is byte-identical](evidence/physical-reader-forward-rollback-source.json).
Both existing oldc8 MCP pods passed fresh ordinary TCP attestation after274 during
normal rolling update ([actual old-pod proof](evidence/physical-reader-old-mcp-tcp-after274.json)).
The old API pod had already disappeared before capture; its compatibility proof
is identical startup SQL plus new325 actual TCP success, not an old-image TCP
probe. No rollback rollout was performed. Oldc8 consumers would continue reporting
physical evidence unavailable and would not serve a later qualified revision.

The first Iris operator probe used system Python's unavailable `mcp` SDK import
([sanitized original error](evidence/physical-reader-iris-operator-sdk-unavailable.json)).
The successful probe used the existing standard-library JSON-RPC transport; no
product patch was needed. One earlier emulated ingestor attestation returned false
without a reproduced cause; its history remains in the
[source274 boundary rehearsal handoff](https://github.com/VerdifyConsultancy/verdify-platform/blob/325558d4d8f730b0c42afff0de87e08eda3b5aa1/docs/handoffs/s1-2026-10-05/physical-reader-boundary-rehearsal.md).
ROOT's immediate live source81 TCP qualification supersedes emulation as current
acceptance without deleting that historical limitation.

[Artifact byte hashes](evidence/physical-reader-artifact-hashes.json) bind the
copied fixture, failed/repaired CI, original/derived live capture and rollback
proofs. The original validation Pod UIDs and complete stdout were retained before
GC; missing terminal Pod objects are explicitly reported in their receipts.
