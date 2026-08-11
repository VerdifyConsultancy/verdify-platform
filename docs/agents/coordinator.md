# Direct repository work

This filename is retained for old links. Repository agents work directly from
the user's request; there is no coordinator role or separate process owner.

## Shared surfaces

- `verdify_schemas/` defines cross-component contracts.
- `db/migrations/**` is serialized and validated against a disposable database.
- `docker-compose.yml`, `systemd/`, `traefik/`, `mqtt/`, and
  `.github/workflows/` define infrastructure and delivery behavior.
- `CLAUDE.md`, `AGENTS.md`, `README.md`, and `docs/agents/**` describe current
  repository behavior.

## Execution rules

1. Implement the requested change directly, including cross-component edits
   needed to keep contracts consistent.
2. Land schema and migration changes before or with their consumers; never
   leave an incompatible intermediate state.
3. Run the relevant focused checks plus the repository checks in `CLAUDE.md`.
4. For production changes, preserve GitOps ownership, record the exact target,
   verify backup or rollback readiness, and verify the post-change state.
5. Keep secrets out of source and command output.

GitHub issues and repository docs are technical context, not permission gates.
