# WIP C5 policy-twin query handoff — 2026-10-02

## Status

Unmerged source candidate; do not promote or claim dataset parity. Jason requested shutdown. No PR or new CI is started for this checkpoint.

The two-file change replaces inverse-free sliding timestamp MAX with cumulative MAX and exact inclusive 24-hour clipping. Native first-window pseudochange, conflicting duplicate fallback, all joins/cardinality/NULL behavior, all 186 views / 103 CTEs / seven timestamp fields, and native 120-second limit remain intact.

44 focused private/source tests passed; ruff and diff checks passed. Private PostgreSQL 16 fixture branch timings at 20,000 rows were 1,130.339 ms before and 37.737 ms after, with bidirectional EXCEPT ALL equality. These prove private branch behavior only.

## Failed actual proof

ROOT executed exactly one complete actual policy count plus all seven timestamp endpoint aggregates. Original ROOT handle 39638 terminated with operator exit 1 after retained native exit 3: statement_timeout at 120.554741 seconds (outer timeout false). Complete actual performance remains unproven. ROOT verified the exact `cnpg-cumulative-policy-proof-once` application had no remaining backend. No retry, full SOURCE/A/B collector, merge, deployment, or database write followed.

Prior failed whole collectors and their private native SQL/errors remain immutable. SOURCE/A/B complete dataset parity, physical runtime admission, and physical ordinary-client authentication remain pending. Existing genuine PITR marker/recovery boundary proofs remain separate.

## Local private evidence

Evidence directory: `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis`.

Keep native SQL/stderr, cluster/pod snapshots, and binaries private; ROOT owns encrypted preservation. No credentials or binary artifacts are included in this source commit.

Private handles 37933 and 51313 are terminal 0; source tests 57474 terminal 0. Original native observer 78220 terminal 0 and failed collector backend 36178 settled absent. No lane-owned active process is intentionally retained.

## Resume

Inspect the retained actual failing query plan/source and identify the remaining cost without a blind collector retry or scope reduction. The cumulative patch is a WIP candidate, not a demonstrated complete fix. Rebind current native UID/container/SystemID/timeline and preserve all original outcomes before any separately authorized operation.

## Evidence hashes

- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/cumulative-source-review.patch`: `ba81177f2bb59d0f4a0622daf2658754f91a64cbbd1f66315268a47f3eeeb6b7`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/cumulative-source-review.json`: `1e19dd6fb8b9860566cac1c50dcdbc4508115b532100e9449d8c6f47a9ccb0c8`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/cumulative-native-policy-once.sql`: `97456bc6f5757172af6c26f5304dd3a35590f2bf1ae525ed606a9548e8b95e05`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/cumulative-native-policy-once.py`: `d50c6de99956a5d05ef3c3512e10c77ab498097315df8cf8e3c854be1a860f13`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/cumulative-native-policy-contract.json`: `742ad8aec749e1664c065357288b3b078ba67696e2789eb462b766c8704399e1`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/actual-cumulative-policy-once/terminal.json`: `43302f08041745636543e659972531107616f25e6dfd1e388de1cb9c8310e728`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/actual-cumulative-policy-once/stdout`: `7354a5f4e5a47049b6a536c1364c474fc80f8b95730cc18bc49f8a13b659dfe7`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/actual-cumulative-policy-once/private.stderr`: `3b30088622f6392862527fe3f76e55a864025ab93e6ac93d4dc80b7996c07756`
- `/Users/jason/Documents/Codex/verdify-cnpg-policy-twin-capture-preparation-20261002/native-timeout-diagnosis/private-cumulative-components.json`: `12de2b60002cc572db6c04776726abb6f77d90a9f6fbe2b6fba3010238464add`
