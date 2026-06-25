# Industry alignment

Reviewed 2026-06-22 against primary sources.

## Agent Skills

The Agent Skills specification defines a skill as a directory containing `SKILL.md` with optional `scripts/`, `references/`, and `assets/`. It recommends progressive disclosure, keeping the main instructions below 500 lines and approximately 5,000 tokens, and using focused references only when needed.

- https://agentskills.io/specification
- https://agentskills.io/skill-creation/best-practices

Verdify therefore exposes coherent lifecycle skills instead of 17 tiny skills or
one monolith, keeps each `SKILL.md` concise, and moves detailed procedures to
focused references. The original delivery set contained nine skills; readiness,
North Star planning, research-ingest, and North Star interview loops now extend the package to seventeen
while preserving progressive disclosure. Standalone helper skills can be
packaged alongside the lifecycle when they serve GitHub-native work without
changing the lifecycle graph.

## GitHub backlog and delivery primitives

GitHub documents Issues as a flexible system for planning and tracking work, with native sub-issues, issue dependencies, labels, milestones, Projects, and PR linking. Protected branches/rulesets can require checks and reviews. Merge queues validate changes against a busy target branch. Environments provide deployment protection and history.

- https://docs.github.com/en/issues/tracking-your-work-with-issues/learning-about-issues/about-issues
- https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues
- https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments

Verdify uses those primitives directly instead of rebuilding a parallel backlog or merge system.

## Git worktrees

Git's official worktree documentation defines linked worktrees with independent `HEAD` and index state and supports `add`, `list`, `lock`, `remove`, `repair`, and `prune`.

- https://git-scm.com/docs/git-worktree

Verdify adds a role/session lease and runtime namespace layer around these native commands; the worktree remains disposable Git infrastructure.
