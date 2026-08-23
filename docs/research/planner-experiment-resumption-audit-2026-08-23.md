# Controlled planner experiment resumption audit — 2026-08-23

> **Planning update:** this report remains the authoritative factual audit of
> current code/runtime gaps. ADR-0010 and
> [`planner-experiment-fast-path-2026-08-23.md`](../plans/planner-experiment-fast-path-2026-08-23.md)
> supersede its Phase 1–5 generalized policy-vector critical path. The fast path
> deliberately keeps that mode off and uses the deployed confirmed-component
> setter/readback transport; it does not claim these audited platform defects
> are fixed.

- **Decision:** HOLD physical experiment activation. Keep the production policy-vector path off.
- **Audited source:** `62caffead96416f3d57fa5bf9bf727dfbf376bf0` (`main`, fetched 2026-08-23).
- **Live observation window:** 2026-08-23 16:50–16:56 UTC.
- **Scope:** greenhouse automation, the operational Iris/Hermes planner, bounded AI tunables, and the planned AI-vs-Frozen-FSM switchback in #581.
- **Supersedes for current status:** the build-phase status in
  [`docs/plans/planner-experiment-program.md`](../plans/planner-experiment-program.md).
  The 2026-08-14 audit remains the design and historical-data basis.

## 1. Executive verdict

Routine greenhouse automation is currently operating, but the controlled
experiment has not started and is not runnable end to end.

The production planner is delivering recent required plans and the core
workloads are Ready. The experiment feature flags are deliberately inert,
there is no active experiment ID, legacy direct policy writes remain enabled,
MCP audience enforcement is off, the firmware twin is absent, and the firmware
builder is suspended. This is a safe current state.

The August implementation wave delivered substantial foundations: a canonical
48-field schema and Python/C++ codecs, experiment tables and ledgered
migrations, worker skeletons, a bounded template-selection prompt, a firmware
policy engine, lifecycle API/observability scaffolding, qualification and A/A
tools, candidate templates, randomization, and a frozen analyzer. Those pieces
pass their isolated tests.

They do not compose. The first real vertical path is blocked by incompatible
host/firmware service arguments, incompatible content-hash definitions, no
host-side manifest delivery, insufficient device-identity persistence, no
baseline/randomized arm executor, and no daily outcome pipeline. Enabling
`live` now would not produce a valid exposure and must not be attempted.

There is therefore **no causal evidence yet that the AI tunables improve the
greenhouse**. Current planner health proves availability, not benefit. The
trial remains worth completing because it is the first design in the repo that
can estimate benefit without giving AI safety or target authority, but only
after the executable-path blockers below pass a real integration test.

## 2. What is implemented, deployed, and still missing

| Surface | Implemented in source | Current production / evidence | Status |
|---|---|---|---|
| Deterministic automation | Eight-state firmware FSM, relay interlocks, existing setpoint/readback path | Ingestor, API, MCP, planner, setpoint server and Hermes Ready; current public data health `ok` | Operational; experiment prerequisite checks remain |
| Operational AI planner | Ingestor → Hermes → GPT-5.6 Sol/xhigh → MCP; bounded plan/tunable contracts | Public planner health `ok`; 0 missed, overdue or failed required cycles; recent SUNRISE and MIDNIGHT plans reached `plan_written` | Available; efficacy unproven |
| Lane A — wire schema | Canonical schema v2, 48 fields, generated Python/C++, manifest, domain-separated hashes and goldens | Source/tests only for the experiment path | Codec complete; downstream hash contracts disagree |
| Lane B — database | Migrations 207–213, transition functions, ledger runner, restore verification | #608 recorded 220 current migrations, 0 pending/mismatch/missing | Foundations deployed; admission hash and role split incomplete |
| Lane C — assignment/delivery | Assignment boundary worker, arbiter, outbox/delivery worker, serialized transaction skeleton, writer demotion | Feature-off and legacy-write behavior active | Not executable against firmware; no A/A/randomized activation executor |
| Lane D — treatment firewall | Fail-closed experiment gather packet, template selector, MCP audiences, dark Hermes profile | `VERDIFY_MCP_AUTH_MODE=off`; experiment profile not mounted | Source scaffold present; rollout not exercised |
| Lane E — firmware engine | Policy slots, manifest/vector staging, journal/recovery model, 48/48 consumer manifest, native tests | No experiment/recovery artifact build, OTA, HIL, identity proof or 48 h bake; builder suspended | Not physically qualified |
| Lane F — GitOps/API/twin | Safe-default flags, config revision, lifecycle endpoints, blinded ops board, twin adapter/component | Mode `off`; empty ID; no twin Deployment; no twin CI image profile | Safe defaults deployed; outcome view and live twin absent |
| Lane G — study package | Candidate baseline/moderate/aggressive vectors, qualification template/analyzer, A/A checker, randomization and frozen switchback analyzer | No locked qualification or protocol instance; no run data | Tooling only; not preregistered or executed |

All experiment PRs #589–#604 are merged. “Merged” is not equivalent to
“source-complete” for the paths below: the tests replace the database,
transport, firmware, or outcome dataset at the exact seams that fail.

## 3. Live state snapshot

### 3.1 Safe experiment state

The live `verdify-config` values were:

```text
VERDIFY_POLICY_VECTOR_MODE=off
VERDIFY_ACTIVE_EXPERIMENT_ID=
VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED=1
VERDIFY_MCP_AUTH_MODE=off
```

No `verdify-twin-prod` Deployment exists. The `verdify-firmware-builder`
CronJob is suspended and has no last schedule or successful build. The current
generated `.agent-fleet/ci.yaml` has no `verdify-twin` image profile.

#608 and #609 record the exact current GitOps revision as Synced + Healthy.
#608 also records an isolated restore and the complete migration ledger. This
audit could inspect safe workload/ConfigMap metadata but could not `exec` into
the database pod; no secret or Secret value was read.

### 3.2 Routine planner and data health

At 16:55 UTC the public planner-health surface reported:

- overall status `ok`;
- 0 missed expected cycles, 0 overdue deliveries and 0 required failures;
- 0 active-plan range violations;
- recent SUNRISE and MIDNIGHT required cycles resolved to `plan_written`.

The public data-health surface was also `ok`: current climate, forecast and
action data were fresh and there were zero open critical/high alerts.

This does not close the writer-truth acceptance in #433. A deliberate
one-drift/one-write proof, reconnect-generation proof, truthful partial-batch
lifecycle, and two steady-state hours remain required before A/A.

### 3.3 Current band-readback contradiction

The public band-trace response at `2026-08-23T16:56:25Z` reported:

```text
firmware: temp 74–84 °F, VPD 0.67–1.22 kPa
readback: temp 40–95 °F, VPD 0.35–2.80 kPa
readback_matches_fw_band: false
24 h readback_match_pct: 0
24 h ok_trace_pct: 0
```

This supersedes older evidence that #424 was only a repaired view artifact.
The cause may still be representational rather than device state, but the live
experiment cannot treat the current readback as proven effective policy truth.

## 4. Decisive implementation blockers

### P0-1 — host and firmware transaction contracts are different

The host sends:

```text
policy_begin  {generation,total_size,chunk_count,content_sha256,activation_sha256,assignment_id}
policy_chunk  {seq,data_hex}
policy_commit {generation}
policy_abort  {reason}
```

The firmware services require:

```text
policy_begin  {header_hex}
policy_chunk  {offset,data_hex}
policy_commit {slot,effective_at}
policy_abort  {slot}
```

Evidence:

- `verdify_schemas/policy_transport.py:13-31,200-238`
- `ingestor/policy_transport.py:143-189`
- `firmware/greenhouse/policy_engine.yaml:24-110`
- `firmware/lib/policy_vector.h:102-110`

`Esp32PolicyTransport.available()` checks service names only, so it can report
the unusable surface as available. The passing transaction test mocks the host
shape and never validates it against the ESPHome declaration.

The firmware also refuses non-ROM vectors until an experiment manifest is
committed, but the host declares and calls only the five vector services. No
manifest encoder, sender, outbox stage, or service caller exists in the host.

### P0-2 — canonical content identity has incompatible definitions

Lane A correctly defines `content_sha256` as a domain-separated digest over
the schema version, wire-manifest digest, canonical bytes and frozen revision
IDs (`verdify_schemas/policy_vector.py:343-360`).

The applied database contract instead requires plain
`sha256(canonical_bytes)` for templates, effective vectors and admission:

- `db/migrations/207-controlled-policy-experiment.sql:146-170,425-452`
- `db/migrations/209-wire-schema-v2-field-count.sql:428-444`

The arbiter supplies the Lane A digest (`ingestor/tasks/policy_arbiter.py:278-345`),
so a valid vector is rejected. A local golden-vector probe confirmed:

```text
vector_fields=48 vector_bytes=178
domain_hash_equals_raw_sha256=false
```

Firmware recomputes against a fixed ROM revision JSON while the arbiter binds
the experiment revisions, creating a third definition. The A/A gate repeats
the raw-byte assumption. This must be corrected with a new ledgered migration;
already-applied migration files must not be edited.

### P0-3 — contract-v2 device truth cannot satisfy qualification, twin or A/A

After activation the delivery worker intentionally writes
`content_sha256=NULL` and `firmware_revision=NULL` to the only persisted device
snapshot (`ingestor/tasks/policy_delivery.py:289-310`). Later
`policy_identity` updates are cached only in memory
(`ingestor/ingestor.py:1650-1660`).

The downstream gates require what was discarded:

- qualification maps the current vector by non-null content hash and requires
  a 60-minute trace with no snapshot gap over 180 seconds
  (`ingestor/tasks/experiment_qualification.py:181-225,305-343`);
- the twin as-of view requires observed content identity
  (`db/migrations/211-twin-asof-input.sql:169-191`);
- A/A gates require the observed and expected content to agree
  (`scripts/experiment-aa-gates.py:360-415,603-609`).

The delivery unit test explicitly asserts the contradictory NULL. One
activation-time snapshot also cannot prove continuous exposure. Persist a
periodic device-identity receipt (or a rigorously reversible activation→content
binding) before any qualification clock starts.

### P0-4 — no blinded arm/baseline execution service exists

The randomization package generates and verifies artifacts, but no runtime
service privately resolves X/Y, activates Frozen-FSM on baseline days, permits
the two templates on AI days, or drives the deterministic fallback.

`experiment_assignments.py` closes boundaries, manages the legacy-write hold,
and emits a `boundary_activation_intent` note. It does not create the policy
proposal/activation for A/A or randomized assignments. The qualification
worker explicitly returns for other experiment kinds. There is also no
audited loader that materializes the committed schedule before arming, and the
unblind API records an event without populating the arm-resolution mapping.

The delivery lease is additionally global: it is enabled by one configured
experiment ID but does not constrain the leased outbox row to that experiment
or reject an expired validity boundary. Add those fences to the vertical-path
test.

### P0-5 — no frozen daily outcome or causal result pipeline exists

The API says the intended `v_control_experiment_daily_outcomes` view has not
landed (`api/main.py:4770-4773`) and exports only assignment/exposure metadata
(`api/main.py:4770-4822`). The frozen analyzer requires a complete daily
outcome dataset with VPD distance, temperature distance and exact nine-device
runtime. No implementation materializes the locked 15-minute/day eligibility,
counter reconciliation, deviations, ITT/per-protocol rows or missing-outcome
bounds.

Current result-binding functions accept the presence of a 64-hex hash; they do
not prove artifact type or a passing result. Completion/unblind does not yet
require closed exposures, device-confirmed baseline, frozen endpoints and
deviations, or an integrity pass.

### P0-6 — the staged shadow rollout is inert and stale

`experiment_enabled()` and both assignment/arbiter workers require a non-off
mode **and** a valid active experiment UUID. The unmerged
`origin/rollout/shadow-mode` branch sets `shadow` but leaves the ID empty, so
the workers return without touching the database. It has no PR and is 48
commits behind current `main`; it must not be merged.

The runbook also claims the outbox persists in shadow. The arbiter intentionally
marks compiled proposals `shadow` and creates no outbox. The corrected shadow
gate must use an explicit shadow experiment and verify compiled proposals,
zero outbox rows and zero device calls, unless a separately reviewed change
defines different shadow semantics.

### P0-7 — twin and isolation are not production-ready

The live twin component is not in the production overlay and its image has no
registered build profile (#606). Its action oracle maps 22 of 48 policy fields;
26 fields whose consumers sit outside the replay harness are identity-only,
not action-compared. It also compares six relays rather than all nine climate
actuators. A passing 7–14-day report on that scope is not the promised complete
shared-oracle gate.

The experiment DB roles remain a standalone scaffold rather than an applied
migration, and workloads still use the shared `verdify` credential. The
blinding/least-privilege allow/deny matrix must pass with live workload roles
before a blinded run.

## 5. What the experiment can and cannot establish

The treatment is deliberately narrow. Arm A is the current deterministic FSM
with one immutable, device-confirmed baseline vector. Arm B runs the same FSM
and lets the operational planner choose between two frozen hot/dry templates;
only 11 of 48 policy fields may differ. The deterministic forecast proposal is
shadow-only.

The three co-primary gates are:

1. VPD corridor-distance noninferiority at +0.05 kPa;
2. temperature corridor-distance noninferiority at +0.50 °F;
3. superiority on aggregate runtime of nine climate actuators.

All three must pass at one-sided 97.5% bounds. This is a large-effect screen;
a null result is inconclusive by design.

A successful result supports only the bounded template selector under this
greenhouse, firmware, season, templates and protocol. It does **not** validate
general online optimization, learned MPC, a literal PID replacement, yield,
crop quality, DLI improvement, energy price savings or profitability. The
historical audit rejected a physical PID arm because the plant models failed
their prespecified gates; Frozen-FSM is the honest comparator.

## 6. Replanned execution and hard exit criteria

### Phase 0 — preserve the safe state and repair tracker/docs (now)

- Keep mode `off`, active ID empty, legacy writes enabled and MCP auth off.
- Do not merge `origin/rollout/shadow-mode`.
- Correct schema-v2 counts and Jason's automated-commitment decision in the
  unlocked protocol documents.
- Track the three vertical blockers separately and keep #581 as the program
  owner.

Exit: current main remains Synced + Healthy; no device, database or Secret
mutation; rollback state is the current config revision and digests.

### Phase 1 — build one executable vertical slice

1. Define one generated manifest/vector service and packed-header contract for
   Python and ESPHome/C++.
2. Define one content/revision hash contract and deliver it through a new
   ledgered migration, Python and firmware goldens.
3. Persist complete, periodic device truth and scope delivery to the configured
   experiment plus current validity.
4. Implement the sole blinded assignment/baseline executor and schedule loader.
5. Materialize frozen daily outcomes and strengthen result/completion gates.
6. Promote experiment roles to ledgered grants and per-workload credentials.

Exit: on a recent database restore, one test chains real SQL admission → outbox
→ manifest/vector calls against the compiled firmware interface → periodic
snapshot/exposure → qualification/A/A fixtures → outcome export/analyzer. It
must also inject failure at every stage and confirm ROM/Frozen-FSM rollback.

### Phase 2 — corrected shadow rollout

- Create and validate an explicit shadow experiment with locked revisions,
  candidate templates, and a preflighted non-actuating assignment schedule.
  The current workers require DB status `armed|running` plus a current
  assignment even in shadow, so that lifecycle must be made explicit and
  proven safe before the flag changes.
- Recreate the rollout from current main, set the explicit ID and MCP auth
  `log`, regenerate the config revision, build/pin, and sync without pruning.
- Verify compiled shadow proposals, **zero outbox/device calls**, no writer
  demotion, no audience denial that would break normal planning, and a tested
  declarative rollback to off/empty.
- Resolve the current band-trace contradiction (#424) and complete #433's
  deliberate writer/reconnect acceptance before live mode.

Exit: exact revision Synced + Healthy, expected pods restarted, normal planner
health remains green, and the delayed soak shows no actuation or integrity
deviation.

### Phase 3 — artifacts, HIL and OTA

- Register/build the twin image through Zot (#606).
- Mirror/qualify the ESPHome builder through the approved registry path.
- Produce immutable experiment and recovery artifacts with source, toolchain,
  schema, baseline and binary hashes.
- Pass the real service-contract HIL, power-loss/journal cases, recovery flash,
  physical heap measurement and device-identity cadence.
- Schedule the OTA and complete the 48-hour bake.

Exit: the device is confirmed on the expected firmware and immutable baseline;
recovery is rehearsed and no experiment is armed.

### Phase 4 — complete twin oracle, then qualification

- Expand/justify the oracle to all 48 fields and all nine climate actuators.
- Run 7–14 consecutive days with byte-identical policy and action agreement,
  explicit gap accounting and a frozen result hash.
- Lock and run the non-efficacy qualification: 96 transitions, 24 cells, four
  slots each, within the 45-local-day weather window.

Exit: every edge carries a passing qualification result; baseline is confirmed
on-device and the result artifact is bound before A/A.

### Phase 5 — A/A, protocol lock and randomized run

- Run seven fixed local days of A/A and require all six gates.
- Implement Jason's approved automated OS-CSPRNG assignment-service draw;
  publish the domain-separated commitment in Git before the named beacon.
- Lock every remaining protocol/spec/revision/artifact value, generate the
  30-day blinded schedule, and run without efficacy peeking.
- Freeze outcomes, deviations and fidelity exports; confirm baseline; complete;
  then unblind once.

Exit: a reproducible analyzer result and decision whose hash binds the exact
source, firmware, templates, assignments, exposures and outcome export.

The earlier “10–13 weeks after build” clock is no longer a useful forecast
because Phase 1 is unfinished. The fixed twin, A/A and randomized windows alone
consume 44–51 days; qualification can add up to 45 more, excluding engineering,
OTA cadence and review. A 30-day Denver window must not cross the 2026-11-01
DST transition, so the credible randomized window is after that transition
unless every prerequisite finishes unusually early.

## 7. Validation performed

- Focused planner/experiment/qualification suites: 348 application tests
  passed; an overlapping planner suite reported 338 passed and 14 expected
  skips.
- Frozen research package: 61 tests passed, including the new schema-v2 /
  automated-commitment protocol drift checks.
- Native firmware: 273 policy-vector checks, 251 policy-engine checks and 30
  recovery checks passed.
- Replay invariants: 296,698 rows passed.
- Generated contract, header, consumer-manifest, policy-vector and config
  revision drift checks passed.
- Firmware policy consumer manifest: all 48 fields classified, 159 migrated
  reads, 32 justified boot-repair reads and 63/63 control reads migrated.
- Live safe-state/readiness/public API probes passed as described above.
- Docker/Postgres integration tests were unavailable because the workspace has
  no Docker daemon. Direct production DB execution was denied by the intended
  service-account RBAC.
- The repository's complete `make ci` gate passed. Its first run found only
  ignored local `site/node_modules` / `tsconfig.tsbuildinfo` artifacts in a
  test that inventories every file under `site/`; the clean-environment rerun
  passed after those ignored paths were moved aside and the exit trap restored
  them.

These results demonstrate strong unit-level foundations and safe feature-off
behavior. They do not waive the missing vertical integration test.

## 8. Issue disposition

The canonical current status is the dated comment linking this report on each
issue. Original issue bodies are retained as acceptance contracts.

| Issue | Disposition after this audit |
|---|---|
| #581 | Keep open. Experiment not started; link all vertical blockers and this replan. |
| #582 | Close as the 48-field codec lane; downstream SQL/firmware integration moves to the wire-contract blocker. |
| #583 | Keep open. Ledger/schema are deployed, but the raw hash contract and workload role split are incomplete. |
| #584 | Keep open. Worker skeletons exist, but manifest/vector transport, delivery fencing, periodic truth and randomized/baseline execution are incomplete. |
| #585 | Close as a source implementation lane; record that live auth remains off and rollout proof belongs to #581/#587. |
| #586 | Keep open. Source/native tests are not OTA/HIL/recovery/bake acceptance. |
| #587 | Keep open. Outcome view, production twin, full oracle and live rollout remain incomplete. |
| #588 | Keep open. Corrected templates remain unlocked; automated commitment and all artifacts/runs are pending. |
| #599 | Close resolved. Automated Zot pin commits resumed and #608 deployed the outputs. |
| #605 | Close resolved/superseded by #608; #317 retains the generic selective-scope defect. |
| #606 | Keep open and prioritize. No registered twin build profile or production twin exists. |
| #575 | Close recovered. Its bounded workload/backfill/Argo/alert acceptance is now evidenced; writer truth remains #433. |
| #427 | Keep open but update: real required planning currently works; fault/recovery and idle `planner_graph` disposition remain. |
| #433 | Keep open as a live/A/A prerequisite. |
| #424 | Keep open: the current public trace is 0% matched/0% quality over 24 h. |
| #365 | Close: ADR-0004 outcome guidance and zero target-hugging authority are implemented. |
| #371 | Keep open: the broad daily/homepage resource and crop-outcome surface is not the experiment dashboard. |
| #346 | Leave closed; clarify that bounded AI capability is not evidence of efficacy. |

Three new P0 blockers own the work that previously fell between lane issues:

1. unify and prove the manifest/vector wire and content-identity contract;
2. implement blinded assignment execution and continuous device truth;
3. materialize frozen outcomes and enforce completion/unblind gates.
