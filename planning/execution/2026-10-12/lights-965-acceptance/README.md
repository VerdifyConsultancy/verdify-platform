# #965: deployed disabled-light boundary and retained-history bounds

This packet reuses the shipped fix and existing qualification. It changes no
product source, build, device command or historical data. The exact
`control_lights` function and its 14 ASGI route/auth/input/OpenAPI regression
cases are byte-identical across PR970, frozen PR973, built source
`cb21fd3d3295aa8d7183f70b0d5ac82405410b1a` and promotion
`d43919e933eb1be69559be7566853ff69a64e903`. The original 78 focused tests and
successful final-source native CI are retained; this lane reran no tests.

The owner-published delivery receipt at commit
`bd7f71de172540714567b1e78d3be85710add3ad` binds both new API Pod UIDs to
`sha256:75f510f52d71846197fdc1ddabead87912d8a31070439a29747e4ec36dc37624`,
the cb21 built source and d439 promotion. `acceptance.json` records exact input
hashes and source/POD/digest comparisons. This receipt proves adoption; it does
not contain a route-specific live OpenAPI/function-hash readback.

The deployed source deliberately rejects valid authenticated light requests
with HTTP501 `light_control_unavailable`, accepting no command and acquiring
no DB connection. Auth failures remain 403 and invalid input remains 400. The
owning HA/setpoint writer and actual-observation paths are retained. A disabled
route has no delivery-success or timeout fallback to qualify, and no physical
command is necessary to establish this supported disabled result.

## Honest historical bounds

The checksum-verified October11 audit counted **87,120 retained light rows**:
43,488 grow and 43,632 main, spanning November 17, 2024 through October 11, 2026.
The table retained only timestamp, equipment, boolean state and greenhouse ID.
It had no writer, source or request identity. Consequently, confirmed API-origin
rows have a lower bound of zero; the conservative unclassified-row upper bound
at that snapshot is 87,120. That is a row-count bound, not a count of known
fabrication or a bound on physical runtime, resource use or treatment effect.
No particular historical row can be certified or relabeled from this schema.

The two retained API-container log scans covered 181,127 lines, with zero light
POSTs and no scan bound reached. Coverage begins October 6 at approximately
01:03Z and ends October 11 at 15:34Z. This supports absence only in those retained
access logs. It says nothing about earlier, later, rotated or missing logs.
The first tracked source containing the faulty write is dated April 9, 2026;
source chronology is not a deployed exposure timestamp and does not authenticate
imported older records.

Preserve every existing history row, assigned-day denominator and study outcome.
Light history without independent source custody remains unqualified for
verified physical-runtime/resource credit. Removing this API writer prevents
new false intent from this route; it does not retroactively authenticate old
rows or automatically certify every subsequent observation. Physical claims
still require authentic source-bound observation under the existing claim rules.

## Only the missing read-only checks

The coordinator relays these exact files/hashes to Retry, the sole production
owner, before execution. This packet lane has made no production DB/API probe.
Reuse newer equivalent owner receipts if they already exist.

- `history-bounds-readonly.sql`: one repeatable-read READ ONLY aggregate/schema
  query with a 10-second statement timeout and ROLLBACK. It refreshes retained
  counts and bounds the gap between the original audit and the owner's fixed
  source observation. It exports no equipment rows and classifies no row origin.
- `live-contract-readonly.py`: executed inside each already-bound API container
  with `python -I -S`; standard-library AST/file hashing plus one 5-second,
  2MB-bounded loopback GET of `/openapi.json`. Proxy-environment lookup and
  redirects are disabled. There is no application import/startup, environment
  or Secret read, database/device call or POST.
- `expected-live-contract.json`: expected source/function hashes, the two API
  Pod UIDs and exact digest. Retry binds native Pod/container/image/restart
  identities before and after readback. A changed identity is refreshed by its
  owner; it never authorizes a rollout or replacement from this packet.

The live OpenAPI must declare 501 without 200 for the route, and actual file and
function hashes must match the already-qualified source. This composition of
native focused tests, exact deployed source and read-only live schema proves
the disabled contract without executing a production command. It makes no
physical readback, full-history provenance or resource-credit claim.

The five clause assessments and remaining receipt gaps are explicit in
`acceptance.json`. Issue/sprint publication remains with the coordinator.
#963 OTA, the later #367 firmware change and **27/48 UNPROVEN** native coverage
remain outside this result.
