# Verdify Platform Project Board

Last updated: 2026-06-16

Agent name: `verdify-platform`

## Connection Status

- Required board: `verdify-platform`.
- Current board: `VerdifyConsultancy` project #5,
  <https://github.com/orgs/VerdifyConsultancy/projects/5>.
- Current finding: the exact `verdify-platform` board exists with 27 issue
  cards and 22 fields.
- Repository link: linked to `VerdifyConsultancy/verdify-platform`.
- Current planning surface: the ten controller-replan lane epics #343-#352.
- Legacy/evidence cards remain on the board so old issue history is not lost;
  they are no longer the primary planning decomposition.
- Required fallback: keep `## Project Tracking` in every new or materially
  updated issue because issue bodies remain durable when project fields drift.

Workflow details live in `docs/PROJECT_BOARD_WORKFLOW.md`.

## Required Issue Block

```markdown
## Project Tracking

- Status: Ready
- Priority: P1
- Effort: L
- Component: firmware/ingestor/db/grafana/docs
- Sprint: S5 firmware-first-climate-core
- Milestone: G1 - Firmware-First Determinism
- Epic: L2 Firmware Core
- Agent Lane: verdify-platform
- Related Issues/PRs: #287, #344
- Dependencies: Jason OTA gate; schema-first sequencing
- Evidence: AGENTS.md, EPICS.md
```

## Field Vocabulary

Statuses: `Backlog`, `Ready`, `In Progress`, `In Review`, `Done`.

Fields: `Priority`, `Effort`, `Component`, `Sprint`, `Epic`, `Agent Lane`,
`Milestone`, `Related Issues/PRs`, `Dependencies`, `Evidence`.

Board cards are EPICS. Child issues/tasks can exist, but planning decisions
happen at the lane-epic level unless a child issue is explicitly operator-gated.

## Canonical Current Lane Cards

| Status | Priority | Epic card | Sprint | Milestone |
|---|---|---|---|---|
| In Progress | P1 | #343 L1 Architecture Audit, Drift Check, and CI/CD | S4 controller-architecture-audit | G0 - Controller Architecture Audit |
| In Progress | P1 | #344 L2 Firmware Core | S5 firmware-first-climate-core | G1 - Firmware-First Determinism |
| Ready | P1 | #345 L3 Climate Control | S5 firmware-first-climate-core | G1 - Firmware-First Determinism |
| Ready | P1 | #346 L4 AI Planner and Tunables | S6 data-observability-planner-contracts | G3 - Planner, Irrigation, Lab, and Research |
| Ready | P1 | #347 L5 Data, Schema, and Source of Truth | S6 data-observability-planner-contracts | G2 - Data Contracts and Observability |
| Ready | P1 | #348 L6 Observability, Dashboards, and KPIs | S6 data-observability-planner-contracts | G2 - Data Contracts and Observability |
| Ready | P1 | #349 L7 Lighting and Occupancy | S5 firmware-first-climate-core | G1 - Firmware-First Determinism |
| Ready | P1 | #350 L8 Irrigation, Fertilization, and Orchids | S7 irrigation-lab-testing-hardening | G3 - Planner, Irrigation, Lab, and Research |
| Ready | P2 | #351 L9 Lab Notebook, Website, and Publishing | S7 irrigation-lab-testing-hardening | G3 - Planner, Irrigation, Lab, and Research |
| Ready | P1 | #352 L10 Testing and Research | S7 irrigation-lab-testing-hardening | G3 - Planner, Irrigation, Lab, and Research |

## Legacy And Evidence Cards

These cards remain useful issue anchors. Treat them as evidence, child umbrellas,
or historical workstreams under the current lane epics instead of adding new
top-level planning around them.

| Status | Epic card | Current relationship |
|---|---|---|
| Done | #334 Lane Board Normalization | Historical board setup evidence |
| Ready | #207 Platform Architecture Inventory | L1 evidence/child anchor |
| Done | #331 API/Service Map | L1 evidence |
| In Progress | #335 CI/CD And Promotion Hardening | L1 child anchor |
| In Progress | #336 ArgoCD Deployment And GitOps Cleanup | L1 child anchor |
| In Progress | #288 Deploy Enablement And Agent Access | L1/L10 enabling anchor |
| In Progress | #218 Data/Storage Durability And DB HA/PITR | L5 data reliability anchor |
| Ready | #75 Observability, Data Hygiene, And Product Health | L6 child anchor |
| In Progress | #287 Greenhouse Control Optimization | L2/L3/L7/L8 legacy umbrella |
| In Progress | #225 HA Resilience | L1/L5 reliability anchor |
| Ready | #13 Band And Compliance Rearchitecture | L3/L6 anchor |
| Ready | #14 Firmware Digital Twins | L10 anchor |
| Ready | #337 Decommission, Auth, And Residual Product Plane | L9/L1 residual anchor |
| Backlog | #16 Hardware And Seasonal Operations | L8 hardware anchor |
| Backlog | #332 Fable Workstream Clarification | Not in current greenhouse lanes |
| Ready | #330 Repo Cleanup And Branch Review | L1 hygiene anchor |
| Done | #333 Historical Completed Milestones | Historical evidence |

## Known Issue Anchors

| Lane | Existing issue evidence |
|---|---|
| L1 Architecture/CI/CD | #207, #335, #336, #339, #341, #342 |
| L2 Firmware core | #287, #289, #290, #292, #299, #300, #323, #324, #327, #340 |
| L3 Climate control | #13, #17, #20, #291, #292, #293, #323, #324, #328, #341 |
| L4 Planner/tunables | #214, #315, #293, #300 |
| L5 Data/schema/source of truth | #13, #14, #31, #207, #324, #327, #341 |
| L6 Observability/KPIs | #75, #89, #200, #241, #308, #328, #341 |
| L7 Lighting/occupancy | #118, #294, #295, #300, #341 |
| L8 Irrigation/fertilization | #16, #37, #45, #296, #297, #298 |
| L9 Lab/site/publishing | #43, #219, #308, #337 |
| L10 Testing/research | #14, #31, #303, #322, #335, #340 |

## Access Notes

Project fields are useful for views, but issue `## Project Tracking` blocks
remain the durable fallback. The 2026-06-16 replan added milestones G0-G3,
issues #343-#352, and project card metadata for each lane.
