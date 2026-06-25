# Resolution Register Rules

Use `assets/resolution-register.template.yaml` for
`.agent-workflow/northstar/question-resolution/<run-id>/resolution-register.yaml`.

## Status values

- `inventory`: questions have been discovered but not clustered.
- `researching`: at least one cluster needs evidence collection.
- `answering`: evidence exists and delegated answers are being recorded.
- `planning-handoff`: the register is ready for `northstar-planning`.
- `blocked`: the run cannot continue without human input or unsafe access.

## Cluster rules

Group questions by the decision they need, not by file alone. A cluster should
usually answer many raw questions. Split a cluster when the evidence, affected
artifact, or protected-decision class differs materially.

Each cluster should include:

- `theme`: stable short slug.
- `decision_class`: one of `product`, `architecture`, `schema`, `storage`,
  `security`, `delivery`, `operations`, `platform`, `research`, `governance`, or
  `other`.
- `protected_decision`: true when the decision may need a human gate.
- `question_ids`: raw question IDs covered by the cluster.
- `research_queries`: Brave or internal evidence queries.
- `evidence_refs`: `northstar://evidence/<id>` refs and local source paths.
- `options`: candidate answers with tradeoffs.
- `selected_answer`: the delegated answer or proposed human decision.
- `confidence`: `high`, `medium`, or `low`.
- `human_escalation`: whether a human still must decide.

## Answer rules

- `high` confidence requires clear evidence, local architecture fit, and no
  unresolved protected gate.
- `medium` confidence is acceptable for backlog defaults, research queues, or
  reversible architecture choices.
- `low` confidence should normally become research-needed or human-escalation.
- A protected decision can still have a recommended default, but it must not be
  marked as finally approved unless the configured gate is resolved.

## Handoff rules

When the register reaches `planning-handoff`, run or request
`northstar-planning` in `artifact-loop` or `review-feedback` mode. Planning
should resolve the corresponding `NSQ-*`/`NQI-*` items, update traceability, and
keep final lock approval separate.
