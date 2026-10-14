# Maintenance source and image delivery complete; production held

[PR #979](https://github.com/VerdifyConsultancy/verdify-platform/pull/979) passed native CI on exact head `2ccbba16` and merged as [source `3b500cab`](https://github.com/VerdifyConsultancy/verdify-platform/commit/3b500cab56d388229a8e01b4c426592884506296). [The terminal receipt](receipt.json) verifies all 32 payload blobs: 20 qualified source files, the complete 11-file canonical planning snapshot and one two-line migration-test correction. Thirty files actually changed. The original qualified executable bytes and old 275 fixture remain unchanged.

The first native PR run failed because migration 276 was missing from the existing forward-only exception list in the historical-baseline test. The append-only correction adds it to that list, whose assertions also require the migration to remain absent from baseline backfill. All baseline SQL and applied hashes are untouched. That failed run remains in history; the corrected exact-head run passed. Local checks passed 57 maintenance/projection/bootstrap tests, 17 migration-contract tests, 35 planning tests, 89 issues/nine stages/76 dependency edges, all seven generated views, migration wrap-safety, Ruff and hooks.

Automatic main workflow `verdify-platform-ci-fmrsp` / UID `f6eed83a-f131-4000-ad20-c89506e8d8df` succeeded at 17:51:21 UTC on October 14, 2026. Its actual classifier selected exactly ingestor, migrate and Lab publisher. The trusted receipt accepts all three immutable published images at the same exact build source:

| Image | Published digest |
| --- | --- |
| ingestor | `sha256:fd18c2920fb1d46d9fbb9e4ea12956a1738f1cf35d9e3e255786e40983f8d44d` |
| migrate | `sha256:1c1a138001c3f05ceeefc98c172a9168bae897259a6da7b34ac971395d10b287` |
| Lab publisher | `sha256:9ea55b7603e1e078b3afd59fd03264d9599ff51caaa9e1e63ce2a96b65f41797` |

[Actuator `cc9750fc`](https://github.com/VerdifyConsultancy/verdify-platform/commit/cc9750fc15a22148cb17e3f17707966d45c80072) is the exact child of the build source and changes only the ingestor/migrate candidates. All five managed pairs were reviewed: API, MCP and orchestrator are unchanged/equal to their release pins. All release pins and the current Lab hand-pin remain unchanged. The actuator follow-up `zd64q` succeeded with `skip`, zero profiles and zero builds. Candidate actuation retains the reviewed source render `c7e39d2b`, with 129 resources and zero rendered Secrets. Source itself changes two maintenance CronJobs and adds the bootstrap hook; it has not been synced to production.

The fresh descendant ingestor publication and candidate replace the old pre-#975 `6856b464` candidate and cover that outstanding publication gap. The original `496hd` failure remains Failed, and production runtime adoption is still pending. No extra #975 build or ingestor replacement was performed.

The genuine projected native proof remains bounded: nine fault families, permission/malformed-input denials, supported fixed operations and rollback with the recorded conditioned allocator inverse. Both raw catalog seals and the exact four-field autovacuum delta remain explicit. No byte-identical physical reseal, original materialized-view row hash, production LOGIN/TCP/pool or committed production migration claim is inferred from source or CI acceptance.

The shared CI window is complete and released. Sole Verdify main/production-writer custody is retained for the specifically authorized next step. The concrete private decision packet now binds these source/images and rollback pins to exactly two new persistent production credentials through the existing central-KSOPS owner, then one attended full-hook adoption. Credential provisioning, release promotion, Lab hand-pin, production sync, production SQL and device work remain held; #643 stays open until its declared production adoption/rollback acceptance is reconciled. Detailed credential custody and CI metadata remain private.
