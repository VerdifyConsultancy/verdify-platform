# Verdify Platform Milestones

Last updated: 2026-06-13

Agent name: `verdify-platform`

## Active GitHub Milestones

| Milestone | Open | Closed | Notes |
|---|---:|---:|---|
| Cutover Complete (done) | 0 | 30 | Historical done bucket; do not add new active work. |
| Enablement: Three-Env (dev/stage parity) | 7 | 2 | Contains older three-env language; staging is retired per `AGENTS.md`. #111 and #112 are closed as superseded. |
| Enablement: Compliance & Twins | 5 | 4 | Firmware/twin/compliance follow-ups. |
| Enablement: Data Hygiene & Observability | 11 | 7 | Current product-plane and observability cleanup; old unified roll-up #286 is closed as superseded. |
| Enablement: Decommission & Auth | 7 | 0 | VM/auth cleanup and Jason-gated decommission tasks. |
| Hardware / Seasonal (operator-gated) | 6 | 0 | Hardware and seasonal changes; Jason-gated. |
| M7 — HA: first-principles resilience | 10 | 15 | HA hardening after 2026-06-07 incident; storage-heavy dependencies. |
| Greenhouse Control Optimization | 17 | 0 | Firmware/control optimization issue set from 2026-06-09 replan. |
| Deploy Enablement (agent access + firmware CI/OTA) | 12 | 0 | Agent-pod access, CI, OTA secret sealing, safe shadow iteration. |

## Historical Milestones

| Milestone | Evidence |
|---|---|
| M1 — CI unblocked + images published | Closed issue #69; issues #78, #81, #82, #92, #99, #126-#128. |
| M2 — Data safe before migration | Closed issue #72; parity and schema work including #129. |
| M3 — Dev/prod substrate ready | Closed issues #25, #28, #84; current staging language is historical. |
| M4 — Handoff safety green | Closed issue #71 and related route/device safety work. |
| M5 — Single-writer cutover | Record issue #216 documents execution on 2026-06-07. |
| M6 — Iris decommission ready + product plane | Record issue #217 documents VM powered off; product follow-ups remain. |

## Next Milestone Recommendation

Create a current milestone named `Lane Board Normalization` only if GitHub issue
work expands beyond the documentation pass. Otherwise, track normalization under
the relevant existing issues and `PROJECT_BOARD.md`.
