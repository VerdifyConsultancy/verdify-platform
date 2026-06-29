# North Star Planning Contract

## Required planning records

Create stable IDs:

- `NSG-*` for goals.
- `NSR-*` for requirements.
- `NSS-*` for user stories.
- `NSP-*` for architecture principles.
- `NSM-*` for milestones.
- `NSQ-*` for open questions.
- `NSK-*` for risks.

Every record must include source IDs and at least one next action. If a record
cannot be traced to evidence, keep it as an idea or question.

The signed-off planning authority lives in `NORTHSTAR_PRODUCT.md`,
`NORTHSTAR_ARCHITECTURE.md`, and `northstar-artifacts.yaml`. Keep
`northstar-plan.yaml` as a structured synthesis/index, not as a replacement for
the paired product and architecture artifacts.

## Product and architecture separation

Product records describe who, what, why, and acceptance. Architecture records
describe constraints, principles, interfaces, quality attributes, and decision
areas. Architecture records must link back to one or more product, operations,
security, delivery, or cost purposes.

## Adversarial review

For material plans, include findings from at least these perspectives when
relevant:

- end user;
- administrator/operator;
- historian/auditor;
- architect;
- SRE;
- scalability and performance;
- cost/TCO and finance;
- security/privacy.

Each finding needs a disposition: `accepted`, `rejected`, `deferred`,
`needs_research`, or `final_lock_required`. Use `needs_research` for ordinary
uncertainty; reserve `final_lock_required` for the review point that locks the
North Star for the next milestone.

## Milestones and waves

Milestones describe outcomes. Waves describe bounded execution slices with a
CI/CD evidence and deployment path when implementation or environment work is in
scope. Do not use a milestone or wave to bypass the current one
issue/lane/branch/worktree default unless a branch-model decision has approved
that change.

## Handoff rules

- Missing or contradictory product intent -> `project-definition`.
- Architecture decision areas ready but unapproved -> `architecture-contracts`.
- Backlog sequencing needed -> `state-of-union`.
- Repo compliance needed -> `repo-hygiene`.
- Platform or environment gate needed -> `platform-readiness`.
- Gravity pilot gate needed -> `gravity-readiness`.

Name exactly one next skill and mode.
