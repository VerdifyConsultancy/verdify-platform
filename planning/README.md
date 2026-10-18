# Verdify planning source

[The S3 execution review](reviews/2026-10-17-s3-execution/README.md) is the current incremental execution record. Accepted #977 and #980 leave 48 open issues and 27 active completion edges, including Home Assistant #878 → #981, plus 25 retained closed predecessor records. A complete native graph readback matches source. Six open S3 allocations retain original estimates of 25–50 hours; these are not spent or remaining-time measurements. Actual-role behavior and owner adoption remain in progress; sprint completion is not claimed.

Start with [current outcomes and next actions](reviews/2026-10-17-s3-execution/README.md) and the [current graph](reviews/2026-10-17-s3-execution/DEPENDENCIES.md). The [October 17 triage](reviews/2026-10-17-replan/README.md), its original October 19–30 sprint allocation, [all issue dispositions](reviews/2026-10-17-replan/ISSUE-TRIAGE.md) and [scope transfers](reviews/2026-10-17-replan/SCOPE-TRANSFERS.md) remain frozen historical evidence.

`backlog.yaml` is the JSON-compatible YAML source for campaign contracts, issue status, active sprint membership, priorities, ownership and dependencies. It preserves original campaign coverage, including closed history. `accepted` defaults to false and may be true only for closed work with explicit scoped acceptance evidence; closed retired/superseded records do not gain checked acceptance boxes. Native blocked-by edges retain closed predecessor records; only open predecessors block current acceptance. Parentage is ownership, never a completion gate.

```sh
python -m planning.render
make planning-validate
python planning/schema.py planning/backlog.yaml --published
```

The renderer generates the root planning views, dependency view and `issues_manifest.json`. `python -m planning.render --body-dir /path/to/bodies` emits exact issue bodies without writing to GitHub. Before publication compare current bodies and metadata with the before-image; preserve concurrent edits and existing assignees. Publish only reviewed differences, then verify body hashes, priorities, milestones and native relationships by readback.

The complete triage inventory, original open bodies, reviewed assessments and original runtime snapshot remain in the frozen October 17 review. Later sanitized execution evidence is retained in the current S3 review directory. No ProjectV2 board, production deployment or physical/scientific acceptance is implied by a planning update.
