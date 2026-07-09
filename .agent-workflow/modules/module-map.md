# Recovery module map

Status: approved. Contracts are canonical YAML under `contracts/`.

| Module | Responsibility | Exclusive mutable surface | Requirements |
| --- | --- | --- | --- |
| `device-writer-reconcile` | Canonical cfg IDs, true reconnect generation, normalized no-op logic, yielding writer queue, terminal write truth | Ingestor writer core, `_common`, dispatcher/confirmation, tunable registry/entity map | NSR-001, NSR-007 |
| `planner-delivery` | MCP tool health, terminal action, bounds, plan lifecycle, forecast correction, active recovery | MCP planner/tool sections after evidence-adapter handoff, forecast/context, Hermes manifests, planner migrations | NSR-002, NSR-007 |
| `firmware-control-policy` | One relay resolver, center-only climate, explicit zone irrigation, wall feed, DLI/night signals, heap safety, preservation regressions | Overlapping greenhouse firmware YAML/lib/tests | NSR-003–NSR-007 |
| `evidence-contracts` | Availability, solar/VPD/cycle/night/replay truth, canonical equipment, fresh water and scoped energy evidence | Serialized evidence migrations/schema, outcome adapter, daily/alerts, API/site/dashboard, replay exporter/corpus | NSR-004–NSR-007 |
| `runtime-release-verification` | Credential gate, ordered integration, cycling baseline, promotion, migration apply, stale-intent retirement, OTA, runtime/rollback proof | Release scripts, prod overlay, recovery release/runbook/closeout artifacts | NSR-007 |

The modules intentionally do not mirror every repository directory. They isolate mutable ownership where this recovery has coupled state. In particular, all greenhouse firmware paths stay in one module, and `mcp/server.py` stays with planner delivery while DLI availability is produced upstream by the evidence contract.
