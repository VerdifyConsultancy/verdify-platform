# Verdify planning source

[The October 17 replan](reviews/2026-10-17-replan/README.md) is the current execution plan. It covers all 50 open issues, retains the seven earlier consolidation/no-build closures and completed #801, and replaces the prior sprint calendar with **October 19–30**. The current graph has 26 active completion edges plus 25 retained closed predecessor records.

Start with [the next sprint](reviews/2026-10-17-replan/NEXT-SPRINT.md), [execution handoff](reviews/2026-10-17-replan/EXECUTION.md), [all issue dispositions](reviews/2026-10-17-replan/ISSUE-TRIAGE.md) and [scope transfers](reviews/2026-10-17-replan/SCOPE-TRANSFERS.md). Earlier reviews and append-only operational ledgers are historical evidence, not competing current queues.

`backlog.yaml` is the JSON-compatible YAML source for campaign contracts, issue status, active sprint membership, priorities, ownership and dependencies. It preserves original campaign coverage, including closed history. Native blocked-by edges retain closed predecessor records; only open predecessors block current acceptance. Parentage is ownership, never a completion gate.

```sh
python -m planning.render
make planning-validate
python planning/schema.py planning/backlog.yaml --published
```

The renderer generates the root planning views, dependency view and `issues_manifest.json`. `python -m planning.render --body-dir /path/to/bodies` emits exact issue bodies without writing to GitHub. Before publication compare current bodies and metadata with the before-image; preserve concurrent edits and existing assignees. Publish only reviewed differences, then verify body hashes, priorities, milestones and native relationships by readback.

The complete inventory, original open bodies, reviewed assessments, graph and metadata-only runtime snapshot are retained in the current review directory. No ProjectV2 board, production deployment or physical/scientific acceptance is implied by a planning update.
