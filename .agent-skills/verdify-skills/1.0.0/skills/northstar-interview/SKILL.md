---
name: northstar-interview
description: >-
  Reviews current North Star product and architecture artifacts, registered
  evidence, review packets, gates, and prior questions to produce a prioritized
  interview/Q&A packet with proposed decisions, architecture options, tradeoffs,
  and answer capture rules. Use when Codex needs to question Jason, James, or
  other reviewers before locking or revising a North Star, after new evidence is
  ingested, when priorities are unclear, or when a planning loop needs
  interview-ready questions rather than immediate artifact edits.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "2c"
---

# North Star Interview

Produce a focused interview packet that helps humans refine a North Star before
final lock approval. This skill does not approve the North Star, start
implementation, or rewrite protected artifacts from inferred answers.

## Canonical outputs

- `.agent-workflow/northstar/NORTHSTAR_INTERVIEW.md` - human-facing interview
  packet with findings, proposed priorities, decisions, tradeoffs, questions,
  and answer capture rules.
- Proposed updates to `NORTHSTAR_PRODUCT.md`, `NORTHSTAR_ARCHITECTURE.md`, and
  `northstar-artifacts.yaml` only when the user explicitly asks to apply
  accepted answers.

## Procedure

1. Read `../../COMMON_OPERATING_CONTRACT.md`.
2. Read `NORTHSTAR_PRODUCT.md`, `NORTHSTAR_ARCHITECTURE.md`,
   `northstar-artifacts.yaml`, `northstar-plan.yaml`, `REVIEW_PLAN.md`,
   `.agent-workflow/gates/northstar.yaml`, and
   `.agent-workflow/northstar/evidence-registry.yaml` when present.
3. Read the latest relevant evidence items, especially newly ingested research
   or transcript records.
4. Review product/service scope, personas, user stories, requirements,
   architecture requirements, interfaces, delivery model, gates, readiness,
   observability, security, and traceability.
5. Identify material decisions, conflicts, weak evidence, missing priorities,
   and architecture tradeoffs. Label each as `must-decide`, `should-decide`,
   `can-defer`, or `research-needed`.
6. Produce or update `NORTHSTAR_INTERVIEW.md` from
   `assets/NORTHSTAR_INTERVIEW.template.md`.
7. Sort questions by interview priority:
   - `P0`: needed before final lock approval.
   - `P1`: needed before downstream architecture/contracts or platform pilot.
   - `P2`: useful for backlog, optimization, or later roadmap clarity.
8. For each question include: context, why it matters, proposed default,
   options, tradeoffs, affected North Star IDs, evidence references, and answer
   shape.
9. Keep ordinary questions nonblocking inside the planning loop. Only the final
   North Star lock approval is a gate for advancing to the next milestone.
10. After the human answers, preserve the answers as feedback/evidence and route
    back to `northstar-planning` / `review-feedback` or `artifact-loop`.

## Question standards

Questions should be specific enough for a human to answer quickly but rich
enough to expose priority, tradeoff, or ownership. Avoid generic discovery
questions when the repo already contains a proposed default.

Good interview questions ask the human to choose among explicit options, accept
or reject a proposed default, rank competing outcomes, or identify a missing
constraint.

## Stop conditions

Stop before recording approval, changing protected North Star content, or
turning an inferred answer into a committed requirement. If the interview
reveals unsafe access, production mutation, raw secrets, or destructive changes,
record a gate or protected decision instead.

## Load references only when needed

- Read `references/question-rubric.md` when drafting or reviewing an interview
  packet.
