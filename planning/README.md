# Validated campaign source

Start with [CAMPAIGN.md](CAMPAIGN.md) for implementation strategy and [the roadmap](../PROJECT_BOARD.md) for the complete issue index. [EVIDENCE.md](EVIDENCE.md) explains the review findings and their limits.

The [October 5 greenhouse and acceptance review](reviews/2026-10-05/NEXT-SPRINT.md) supersedes the next-sprint pull set and adds current full-issue acceptance, health and native graph receipts.

The [September 26 production, issue, branch and worktree review](reviews/2026-09-26/NEXT-SPRINT.md) provides the next-sprint pull set and a full 80-issue inventory. The two later estate-rationalization issues, #801 and #802, are outside this 78-issue campaign source and are included in that review with their native cross-repository dependencies.

`backlog.yaml` is the v2 source of truth (JSON-compatible YAML): nine stages and every campaign issue's owner role, priority, effort, what, why, how, acceptance, actual source paths, blocking dependencies, parent, delivery, rollback and decision boundary. The old lane/story schema and issue bodies are preserved in [the archive](archive/2026-09-05/README.md); they are not a second active backlog.

```sh
python -m planning.render
make planning-validate
python planning/schema.py planning/backlog.yaml --published
```

The renderer updates the five root planning views, [DEPENDENCIES.md](DEPENDENCIES.md) and `issues_manifest.json`. `python -m planning.render --body-dir /path/to/scratch/bodies` emits exact GitHub issue bodies; it performs no network writes. Tests reject lost original issues, duplicate IDs, missing what/why/how/acceptance, unknown references, cycles, parent deadlocks, missing source paths and stale generated views.

For later GitHub synchronization, read the current issue metadata first, compare against the last receipt and preserve concurrent changes. Publish body/title/milestone/priority plus native blocked-by edges and native parentage, then read everything back against the manifest hashes. Keep closed history and unrelated assignments. Parentage is not a blocking gate; hard dependencies name bounded tasks, not an epic that contains the dependent.

The September 5 delivery adds ten gap issues to the 68 originally open issues. No inaccessible ProjectV2 board is claimed updated: the old project #5 did not resolve and the accessible organization project list was empty. GitHub issues, nine campaign milestones and native dependencies/sub-issues are the delivered tracker.
