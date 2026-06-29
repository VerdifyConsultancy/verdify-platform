# Session Ledger

Use this reference when controller state, child sessions, wave execution,
review, release, or handoff history must survive model context loss.

`session-ledger` is a promoted capability contract owned first by
`controller-loop`. It is not a standalone canonical lifecycle skill yet. The
ledger is an append-oriented YAML artifact that records session graph and
lifecycle-significant events while linking to authoritative GitHub and
`.agent-workflow` records instead of duplicating them.

## Inputs

- Controller state, route decisions, North Star artifacts, sprint plans, lane
  contracts, leases, prompt manifests, worker closeouts, critic reports, review
  packets, diagnostic packets, release evidence, outcome reviews, gates, and
  learning proposals.
- GitHub issue, PR, check, workflow run, deployment, commit, and review
  references.
- Agent Platform, MCP/API, terminal, CI/CD, deployment, trace, provenance, or
  transparency references when available.

## Procedure

1. Maintain `.agent-workflow/controller/session-ledger.yaml` as append-oriented
   state and validate it against `../../schemas/session-ledger.schema.yaml`.
2. Record each parent or child session with role, executor, repository, issue,
   lane, wave, branch, worktree, PR refs, artifact refs, status, start, and end.
3. Append one ledger event for every lifecycle-significant transition:
   routing, research ingest, planning update, sprint plan, wave release plan,
   lane dispatch, lease creation, prompt compilation, worker status, closeout,
   criticism, review packet, diagnostics, gate decision, CI observation,
   deployment observation, release verification, outcome review, handoff,
   session loss, exception, and learning proposal.
4. Use CloudEvents-like event fields: event ID, source, type, time, subject,
   actor, session ID, result, summary, and typed refs.
5. Preserve correlation IDs: trace, wave, session, issue, lane, PR,
   deployment, and policy decision.
6. Link authoritative artifact, evidence, and external refs rather than copying
   raw logs, secrets, or large payloads.
7. Record previous event ID and content SHA-256 when available. Use `null` only
   for the first manual contract or when the hash was not computed.
8. Record exceptions for missing ledger coverage and route them to an owner.

## Completeness Rules

Controller reconstruction is incomplete when:

- a child session exists without a ledger session record;
- a lifecycle transition changed state without a ledger event or exception;
- an event lacks artifact, evidence, or external refs needed to verify it;
- session, issue, lane, PR, deployment, or gate identities disagree with
  authoritative records;
- raw secret or private payload content was copied into the ledger.

## Stop Conditions

Stop and route to `controller-loop`, `sprint-orchestrator`,
`release-verification`, `platform-readiness`, or human review when:

- session identity or parent/child relationships are ambiguous;
- the next lifecycle step would rely on hidden chat history;
- ledger events contradict GitHub, leases, checks, deployments, or approved
  artifacts;
- a protected transition lacks an event, gate decision, or explicit exception.
