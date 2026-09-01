# Experiment-v2 credential bootstrap

This component is the non-actuating credential prerequisite for Gate 2. It
runs as Argo CD `PreSync` wave 2, after ledgered migrations (wave 0) and the
ordinary API/ingestor bootstrap (wave 1). It never changes the experiment
capability, active experiment ID, lifecycle, phase, admission, or device state.

The fleet Secret authority must reconcile these names before this component is
merged and synced. Values never belong in Git or rollout evidence.

| Secret | key | exact database login / purpose |
| --- | --- | --- |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_LIFECYCLE_DB_USER` / `VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD` | `verdify_experiment_v2_lifecycle_login` → `verdify_experiment_lifecycle` |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_COMPONENT_DB_USER` / `VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD` | `verdify_experiment_v2_component_executor_login` → `verdify_experiment_component_executor` |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER` / `VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD` | `verdify_experiment_v2_equipment_source_collector_login` → `verdify_experiment_equipment_source_collector` |
| `verdify-experiment-v2-shadow-scheduler-db` | `password` | `verdify_experiment_v2_shadow_scheduler_login` → `verdify_experiment_shadow_scheduler` |
| `verdify-experiment-v2-randomizer-db` | `password` | `verdify_experiment_v2_randomizer_login` → `verdify_experiment_randomizer` |
| `verdify-experiment-v2-outcome-freezer-db` | `password` | `verdify_experiment_v2_outcome_freezer_login` → `verdify_experiment_outcome_freezer` |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_API_TOKEN` | blinded lifecycle command authorization |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_OPERATOR_TOKEN` | separately authorized safety/status surface |

Generate each of the six database passwords and both API tokens independently
as 32 CSPRNG bytes encoded as 64 lowercase hexadecimal characters. They must be
pairwise distinct and must not reuse the database owner or either ordinary
runtime password. The hook enforces that shape before opening a DB connection,
installs all six SCRAM verifiers in one transaction, then opens six actual TCP
sessions and proves the exact login, sole non-admin duty membership, safe role
attributes, no object ownership/direct relation or sequence ACL, and exact
function allowlist. Its successful log receipt contains no credential material
and is retained for 600 seconds.

The blinded analyst is deliberately a `NOLOGIN` read-only duty in this fast
path and therefore receives no runtime credential. Broader platform-wide role
splitting remains deferred under #643; the six login boundaries above are the
minimum P0 experiment-integrity contract and cannot be deferred.

The selector-provider credential is independent of this database bootstrap.
The selector reuses `verdify-hermes` / `OPENAI_API_KEY` only when the frozen
selector identity and experiment capability are active. The adapter accepts
only official `https://api.openai.com/v1` (normalized internally to
`/v1/chat/completions`) or that exact completions URL and the locked
`gpt-5.6-luna` / medium-reasoning contract. It sends the canonical
`verdify-daily-selector-request-v2` document inside frozen system/user messages
and admits only the locked model, `stop` finish, and canonical profile-only JSON
response. Kubernetes allows selector TCP/443 egress because NetworkPolicy
cannot name an FQDN; the application separately hard-locks the OpenAI authority
and global DNS answers. With no key the selector records baseline fallback,
which is safe for credential activation but is not provider qualification. The
repository pod must not read, print, or transport the key. That provider key is
intentionally exempt from this hook's 64-hex and pairwise-distinct activation
checks.
