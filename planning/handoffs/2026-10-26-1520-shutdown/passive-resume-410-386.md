# Passive #410 / #386 resume evidence

Observed 2026-10-26T14:14:31.343565+00:00. Read-only; four bounded prod DB transactions, no retries. The newly completed local day/night is October25. Solar phase at the first read was 0.13627172036965624; October26 sunrise was 2026-10-26T13:22:20.207535+00:00.

## #410 remains open; accepted criteria 1 and 3 are retained

The unchanged installed getter (`89782089fd6ae3ecfeecdfbc1b730f6ab6a272c0da7b35ef562788eddbfa42c0`) read night **October25** in **3.718 seconds** and returned `no_episodes` / zero episode rows. This is negative availability evidence, not a clean or measured-zero outcome. No new model or scorecard consumer invocation was made.

The completed October25 daylight period has **610/646 valid represented minute buckets, 36 missing**. All 610 observed minutes carry the selected decision fields; there are zero observed held-temperature/held-admission samples and **4 ordinary nonhold drying minutes**. Complete recorded decision coverage is false; complete-day measured-zero remains **NULL**. Missing buckets were neither filled nor interpolated. Relay-key presence does not establish source freshness or continuous physical absence.

The naturally scheduled SUNSET/SUNRISE plan metadata exists for `iris-20261025-1808` / `iris-20261026-0725`; this metadata alone is not another consumer trace. The previous retained natural consumer evidence remains accepted: https://github.com/VerdifyConsultancy/verdify-platform/issues/410#issuecomment-6400035057 . The reused getter query’s constant natural-consumption flag is scoped to that query, not a revocation of accepted criterion 3.

Next: retain the existing collectors and getter. A genuinely complete prospective observation with explicit freshness/continuity is still required; repeating an incomplete count or waiting alone does not turn it into whole-day zero.

## #386 remains open; new ordinary cutoff receipts

| Circuit | Local cutoff October25 | Reported relay OFF UTC | State OFF UTC | Reason UTC | Decision epoch |
|---|---|---|---|---|---|
| main | 20:00 MDT | 2026-10-26T02:00:00.175863+00:00 | 2026-10-26T02:00:00.206568+00:00 | `outside_window` 2026-10-26T02:00:00.234409+00:00 | 1792980000 |
| grow | 22:00 MDT | 2026-10-26T04:00:00.761588+00:00 | 2026-10-26T04:00:00.816098+00:00 | `outside_window` 2026-10-26T04:00:00.833037+00:00 | 1792987200 |

Only two **±3 minute** windows were read:4 equipment rows and36 system rows,6947 bytes. No original ON transition, active minimum-on overlap, continuous physical ON, or manual/safety override event was established. No DLI/efficiency conclusion follows.

Current source `119b85290ded94730abc03f45c332606d67431f4` has the exact same `evaluate_lighting` function as reported firmware source 620a218a: SHA-256 `970c2012ba557ba063799faa7a25555dd16ec0b5fb5762c9e8bdc5d674654f82`. Existing precedence remains: auto-disable dominates; ordinary solar cutoff removes demand; enabled current ON may finish min-on. Source equality is not a fresh flash-byte hash or live boundary qualification. Reported firmware remains `2026.10.2.0637.620a218a-wifi-bound`.

Next: await a naturally eligible original ON transition shortly before cutoff, then retain exact decision/relay evidence through the actual min-on expiry. This packet shows no contradiction that warrants a code change, forced toggle or OTA.

## Calls, bounds and custody

| DB call | Client elapsed s | Statement timeout | Bounds |
|---|---:|---|---|
| metadata | 0.486443 | 5s +1s lock | one metadata/firmware row |
| getter | 3.717843 | 15s +1s lock | 501 sentinel rows;500 accepted episodes;64KiB |
| daytime | 0.486083 | 5s +1s lock | 24h exact date;2001 climate /5001 action sentinel rows |
| lighting | 4.774617 | 5s +1s lock | two six-minute windows;201 equipment /601 system sentinel rows;64KiB |

Transport: explicit context `vallery`, namespace `verdify-prod`, existing `verdify-db-0` / `postgres`, direct psql as supported by `scripts/verdify-db.sh prod`. Each query used `BEGIN READ ONLY`, local statement/lock fences and `ROLLBACK`. No credentials were fetched or printed.

All exact executed SQL, source hashes, raw result hashes, receipts and call timings are in matching `passive-resume-410-386.*` artifacts. No GitHub/cluster write, new device connection, node action, model invocation, forced equipment change or broad history scan occurred.
