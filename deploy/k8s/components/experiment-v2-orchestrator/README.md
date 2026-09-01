# Experiment-v2 non-device orchestrator

This opt-in component deploys one immutable image as three `Recreate`,
single-replica duties: lifecycle/boundary scheduling, selector/randomizer, and
outcome freezer. None of the containers includes ESPHome, MQTT, a setter, or a
device client.

The component renders safe but inert. The shared capability, vector-mode, and
active-experiment gates come only from the consuming environment's
`verdify-config`; when that ConfigMap is absent the runtime defaults capability
to `off`. All three database Secret references are optional and distinct.
Missing configuration keeps the process alive as a structured, non-actuating
no-op and makes no DB or network call. Only capability `off` is Ready in that
state; an enabled worker with incomplete configuration is intentionally
unready.

The lifecycle plan is one canonical JSON action, bound by
`VERDIFY_EXPERIMENT_V2_LIFECYCLE_PLAN_SHA256`: either one idempotent shadow
schedule or one server-clock boundary cycle. It cannot request both actions or
enumerate future assignments. An absent plan digest performs no lifecycle
mutation.

The checked-in selector endpoint is the credential-free official OpenAI base
URL. Kubernetes NetworkPolicy cannot select an FQDN, so its only Internet rule
permits TCP/443 while the runtime independently accepts only
`api.openai.com:443`, re-resolves it before every attempt, rejects non-global
answers, ignores proxy environment variables, and rejects redirects. The
shared capability and active-experiment gates still keep the component inert.

The production selector adapter is deliberately narrower than a generic
OpenAI client. It accepts only `https://api.openai.com/v1` (normalized to the
exact `/v1/chat/completions` path) or that exact completions URL. The frozen
identity names `gpt-5.6-luna` with `reasoning_effort=medium`. Requests are
non-streaming and tool-free with a bounded `max_completion_tokens` and a strict
profile-only JSON schema. A non-`stop` finish, model drift, malformed
envelope/content, DNS drift, or transport failure is recorded as a
baseline-safe failure and never admitted as a provider choice. OpenAI's
optional infrastructure fingerprint is retained when present but is not a
stable model identity.

The outcome freezer accepts only the one DB-snapshotted canonical source
bundle returned by `fn_experiment_v2_outcome_source_cycle`. Its mounted
`identity.json` is canonical JSON whose SHA-256 is the locked
`endpoint_artifact_sha256`; it names the exact copied evaluator-source SHA-256,
the locked outcome-schema SHA-256, and bounded climate duplicate tolerances.
Missing or mismatched identity/source evidence freezes explicit null endpoints
with stable missing codes. Equipment completeness additionally requires the
anchored, server-sequenced receipt chain from the immediate barrier at or before
the earliest direct seed through the first barrier at or after the window end.
Every in-coverage link must be consecutive, hash-bound to its PostgreSQL-v2
receipt preimage, gap-free, no more than 60 seconds apart, and share the exact
runtime, connection generation, and firmware of every direct seed and counter
endpoint. State transitions are derived only from canonical receipt events and
must project byte-for-byte to the source bundle; a missing, reordered, altered,
or dropped link freezes an explicit null endpoint. The locked equipment source
map retains eleven raw streams: south
and west normal/fertilized mister components are combined atomically with OR
into the nine analyzed logical streams, and any positive-duration component
overlap fails reconciliation instead of hiding counter double-counting. The
freezer never reads telemetry tables directly.

Readiness is a bounded, credential-free file in each pod's private
`/run/verdify` `emptyDir`. Capability `off` is Ready without a DB or network
call. When enabled, a worker starts unready and becomes Ready only after its
dedicated login passes exact attestation and one server cycle succeeds. One or
two consecutive cycle failures retain the last-known-ready state; the third
failure makes the worker unready. A successful later cycle recovers it. The
file expires after the larger of 30 seconds or three poll intervals (45 seconds
at the default 15-second poll), and the exec probe samples every five seconds
with a three-failure threshold. Missing database credentials or failed
attestation stay unready. There is deliberately no liveness probe: a PostgreSQL
or provider outage must not create a container restart loop.

Named external inputs (all optional, values never belong in Git):

- Secret `verdify-experiment-v2-shadow-scheduler-db`, key `password`;
- Secret `verdify-experiment-v2-randomizer-db`, key `password`;
- Secret `verdify-experiment-v2-outcome-freezer-db`, key `password`;
- Secret `verdify-hermes`, key `OPENAI_API_KEY`;
- ConfigMap `verdify-experiment-v2-lifecycle-plan`, key `plan.json`;
- ConfigMap `verdify-experiment-v2-selector-identity`, key `identity.json`;
- ConfigMap `verdify-experiment-v2-outcome-identity`, key `identity.json`.

Each database username is fixed and distinct in the workload. Its external
LOGIN role must be a member of exactly the named NOLOGIN duty role. The
background lifecycle process is specifically a member of
`verdify_experiment_shadow_scheduler`; the broader API-only
`verdify_experiment_lifecycle` duty is never mounted here. Startup
attestation rejects owner/elevated/extra-role identities, relation or sequence
privileges, missing functions, and any unexpected experiment-v2 function.
