# Planner Graph Verdify Integration

Status: shadow-only integration scaffold, 2026-05-20.

Verdify can call the private `planner_graph` service as a shadow planner. The remote proposal is submitted, polled, compared, and persisted for evaluation. It is not executed.

## Execution Boundary

The existing Hermes/Iris planner path remains the production control path. `planner_graph` has no relay authority and does not bypass MCP, dispatcher validation, setpoint clamps, or ESP32 firmware safety logic.

The sidecar hook is in `ingestor/iris_planner.py::send_to_iris`, after the Hermes delivery attempt has completed. It is disabled unless `PLANNER_GRAPH_SHADOW_ENABLED=1`.

## Runtime Configuration

Required for runtime shadow calls:

```bash
PLANNER_GRAPH_SHADOW_ENABLED=1
PLANNER_GRAPH_URL=https://planner-dp4w3uutza-uc.a.run.app
PLANNER_GRAPH_AUTH_MODE=google_oidc
PLANNER_GRAPH_GCLOUD_CONFIGURATION=verdify-planner
PLANNER_GRAPH_IMPERSONATE_SERVICE_ACCOUNT=verdify-planner-admin@buoyant-valve-496719-m0.iam.gserviceaccount.com
```

Useful rollout controls:

```bash
PLANNER_GRAPH_SHADOW_EVENT_TYPES=MANUAL
PLANNER_GRAPH_REQUEST_TIMEOUT_S=10
PLANNER_GRAPH_POLL_INTERVAL_S=2
PLANNER_GRAPH_POLL_TIMEOUT_S=120
PLANNER_GRAPH_LOCAL_WAIT_TIMEOUT_S=0
```

The default event cohort is:

```text
SUNRISE,SUNSET,MIDNIGHT,SOLAR_MAX,TRANSITION,FORECAST_DEVIATION,MANUAL
```

## Cloud Run Endpoint

Verified planner service, 2026-05-20:

- project: `buoyant-valve-496719-m0`
- project number: `833833246756`
- organization: `781431221097`
- region: `us-central1`
- service: `planner`
- canonical URL: `https://planner-dp4w3uutza-uc.a.run.app`
- alternate URL observed from Cloud Run list output: `https://planner-833833246756.us-central1.run.app`

The service is private. Unauthenticated requests return 403. Authenticated `/health` with the configured impersonated caller returns:

```json
{"service":"ok","private_api":true,"default_run_mode":"shadow","worker":"ok","db":"ok","openai":"fallback","mcp":"shadow-only","checkpoint":"in-memory"}
```

## Authentication

The preferred production model is a dedicated Verdify service identity with Cloud Run `roles/run.invoker` on the planner service. The caller should send a Google-signed ID token whose audience is the Cloud Run service URL.

The auth resolver supports, in order:

- `PLANNER_GRAPH_ID_TOKEN`
- `PLANNER_GRAPH_BEARER_TOKEN_FILE`
- `PLANNER_GRAPH_GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_APPLICATION_CREDENTIALS` service-account ID tokens
- GCP metadata-server identity tokens
- ADC ID tokens
- `gcloud auth print-identity-token` only when `PLANNER_GRAPH_AUTH_MODE=google_oidc` or `PLANNER_GRAPH_ALLOW_GCLOUD_AUTH=1`; set `PLANNER_GRAPH_GCLOUD_CONFIGURATION` and `PLANNER_GRAPH_IMPERSONATE_SERVICE_ACCOUNT` for keyless service-account impersonation

### Current Host Auth

The Verdify host has a durable keyless gcloud configuration:

```bash
gcloud --configuration=verdify-planner config list
```

Observed non-secret values on 2026-05-22:

```text
core.account = verdify-planner-caller@buoyant-valve-496719-m0.iam.gserviceaccount.com
core.project = buoyant-valve-496719-m0
auth.impersonate_service_account = verdify-planner-admin@buoyant-valve-496719-m0.iam.gserviceaccount.com
```

Do not create or store service-account JSON keys for this integration. The org policy `constraints/iam.managed.disableServiceAccountKeyCreation` blocks key creation, and the intended path is service-account impersonation.

The active caller account can mint a Cloud Run audience token directly. Until
the stored admin impersonation path is repaired, clear the Cloud SDK
impersonation override when running local smoke commands:

```bash
CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=
PLANNER_GRAPH_IMPERSONATE_SERVICE_ACCOUNT=
```

Current IAM facts verified on 2026-05-20:

- `jason@verdify.ai` has `roles/iam.serviceAccountTokenCreator` on `verdify-planner-admin@buoyant-valve-496719-m0.iam.gserviceaccount.com`.
- `verdify-planner-admin@buoyant-valve-496719-m0.iam.gserviceaccount.com` has `roles/run.invoker` on Cloud Run service `planner`.
- Cloud Run service policy also lists `verdify-planner-caller@buoyant-valve-496719-m0.iam.gserviceaccount.com` as an invoker, but no local credential is configured for that account.
- The impersonated account can list and describe Cloud Run services in `buoyant-valve-496719-m0`.
- The impersonated account cannot read project IAM policy via `projects get-iam-policy`; use an org/project admin identity for IAM audits.

Manual auth probe from this host:

```bash
TOKEN="$(gcloud --configuration=verdify-planner auth print-identity-token \
  --audiences=https://planner-dp4w3uutza-uc.a.run.app)"

curl -i -H "Authorization: Bearer ${TOKEN}" \
  https://planner-dp4w3uutza-uc.a.run.app/health
```

Expected result: `HTTP/2 200`.

## Persistence

Shadow evaluations are inserted into `plan_delivery_log_shadow`:

- `event_type='PLANNER_GRAPH_SHADOW'`
- `instance='planner_graph'`
- same `trigger_id` as the production planner row
- `gateway_body` contains the request metadata, remote terminal status, local planner output, diff summary, validation outcome, latency, and any error

## Smoke And Reporting

Run a one-off Cloud Run smoke:

```bash
PLANNER_GRAPH_AUTH_MODE=google_oidc \
PLANNER_GRAPH_GCLOUD_CONFIGURATION=verdify-planner \
PLANNER_GRAPH_IMPERSONATE_SERVICE_ACCOUNT= \
CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT= \
/srv/greenhouse/.venv/bin/python scripts/planner-graph-shadow-smoke.py \
  --base-url https://planner-dp4w3uutza-uc.a.run.app
```

Summarize persisted shadow outcomes:

```bash
/srv/greenhouse/.venv/bin/python scripts/planner-graph-shadow-report.py --since "7 days"
```

Latest verified smoke, 2026-05-22:

```json
{
  "auth_source": "gcloud",
  "error": null,
  "judgement": "unclear",
  "local_action": "set_plan",
  "poll_count": 3,
  "remote_status": "completed",
  "remote_action": "set_plan",
  "would_accept_remote": true,
  "shadow_id": 4,
  "trigger_id": "7baf807c-cd51-4630-9530-511e16f7a410"
}
```

## Cutover Rule

Do not execute remote proposals until the shadow record shows durable evidence that request shape, action quality, validation acceptance, and failure behavior are stable. Any cutover requires a separate operator decision and a new execution-path change.
