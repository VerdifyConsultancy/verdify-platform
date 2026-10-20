# October 20 remaining unblocked follow-through

Jason authorized continued owner-lane delivery, deployment and live verification, starting with #1008. The earlier S6 audit/receipts remain historical acceptance evidence.

| Issue | Current result | Remaining acceptance |
| --- | --- | --- |
| #1008 | Camera-only Cloudflare freshness and cached-request per-IP limiter are deployed; 50 same-URL requests yielded eight 429s in 3.382 seconds, both sites 200. Network PR #756 merged. Turnstile widget exists, central encrypted custody PR #5200 merged and Secret-only Argo reconciled. Publisher-first PR #1030 merged; exact full sync submitted. Both selected images built successfully. | Served publisher generation and actual widget check, then checked API activation PR #1032, full sync and live token denial. |
| #990 | Exactly 44 inactive Jobs, 38 runner ConfigMaps and 36 unreferenced deny-all NetworkPolicies removed by UID/version guards and verified body-level dry-runs. Every policy selector matched zero pods immediately before deletion; no live controller consumer. Restore ingress, root CA and all data objects preserved. All three product Applications were Synced/Healthy after cleanup. Renderer now emits a 86400-second TTL; two focused tests pass. | Checked merge of renderer/receipts. |
| #993 | Desired restore policy spec exactly equals live; no widened selector or port. | Checked merge, full sync, Argo tracking and read-only connectivity probe. |
| #996 | Four obsolete roles/bindings removed; sole identity namespace absent, no other binding consumers, no credential touched. | Product/central ownership records merge. |
| #997 | Four stale ConfigMaps removed after checking all pod/controller references. Production four ConfigMap UIDs/content hashes unchanged; all 21 approved public dashboard metadata routes return 200 through Cloudflare. | Monitoring PR #1039 removes 26 staged and eight legacy JSON copies with byte-identical full observability render; owner/central records merge. |

## Recovery custody and limits

`evidence/990-preservation.json` and `990-exact-targets.json` are sanitized metadata. The encrypted recovery archive contains the exact 118 pre-state objects, the one retained Job log, and four existing tracked citation files. Independent Mac decryption produced the same archive hash. Forty-three old Jobs already had no retained Pods before this cleanup; no replacement logs were fabricated. The archive stays in private local operator custody, excluded from Git, with mode 0600 in a mode 0700 directory. Automatic approval review rejected GitHub export of the derived snapshots/logs; only this sanitized receipt is published. Existing cited repository evidence remains unchanged.

No PVC, PV, database, credential, namespace, firmware, dosing or irrigation action was performed by these cleanup operations. The one existing completed repair Pod could be garbage-collected with its deleted Job only after confirming no claim was owned by any targeted Job/Pod. Private recovery material is not a claim of off-Mac backup or whole-estate recovery.

Each final issue closure still requires its remaining real end-state checks. Source, image-build success and planning status do not substitute for deployment acceptance.
