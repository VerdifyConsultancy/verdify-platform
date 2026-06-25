# ADR-0008: Add North Star interview skill

- Status: accepted
- Date: 2026-06-23

## Context

Registered North Star evidence now includes a broader end-to-end SDLC service
model. The existing `northstar-planning` skill can synthesize evidence into
product and architecture artifacts, but final review needs a repeatable way to
ask humans targeted questions about priorities, boundaries, proposed defaults,
architecture options, and tradeoffs before lock approval.

## Decision

Add `northstar-interview` as a first-class skill after `northstar-planning`.

The skill:

- reviews the current product and architecture North Star artifacts;
- reads the evidence registry, review plan, loop record, and open North Star
  gate;
- produces `.agent-workflow/northstar/NORTHSTAR_INTERVIEW.md`;
- prioritizes questions as `P0`, `P1`, or `P2`;
- includes proposed defaults, options, tradeoffs, affected IDs, evidence
  references, and answer-capture rules;
- routes answers back to `northstar-planning` / `review-feedback` or
  `artifact-loop`.

It does not mark final approval, rewrite protected artifacts from inferred
answers, or start implementation.

## Consequences

- At the time of this ADR, the package exposed seventeen canonical skills.
  `ADR-0009` later added `northstar-question-resolution`.
- Human review becomes easier to conduct because questions are concrete,
  prioritized, and evidence-linked.
- The final North Star lock gate remains separate from interview answers.
- The skills repository can preserve a Q&A planning loop without turning every
  ordinary question into a gate.
