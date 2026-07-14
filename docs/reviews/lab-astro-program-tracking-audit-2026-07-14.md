# Lab Astro Program Tracking Audit — 2026-07-14

Issue: #351. Reviewed at 2026-07-14T10:31:43Z.

This audit reconciles the Lab Astro program's implementation state, GitHub
work graph, Project #5 fields, protected-delivery ownership, and durable docs.
It does not activate a workload or change the already-ratified architecture.

## Consensus boundary

The architecture and nine surface issues #474-#482 were ratified at frozen
Platform commit `1e7dc6ccede1a2a87ce09b364a107e9e3660d70e`. The preserved
`.agent-workflow/consensus/lab-astro-migration.consensus-report.yaml` records
all six approving seats, every lane-owner vote, and two consecutive unchanged
Claude and Codex sign-offs. The pure-Python consensus validator returns no
errors for that report.

Issues #533-#542 are implementation decomposition inside those approved
surfaces. They are not a new architecture decision, so this audit does not
rewrite the frozen consensus report or append synthetic model votes to its
ledger.

## Evidence reviewed

- Live static stage: 2/2 Ready, zero restarts, distinct nodes, exact image
  `verdify-lab-astro@sha256:878c522740a44df44369dae1154b162b485a29d4b4b45d9ad48e20a44f22d56b`.
- Static output: 143 graph and two camera identities, zero materialized
  occurrence blobs, and no selected occurrence release.
- Live release runtime: zero replicas and unrouted. Git contains the newer
  #532 dormant pins; the live object has not reconciled them.
- CI: exact #532 and #528 PR workflows succeeded; the canonical exporter
  probe passed its exact-source offline 143+2 contract on node4 after the
  projected-token fix. agents#3002 is closed; no placement workaround remains.
- GitHub: #351, surface issues #475/#476/#479/#480/#481/#482, implementation
  issues #533-#542, their Project #5 records, native parents/blockers, labels,
  milestones, comments, and linked provider/control-plane issues.
- Local reviewed source: `e79a978`, `f6348bf`, `d3ccea9`, and `448d556`, none
  of which is on main or represented by an open Platform PR.

## Normalized work graph

Every independently executable remaining unit has one issue with
What/Why/How/Test/Success, owner/gate evidence, and complete Project #5 fields:

| Issue | Unit | Native prerequisite(s) | Native downstream |
|---|---|---|---|
| #533 | Protected stage store and scoped reader/writer delivery | Protected Garage administration | #540, #541 |
| #534 | Isolated reporting tier/feed and separate credential | None; `verdify-platform` executes under recorded approval | #541 |
| #535 | Executable 143+2 producer and dormant reporting GitOps | None for inert source | #542, #541 |
| #537 | Deterministic packs and resource-budget proof | None | #540, #542, #541 |
| #540 | Distributed lease/fence, GC, accounting, endpoint proof | #533, #537 | #542, #541 |
| #542 | Executable packed publisher and dormant two-pod runtime | #535, #537, #540 | #541 |
| #541 | Exact-revision two-pass activation and durability proof | #533, #534, #535, #537, #540, #542 | #476 and #480 closure |
| #536 | Public-output/media follow-ups | Non-critical path | #475 follow-through |

The native graph is acyclic. #476 and #480 stay open until #541 supplies their
joint live proof; #535 is not incorrectly blocked by protected #534 delivery.

## Branch disposition

`phase4c/deterministic-release-packs@f6348bf` is a mixed review vehicle, not a
safe PR head. Reconstruct #537 by cherry-picking only full commit
`f6348bfc9a92c5340e1852295c7e263732aa1e04` onto current main, preserving the
current live-acceptance package entry. After #537 lands, reconstruct #540 by
replaying these non-merge commits in order:

1. `a288c40475d656462972921e2f0f3790b378b18e`
2. `9faead5c32d081855292038c25bdc6a3822ed2d0`
3. `0b9473f181e63a9e0fe8b04649c250aa57146d74`
4. `bae3bde5243b1ef359eb376cc239730ce2f22e0b`
5. `b632a3d5a0abd7d8be394cc969434bdbc9a6f97b`
6. `8228a9471be1b9dc1c998c34bf02a38bbf3951e8`
7. `d3ccea9c0f4ca98ef75e3ada18040c762d524439`

Exclude merge commits `fd3b6ed` and `51e1828`; their main-side content is
already present. The implementation modules do not overlap, and the extracted
#537 focused suite passed 10/10. Each reconstructed branch still needs its full
issue-local verification before a PR opens.

## Exact-revision rollout contract

For each #541 pass, freeze one immutable Platform commit and exact image
pin-set, render and hash the complete expected resource inventory, verify the
live fleet AppProject admits every kind including `CronJob`, and precompute the
previous known-good rollback revision and manual trigger. Use a reviewed
`jvallery/agents` actuator to target only that commit. Argo must report the
exact revision `Synced` and `Healthy`, with no unexpected resources. After
T+10, a separate reviewed fleet change restores `targetRevision: main`,
autosync disabled, `prune:false`, and `selfHeal:false`. This repeats the proven
agents#2998/#2999 pattern and does not sync moving main directly.

## Six-seat execution review

This factual reconciliation received final `APPROVE / NO CHANGES` verdicts
from all six seats:

- Product and engineering manager: issue completeness, branch extraction,
  acyclic closure order, ownership, and live/desired/local/protected-state
  distinctions.
- Finance and security/privacy: numeric resource envelopes, 80% alert and 100%
  publication block, 48-hour recovery, credential separation, anonymous
  denial, sanitized camera payloads, and Track A isolation.
- Infrastructure and SRE: exact-revision actuation, AppProject permission,
  Argo health/inventory checks, manual-posture restoration, rollback, LKG,
  freshness, and durable acceptance.

Review-requested corrections were applied before the final votes. No seat has
an outstanding rejection.

## Boundary and result

The program tracker, root operating indexes, issue contracts, native graph,
and Project #5 fields now agree. This audit performed no workload activation,
store/reporting provisioning, credential read or value disclosure, stage
sync, route change, production action, DNS/edge change, Quartz change, or
device/private-network action.
