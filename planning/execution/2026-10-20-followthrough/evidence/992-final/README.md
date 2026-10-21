# Verdify rehearsal retirement — 2026-10-21

Verdify #992 live retirement finished in one attended sequence after native CI for agents #5242 passed and the PR merged normally. Parent catalog and AppProjects are Synced/Healthy at `03dfb8a581c66c4593007d4a014d5afbb86864b1`; three Applications and two AppProjects are absent. The CNPG Cluster, three pods, eight claims and namespace are absent. No forced deletion or finalizer removal was used.

Garage's exact legacy bucket and both bucket-scoped identities are absent. All 5,248 frozen objects (9,905,900,646 bytes) were removed in 213.36 seconds under the existing global NAS lock, 25 deletions/second cap and pressure guard; no pressure pauses occurred. Provider health passed afterward. The NAS window was handed back; the separate N17 qualification remains failed/stopped and was not accepted by this work.

Eight original Released/Retain PVs and detached Longhorn recovery volumes remain owned by storage-infra. Native snapshots and all eight native B2 backups remain preserved. Independent verification covered 935 decompressed SHA-512 blocks; subsequent read-only metadata verification confirmed unchanged configuration hashes and every referenced object present. This does not claim a full database restore or physical capacity reclamation.

Private encrypted logical dumps, sealed credential recovery and pre-retirement metadata were independently decrypt-verified. Raw archives and credentials are excluded from Git. The live product database pod and production database/lab claim UIDs were preserved. Production smoke: 9 passed, 0 failed; device monitor: exactly one connection.

The source closeout removes the obsolete KSOPS generator and eight ciphertext files, active bucket request and custody exception, records retired ownership, and explicitly inventories retained recovery storage. Historical runbooks and fixture-only profiles remain as evidence, not active delivery sources.
