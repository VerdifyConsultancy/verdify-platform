# GitHub reconciliation

For each lane verify:

- issue exists and remains in approved scope;
- issue is not assigned to another lane;
- branch matches the contract;
- pull request targets the intended base branch and links the issue;
- required checks reflect the current head SHA;
- critic decision refers to the current diff;
- labels/Project fields match the lifecycle state;
- merged/closed state does not overstate deployment verification.

Refresh the snapshot before acting on a discrepancy. Write a reconciliation report; never resolve disagreement by silently editing both sides.

When the discrepancy affects backlog strategy or future sprint candidates, also
consume or update the state-of-union
`.agent-workflow/strategy/github-backlog-sync.yaml` artifact so issue, PR, lane,
and delivery findings share one typed reconciliation record.
