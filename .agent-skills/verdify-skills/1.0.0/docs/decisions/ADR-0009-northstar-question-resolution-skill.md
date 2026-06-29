# ADR-0009: Add North Star question resolution skill

- Status: accepted
- Date: 2026-06-24

## Context

North Star planning and interview artifacts can expose many open questions. The
Gravity documentation corpus demonstrates the scaling problem: hundreds of
`[QUESTION:*]` markers should not become hundreds of human gates. Most questions
can be answered by grouping them into thematic decisions, researching missing
evidence, selecting delegated defaults, and preserving only the few unresolved
human judgments.

`northstar-planning` owns artifact synthesis, and `northstar-interview` owns a
targeted human Q&A packet. Neither should become a large-corpus question
inventory and research engine.

## Decision

Add `northstar-question-resolution` as a first-class North Star support skill
after `northstar-interview`.

The skill:

- inventories questions across docs, issue exports, interview packets, and
  `.agent-workflow` artifacts;
- clusters questions by shared thematic or architecture decision;
- uses registered evidence first and external research when evidence is thin;
- documents Brave Search credential handling without exposing raw keys;
- writes research notes suitable for `northstar-research-ingest`;
- records delegated answers with options, tradeoffs, confidence, evidence refs,
  affected artifacts, and protected-decision status;
- hands evidence-backed resolutions back to `northstar-planning`.

It does not replace final North Star lock approval, approve protected decisions,
or bypass risk gates for public APIs, schemas, storage, security, destructive
operations, external dependencies, or deployment risk.

## Consequences

- The package now exposes eighteen canonical skills.
- Large question sets can converge without overwhelming human reviewers.
- Human attention shifts from raw question triage to short thematic escalation
  packs.
- `northstar-planning` stays responsible for updating product and architecture
  artifacts after resolutions are registered.
