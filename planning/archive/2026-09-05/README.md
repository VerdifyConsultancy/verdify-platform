# Historical planning snapshot — not execution instructions

`prior-source/` preserves ten planning files byte-for-byte as they appeared at main `963ea818aad09b02259509cffa6bfdafb48d1702` before the September 5 rewrite. Python files have a `.txt` suffix to prevent accidental execution/test collection; `source-manifest.json` binds original paths and content hashes. `issues.json` preserves the 68 open issue titles, bodies, labels, milestones, assignees and update timestamps. `graph.json` preserves the former native blocking and sub-issue relationships.

These files intentionally contain obsolete dates, priorities, assumptions, paths and superseded instructions. The active plan is [../../backlog.yaml](../../backlog.yaml) and [../../CAMPAIGN.md](../../CAMPAIGN.md). Closed issue history and existing comments were not edited.

Rollback is a reviewed restoration of exact affected metadata from this snapshot, not a blind replay: compare current issue bodies/labels/milestones/parents first so concurrent user changes are preserved. Newly created gap issues should be retained or closed with a supersession link, not deleted. Source rollback uses a normal revert of the planning commit; it does not restore stale runtime pins or mutate production.
