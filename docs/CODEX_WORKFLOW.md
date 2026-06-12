# Codex Workflow Prompts

Use these prompts to start or steer Codex sessions in this repo. They are
repo-specific wrappers around the operating protocol in `AGENTS.md`.

## Wake / Orientation

```text
Wake up in /Users/jason/repos/verdify-platform. Read AGENTS.md,
docs/AGENT_STATE.md, README.md, GOAL.md, docs/runbooks/laptop-operator.md,
Makefile, pyproject.toml, and .github/workflows/ci.yml. Inspect git status,
recent history, available manifests, and relevant docs/agents references.
Report branch/worktree state, access assumptions, current goal, affected
subsystems, safety gates, and the verification plan before editing.
```

## Planning

```text
Plan the requested Verdify change before editing. Use repo files, GitHub issues
if needed, Makefile, CI workflows, and the relevant docs/agents subsystem note.
Identify the smallest safe implementation path, safety gates, files likely to
change, verification commands in order, and any Jason-gated action that must not
be performed by the agent.
```

## Goal Mode

```text
Create or continue a Codex goal for this Verdify task. Keep working until the
repo-file objective is complete, blocked by a real gate, or verification proves
the change. Update docs/AGENT_STATE.md before the final response with changed
state, commands run, residual risks, and the next prompt.
```

## End-Of-Session Handoff

```text
End this Verdify session with a durable handoff. Update docs/AGENT_STATE.md with
what changed, which commands passed or failed, what remains unverified, known
risks/blockers, and the exact next recommended Codex prompt. Then summarize the
changed files and git status.
```

## Optional Reconnaissance

Use this only for large repo areas. The main agent remains responsible for
reading selected instructions and making final decisions.

```text
Reconnoiter the <area> subsystem only. Read AGENTS.md, docs/AGENT_STATE.md, and
the relevant docs/agents/<area>.md. Inspect entrypoints, tests, manifests, and
CI gates for that area. Return concise findings with file references, risks, and
recommended verification. Do not edit files or run production/device-affecting
commands.
```
