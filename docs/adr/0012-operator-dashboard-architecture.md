# ADR-0012: Operator dashboard architecture

`dashboard.verdify.ai` is built as a React Router 8 SSR app on an Express BFF (the weather.vallery.net stack)
in its own namespace, `verdify-dashboard`, with native Authentik OIDC and two roles. It talks to an
internal-only operator listener built from the `verdify-api` image. Every human or agent setting write
appends one row to a `desired_state` table: the newest write wins, a `manual` flag makes Iris's plan lose,
and the ingestor's fenced dispatcher, still the only device writer, converges the device to desired state in
batches of at most 12 commands, on every pass and after every reconnect. Iris's planner path is unchanged.
MCP and the dashboard share one service layer. Holds, precedence lattices, step-up, dry runs and
kill-switch matrices are deliberately left out.

- Status: accepted (Jason, 2026-11-01)
- Date: 2026-11-01
- Owners: verdify-platform (dashboard, operator API, dispatcher, MCP)
- Design package: Draft v2 (2026-11-01): [README](../design/dashboard/README.md) and the docs it links, chiefly
  [architecture](../design/dashboard/architecture.md), [OpenAPI](../design/dashboard/openapi/dashboard-api.v1.yaml)
  and [decisions](../design/dashboard/decisions.md)
- Related: [ADR-0001](0001-device-write-api-and-single-writer-at-scale.md) D1 (human API separate from the device
  path), [ADR-0002](0002-planner-hermes-vs-direct-gpt5.md) (MCP plus the registry is the planner's safety
  boundary), [ADR-0011](0011-irrigation-scheduling-program.md) (the irrigation program behind API-IRR)

## Context

Jason, 2026-11-01: "we need a UI/UX for verdify. I want a React+TypeScript front end which enables graphing,
ability to control all settings, know what is planned, and to set/change/monitor irrigation", on
dashboard.verdify.ai, behind Authentik, in the weather.vallery.net style. After the first design pass he added:
"the design is far too complicated with far too many holds, safety gates, etc. ... We can take risk. This
environment is our experimental greenhouse. Priority is on forward progress."

Facts at `0950a3bdb` that shape the answer:
- There is no human write path. MCP `set_tunable` is planner-only and needs a trigger (`mcp/server.py:2811`).
  The dispatcher builds desired state only from plans, band and defaults, so a manual value reverts at the
  next ESP32 reboot (`ingestor/tasks/dispatcher.py:782`).
- The bounded writer halted on 2026-10-19 awaiting manual custody until it was cleared on 2026-11-01.
- `api.verdify.ai` forwards every path to the public app on `:8080`. Anything in `verdify-prod` ships only
  through the attended `verdify-prod-dark` full sync, which runs migrations and can Recreate the ingestor.
- The tier-1 HostRegexp (priority 50) would claim `dashboard.verdify.ai` unless excluded, and the
  `setpoint_changes` NOTIFY fast path skips the dispatcher's guardrails (`db/migrations/055-realtime-sync.sql`).

## Decision

1. **Topology.** Browser → Cloudflare → traefik-apps → `verdify-dashboard-web` (BFF, namespace
   `verdify-dashboard`, its own auto-synced Argo app) → `verdify-operator-api` (a separate Deployment of the
   `verdify-api` image, ClusterIP `:8090`, no route, in `verdify-prod`) → `verdify-db`. The browser never reaches
   the Python API, MCP, the database or the device.
2. **Sign-in.** The options-tracker pattern: `openid-client` v6, a confidential client with PKCE, a server-side
   session (hashed in `dashboard_session`), `__Host-` cookies, an exact-Origin check on writes. Two roles from
   two Authentik groups: `verdify-dashboard-operators` (everything) and `verdify-dashboard-viewers` (read only).
   Member-tier MFA: a caretaker enrols a second factor at first sign-in; no step-up. The API trusts the BFF
   through a NetworkPolicy plus one bearer token.
3. **One control rule.** Setting writes from people and agents append rows to `desired_state` (`param, value,
   manual, source, actor, reason, created_at`); Iris's plans stay in `setpoint_plan`. Per setting the newest
   write wins against Iris's plan (`v_active_plan.created_at`); `manual = true` beats the plan regardless of
   age; `set_setting` refuses Iris with `manual` on a manual item, and any other write wins and stores the flag it
   carries (dashboard default true, MCP false); `value = NULL` releases the setting to plan, band or default. No
   expiries or tiers. Nothing is refused because an experiment is armed; writes record its id.
4. **Single writer, values that survive reboots.** The dispatcher resolves desired state where it builds each
   batch and converges the device with readback, at most 12 commands per pass and never waiting for manual
   custody, through its existing fences (registry clamp, physics invariants, moisture guardrails, heap
   deferral, the writer Lease, the device-write gate). Fields an armed experiment manifest serves wait for
   disarm. An empty `NOTIFY desired_state_changed` only wakes the next pass. Nothing uses the `setpoint_changes`
   fast path, and no dashboard or MCP login can insert there.
5. **Validation once.** A new `verdify_services` package, used by the operator API and MCP, validates on
   submit against registry min/max (intersected with the firmware clamp) and snaps to the registry step. MCP
   `set_plan` and `set_tunable` are unchanged and keep writing `setpoint_plan` for Iris; a new `set_setting` tool
   lets agents, Iris included, write `desired_state`, and new `settings` and `activity` tools read it. A new
   `agent` audience with one key per named in-cluster agent writes the same way, each write attributed.
6. **Honest readback.** REQUESTED → CONFIRMED, HELD, NOT CONFIRMED (after 5 min) or NO READBACK, from device
   readbacks with a step-aware comparator; display only.
7. **Read model.** Existing views, a metric registry over every measured column, and a bounded
   `time_bucket_gapfill` series endpoint (point caps, `daily_summary` beyond 31 days or raw retention, a 10-second
   statement timeout on the dashboard's own login). Gaps stay `null`. Polling only.
8. **One switch.** `DASHBOARD_WRITES_ENABLED` on the BFF, in a generated ConfigMap so a change rolls the pod,
   disables every dashboard write except irrigation Stop; reads continue.
9. **Delivery.** The operator API, migrations and the tier-1 exclusion (`!Host(dashboard.verdify.ai)` plus its
   test) ride the release pins and one gated sync. The ninth image, the web, is built by the verdify-platform CI
   and pinned by digest in a pin PR, then auto-synced; its Deployment ships at replicas 0 until R1's pin PR sets
   replicas 1. Cross-repo: jvallery/agents (Authentik, KSOPS, CI, Argo app, via the coordinator's pin PR),
   network-infra (a DNS row) and monitoring-stack (a probe).

## Alternatives considered

| Alternative | Why not |
|---|---|
| The v1 design: precedence lattice, holds with expiries, step-up, dry-run previews, command lifecycle, per-class kill switches, signed assertions, agent envelopes | Correct but slow to build and use; Jason rejected it for an experimental greenhouse |
| Writing human values as `setpoint_plan` rows | The next full Iris plan replaces them, non-pushable rows are dropped, and nothing re-asserts them after a reboot |
| Repointing Iris's `set_tunable` at `desired_state` | Iris's one-shots would stop expiring and the planner trigger contract would change; R5 keeps Iris's path as is |
| Writing straight to `setpoint_changes` (the NOTIFY fast path) | Skips the dispatcher's guardrails, and the next pass overwrites the value |
| Operator routes on the public `:8080` app, or a second container in its pod | `api.verdify.ai` forwards every path there; a shared pod shares network namespace and secrets |
| The web tier in `verdify-prod` | Every UI release would ride the device-gated full sync |
| An SPA holding tokens, or a BFF with database credentials | Tokens in JavaScript; a database credential in the internet-facing pod; TypeScript re-implementing registry rules |
| Forward-auth (`ak-forwardauth`) in front of the host | The estate's newer native-OIDC decision (agents#5643); no per-app session or clean 401s for data fetches |
| Embedding Grafana for graphs | Frames, public-share classification and no write UX; graphs.verdify.ai stays for public shares |
| SSE or WebSockets first | Polling covers the need; SSE is later |

## Consequences

- People and agents get one attributed write path, and human values survive ESP32 reboots. UI releases ship
  without touching the device gate; backend changes still ride the gated sync.
- Last write wins can surprise. A non-manual human edit to a planner setting lasts only until Iris's next plan,
  and the UI says so ("Iris changed this at 14:05"). `manual` is the way to keep a value.
- An operator write can break an experiment protocol. This is accepted: nothing is refused, and each write
  records the experiment id.
- The BFF is trusted to assert identity, so a compromised web image could act as any signed-in role. The
  NetworkPolicy, the bearer token, path normalization and the absence of database or device credentials in the
  web pod limit the damage; the risk is accepted.
- New things to own: one image, namespace and Argo app; three tables (`desired_state`, `activity_log`,
  `dashboard_session`) and a database login; one MCP write tool (`set_setting`), two read tools (`settings`,
  `activity`), an audience and per-agent keys. Iris gains only tools and a MANUAL SETTINGS context section.

## Validation and revisit trigger

Validation: unit tests cover the resolution rule (manual, newer plan, release) and snapping; dispatcher tests
show a value re-sent after a simulated reboot, 20 changes converging in passes of at most 12 with no approval
step, and a wake that never carries a value; database tests show no dashboard or MCP INSERT on
`setpoint_changes`, and the rolled-back native qualification and the first sync show every runtime login still
attesting; the contract tests pass (the TS client render matches; each served route matches the committed file,
and committed operations not yet served are allowed; responses validate against the YAML); Playwright and axe
pass at 320, 768 and 1440 px in-cluster. Live: anonymous requests go to Authentik, an operator's change goes
REQUESTED → CONFIRMED and survives an ESP32 reboot, and a viewer sees no write controls.

Revisit when people and Iris often fight over a setting (overridden states are common), when series p95 exceeds
2 s (add continuous aggregates), when more people than Jason and a caretaker use it (BFF trust, per-user audit),
when a second greenhouse is added, or when the experiments UI ships (blinding, and refusing writes again).
