# Shelly source-qualified interval repair

Campaign #775 / resource contract #781. **Candidate only; not commissioned or
deployed.** Forward migration 248 changes the ordinary runtime boundary and is
not authorized by the seven-file 241–247 C0 transition. Do not deploy it with the
older contract, silently expand that inventory, rerun 217 or recapture startup
receipts. A separately reviewed transition and real restored-target proof remain.
The [eight-file transition candidate](c0-resource-transition.md) provides the
owning-runner source for that qualification, not an approved production contract.

Both migration entrypoints and the inactive restore-rehearsal detector include
248 even when 241–247 are absent from a selected inventory. This is detection,
not expansion of the seven-file transition: a 248-only delivery fails exact
inventory admission before database contact or legacy bootstrap, in apply and
plan modes. The restored inspection path remains an explicit hold. A native
disposable counterexample against the prior dispatch showed legacy 248-only
apply returning success while changing the database and making both ordinary
startup probes fail. Regression cases preserve this release-boundary failure;
they do not authorize a resource transition or a receipt refresh.

## Evidence and interval contract

`ingestor/shelly_energy.py` converts only the two named HA power entities. Payload
identity and W unit must match. Both channels must be finite with aware HA
`last_updated` timestamps between collection time minus 300 seconds (exclusive)
and collection time (inclusive). Missing, invalid, unavailable, nonfinite,
unknown-time, future and stale sources remain separately labeled. Source values,
timestamps, IDs and quality accompany revision `ha_shelly_power_v1` in the typed
row; `energy.ts` remains collection time, not a device sample clock.

Repeated fetches cannot renew an old HA source timestamp. Unchanged physical power
with an old HA update can therefore remain unqualified: no heartbeat is invented.
This gateway-clock limitation requires real commissioning evidence. Zero is an
observation, not a replacement for a missing channel; signed power is preserved.
A failed fetch inserts a gap row. The unverified cumulative counter no longer
becomes `kwh_today`; unverified heat/fan/other attribution remains NULL.

Migration 248 appends evidence columns and bounded insert grants to the existing
owner-sealed write facade; it grants no base-table DML or runtime function. The
interval calculation is inlined into `v_energy_daily` and the API's actual
`v_resource_accounting_health` reader, not a private health callee. Mutation tests
verify both fingerprints cover each copy using the energy-related grants extracted
from immutable migration 217. Old rows retain their existing field values and
NULL revision, not invented source provenance.
All polls participate in sequencing, including invalid/null polls. Duplicate
timestamps cannot supply an interval. Valid intervals end at the earliest next
poll, collection+300s or either source+300s. The final sample has no extrapolated
duration. The SQL rechecks metadata, finiteness, freshness and channel sum, with
a relative floating-point roundoff tolerance that is not calibration uncertainty.

Explicit America/Denver boundaries split midnight intervals and give actual
23/24/25-hour denominators independent of session timezone. Energy is signed
watt-seconds/3,600,000; average power is duration-weighted. Display energy rounds
to 0.001 kWh: summing rounded days need not equal a rounded multi-day integral.
No qualified interval yields NULL energy, not zero. Both the 300-second hold and
90% coverage label are diagnostic policies, not a scientific endpoint. Scoring
eligibility remains false even at 100% coverage pending separate commissioning.

Meter health uses source timestamps. Reconciliation keeps the two scopes separate
and sets their subtraction to NULL: partial-measured versus whole-modeled energy
does not define waste. The public projection retains scope and false eligibility.
Both daily writers use the null-preserving consumer to clear stale derived totals
and peaks for the intended greenhouse/day. Frozen research files and outcomes
are not rewritten, and no calibration, daily reset or installed circuit is inferred.

## Source-derived reader boundary

The required existing, non-grantable table-level SELECT grants are:

| Reader view | Duty role |
|---|---|
| `v_energy_daily` | `verdify_ingestor_runtime` |
| `v_energy_estimate_reconciliation` | `verdify_api_runtime` |
| `v_resource_accounting_health` | `verdify_api_runtime` |

These ACL entries put each body in both 217 catalog fingerprints. Missing,
indirect-only, PUBLIC-only or grantable substitutes are refused before columns or
privileges change. The migration does not repair or invent a reader grant. This
checks the required energy subset, not the complete production role inventory.

The earlier `cdf423f5` fixture granted API access directly to private
`v_energy_meter_health` and to `v_energy_daily`; those are not migration 217's API
grants. Its mutation pass therefore did not establish the real reader boundary.
The corrected test proves that changing the old private health body can leave
both fingerprints unchanged. The repaired outer view contains its own power-health
calculation and preserves the existing water/runtime-model UNION branches. The
old private health view becomes a compatibility projection of that outer view;
the dependency no longer runs from the public reader into the private helper.
API access to both private health and the ingestor-only daily view stays denied.
No new SELECT privilege is added.

## Qualification and delivery

```sh
SCORECARD_TEST_PG_BIN=/path/to/pg16-timescale252/bin python -m pytest -q \
  tests/test_shelly_source_intervals.py tests/test_resource_ledger.py \
  verdify_schemas/tests/test_telemetry.py
```

The tests start a private TCP-disabled native TimescaleDB 2.25.2/PG16 cluster with
migration 194's synthetic predecessor. They exercise actual producer conversion,
new SQL, duty-role insert/API read, public projection and daily consumer; null
breaks, stale/future clocks, duplicates, zero/signed/fractional values, midnight,
DST/timezone, compressed legacy chunks and transactional rollback. The actual 217
startup functions on a synthetic catalog prove both fingerprints change and old
receipts remain unchanged. This is a release-blocking counterexample, not complete
role normalization, a real production restore, physical clock proof or deployment.

Do not count missing-dependency skips as qualification. Add these tests to the
owning fleet CI registry, not its generated mirror. Obtain exact-SHA CI evidence,
real isolated restore/rollback proof, a reviewed transition contract and independent
pin. Build affected registered images through Kaniko/Zot, retain prior identities,
and use owning GitOps. The new producer requires the expanded facade; the old
producer supplies only unqualified legacy rows after this schema change. Plan and
verify the bounded writer handoff, exact schema/consumer/producer/startup adoption,
Argo Synced + Healthy and current source-quality/data evidence together.

Keep source rows, backup, previous pins, catalog/ledger/receipt snapshots and failed
attempts. Pre-commit DDL/data/grant changes roll back together. An image-only
rollback after database commit is not sufficient: qualify a forward correction or
restore path. This source neither grants production access nor commissions the
resource endpoint, resolves the incident, chooses the season/design, or authorizes
physical Gate P, a random draw or pilot launch.
