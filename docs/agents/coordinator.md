# Coordinator (Jason)

Human-in-the-loop role. No Claude agent is the coordinator; this doc describes what Jason holds and why.

## Owns

- `verdify_schemas/` — all Pydantic models; cross-agent contract
- `db/migrations/**` — SQL migrations; serialized, reviewed holistically
- `docker-compose.yml`, `systemd/`, `traefik/`, `mqtt/`, `.github/workflows/` — infrastructure
- `CLAUDE.md`, `README.md`, `docs/agents/**` — organizational docs
- `pyproject.toml` — tool config, dependencies
- Every merge to `main` — agents work on feature branches; coordinator merges
- Production deploys — `sudo systemctl restart verdify-*` actions

## Responsibilities

1. **Merge discipline.** One migration at a time. Schema changes land before their consumers. Dependent work across agents is staged, not interleaved.
2. **Review queue.** All agent PRs get coordinator review before merge. Focus: contract breakage, migration safety, multi-tenant invariants.
3. **Scope kickoff.** New work starts from GitHub issues on
   `VerdifyConsultancy/verdify-platform`; the old `docs/backlog/{agent}.md`
   files are archived in `/Users/jason/Orbit/context_dump/verdify-platform/`.
4. **Cross-cutting work.** Anything that touches 2+ agents' scopes is
   scheduled from GitHub issues and reviewed by coordinator.
5. **Live-deploy authorization.** Restarting production services, force-pushing, force-merging — coordinator only.

## Decides

- When to add/remove/retire an agent
- Observability work routing (ephemeral vs. new persistent agent)
- Model swaps, dependency upgrades, security-driven rewrites
- Sprint numbering + ordering across agents
- Whether a proposed change is "an agent's scope" or "cross-cutting"

## Does not do (on purpose)

- Day-to-day feature work inside one agent's scope — that's the agent's
- Prompt engineering — that's `genai`
- Dashboard tuning — ephemeral work, dispatched
- Firmware physics — that's `firmware`

## Tools & workflow

- Runs agents from the main worktree (`/mnt/iris/verdify` on `main`) or via the TUI orchestrator
- Merges via `git -C /mnt/iris/verdify merge --ff-only {agent}/sprint-N-...`
- Deploy via `sudo systemctl restart verdify-{service}` after merge
- Reviews using agent-provided verification output + live journal tail

## Ask coordinator when

See every other agent's doc — they list this explicitly. Rule of thumb: if your change touches `verdify_schemas/`, `db/migrations/`, production config, or the wire between two agents — ask first.
