# ADR-0005: Add North Star planning loop

- Status: accepted
- Date: 2026-06-23

## Context

`transcript-replan` routes raw conversational evidence, and
`northstar-research-ingest` registers research as typed evidence. Those inputs
are not yet enough for project definition, architecture, or sprint planning. The
North Star evidence requires a loop that can combine transcripts, registered
research, ideation, requirements, adversarial findings, user stories, risks,
milestones, open questions, review feedback, and follow-up research into one
structured planning artifact.

## Decision

Add `northstar-planning` as a first-class synthesis skill after
`transcript-replan` and `northstar-research-ingest`, before
`project-definition`.

The initial structured synthesis artifact is:

- `.agent-workflow/northstar/northstar-plan.yaml`

`ADR-0007` extends this into the signed-off product and architecture artifacts:

- `.agent-workflow/northstar/NORTHSTAR_PRODUCT.md`
- `.agent-workflow/northstar/NORTHSTAR_ARCHITECTURE.md`
- `.agent-workflow/northstar/northstar-artifacts.yaml`

The artifact records goals, requirements, user stories, architecture
principles, milestones, risks, adversarial findings, open questions, conflicts,
traceability, proposed artifact changes, issue recommendations, research
recommendations, review feedback, and one handoff.

Ordinary planning questions do not open a human gate. The loop should keep
distilling evidence, proposing research, answering questions, proposing design,
and incorporating feedback until the artifacts are ready for review. Final
human approval is required only when locking the North Star and proceeding to
the next milestone.

## Consequences

- Routed transcript evidence and registered research now flow to
  `northstar-planning` before project definition when a North Star plan is
  missing or draft.
- Project definition and architecture consume a structured planning artifact
  rather than raw ideation.
- Protected North Star edits remain proposed until the final lock approval rule
  is satisfied.
- The package now exposes seventeen canonical skills after `ADR-0008` adds
  `northstar-interview`.
