# Repo Agent Scope Contract

Use this reference when repo hygiene, repo bootstrap, or discovery assigns a
controller or long-lived agent to a repository.

## Purpose

`repo-agent-scope.yaml` is the machine-readable charter for a repo-associated
agent. It defines what the agent owns, what it may inspect or change, which
records are authoritative, what credentials and runtime surfaces it may use, and
when it must escalate.

The artifact complements `AGENTS.md`. `AGENTS.md` gives local operating
instructions to humans and agents. `repo-agent-scope.yaml` gives the lifecycle
system a typed contract that controller loops, platform readiness, state of
union, sprint planning, and review can consume.

## Default Path

Use `.agent-workflow/hygiene/repo-agent-scope.yaml` for the first repo hygiene
or bootstrap pass. A controller loop may copy the approved scope into
`.agent-workflow/controller/` only when it records the source path and approval.

## Required Content

1. Repository identity: owner, name, full name, default branch, GitHub URL, and
   local path when known.
2. Scope: purpose, in-scope work, out-of-scope work, owned paths, protected
   paths, and stakeholders.
3. Ownership: primary owner, reviewers, upstream authorities, and downstream
   consumers.
4. Responsibilities: stable `RAS-*` records with lifecycle skill, acceptance
   signal, and evidence references.
5. Authority boundaries: allowed operations, prohibited operations, approval
   required for sensitive work, credential rules, and environment limits.
6. Discovery inputs: repo, GitHub, runtime, observability, credential-reference,
   storage, route, CI/CD, and project-definition evidence used to build the
   scope.
7. Runtime context: namespaces, storage mounts, routes, credential references,
   and observability references.
8. Escalation paths: trigger, owner, route, and whether the condition blocks
   further work.
9. Review, handoff, and approval.

## Completion Rules

- The artifact must validate against
  `../../schemas/repo-agent-scope.schema.yaml`.
- Raw secret values must never appear in the artifact.
- A repo agent must not claim operational ownership until scope, ownership,
  authority boundaries, and escalation paths are explicit enough for a reviewer
  to identify overreach.
- Missing runtime, credential, namespace, storage, or route evidence should be a
  gap or escalation, not an invented permission.
