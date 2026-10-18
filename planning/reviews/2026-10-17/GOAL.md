# Execution handoff — October 17, 2026

Finish #749 first using the existing writer session `01a12b3b-f57c-7ab0-bdcc-3cdba80e82c3`. Source `67f08ccf`, image `249c7647`, release `f91602f6` are deployed and the corrected wait/resumption behavior is verified. Four ordinary snapshot waits defeated the finite 30-minute quiet candidate. Final SQL/readiness was never attempted; do not restart that observation repeatedly.

The bounded offline producer-coherence work uses the existing publisher task and pool: one frozen batch for snapshot/confirmation, fanout and acknowledgement after commit, content/generation coalescing and a bounded request before eligible writer admission. The common reader stays read-only and samples current state after SQL. Preserve strict freshness, equality, generation, full-48, deadlines, authority and history. Exact diff, meaningful focused tests and resource/inverse review precede any genuine delivery; no extra physical-policy run or borrowed approval.

The canonical checkout is `/Users/jason/.codex/worktrees/verdify-live-replan-20261017/verdify-platform`, branch `codex/verdify-live-replan-20261017`. Preserve the original dirty checkout. Never merge the whole planning branch or trigger a planning-only product push. A future genuine source delivery needs a fresh complete canonical manifest.

The full review covered 58 issues and 624 comments: seven are retired as not planned (#296/#317/#324/#361/#586/#606/#638), 51 remain open, all bodies are readback exact, and the native graph has 31 active plus 25 accepted historical edges without cycles. S2's actual open member is #749; S3 has eight bounded members.

Existing Agents owner `01a135dc-ccc3-7473-be5e-5b643a40a3fd` holds shared CI/controller custody. Obtain an actual handoff before new product CI; observation does not retain that window. Preserve PR #982 and the intentional NAS-window exporter pause merged in #983 as183b. Do not create a reader, change credentials, unpause or scan NFS to obtain a readiness pass.

Capacity is 40 shared hours/week, 80/fortnight. S3 base is 28–56 hours plus 24 hours reserve, including #749. Estimates and runtime do not measure hours worked. Keep GitHub and the plan current, and interrupt only for a concrete decision, real blocker or meaningful completion.

[Status](STATUS.md) · [Triage](FULL-TRIAGE.md) · [Dispositions](ISSUE-TRIAGE.md) · [Graph](DEPENDENCIES.md) · [Execution queue](EXECUTION-QUEUE.md) · [Sprint](NEXT-SPRINT.md)


Current desired-state update: At23:44:32UTC, PR983 is merged as183b6f2b, changing only the exporter desired replica count to0. Live exporter remains desired1/Ready1; Argo is Healthy/OutOfSync against183b, last successful operationf916, with no operation pending. Ingestor remains one Ready replica at image249c and generation139. This is intentional pending owner adoption, not a failed writer rollout; do not silently apply or reverse it for readiness. Current backup proof remainsUNKNOWN.


October17 execution release: Jason instructed starting implementation and delivery. Pull #749 first, #801 next in the same product lane; the existing Agents owner has acknowledged #802 preparation after its current qualification. Root has begun #981 source diagnosis. Use the active execution queue for actual status; no new sessions, duplicated CI or re-triage. The eight S3 packets and28–56h plus24h reserve remain within80h.


Execution update: the corrected #749 producer-coherence source packet is accepted after60 focused passes and independent verification of the exact original confirmation SQL, frozen transaction and post-commit acknowledgement. The initial pre-publication SQL-selection defect is corrected and retained as review history. The existing product session is released for genuine canonical composition and normal delivery after the actual shared-owner window; no new images or runtime acceptance are claimed.
