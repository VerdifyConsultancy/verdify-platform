# North Star Interview Question Rubric

Use this rubric when drafting `.agent-workflow/northstar/NORTHSTAR_INTERVIEW.md`.

## Priority

- `P0`: The answer changes whether the North Star can lock for the next
  milestone, what the product/service is, what humans approve, or which
  architecture path is safe.
- `P1`: The answer is needed before architecture contracts, platform readiness,
  controller implementation, release architecture, or a non-Gravity pilot.
- `P2`: The answer improves roadmap clarity, measurement, later optimization, or
  backlog quality but can safely wait.

## Categories

- Product/service boundary: what the skills repo owns versus Agent Platform,
  Gravity, GitHub, Backstage, CI/CD, or cluster services.
- Human governance: final lock approval, risk-based review, exception handling,
  and protected changes.
- Controller architecture: deterministic workflow engine, state persistence,
  retries, cancellation, and agent invocation boundaries.
- Lane/task/wave model: issue, branch, worktree, task, PR, wave, preview,
  milestone, and traceability cardinality.
- Review-ready definition: CI, deployed review URL, immutable artifact,
  provenance, test steps, observability, rollback, and known risks.
- Security and platform: RBAC, secret brokering, runtime identity, network
  policy, terminal access, production diagnostics, and data access.
- Product experience: review inbox, interview/Q&A UX, status pages, session
  ledger, diagnostics, and installation surfaces.
- Evidence and supply chain: SBOM, SLSA provenance, policy-as-code, signed
  attestations, and release records.

## Question shape

Each question should include:

- ID and priority.
- The human decision requested.
- Context from the current North Star and evidence.
- Proposed default.
- Options with tradeoffs.
- Affected product and architecture IDs.
- Evidence references.
- Answer format, for example `choose one`, `rank`, `approve default`, or
  `freeform constraint`.

## Review rules

- Do not ask humans to restate facts already decided by evidence.
- Do not hide your proposed default. Make it easy to accept, reject, or modify.
- Keep nonblocking questions inside the planning loop.
- Record final lock approval separately from interview answers.
