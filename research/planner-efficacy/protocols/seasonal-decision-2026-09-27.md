# Seasonal decision before the experiment-v2 draw (2026-09-27)

## Decision and evidence limit

The existing `direct-launch-basis-v1.json` and mounted `basis.json` name
2026-11-02 as a *candidate* start for the 30-pair/60-local-day randomized
hot/dry study. That date is the earliest offset-stable 60-day start available
from 2026-09-27 under the current scheduler: its assigned days would be
November 2 through December 31 in `America/Denver`. It is a winter interval.
The bounded moderate/aggressive profiles target cooling and wetting in hot/dry
conditions, so this interval does not support the proposed treatment question.
Do not promote that candidate into a design lock or random draw merely because
the date passes the code's future/UTC-offset checks. This note leaves the
historical candidate and the accepted 0.13776 modeled joint-power figure
intact; neither is a qualified prospective efficacy design.

The first **coverage-supported** 60-day hot/dry window is not yet known. The
current source-stamped complete-day study covers July 11 through August 13,
2026 (34 days), and the September 4 incident supplies one later hot/dry day.
Those observations do not establish a complete 60-day seasonal distribution,
fixed-panel eligibility, selector admission, fallback, pair completeness, or
joint power. A July 11 through September 8, 2027 window is a calendar
*candidate* because it begins with the historically observed July segment and
does not cross a Denver UTC-offset change; it is not approved for lock.
Resolve [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371)
and [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779),
then perform [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782)'s
fixed-panel, actual 06:00–24:00 endpoint and frozen-selector replay before
selecting the first supported future window. Preserve the same 30-pair option
as exploratory unless a separately justified prospective redesign is locked.
No 60-day hot/dry efficacy claim, confirmation of benefit, or whole-resource
savings follows from the present power artifact.

## Earliest prospective winter feasibility packet

Prepare a **separate observational study**, provisionally titled
`verdify-winter-feasibility-2026-27`. Its candidate calendar is November 2
through December 31, 2026 inclusive: 60 local days at UTC−07:00, with the
same 06:00–24:00 (18 elapsed hour) daily observation window. This is a
prospective operations and measurement question, not a second arm, a
replacement randomized pair, or a continuation of the hot/dry study ID.
It has no design lock, random draw, experiment-owned setter call, nonbaseline
profile exposure, or day-1 authorization. Ordinary deterministic controller
operation and safety interventions remain under their existing authority.

Before its first observation day, freeze a separate source-stamped protocol
instance with the actual start date, fixed north/east/west probe IDs, crop
target and firmware revisions, 48-field observation schema, extractor and
analysis hashes, storage location, observer role, and an audit of existing
writer/backup/Argo state. If those inputs are incomplete on November 1,
record a missed candidate and preregister a new prospective interval; never
backfill days into a prospective claim. No physical check is a gate for
this passive packet. A later component proof or randomized launch retains
its own technical and authority boundaries.

For every scheduled day, retain the source-stamped extract and canonical
hashes for fixed-panel climate sample eligibility, target/band revisions,
forecast vintage and observed outdoor weather, 48-field cfg freshness and
generation where visible, selector *virtual* choice/fallback, incident and
safety flags, and precisely classified resource streams. Record absent or
unobservable fields as such, including on-chip consumed band edges. Preserve
all 60 calendar rows and a reason for each missing interval; no day is
replaced. Review operational completeness and safety daily without
computing A-versus-B efficacy. Freeze a final manifest and report observed
data yield, selector opportunity, fallback, semantic gaps and supportable
resource scope. Do not report a treatment effect from this packet.

The winter packet can be prepared in source now and can collect read-only
data under the ordinary operating contract. It cannot satisfy #641 physical
proof, #424 on-chip consumed-band lineage, #778 live wetting safety, #783
restored-data vertical qualification, #588 lock/draw, or #642 randomized
activation. Those remain separate evidence requirements for the hot/dry pilot.

## Immutable read-only collection

`research/planner-efficacy/winter_feasibility.py` is the extractor for this
separate observational packet. Before the first 06:00 Denver observation,
register one instance from concrete, retained files for the fixed panel,
crop-target history and 48-field cfg schema. The `register` command copies
those source bytes into a private archive, hashes them, records the exact Git
commit and extractor/protocol bytes, and refuses a past start or a 60-day
calendar crossing a Denver UTC-offset change. Use actual observed firmware
revision and a named observer role. Do not invent source files merely to make
registration pass. For the current candidate, the first window is
2026-11-02 13:00Z–2026-11-03 07:00Z and the last ends 2027-01-01 07:00Z.
The `winter-2026-27-source-candidates/` files identify route-only probes and
the cfg schema, but its crop-target reference explicitly lacks qualified
future bins and must not be used to register the study.

```sh
python3 research/planner-efficacy/winter_feasibility.py register \
  --output-dir /private/archive/winter-2026-27 \
  --start 2026-11-02 \
  --panel-source /retained/panel-source.json \
  --crop-target-source /retained/crop-target-source.json \
  --cfg-schema-source /retained/cfg-schema-source.json \
  --firmware-revision 'observed-version' \
  --observer-role 'read_only_research' \
  --archive-id 'winter-2026-27-v1'
```

After each *completed* local day, run from the registered exact Git revision
with an existing `DB_DSN` in the process environment. The collector opens one
repeatable-read, read-only transaction with bounded statements, makes only
`SELECT` calls, and writes one exclusive mode-0600 day artifact. It does not
connect to ESPHome, invoke lifecycle functions or write to the database. Keep
the archive outside a public web root and back it up by the existing policy.

```sh
python3 research/planner-efficacy/winter_feasibility.py collect \
  --instance /private/archive/winter-2026-27/instance.json \
  --output-dir /private/archive/winter-2026-27 \
  --day 2026-11-02
python3 research/planner-efficacy/winter_feasibility.py manifest \
  --instance /private/archive/winter-2026-27/instance.json \
  --output-dir /private/archive/winter-2026-27 \
  --as-of 2027-01-02T00:00:00+00:00
```

Every manifest has exactly 60 calendar rows. Completed days without a frozen
artifact remain `uncollected_completed`; future days remain `scheduled`.
Collection more than 24 hours after a window ends remains `captured_late`,
not a timely prospective day. Captured days report each source row count and
a hash of the canonical day bytes. The fixed-panel coverage count is based on complete north/east/west
**database flush** minutes and is explicitly ineligible as a physical result
without the separate panel and target proofs. The 48-field cfg rows likewise
show shared flush timestamps, not independent device callback times or
generation. Served bands are not on-chip consumed bands. Power, water and
equipment rows are raw, scope-unqualified observations, not whole-resource
savings. Virtual selector choice/fallback is `unobservable` until a frozen
selector replay is supplied; no randomized assignment is inferred.
