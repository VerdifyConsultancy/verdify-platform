# #783 vertical qualification handoff — 2026-10-02

## Current disposition

Issue [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783)
remains open. Inspection used source `a6f190f7594df2f7903d7a09a2752b7126d4feb7`.
No restored SOURCE/A/B target, database, device, credentials, release pin or
production authority was changed. No restore was repeated.

The real backup/schema/role restore evidence and the connected synthetic
experiment evidence are separate:

- [Real post-262 restore](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5895257032): paired backup, restored Timescale data, catalog/ACL and role evidence.
- [Real post-263 retained route](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5916327577): authentic prior route lineage retained through restore.
- [Connected six-day source fixture](https://github.com/VerdifyConsultancy/verdify-platform/issues/783#issuecomment-5917252283): actual selector APIs, Asyncpg executor store, sequential requested/queued/sent/confirmed journals, freezer and exact SQL export. Clock, commissioning prerequisites, selector response, transport, cfg epochs and endpoints were synthetic. It does not supply physical observations or efficacy.

The connected fixture retained six assigned days, two failed deliveries,
five zero endpoint days, one null endpoint day, command-error/reset history,
immutable draw and retry/no-redraw checks. Two freeze/readbacks and analyzer
calls were identical. See the existing
[connected qualification](c5-connected-randomized-clock-qualification-2026-09-30.md).

## Completed read-only/offline check

Current source consumed the original retained **7,547 bytes unchanged**, twice,
with the same original analyzer result: `inconclusive_null_endpoint`, six
assigned days, three locked pairs and no replacement. Altering the input by one
space was refused. The export domain hash remains
`0c70104e0a294deeeb66654f12211c3fd768f89ba15ec5ab92206a446bbac51c`;
raw file SHA-256 is
`f6301499aa776f27a34fda81f62802a130745c9e9116a9f37a0c2dc60ef4d40b`.
Canonical analysis SHA-256 is
`1c6450eba2128af6c6ce84cdde12ac007dd07696e1d62e00f60b0812befd002b`.

This is a current analyzer replay of retained evidence, **not a new execution
of the whole connected path**. It does not claim two C1 epochs as #783.

One read-only command reproduces this custody/analyzer check without SQL,
network access, transport or artifact reshaping. It reads the original private
post-freeze mapping in memory and never emits it:

```sh
VERDIFY_DEVICE_WRITE_ENABLED=0 .venv/bin/python \
  scripts/verify-783-retained-export.py \
  --artifact-dir /Users/jason/Documents/Codex/verdify-783-connected-clock-20260930
```

Use normal Python, without `-O`. Output is a non-secret artifact index and
explicitly limited replay receipt. It requires the retained original export,
metadata, assignment journal and analysis files; it does not invent missing
artifacts or regenerate endpoints.

Focused offline source and analyzer tests passed: data contract, append-only
retry, terminal failure boundary, zero-exposure seal, exact SQL export analyzer,
paired export and assigned-day export. These cover missing/first-day/null/zero,
retention independent of exposure, reset/failure source guards, byte/identity
binding and mapping leakage. They are source/fixture proof; native SQL role
allow/deny execution was not repeated in this task.

Private receipts: `/Users/jason/Documents/Codex/verdify-783-vertical-preparation-20261002/`
(`focused-offline.log`, `retained-replay-qualified.json`). Historical private
connected artifacts remain at the command's artifact directory.

## Concrete remaining work

1. The existing connected README still requires six manual steps. The retained
   one-command replay above closes custody convenience only. #783's entire
   selector→setter schema→sequential receipt→freezer→unreshaped analyzer path
   does not yet have a single source-owned driver/artifact index that executes
   and verifies its idempotent second pass on current restored roles.
2. `qualify-v2-connected-clock.py` uses authentic randomizer and executor
   login pools, but owner access performs lifecycle transitions and endpoint
   scaffolding. `freeze-export.sql` also runs as owner. Existing migration
   fixtures assert narrow grants/denials, but this does not prove the complete
   connected path traverses lifecycle/freezer/blinded-analyst boundaries with
   actual login identities.
3. The fixture installs an owner clock into functions, inserts synthetic
   context/endpoints and uses a fixed experiment UUID. It must **not** run on
   actual restored SOURCE/A/B targets. Those genuine target inventories and
   physical recovery evidence must remain intact. No safe current writable
   disposable execution target was established by this read-only task.
4. [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782),
   [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) and
   [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587)
   remained open on inspection. #782's pretrial/scientific contract must not be
   inferred from the synthetic fixture or forecasts.

Smallest complete next implementation: compose the existing guarded fixture
steps into one device-denied driver for an explicitly disposable writable
rehearsal target, retain real backup/ledger/image/role provenance, separate
owner-only scaffold from real lifecycle/freezer/analyst login calls, and perform
an idempotent second invocation on the same immutable draw. Preserve original
failure/reset rows and compare exact freezer bytes and canonical analyzer
hashes. No redraw, endpoint reconstruction, re-filtering by exposure, production
credentials or physical target mutation is part of that driver. Actual target
selection and execution remain ROOT-owned; this handoff does not authorize
another restore or manufacture missing predecessor proof.
