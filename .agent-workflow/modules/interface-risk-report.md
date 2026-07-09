# Cross-contract interface risk review

Status: approved with no blocking risk.

## Findings and dispositions

| Risk | Severity | Disposition |
| --- | --- | --- |
| `firmware/greenhouse/controls.yaml` contains irrigation, DLI, and dry-out behavior | High | One `firmware-control-policy` owner; do not parallel-edit the file |
| `mcp/server.py` contains the evidence `outcome_kpi` adapter and planner tool/lifecycle code | High | Evidence/resource/DLI lanes edit only the adapter in serialized waves; planner receives the final file and owns all other MCP behavior; no concurrent edit |
| `_common.py` is touched by dispatcher and alert work | High | Device writer owns `_common.py`; evidence alert changes consume its public helpers without modifying it |
| New migrations for plan lifecycle, DLI/job evidence, forecast correction, and dry-out could collide | High | Controller assigns unique sequential numbers and applies/rollback-proves one migration at a time |
| Base planner manifests and prod overlay can conflict during promotion | Medium | Planner owns base component changes; release verifier owns only prod digest overlay and deployment evidence |
| Center climate mist and explicit irrigation share physical relays | Critical | One firmware resolver/owner; no independent relay writes or new job path outside it |
| Stale 0.25 plan can repin before consumers normalize correctly | Critical | Retire only after planner and writer repairs are live and verified; transaction plus zero-repin watch |
| Automatic wall schedule could expose uncommissioned fertilizer | Critical | Missing commissioning is an invariant failure; schedule may be enabled only after topology and dry-run proof, while physical feed remains disabled until measurements exist |
| DLI schema can break existing consumers | High | Add availability/backward-compatible nullable fields first, update every consumer, then change firmware publication |
| OTA combines several firmware behaviors | High | Single firmware owner, targeted behavior tests, full replay/invariants/check, map/heap review, and retained last-good rollback |
| Old cycle premises could trigger speculative firmware behavior | High | Raw-transition audit narrows #299/#383/#386 to preservation/tests; #367/#371 are deferred; #389/#390 are acceptance authority |
| Canonical/legacy equipment slugs and stale water ledger can corrupt resource evidence | High | Migrate active aliases before consumers, restore idempotent freshness-monitored events, conserve complete-day totals, and keep scope/uncertainty explicit |
| Committed DB credential matches the live application secret | Critical | Fail-closed source cleanup may merge; all production release remains blocked until explicit scoped rotation and redacted consumer verification |

## Review conclusion

Every output has a named consumer, every input a producer, schemas and compatibility rules are explicit, mutable paths have one owner, and the only bidirectional runtime dependency has a serialized schema strategy. The contracts are suitable for bounded sprint slicing; no implementation lane may expand owned paths without controller review.
