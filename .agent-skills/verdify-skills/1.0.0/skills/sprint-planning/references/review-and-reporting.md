# Review And Reporting Plan

Use this reference while filling `sprint-plan.yaml.review_plan` and the final
plan approval summary.

## Required fields

Every approved sprint must answer:

- What's in: the issue-backed scope required for the sprint goal.
- What's deferred: nearby valid work intentionally left out.
- Lanes: lane ID, issue IDs, owner, reviewer, branch, contract path, and a short
  responsibility summary.
- Dependency order: parallel groups and serial blockers.
- QA milestones: milestone ID, name, due/trigger, and expected evidence.
- Human review milestones: owner, trigger, review packet path, and expected
  handoff.
- User stories for review: user-facing story, issue IDs, lane IDs, and sprint
  acceptance refs.

## Ownership rules

Use issue assignees, module owners, lane contract ownership, or the approved
strategy to name owners. Use role names such as `worker`, `critic`,
`release-verifier`, or `human-reviewer` when named people are unavailable. Do
not leave ownership implicit in prose.

## Review milestones

The first human review milestone should normally trigger after:

- worker closeout exists for required lanes;
- independent critic approval covers the current head SHA;
- required checks are observed;
- preview/review deployment is active or explicitly not applicable;
- review inbox packet is complete enough for a human to test without hidden
  chat context.

Deployment-impacting work also needs the wave release plan to name review packet
path, human test steps, release-health signals, and rollback evidence.

## Summary discipline

The final sprint-planning answer must summarize the approved or approval-ready
artifact. It must not add untracked scope, hidden follow-up tasks, or private
owner assignments that are absent from the sprint plan, lane contracts, GitHub,
or gates.
