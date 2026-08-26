Physical-readiness source update — 2026-08-26 02:22 UTC. No approval is requested or inferred by this comment, and no device call occurred.

PR #668 is merged as exact main `e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf`. It adds two deliberately non-actuating preparation surfaces:

- a 48-setter/48-readback entity-grid attestor fed only by the ingestor's one existing authenticated ESPHome enumeration and existing firmware-state callback—no new connection, enumeration, subscription, replay, setter, or service call;
- an offline exclusive `0600` prefix-replay packet generator covering baseline↔moderate/aggressive and every full-48 recovery prefix without claiming compiled/HIL qualification.

The source-grid candidates are exact, but physical execution remains fail-closed: deployed source identity, running firmware/grid/current-state receipt, actual generated `main.cpp` and binary, compiled replay, HIL results, the direct #424 semantics proof, and controlled #433 writer/reconnect evidence are still required before `GRID_REVISION`/`ORDER_REVISION` can be promoted. Independent audit and full/in-cluster CI are green, including dynamic connect-then-stop fault tests.

Production has not deployed this source and the component capability remains OFF with active experiment ID empty and generalized vector mode OFF. The first #641 scoped `commissioning_probe` approval and later combined physical signoff are still absent. They remain exactly the two decisions defined by this issue; software preparation and passive live-grid capture do not consume either approval.
