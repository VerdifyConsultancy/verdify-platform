# P3: bounded solar-night diagnosis for #410

The installed getter and its twelve function dependencies match source. The
one preselected October 16 pilot then timed out at its two-second statement
limit. It returned the successful identity guard only: no episode result,
daytime held-admission count, AH/VPD response or real episode verdict is
available. No retry, date substitution, wider scan or further night was run.
**#410 remains open.**

The independent source review found and fixed an MCP summary defect: a nonempty
set of episodes with `safety_gate_status: incomplete` became aggregate `pass`.
An unknown or null status had the same defect. The corrected summary gives
explicit failure priority, requires every episode to say `pass` before returning
`pass`, otherwise reports `incomplete`; no rows remains `pending`. It preserves
every episode, disposition and confound, and does not turn a safety pass into an
effective dry-out outcome. This source change has not been deployed or verified
through a running MCP/planner.

## Actual custody and terminal result

- [Identity capture](identity-capture-receipt.json), at 2026-10-18 15:23:33 UTC:
  22 JSON metadata rows, 15,200 bytes, exit 0, no stderr. Retained
  [observations](identity-observed.jsonl) SHA256
  `970852793d4ab0648c6b5cf8b52a5139df9f83bb7c3fa20594253fef83296388`.
- [Offline identity verification](identity-verification.json) matched all
  thirteen complete definitions, bodies, signatures/returns, language,
  volatility, owner/config and eight exact migration file hashes. The eight
  ledger entries are **baseline stamps from August 15**, not evidence of a new
  migration execution. Current installed definition matching is separate proof.
- [Pilot terminal](pilot-terminal.json), 15:27:13–15:27:16 UTC: exit 3,
  [331-byte guard output](pilot-stdout.jsonl) and
  [89-byte timeout error](pilot-stderr.txt). The source/ledger/postmaster guard
  passed before the getter timed out. The original
  [capture receipt](pilot-capture-receipt.json) is retained unchanged. Because
  `ON_ERROR_STOP` ended psql on the error, the trailing explicit `ROLLBACK` was
  not reached; the failed read-only transaction ended with its connection.
- ROOT's before/after capture reports the same production database Pod UID
  `b1fa2c0e-a995-481f-a29f-4cfaad4e697b` and unchanged container state. These
  captures did not change schema, settings, device state or credentials.

Private originals remain in
`/Users/jason/maintenance/verdify-s3b-execution-20261018/p3-identity-01/`
and `p3-pilot-20261016-01/`. Public copies contain function/ledger metadata and
the bounded error only, with no credentials or Secret data.

## Source and measurement semantics

[source-contract.json](source-contract.json) binds source
`541c11ad0786e00e50e45f52ca4828173ddc58c2`. The latest owning declarations are
migrations 145, 164, 165, 170, 172, 181, 186 and 191.
[verify_identity.py](verify_identity.py) reproduces the complete definition
hashes from those source headers and unchanged function-body bytes, including
defaults, strictness and row declarations. It also checks the previously
retained native getter definition independently. It rejects missing functions,
overloads, changed definitions, changed ledger hashes and a different database.

Migration 191's `fn_realized_solar_night_dryout(date,date,text)` is the supported
getter already used by `outcome_kpi`. Its source uses solar phase >= 2 for night,
with smooth sunrise/noon/sunset/midnight anchors from migration 172 and the
Longmont NOAA sunrise/sunset calculation from migration 186, including Denver
DST. This is a solar boundary, not a fixed 02:00–06:00 clock. Source inspection
does not prove the current device and database are bit-for-bit phase-equivalent.

For one requested night the getter reads two adjacent local calendar days. The
preselection was October 16 because the metadata capture's Denver date was
October 18; both October 16 and 17 were complete. Calendar completeness does
not establish whole-night telemetry coverage. October 15 and 14 were merely
the remaining possible bounded dates, not attempted or substituted dates.

The getter uses same-row relay truth and admission conditions, including the
held-temperature flavor. General daytime dehumidification and daytime
held-temperature admission are distinct. Counts for the two adjacent local
days repeat on each night's episode, so MCP takes their maximum rather than
their sum. **No episode result does not prove a zero daytime hold count.**

The returned episode contract includes climate/action coverage and gaps,
observed vent/fan/heat/wetting duties, indoor/outdoor absolute humidity,
temperature floor margins, before/after VPD and AH responses, admission and
stop reasons, gate violations, confounds, evidence status and an explicit
`effective`, `ineffective`, `blocked` or `insufficient_evidence` disposition.
The response window is 10–20 minutes after the episode start. Missing response
floors yield incomplete safety; held daytime admission, forbidden heat2 or
admitted floor breaches fail safety. Missing samples/coverage can leave outcome
evidence incomplete even when its narrower safety field says pass. Wetting or
nonpositive outdoor AH advantage can confound the result. These labels must
remain visible; they are not a universal VPD target or crop-safety claim.

Historical `setpoint_snapshot` values are preferred for served temperature/VPD
floors, but the getter can fall back to current crop-band functions, current
season and anchors. Its rows do not expose which minutes took that fallback.
The current aggregate climate fields also do not certify a fixed three-probe
panel. Neither limitation is silently repaired by this packet.

## Query and projection boundaries

[identity.sql](identity.sql) was the metadata-only first step.
[pilot-2026-10-16.sql](pilot-2026-10-16.sql) used the existing getter once,
read-only with 2-second statement timeout, 500 ms lock timeout, parallel workers
0 and 4 MiB work memory. Its guard rechecked exact definitions, ledger hashes
and postmaster before the episode statement. It would emit at most 500
episodes and explicitly flag a 501st row; it does not truncate and then claim
complete evidence. The latest-30-night convenience view was never queried.

[summarize_pilot.py](summarize_pilot.py) is a bounded offline reader for a
successful retained response. It preserves every original row and verdict,
rejects overflow or a guard-only result, and leaves absent daytime counts
unknown. It was tested with synthetic rows; **it cannot summarize this actual
timeout as an empty successful result**. The original getter's performance
cause was not established by a two-second cancellation.

The MCP `outcome_kpi` projection carries these validity-bearing rows in
`vpd_policy.realized_solar_night_dryout`. Its response schema preserves this
dictionary. The regression executes that actual repository-owned assignment
without importing the service runtime or using a database. It reproduced
[five failures against the original source](projection-old-source.json), whose
[complete focused output](projection-old-source-output.json) is retained. The corrected
projection plus metadata/packet guards passed [27 focused tests](pure-checks.json)
and Ruff. These are source-level checks, not genuine greenhouse evidence or a
running planner consuming a result. No actual planner context route was proved
to have consumed this field.

ROOT owns native CI admission and delivery. Register
`tests/test_outcome_kpi_dryout_projection.py` and
`tests/test_solar_night_packet.py` in the existing source CI cohort. The source
commit declares `Post-merge restart: verdify-mcp`; no ingestor change or restart
is needed. A merge alone does not promote or sync production.

Before a future real episode qualification, diagnose the existing getter's
bounded-read performance under a separately reviewed query plan. Do not retry
this failed attempt, extend its timeout or broaden its dates automatically.
Actual episode safety/daytime counts, usable response evidence and a running
MCP/planner seeing the quality-bearing payload remain unverified. This packet
completes the bounded diagnosis, not the original issue's acceptance criteria.
