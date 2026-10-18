# #639 / #587 — completed disposable qualification cleanup

ROOT completed the reviewed cleanup at **2026-10-18T04:38:32Z**. Exactly **15 objects** across five completed attempts are confirmed absent in `verdify-db-rehearsal`: one Job, its terminal Pod and its immutable ConfigMap for each attempt. This housekeeping does not establish remaining #639/#587 behavioral or alert acceptance.

| Completed attempt | Job | Pod | ConfigMap |
|---|---|---|---|
| auth-v1 | `verdify-s3-role-auth-primary2-20261018` | `verdify-s3-role-auth-primary2-20261018-rtb2f` | `verdify-s3-role-auth-primary2-20261018` |
| network | `verdify-s3-role-network-primary2-20261018` | `verdify-s3-role-network-primary2-20261018-vmf8b` | `verdify-s3-role-network-primary2-20261018` |
| auth-v2 | `verdify-s3-role-auth-primary2-v2-20261018` | `verdify-s3-role-auth-primary2-v2-20261018-5zbnr` | `verdify-s3-role-auth-primary2-v2-20261018` |
| behavior-v2 | `s3-experiment-roles-v2-20261018` | `s3-experiment-roles-v2-20261018-tp4dw` | `s3-experiment-roles-v2-20261018` |
| preview-c5 | `verdify-s3-alert-preview-c5-20261018` | `verdify-s3-alert-preview-c5-20261018-64xn5` | `verdify-s3-alert-preview-c5-20261018` |

Each exact UID/resourceVersion-bound DELETE used Orphan propagation. The Job was removed first; its terminal Pod and complete logs were reverified before Pod deletion; the immutable ConfigMap was removed only after both were absent. Every stage retained its presealed inputs, actual API response and named absence readback. All 15 stages completed without uncertain outcomes or mutation retries.

All **57 original evidence/log files** remain present with matching hashes and lengths. Failed auth-v1 and preview-c5 evidence is preserved alongside the successful network/auth-v2/behavior-v2 records. No raw logs or credentials are published here.

The [structured receipt](639-587-disposable-cleanup.json) records all 15 exact UIDs, resourceVersion preconditions, per-stage receipt hashes and 57 retained file hashes. It binds the actual private summary `disposable-cleanup-01/verified-summary.json` by SHA-256 `7f5811831fcb1b4c5d003b2424ebb3d7f42c43226cbefdc656571081d9476f7f`, under `/Users/jason/maintenance/verdify-s3-execution-20261017`. Source validation rechecked every listed retained file and every stage receipt against that summary.

Cleanup targeted only these disposable API objects. It did not delete database rows, Secrets, PVCs/PVs, CNPG resources, or retained data. Future or unreleased point-proof and alert packets/Jobs are outside this receipt; their cleanup is not claimed. The synthetic rows and original failed qualification receipts remain in custody.
