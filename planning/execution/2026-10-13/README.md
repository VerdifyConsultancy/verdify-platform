# October 13 CI mirror distribution and Lab limitation

[Exact terminal delivery receipt](ci-mirror-delivery.json): main `82195c1b6ada500e254acca27018278597498a69` contains only the seven frozen canonical paths from `41004f24` and the exact owner-generated `.agent-fleet/ci.yaml` mirror (`af3fecfe`, command `7a5ea7e8`). All eight provider blobs and decoded bytes match. Normal planning validation passed 35 tests, schema 88 issues/9 stages/76 edges, seven generated views and commit hooks. Firmware tree `c4a85e4b` and production render `158eceaf` are unchanged.

The one ordinary automatic workflow `verdify-platform-ci-9l5f5` / UID `32b09b0d-ac24-43a0-b052-dca35e0d58f2` ran 16:06:34–16:15:50 UTC and Succeeded with mode `validate`, zero image profiles, one actual validation Pod and zero builders. The directly captured Pod UID and terminal workflow node are preserved separately from post-completion Pod garbage collection.

Final live state is **Synced at `82195c1b`, Degraded**. All 15 Deployment specs/UIDs remain ready and unchanged; ingestor Pod UID `6509f094` retains the same container and zero restarts. Application spec and its last full sync are unchanged; no new production sync occurred. This is source/CI acceptance, not a passing current production-health claim.

Lab publisher Jobs at 15:35 and 16:05 UTC failed with `ValueError: invalid publication receipt` in `lab-publish-receipt.py:_load`; the earlier failure predates this main distribution event. Cron last successful time was 03:44:11 UTC. Failed Jobs/logs remain preserved. The October 12 bounded runtime acceptance and #949 closure remain valid history; the present Lab incident is separate.

Shared CI custody returned to the existing coordinator for the existing owner's registered amd64 compiler/full-main archive qualification. Sole production-writer custody stays with this lane for separate read-only Lab diagnosis and the smallest supported recovery. Every shared CI/build/promotion remains serialized behind the compiler owner's explicit release. No new collector, mask, guard relaxation, image promotion, sync, ingestor replacement, firmware OTA or DB cutover was performed in this frozen eight-file window.
