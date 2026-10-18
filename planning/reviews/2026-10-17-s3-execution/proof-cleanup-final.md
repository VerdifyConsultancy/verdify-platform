# Final disposable proof cleanup

ROOT completed the exact nine-object cleanup at **2026-10-18T05:13:12.443536+00:00** in `vallery` / `verdify-db-rehearsal`: three Jobs, their three terminal Pods and three immutable ConfigMaps. Every entry completed Job → Pod → ConfigMap with UID and resourceVersion preconditions, `Orphan` propagation, a 30-second request expiry and named absence readback. Each object had one delete invocation; no uncertain result or automatic retry occurred.

The private `proof-disposable-cleanup-02/verified-summary.json` is bound by SHA-256 `a8d72c90b93df4cff0e1e1bacb8d9b882d19565d6b0d963e2931fd5865c05170`. The [machine-readable receipt](proof-cleanup-final.json) binds the exact per-stage request, response and absence evidence. The plan is `a14da07cd94b9fce876f8899cf29eea587ed7fc17c42d34c75707ba548de9f15`. All **535 unique retained evidence files** still match their pinned hashes after cleanup, including creation/release custody, full init and main logs, terminal receipts, typed captures and comparisons. Raw logs and Secret values are not published here.

| Entry | Kind | Deleted UID | ResourceVersion precondition |
| --- | --- | --- | --- |
| point | Job | `79ce94f3-9cb0-4429-b4b0-2e0e51164e90` | `281345137` |
| point | Pod | `ff426931-1fbc-4c72-a51c-839eb708e10b` | `281399390` |
| point | ConfigMap | `3f6e2006-4925-4d9b-8cd6-ab9692a52a17` | `281343301` |
| lifecycle-failed-01 | Job | `93a8fbda-90fb-422e-b804-9b3aea8f679f` | `281364021` |
| lifecycle-failed-01 | Pod | `1dd4804c-dcbc-47d3-8b6e-102a206c2fa5` | `281399449` |
| lifecycle-failed-01 | ConfigMap | `87cefa53-af85-42c7-9390-1c4c7f51fd78` | `281349012` |
| lifecycle-v2 | Job | `aa7b2a2c-b5d8-4ced-b765-6e025bf11a25` | `281391179` |
| lifecycle-v2 | Pod | `6401ce46-80bd-426c-98dd-696291abebd3` | `281399570` |
| lifecycle-v2 | ConfigMap | `465f2016-4de7-4118-921c-4e761f6e1d62` | `281390657` |

The histories remain distinct:

- **Point proof:** the four-call return/rollback proof succeeded; the complete typed comparison retained all 29 genuine and synthetic rowsets, with only the bundle-attempt sequence advancing by two.
- **Lifecycle attempt 01:** its original stale-resourceVersion release rejection remains failed. A separately authorized release of the same Job then failed in the harness before behavioral cases. The full logs and zero-effect comparison remain retained: all 29 genuine and synthetic rowsets and all 77 sequence states were unchanged. Cleanup does not relabel either failure as success.
- **Lifecycle v2 attempt 02:** the corrected harness succeeded in its bounded database authority/rollback cases. Its complete typed comparison retained all 29 genuine and synthetic rowsets, with only the event sequence advancing by two. This does not establish physical, locked/armed, ordinary-writer or full issue acceptance.

Cleanup changed no database rows, Secrets, PVCs or CNPG resources and performed no production promotion. The six labeled synthetic rows remain retained; the latest typed `after-04` capture is still `d8214d8b6e8df76640679a37e77715bd0f0103de3f2ad54f431c0792e594853b`. Its finite scope is unchanged.

This cleanup is separate from the [earlier fifteen-object cleanup](639-587-disposable-cleanup.md). Together they removed **24 disposable objects across eight unique Jobs**. The retained data and proof histories remain available; issues #639 and #587 are not closed by this cleanup receipt.
