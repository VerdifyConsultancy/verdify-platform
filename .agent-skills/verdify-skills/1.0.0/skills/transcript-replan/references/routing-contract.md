# Transcript Routing Contract

## Item taxonomy

Use these categories:

- `decision`: a committed or proposed governance/product/architecture decision.
- `direction`: a strong but not yet approved preference.
- `requirement`: measurable behavior or constraint.
- `user_story`: actor, need, and acceptance signal.
- `architecture_input`: topology, interface, data, infrastructure, security, or
  quality attribute input for `architecture-contracts`.
- `idea`: exploratory backlog hypothesis.
- `question`: unresolved decision or missing evidence.
- `conflict`: contradiction with current artifacts or prior decisions.
- `memory`: personal or planning context that should not become a requirement by
  itself.

## Evidence status

Use `reported` for transcript extractions unless the original recording,
repository state, or GitHub state was independently checked. Use `verified` only
for directly inspected artifacts or live system evidence.

## Routing fields

Every material item needs:

- stable local ID;
- source quote or paraphrase;
- evidence status;
- target repository or `multi_repo`;
- lifecycle phase;
- affected skill or lane;
- proposed next action;
- protected-artifact impact.

## Conflict handling

Do not resolve conflicts by preference. Record:

- existing authority;
- transcript proposal;
- why both cannot hold as stated;
- owner required to decide;
- blocking status;
- safest next skill.

## Issue and gate recommendations

Recommend GitHub Issues for backlog work. Recommend gates for approval,
security, production, destructive operations, protected North Star edits, or
Gravity readiness. Do not create issues unless the user or repository policy
allows it.
