# Validated campaign source

Start with [CAMPAIGN.md](CAMPAIGN.md) for implementation strategy and [the roadmap](../PROJECT_BOARD.md) for the complete issue index. [EVIDENCE.md](EVIDENCE.md) explains the review findings and their limits.

The [October 11 acceptance review and S2 completion sprint](reviews/2026-10-11/NEXT-SPRINT.md) is the latest full review. It supersedes the October 6 runtime snapshot: production promotion ultimately failed, #949 has regressed, #49 is accepted, and seven open sprint issues have 46–88 remaining engineering hours. [Status against every original stage](reviews/2026-10-11/STATUS.md), [complete clause matrix](reviews/2026-10-11/ISSUE-TRIAGE.md), and [fresh-session goal](reviews/2026-10-11/GOAL.md) retain the full C0–C8 scope. S1's historical acceptances remain dated evidence; the reopened convergence issue must be qualified again.

The [October 6 review](reviews/2026-10-06/NEXT-SPRINT.md) remains historical evidence of the earlier healthy snapshot, not current deployment acceptance.

The [October11 Iris execution receipt](execution/2026-10-11/IRIS-953.md) seals #953's first post-rollout and current natural-cycle acceptance. It updates that bounded issue after the full review; the remaining S2 and C0–C8 requirements stay open.

The historical [October 5 greenhouse and acceptance review](reviews/2026-10-05/NEXT-SPRINT.md) records the previous S1 pull set and its dated full-issue acceptance, health and native graph receipts.

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
