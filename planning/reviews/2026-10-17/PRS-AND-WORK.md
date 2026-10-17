# Product PR and work disposition — October 17, 2026

All six open product PRs were checked against actual main2208401 and their exact current heads. A source-equivalent closure is not an issue/runtime acceptance or a merge. Branches and failed receipts are retained.

| PR | Disposition | Evidence / remaining owner |
|---|---|---|
|971|Close without merge as superseded|13/14 changed-file blobs exact at main, including producer/consumer/tests/runbook. Only later additive CI differs. Incorporated through973;963 remains open for actual firmware/HA acceptance.|
|970|Close without merge as superseded|6/8 blobs exact, including API/test/runbook/history. Other differences are later service-map/CI additions;965 already accepted.|
|969|Close without merge as superseded|16/19 blobs exact including implementation/manifests. Later regression/receipt/CI additions preserve its work. Incorporated through973; never merge/build independently.396 is complete;245 remains separate.|
|968|Close without merge as superseded|27/29 immutable evidence blobs exact. Only obsolete backlog/generated-manifest snapshots differ; current planning supersedes those snapshots.|
|774|Close without merge as superseded|Both capture-tool and test blobs exact at main; do not maintain a competing source PR. This is no new physical current-state qualification.|
|855|Keep draft / parked|All four activation files differ from current main. Historical placeholders/source/lease assumptions need a fresh exact packet. No merge, activation or physical approval from this review.|

#367's isolated firmware dwell correction remains real unmerged work with retained red/green/native/replay receipts; it is not part of source220 or the current runtime. Review it only within the next selected firmware release scope. Source presence and actual controller efficacy remain separate.

Agents4977 is the concrete external749 delivery step: native source check succeeds at head6edbc32, while the PR remains open. Source qualification does not prove owner images/adoption, candidate publication or product runtime. Reuse the existing owner, automatic image route and qualified product receipt set; do not create a second submission or hand-edit candidate pins.
